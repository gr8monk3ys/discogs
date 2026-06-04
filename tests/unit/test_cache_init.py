import sqlite3
from pathlib import Path

from discogs.cache.store import CURRENT_SCHEMA_VERSION, CacheStore, init_db


def test_init_db_creates_all_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = {r[0] for r in rows}

    expected = {
        "releases", "masters", "artists", "labels",
        "release_credits", "release_labels",
        "release_styles", "release_genres",
        "collection_items", "wantlist_items",
        "artist_influences", "artist_top_releases",
        "recommendation_history", "runs",
        "schema_version",
    }
    assert expected.issubset(table_names)


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    init_db(db_path)
    init_db(db_path)


def test_cache_store_opens_initialized_db(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    init_db(db_path)
    store = CacheStore(db_path)
    assert store.schema_version == CURRENT_SCHEMA_VERSION
    store.close()
