from pathlib import Path

import pytest
from click.testing import CliRunner

from discogs.cache.store import CacheStore, init_db
from discogs.cli.__main__ import cli


def _config(home: Path) -> Path:
    cfg_dir = home / ".discogs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text('[discogs]\ntoken = "t"\nusername = "lorenzo"')
    return cfg_dir / "cache.db"


def _make_run(store: CacheStore, run_id: str, display_id: str, picks: dict[int, float]) -> None:
    """Insert a run with explicit display_id (avoids second-precision collisions)."""
    store.conn.execute(
        "INSERT INTO runs (id, display_id, started_at) VALUES (?, ?, ?)",
        (run_id, display_id, "2026-05-01T00:00:00+00:00"),
    )
    for rid, score in picks.items():
        store.record_recommendation(run_id, release_id=rid, score=score)
    store.conn.commit()


def test_diff_added_dropped_rescored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    db = _config(tmp_path)
    init_db(db)
    store = CacheStore(db)
    _make_run(store, "a", "2026-05-01-100000", {1: 0.8, 2: 0.6})
    _make_run(store, "b", "2026-05-02-100000", {2: 0.7, 3: 0.5})
    store.close()

    result = CliRunner().invoke(cli, ["diff", "2026-05-01-100000", "2026-05-02-100000"])
    assert result.exit_code == 0, result.output
    assert "1 added, 1 dropped, 1 rescored" in result.output


def test_diff_unknown_run_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    db = _config(tmp_path)
    init_db(db)

    result = CliRunner().invoke(cli, ["diff", "nope-a", "nope-b"])
    assert result.exit_code != 0
    assert "No run" in result.output
