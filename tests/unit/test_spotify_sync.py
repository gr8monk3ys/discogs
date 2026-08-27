"""Tests for the pure sync planner: which Spotify favourites go on the
wantlist, and which wantlist items are now owned."""
from __future__ import annotations

from datetime import UTC, datetime

from discogs.models import Release
from discogs.spotify.interchange import SpotifyAlbum, SpotifyArtist
from discogs.spotify.names import key, normalise, strip_article
from discogs.spotify.sync import plan_sync


def _alb(
    i: str, artist: str, title: str, affinity: float = 1.0, liked: int = 10,
) -> SpotifyAlbum:
    return SpotifyAlbum(
        spotify_album_id=i, title=title, artists=(SpotifyArtist(i, artist, liked),),
        year=2000, liked_track_count=liked, total_tracks=10, affinity=affinity,
        isrcs=(), genres=(),
    )


def _rel(i: int, artist: str, title: str, master: int | None = None) -> Release:
    return Release(
        id=i, master_id=master, title=title, year=2000, artists=[artist],
        community_have=0, community_want=0, community_avg_rating=0.0,
        community_rating_count=0, fetched_at=datetime.now(UTC),
    )


def test_normalise_strips_edition_noise() -> None:
    assert normalise("In Utero (Deluxe Edition)") == "in utero"
    assert normalise("OK Computer - Remastered 2011") == "ok computer"
    assert strip_article("The Beatles") == "beatles"
    assert key("The Beatles", "Abbey Road") == ("beatles", "abbey road")


def test_favourites_not_owned_or_wanted_become_candidates() -> None:
    plan = plan_sync(
        [_alb("a", "Nirvana", "In Utero (Deluxe Edition)")], [], [],
        min_affinity=0.6, min_liked=4,
    )
    assert [c.title for c in plan.candidates] == ["In Utero (Deluxe Edition)"]
    assert plan.candidates[0].spotify_album_id == "a"
    assert plan.candidates[0].artist == "Nirvana"


def test_owned_and_wanted_are_excluded_and_counted() -> None:
    plan = plan_sync(
        [_alb("a", "Nirvana", "In Utero"), _alb("b", "Radiohead", "Kid A")],
        [_rel(1, "Nirvana", "In Utero")], [_rel(2, "Radiohead", "Kid A")],
        min_affinity=0.6, min_liked=4,
    )
    assert plan.candidates == []
    assert plan.already_owned == 1
    assert plan.already_wanted == 1


def test_thin_albums_are_not_candidates() -> None:
    plan = plan_sync(
        [_alb("a", "X", "Y", affinity=0.5), _alb("b", "X", "Z", liked=3)], [], [],
        min_affinity=0.6, min_liked=4,
    )
    assert plan.candidates == []


def test_wantlist_items_now_owned_are_pruned_by_master() -> None:
    plan = plan_sync(
        [], [_rel(1, "Björk", "Homogenic", master=7)], [_rel(2, "Björk", "Homogenic", master=7)],
        min_affinity=0.6, min_liked=4,
    )
    assert [p.release_id for p in plan.prunes] == [2]
    assert plan.prunes[0].artist == "Björk"


def test_wantlist_items_without_master_are_never_pruned() -> None:
    plan = plan_sync(
        [], [_rel(1, "Björk", "Homogenic")], [_rel(2, "Björk", "Homogenic")],
        min_affinity=0.6, min_liked=4,
    )
    assert plan.prunes == []


def test_one_record_under_several_spotify_ids_is_one_candidate() -> None:
    plan = plan_sync(
        [_alb("a", "Nirvana", "In Utero"), _alb("b", "Nirvana", "In Utero (Deluxe)")], [], [],
        min_affinity=0.6, min_liked=4,
    )
    assert len(plan.candidates) == 1
