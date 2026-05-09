"""Fetch Artist detail and (later) discography."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore
from discogs.models import Artist

ARTIST_TTL = timedelta(days=30)


def _ref_type(ref: Any) -> str:
    """Return the type string of an artist-discography reference ('release' or 'master').

    python3-discogs-client doesn't expose `type` as a SimpleField descriptor on
    Release/Master objects, so plain attribute access via getattr returns the default
    rather than the real value.  Use fetch("type") which reads from the underlying
    data dict.  The isinstance guard ensures that MagicMock returns (which are not
    plain strings) fall through to the getattr fallback used by test mocks that set
    .type directly.
    """
    if hasattr(ref, "fetch"):
        try:
            t = ref.fetch("type")
            if isinstance(t, str):
                return t
        except (KeyError, AttributeError, TypeError):
            pass
    return str(getattr(ref, "type", "release"))


def fetch_artist(client: DiscogsClient, store: CacheStore, artist_id: int) -> Artist:
    age = store.artist_age(artist_id)
    if age is not None and age < ARTIST_TTL:
        cached = store.get_artist(artist_id)
        if cached is not None:
            return cached

    raw = client.call("artist", artist_id)
    artist = _artist_from_raw(raw)
    store.upsert_artist(artist)
    return artist


def _artist_from_raw(raw: Any) -> Artist:
    return Artist(
        id=int(raw.id),
        name=str(raw.name),
        profile=getattr(raw, "profile", None) or None,
        fetched_at=datetime.now(UTC),
    )


ARTIST_TOP_RELEASES_TTL = timedelta(days=30)


def fetch_artist_releases(
    client: DiscogsClient, store: CacheStore, artist_id: int, *, top_k: int = 25,
    page_size: int = 50,
) -> list[int]:
    """Return up to `top_k` release IDs for `artist_id` from page 1 of their discography.

    Uses the `artist_top_releases` cache (30d TTL). On miss, paginates page 1 only
    (capped at `page_size` items), filters to type='release', takes the first `top_k`,
    persists, returns.
    """
    age = store.artist_top_releases_age(artist_id)
    if age is not None and age < ARTIST_TOP_RELEASES_TTL:
        cached = store.get_artist_top_release_ids(artist_id)
        if cached:
            return cached[:top_k]

    raw = client.call("artist", artist_id)
    rids: list[int] = []
    for i, ref in enumerate(raw.releases):
        if i >= page_size:
            break
        ref_type = _ref_type(ref)
        if ref_type != "release":
            continue
        rids.append(int(ref.id))
        if len(rids) >= top_k:
            break

    store.replace_artist_top_releases(artist_id, rids)
    return rids
