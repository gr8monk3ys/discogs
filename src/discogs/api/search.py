"""Discogs database search wrapper."""
from __future__ import annotations

import re
from typing import Any

from discogs.api.client import DiscogsClient

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
