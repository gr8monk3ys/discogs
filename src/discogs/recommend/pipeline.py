"""Stage 5: orchestrate seeds → graph → scoring → final selection → history."""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field

from discogs.api.client import DiscogsClient
from discogs.api.releases import fetch_release
from discogs.cache.store import CacheStore
from discogs.config import Config
from discogs.models import Release
from discogs.recommend.graph import walk_credit_graph
from discogs.recommend.scoring import DEFAULT_WEIGHTS, ScoredCandidate, score_candidates
from discogs.recommend.seeds import select_seeds


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
    *,
    max_recs: int = 25,
    max_per_artist: int = 3,
    seed_mode: str = "both",
    min_seed_occurrences: int = 2,
    max_neighbors_per_seed: int = 5,
    max_releases_per_neighbor: int = 25,
    budget: int = 800,
    weights: dict[str, float] | None = None,
) -> RunResult:
    """Run the full Phase 2 recommendation pipeline. Dry-run only (no wantlist writes)."""
    weights = weights or DEFAULT_WEIGHTS
    args = {
        "max_recs": max_recs, "max_per_artist": max_per_artist,
        "seed_mode": seed_mode, "min_seed_occurrences": min_seed_occurrences,
        "max_neighbors_per_seed": max_neighbors_per_seed,
        "max_releases_per_neighbor": max_releases_per_neighbor,
        "budget": budget,
    }
    run_id, display_id = store.start_run(args)

    started = time.monotonic()
    api_calls_at_start = store.api_calls_today()

    try:
        # Ensure library releases have their credits cached so seed selection has data.
        _prefetch_library_releases(client, store, scope=seed_mode, daily_budget=config.daily_api_budget)

        seeds = select_seeds(store, mode=seed_mode, min_occurrences=min_seed_occurrences)  # type: ignore[arg-type]
        if not seeds:
            store.finish_run(run_id, summary={"seeds": 0, "candidates": 0, "selected": 0})
            return RunResult(
                run_id=run_id, run_display_id=display_id, picks=[],
                seed_count=0, candidate_count=0,
                api_calls_used=0, wall_seconds=time.monotonic() - started,
                args=args,
            )

        candidate_paths = walk_credit_graph(
            client, store, seeds,
            max_neighbors_per_seed=max_neighbors_per_seed,
            max_releases_per_neighbor=max_releases_per_neighbor,
            budget=budget,
        )

        # After the graph walk, fill in any candidates whose full release detail isn't
        # cached yet. Cap to whatever's left of the user's daily API budget so we can't
        # blow past it from the load phase.
        release_load_budget = max(0, config.daily_api_budget - store.api_calls_today())
        releases = _load_releases(client, store, list(candidate_paths.keys()), budget_left=release_load_budget)
        label_counts = _load_label_counts(store, list(candidate_paths.keys()))

        scored = score_candidates(
            store=store, candidate_paths=candidate_paths,
            releases=releases, label_release_counts=label_counts, weights=weights,
        )

        picks = _apply_diversity(scored, max_recs=max_recs, max_per_artist=max_per_artist)

        for p in picks:
            store.record_recommendation(run_id=run_id, release_id=p.release_id, score=p.score)

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
        if budget_left <= 0:
            break
        cached = store.get_release(rid)
        if cached is not None:
            out[rid] = cached
            continue
        out[rid] = fetch_release(client, store, rid)
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
        fetch_release(client, store, release_id, force_credits=True)
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
