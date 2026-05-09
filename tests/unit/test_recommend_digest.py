from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import Format, Label, Release
from discogs.recommend.digest import render_digest
from discogs.recommend.graph import GraphPath
from discogs.recommend.pipeline import RunResult
from discogs.recommend.scoring import ScoredCandidate


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def _setup_pick(store: CacheStore, release_id: int = 100) -> ScoredCandidate:
    rel = Release(
        id=release_id, master_id=None, title="Karma", year=1969, country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Spiritual Jazz", "Free Jazz"], genres=["Jazz"],
        community_have=2500, community_want=8000,
        community_avg_rating=4.6, community_rating_count=320,
        fetched_at=datetime.now(UTC),
    )
    store.upsert_release(rel)
    store.replace_release_labels(release_id, [(101, "AS-9181")])
    store.upsert_label(Label(
        id=101, name="Impulse!", parent_label=None, releases_count=200,
        fetched_at=datetime.now(UTC),
    ))
    from discogs.models import Artist
    store.upsert_artist(Artist(id=7, name="Pharoah Sanders", profile=None, fetched_at=datetime.now(UTC)))

    return ScoredCandidate(
        release_id=release_id, score=0.78,
        subscores={
            "connection": 0.92, "influence_chain": 0.0, "rarity": 0.5,
            "demand_ratio": 0.4, "label_obscurity": 0.4, "style_niche": 0.6,
            "rating": 0.8, "format": 1.0, "recency_match": 0.7,
        },
        paths=(GraphPath(
            seed_artist_id=7, seed_weight=0.94,
            edge_chain=((7, release_id, "direct"),), edge_weight=1.0,
        ),),
    )


def test_digest_includes_header_and_pick(store: CacheStore) -> None:
    pick = _setup_pick(store)
    result = RunResult(
        run_id="abc-123", run_display_id="2026-05-08-1830", picks=[pick],
        seed_count=8, candidate_count=247, api_calls_used=423, wall_seconds=494.0,
        args={"max_recs": 25},
    )
    md = render_digest(store, result)

    assert "# Discogs recommendations" in md
    assert "2026-05-08-1830" in md
    assert "Karma" in md
    assert "1969" in md
    assert "Impulse!" in md
    assert "0.78" in md
    assert "Pharoah Sanders" in md
    assert "Spiritual Jazz" in md


def test_digest_run_stats(store: CacheStore) -> None:
    pick = _setup_pick(store)
    result = RunResult(
        run_id="abc", run_display_id="2026-05-08-1830", picks=[pick],
        seed_count=8, candidate_count=247, api_calls_used=423, wall_seconds=494.0,
        args={},
    )
    md = render_digest(store, result)
    assert "423" in md
    assert "8m" in md or "494" in md  # wall time formatted
    assert "247" in md  # candidate count
    assert "8" in md   # seed count


def test_digest_handles_no_picks(store: CacheStore) -> None:
    result = RunResult(
        run_id="abc", run_display_id="2026-05-08-1830", picks=[],
        seed_count=0, candidate_count=0, api_calls_used=0, wall_seconds=1.0,
        args={},
    )
    md = render_digest(store, result)
    assert "no picks" in md.lower() or "0 selected" in md.lower()
