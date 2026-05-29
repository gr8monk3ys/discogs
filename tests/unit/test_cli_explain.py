from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from discogs.cache.store import CacheStore, init_db
from discogs.cli.__main__ import cli
from discogs.models import Format, Release

_SUBSCORES = {
    "connection": 1.0, "influence_chain": 0.0, "rarity": 0.6, "demand_ratio": 0.4,
    "label_obscurity": 0.5, "style_niche": 0.7, "rating": 0.8, "format": 1.0,
    "recency_match": 0.3,
}


def _config(home: Path) -> Path:
    cfg_dir = home / ".discogs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text('[discogs]\ntoken = "t"\nusername = "lorenzo"')
    return cfg_dir / "cache.db"


def test_explain_shows_breakdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    db = _config(tmp_path)
    init_db(db)
    store = CacheStore(db)
    store.upsert_release(Release(
        id=1, title="Kind of Blue", year=1959,
        formats=[Format(name="Vinyl", descriptions=["LP", "Album"])],
        styles=["Jazz"], genres=["Jazz"],
        community_have=100, community_want=50,
        community_avg_rating=4.5, community_rating_count=30,
        fetched_at=datetime.now(UTC),
    ))
    run_id, _ = store.start_run(args={})
    store.record_recommendation(run_id, release_id=1, score=0.62, subscores=_SUBSCORES)
    store.finish_run(run_id, summary={})
    store.close()

    result = CliRunner().invoke(cli, ["explain", "1"])
    assert result.exit_code == 0, result.output
    assert "Kind of Blue" in result.output
    assert "connection" in result.output
    assert "Score breakdown" in result.output


def test_explain_never_recommended(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    db = _config(tmp_path)
    init_db(db)

    result = CliRunner().invoke(cli, ["explain", "999"])
    assert result.exit_code == 0, result.output
    assert "never been recommended" in result.output
