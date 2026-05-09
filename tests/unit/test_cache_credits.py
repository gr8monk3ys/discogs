from collections.abc import Iterator
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import Credit


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def test_replace_release_credits_inserts(store: CacheStore) -> None:
    store.replace_release_credits(
        release_id=10,
        credits=[
            Credit(release_id=10, artist_id=1, role="Producer"),
            Credit(release_id=10, artist_id=2, role="Engineer"),
        ],
    )
    fetched = store.get_release_credits(10)
    assert {(c.artist_id, c.role) for c in fetched} == {(1, "Producer"), (2, "Engineer")}


def test_replace_release_credits_overwrites(store: CacheStore) -> None:
    store.replace_release_credits(
        release_id=10,
        credits=[Credit(release_id=10, artist_id=1, role="Producer")],
    )
    store.replace_release_credits(
        release_id=10,
        credits=[Credit(release_id=10, artist_id=99, role="Bass")],
    )
    fetched = store.get_release_credits(10)
    assert {c.artist_id for c in fetched} == {99}


def test_get_release_credits_empty_when_missing(store: CacheStore) -> None:
    assert store.get_release_credits(999) == []


def test_replace_release_labels_inserts(store: CacheStore) -> None:
    store.replace_release_labels(
        release_id=10,
        labels=[(101, "AS-9181"), (102, None)],
    )
    fetched = store.get_release_label_ids(10)
    assert set(fetched) == {101, 102}


def test_replace_release_labels_overwrites(store: CacheStore) -> None:
    store.replace_release_labels(release_id=10, labels=[(101, "X")])
    store.replace_release_labels(release_id=10, labels=[(202, "Y")])
    assert set(store.get_release_label_ids(10)) == {202}
