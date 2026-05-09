from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def test_replace_and_get_artist_top_releases(store: CacheStore) -> None:
    store.replace_artist_top_releases(artist_id=1, release_ids=[101, 102, 103])
    rids = store.get_artist_top_release_ids(1)
    assert rids == [101, 102, 103]


def test_replace_overwrites(store: CacheStore) -> None:
    store.replace_artist_top_releases(artist_id=1, release_ids=[101, 102])
    store.replace_artist_top_releases(artist_id=1, release_ids=[201])
    assert store.get_artist_top_release_ids(1) == [201]


def test_get_returns_empty_when_missing(store: CacheStore) -> None:
    assert store.get_artist_top_release_ids(999) == []


def test_artist_top_releases_age(store: CacheStore) -> None:
    store.replace_artist_top_releases(artist_id=1, release_ids=[101])
    age = store.artist_top_releases_age(1)
    assert age is not None
    assert age.total_seconds() < 5


def test_artist_top_releases_age_none_when_missing(store: CacheStore) -> None:
    assert store.artist_top_releases_age(999) is None


def test_old_entries_count_against_age(store: CacheStore) -> None:
    # Manually insert with old timestamp to verify age computation
    old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    with store.conn:
        store.conn.execute(
            "INSERT INTO artist_top_releases (artist_id, release_id, rank, fetched_at) VALUES (?, ?, ?, ?)",
            (1, 101, 0, old),
        )
    age = store.artist_top_releases_age(1)
    assert age is not None
    assert age > timedelta(days=30)
