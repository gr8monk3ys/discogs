"""Discogs database search wrapper."""
from __future__ import annotations

from discogs.api.client import DiscogsClient


def resolve_artist_name(
    client: DiscogsClient, name: str, *, min_score: float = 0.85,
) -> tuple[int, str] | None:
    """Search Discogs for an artist by name; return (id, canonical_name) for the
    top hit if its score >= min_score, else None.

    The caller is responsible for spending the API call budget — `client.call("search", ...)`
    increments the daily counter automatically.
    """
    hits = client.call("search", name, type="artist")
    for hit in hits:
        score = float(hit.data.get("score", 0)) if hasattr(hit, "data") else 0.0
        if score >= min_score:
            return int(hit.id), str(hit.title)
        return None
    return None
