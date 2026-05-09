from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.wantlist_writer import PushResult, push_to_wantlist


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[CacheStore, DiscogsClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db", daily_api_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    yield store, client
    store.close()


def test_push_success(setup) -> None:
    _, client = setup
    user = MagicMock()
    user.wantlist.add.return_value = None
    client.upstream.user.return_value = user

    result = push_to_wantlist(client, username="u", release_id=42)
    assert result == PushResult(release_id=42, ok=True, error=None)
    user.wantlist.add.assert_called_once_with(42)


def test_push_failure_captures_error(setup) -> None:
    _, client = setup
    user = MagicMock()
    user.wantlist.add.side_effect = RuntimeError("HTTP 500")
    client.upstream.user.return_value = user

    result = push_to_wantlist(client, username="u", release_id=42)
    assert result.ok is False
    assert result.release_id == 42
    assert "HTTP 500" in (result.error or "")


def test_push_increments_api_call_counter(setup) -> None:
    store, client = setup
    user = MagicMock()
    user.wantlist.add.return_value = None
    client.upstream.user.return_value = user

    initial = store.api_calls_today()
    push_to_wantlist(client, username="u", release_id=42)
    # one call for `user(username)`, one for `wantlist.add` — but discogs client wraps both.
    # We at minimum want >= 1 increment.
    assert store.api_calls_today() > initial
