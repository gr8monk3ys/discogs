from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import Format, Release
from discogs.recommend.graph import GraphPath
from discogs.recommend.scoring import (
    DEFAULT_WEIGHTS,
    ScoredCandidate,
    score_candidates,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def _release(rid: int, *, have: int = 1000, want: int = 500, rating: float = 4.2,
             rating_count: int = 50, year: int = 1975,
             formats=None, styles=None) -> Release:
    return Release(
        id=rid, master_id=None, title=f"r{rid}", year=year, country="US",
        formats=formats or [Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=styles or ["Jazz"], genres=["Jazz"],
        community_have=have, community_want=want,
        community_avg_rating=rating, community_rating_count=rating_count,
        fetched_at=datetime.now(UTC),
    )


def test_score_in_range_and_total_le_085(store: CacheStore) -> None:
    paths = {
        100: [GraphPath(seed_artist_id=1, seed_weight=0.9,
                        edge_chain=((1, 100, "direct"),), edge_weight=1.0)],
    }
    scored = score_candidates(
        store=store,
        candidate_paths=paths,
        releases={100: _release(100)},
        label_release_counts={100: 50},
        weights=DEFAULT_WEIGHTS,
    )
    assert len(scored) == 1
    s = scored[0]
    assert isinstance(s, ScoredCandidate)
    assert 0.0 <= s.score <= 0.85   # influence_chain_score weight (0.15) is unused in Phase 2


def test_higher_have_lowers_rarity(store: CacheStore) -> None:
    paths = {
        1: [GraphPath(1, 0.9, ((1, 1, "direct"),), 1.0)],
        2: [GraphPath(1, 0.9, ((1, 2, "direct"),), 1.0)],
    }
    releases = {1: _release(1, have=10), 2: _release(2, have=100_000)}
    scored = {s.release_id: s for s in score_candidates(
        store=store, candidate_paths=paths, releases=releases,
        label_release_counts={1: 50, 2: 50}, weights=DEFAULT_WEIGHTS,
    )}
    assert scored[1].subscores["rarity"] > scored[2].subscores["rarity"]


def test_album_format_beats_single(store: CacheStore) -> None:
    paths = {
        1: [GraphPath(1, 0.9, ((1, 1, "direct"),), 1.0)],
        2: [GraphPath(1, 0.9, ((1, 2, "direct"),), 1.0)],
    }
    releases = {
        1: _release(1, formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])]),
        2: _release(2, formats=[Format(name="Vinyl", qty=1, descriptions=["7\""])]),
    }
    scored = {s.release_id: s for s in score_candidates(
        store=store, candidate_paths=paths, releases=releases,
        label_release_counts={1: 50, 2: 50}, weights=DEFAULT_WEIGHTS,
    )}
    assert scored[1].subscores["format"] > scored[2].subscores["format"]


def test_low_rating_count_zeros_rating_subscore(store: CacheStore) -> None:
    paths = {1: [GraphPath(1, 0.9, ((1, 1, "direct"),), 1.0)]}
    releases = {1: _release(1, rating=4.9, rating_count=2)}  # < threshold of 5
    scored = score_candidates(
        store=store, candidate_paths=paths, releases=releases,
        label_release_counts={1: 50}, weights=DEFAULT_WEIGHTS,
    )
    assert scored[0].subscores["rating"] == 0.0


def test_influence_chain_score_is_zero_in_phase_2(store: CacheStore) -> None:
    paths = {1: [GraphPath(1, 0.9, ((1, 1, "direct"),), 1.0)]}
    releases = {1: _release(1)}
    scored = score_candidates(
        store=store, candidate_paths=paths, releases=releases,
        label_release_counts={1: 50}, weights=DEFAULT_WEIGHTS,
    )
    assert scored[0].subscores["influence_chain"] == 0.0


def test_results_sorted_descending(store: CacheStore) -> None:
    paths = {
        1: [GraphPath(1, 0.9, ((1, 1, "direct"),), 1.0)],
        2: [GraphPath(1, 0.9, ((1, 2, "direct"),), 1.0)],
    }
    releases = {
        1: _release(1, have=10, want=500, rating=4.8, rating_count=200),
        2: _release(2, have=50_000, want=10, rating=2.5, rating_count=200),
    }
    scored = score_candidates(
        store=store, candidate_paths=paths, releases=releases,
        label_release_counts={1: 5, 2: 5_000}, weights=DEFAULT_WEIGHTS,
    )
    assert [s.release_id for s in scored] == [1, 2]
