"""Markdown digest for a `discogs sync-spotify` run."""
from __future__ import annotations

from discogs.api.search import ResolvedRelease
from discogs.spotify.sync import Candidate, SyncPlan


def render_sync_digest(
    display_id: str,
    plan: SyncPlan,
    resolved: list[tuple[Candidate, ResolvedRelease]],
    unresolved: list[Candidate],
    *,
    limit: int,
    applied: bool,
    partial: bool = False,
) -> str:
    lines = [f"# Spotify → Discogs wantlist sync — {display_id}\n"]
    if partial:
        lines.append("**Partial run: the daily API budget ran out before every candidate was resolved.**\n")
    lines.append(
        f"Spotify favourites: {len(plan.candidates)} candidate(s) "
        f"({plan.already_owned} already owned, {plan.already_wanted} already wanted); "
        f"{min(limit, len(plan.candidates))} resolved this run.\n"
    )

    lines.append(f"## To add ({len(resolved)})\n")
    if not resolved:
        lines.append("_Nothing to add._\n")
    for c, r in resolved:
        year = f" ({c.year})" if c.year else ""
        lines.append(
            f"- **{c.artist} — {c.title}**{year} → release `{r.release_id}` "
            f"(master `{r.master_id}`, Discogs: {r.canonical}); "
            f"{c.liked} liked, affinity {c.affinity:.2f}"
        )
    lines.append("")

    lines.append(f"## To prune ({len(plan.prunes)})\n")
    if not plan.prunes:
        lines.append("_Nothing on the wantlist is already in the collection._\n")
    for p in plan.prunes:
        lines.append(f"- {p.artist} — {p.title} (release `{p.release_id}`): master now owned")
    lines.append("")

    lines.append(f"## Unresolved ({len(unresolved)})\n")
    if not unresolved:
        lines.append("_Every candidate resolved to exactly one release._\n")
    for c in unresolved:
        lines.append(f"- {c.artist} — {c.title}: zero or several Discogs masters match; not guessed")
    lines.append("")

    lines.append("## Outcome\n")
    lines.append(
        "- Applied to the wantlist." if applied
        else f"- Dry run. Apply the additions with `discogs apply {display_id}`; "
             f"prunes are only removed by `discogs sync-spotify --apply`."
    )
    return "\n".join(lines) + "\n"
