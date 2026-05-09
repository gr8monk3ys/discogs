from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.models import CollectionItem, WantlistItem
from discogs.sync.syncer import Syncer


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[Config, CacheStore, DiscogsClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="lorenzo",
        cache_path=tmp_path / "cache.db", daily_api_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    yield cfg, store, client
    store.close()


def test_sync_collection_writes_to_cache(setup, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, store, client = setup
    items = [
        CollectionItem(release_id=1, folder_id=0, instance_id=10, date_added=datetime.now(UTC)),
        CollectionItem(release_id=2, folder_id=0, instance_id=20, date_added=datetime.now(UTC)),
    ]
    monkeypatch.setattr("discogs.sync.syncer.fetch_collection", lambda _c: iter(items))
    monkeypatch.setattr("discogs.sync.syncer.fetch_wantlist", lambda _c, _u: iter([]))

    syncer = Syncer(cfg, store, client)
    result = syncer.sync(scope="collection")

    assert result.collection_synced == 2
    assert result.wantlist_synced is None
    assert store.collection_release_ids() == {1, 2}
    assert store.last_sync_at("collection") is not None


def test_sync_skips_when_within_ttl(setup, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, store, client = setup
    store.record_sync("collection", datetime.now(UTC) - timedelta(hours=1))
    called = {"n": 0}

    def fake_fetch(_c):
        called["n"] += 1
        return iter([])

    monkeypatch.setattr("discogs.sync.syncer.fetch_collection", fake_fetch)
    monkeypatch.setattr("discogs.sync.syncer.fetch_wantlist", lambda _c, _u: iter([]))

    syncer = Syncer(cfg, store, client)
    result = syncer.sync(scope="collection")

    assert result.collection_synced is None
    assert called["n"] == 0


def test_sync_force_bypasses_ttl(setup, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, store, client = setup
    store.record_sync("collection", datetime.now(UTC) - timedelta(hours=1))
    monkeypatch.setattr("discogs.sync.syncer.fetch_collection", lambda _c: iter([]))
    monkeypatch.setattr("discogs.sync.syncer.fetch_wantlist", lambda _c, _u: iter([]))

    syncer = Syncer(cfg, store, client)
    result = syncer.sync(scope="collection", force=True)

    assert result.collection_synced == 0


def test_sync_both_scope(setup, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, store, client = setup
    monkeypatch.setattr("discogs.sync.syncer.fetch_collection", lambda _c: iter([
        CollectionItem(release_id=1, folder_id=0, instance_id=10, date_added=datetime.now(UTC)),
    ]))
    monkeypatch.setattr("discogs.sync.syncer.fetch_wantlist", lambda _c, _u: iter([
        WantlistItem(release_id=99, date_added=datetime.now(UTC), notes=None),
    ]))

    syncer = Syncer(cfg, store, client)
    result = syncer.sync(scope="both")

    assert result.collection_synced == 1
    assert result.wantlist_synced == 1
