from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import Artist


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def make_artist(aid: int = 1, *, fetched_at: datetime | None = None) -> Artist:
    return Artist(
        id=aid,
        name="Pharoah Sanders",
        profile="American jazz saxophonist",
        fetched_at=fetched_at or datetime.now(UTC),
    )


def test_upsert_and_get_artist(store: CacheStore) -> None:
    store.upsert_artist(make_artist())
    fetched = store.get_artist(1)
    assert fetched is not None
    assert fetched.name == "Pharoah Sanders"


def test_upsert_replaces_existing(store: CacheStore) -> None:
    store.upsert_artist(make_artist(aid=1))
    store.upsert_artist(
        Artist(id=1, name="Updated", profile=None, fetched_at=datetime.now(UTC))
    )
    fetched = store.get_artist(1)
    assert fetched is not None
    assert fetched.name == "Updated"


def test_get_artist_returns_none_when_missing(store: CacheStore) -> None:
    assert store.get_artist(999) is None


def test_artist_age(store: CacheStore) -> None:
    fetched_at = datetime.now(UTC) - timedelta(seconds=42)
    store.upsert_artist(make_artist(fetched_at=fetched_at))
    age = store.artist_age(1)
    assert age is not None
    assert 40 <= age.total_seconds() <= 60


def test_artist_age_returns_none_when_missing(store: CacheStore) -> None:
    assert store.artist_age(999) is None
