from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import Format, Release


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def make_release(rid: int = 1, *, fetched_at: datetime | None = None) -> Release:
    return Release(
        id=rid,
        title="Karma",
        year=1969,
        country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Spiritual Jazz", "Free Jazz"],
        genres=["Jazz"],
        community_have=2500,
        community_want=8000,
        community_avg_rating=4.6,
        community_rating_count=320,
        fetched_at=fetched_at or datetime.now(UTC),
    )


def test_upsert_and_get_release(store: CacheStore) -> None:
    r = make_release()
    store.upsert_release(r)

    fetched = store.get_release(r.id)
    assert fetched is not None
    assert fetched.title == "Karma"
    assert set(fetched.styles) == {"Spiritual Jazz", "Free Jazz"}
    assert set(fetched.genres) == {"Jazz"}


def test_upsert_replaces_existing(store: CacheStore) -> None:
    store.upsert_release(make_release(rid=1))
    updated = make_release(rid=1)
    updated_dict = updated.model_dump()
    updated_dict["community_have"] = 9999
    store.upsert_release(Release(**updated_dict))

    fetched = store.get_release(1)
    assert fetched is not None
    assert fetched.community_have == 9999


def test_get_release_returns_none_when_missing(store: CacheStore) -> None:
    assert store.get_release(99999) is None


def test_release_age_returns_seconds(store: CacheStore) -> None:
    fetched_at = datetime.now(UTC) - timedelta(seconds=42)
    store.upsert_release(make_release(fetched_at=fetched_at))
    age = store.release_age(1)
    assert age is not None
    assert 40 <= age.total_seconds() <= 60


def test_release_age_returns_none_when_missing(store: CacheStore) -> None:
    assert store.release_age(99999) is None


def test_release_round_trips_artists(store: CacheStore) -> None:
    store.upsert_release(
        Release(
            id=7, title="In Utero", year=1993, artists=["Nirvana"],
            community_have=0, community_want=0, community_avg_rating=0.0,
            community_rating_count=0, fetched_at=datetime.now(UTC),
        )
    )
    fetched = store.get_release(7)
    assert fetched is not None
    assert fetched.artists == ["Nirvana"]
