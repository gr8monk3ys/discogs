from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import Label


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def make_label(lid: int = 1, *, fetched_at: datetime | None = None) -> Label:
    return Label(
        id=lid,
        name="Impulse!",
        parent_label="ABC Records",
        releases_count=200,
        fetched_at=fetched_at or datetime.now(UTC),
    )


def test_upsert_and_get_label(store: CacheStore) -> None:
    store.upsert_label(make_label())
    fetched = store.get_label(1)
    assert fetched is not None
    assert fetched.name == "Impulse!"
    assert fetched.releases_count == 200


def test_upsert_replaces_existing(store: CacheStore) -> None:
    store.upsert_label(make_label(lid=1))
    store.upsert_label(
        Label(id=1, name="Impulse!", parent_label=None, releases_count=999, fetched_at=datetime.now(UTC))
    )
    fetched = store.get_label(1)
    assert fetched is not None
    assert fetched.releases_count == 999


def test_get_label_returns_none_when_missing(store: CacheStore) -> None:
    assert store.get_label(999) is None


def test_label_age(store: CacheStore) -> None:
    fetched_at = datetime.now(UTC) - timedelta(seconds=42)
    store.upsert_label(make_label(fetched_at=fetched_at))
    age = store.label_age(1)
    assert age is not None
    assert 40 <= age.total_seconds() <= 60


def test_label_age_returns_none_when_missing(store: CacheStore) -> None:
    assert store.label_age(999) is None
