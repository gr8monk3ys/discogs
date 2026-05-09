"""Stage 1.5: ask Claude for an artist's influences (no resolution yet)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from discogs.api.llm import LLMClient

Confidence = Literal["high", "medium", "low"]
_VALID_CONFIDENCES = {"high", "medium", "low"}

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
