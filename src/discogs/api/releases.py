"""Fetch full Discogs release detail (with credits + labels) and persist."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore
from discogs.models import Credit, Format, Release

RELEASE_TTL = timedelta(days=30)


def _safe_fetch(obj: Any, field: str) -> Any:
    """Try obj.fetch(field) first (real Discogs object), fall back to getattr.

    python3-discogs-client exposes data fields via fetch(field) rather than as
    Python attribute descriptors.  getattr therefore returns None for fields that
    aren't declared as SimpleField descriptors.  We call fetch() when available
    and trust the result only when it is a plain scalar (str, int, float, or None);
    a MagicMock return (from test mocks) is not a scalar, so we fall through to
    getattr which *does* find the attribute the test fixture set directly.
    """
    if hasattr(obj, "fetch"):
        try:
            v = obj.fetch(field)
            if isinstance(v, (str, int, float, type(None))):
                return v
        except (KeyError, AttributeError, TypeError):
            pass
    return getattr(obj, field, None)


def _int_or_none(v: Any) -> int | None:
    """Convert v to int, returning None for None or un-convertible values."""
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def fetch_release(
    client: DiscogsClient, store: CacheStore, release_id: int,
    *, force_credits: bool = False,
) -> Release:
    """Return a Release for `release_id`, fetching from API if cache is missing or stale.

    Persists the release row, credits (extra-artists at the release and track levels),
    label associations, styles, and genres into the local cache.

    If `force_credits` is True and the release has no cached credits (e.g. because
    it was stored before credit-fetching was implemented), a fresh API call is made
    regardless of the release TTL.
    """
    age = store.release_age(release_id)
    credits_missing = force_credits and not store.get_release_credits(release_id)
    if age is not None and age < RELEASE_TTL and not credits_missing:
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
        master_id=_int_or_none(_safe_fetch(raw, "master_id")),
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

    # The python3-discogs-client library exposes extraartists only via fetch(),
    # not as a plain attribute descriptor.  getattr returns None for it.
    # Items in the list are plain dicts with 'id' and 'role' keys.
    def _ea_list(obj: Any, field: str) -> list[dict[str, Any]]:
        """Return the extraartists list from obj, trying fetch() first."""
        if hasattr(obj, "fetch"):
            try:
                v = obj.fetch(field)
                if isinstance(v, list):
                    return v
            except (KeyError, AttributeError, TypeError):
                pass
        v = getattr(obj, field, None)
        return v if isinstance(v, list) else []

    def _ea_id_role(ea: Any) -> tuple[int, str] | None:
        """Extract (artist_id, role) from an extraartist entry (dict or object)."""
        if isinstance(ea, dict):
            try:
                return int(ea["id"]), str(ea.get("role", ""))
            except (KeyError, TypeError, ValueError):
                return None
        try:
            return int(ea.id), str(ea.role)
        except (AttributeError, TypeError, ValueError):
            return None

    for ea in _ea_list(raw, "extraartists"):
        pair = _ea_id_role(ea)
        if pair is None or pair in seen:
            continue
        seen.add(pair)
        credits.append(Credit(release_id=release_id, artist_id=pair[0], role=pair[1]))

    for track in getattr(raw, "tracklist", None) or []:
        for ea in _ea_list(track, "extraartists"):
            pair = _ea_id_role(ea)
            if pair is None or pair in seen:
                continue
            seen.add(pair)
            credits.append(Credit(release_id=release_id, artist_id=pair[0], role=pair[1]))

    return credits


def _labels_from_raw(raw: Any) -> list[tuple[int, str | None]]:
    out: list[tuple[int, str | None]] = []
    for label in getattr(raw, "labels", None) or []:
        label_id = getattr(label, "id", None)
        if label_id is None:
            continue
        catno = getattr(label, "catno", None)
        out.append((int(label_id), catno))
    return out
