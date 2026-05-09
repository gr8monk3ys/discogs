"""Stage 3: score the candidate set produced by the graph walk.

9 sub-scores in [0, 1]; `influence_chain` is non-zero when influence-kind paths
are present (Phase 3+). `connection` counts only direct-seed paths.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from discogs.cache.store import CacheStore
from discogs.models import Release
from discogs.recommend.graph import GraphPath

DEFAULT_WEIGHTS: dict[str, float] = {
    "connection": 0.20,
    "influence_chain": 0.15,
    "rarity": 0.20,
    "demand_ratio": 0.05,
    "label_obscurity": 0.05,
    "style_niche": 0.05,
    "rating": 0.15,
    "format": 0.10,
    "recency_match": 0.05,
}

_RATING_COUNT_FLOOR = 5


@dataclass(frozen=True)
class ScoredCandidate:
    release_id: int
    score: float
    subscores: dict[str, float]
    paths: tuple[GraphPath, ...]


def score_candidates(
    *,
    store: CacheStore,
    candidate_paths: dict[int, list[GraphPath]],
    releases: dict[int, Release],
    label_release_counts: dict[int, int],   # release_id -> max(label.releases_count) for its labels
    weights: dict[str, float] = DEFAULT_WEIGHTS,
) -> list[ScoredCandidate]:
    """Score every candidate. Returns sorted descending by total score."""
    if not candidate_paths:
        return []

    user_decade_dist = _user_decade_distribution(store)
    user_style_freq = _user_style_frequency(store)

    raw_connections: dict[int, float] = {
        rid: sum(p.seed_weight * p.edge_weight for p in ps if p.seed_kind == "direct")
        for rid, ps in candidate_paths.items()
    }
    max_conn = max(raw_connections.values()) or 1.0

    raw_influences: dict[int, float] = {
        rid: sum(p.seed_weight * p.edge_weight for p in ps if p.seed_kind == "influence")
        for rid, ps in candidate_paths.items()
    }
    max_infl = max(raw_influences.values()) or 1.0

    have_values = [releases[rid].community_have for rid in candidate_paths if rid in releases]
    max_have = max(have_values) if have_values else 1
    max_label_count = max(label_release_counts.values()) if label_release_counts else 1

    scored: list[ScoredCandidate] = []

    for rid, ps in candidate_paths.items():
        rel = releases.get(rid)
        if rel is None:
            continue

        sub = {
            "connection": raw_connections[rid] / max_conn,
            "influence_chain": raw_influences[rid] / max_infl,
            "rarity": 1.0 - math.log(rel.community_have + 1) / math.log(max_have + 1),
            "demand_ratio": min(1.0, (rel.community_want / max(rel.community_have, 1)) / 2.0),
            "label_obscurity": 1.0 - math.log(label_release_counts.get(rid, 1) + 1) / math.log(max_label_count + 1),
            "style_niche": _style_niche(rel.styles, user_style_freq),
            "rating": _rating_score(rel),
            "format": _format_score(rel),
            "recency_match": _decade_match(rel.year, user_decade_dist),
        }
        total = sum(weights[k] * sub[k] for k in sub)
        scored.append(ScoredCandidate(
            release_id=rid, score=total, subscores=sub, paths=tuple(ps),
        ))

    scored.sort(key=lambda s: -s.score)
    return scored


def _rating_score(rel: Release) -> float:
    if rel.community_rating_count < _RATING_COUNT_FLOOR:
        return 0.0
    return max(0.0, min(1.0, (rel.community_avg_rating - 3.0) / 2.0))


def _format_score(rel: Release) -> float:
    if rel.is_compilation:
        return 0.3
    if rel.is_album_or_ep:
        return 1.0
    return 0.0


def _style_niche(styles: list[str], user_freq: dict[str, float]) -> float:
    if not styles:
        return 0.5
    avg_freq = sum(user_freq.get(s, 0.0) for s in styles) / len(styles)
    return max(0.0, min(1.0, 1.0 - avg_freq))


def _user_style_frequency(store: CacheStore) -> dict[str, float]:
    rows = store.conn.execute(
        "SELECT style FROM release_styles WHERE release_id IN ("
        "  SELECT release_id FROM collection_items"
        ")"
    )
    counts = Counter(r["style"] for r in rows)
    if not counts:
        return {}
    total = sum(counts.values())
    return {style: n / total for style, n in counts.items()}


def _user_decade_distribution(store: CacheStore) -> dict[int, float]:
    rows = store.conn.execute(
        "SELECT year FROM releases WHERE id IN ("
        "  SELECT release_id FROM collection_items"
        ")"
    )
    years = [int(r["year"]) for r in rows if r["year"]]
    if not years:
        return {}
    decades = Counter((y // 10) * 10 for y in years)
    total = sum(decades.values())
    return {d: n / total for d, n in decades.items()}


def _decade_match(year: int, user_dist: dict[int, float]) -> float:
    if not user_dist or not year:
        return 0.5
    return user_dist.get((year // 10) * 10, 0.0)
