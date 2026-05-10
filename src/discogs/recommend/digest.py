"""Markdown digest renderer for a recommendation run."""
from __future__ import annotations

from discogs.cache.store import CacheStore
from discogs.models import Release
from discogs.recommend.apply import ApplyReport
from discogs.recommend.pipeline import RunResult
from discogs.recommend.scoring import ScoredCandidate


def render_digest(
    store: CacheStore,
    result: RunResult,
    *,
    apply_report: ApplyReport | None = None,
) -> str:
    lines: list[str] = []
    lines.append(f"# Discogs recommendations — {result.run_display_id}\n")
    lines.append(f"Run: `{result.run_display_id}` (uuid: `{result.run_id}`)")
    lines.append(
        f"Seeds: {result.seed_count} artists  "
        f"Candidates: {result.candidate_count} considered → {len(result.picks)} selected\n"
    )

    if not result.picks:
        lines.append("_No picks this run._\n")
    else:
        for rank, pick in enumerate(result.picks, start=1):
            lines.append(_render_pick(store, rank, pick))

    lines.append("## Run stats\n")
    lines.append(f"- API calls: {result.api_calls_used}")
    lines.append(f"- Wall time: {_fmt_seconds(result.wall_seconds)}")
    if result.picks:
        primary_artists = {p.paths[0].seed_artist_id for p in result.picks if p.paths}
        lines.append(f"- Distinct seed artists in selection: {len(primary_artists)}")

    if apply_report is not None:
        lines.append("")
        lines.append("## Apply outcome\n")
        lines.append(f"- {apply_report.successes} successes")
        lines.append(f"- {apply_report.failures} failures")
        if apply_report.skipped_already_applied:
            lines.append(f"- {apply_report.skipped_already_applied} skipped (already applied)")
        if apply_report.failed_picks:
            lines.append("\n**Failed picks:**")
            for rid, err in apply_report.failed_picks:
                lines.append(f"- release `{rid}`: {err}")

    return "\n".join(lines) + "\n"


def _render_pick(store: CacheStore, rank: int, pick: ScoredCandidate) -> str:
    rel = store.get_release(pick.release_id)
    title = rel.title if rel else f"release #{pick.release_id}"
    year = rel.year if rel else 0
    fmt_str = _format_summary(rel) if rel else "?"
    have = rel.community_have if rel else 0
    want = rel.community_want if rel else 0
    rating = rel.community_avg_rating if rel else 0.0
    rating_count = rel.community_rating_count if rel else 0
    styles = ", ".join(rel.styles) if rel and rel.styles else ""

    label_ids = store.get_release_label_ids(pick.release_id)
    label_names: list[str] = []
    for lid in label_ids:
        lab = store.get_label(lid)
        if lab is not None:
            label_names.append(lab.name)
    label_str = ", ".join(label_names) if label_names else "—"

    primary_path = pick.paths[0] if pick.paths else None
    seed_artist = (
        store.get_artist(primary_path.seed_artist_id) if primary_path else None
    )
    seed_name = seed_artist.name if seed_artist else (
        f"artist #{primary_path.seed_artist_id}" if primary_path else "?"
    )
    chain_kind = "direct" if primary_path and len(primary_path.edge_chain) == 1 else "neighbor"

    parts = [
        f"## {rank}. {seed_name} — {title} ({year})  [score: {pick.score:.2f}]",
        f"- Label: {label_str}",
        f"- Format: {fmt_str}",
        f"- Discogs: {have:,} have / {want:,} want / {rating:.1f} avg ({rating_count} ratings)",
    ]
    if styles:
        parts.append(f"- Styles: {styles}")
    parts.append(
        f"- Connection: {seed_name} [{chain_kind}, weight {primary_path.seed_weight:.2f}]"
        if primary_path else ""
    )
    if pick.enrichment is not None:
        note = pick.enrichment.note.strip()
        confidence = pick.enrichment.confidence
        parts.append(f"> {note}")
        parts.append(f"> *(Claude editorial — confidence: {confidence})*")
    parts.append("")
    return "\n".join(p for p in parts if p)


def _format_summary(rel: Release | None) -> str:
    if rel is None or not rel.formats:
        return "?"
    f = rel.formats[0]
    descs = ", ".join(f.descriptions) if f.descriptions else ""
    return f"{f.name}{f' ({descs})' if descs else ''}"


def _fmt_seconds(secs: float) -> str:
    secs_int = int(secs)
    if secs_int < 60:
        return f"{secs_int}s"
    return f"{secs_int // 60}m {secs_int % 60}s"
