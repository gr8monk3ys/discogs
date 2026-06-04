from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from discogs.cli.__main__ import cli
from discogs.config import load_config
from discogs.recommend.pipeline import RunResult


def _seed_config(home: Path, *, with_anthropic: bool = True) -> None:
    cfg_dir = home / ".discogs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    body = '[discogs]\ntoken = "t"\nusername = "u"\n'
    if with_anthropic:
        body += '[anthropic]\napi_key = "sk-test"\n'
    (cfg_dir / "config.toml").write_text(body)


def _empty_run(display_id: str = "2026-05-09-1830") -> RunResult:
    return RunResult(
        run_id="u", run_display_id=display_id, picks=[],
        seed_count=0, candidate_count=0, api_calls_used=0, wall_seconds=0.1,
        args={},
    )


def test_recommend_passes_llm_when_configured(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path, with_anthropic=True)

    with patch("discogs.cli.commands.recommend._build_pipeline_context",
               return_value=(MagicMock(), MagicMock(), load_config())), \
         patch("discogs.cli.commands.recommend._build_llm_client") as build_llm, \
         patch("discogs.cli.commands.recommend.run_recommend",
               return_value=_empty_run()) as rr, \
         patch("discogs.cli.commands.recommend.render_digest", return_value="DIGEST"):
        build_llm.return_value = MagicMock()
        result = CliRunner().invoke(cli, ["recommend"])

    assert result.exit_code == 0, result.output
    params = rr.call_args.args[3]
    assert rr.call_args.kwargs.get("llm") is build_llm.return_value
    assert params.with_influences is True
    assert params.with_enrichment is True


def test_recommend_no_influences_flag(tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path, with_anthropic=True)

    with patch("discogs.cli.commands.recommend._build_pipeline_context",
               return_value=(MagicMock(), MagicMock(), load_config())), \
         patch("discogs.cli.commands.recommend._build_llm_client",
               return_value=MagicMock()), \
         patch("discogs.cli.commands.recommend.run_recommend",
               return_value=_empty_run()) as rr, \
         patch("discogs.cli.commands.recommend.render_digest", return_value=""):
        CliRunner().invoke(cli, ["recommend", "--no-influences"])

    assert rr.call_args.args[3].with_influences is False


def test_recommend_no_enrich_flag(tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path, with_anthropic=True)

    with patch("discogs.cli.commands.recommend._build_pipeline_context",
               return_value=(MagicMock(), MagicMock(), load_config())), \
         patch("discogs.cli.commands.recommend._build_llm_client",
               return_value=MagicMock()), \
         patch("discogs.cli.commands.recommend.run_recommend",
               return_value=_empty_run()) as rr, \
         patch("discogs.cli.commands.recommend.render_digest", return_value=""):
        CliRunner().invoke(cli, ["recommend", "--no-enrich"])

    assert rr.call_args.args[3].with_enrichment is False


def test_recommend_warns_and_disables_llm_when_no_api_key(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path, with_anthropic=False)

    with patch("discogs.cli.commands.recommend._build_pipeline_context",
               return_value=(MagicMock(), MagicMock(), load_config())), \
         patch("discogs.cli.commands.recommend.run_recommend",
               return_value=_empty_run()) as rr, \
         patch("discogs.cli.commands.recommend.render_digest", return_value=""):
        result = CliRunner().invoke(cli, ["recommend"])

    assert "anthropic" in result.output.lower() or "llm disabled" in result.output.lower()
    params = rr.call_args.args[3]
    assert rr.call_args.kwargs.get("llm") is None
    assert params.with_influences is False
    assert params.with_enrichment is False
