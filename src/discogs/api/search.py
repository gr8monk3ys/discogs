"""Discogs database search wrapper."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from discogs_client.exceptions import HTTPError

from discogs.api.client import DiscogsClient
from discogs.spotify.names import key, strip_edition

# Discogs disambiguates artists who share a name with a numeric suffix:
# "Nirvana", "Nirvana (2)", "Nirvana (10)". The suffix is not part of the
# name, so it is stripped before comparing — but its presence still says
# which entry Discogs treats as the primary one.
_DISAMBIGUATOR = re.compile(r"\s*\(\d+\)\s*$")


def _title_of(hit: object) -> str:
    """The artist's name as Discogs spells it.

    Lives in the raw payload, not on the model: `hit.title` is None for
    artist search results, which is why the previous implementation
    compared against nothing and matched nothing.
    """
    data: Any = getattr(hit, "data", None)
    if isinstance(data, dict) and data.get("title"):
        return str(data["title"])
    return str(getattr(hit, "title", "") or "")


def _bare(name: str) -> str:
    return _DISAMBIGUATOR.sub("", name).strip().casefold()


def resolve_artist_name(client: DiscogsClient, name: str) -> tuple[int, str] | None:
    """Search Discogs for an artist by *name*; return (id, canonical_name).

    Returns None unless exactly one candidate is convincing, because an
    unresolved artist is recoverable and a wrong one silently poisons
    every recommendation seeded from it.

    Matching is by name, not by relevance rank, and not by score: the
    Discogs search API returns **no score field at all** for artist
    queries. The previous implementation required `score >= 0.85` from a
    key that is never present and read the name from `hit.title`, which
    is always None here — so it returned None for every artist ever
    looked up, in this importer and in influence expansion alike.

    Among hits whose bare name matches, the unsuffixed entry wins: given
    "Nirvana", "Nirvana (2)" and "Nirvana (10)", Discogs treats the plain
    one as the primary. When every match carries a suffix the name is
    genuinely ambiguous — several distinct acts share it and nothing in
    the query says which — so it stays unresolved rather than guessing.
    """
    wanted = _bare(name)
    if not wanted:
        return None

    matches: list[tuple[int, str]] = []
    for hit in client.call("search", name, type="artist"):
        title = _title_of(hit)
        if _bare(title) == wanted:
            hit_id = getattr(hit, "id", None) or (getattr(hit, "data", {}) or {}).get("id")
            if hit_id is not None:
                matches.append((int(hit_id), title))

    if not matches:
        return None

    unsuffixed = [m for m in matches if not _DISAMBIGUATOR.search(m[1])]
    if unsuffixed:
        return unsuffixed[0]
    if len(matches) == 1:
        return matches[0]
    return None  # several distinct acts share the name; refuse to pick one


@dataclass(frozen=True)
class ResolvedRelease:
    """The one Discogs release an artist/title pair names, via its master."""

    release_id: int
    master_id: int | None
    canonical: str


def _hit_id(hit: object) -> int | None:
    raw = getattr(hit, "id", None) or (getattr(hit, "data", None) or {}).get("id")
    return int(raw) if raw is not None else None


def _main_release_id(client: DiscogsClient, hit: object, master_id: int) -> int | None:
    """From the search payload when present; one extra call otherwise."""
    data: Any = getattr(hit, "data", None)
    if isinstance(data, dict) and data.get("main_release") is not None:
        return int(data["main_release"])
    # The live search payload never carries main_release (verified 2026-08-27),
    # so this fetch is the norm. A master the search still lists can 404 on
    # fetch (deleted or merged); that hit is dropped rather than fatal.
    try:
        master = client.call("master", master_id)
        main = getattr(master, "main_release", None)
    except HTTPError:
        return None
    main_id = getattr(main, "id", main)
    return int(main_id) if main_id is not None else None


def resolve_release(client: DiscogsClient, artist: str, title: str) -> ResolvedRelease | None:
    """Search Discogs masters for *artist* + *title*; return the main release.

    Master search results title themselves "Artist - Title". A hit counts
    when its normalised pair equals ours (edition noise, punctuation, case
    and a leading article ignored). Exactly one distinct answer is
    returned; zero or several is None — a wrong release on the wantlist is
    worse than a missing one.
    """
    wanted = key(artist, title)
    if not wanted[0] or not wanted[1]:
        return None

    matches: dict[int, tuple[int, str]] = {}  # release_id -> (master_id, canonical)
    for hit in client.call("search", f"{artist} {strip_edition(title)}", type="master"):
        canonical = _title_of(hit)
        hit_artist, sep, hit_title = canonical.partition(" - ")
        if not sep:
            continue
        if key(hit_artist, hit_title) != wanted:
            continue
        master_id = _hit_id(hit)
        if master_id is None:
            continue
        release_id = _main_release_id(client, hit, master_id)
        if release_id is None:
            continue
        matches.setdefault(release_id, (master_id, canonical))

    if len(matches) != 1:
        return None
    release_id, (master_id, canonical) = next(iter(matches.items()))
    return ResolvedRelease(release_id=release_id, master_id=master_id, canonical=canonical)
