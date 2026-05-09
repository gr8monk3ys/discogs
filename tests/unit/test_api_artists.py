from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.artists import fetch_artist
from discogs.api.client import DiscogsClient
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


def _fake_raw_artist(aid: int = 7) -> MagicMock:
    raw = MagicMock()
    raw.id = aid
    raw.name = "Pharoah Sanders"
    raw.profile = "American jazz saxophonist"
    return raw


def test_fetch_artist_persists(setup) -> None:
    _, store, client = setup
    client.upstream.artist.return_value = _fake_raw_artist()
    a = fetch_artist(client, store, 7)
    assert a.name == "Pharoah Sanders"
    cached = store.get_artist(7)
    assert cached is not None and cached.name == "Pharoah Sanders"


def test_fetch_artist_uses_cache_when_fresh(setup) -> None:
    _, store, client = setup
    client.upstream.artist.return_value = _fake_raw_artist()
    fetch_artist(client, store, 7)
    initial = store.api_calls_today()
    fetch_artist(client, store, 7)
    assert store.api_calls_today() == initial


def test_fetch_artist_refreshes_when_stale(setup) -> None:
    _, store, client = setup
    client.upstream.artist.return_value = _fake_raw_artist()
    fetch_artist(client, store, 7)
    initial = store.api_calls_today()
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    with store.conn:
        store.conn.execute("UPDATE artists SET fetched_at = ? WHERE id = ?", (old, 7))
    fetch_artist(client, store, 7)
    assert store.api_calls_today() == initial + 1
