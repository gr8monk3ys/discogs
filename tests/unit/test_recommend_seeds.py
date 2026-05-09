from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import CollectionItem, Credit, WantlistItem
from discogs.recommend.seeds import SeedArtist, select_seeds


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def _seed_release(store: CacheStore, release_id: int, artist_credits: list[tuple[int, str]]) -> None:
    """Insert one library release and its credits without triggering full release upsert."""
    with store.conn:
        store.conn.execute(
            "INSERT OR IGNORE INTO releases ("
            "id, master_id, title, year, country, formats_json, "
            "community_have, community_want, community_avg_rating, community_rating_count, fetched_at"
            ") VALUES (?, NULL, ?, 1970, NULL, '[]', 0, 0, 0.0, 0, ?)",
            (release_id, f"r{release_id}", datetime.now(UTC).isoformat()),
        )
    store.replace_release_credits(
        release_id,
        [Credit(release_id=release_id, artist_id=aid, role=role) for aid, role in artist_credits],
    )


def test_seeds_filter_by_min_occurrences(store: CacheStore) -> None:
    # Two releases in collection, one in wantlist.
    _seed_release(store, 1, [(7, "Saxophone"), (99, "Engineer")])
    _seed_release(store, 2, [(7, "Saxophone")])
    _seed_release(store, 3, [(99, "Producer")])

    store.replace_collection([
        CollectionItem(release_id=1, folder_id=0, instance_id=10, date_added=datetime.now(UTC)),
        CollectionItem(release_id=2, folder_id=0, instance_id=20, date_added=datetime.now(UTC)),
    ])
    store.replace_wantlist([
        WantlistItem(release_id=3, date_added=datetime.now(UTC), notes=None),
    ])

    seeds = select_seeds(store, mode="both", min_occurrences=2)
    seed_ids = {s.artist_id for s in seeds}
    assert seed_ids == {7, 99}  # both appear ≥ 2x across library


def test_seeds_respect_mode(store: CacheStore) -> None:
    _seed_release(store, 1, [(7, "A"), (8, "B")])
    _seed_release(store, 2, [(7, "A")])
    _seed_release(store, 3, [(8, "B")])

    store.replace_collection([
        CollectionItem(release_id=1, folder_id=0, instance_id=10, date_added=datetime.now(UTC)),
        CollectionItem(release_id=2, folder_id=0, instance_id=20, date_added=datetime.now(UTC)),
    ])
    store.replace_wantlist([
        WantlistItem(release_id=3, date_added=datetime.now(UTC), notes=None),
    ])

    coll_only = select_seeds(store, mode="collection", min_occurrences=2)
    assert {s.artist_id for s in coll_only} == {7}

    want_only = select_seeds(store, mode="wantlist", min_occurrences=1)
    assert {s.artist_id for s in want_only} == {8}


def test_seed_weights_in_range(store: CacheStore) -> None:
    _seed_release(store, 1, [(7, "A"), (8, "A"), (9, "A")])
    _seed_release(store, 2, [(7, "A"), (8, "A")])
    _seed_release(store, 3, [(7, "A")])

    store.replace_collection([
        CollectionItem(release_id=r, folder_id=0, instance_id=10 * r, date_added=datetime.now(UTC))
        for r in (1, 2, 3)
    ])

    seeds = select_seeds(store, mode="collection", min_occurrences=1)
    weights = {s.artist_id: s.weight for s in seeds}
    assert all(0.1 <= w <= 1.0 for w in weights.values())
    # Artist 7 appears most often → smallest weight (least obscure within library)
    assert weights[7] <= weights[9]


def test_no_seeds_when_library_empty(store: CacheStore) -> None:
    seeds = select_seeds(store, mode="both", min_occurrences=2)
    assert seeds == []


def test_seed_artist_immutable() -> None:
    s = SeedArtist(artist_id=1, weight=0.5, sources=("collection",))
    with pytest.raises((AttributeError, TypeError)):
        s.weight = 0.9  # type: ignore[misc]
