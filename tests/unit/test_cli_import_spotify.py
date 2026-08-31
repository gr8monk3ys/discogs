"""CLI-level tests for `discogs import-spotify`."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from discogs.cli.__main__ import cli

runner = CliRunner()

_DOC = {
    "schema": "music-library/1",
    "generated_at": "2026-08-19T00:00:00Z",
    "source": {"platform": "spotify", "user": "gr8monk3ys"},
    "albums": [
        {
            "spotify_album_id": "alb1",
            "title": "In Utero",
            "artists": [{"spotify_id": "art1", "name": "Nirvana"}],
            "year": 1993,
            "liked_track_count": 9,
            "total_tracks": 12,
            "affinity": 0.75,
            "isrcs": [],
        }
    ],
    "discoveries": [],
}


class _Hit:
    def __init__(self, hit_id: int, title: str) -> None:
        self.id = hit_id
        self.title = None
        self.data = {"id": hit_id, "title": title, "type": "artist"}


def _write(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "music-library.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_import_reports_what_it_resolved(tmp_path: Path) -> None:
    from discogs.config import Config

    cfg = Config(
        discogs_token="t", discogs_username="u", cache_path=tmp_path / "cache.db",
    )
    client = MagicMock()
    client.call.return_value = [_Hit(125246, "Nirvana")]

    with (
        patch("discogs.cli.commands.import_spotify_cmd.load_config", return_value=cfg),
        patch("discogs.cli.commands.import_spotify_cmd.DiscogsClient", return_value=client),
    ):
        result = runner.invoke(
            cli, ["import-spotify", "--file", str(_write(tmp_path, _DOC))]
        )

    assert result.exit_code == 0, result.output
    assert "1 newly resolved" in result.output
    assert "1 of 1 imported artists" in result.output


def test_a_missing_file_is_a_clean_error_not_a_traceback(tmp_path: Path) -> None:
    result = runner.invoke(cli, ["import-spotify", "--file", str(tmp_path / "nope.json")])

    assert result.exit_code != 0
    assert "spotifyforge export library" in result.output
