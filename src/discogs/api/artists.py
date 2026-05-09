"""Fetch Artist detail and (later) discography."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore
from discogs.models import Artist

ARTIST_TTL = timedelta(days=30)


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
