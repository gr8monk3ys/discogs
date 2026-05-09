from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from discogs.cache.store import CacheStore, init_db
from discogs.cli.__main__ import cli


def _seed(home: Path) -> None:
    cfg_dir = home / ".discogs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text('[discogs]\ntoken = "t"\nusername = "lorenzo"')
    init_db(cfg_dir / "cache.db")
    store = CacheStore(cfg_dir / "cache.db")
    store.record_sync("collection", datetime.now(UTC) - timedelta(hours=2))
    store.increment_api_calls(17)
    store.close()


def test_status_reports_key_facts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed(tmp_path)

    result = CliRunner().invoke(cli, ["status"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "lorenzo" in out
    assert "17" in out
    assert "1500" in out
    assert "collection" in out.lower()
    assert "never" in out.lower() or "wantlist" in out.lower()
