"""Orchestrate collection + wantlist sync into the local cache."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from discogs.api.client import DiscogsClient
from discogs.api.collection import fetch_collection
from discogs.api.wantlist import fetch_wantlist
from discogs.cache.store import CacheStore
from discogs.config import Config

Scope = Literal["collection", "wantlist", "both"]
DEFAULT_TTL = timedelta(hours=24)


@dataclass
class SyncResult:
    collection_synced: int | None  # None means skipped (within TTL)
    wantlist_synced: int | None


class Syncer:
    def __init__(
        self, config: Config, store: CacheStore, client: DiscogsClient,
        *, ttl: timedelta = DEFAULT_TTL,
    ) -> None:
        self._config = config
        self._store = store
        self._client = client
        self._ttl = ttl

    def sync(self, *, scope: Scope = "both", force: bool = False) -> SyncResult:
        coll = self._sync_collection(force) if scope in ("collection", "both") else None
        want = self._sync_wantlist(force) if scope in ("wantlist", "both") else None
        return SyncResult(collection_synced=coll, wantlist_synced=want)

    def _is_fresh(self, scope: str) -> bool:
        last = self._store.last_sync_at(scope)
        if last is None:
            return False
        return datetime.now(UTC) - last < self._ttl

    def _sync_collection(self, force: bool) -> int | None:
        if not force and self._is_fresh("collection"):
            return None
        items = list(fetch_collection(self._client))
        self._store.replace_collection(items)
        self._store.record_sync("collection", datetime.now(UTC))
        return len(items)

    def _sync_wantlist(self, force: bool) -> int | None:
        if not force and self._is_fresh("wantlist"):
            return None
        items = list(fetch_wantlist(self._client, self._config.discogs_username))
        self._store.replace_wantlist(items)
        self._store.record_sync("wantlist", datetime.now(UTC))
        return len(items)
