from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from discogs.cli.__main__ import cli
from discogs.sync.syncer import SyncResult


def _seed_config(home: Path) -> None:
    cfg_dir = home / ".discogs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text('[discogs]\ntoken = "t"\nusername = "lorenzo"')


def test_sync_default_scope_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_syncer = MagicMock()
    fake_syncer.sync.return_value = SyncResult(collection_synced=42, wantlist_synced=7)

    with patch("discogs.cli.commands.sync_cmd._build_syncer", return_value=fake_syncer):
        result = CliRunner().invoke(cli, ["sync"])

    assert result.exit_code == 0, result.output
    fake_syncer.sync.assert_called_once_with(scope="both", force=False)
    assert "42" in result.output
    assert "7" in result.output


def test_sync_force_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake = MagicMock()
    fake.sync.return_value = SyncResult(collection_synced=0, wantlist_synced=0)

    with patch("discogs.cli.commands.sync_cmd._build_syncer", return_value=fake):
        result = CliRunner().invoke(cli, ["sync", "--force", "--scope", "collection"])

    assert result.exit_code == 0
    fake.sync.assert_called_once_with(scope="collection", force=True)


def test_sync_reports_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake = MagicMock()
    fake.sync.return_value = SyncResult(collection_synced=None, wantlist_synced=None)

    with patch("discogs.cli.commands.sync_cmd._build_syncer", return_value=fake):
        result = CliRunner().invoke(cli, ["sync"])

    assert "skipped" in result.output.lower()
