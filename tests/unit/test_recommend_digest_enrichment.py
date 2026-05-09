from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import Format, Release
from discogs.recommend.digest import render_digest
from discogs.recommend.graph import GraphPath
from discogs.recommend.pipeline import RunResult
from discogs.recommend.scoring import Enrichment, ScoredCandidate


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def test_digest_renders_enrichment_note(store: CacheStore) -> None:
    rel = Release(
        id=100, master_id=None, title="Karma", year=1969, country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Spiritual Jazz"], genres=["Jazz"],
        community_have=2500, community_want=8000,
        community_avg_rating=4.6, community_rating_count=320,
        fetched_at=datetime.now(UTC),
    )
    store.upsert_release(rel)

    pick = ScoredCandidate(
        release_id=100, score=0.78,
        subscores={"connection": 1.0, "influence_chain": 0.0, "rarity": 0.5,
                   "demand_ratio": 0.4, "label_obscurity": 0.4, "style_niche": 0.5,
                   "rating": 0.7, "format": 1.0, "recency_match": 0.5},
        paths=(GraphPath(seed_artist_id=7, seed_weight=0.94,
                         edge_chain=((7, 100, "direct"),), edge_weight=1.0,
                         seed_kind="direct"),),
        enrichment=Enrichment(
            note="A landmark of spiritual jazz. Sanders' tenor leads a long-form modal "
                 "meditation backed by Lonnie Liston Smith's piano.",
            confidence="high",
        ),
    )

    result = RunResult(
        run_id="abc", run_display_id="2026-05-09-1830", picks=[pick],
        seed_count=1, candidate_count=1, api_calls_used=5, wall_seconds=2.0,
        args={},
    )
    md = render_digest(store, result)

    assert "spiritual jazz" in md.lower()
    assert "Lonnie Liston Smith" in md
    assert "confidence: high" in md.lower() or "[high]" in md.lower()


def test_digest_skips_enrichment_when_absent(store: CacheStore) -> None:
    rel = Release(
        id=100, master_id=None, title="Karma", year=1969, country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Spiritual Jazz"], genres=["Jazz"],
        community_have=2500, community_want=8000,
        community_avg_rating=4.6, community_rating_count=320,
        fetched_at=datetime.now(UTC),
    )
    store.upsert_release(rel)

    pick = ScoredCandidate(
        release_id=100, score=0.78,
        subscores={"connection": 1.0, "influence_chain": 0.0},
        paths=(GraphPath(seed_artist_id=7, seed_weight=0.94,
                         edge_chain=((7, 100, "direct"),), edge_weight=1.0,
                         seed_kind="direct"),),
        enrichment=None,
    )
    result = RunResult(
        run_id="abc", run_display_id="2026-05-09-1830", picks=[pick],
        seed_count=1, candidate_count=1, api_calls_used=5, wall_seconds=2.0,
        args={},
    )
    md = render_digest(store, result)
    assert "confidence:" not in md.lower()
