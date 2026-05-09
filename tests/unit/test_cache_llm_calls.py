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


def test_increment_llm_calls(store: CacheStore) -> None:
    assert store.llm_calls_today() == 0
    store.increment_llm_calls(3)
    store.increment_llm_calls(2)
    assert store.llm_calls_today() == 5


def test_yesterday_does_not_interfere(store: CacheStore) -> None:
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    store.conn.execute(
        "INSERT OR REPLACE INTO _llm_call_counts(day, count) VALUES (?, ?)",
        (yesterday.isoformat(), 999),
    )
    store.conn.commit()
    store.increment_llm_calls(7)
    assert store.llm_calls_today() == 7
