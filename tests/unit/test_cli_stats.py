from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from discogs.cache.store import CacheStore, init_db
from discogs.cli.__main__ import cli
from discogs.models import CollectionItem, Format, Release


def _config(home: Path) -> Path:
    cfg_dir = home / ".discogs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text('[discogs]\ntoken = "t"\nusername = "lorenzo"')
    return cfg_dir / "cache.db"


def _release(rid: int, year: int, styles: list[str]) -> Release:
    return Release(
        id=rid, title=f"r{rid}", year=year,
        formats=[Format(name="Vinyl", descriptions=["LP", "Album"])],
        styles=styles, genres=["Jazz"],
        community_have=100, community_want=50,
        community_avg_rating=4.0, community_rating_count=10,
        fetched_at=datetime.now(UTC),
    )


def test_stats_shows_decades_and_styles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    db = _config(tmp_path)
    init_db(db)
    store = CacheStore(db)
    store.upsert_release(_release(1, 1975, ["Jazz"]))
    store.upsert_release(_release(2, 1985, ["Jazz"]))
    now = datetime.now(UTC)
    store.replace_collection([
        CollectionItem(release_id=1, folder_id=0, instance_id=1, date_added=now),
        CollectionItem(release_id=2, folder_id=0, instance_id=2, date_added=now),
    ])
    store.close()

    result = CliRunner().invoke(cli, ["stats", "--scope", "collection"])
    assert result.exit_code == 0, result.output
    assert "1970s" in result.output
    assert "Jazz" in result.output
    assert "2 releases" in result.output


def test_stats_empty_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    db = _config(tmp_path)
    init_db(db)

    result = CliRunner().invoke(cli, ["stats"])
    assert result.exit_code == 0, result.output
    assert "No releases" in result.output
