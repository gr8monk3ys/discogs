from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import DiscogsClient
from discogs.api.collection import fetch_collection
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config


@pytest.fixture
def client(tmp_path: Path) -> DiscogsClient:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db",
        daily_api_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    return DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())


def _fake_collection_item(rid: int, instance: int) -> MagicMock:
    item = MagicMock()
    item.release.id = rid
    item.id = rid                # python3-discogs-client aliases .id to release.id
    item.instance_id = instance  # actual unique key for an owned copy
    item.folder_id = 0
    item.date_added = datetime.now(UTC).isoformat()
    return item


def test_fetch_collection_yields_all_pages(client: DiscogsClient) -> None:
    folder = MagicMock()
    folder.releases.count = 3
    folder.releases.__iter__.return_value = iter([
        _fake_collection_item(1, 10),
        _fake_collection_item(2, 20),
        _fake_collection_item(3, 30),
    ])
    identity = MagicMock()
    identity.collection_folders = [folder]
    client.upstream.identity.return_value = identity

    items = list(fetch_collection(client))

    assert {i.release_id for i in items} == {1, 2, 3}
    assert {i.instance_id for i in items} == {10, 20, 30}
