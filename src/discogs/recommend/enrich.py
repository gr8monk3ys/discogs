"""Stage 4: ask Claude for short editorial notes per top candidate."""
from __future__ import annotations

import json

from discogs.api.llm import LLMClient
from discogs.models import Release
from discogs.recommend.scoring import Enrichment, ScoredCandidate

BATCH_SIZE = 10
_BOOST = {"high": 0.05, "medium": 0.0, "low": -0.03}

_SYSTEM_PROMPT = """You are a music historian writing concise, factual notes about
specific Discogs releases. Use what you know about the artist, the label, and the era.
Do not speculate. If you don't know, say so.

Respond with strict JSON only — no prose, no markdown, no preamble."""


def enrich_candidates(
    llm: LLMClient,
    candidates: list[ScoredCandidate],
    releases: dict[int, Release],
) -> list[ScoredCandidate]:
    """For each candidate, attach an Enrichment (note + confidence) and adjust the
    score by ±0.05/±0.03 based on confidence. Original candidates with no Claude
    coverage are returned unchanged.
    """
    if not candidates:
        return []

    notes: dict[int, Enrichment] = {}
    for batch_start in range(0, len(candidates), BATCH_SIZE):
        batch = candidates[batch_start:batch_start + BATCH_SIZE]
        notes.update(_enrich_one_batch(llm, batch, releases))

    out: list[ScoredCandidate] = []
    for cand in candidates:
        ench = notes.get(cand.release_id)
        if ench is None:
            out.append(cand)
            continue
        adjusted = max(0.0, min(1.0, cand.score + _BOOST.get(ench.confidence, 0.0)))
        out.append(ScoredCandidate(
            release_id=cand.release_id, score=adjusted,
            subscores=cand.subscores, paths=cand.paths,
            enrichment=ench,
        ))
    return out


def _enrich_one_batch(
    llm: LLMClient,
    batch: list[ScoredCandidate],
    releases: dict[int, Release],
) -> dict[int, Enrichment]:
    items = []
    for cand in batch:
        rel = releases.get(cand.release_id)
        if rel is None:
            continue
        items.append({
            "release_id": cand.release_id,
            "title": rel.title,
            "year": rel.year,
            "styles": rel.styles,
        })

    if not items:
        return {}

    user = (
        f"For each of the following Discogs releases, write a 2-3 sentence note "
        f"explaining why it might matter to a collector. Highlight: notable "
        f"personnel when known, scene/era context, what makes it distinctive. "
        f'Output JSON: {{"items": [{{"release_id": <int>, "note": <str>, '
        f'"confidence": "high"|"medium"|"low"}}, ...]}}\n\n'
        f"Releases:\n{json.dumps(items, indent=2)}"
    )

    raw = llm.complete(system=_SYSTEM_PROMPT, user=user)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    items_out = data.get("items", []) if isinstance(data, dict) else []
    result: dict[int, Enrichment] = {}
    for item in items_out:
        if not isinstance(item, dict):
            continue
        rid = item.get("release_id")
        note = item.get("note")
        conf = item.get("confidence")
        if not isinstance(rid, int) or not isinstance(note, str):
            continue
        if conf not in {"high", "medium", "low"}:
            continue
        result[rid] = Enrichment(note=note, confidence=conf)
    return result
