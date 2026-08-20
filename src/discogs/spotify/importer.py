"""Resolve Spotify artists to Discogs artists and cache the result."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from discogs.api.search import resolve_artist_name

if TYPE_CHECKING:
    from collections.abc import Sequence

    from discogs.api.client import DiscogsClient
    from discogs.cache.store import CacheStore
    from discogs.spotify.interchange import SpotifyArtist

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImportResult:
    """What one import run did."""

    resolved: int
    unresolved: int
    skipped: int  # already resolved by an earlier run
    attempted: int

    @property
    def total(self) -> int:
        return self.resolved + self.unresolved + self.skipped


def import_artists(
    store: CacheStore,
    client: DiscogsClient,
    artists: Sequence[SpotifyArtist],
    *,
    limit: int | None = None,
    refresh: bool = False,
) -> ImportResult:
    """Resolve *artists* to Discogs ids, caching every outcome.

    Artists already carrying a Discogs id are skipped unless *refresh*,
    because resolution costs an API call and does not change. Their
    liked-track count is still refreshed — the library grows, so the seed
    weight has to.

    *limit* caps how many resolutions this run attempts, so a large
    library can be imported across several runs without going near the
    daily call budget. Artists arrive heaviest-first, so a truncated run
    resolves the ones that matter most.
    """
    known = store.spotify_artist_resolutions()
    now = datetime.now(UTC).isoformat()
    resolved = unresolved = skipped = attempted = 0

    for artist in artists:
        already = known.get(artist.spotify_id)
        if already is not None and not refresh:
            # Refresh the weight, keep the resolution, spend nothing.
            store.upsert_spotify_artist(
                spotify_artist_id=artist.spotify_id,
                name=artist.name,
                liked_track_count=artist.liked_track_count,
                discogs_artist_id=None,
                match_method="cached",
                resolved_at=now,
            )
            skipped += 1
            continue

        if limit is not None and attempted >= limit:
            # Out of budget for this run: still record the artist so its
            # weight is current and the next run knows to resolve it.
            store.upsert_spotify_artist(
                spotify_artist_id=artist.spotify_id,
                name=artist.name,
                liked_track_count=artist.liked_track_count,
                discogs_artist_id=None,
                match_method="pending",
                resolved_at=now,
            )
            continue

        attempted += 1
        match = resolve_artist_name(client, artist.name)
        if match is None:
            logger.info("No confident Discogs artist for %r", artist.name)
            unresolved += 1
            store.upsert_spotify_artist(
                spotify_artist_id=artist.spotify_id,
                name=artist.name,
                liked_track_count=artist.liked_track_count,
                discogs_artist_id=None,
                match_method="unresolved",
                resolved_at=now,
            )
            continue

        discogs_id, canonical = match
        logger.debug("Resolved %r to Discogs artist %d (%s)", artist.name, discogs_id, canonical)
        resolved += 1
        store.upsert_spotify_artist(
            spotify_artist_id=artist.spotify_id,
            name=artist.name,
            liked_track_count=artist.liked_track_count,
            discogs_artist_id=discogs_id,
            match_method="search",
            resolved_at=now,
        )

    return ImportResult(
        resolved=resolved, unresolved=unresolved, skipped=skipped, attempted=attempted
    )
