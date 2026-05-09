from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import CollectionItem, WantlistItem


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def test_replace_collection_inserts_all(store: CacheStore) -> None:
    items = [
        CollectionItem(release_id=1, folder_id=0, instance_id=10, date_added=datetime.now(UTC)),
        CollectionItem(release_id=2, folder_id=0, instance_id=20, date_added=datetime.now(UTC)),
    ]
    store.replace_collection(items)

    fetched = list(store.iter_collection())
    assert {i.release_id for i in fetched} == {1, 2}


def test_replace_collection_overwrites_previous(store: CacheStore) -> None:
    store.replace_collection([
        CollectionItem(release_id=1, folder_id=0, instance_id=10, date_added=datetime.now(UTC)),
    ])
    store.replace_collection([
        CollectionItem(release_id=2, folder_id=0, instance_id=20, date_added=datetime.now(UTC)),
    ])
    fetched = list(store.iter_collection())
    assert [i.release_id for i in fetched] == [2]


def test_replace_wantlist(store: CacheStore) -> None:
    items = [
        WantlistItem(release_id=42, date_added=datetime.now(UTC), notes="signed"),
    ]
    store.replace_wantlist(items)
    fetched = list(store.iter_wantlist())
    assert fetched[0].release_id == 42
    assert fetched[0].notes == "signed"


def test_collection_release_ids_excludes_wantlist(store: CacheStore) -> None:
    store.replace_collection([
        CollectionItem(release_id=1, folder_id=0, instance_id=10, date_added=datetime.now(UTC)),
    ])
    store.replace_wantlist([
        WantlistItem(release_id=2, date_added=datetime.now(UTC), notes=None),
    ])
    assert store.collection_release_ids() == {1}
    assert store.wantlist_release_ids() == {2}
