from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from discogs.cli.__main__ import cli
from discogs.config import load_config
from discogs.recommend.graph import GraphPath
from discogs.recommend.pipeline import RunResult
from discogs.recommend.scoring import ScoredCandidate


def _seed_config(home: Path) -> None:
    cfg_dir = home / ".discogs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text('[discogs]\ntoken = "t"\nusername = "lorenzo"')


def test_recommend_writes_digest_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    pick = ScoredCandidate(
        release_id=42, score=0.7,
        subscores={"connection": 1.0, "influence_chain": 0.0},
        paths=(GraphPath(seed_artist_id=1, seed_weight=1.0,
                         edge_chain=((1, 42, "direct"),), edge_weight=1.0),),
    )
    fake_result = RunResult(
        run_id="u", run_display_id="2026-05-08-1830", picks=[pick],
        seed_count=1, candidate_count=10, api_calls_used=5, wall_seconds=1.5,
        args={},
    )

    real_cfg = load_config()  # uses tmp_path/.discogs/config.toml since HOME is patched

    with patch("discogs.cli.commands.recommend._build_pipeline_context") as bp, \
         patch("discogs.cli.commands.recommend.run_recommend", return_value=fake_result), \
         patch("discogs.cli.commands.recommend.render_digest", return_value="DIGEST_BODY"):
        bp.return_value = (MagicMock(), MagicMock(), real_cfg)
        result = CliRunner().invoke(cli, ["recommend"])

    assert result.exit_code == 0, result.output
    digest_path = tmp_path / ".discogs" / "digests" / "2026-05-08-1830-recommendations.md"
    assert digest_path.exists()
    assert digest_path.read_text() == "DIGEST_BODY"
    assert "2026-05-08-1830" in result.output


def test_recommend_max_recs_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_result = RunResult(
        run_id="u", run_display_id="2026-05-08-1830", picks=[],
        seed_count=0, candidate_count=0, api_calls_used=0, wall_seconds=0.1,
        args={},
    )

    real_cfg = load_config()

    with patch("discogs.cli.commands.recommend._build_pipeline_context") as bp, \
         patch("discogs.cli.commands.recommend.run_recommend", return_value=fake_result) as rr, \
         patch("discogs.cli.commands.recommend.render_digest", return_value=""):
        bp.return_value = (MagicMock(), MagicMock(), real_cfg)
        result = CliRunner().invoke(cli, ["recommend", "--max-recs", "5"])

    assert result.exit_code == 0, result.output
    rr.assert_called_once()
    kwargs = rr.call_args.kwargs
    assert kwargs["max_recs"] == 5


def test_recommend_apply_with_yes_succeeds_on_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Phase 2 UsageError is gone — --apply --yes now runs apply_run."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    from discogs.recommend.apply import ApplyReport
    from discogs.recommend.pipeline import RunResult

    fake_store = MagicMock()
    fake_store.has_any_apply.return_value = True

    empty = RunResult(
        run_id="r", run_display_id="2026-05-09-1830", picks=[],
        seed_count=0, candidate_count=0, api_calls_used=0, wall_seconds=0.0, args={},
    )
    with patch("discogs.cli.commands.recommend._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.recommend.run_recommend", return_value=empty), \
         patch("discogs.cli.commands.recommend.render_digest", return_value=""), \
         patch("discogs.cli.commands.recommend.apply_run",
               return_value=ApplyReport(run_id="r", successes=0, failures=0)):
        result = CliRunner().invoke(cli, ["recommend", "--apply", "--yes"])

    assert result.exit_code == 0, result.output
