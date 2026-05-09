"""Stage 1: pick seed artists from the user's library."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from discogs.cache.store import CacheStore

Mode = Literal["collection", "wantlist", "both"]
SeedKind = Literal["direct", "influence"]


@dataclass(frozen=True)
class SeedArtist:
    artist_id: int
    weight: float            # in [0.1, 1.0]
    sources: tuple[str, ...] # subset of ("collection", "wantlist")
    seed_kind: SeedKind = "direct"


def select_seeds(
    store: CacheStore, *, mode: Mode = "both", min_occurrences: int = 2,
) -> list[SeedArtist]:
    """Return the seed-artist set from cached library data.

    An artist becomes a seed when their `artist_id` appears in the credits of
    at least `min_occurrences` releases the user owns or wants (per `mode`).
    """
    coll_ids = store.collection_release_ids() if mode in ("collection", "both") else set()
    want_ids = store.wantlist_release_ids() if mode in ("wantlist", "both") else set()
    library_ids = coll_ids | want_ids
    if not library_ids:
        return []

    placeholders = ",".join("?" for _ in library_ids)
    rows = store.conn.execute(
        f"SELECT release_id, artist_id FROM release_credits WHERE release_id IN ({placeholders})",
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
    if not eligible:
        return []

    raw_weights = {aid: 1.0 / math.log(n + 10) for aid, n in eligible}
    lo, hi = min(raw_weights.values()), max(raw_weights.values())
    span = hi - lo

    def normalize(w: float) -> float:
        if span == 0:
            return 1.0
        return 0.1 + 0.9 * (w - lo) / span

    return [
        SeedArtist(
            artist_id=aid,
            weight=normalize(raw_weights[aid]),
            sources=tuple(sorted(sources.get(aid, set()))),
        )
        for aid, _ in sorted(eligible, key=lambda x: -x[1])
    ]
