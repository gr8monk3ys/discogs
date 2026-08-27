"""CLI-level tests for `discogs sync-spotify`.

The cache is real (a temp SQLite file); only the network edges are patched:
config, the Discogs client, release resolution, and the two wantlist writers.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from discogs.api.search import ResolvedRelease
from discogs.cache.store import CacheStore, init_db
from discogs.cli.__main__ import cli
from discogs.config import Config
from discogs.models import CollectionItem, Release, WantlistItem
from discogs.wantlist_writer import PushResult, RemoveResult

runner = CliRunner()
CMD = "discogs.cli.commands.sync_spotify_cmd"


def _rel(i: int, artist: str, title: str, master: int | None) -> Release:
    return Release(
        id=i, master_id=master, title=title, year=2000, artists=[artist],
        community_have=0, community_want=0, community_avg_rating=0.0,
        community_rating_count=0, fetched_at=datetime.now(UTC),
    )


def _album(i: str, artist: str, title: str) -> dict:
    return {
        "spotify_album_id": i, "title": title,
        "artists": [{"spotify_id": f"art-{i}", "name": artist}],
        "year": 1993, "liked_track_count": 9, "total_tracks": 12, "affinity": 0.75, "isrcs": [],
    }


def _seed(tmp_path: Path) -> tuple[Config, Path]:
    cfg = Config(
        discogs_token="t", discogs_username="u", cache_path=tmp_path / "cache.db",
        digests_dir=tmp_path / "digests", music_dir=tmp_path / "music",
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    now = datetime.now(UTC)
    store.upsert_release(_rel(1, "Björk", "Homogenic", master=7))       # owned
    store.upsert_release(_rel(2, "Björk", "Homogenic", master=7))       # wanted, same master
    store.replace_collection([CollectionItem(release_id=1, folder_id=1, instance_id=1, date_added=now)])
    store.replace_wantlist([WantlistItem(release_id=2, date_added=now, notes=None)])
    store.close()

    library = tmp_path / "music-library.json"
    library.write_text(json.dumps({
        "schema": "music-library/1",
        "albums": [_album("a", "Nirvana", "In Utero"), _album("b", "Björk", "Homogenic")],
    }))
    return cfg, library


def _resolve(client: object, artist: str, title: str) -> ResolvedRelease | None:
    return ResolvedRelease(release_id=100, master_id=50, canonical=f"{artist} - {title}")


def test_dry_run_plans_and_records_but_writes_nothing(tmp_path: Path) -> None:
    cfg, library = _seed(tmp_path)
    client = MagicMock()

    with (
        patch(f"{CMD}.load_config", return_value=cfg),
        patch(f"{CMD}.DiscogsClient", return_value=client),
        patch(f"{CMD}.Syncer"),
        patch(f"{CMD}.resolve_release", side_effect=_resolve),
    ):
        result = runner.invoke(cli, ["sync-spotify", "--file", str(library)])

    assert result.exit_code == 0, result.output
    assert "1 to add" in result.output
    assert "1 to prune" in result.output
    assert "discogs apply" in result.output

    digests = list((tmp_path / "digests").glob("*-spotify-sync.md"))
    assert len(digests) == 1
    text = digests[0].read_text()
    assert "In Utero" in text and "Homogenic" in text

    store = CacheStore(cfg.cache_path)
    try:
        run_id = store.get_run_by_display_id(digests[0].name.removesuffix("-spotify-sync.md"))
        assert run_id is not None
        picks = store.get_recommendations_for_run(run_id)
        assert [(p["release_id"], p["applied_to_wantlist"]) for p in picks] == [(100, 0)]
    finally:
        store.close()

    assert not any(c.args and c.args[0] == "user" for c in client.call.call_args_list)


def test_apply_pushes_additions_and_removes_prunes(tmp_path: Path) -> None:
    cfg, library = _seed(tmp_path)
    client = MagicMock()

    with (
        patch(f"{CMD}.load_config", return_value=cfg),
        patch(f"{CMD}.DiscogsClient", return_value=client),
        patch(f"{CMD}.Syncer"),
        patch(f"{CMD}.resolve_release", side_effect=_resolve),
        patch("discogs.recommend.apply.push_to_wantlist",
              return_value=PushResult(release_id=100, ok=True, error=None)) as push,
        patch(f"{CMD}.remove_from_wantlist",
              return_value=RemoveResult(release_id=2, status="removed", error=None)) as remove,
    ):
        result = runner.invoke(cli, ["sync-spotify", "--file", str(library), "--apply", "--yes"])

    assert result.exit_code == 0, result.output
    push.assert_called_once()
    assert push.call_args.kwargs["release_id"] == 100
    remove.assert_called_once()
    assert remove.call_args.kwargs["release_id"] == 2
    assert "discogs apply" not in result.output


def test_unresolved_candidates_are_listed_never_guessed(tmp_path: Path) -> None:
    cfg, library = _seed(tmp_path)

    with (
        patch(f"{CMD}.load_config", return_value=cfg),
        patch(f"{CMD}.DiscogsClient", return_value=MagicMock()),
        patch(f"{CMD}.Syncer"),
        patch(f"{CMD}.resolve_release", return_value=None),
    ):
        result = runner.invoke(cli, ["sync-spotify", "--file", str(library)])

    assert result.exit_code == 0, result.output
    assert "0 to add" in result.output
    assert "1 unresolved" in result.output


def test_missing_library_is_a_clean_error(tmp_path: Path) -> None:
    result = runner.invoke(cli, ["sync-spotify", "--file", str(tmp_path / "nope.json")])

    assert result.exit_code != 0
    assert "spotifyforge export library" in result.output


def test_a_second_pass_does_not_re_add_what_the_first_applied(tmp_path: Path) -> None:
    """The first live run re-proposed all 34 of its own additions an hour
    later: the wantlist cache was inside its TTL and still empty of them.
    The wantlist is now refreshed before planning, and a resolved id an
    earlier run already pushed is skipped even if the name join missed."""
    cfg, library = _seed(tmp_path)
    client = MagicMock()
    patches = (
        patch(f"{CMD}.load_config", return_value=cfg),
        patch(f"{CMD}.DiscogsClient", return_value=client),
        patch(f"{CMD}.Syncer"),
        patch(f"{CMD}.resolve_release", side_effect=_resolve),
        patch("discogs.recommend.apply.push_to_wantlist",
              side_effect=lambda c, *, username, release_id: PushResult(release_id, True, None)),
        patch(f"{CMD}.remove_from_wantlist",
              side_effect=lambda c, *, username, release_id: RemoveResult(release_id, "removed", None)),
    )
    for p in patches:
        p.start()
    try:
        first = runner.invoke(cli, ["sync-spotify", "--file", str(library), "--apply", "--yes"])
        assert first.exit_code == 0, first.output
        assert "1 to add" in first.output
        second = runner.invoke(cli, ["sync-spotify", "--file", str(library), "--apply", "--yes"])
        assert second.exit_code == 0, second.output
        assert "0 to add" in second.output
    finally:
        for p in patches:
            p.stop()
