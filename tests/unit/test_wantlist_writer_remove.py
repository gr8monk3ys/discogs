from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import BudgetExceeded, DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.wantlist_writer import RemoveResult, remove_from_wantlist


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


def test_remove_success(setup) -> None:
    _, client = setup
    user = MagicMock()
    user.wantlist.remove.return_value = None
    client.upstream.user.return_value = user

    result = remove_from_wantlist(client, username="u", release_id=42)
    assert result == RemoveResult(release_id=42, status="removed", error=None)


def test_remove_skipped_when_not_in_wantlist(setup) -> None:
    """Discogs returns 404 when removing a release that isn't wantlisted; we
    treat that as 'skipped' rather than an error."""
    _, client = setup
    user = MagicMock()
    err = RuntimeError("404 Not Found")
    user.wantlist.remove.side_effect = err
    client.upstream.user.return_value = user

    result = remove_from_wantlist(client, username="u", release_id=42)
    assert result.status == "skipped"
    assert "404" in (result.error or "")


def test_remove_genuine_error(setup) -> None:
    _, client = setup
    user = MagicMock()
    user.wantlist.remove.side_effect = RuntimeError("HTTP 500 server error")
    client.upstream.user.return_value = user

    result = remove_from_wantlist(client, username="u", release_id=42)
    assert result.status == "error"
    assert "500" in (result.error or "")


def test_remove_increments_api_call_counter(setup) -> None:
    store, client = setup
    user = MagicMock()
    user.wantlist.remove.return_value = None
    client.upstream.user.return_value = user

    initial = store.api_calls_today()
    remove_from_wantlist(client, username="u", release_id=42)
    # 1 call for client.call("user", ...); 1 manual charge_call(1) for wantlist.remove
    assert store.api_calls_today() == initial + 2


def test_remove_propagates_budget_exceeded(tmp_path: Path) -> None:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db", daily_api_budget=0,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())

    with pytest.raises(BudgetExceeded):
        remove_from_wantlist(client, username="u", release_id=42)

    store.close()
