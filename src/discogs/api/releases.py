"""Fetch full Discogs release detail (with credits + labels) and persist."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore
from discogs.models import Credit, Format, Release

RELEASE_TTL = timedelta(days=30)


def fetch_release(
    client: DiscogsClient, store: CacheStore, release_id: int
) -> Release:
    """Return a Release for `release_id`, fetching from API if cache is missing or stale.

    Persists the release row, credits (extra-artists at the release and track levels),
    label associations, styles, and genres into the local cache.
    """
    age = store.release_age(release_id)
    if age is not None and age < RELEASE_TTL:
        cached = store.get_release(release_id)
        if cached is not None:
            return cached

    raw = client.call("release", release_id)
    release = _release_from_raw(raw)
    store.upsert_release(release)
    store.replace_release_credits(release_id, _credits_from_raw(raw, release_id))
    store.replace_release_labels(release_id, _labels_from_raw(raw))
    return release


def _release_from_raw(raw: Any) -> Release:
    community = getattr(raw, "community", None)
    rating = getattr(community, "rating", None) if community is not None else None
    return Release(
        id=int(raw.id),
        master_id=int(raw.master_id) if getattr(raw, "master_id", None) else None,
        title=str(raw.title),
        year=int(getattr(raw, "year", 0) or 0),
        country=getattr(raw, "country", None),
        formats=[
            Format(
                name=str(f.get("name", "")),
                qty=int(f.get("qty", 1) or 1),
                descriptions=list(f.get("descriptions", []) or []),
            )
            for f in (getattr(raw, "formats", None) or [])
        ],
        styles=list(getattr(raw, "styles", None) or []),
        genres=list(getattr(raw, "genres", None) or []),
        community_have=int(getattr(community, "have", None) or 0),
        community_want=int(getattr(community, "want", None) or 0),
        community_avg_rating=float(getattr(rating, "average", None) or 0.0),
        community_rating_count=int(getattr(rating, "count", None) or 0),
        fetched_at=datetime.now(UTC),
    )


def _credits_from_raw(raw: Any, release_id: int) -> list[Credit]:
    seen: set[tuple[int, str]] = set()
    credits: list[Credit] = []

    for ea in getattr(raw, "extraartists", None) or []:
        key = (int(ea.id), str(ea.role))
        if key in seen:
            continue
        seen.add(key)
        credits.append(Credit(release_id=release_id, artist_id=int(ea.id), role=str(ea.role)))

    for track in getattr(raw, "tracklist", None) or []:
        for ea in getattr(track, "extraartists", None) or []:
            key = (int(ea.id), str(ea.role))
            if key in seen:
                continue
            seen.add(key)
            credits.append(Credit(release_id=release_id, artist_id=int(ea.id), role=str(ea.role)))

    return credits


def _labels_from_raw(raw: Any) -> list[tuple[int, str | None]]:
    out: list[tuple[int, str | None]] = []
    for label in getattr(raw, "labels", None) or []:
        catno = getattr(label, "catno", None)
        out.append((int(label.id), catno))
    return out
