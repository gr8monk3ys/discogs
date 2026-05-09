from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import BudgetExceeded, DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db",
        user_agent="discogs-test/0.0",
        daily_api_budget=3,
    )


@pytest.fixture
def store(cfg: Config) -> Iterator[CacheStore]:
    init_db(cfg.cache_path)
    s = CacheStore(cfg.cache_path)
    yield s
    s.close()


def test_client_uses_configured_user_agent(cfg: Config, store: CacheStore) -> None:
    upstream_factory = MagicMock()
    upstream_factory.return_value = MagicMock()
    DiscogsClient(cfg, store, upstream_factory=upstream_factory)
    upstream_factory.assert_called_once_with(cfg.user_agent, user_token=cfg.discogs_token)


def test_call_increments_budget(cfg: Config, store: CacheStore) -> None:
    upstream = MagicMock()
    upstream.identity.return_value = MagicMock(username="u")
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: upstream)

    assert store.api_calls_today() == 0
    client.call("identity")
    assert store.api_calls_today() == 1
    upstream.identity.assert_called_once()


def test_budget_exceeded_raises(cfg: Config, store: CacheStore) -> None:
    upstream = MagicMock()
    upstream.identity.return_value = None
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: upstream)
    client.call("identity")
    client.call("identity")
    client.call("identity")

    with pytest.raises(BudgetExceeded):
        client.call("identity")


def test_call_with_args_forwards(cfg: Config, store: CacheStore) -> None:
    upstream = MagicMock()
    upstream.release.return_value = MagicMock(id=42)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: upstream)

    client.call("release", 42)
    upstream.release.assert_called_once_with(42)
