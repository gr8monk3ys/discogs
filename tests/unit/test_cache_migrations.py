"""Schema migration runner (F0).

Both a fresh DB and a pre-existing v1 DB must converge on the current schema
version with the subscores_json column present.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from discogs.cache.store import CURRENT_SCHEMA_VERSION, CacheStore, init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _make_v1_db(path: Path) -> None:
    """Build a DB shaped like schema version 1 (no subscores_json column)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
        );
        CREATE TABLE runs (
            id TEXT PRIMARY KEY, display_id TEXT NOT NULL UNIQUE,
            started_at TEXT NOT NULL, finished_at TEXT,
            args_json TEXT, summary_json TEXT
        );
        CREATE TABLE recommendation_history (
            release_id INTEGER NOT NULL, run_id TEXT NOT NULL, score REAL NOT NULL,
            applied_to_wantlist INTEGER NOT NULL DEFAULT 0,
            applied_at TEXT, removed_at TEXT, removed_reason TEXT,
            PRIMARY KEY (release_id, run_id)
        );
        INSERT INTO schema_version(version, applied_at)
            VALUES (1, '2026-01-01T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()


def test_fresh_db_reaches_current_version(tmp_path: Path) -> None:
    db = tmp_path / "cache.db"
    init_db(db)
    store = CacheStore(db)
    try:
        assert store.schema_version == CURRENT_SCHEMA_VERSION
        assert "subscores_json" in _columns(store.conn, "recommendation_history")
    finally:
        store.close()


def test_v1_db_migrates_to_v2(tmp_path: Path) -> None:
    db = tmp_path / "cache.db"
    _make_v1_db(db)

    # Sanity: the v1 fixture really lacks the column.
    pre = sqlite3.connect(db)
    assert "subscores_json" not in _columns(pre, "recommendation_history")
    pre.close()

    init_db(db)

    store = CacheStore(db)
    try:
        assert store.schema_version == CURRENT_SCHEMA_VERSION
        assert "subscores_json" in _columns(store.conn, "recommendation_history")
    finally:
        store.close()


def test_v1_data_survives_migration(tmp_path: Path) -> None:
    db = tmp_path / "cache.db"
    _make_v1_db(db)
    seed = sqlite3.connect(db)
    seed.execute(
        "INSERT INTO runs (id, display_id, started_at) VALUES ('r1', '2026-01-01-000000', 'x')"
    )
    seed.execute(
        "INSERT INTO recommendation_history (release_id, run_id, score) VALUES (42, 'r1', 0.7)"
    )
    seed.commit()
    seed.close()

    init_db(db)

    store = CacheStore(db)
    try:
        row = store.conn.execute(
            "SELECT score, subscores_json FROM recommendation_history WHERE release_id = 42"
        ).fetchone()
        assert row["score"] == 0.7
        assert row["subscores_json"] is None  # old rows have no breakdown
    finally:
        store.close()


def test_init_db_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "cache.db"
    init_db(db)
    init_db(db)  # second call must not raise (no duplicate-column ALTER)
    store = CacheStore(db)
    try:
        assert store.schema_version == CURRENT_SCHEMA_VERSION
    finally:
        store.close()


def test_v3_database_gains_artists_column(tmp_path: Path) -> None:
    from discogs.cache.store import SCHEMA_FILE

    db = tmp_path / "cache.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA_FILE.read_text())
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version VALUES (3, 'x')")
    conn.commit()
    conn.close()

    init_db(db)

    post = sqlite3.connect(db)
    assert "artists_json" in _columns(post, "releases")
    post.close()
