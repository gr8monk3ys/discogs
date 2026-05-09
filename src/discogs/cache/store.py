"""SQLite-backed cache for Discogs data and recommendation history."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_FILE = Path(__file__).parent / "schema.sql"
CURRENT_SCHEMA_VERSION = 1


def init_db(path: Path) -> None:
    """Create the cache database and apply the schema. Idempotent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = SCHEMA_FILE.read_text()
    with sqlite3.connect(path) as conn:
        conn.executescript(schema_sql)
        conn.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
            (CURRENT_SCHEMA_VERSION, datetime.now(UTC).isoformat()),
        )
        conn.commit()


class CacheStore:
    """Read/write API over the SQLite cache."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    @property
    def schema_version(self) -> int:
        row = self.conn.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()
        return int(row["v"]) if row and row["v"] is not None else 0

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "CacheStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
