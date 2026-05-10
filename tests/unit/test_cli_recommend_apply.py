from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from discogs.cli.__main__ import cli
from discogs.config import load_config
from discogs.recommend.apply import ApplyReport
from discogs.recommend.pipeline import RunResult


def _seed_config(home: Path) -> None:
    cfg_dir = home / ".discogs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text('[discogs]\ntoken = "t"\nusername = "u"')


def _empty_run(display_id: str = "2026-05-09-1830") -> RunResult:
    return RunResult(
        run_id="u-uuid", run_display_id=display_id, picks=[],
        seed_count=1, candidate_count=1, api_calls_used=0, wall_seconds=0.1,
        args={},
    )


def test_recommend_apply_first_time_requires_confirm(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.has_any_apply.return_value = False
    fake_store.last_applied_run_id.return_value = None

    with patch("discogs.cli.commands.recommend._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.recommend.run_recommend",
               return_value=_empty_run()), \
         patch("discogs.cli.commands.recommend.render_digest", return_value=""), \
         patch("discogs.cli.commands.recommend.apply_run") as ar:
        # Decline the prompt
        result = CliRunner().invoke(cli, ["recommend", "--apply"], input="n\n")

    assert not ar.called
    assert "cancel" in result.output.lower() or "skipped" in result.output.lower()


def test_recommend_apply_yes_bypasses_confirm(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.has_any_apply.return_value = False

    with patch("discogs.cli.commands.recommend._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.recommend.run_recommend",
               return_value=_empty_run()), \
         patch("discogs.cli.commands.recommend.render_digest", return_value=""), \
         patch("discogs.cli.commands.recommend.apply_run") as ar:
        ar.return_value = ApplyReport(run_id="u-uuid", successes=0, failures=0)
        result = CliRunner().invoke(cli, ["recommend", "--apply", "--yes"])

    assert result.exit_code == 0, result.output
    ar.assert_called_once()


def test_recommend_apply_subsequent_no_confirm(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.has_any_apply.return_value = True

    with patch("discogs.cli.commands.recommend._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.recommend.run_recommend",
               return_value=_empty_run()), \
         patch("discogs.cli.commands.recommend.render_digest", return_value=""), \
         patch("discogs.cli.commands.recommend.apply_run") as ar:
        ar.return_value = ApplyReport(run_id="u-uuid", successes=3, failures=0)
        result = CliRunner().invoke(cli, ["recommend", "--apply"])

    assert result.exit_code == 0, result.output
    ar.assert_called_once()
    assert "applied 3" in result.output.lower() or "3 successes" in result.output.lower()


def test_recommend_apply_reports_failures(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.has_any_apply.return_value = True

    with patch("discogs.cli.commands.recommend._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.recommend.run_recommend",
               return_value=_empty_run()), \
         patch("discogs.cli.commands.recommend.render_digest", return_value=""), \
         patch("discogs.cli.commands.recommend.apply_run") as ar:
        ar.return_value = ApplyReport(
            run_id="u-uuid", successes=2, failures=1,
            failed_picks=[(42, "HTTP 500")],
        )
        result = CliRunner().invoke(cli, ["recommend", "--apply", "--yes"])

    assert result.exit_code == 0  # partial success is still success
    assert "42" in result.output
    assert "HTTP 500" in result.output
