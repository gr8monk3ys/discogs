"""Stage 1: pick seed artists from the user's library."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from discogs.cache.store import CacheStore

Mode = Literal["collection", "wantlist", "both", "spotify", "all"]
SeedKind = Literal["direct", "influence"]


@dataclass(frozen=True)
class SeedArtist:
    artist_id: int
    weight: float            # in [0.1, 1.0]
    sources: tuple[str, ...] # subset of ("collection", "wantlist", "spotify")
    seed_kind: SeedKind = "direct"


def select_seeds(
    store: CacheStore, *, mode: Mode = "both", min_occurrences: int = 2,
) -> list[SeedArtist]:
    """Return the seed-artist set from cached library data.

    An artist becomes a seed when their `artist_id` appears in the credits of
    at least `min_occurrences` releases the user owns or wants (per `mode`).

    Modes "spotify" and "all" additionally seed from the imported Spotify
    library, where an artist's weight is how many liked tracks are theirs
    rather than how many owned records they are credited on. That matters
    because the physical collection is 101 records and the listening
    history is thousands: seeding from credits alone recommends to a
    listener who barely exists.
    """
    coll_ids = store.collection_release_ids() if mode in ("collection", "both", "all") else set()
    want_ids = store.wantlist_release_ids() if mode in ("wantlist", "both", "all") else set()
    spotify_weights = (
        store.spotify_seed_weights() if mode in ("spotify", "all") else {}
    )
    library_ids = coll_ids | want_ids
    if not library_ids and not spotify_weights:
        return []

    rows = []
    if library_ids:
        placeholders = ",".join("?" for _ in library_ids)
        rows = store.conn.execute(
            f"SELECT release_id, artist_id FROM release_credits "
            f"WHERE release_id IN ({placeholders})",
            tuple(library_ids),
        ).fetchall()

    occurrences: Counter[int] = Counter()
    sources: dict[int, set[str]] = {}
    for r in rows:
        rid = int(r["release_id"])
        aid = int(r["artist_id"])
        occurrences[aid] += 1
        bucket = sources.setdefault(aid, set())
        if rid in coll_ids:
            bucket.add("collection")
        if rid in want_ids:
            bucket.add("wantlist")

    eligible = [(aid, n) for aid, n in occurrences.items() if n >= min_occurrences]

    # Credit counts and liked-track counts mean opposite things, so they
    # cannot share a formula. Being credited on many owned records makes
    # an artist *less* distinctive — a session player on everything — and
    # the inverse-log weighting below says so. Having many liked tracks
    # makes an artist *more* central to the taste. Folding the two into
    # one counter gave The Beatles, at 135 liked tracks the heaviest
    # artist in the library, the lowest weight of any seed.
    weights: dict[int, float] = {}
    if eligible:
        credit_raw = {aid: 1.0 / math.log(n + 10) for aid, n in eligible}
        weights.update(_normalized(credit_raw))

    if spotify_weights:
        # log so that 135 liked tracks outranks 40 without swamping it.
        listen_raw = {aid: math.log(n + 1) for aid, n in spotify_weights.items()}
        for aid, weight in _normalized(listen_raw).items():
            sources.setdefault(aid, set()).add("spotify")
            # An artist evidenced both ways keeps the stronger claim.
            weights[aid] = max(weights.get(aid, 0.0), weight)

    if not weights:
        return []

    return [
        SeedArtist(
            artist_id=aid,
            weight=weight,
            sources=tuple(sorted(sources.get(aid, set()))),
        )
        for aid, weight in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _normalized(raw: dict[int, float]) -> dict[int, float]:
    """Scale raw scores onto the [0.1, 1.0] range seeds are weighted in."""
    lo, hi = min(raw.values()), max(raw.values())
    span = hi - lo
    if span == 0:
        return dict.fromkeys(raw, 1.0)
    return {aid: 0.1 + 0.9 * (value - lo) / span for aid, value in raw.items()}
