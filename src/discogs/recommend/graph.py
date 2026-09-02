"""Stage 2: bounded BFS through the credit graph."""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from discogs_client.exceptions import HTTPError

from discogs.api.artists import fetch_artist_releases
from discogs.api.client import DiscogsClient
from discogs.api.releases import fetch_release
from discogs.cache.store import CacheStore
from discogs.recommend.seeds import SeedArtist

if TYPE_CHECKING:
    from discogs.models import Credit


@dataclass(frozen=True)
class GraphPath:
    """One trace of how a candidate was reached.

    `edge_chain` is a tuple of (artist_id, release_id, role) tuples. For a direct
    seed release the chain has length 1 (role="direct"); for a one-hop neighbor
    it has length 2.
    """
    seed_artist_id: int
    seed_weight: float
    edge_chain: tuple[tuple[int, int, str], ...]
    edge_weight: float  # product of role weights along the chain
    seed_kind: str = "direct"  # "direct" | "influence"


_PRIMARY_ROLES = {
    "producer", "co-producer", "executive producer",
    "vocals", "guitar", "bass", "drums", "piano", "synthesizer",
    "saxophone", "trumpet", "trombone", "violin", "performer",
}
_INSTRUMENT_HINTS = (
    "saxophone", "guitar", "bass", "vocals", "drums", "piano", "synth",
    "keys", "horn", "trumpet", "trombone", "percussion", "violin", "cello",
)


def role_weight(role: str) -> float:
    """Map a Discogs role string to a graph-edge weight in [0.2, 1.0]."""
    base = role.split("[", 1)[0].strip().lower()

    if any(hint in base for hint in _INSTRUMENT_HINTS):
        return 1.0
    if base in _PRIMARY_ROLES:
        return 1.0
    if base in {"engineer", "recording engineer", "mixed by"}:
        return 0.5
    if base in {"mastered by", "remastered by"}:
        return 0.3
    if base in {"liner notes", "design", "photography", "artwork", "illustration"}:
        return 0.2
    return 0.5


def walk_credit_graph(
    client: DiscogsClient,
    store: CacheStore,
    seeds: Sequence[SeedArtist],
    *,
    max_neighbors_per_seed: int = 5,
    max_releases_per_neighbor: int = 25,
    budget: int = 800,
    allow_rerecommend: bool = False,
) -> dict[int, list[GraphPath]]:
    """Walk the credit graph from `seeds`, returning candidate releases with their paths.

    Releases already in the user's collection or wantlist are always excluded.
    Previously-recommended releases are also excluded unless `allow_rerecommend=True`.
    """
    excluded = store.collection_release_ids() | store.wantlist_release_ids()
    if not allow_rerecommend:
        excluded |= store.previously_recommended_release_ids()

    api_calls_at_start = store.api_calls_today()

    def remaining() -> int:
        spent = store.api_calls_today() - api_calls_at_start
        return budget - spent

    paths: dict[int, list[GraphPath]] = defaultdict(list)

    for seed in seeds:
        if remaining() <= 0:
            break

        try:
            seed_release_ids = fetch_artist_releases(
                client, store, seed.artist_id, top_k=max_releases_per_neighbor,
            )
        except (HTTPError, json.JSONDecodeError, ValueError, OSError):
            continue

        for release_id in seed_release_ids:
            if remaining() <= 0:
                break

            if release_id not in excluded:
                paths[release_id].append(GraphPath(
                    seed_artist_id=seed.artist_id,
                    seed_weight=seed.weight,
                    edge_chain=((seed.artist_id, release_id, "direct"),),
                    edge_weight=1.0,
                    seed_kind=seed.seed_kind,
                ))

            try:
                fetch_release(client, store, release_id)
            except (HTTPError, json.JSONDecodeError, ValueError, OSError):
                continue
            credits = store.get_release_credits(release_id)

            ranked_neighbors = _rank_neighbors(
                credits, exclude_artist_id=seed.artist_id, top=max_neighbors_per_seed,
            )

            for neighbor_id, neighbor_role in ranked_neighbors:
                if remaining() <= 0:
                    break

                try:
                    neighbor_release_ids = fetch_artist_releases(
                        client, store, neighbor_id, top_k=max_releases_per_neighbor,
                    )
                except (HTTPError, json.JSONDecodeError, ValueError, OSError):
                    continue
                for nr_id in neighbor_release_ids:
                    if nr_id in excluded:
                        continue
                    paths[nr_id].append(GraphPath(
                        seed_artist_id=seed.artist_id,
                        seed_weight=seed.weight,
                        edge_chain=(
                            (seed.artist_id, release_id, "direct"),
                            (neighbor_id, nr_id, neighbor_role),
                        ),
                        edge_weight=role_weight(neighbor_role),
                        seed_kind=seed.seed_kind,
                    ))

    return dict(paths)


def _rank_neighbors(
    credits: Sequence[Credit], *, exclude_artist_id: int, top: int,
) -> list[tuple[int, str]]:
    """Return up to `top` (artist_id, role) pairs ranked by role weight."""
    seen: dict[int, tuple[float, str]] = {}
    for c in credits:
        if c.artist_id == exclude_artist_id:
            continue
        w = role_weight(c.role)
        if c.artist_id not in seen or w > seen[c.artist_id][0]:
            seen[c.artist_id] = (w, c.role)
    ranked = sorted(seen.items(), key=lambda item: -item[1][0])
    return [(aid, role) for aid, (_w, role) in ranked[:top]]
