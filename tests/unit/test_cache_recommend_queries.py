"""Store query methods backing explain / diff / stats (Phase 6)."""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import CollectionItem, Format, Label, Release


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def _release(rid: int, year: int, styles: list[str]) -> Release:
    return Release(
        id=rid, title=f"r{rid}", year=year,
        formats=[Format(name="Vinyl", descriptions=["LP", "Album"])],
        styles=styles, genres=["Jazz"],
        community_have=100, community_want=50,
        community_avg_rating=4.0, community_rating_count=10,
        fetched_at=datetime.now(UTC),
    )


def _collect(store: CacheStore, *release_ids: int) -> None:
    now = datetime.now(UTC)
    store.replace_collection([
        CollectionItem(release_id=rid, folder_id=0, instance_id=i, date_added=now)
        for i, rid in enumerate(release_ids, start=1)
    ])


def test_library_size_counts_distinct(store: CacheStore) -> None:
    store.upsert_release(_release(1, 1975, ["Jazz"]))
    store.upsert_release(_release(2, 1985, ["Jazz"]))
    _collect(store, 1, 2, 3)  # 3 has no cached detail
    assert store.library_size("collection") == 3
    assert store.cached_release_count("collection") == 2


def test_decade_distribution(store: CacheStore) -> None:
    store.upsert_release(_release(1, 1975, ["Jazz"]))
    store.upsert_release(_release(2, 1978, ["Jazz"]))
    store.upsert_release(_release(3, 1985, ["Jazz"]))
    _collect(store, 1, 2, 3)
    assert store.decade_distribution("collection") == [(1970, 2), (1980, 1)]


def test_top_styles_ranked(store: CacheStore) -> None:
    store.upsert_release(_release(1, 1975, ["Jazz", "Fusion"]))
    store.upsert_release(_release(2, 1985, ["Jazz"]))
    _collect(store, 1, 2)
    assert store.top_styles("collection") == [("Jazz", 2), ("Fusion", 1)]


def test_top_labels_ranked(store: CacheStore) -> None:
    now = datetime.now(UTC)
    store.upsert_release(_release(1, 1975, ["Jazz"]))
    store.upsert_release(_release(2, 1985, ["Jazz"]))
    store.upsert_label(Label(id=10, name="BN", parent_label=None, releases_count=500, fetched_at=now))
    store.replace_release_labels(1, [(10, "BN-1")])
    store.replace_release_labels(2, [(10, "BN-2")])
    _collect(store, 1, 2)
    assert store.top_labels("collection") == [("BN", 2)]


def test_record_recommendation_persists_subscores(store: CacheStore) -> None:
    run_id, display_id = store.start_run(args={})
    subs = {"connection": 0.5, "rarity": 0.3}
    store.record_recommendation(run_id, release_id=1, score=0.8, subscores=subs)

    rows = store.get_recommendations_for_release(1)
    assert len(rows) == 1
    assert rows[0]["display_id"] == display_id
    assert rows[0]["score"] == 0.8
    assert json.loads(rows[0]["subscores_json"]) == subs


def test_record_recommendation_without_subscores_stores_null(store: CacheStore) -> None:
    run_id, _ = store.start_run(args={})
    store.record_recommendation(run_id, release_id=1, score=0.8)
    rows = store.get_recommendations_for_release(1)
    assert rows[0]["subscores_json"] is None


def test_get_recommendations_for_release_empty(store: CacheStore) -> None:
    assert store.get_recommendations_for_release(999) == []
