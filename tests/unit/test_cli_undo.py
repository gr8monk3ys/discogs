from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from discogs.cli.__main__ import cli
from discogs.config import load_config
from discogs.recommend.apply import UndoReport


def _seed_config(home: Path) -> None:
    cfg_dir = home / ".discogs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text('[discogs]\ntoken = "t"\nusername = "u"')


def test_undo_last_batch_resolves_via_helper(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.last_applied_run_id.return_value = "u-uuid"

    with patch("discogs.cli.commands.undo_cmd._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.undo_cmd.undo_run") as ur:
        ur.return_value = UndoReport(run_id="u-uuid", removed=3, skipped=0, errors=0)
        result = CliRunner().invoke(cli, ["undo-last-batch", "--yes"])

    assert result.exit_code == 0, result.output
    ur.assert_called_once()
    assert ur.call_args.kwargs["run_id"] == "u-uuid"


def test_undo_last_batch_no_history(tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.last_applied_run_id.return_value = None

    with patch("discogs.cli.commands.undo_cmd._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())):
        result = CliRunner().invoke(cli, ["undo-last-batch"])

    assert result.exit_code != 0
    assert "no" in result.output.lower() and ("apply" in result.output.lower()
                                              or "history" in result.output.lower())


def test_undo_specific_run(tmp_path: Path,
                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.get_run_by_display_id.return_value = "u-uuid"

    with patch("discogs.cli.commands.undo_cmd._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.undo_cmd.undo_run") as ur:
        ur.return_value = UndoReport(run_id="u-uuid", removed=2, skipped=1, errors=0)
        result = CliRunner().invoke(cli, ["undo", "2026-05-09-1830", "--yes"])

    assert result.exit_code == 0, result.output
    fake_store.get_run_by_display_id.assert_called_once_with("2026-05-09-1830")
    assert "removed 2" in result.output.lower()
    assert "skipped 1" in result.output.lower()


def test_undo_specific_run_unknown(tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.get_run_by_display_id.return_value = None

    with patch("discogs.cli.commands.undo_cmd._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())):
        result = CliRunner().invoke(cli, ["undo", "nonexistent", "--yes"])

    assert result.exit_code != 0


def test_undo_prompts_unless_yes(tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.last_applied_run_id.return_value = "u-uuid"
    fake_store.get_recommendations_for_run.return_value = [{"release_id": 1}, {"release_id": 2}]

    with patch("discogs.cli.commands.undo_cmd._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.undo_cmd.undo_run") as ur:
        result = CliRunner().invoke(cli, ["undo-last-batch"], input="n\n")

    ur.assert_not_called()
    assert "cancel" in result.output.lower()
