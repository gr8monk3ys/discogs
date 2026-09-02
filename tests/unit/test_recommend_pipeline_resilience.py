"""A single failed release fetch must not abort the whole recommend run.

Both fetch sites in pipeline.py (the credit prefetch and the candidate detail
load) used to let any exception propagate, so one 404 or transient connection
reset killed the entire run. These tests pin the skip-and-continue behavior,
including transient network errors (requests' ConnectionError is an OSError).
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from discogs.cache.store import CacheStore, init_db
from discogs.models import CollectionItem
from discogs.recommend.pipeline import _load_releases, _prefetch_library_releases


class _BoomClient:
    """A stand-in client whose every call raises a transient network error."""

    def call(self, *_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionError("connection reset by peer")  # an OSError subclass


def _store(tmp_path: Path) -> CacheStore:
    db = tmp_path / "cache.db"
    init_db(db)
    return CacheStore(db)


def test_load_releases_skips_transient_network_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        # None of these are cached, so each triggers a fetch -> ConnectionError.
        out = _load_releases(_BoomClient(), store, [1, 2, 3], budget_left=10)  # type: ignore[arg-type]
        assert out == {}  # all skipped, nothing raised
    finally:
        store.close()


def test_prefetch_skips_transient_network_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        store.replace_collection(
            [CollectionItem(release_id=1, folder_id=0, instance_id=1,
                            date_added=datetime.now(UTC))]
        )
        n = _prefetch_library_releases(
            _BoomClient(), store, scope="collection", daily_budget=100,  # type: ignore[arg-type]
        )
        assert n == 0  # the one library release failed to fetch but didn't crash
    finally:
        store.close()
