from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from discogs.cli.__main__ import cli
from discogs.config import load_config
from discogs.recommend.apply import ApplyReport


def _seed_config(home: Path) -> None:
    cfg_dir = home / ".discogs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text('[discogs]\ntoken = "t"\nusername = "u"')


def test_apply_command_resolves_display_id(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.get_run_by_display_id.return_value = "u-uuid"
    fake_store.has_any_apply.return_value = True

    with patch("discogs.cli.commands.apply_cmd._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.apply_cmd.apply_run") as ar:
        ar.return_value = ApplyReport(run_id="u-uuid", successes=5, failures=0)
        result = CliRunner().invoke(cli, ["apply", "2026-05-09-1830"])

    assert result.exit_code == 0, result.output
    fake_store.get_run_by_display_id.assert_called_once_with("2026-05-09-1830")
    ar.assert_called_once()
    assert ar.call_args.kwargs["run_id"] == "u-uuid"


def test_apply_command_unknown_display_id(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.get_run_by_display_id.return_value = None

    with patch("discogs.cli.commands.apply_cmd._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())):
        result = CliRunner().invoke(cli, ["apply", "nonexistent"])

    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "no run" in result.output.lower()


def test_apply_command_first_time_prompts(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.get_run_by_display_id.return_value = "u-uuid"
    fake_store.has_any_apply.return_value = False
    fake_store.get_recommendations_for_run.return_value = [{"release_id": 1}]

    with patch("discogs.cli.commands.apply_cmd._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.apply_cmd.apply_run") as ar:
        result = CliRunner().invoke(cli, ["apply", "2026-05-09-1830"], input="n\n")

    ar.assert_not_called()
    assert "cancel" in result.output.lower()
