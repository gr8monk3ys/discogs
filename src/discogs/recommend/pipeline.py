"""Stage 5: orchestrate seeds → graph → scoring → final selection → history."""
from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import asdict, dataclass, field

from discogs_client.exceptions import HTTPError

from discogs.api.client import DiscogsClient
from discogs.api.llm import LLMClient
from discogs.api.releases import fetch_release
from discogs.cache.store import CacheStore
from discogs.config import Config
from discogs.models import Release
from discogs.recommend.enrich import enrich_candidates
from discogs.recommend.graph import walk_credit_graph
from discogs.recommend.influences import expand_influences
from discogs.recommend.scoring import DEFAULT_WEIGHTS, ScoredCandidate, score_candidates
from discogs.recommend.seeds import SeedArtist, select_seeds

_INFLUENCE_DECAY = 0.6
_CONFIDENCE_FACTOR = {"high": 1.0, "medium": 0.7, "low": 0.4}
_VALID_SEED_MODES = ("collection", "wantlist", "both", "spotify", "all")


@dataclass(frozen=True)
class RecommendParams:
    """User-facing tuning knobs for `run_recommend`.

    Field names match the keys persisted to `runs.args` (JSON) so historical
    run records remain readable. Dependencies (client/store/config/llm) and
    scoring weights are passed separately to `run_recommend`.
    """
    max_recs: int = 25
    max_per_artist: int = 3
    seed_mode: str = "both"
    min_seed_occurrences: int = 2
    max_neighbors_per_seed: int = 5
    max_releases_per_neighbor: int = 25
    budget: int = 800
    with_influences: bool = True
    top_k_seeds_for_influences: int = 20
    with_enrichment: bool = True
    allow_rerecommend: bool = False

    def __post_init__(self) -> None:
        if self.max_recs < 1:
            raise ValueError(f"max_recs must be >= 1, got {self.max_recs}")
        if self.max_per_artist < 1:
            raise ValueError(f"max_per_artist must be >= 1, got {self.max_per_artist}")
        if self.seed_mode not in _VALID_SEED_MODES:
            raise ValueError(
                f"seed_mode must be one of {_VALID_SEED_MODES}, got {self.seed_mode!r}"
            )
        if self.min_seed_occurrences < 1:
            raise ValueError(
                f"min_seed_occurrences must be >= 1, got {self.min_seed_occurrences}"
            )
        if self.max_neighbors_per_seed < 1:
            raise ValueError(
                f"max_neighbors_per_seed must be >= 1, got {self.max_neighbors_per_seed}"
            )
        if self.max_releases_per_neighbor < 1:
            raise ValueError(
                f"max_releases_per_neighbor must be >= 1, got {self.max_releases_per_neighbor}"
            )
        # budget=0 is intentionally allowed: cached-data-only runs are supported.
        if self.budget < 0:
            raise ValueError(f"budget must be >= 0, got {self.budget}")
        if self.top_k_seeds_for_influences < 1:
            raise ValueError(
                f"top_k_seeds_for_influences must be >= 1, "
                f"got {self.top_k_seeds_for_influences}"
            )


@dataclass
class RunResult:
    run_id: str
    run_display_id: str
    picks: list[ScoredCandidate]
    seed_count: int
    candidate_count: int
    api_calls_used: int
    wall_seconds: float
    args: dict[str, object] = field(default_factory=dict)


def run_recommend(
    client: DiscogsClient,
    store: CacheStore,
    config: Config,
    params: RecommendParams | None = None,
    *,
    weights: dict[str, float] | None = None,
    llm: LLMClient | None = None,
) -> RunResult:
    """Run the full Phase 2 recommendation pipeline. Dry-run only (no wantlist writes)."""
    params = params or RecommendParams()
    weights = weights or DEFAULT_WEIGHTS
    args: dict[str, object] = asdict(params)
    run_id, display_id = store.start_run(args)

    started = time.monotonic()
    api_calls_at_start = store.api_calls_today()

    try:
        # Ensure library releases have their credits cached so seed selection has data.
        _prefetch_library_releases(
            client, store, scope=params.seed_mode, daily_budget=config.daily_api_budget,
        )

        seeds = select_seeds(
            store, mode=params.seed_mode, min_occurrences=params.min_seed_occurrences,  # type: ignore[arg-type]
        )
        if not seeds:
            store.finish_run(run_id, summary={"seeds": 0, "candidates": 0, "selected": 0})
            return RunResult(
                run_id=run_id, run_display_id=display_id, picks=[],
                seed_count=0, candidate_count=0,
                api_calls_used=0, wall_seconds=time.monotonic() - started,
                args=args,
            )

        if params.with_influences and llm is not None and seeds:
            seeds = _expand_seed_pool_with_influences(
                client, store, llm, seeds, top_k=params.top_k_seeds_for_influences,
            )

        candidate_paths = walk_credit_graph(
            client, store, seeds,
            max_neighbors_per_seed=params.max_neighbors_per_seed,
            max_releases_per_neighbor=params.max_releases_per_neighbor,
            budget=params.budget,
            allow_rerecommend=params.allow_rerecommend,
        )

        # After the graph walk, fill in any candidates whose full release detail isn't
        # cached yet. Cap to whatever's left of the user's --budget for this run so the
        # total spend honors their request (the graph walk shares the same pool).
        spent_so_far = store.api_calls_today() - api_calls_at_start
        release_load_budget = max(0, params.budget - spent_so_far)
        releases = _load_releases(client, store, list(candidate_paths.keys()), budget_left=release_load_budget)
        label_counts = _load_label_counts(store, list(candidate_paths.keys()))

        scored = score_candidates(
            store=store, candidate_paths=candidate_paths,
            releases=releases, label_release_counts=label_counts, weights=weights,
        )

        if params.with_enrichment and llm is not None and scored:
            head = scored[: params.max_recs * 2]
            tail = scored[params.max_recs * 2 :]
            enriched_head = enrich_candidates(llm, head, releases)
            enriched_head.sort(key=lambda s: -s.score)
            scored = enriched_head + tail

        picks = _apply_diversity(scored, max_recs=params.max_recs, max_per_artist=params.max_per_artist)

        for p in picks:
            store.record_recommendation(
                run_id=run_id, release_id=p.release_id, score=p.score,
                subscores=p.subscores,
            )

        api_calls_used = store.api_calls_today() - api_calls_at_start
        store.finish_run(run_id, summary={
            "seeds": len(seeds),
            "candidates": len(candidate_paths),
            "selected": len(picks),
            "api_calls_used": api_calls_used,
        })

        return RunResult(
            run_id=run_id, run_display_id=display_id, picks=picks,
            seed_count=len(seeds), candidate_count=len(candidate_paths),
            api_calls_used=api_calls_used,
            wall_seconds=time.monotonic() - started,
            args=args,
        )
    except Exception:
        store.finish_run(run_id, summary={"error": True})
        raise


def _apply_diversity(
    scored: list[ScoredCandidate], *, max_recs: int, max_per_artist: int,
) -> list[ScoredCandidate]:
    counts: Counter[int] = Counter()
    out: list[ScoredCandidate] = []
    for cand in scored:
        primary = cand.paths[0].seed_artist_id if cand.paths else -1
        if counts[primary] >= max_per_artist:
            continue
        out.append(cand)
        counts[primary] += 1
        if len(out) >= max_recs:
            break
    return out


def _load_releases(
    client: DiscogsClient, store: CacheStore, release_ids: list[int],
    *, budget_left: int,
) -> dict[int, Release]:
    """Load full Release objects for scoring. Cache hits cost 0; misses spend API budget."""
    out: dict[int, Release] = {}
    for rid in release_ids:
        cached = store.get_release(rid)
        if cached is not None:
            out[rid] = cached
            continue
        if budget_left <= 0:
            continue
        # A single bad release (404, malformed payload, transient error) shouldn't
        # abort the whole run — skip it, like the graph walk does. BudgetExceeded
        # is a RuntimeError and is intentionally NOT caught here, so it propagates.
        try:
            out[rid] = fetch_release(client, store, rid)
        except (HTTPError, json.JSONDecodeError, ValueError, OSError):
            continue
        budget_left -= 1
    return out


def _prefetch_library_releases(
    client: DiscogsClient, store: CacheStore, *, scope: str,
    daily_budget: int,
) -> int:
    """Ensure release_credits is populated for every library release.

    select_seeds reads release_credits to count artist occurrences. Phase 1's
    sync only stores release IDs in collection_items / wantlist_items; the
    credits come from fetch_release, which we haven't called yet for these.

    Returns the number of fetches performed (so the caller can report it).
    """
    coll_ids = store.collection_release_ids() if scope in ("collection", "both") else set()
    want_ids = store.wantlist_release_ids() if scope in ("wantlist", "both") else set()
    library_ids = coll_ids | want_ids

    fetches = 0
    for release_id in library_ids:
        budget_remaining = daily_budget - store.api_calls_today()
        if budget_remaining <= 0:
            break
        # force_credits=True ensures a fresh API call if the release is cached
        # but has no credits (e.g. stored before credit-fetching was implemented).
        # Once credits exist, subsequent calls return from cache at no cost.
        # A failed fetch for one release shouldn't abort the whole run — skip it,
        # consistent with the graph walk. BudgetExceeded still propagates.
        try:
            fetch_release(client, store, release_id, force_credits=True)
        except (HTTPError, json.JSONDecodeError, ValueError, OSError):
            continue
        fetches += 1
    return fetches


def _load_label_counts(store: CacheStore, release_ids: list[int]) -> dict[int, int]:
    """For each candidate release, return the largest releases_count among its labels.

    Larger label = less obscure. Default 0 when no labels are known.
    """
    out: dict[int, int] = {}
    for rid in release_ids:
        label_ids = store.get_release_label_ids(rid)
        if not label_ids:
            out[rid] = 0
            continue
        placeholders = ",".join("?" for _ in label_ids)
        row = store.conn.execute(
            f"SELECT MAX(releases_count) AS rc FROM labels WHERE id IN ({placeholders})",
            tuple(label_ids),
        ).fetchone()
        out[rid] = int(row["rc"]) if row and row["rc"] is not None else 0
    return out


def _expand_seed_pool_with_influences(
    client: DiscogsClient,
    store: CacheStore,
    llm: LLMClient,
    seeds: list[SeedArtist],
    *,
    top_k: int,
) -> list[SeedArtist]:
    """For the top `top_k` direct seeds (by weight), fetch Claude-derived
    influences and append them as additional SeedArtists with seed_kind='influence'.

    Decayed weight = original_seed_weight * confidence_factor * 0.6.
    """
    direct_seeds = [s for s in seeds if s.seed_kind == "direct"]
    direct_seeds.sort(key=lambda s: -s.weight)
    pool = list(seeds)
    seen_influence_ids: set[int] = set()

    for seed in direct_seeds[:top_k]:
        artist = store.get_artist(seed.artist_id)
        artist_name = artist.name if artist is not None else f"artist-{seed.artist_id}"
        styles: list[str] = []  # Phase 3 v1: skip per-artist style lookup; future can wire it.

        edges = expand_influences(
            client, store, llm,
            artist_id=seed.artist_id,
            artist_name=artist_name,
            primary_styles=styles,
        )

        for edge in edges:
            if edge.influence_artist_id in seen_influence_ids:
                continue
            seen_influence_ids.add(edge.influence_artist_id)

            factor = _CONFIDENCE_FACTOR.get(edge.confidence, 0.4)
            decayed = seed.weight * factor * _INFLUENCE_DECAY
            decayed = max(0.05, min(1.0, decayed))

            pool.append(SeedArtist(
                artist_id=edge.influence_artist_id,
                weight=decayed,
                sources=(),
                seed_kind="influence",
            ))

    return pool
