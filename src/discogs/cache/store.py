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
    from discogs.models import (
        Artist,
        ArtistInfluence,
        CollectionItem,
        Credit,
        Label,
        Release,
        WantlistItem,
    )

SCHEMA_FILE = Path(__file__).parent / "schema.sql"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
CURRENT_SCHEMA_VERSION = 3


def init_db(path: Path) -> None:
    """Create or upgrade the cache database. Idempotent.

    Fresh databases get the current schema directly from schema.sql and are
    stamped at CURRENT_SCHEMA_VERSION. Existing databases are upgraded by
    applying migrations/v{N}.sql for each version between their stored version
    and CURRENT_SCHEMA_VERSION — so a column added to an existing table reaches
    DBs that predate it (schema.sql's CREATE ... IF NOT EXISTS can't).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        existing = _stored_version(conn)
        conn.executescript(SCHEMA_FILE.read_text())
        if existing == 0:
            _stamp_version(conn, CURRENT_SCHEMA_VERSION)
        else:
            _apply_migrations(conn, from_version=existing)
        conn.commit()


def _stored_version(conn: sqlite3.Connection) -> int:
    """Highest applied schema version, or 0 for a brand-new database."""
    try:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0  # schema_version table doesn't exist yet
    return int(row[0]) if row and row[0] is not None else 0


def _stamp_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
        (version, datetime.now(UTC).isoformat()),
    )


def _apply_migrations(conn: sqlite3.Connection, *, from_version: int) -> None:
    for version in range(from_version + 1, CURRENT_SCHEMA_VERSION + 1):
        migration = MIGRATIONS_DIR / f"v{version}.sql"
        conn.executescript(migration.read_text())
        _stamp_version(conn, version)


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

        display_id is YYYY-MM-DD-HHMMSS in UTC and serves as the human handle
        used by `discogs apply <run-id>` (Phase 4).
        """
        run_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        display_id = now.strftime("%Y-%m-%d-%H%M%S")
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
        self, run_id: str, release_id: int, score: float,
        subscores: dict[str, float] | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO recommendation_history "
                "(release_id, run_id, score, subscores_json) VALUES (?, ?, ?, ?)",
                (release_id, run_id, score,
                 json.dumps(subscores) if subscores is not None else None),
            )

    def previously_recommended_release_ids(self) -> set[int]:
        return {
            int(r["release_id"])
            for r in self.conn.execute("SELECT DISTINCT release_id FROM recommendation_history")
        }

    def replace_artist_top_releases(self, artist_id: int, release_ids: list[int]) -> None:
        now = datetime.now(UTC).isoformat()
        with self.conn:
            self.conn.execute(
                "DELETE FROM artist_top_releases WHERE artist_id = ?", (artist_id,)
            )
            self.conn.executemany(
                "INSERT INTO artist_top_releases (artist_id, release_id, rank, fetched_at) "
                "VALUES (?, ?, ?, ?)",
                [(artist_id, rid, rank, now) for rank, rid in enumerate(release_ids)],
            )

    def get_artist_top_release_ids(self, artist_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT release_id FROM artist_top_releases "
            "WHERE artist_id = ? ORDER BY rank ASC",
            (artist_id,),
        )
        return [int(r["release_id"]) for r in rows]

    def artist_top_releases_age(self, artist_id: int) -> timedelta | None:
        row = self.conn.execute(
            "SELECT MIN(fetched_at) AS oldest FROM artist_top_releases WHERE artist_id = ?",
            (artist_id,),
        ).fetchone()
        if row is None or row["oldest"] is None:
            return None
        return datetime.now(UTC) - datetime.fromisoformat(row["oldest"])

    def increment_llm_calls(self, n: int = 1) -> None:
        today = datetime.now(UTC).date().isoformat()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO _llm_call_counts(day, count) VALUES (?, ?)
                ON CONFLICT(day) DO UPDATE SET count = count + excluded.count
                """,
                (today, n),
            )

    def llm_calls_today(self) -> int:
        today = datetime.now(UTC).date().isoformat()
        row = self.conn.execute(
            "SELECT count FROM _llm_call_counts WHERE day = ?", (today,)
        ).fetchone()
        return int(row["count"]) if row else 0

    def replace_artist_influences(
        self, source_artist_id: int, edges: list[ArtistInfluence], *,
        source: str = "claude",
    ) -> None:
        """Replace influence edges for a (source_artist_id, source) pair atomically.

        edges with a different `source` value are inserted alongside (no delete).
        """
        with self.conn:
            self.conn.execute(
                "DELETE FROM artist_influences "
                "WHERE source_artist_id = ? AND source = ?",
                (source_artist_id, source),
            )
            self.conn.executemany(
                "INSERT INTO artist_influences "
                "(source_artist_id, influence_artist_id, confidence, source, fetched_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (e.source_artist_id, e.influence_artist_id, e.confidence, e.source,
                     e.fetched_at.isoformat())
                    for e in edges
                ],
            )

    def get_artist_influences(self, source_artist_id: int) -> list[ArtistInfluence]:
        from discogs.models import ArtistInfluence
        rows = self.conn.execute(
            "SELECT source_artist_id, influence_artist_id, confidence, source, fetched_at "
            "FROM artist_influences WHERE source_artist_id = ?",
            (source_artist_id,),
        )
        return [
            ArtistInfluence(
                source_artist_id=r["source_artist_id"],
                influence_artist_id=r["influence_artist_id"],
                confidence=r["confidence"],
                source=r["source"],
                fetched_at=datetime.fromisoformat(r["fetched_at"]),
            )
            for r in rows
        ]

    def artist_influences_age(self, source_artist_id: int) -> timedelta | None:
        row = self.conn.execute(
            "SELECT MIN(fetched_at) AS oldest FROM artist_influences "
            "WHERE source_artist_id = ?",
            (source_artist_id,),
        ).fetchone()
        if row is None or row["oldest"] is None:
            return None
        return datetime.now(UTC) - datetime.fromisoformat(row["oldest"])

    # ------------------------------------------------------------------
    # Phase 4 — apply / undo helpers
    # ------------------------------------------------------------------

    def get_run_by_display_id(self, display_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT id FROM runs WHERE display_id = ?", (display_id,),
        ).fetchone()
        return str(row["id"]) if row else None

    def get_recommendations_for_run(self, run_id: str) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT release_id, score, applied_to_wantlist, applied_at, "
            "removed_at, removed_reason FROM recommendation_history "
            "WHERE run_id = ? ORDER BY score DESC",
            (run_id,),
        ).fetchall()
        return list(rows)

    def mark_recommendation_applied(
        self, run_id: str, release_id: int, applied_at: datetime,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE recommendation_history "
                "SET applied_to_wantlist = 1, applied_at = ?, "
                "    removed_at = NULL, removed_reason = NULL "
                "WHERE run_id = ? AND release_id = ?",
                (applied_at.isoformat(), run_id, release_id),
            )

    def mark_recommendation_removed(
        self, run_id: str, release_id: int, removed_at: datetime, reason: str,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE recommendation_history "
                "SET applied_to_wantlist = 0, removed_at = ?, removed_reason = ? "
                "WHERE run_id = ? AND release_id = ?",
                (removed_at.isoformat(), reason, run_id, release_id),
            )

    def last_applied_run_id(self) -> str | None:
        """Return the run_id of the most recently applied batch (latest applied_at)."""
        row = self.conn.execute(
            "SELECT run_id FROM recommendation_history "
            "WHERE applied_at IS NOT NULL "
            "ORDER BY applied_at DESC LIMIT 1"
        ).fetchone()
        return str(row["run_id"]) if row else None

    def has_any_apply(self) -> bool:
        """True iff any recommendation has ever been applied (drives first-apply confirm)."""
        row = self.conn.execute(
            "SELECT 1 FROM recommendation_history "
            "WHERE applied_at IS NOT NULL LIMIT 1"
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Phase 6 — explain / diff / stats
    # ------------------------------------------------------------------

    def get_recommendations_for_release(self, release_id: int) -> list[sqlite3.Row]:
        """Every run that recommended this release, newest run first.

        Columns: run_id, display_id, started_at, score, subscores_json,
        applied_to_wantlist.
        """
        return self.conn.execute(
            "SELECT h.run_id, r.display_id, r.started_at, h.score, "
            "h.subscores_json, h.applied_to_wantlist "
            "FROM recommendation_history h JOIN runs r ON r.id = h.run_id "
            "WHERE h.release_id = ? ORDER BY r.started_at DESC",
            (release_id,),
        ).fetchall()

    def _library_subquery(self, scope: str) -> str:
        """SQL that selects the library's release_ids for the given scope.

        Returned text is a fixed string keyed only on `scope` (no user input),
        safe to interpolate into the surrounding aggregate queries.
        """
        if scope == "collection":
            return "SELECT release_id FROM collection_items"
        if scope == "wantlist":
            return "SELECT release_id FROM wantlist_items"
        return (
            "SELECT release_id FROM collection_items "
            "UNION SELECT release_id FROM wantlist_items"
        )

    def library_size(self, scope: str = "both") -> int:
        """Distinct release_ids in the library (whether or not detail is cached)."""
        sub = self._library_subquery(scope)
        row = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM ({sub})"
        ).fetchone()
        return int(row["n"]) if row else 0

    def cached_release_count(self, scope: str = "both") -> int:
        """Library releases that have full detail cached in `releases`."""
        sub = self._library_subquery(scope)
        row = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM releases WHERE id IN ({sub})"
        ).fetchone()
        return int(row["n"]) if row else 0

    def decade_distribution(self, scope: str = "both") -> list[tuple[int, int]]:
        """(decade, count) over cached library releases, oldest first. Year 0 skipped."""
        sub = self._library_subquery(scope)
        rows = self.conn.execute(
            f"SELECT (year / 10) * 10 AS decade, COUNT(*) AS n FROM releases "
            f"WHERE id IN ({sub}) AND year > 0 "
            f"GROUP BY decade ORDER BY decade ASC"
        ).fetchall()
        return [(int(r["decade"]), int(r["n"])) for r in rows]

    def top_styles(self, scope: str = "both", limit: int = 10) -> list[tuple[str, int]]:
        sub = self._library_subquery(scope)
        rows = self.conn.execute(
            f"SELECT style, COUNT(*) AS n FROM release_styles "
            f"WHERE release_id IN ({sub}) "
            f"GROUP BY style ORDER BY n DESC, style ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(str(r["style"]), int(r["n"])) for r in rows]

    def top_labels(self, scope: str = "both", limit: int = 10) -> list[tuple[str, int]]:
        sub = self._library_subquery(scope)
        rows = self.conn.execute(
            f"SELECT l.name AS name, COUNT(*) AS n FROM release_labels rl "
            f"JOIN labels l ON l.id = rl.label_id "
            f"WHERE rl.release_id IN ({sub}) "
            f"GROUP BY l.id ORDER BY n DESC, name ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(str(r["name"]), int(r["n"])) for r in rows]

    # ------------------------------------------------------------------
    # Spotify interchange
    # ------------------------------------------------------------------

    def upsert_spotify_artist(
        self,
        *,
        spotify_artist_id: str,
        name: str,
        liked_track_count: int,
        discogs_artist_id: int | None,
        match_method: str,
        resolved_at: str,
    ) -> None:
        """Record one Spotify artist and how it resolved.

        `liked_track_count` is refreshed on every import because the
        library grows, but the resolution is only overwritten when this
        run actually produced one — a re-import that skipped an
        already-resolved artist must not blank it back to unresolved.
        """
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO spotify_artists (
                    spotify_artist_id, name, discogs_artist_id,
                    liked_track_count, match_method, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(spotify_artist_id) DO UPDATE SET
                    name = excluded.name,
                    liked_track_count = excluded.liked_track_count,
                    discogs_artist_id = COALESCE(
                        excluded.discogs_artist_id, spotify_artists.discogs_artist_id
                    ),
                    match_method = excluded.match_method,
                    resolved_at = excluded.resolved_at
                """,
                (
                    spotify_artist_id,
                    name,
                    discogs_artist_id,
                    liked_track_count,
                    match_method,
                    resolved_at,
                ),
            )

    def spotify_artist_resolutions(self) -> dict[str, int | None]:
        """Every imported Spotify artist id mapped to its Discogs id."""
        return {
            str(r["spotify_artist_id"]): (
                int(r["discogs_artist_id"]) if r["discogs_artist_id"] is not None else None
            )
            for r in self.conn.execute(
                "SELECT spotify_artist_id, discogs_artist_id FROM spotify_artists"
            )
        }

    def spotify_seed_weights(self) -> dict[int, int]:
        """Discogs artist id → liked-track count, resolved artists only.

        Summed, because two Spotify artist ids can resolve to one Discogs
        artist — the same act credited separately on different releases.
        """
        rows = self.conn.execute(
            "SELECT discogs_artist_id AS aid, SUM(liked_track_count) AS n "
            "FROM spotify_artists WHERE discogs_artist_id IS NOT NULL "
            "GROUP BY discogs_artist_id"
        ).fetchall()
        return {int(r["aid"]): int(r["n"]) for r in rows}

    def spotify_import_counts(self) -> tuple[int, int]:
        """(total imported, resolved to a Discogs artist)."""
        row = self.conn.execute(
            "SELECT COUNT(*) AS total, "
            "COUNT(discogs_artist_id) AS resolved FROM spotify_artists"
        ).fetchone()
        return int(row["total"]), int(row["resolved"])
