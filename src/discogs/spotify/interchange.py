"""Read the `music-library.json` file the Spotify repo exports.

This is the entire contract between the two repos: one versioned JSON
document, read here and never written. Nothing in this module talks to
Discogs — it turns a file into plain records, so the import can be tested
without an API at all.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "music-library/1"
DEFAULT_PATH = Path.home() / ".spotifyforge" / "music-library.json"


class InterchangeError(Exception):
    """The interchange file is missing, malformed, or a version we cannot read."""


@dataclass(frozen=True)
class SpotifyArtist:
    """A distinct artist in the Spotify library, with its listening weight."""

    spotify_id: str
    name: str
    liked_track_count: int


@dataclass(frozen=True)
class SpotifyAlbum:
    """An album rolled up from liked tracks."""

    spotify_album_id: str
    title: str
    artists: tuple[SpotifyArtist, ...]
    year: int | None
    liked_track_count: int
    total_tracks: int | None
    affinity: float
    isrcs: tuple[str, ...]


def load(path: Path | None = None) -> dict[str, Any]:
    """Parse the interchange file, refusing anything we cannot read.

    The schema string is asserted rather than trusted: two repos consume
    this file, and a silently-changed shape would show up as wrong
    recommendations rather than as an error.
    """
    target = path or DEFAULT_PATH
    if not target.exists():
        raise InterchangeError(
            f"No interchange file at {target}. "
            "Run `spotifyforge export library` in the spotify repo first."
        )
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InterchangeError(f"{target} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InterchangeError(f"{target} is not an object")

    schema = data.get("schema")
    if schema != SCHEMA:
        raise InterchangeError(
            f"{target} declares schema {schema!r}; this build reads {SCHEMA!r}."
        )
    return data


def albums(data: dict[str, Any]) -> list[SpotifyAlbum]:
    """The albums in the document, most-liked first."""
    out: list[SpotifyAlbum] = []
    for raw in data.get("albums") or []:
        credited = tuple(
            SpotifyArtist(
                spotify_id=str(a.get("spotify_id") or ""),
                name=str(a.get("name") or ""),
                liked_track_count=int(raw.get("liked_track_count") or 0),
            )
            for a in raw.get("artists") or []
            if a.get("spotify_id")
        )
        out.append(
            SpotifyAlbum(
                spotify_album_id=str(raw.get("spotify_album_id") or ""),
                title=str(raw.get("title") or ""),
                artists=credited,
                year=int(raw["year"]) if raw.get("year") else None,
                liked_track_count=int(raw.get("liked_track_count") or 0),
                total_tracks=int(raw["total_tracks"]) if raw.get("total_tracks") else None,
                affinity=float(raw.get("affinity") or 0.0),
                isrcs=tuple(str(i) for i in raw.get("isrcs") or []),
            )
        )
    out.sort(key=lambda a: (-a.liked_track_count, a.title))
    return out


def distinct_artists(data: dict[str, Any]) -> list[SpotifyArtist]:
    """Every credited artist, weighted by the liked tracks behind them.

    An artist credited on several albums is one row, and their counts are
    summed — the weight is "how much of this library is theirs", which is
    exactly what a seed weight should mean. Sorted heaviest first so a
    truncated run resolves the artists that matter most.
    """
    totals: dict[str, int] = {}
    names: dict[str, str] = {}
    for album in albums(data):
        for artist in album.artists:
            totals[artist.spotify_id] = totals.get(artist.spotify_id, 0) + album.liked_track_count
            names.setdefault(artist.spotify_id, artist.name)

    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], names.get(kv[0], "")))
    return [
        SpotifyArtist(spotify_id=sid, name=names.get(sid, sid), liked_track_count=n)
        for sid, n in ranked
    ]
