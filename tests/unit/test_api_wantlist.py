from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import DiscogsClient
from discogs.api.wantlist import fetch_wantlist
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config


@pytest.fixture
def client(tmp_path: Path) -> DiscogsClient:
    cfg = Config(
        discogs_token="t", discogs_username="lorenzo",
        cache_path=tmp_path / "cache.db",
        daily_api_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    return DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())


def _fake_want(rid: int, notes: str | None = None) -> MagicMock:
    w = MagicMock()
    w.release.id = rid
    w.date_added = datetime.now(UTC).isoformat()
    w.notes = notes
    return w


def test_fetch_wantlist_yields_all(client: DiscogsClient) -> None:
    user = MagicMock()
    user.wantlist = iter([_fake_want(1), _fake_want(2, notes="signed copy")])
    client.upstream.user.return_value = user

    items = list(fetch_wantlist(client, "lorenzo"))

    assert [i.release_id for i in items] == [1, 2]
    assert items[1].notes == "signed copy"
