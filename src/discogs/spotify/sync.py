"""Decide what `discogs sync-spotify` would change — without touching anything.

Pure: takes the Spotify albums and the cached collection/wantlist releases,
returns a plan. The CLI resolves candidates against Discogs and performs the
writes; this module never sees a client.
"""
from __future__ import annotations

from dataclasses import dataclass

from discogs.models import Release
from discogs.spotify.interchange import SpotifyAlbum
from discogs.spotify.names import key


@dataclass(frozen=True)
class Candidate:
    """A Spotify favourite that is neither owned nor already wanted."""

    spotify_album_id: str
    artist: str
    title: str
    year: int | None
    affinity: float
    liked: int


@dataclass(frozen=True)
class Prune:
    """A wantlist entry whose master is now in the collection."""

    release_id: int
    title: str
    artist: str


@dataclass(frozen=True)
class SyncPlan:
    candidates: list[Candidate]
    prunes: list[Prune]
    already_owned: int
    already_wanted: int


def plan_sync(
    albums: list[SpotifyAlbum],
    collection: list[Release],
    wantlist: list[Release],
    *,
    min_affinity: float,
    min_liked: int,
) -> SyncPlan:
    """Candidates are favourites above both floors, matched by normalised
    artist + title against nothing owned or wanted; one record under several
    Spotify ids (editions) is one candidate. Prunes are matched by master id
    only — a name match is not proof the same record is on the shelf."""
    owned_keys = {key(r.artists[0], r.title) for r in collection if r.artists}
    wanted_keys = {key(r.artists[0], r.title) for r in wantlist if r.artists}
    owned_masters = {r.master_id for r in collection if r.master_id}

    candidates: list[Candidate] = []
    seen: set[tuple[str, str]] = set()
    already_owned = already_wanted = 0
    for album in albums:  # interchange.albums() sorts most-liked first
        if (
            not album.artists
            or album.affinity < min_affinity
            or album.liked_track_count < min_liked
        ):
            continue
        k = key(album.artists[0].name, album.title)
        if k in owned_keys:
            already_owned += 1
            continue
        if k in wanted_keys:
            already_wanted += 1
            continue
        if k in seen:
            continue
        seen.add(k)
        candidates.append(
            Candidate(
                spotify_album_id=album.spotify_album_id,
                artist=album.artists[0].name,
                title=album.title,
                year=album.year,
                affinity=album.affinity,
                liked=album.liked_track_count,
            )
        )

    prunes = [
        Prune(release_id=r.id, title=r.title, artist=r.artists[0] if r.artists else "")
        for r in wantlist
        if r.master_id and r.master_id in owned_masters
    ]
    return SyncPlan(candidates, prunes, already_owned, already_wanted)
