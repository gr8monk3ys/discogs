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


def test_record_and_read_last_sync(store: CacheStore) -> None:
    assert store.last_sync_at("collection") is None
    now = datetime.now(UTC)
    store.record_sync("collection", now)
    fetched = store.last_sync_at("collection")
    assert fetched is not None
    assert abs((fetched - now).total_seconds()) < 1


def test_increment_daily_calls(store: CacheStore) -> None:
    assert store.api_calls_today() == 0
    store.increment_api_calls(3)
    store.increment_api_calls(2)
    assert store.api_calls_today() == 5

    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    store.conn.execute(
        "INSERT OR REPLACE INTO _api_call_counts(day, count) VALUES (?, ?)",
        (yesterday.isoformat(), 999),
    )
    store.conn.commit()
    assert store.api_calls_today() == 5
