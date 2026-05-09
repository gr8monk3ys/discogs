"""SQLite-backed cache for Discogs data and recommendation history."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from discogs.models import Release

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

    def upsert_release(self, release: "Release") -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO releases (
                    id, master_id, title, year, country, formats_json,
                    community_have, community_want, community_avg_rating,
                    community_rating_count, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    master_id=excluded.master_id,
                    title=excluded.title,
                    year=excluded.year,
                    country=excluded.country,
                    formats_json=excluded.formats_json,
                    community_have=excluded.community_have,
                    community_want=excluded.community_want,
                    community_avg_rating=excluded.community_avg_rating,
                    community_rating_count=excluded.community_rating_count,
                    fetched_at=excluded.fetched_at
                """,
                (
                    release.id, release.master_id, release.title, release.year,
                    release.country, json.dumps([f.model_dump() for f in release.formats]),
                    release.community_have, release.community_want,
                    release.community_avg_rating, release.community_rating_count,
                    release.fetched_at.isoformat(),
                ),
            )
            self.conn.execute("DELETE FROM release_styles WHERE release_id = ?", (release.id,))
            self.conn.executemany(
                "INSERT INTO release_styles (release_id, style) VALUES (?, ?)",
                [(release.id, s) for s in release.styles],
            )
            self.conn.execute("DELETE FROM release_genres WHERE release_id = ?", (release.id,))
            self.conn.executemany(
                "INSERT INTO release_genres (release_id, genre) VALUES (?, ?)",
                [(release.id, g) for g in release.genres],
            )

    def get_release(self, release_id: int) -> "Release | None":
        from discogs.models import Format, Release
        row = self.conn.execute(
            "SELECT * FROM releases WHERE id = ?", (release_id,)
        ).fetchone()
        if row is None:
            return None
        styles = [
            r["style"] for r in self.conn.execute(
                "SELECT style FROM release_styles WHERE release_id = ?", (release_id,)
            )
        ]
        genres = [
            r["genre"] for r in self.conn.execute(
                "SELECT genre FROM release_genres WHERE release_id = ?", (release_id,)
            )
        ]
        formats = [Format(**f) for f in json.loads(row["formats_json"])]
        return Release(
            id=row["id"],
            master_id=row["master_id"],
            title=row["title"],
            year=row["year"],
            country=row["country"],
            formats=formats,
            styles=styles,
            genres=genres,
            community_have=row["community_have"],
            community_want=row["community_want"],
            community_avg_rating=row["community_avg_rating"],
            community_rating_count=row["community_rating_count"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
        )

    def release_age(self, release_id: int) -> timedelta | None:
        row = self.conn.execute(
            "SELECT fetched_at FROM releases WHERE id = ?", (release_id,)
        ).fetchone()
        if row is None:
            return None
        return datetime.now(UTC) - datetime.fromisoformat(row["fetched_at"])
