from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import DiscogsClient
from discogs.api.labels import fetch_label
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[Config, CacheStore, DiscogsClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db", daily_api_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    yield cfg, store, client
    store.close()


def _fake_raw_label(lid: int = 101) -> MagicMock:
    raw = MagicMock()
    raw.id = lid
    raw.name = "Impulse!"
    raw.parent_label = None
    raw.data = {"releases_count": 200}
    return raw


def test_fetch_label_persists(setup) -> None:
    _, store, client = setup
    client.upstream.label.return_value = _fake_raw_label()
    label = fetch_label(client, store, 101)
    assert label.name == "Impulse!"
    assert label.releases_count == 200


def test_fetch_label_uses_cache_when_fresh(setup) -> None:
    _, store, client = setup
    client.upstream.label.return_value = _fake_raw_label()
    fetch_label(client, store, 101)
    initial = store.api_calls_today()
    fetch_label(client, store, 101)
    assert store.api_calls_today() == initial


def test_fetch_label_refreshes_when_stale(setup) -> None:
    _, store, client = setup
    client.upstream.label.return_value = _fake_raw_label()
    fetch_label(client, store, 101)
    initial = store.api_calls_today()
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    with store.conn:
        store.conn.execute("UPDATE labels SET fetched_at = ? WHERE id = ?", (old, 101))
    fetch_label(client, store, 101)
    assert store.api_calls_today() == initial + 1
