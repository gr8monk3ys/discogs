"""Held-out wantlist recall: an offline quality eval for the recommender.

The recommender's scoring is nine hand-tuned weights. Until now nothing measured
whether those weights actually surface things the user wants. This does.

The idea: the wantlist is ground truth — releases the user has independently said
they want. So we hide a random sample of it, run the recommender over everything
else, and measure how many hidden items resurface in the ranking. A held-out item
that comes back near the top means the credit-graph + scoring genuinely captured
the user's taste; one that never appears means it was unreachable or mis-scored.

Two numbers tell the story:
  - recall@k : of the hidden items, how many landed in the top-k picks
  - MRR      : mean reciprocal rank — rewards putting hits near rank 1, not just
               anywhere in the top-k

We also report `reachable` (how many hidden items appeared ANYWHERE in the
ranking). recall@k can never exceed reachable/holdout, so a low recall with low
reachability is a graph-coverage problem, while low recall with high reachability
is a scoring problem. Keeping them separate stops one from masking the other.

The eval mutates only a temp copy of the cache — your real ~/.discogs/cache.db is
never touched.
"""
from __future__ import annotations

import random
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from discogs_client.exceptions import HTTPError

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.recommend.pipeline import RecommendParams, run_recommend


@dataclass(frozen=True)
class EvalResult:
    """Outcome of one held-out run. `api_calls_used == 0` means fully cache-served."""

    holdout_size: int        # how many wantlist items were hidden
    wantlist_total: int      # full wantlist size before holding any out
    k: int                   # the recall@k / top-k cutoff
    candidates_ranked: int   # total candidates the recommender scored and ranked
    reachable: int           # hidden items that appeared ANYWHERE in the ranking
    hits_at_k: int           # hidden items that landed in the top-k
    recall_at_k: float       # hits_at_k / holdout_size
    mrr: float               # mean reciprocal rank over hidden items (0 if absent)
    api_calls_used: int      # network calls made (0 == nothing left the cache)


def compute_recall_metrics(
    ranked_ids: list[int], held_out: set[int], k: int
) -> tuple[int, int, float, float]:
    """Score a ranking against the hidden set. Returns (reachable, hits@k, recall@k, mrr).

    This function *is* the definition of "good recommendation" for this project —
    if you disagree with how quality is measured (e.g. you want nDCG, or recall
    weighted by wantlist age), this is the one place to change it.

    `ranked_ids` is ordered best-first. Rank is 1-indexed for the reciprocal.
    """
    if not held_out:
        return 0, 0, 0.0, 0.0

    rank_of = {rid: i for i, rid in enumerate(ranked_ids)}  # 0-indexed position
    top_k = set(ranked_ids[:k])

    reachable = sum(1 for h in held_out if h in rank_of)
    hits_at_k = sum(1 for h in held_out if h in top_k)
    recall_at_k = hits_at_k / len(held_out)
    mrr = sum(1.0 / (rank_of[h] + 1) for h in held_out if h in rank_of) / len(held_out)

    return reachable, hits_at_k, recall_at_k, mrr


def _blocked_upstream(*_args: Any, **_kwargs: Any) -> Any:
    """An upstream client that refuses all network calls (for the offline guarantee).

    Any attribute access returns a callable that raises HTTPError(503). The graph
    walk catches HTTPError and degrades gracefully; the unwrapped fetch sites
    (_prefetch/_load_releases) will propagate it — which is the point: in a test
    with a complete cache, no call is ever made, so a raised error means the
    fixture is incomplete, not that the eval silently hit the wire.
    """

    class _NoNetwork:
        def __getattr__(self, _name: str) -> Any:
            def _refuse(*_a: Any, **_k: Any) -> Any:
                raise HTTPError("offline eval: network disabled", 503)

            return _refuse

    return _NoNetwork()


def make_offline_client(config: Config, store: CacheStore) -> DiscogsClient:
    """A DiscogsClient that serves only from cache and never touches the network."""
    return DiscogsClient(config, store, upstream_factory=_blocked_upstream)


def run_heldout_eval(
    config: Config,
    source_cache_path: Path,
    *,
    holdout: int = 18,
    k: int = 50,
    seed: int = 42,
    min_seed_occurrences: int = 2,
    budget: int = 1500,
    weights: dict[str, float] | None = None,
    offline: bool = False,
) -> EvalResult:
    """Hide `holdout` wantlist items, recommend over the rest, score the recall.

    Runs against a temp copy of `source_cache_path`; the real cache is untouched.
    By default a live DiscogsClient fills cache gaps (a bounded number of API
    calls); pass `offline=True` to forbid the network entirely — the run then
    serves only from cache and any gap surfaces as graceful zero-coverage.
    """
    rng = random.Random(seed)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / "eval-cache.db"
        shutil.copy2(source_cache_path, tmp_path)
        init_db(tmp_path)  # bring the copy to the current schema if it's older
        store = CacheStore(tmp_path)
        try:
            wantlist = sorted(store.wantlist_release_ids())
            if len(wantlist) <= holdout:
                raise ValueError(
                    f"wantlist has {len(wantlist)} items; need more than holdout={holdout}"
                )

            held_out = set(rng.sample(wantlist, holdout))

            # Remove the held-out items so they (a) stop seeding the graph and
            # (b) become eligible candidates instead of being excluded as wants.
            store.conn.executemany(
                "DELETE FROM wantlist_items WHERE release_id = ?",
                [(rid,) for rid in held_out],
            )
            store.conn.commit()

            # Rank "everything": max_recs/max_per_artist set high so the diversity
            # filter doesn't truncate the list — we slice top-k ourselves. No LLM
            # stages (deterministic); allow_rerecommend so prior history (now in the
            # copy) can't suppress a held-out item.
            big = len(wantlist) + holdout + k + 1
            params = RecommendParams(
                max_recs=big,
                max_per_artist=big,
                seed_mode="both",
                min_seed_occurrences=min_seed_occurrences,
                budget=budget,
                with_influences=False,
                with_enrichment=False,
                allow_rerecommend=True,
            )

            eval_client = (
                make_offline_client(config, store)
                if offline
                else DiscogsClient(config, store)
            )
            result = run_recommend(
                eval_client, store, config, params, weights=weights, llm=None
            )

            ranked_ids = [p.release_id for p in result.picks]
            reachable, hits, recall, mrr = compute_recall_metrics(ranked_ids, held_out, k)

            return EvalResult(
                holdout_size=holdout,
                wantlist_total=len(wantlist),
                k=k,
                candidates_ranked=len(ranked_ids),
                reachable=reachable,
                hits_at_k=hits,
                recall_at_k=recall,
                mrr=mrr,
                api_calls_used=result.api_calls_used,
            )
        finally:
            store.close()
