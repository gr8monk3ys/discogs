from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import ArtistInfluence


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def test_replace_and_get_influences(store: CacheStore) -> None:
    edges = [
        ArtistInfluence(source_artist_id=1, influence_artist_id=2, confidence="high",
                        source="claude", fetched_at=datetime.now(UTC)),
        ArtistInfluence(source_artist_id=1, influence_artist_id=3, confidence="medium",
                        source="claude", fetched_at=datetime.now(UTC)),
    ]
    store.replace_artist_influences(source_artist_id=1, edges=edges)
    fetched = store.get_artist_influences(source_artist_id=1)
    assert {(e.influence_artist_id, e.confidence) for e in fetched} == {(2, "high"), (3, "medium")}


def test_replace_overwrites_same_source(store: CacheStore) -> None:
    e1 = ArtistInfluence(source_artist_id=1, influence_artist_id=2, confidence="high",
                         source="claude", fetched_at=datetime.now(UTC))
    e2 = ArtistInfluence(source_artist_id=1, influence_artist_id=99, confidence="low",
                         source="claude", fetched_at=datetime.now(UTC))
    store.replace_artist_influences(source_artist_id=1, edges=[e1])
    store.replace_artist_influences(source_artist_id=1, edges=[e2])
    fetched = store.get_artist_influences(source_artist_id=1)
    assert {e.influence_artist_id for e in fetched} == {99}


def test_get_influences_empty_when_missing(store: CacheStore) -> None:
    assert store.get_artist_influences(999) == []


def test_artist_influences_age(store: CacheStore) -> None:
    e = ArtistInfluence(source_artist_id=1, influence_artist_id=2, confidence="high",
                       source="claude",
                       fetched_at=datetime.now(UTC) - timedelta(days=10))
    store.replace_artist_influences(source_artist_id=1, edges=[e])
    age = store.artist_influences_age(source_artist_id=1)
    assert age is not None
    assert age > timedelta(days=9)


def test_artist_influences_age_none_when_missing(store: CacheStore) -> None:
    assert store.artist_influences_age(999) is None


def test_replace_does_not_touch_other_sources(store: CacheStore) -> None:
    """If we ever add a 'rym' source (Phase 4+), replacing 'claude' edges
    should not delete 'rym' edges for the same source artist."""
    claude_edge = ArtistInfluence(source_artist_id=1, influence_artist_id=2, confidence="high",
                                  source="claude", fetched_at=datetime.now(UTC))
    rym_edge = ArtistInfluence(source_artist_id=1, influence_artist_id=3, confidence="high",
                               source="rym", fetched_at=datetime.now(UTC))
    store.replace_artist_influences(source_artist_id=1, edges=[claude_edge, rym_edge])

    new_claude_edge = ArtistInfluence(source_artist_id=1, influence_artist_id=99, confidence="low",
                                      source="claude", fetched_at=datetime.now(UTC))
    store.replace_artist_influences(source_artist_id=1, edges=[new_claude_edge], source="claude")

    fetched = store.get_artist_influences(source_artist_id=1)
    assert {(e.influence_artist_id, e.source) for e in fetched} == {(99, "claude"), (3, "rym")}
