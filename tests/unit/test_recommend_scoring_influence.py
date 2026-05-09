from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import Format, Release
from discogs.recommend.graph import GraphPath
from discogs.recommend.scoring import DEFAULT_WEIGHTS, score_candidates


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def _release(rid: int) -> Release:
    return Release(
        id=rid, master_id=None, title=f"r{rid}", year=1975, country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Jazz"], genres=["Jazz"],
        community_have=1000, community_want=500,
        community_avg_rating=4.2, community_rating_count=50,
        fetched_at=datetime.now(UTC),
    )


def _direct(rid: int) -> GraphPath:
    return GraphPath(seed_artist_id=1, seed_weight=0.9,
                     edge_chain=((1, rid, "direct"),), edge_weight=1.0,
                     seed_kind="direct")


def _influence(rid: int) -> GraphPath:
    return GraphPath(seed_artist_id=2, seed_weight=0.6,
                     edge_chain=((2, rid, "direct"),), edge_weight=1.0,
                     seed_kind="influence")


def test_influence_chain_score_nonzero_when_influence_paths_present(store: CacheStore) -> None:
    paths = {
        100: [_direct(100), _influence(100)],
    }
    scored = score_candidates(
        store=store, candidate_paths=paths,
        releases={100: _release(100)}, label_release_counts={100: 50},
        weights=DEFAULT_WEIGHTS,
    )
    assert scored[0].subscores["influence_chain"] > 0.0
    assert scored[0].subscores["connection"] > 0.0


def test_score_in_full_range_with_influence(store: CacheStore) -> None:
    """Scores can now reach up to 1.0 (no longer capped at 0.85)."""
    paths = {1: [_direct(1), _influence(1)]}
    scored = score_candidates(
        store=store, candidate_paths=paths, releases={1: _release(1)},
        label_release_counts={1: 50}, weights=DEFAULT_WEIGHTS,
    )
    assert scored[0].score <= 1.0


def test_pure_direct_paths_have_zero_influence_score(store: CacheStore) -> None:
    paths = {1: [_direct(1)]}
    scored = score_candidates(
        store=store, candidate_paths=paths, releases={1: _release(1)},
        label_release_counts={1: 50}, weights=DEFAULT_WEIGHTS,
    )
    assert scored[0].subscores["influence_chain"] == 0.0
    assert scored[0].subscores["connection"] > 0.0


def test_pure_influence_paths_have_zero_connection_score(store: CacheStore) -> None:
    paths = {1: [_influence(1)]}
    scored = score_candidates(
        store=store, candidate_paths=paths, releases={1: _release(1)},
        label_release_counts={1: 50}, weights=DEFAULT_WEIGHTS,
    )
    assert scored[0].subscores["connection"] == 0.0
    assert scored[0].subscores["influence_chain"] > 0.0
