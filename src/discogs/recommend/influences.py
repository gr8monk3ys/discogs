"""Stage 1.5: ask Claude for an artist's influences (no resolution yet)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from discogs.api.client import DiscogsClient
from discogs.api.llm import LLMClient
from discogs.api.search import resolve_artist_name
from discogs.cache.store import CacheStore
from discogs.models import ArtistInfluence

Confidence = Literal["high", "medium", "low"]
_VALID_CONFIDENCES = {"high", "medium", "low"}

INFLUENCES_TTL = timedelta(days=90)

_SYSTEM_PROMPT = """You are a music historian assisting a record collector.
You answer with strict JSON only. No prose, no markdown, no preamble or
postscript — just the JSON object."""


@dataclass(frozen=True)
class InfluenceCandidate:
    name: str
    confidence: Confidence
    note: str


def fetch_influences_from_claude(
    llm: LLMClient, *, artist_name: str, artist_id: int,
    primary_styles: list[str],
) -> list[InfluenceCandidate]:
    """Ask Claude for 5-10 artists who influenced `artist_name`.

    Returns an empty list on parse failure or malformed items rather than raising —
    the caller wants to continue the run, just without this seed's influence edges.
    """
    styles_str = ", ".join(primary_styles) if primary_styles else "(unknown)"
    user = (
        f"Given the artist {artist_name!r} "
        f"(Discogs id {artist_id}, primary styles {styles_str}), "
        f"list 5-10 artists who clearly influenced them. For each, output an item "
        f'in the JSON array under key "items" with fields:\n'
        f'  name (string),\n'
        f'  confidence ("high" | "medium" | "low"),\n'
        f'  note (one short sentence justifying the influence).\n'
        f"Only include artists you are confident exist on Discogs and that you have "
        f"factual knowledge of. If unsure, return fewer names.\n\n"
        f'Output format: {{"items": [{{...}}, {{...}}]}}'
    )

    raw = llm.complete(system=_SYSTEM_PROMPT, user=user)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    items = data.get("items", []) if isinstance(data, dict) else []
    out: list[InfluenceCandidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        confidence = item.get("confidence")
        note = item.get("note", "")
        if not isinstance(name, str) or not isinstance(confidence, str):
            continue
        if confidence not in _VALID_CONFIDENCES:
            continue
        out.append(InfluenceCandidate(
            name=name, confidence=confidence,  # type: ignore[arg-type]
            note=str(note),
        ))
    return out


def expand_influences(
    discogs_client: DiscogsClient,
    store: CacheStore,
    llm: LLMClient,
    *,
    artist_id: int,
    artist_name: str,
    primary_styles: list[str],
) -> list[ArtistInfluence]:
    """Return influence edges for `artist_id`. Cache hit when entries are < 90 days
    old. On miss: ask Claude, resolve each candidate via Discogs search, persist
    the resolved set, and return it.

    Unresolved names are dropped silently (no edge persisted, no error raised).
    """
    age = store.artist_influences_age(source_artist_id=artist_id)
    if age is not None and age < INFLUENCES_TTL:
        return store.get_artist_influences(source_artist_id=artist_id)

    candidates = fetch_influences_from_claude(
        llm, artist_name=artist_name, artist_id=artist_id,
        primary_styles=primary_styles,
    )

    now = datetime.now(UTC)
    resolved: list[ArtistInfluence] = []
    for cand in candidates:
        hit = resolve_artist_name(discogs_client, cand.name)
        if hit is None:
            continue
        influence_id, _ = hit
        resolved.append(ArtistInfluence(
            source_artist_id=artist_id,
            influence_artist_id=influence_id,
            confidence=cand.confidence,
            source="claude",
            fetched_at=now,
        ))

    store.replace_artist_influences(source_artist_id=artist_id, edges=resolved,
                                    source="claude")
    return resolved
