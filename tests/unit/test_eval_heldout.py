"""Held-out wantlist recall eval — offline, deterministic, synthetic fixture."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.eval.heldout import compute_recall_metrics, run_heldout_eval
from discogs.models import CollectionItem, Credit, Format, Release, WantlistItem


def _release(rid: int) -> Release:
    return Release(
        id=rid,
        title=f"Release {rid}",
        year=1990,
        formats=[Format(name="Vinyl", descriptions=["LP", "Album"])],
        community_have=100,
        community_want=50,
        community_avg_rating=4.0,
        community_rating_count=20,
        fetched_at=datetime.now(UTC),
    )


def _build_fixture(path: Path) -> None:
    """A minimal but COMPLETE cache: every release the run touches is cached with
    detail + credits, and the seed artist's discography is cached and fresh — so an
    offline run never needs the network.

    Shape: collection={100}, wantlist={101, 200}. Artist 10 is credited on 100 (so
    it seeds) and its cached discography is [100, 101, 200]. Whichever wantlist item
    is held out becomes a reachable candidate; the other stays excluded as a want.
    """
    init_db(path)
    store = CacheStore(path)
    now = datetime.now(UTC)
    try:
        store.replace_collection(
            [CollectionItem(release_id=100, folder_id=0, instance_id=1, date_added=now)]
        )
        store.replace_wantlist(
            [
                WantlistItem(release_id=101, date_added=now, notes=None),
                WantlistItem(release_id=200, date_added=now, notes=None),
            ]
        )
        for rid in (100, 101, 200):
            store.upsert_release(_release(rid))
            store.replace_release_credits(
                rid, [Credit(release_id=rid, artist_id=10, role="Producer")]
            )
        store.replace_artist_top_releases(10, [100, 101, 200])
    finally:
        store.close()


def test_heldout_eval_offline_recovers_a_known_want(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.db"
    _build_fixture(fixture)
    cfg = Config(discogs_token="x", discogs_username="tester")

    result = run_heldout_eval(
        cfg, fixture, holdout=1, k=10, seed=7, min_seed_occurrences=1, offline=True
    )

    assert result.api_calls_used == 0  # the offline guarantee: nothing left the cache
    assert result.holdout_size == 1
    assert result.reachable == 1
    assert result.hits_at_k == 1
    assert result.recall_at_k == 1.0
    assert result.mrr == 1.0


def test_heldout_eval_rejects_oversized_holdout(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.db"
    _build_fixture(fixture)
    cfg = Config(discogs_token="x", discogs_username="tester")

    with pytest.raises(ValueError, match="need more than holdout"):
        run_heldout_eval(
            cfg, fixture, holdout=5, k=10, min_seed_occurrences=1, offline=True
        )


def test_compute_recall_metrics_ranks_and_misses() -> None:
    ranked = [5, 3, 9, 1, 7]
    held_out = {3, 7, 42}  # 3 at rank 2, 7 at rank 5, 42 absent

    reachable, hits, recall, mrr = compute_recall_metrics(ranked, held_out, k=3)

    assert reachable == 2  # 3 and 7 appear; 42 never does
    assert hits == 1  # only 3 is within the top-3
    assert recall == pytest.approx(1 / 3)
    assert mrr == pytest.approx((1 / 2 + 1 / 5 + 0.0) / 3)


def test_compute_recall_metrics_empty_holdout() -> None:
    assert compute_recall_metrics([], set(), k=5) == (0, 0, 0.0, 0.0)
