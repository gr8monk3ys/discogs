"""SQLite-backed cache for Discogs data and recommendation history."""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from discogs.models import Artist, CollectionItem, Credit, Label, Release, WantlistItem

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

    def __enter__(self) -> CacheStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def upsert_release(self, release: Release) -> None:
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

    def get_release(self, release_id: int) -> Release | None:
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

    def replace_collection(self, items: Iterable[CollectionItem]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM collection_items")
            self.conn.executemany(
                "INSERT INTO collection_items (release_id, folder_id, instance_id, date_added) "
                "VALUES (?, ?, ?, ?)",
                [
                    (i.release_id, i.folder_id, i.instance_id, i.date_added.isoformat())
                    for i in items
                ],
            )

    def iter_collection(self) -> Iterator[CollectionItem]:
        from discogs.models import CollectionItem
        rows = self.conn.execute(
            "SELECT * FROM collection_items ORDER BY date_added DESC"
        )
        for row in rows:
            yield CollectionItem(
                release_id=row["release_id"],
                folder_id=row["folder_id"],
                instance_id=row["instance_id"],
                date_added=datetime.fromisoformat(row["date_added"]),
            )

    def collection_release_ids(self) -> set[int]:
        return {
            int(r["release_id"])
            for r in self.conn.execute("SELECT release_id FROM collection_items")
        }

    def replace_wantlist(self, items: Iterable[WantlistItem]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM wantlist_items")
            self.conn.executemany(
                "INSERT INTO wantlist_items (release_id, date_added, notes) VALUES (?, ?, ?)",
                [(i.release_id, i.date_added.isoformat(), i.notes) for i in items],
            )

    def iter_wantlist(self) -> Iterator[WantlistItem]:
        from discogs.models import WantlistItem
        rows = self.conn.execute(
            "SELECT * FROM wantlist_items ORDER BY date_added DESC"
        )
        for row in rows:
            yield WantlistItem(
                release_id=row["release_id"],
                date_added=datetime.fromisoformat(row["date_added"]),
                notes=row["notes"],
            )

    def wantlist_release_ids(self) -> set[int]:
        return {
            int(r["release_id"])
            for r in self.conn.execute("SELECT release_id FROM wantlist_items")
        }

    def record_sync(self, scope: str, when: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO _sync_metadata(scope, last_sync_at) VALUES (?, ?)",
                (scope, when.isoformat()),
            )

    def last_sync_at(self, scope: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT last_sync_at FROM _sync_metadata WHERE scope = ?", (scope,)
        ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row["last_sync_at"])

    def increment_api_calls(self, n: int = 1) -> None:
        today = datetime.now(UTC).date().isoformat()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO _api_call_counts(day, count) VALUES (?, ?)
                ON CONFLICT(day) DO UPDATE SET count = count + excluded.count
                """,
                (today, n),
            )

    def api_calls_today(self) -> int:
        today = datetime.now(UTC).date().isoformat()
        row = self.conn.execute(
            "SELECT count FROM _api_call_counts WHERE day = ?", (today,)
        ).fetchone()
        return int(row["count"]) if row else 0

    def upsert_artist(self, artist: Artist) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO artists (id, name, profile, fetched_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    profile=excluded.profile,
                    fetched_at=excluded.fetched_at
                """,
                (artist.id, artist.name, artist.profile, artist.fetched_at.isoformat()),
            )

    def get_artist(self, artist_id: int) -> Artist | None:
        from discogs.models import Artist
        row = self.conn.execute(
            "SELECT * FROM artists WHERE id = ?", (artist_id,)
        ).fetchone()
        if row is None:
            return None
        return Artist(
            id=row["id"],
            name=row["name"],
            profile=row["profile"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
        )

    def artist_age(self, artist_id: int) -> timedelta | None:
        row = self.conn.execute(
            "SELECT fetched_at FROM artists WHERE id = ?", (artist_id,)
        ).fetchone()
        if row is None:
            return None
        return datetime.now(UTC) - datetime.fromisoformat(row["fetched_at"])

    def upsert_label(self, label: Label) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO labels (id, name, parent_label, releases_count, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    parent_label=excluded.parent_label,
                    releases_count=excluded.releases_count,
                    fetched_at=excluded.fetched_at
                """,
                (
                    label.id, label.name, label.parent_label,
                    label.releases_count, label.fetched_at.isoformat(),
                ),
            )

    def get_label(self, label_id: int) -> Label | None:
        from discogs.models import Label
        row = self.conn.execute(
            "SELECT * FROM labels WHERE id = ?", (label_id,)
        ).fetchone()
        if row is None:
            return None
        return Label(
            id=row["id"],
            name=row["name"],
            parent_label=row["parent_label"],
            releases_count=row["releases_count"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
        )

    def label_age(self, label_id: int) -> timedelta | None:
        row = self.conn.execute(
            "SELECT fetched_at FROM labels WHERE id = ?", (label_id,)
        ).fetchone()
        if row is None:
            return None
        return datetime.now(UTC) - datetime.fromisoformat(row["fetched_at"])

    def replace_release_credits(self, release_id: int, credits: list[Credit]) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM release_credits WHERE release_id = ?", (release_id,)
            )
            self.conn.executemany(
                "INSERT INTO release_credits (release_id, artist_id, role) VALUES (?, ?, ?)",
                [(c.release_id, c.artist_id, c.role) for c in credits],
            )

    def get_release_credits(self, release_id: int) -> list[Credit]:
        from discogs.models import Credit
        rows = self.conn.execute(
            "SELECT release_id, artist_id, role FROM release_credits WHERE release_id = ?",
            (release_id,),
        )
        return [
            Credit(release_id=r["release_id"], artist_id=r["artist_id"], role=r["role"])
            for r in rows
        ]

    def replace_release_labels(
        self, release_id: int, labels: list[tuple[int, str | None]]
    ) -> None:
        """labels = list of (label_id, catalog_number)."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM release_labels WHERE release_id = ?", (release_id,)
            )
            self.conn.executemany(
                "INSERT INTO release_labels (release_id, label_id, catalog_number) "
                "VALUES (?, ?, ?)",
                [(release_id, lid, cat) for lid, cat in labels],
            )

    def get_release_label_ids(self, release_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT label_id FROM release_labels WHERE release_id = ?", (release_id,)
        )
        return [int(r["label_id"]) for r in rows]

    def start_run(self, args: dict[str, object]) -> tuple[str, str]:
        """Insert a new row in `runs`, return (uuid, display_id).

        display_id is YYYY-MM-DD-HHMM in UTC and serves as the human handle
        used by `discogs apply <run-id>` (Phase 4).
        """
        run_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        display_id = now.strftime("%Y-%m-%d-%H%M")
        with self.conn:
            self.conn.execute(
                "INSERT INTO runs (id, display_id, started_at, args_json) VALUES (?, ?, ?, ?)",
                (run_id, display_id, now.isoformat(), json.dumps(args)),
            )
        return run_id, display_id

    def finish_run(self, run_id: str, summary: dict[str, object]) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET finished_at = ?, summary_json = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), json.dumps(summary), run_id),
            )

    def record_recommendation(
        self, run_id: str, release_id: int, score: float
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO recommendation_history (release_id, run_id, score) VALUES (?, ?, ?)",
                (release_id, run_id, score),
            )

    def previously_recommended_release_ids(self) -> set[int]:
        return {
            int(r["release_id"])
            for r in self.conn.execute("SELECT DISTINCT release_id FROM recommendation_history")
        }
