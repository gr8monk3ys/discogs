"""CLI-level tests for `discogs export`."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from discogs.cli.__main__ import cli
from discogs.config import Config

runner = CliRunner()


def test_export_writes_to_music_dir(tmp_path: Path) -> None:
    cfg = Config(
        discogs_token="t", discogs_username="u", cache_path=tmp_path / "c.db",
        music_dir=tmp_path / "music",
    )
    with patch("discogs.cli.commands.export_cmd.load_config", return_value=cfg):
        result = runner.invoke(cli, ["export"])

    assert result.exit_code == 0, result.output
    out = tmp_path / "music" / "discogs.json"
    assert out.exists()
    assert json.loads(out.read_text())["schema"] == "discogs/1"
    assert str(out) in result.output


def test_export_honours_out(tmp_path: Path) -> None:
    cfg = Config(discogs_token="t", discogs_username="u", cache_path=tmp_path / "c.db")
    with patch("discogs.cli.commands.export_cmd.load_config", return_value=cfg):
        result = runner.invoke(cli, ["export", "--out", str(tmp_path / "x.json")])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "x.json").exists()


def test_music_dir_defaults_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSIC_DIR", str(tmp_path / "m"))
    assert Config(discogs_token="t", discogs_username="u").music_dir == tmp_path / "m"
