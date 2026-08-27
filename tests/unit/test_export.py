"""Tests for the ~/.music/discogs.json export (built from the cache, no API)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from discogs.cache.store import CacheStore, init_db
from discogs.export import SCHEMA, build_export, write_export
from discogs.models import CollectionItem, Format, Release, WantlistItem


def _store(tmp_path: Path) -> CacheStore:
    init_db(tmp_path / "c.db")
    return CacheStore(tmp_path / "c.db")


def _rel(i: int, title: str, artist: str) -> Release:
    return Release(
        id=i, master_id=i * 10, title=title, year=1990 + i, artists=[artist],
        formats=[Format(name="Vinyl")], community_have=0, community_want=0,
        community_avg_rating=0.0, community_rating_count=0, fetched_at=datetime.now(UTC),
    )


def test_export_lists_collection_and_wantlist(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.upsert_release(_rel(1, "Thriller", "Michael Jackson"))
    s.upsert_release(_rel(2, "Kid A", "Radiohead"))
    now = datetime.now(UTC)
    s.replace_collection([CollectionItem(release_id=1, folder_id=1, instance_id=1, date_added=now)])
    s.replace_wantlist([WantlistItem(release_id=2, date_added=now, notes=None)])

    doc = build_export(s, "u", "2026-08-26T00:00:00Z")

    assert doc["schema"] == SCHEMA
    assert doc["username"] == "u"
    assert doc["generated_at"] == "2026-08-26T00:00:00Z"
    assert doc["collection"][0] == {
        "release_id": 1, "master_id": 10, "title": "Thriller",
        "artists": ["Michael Jackson"], "year": 1991, "formats": ["Vinyl"],
        "added_at": now.isoformat(),
    }
    assert [w["title"] for w in doc["wantlist"]] == ["Kid A"]


def test_export_skips_items_whose_release_is_not_cached(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.replace_collection(
        [CollectionItem(release_id=99, folder_id=1, instance_id=1, date_added=datetime.now(UTC))]
    )
    assert build_export(s, "u", "t")["collection"] == []


def test_write_export_creates_parent(tmp_path: Path) -> None:
    p = write_export({"schema": SCHEMA}, tmp_path / "deep" / "discogs.json")
    assert p.exists()
    assert not (tmp_path / "deep" / "discogs.json.tmp").exists()
