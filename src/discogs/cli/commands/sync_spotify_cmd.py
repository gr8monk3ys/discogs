"""`discogs sync-spotify [--apply]` — favourites onto the wantlist, owned off it."""
from __future__ import annotations

from pathlib import Path

import click

from discogs.api.client import BudgetExceeded, DiscogsClient
from discogs.api.releases import fetch_release
from discogs.api.search import ResolvedRelease, resolve_release
from discogs.cache.store import CacheStore, init_db
from discogs.config import load_config
from discogs.models import Release
from discogs.recommend.apply import apply_run
from discogs.spotify import interchange
from discogs.spotify.sync import Candidate, plan_sync
from discogs.spotify.sync_digest import render_sync_digest
from discogs.sync.syncer import Syncer
from discogs.wantlist_writer import remove_from_wantlist


def _releases(client: DiscogsClient, store: CacheStore, ids: set[int]) -> list[Release]:
    """Cache hits unless a row predates the artists column (schema v4)."""
    return [fetch_release(client, store, rid, force_artists=True) for rid in sorted(ids)]


@click.command("sync-spotify")
@click.option(
    "--file", "file_path", type=click.Path(path_type=Path, dir_okay=False), default=None,
    help=f"Interchange file to read [default: {interchange.DEFAULT_PATH}]",
)
@click.option(
    "--limit", type=int, default=50, show_default=True,
    help="Maximum candidates to resolve this run (one or two API calls each).",
)
@click.option("--apply", "do_apply", is_flag=True,
              help="Push the additions and remove the prunes after writing the digest.")
@click.option("--yes", "skip_confirm", is_flag=True,
              help="Bypass the first-apply confirmation prompt.")
def sync_spotify_cmd(file_path: Path | None, limit: int, do_apply: bool, skip_confirm: bool) -> None:
    """Plan wantlist changes from the Spotify library. Dry run unless --apply.

    Additions are Spotify favourites (above the [spotify] thresholds in
    config) that are neither owned nor wanted, resolved to exactly one
    Discogs release. Prunes are wantlist entries whose master is now in the
    collection. The run is recorded like `recommend`, so `discogs apply <id>`
    and `discogs undo <id>` work on the additions.
    """
    try:
        data = interchange.load(file_path)
    except interchange.InterchangeError as exc:
        raise click.ClickException(str(exc)) from exc
    albums = interchange.albums(data)

    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store)
    try:
        # The wantlist is what this command changes, so the 24h TTL is
        # wrong for it: a second pass an hour after the first must see the
        # first pass's additions or it re-proposes every one of them.
        # Two API calls; the collection keeps its TTL.
        Syncer(cfg, store, client).sync(scope="wantlist", force=True)
        collection = _releases(client, store, store.collection_release_ids())
        wanted_ids = store.wantlist_release_ids()
        wantlist = _releases(client, store, wanted_ids)
        plan = plan_sync(
            albums, collection, wantlist,
            min_affinity=cfg.wantlist_min_affinity, min_liked=cfg.wantlist_min_liked,
        )

        resolved: list[tuple[Candidate, ResolvedRelease]] = []
        unresolved: list[Candidate] = []
        partial = False
        for candidate in plan.candidates[:limit]:
            try:
                hit = resolve_release(client, candidate.artist, candidate.title)
            except BudgetExceeded:
                partial = True
                break
            if hit is None:
                unresolved.append(candidate)
            else:
                resolved.append((candidate, hit))

        run_id, display_id = store.start_run(
            {"kind": "spotify-sync", "file": str(file_path or interchange.default_path()),
             "limit": limit, "apply": do_apply},
        )
        # Name matching runs before resolution; this is the same guard on
        # the resolved id, for a release the wantlist holds under a name
        # the matcher did not join, or one an earlier run already pushed.
        already = wanted_ids | store.applied_release_ids()
        seen: set[int] = set()
        for candidate, hit in resolved:
            if hit.release_id in seen or hit.release_id in already:
                continue
            seen.add(hit.release_id)
            store.record_recommendation(run_id, hit.release_id, score=candidate.affinity)
        store.finish_run(run_id, {
            "kind": "spotify-sync",
            "added": len(seen),
            "pruned": [p.release_id for p in plan.prunes],
            "unresolved": len(unresolved),
            "already_owned": plan.already_owned,
            "already_wanted": plan.already_wanted,
            "partial": partial,
        })

        cfg.digests_dir.mkdir(parents=True, exist_ok=True)
        digest_path = cfg.digests_dir / f"{display_id}-spotify-sync.md"
        digest_path.write_text(render_sync_digest(
            display_id, plan, resolved, unresolved, limit=limit, applied=False, partial=partial,
        ))

        click.echo(
            f"Run {display_id}: {len(seen)} to add, {len(plan.prunes)} to prune, "
            f"{len(unresolved)} unresolved; {plan.already_owned} already owned, "
            f"{plan.already_wanted} already wanted."
        )
        remaining = len(plan.candidates) - min(limit, len(plan.candidates))
        if remaining > 0:
            click.echo(f"{remaining} candidate(s) beyond --limit {limit}; re-run to continue.")
        if partial:
            click.echo("Daily API budget exhausted part-way; the digest marks this run partial.")
        click.echo(f"Wrote digest: {digest_path}")

        if not do_apply:
            if seen:
                click.echo(f"Apply with: discogs apply {display_id}")
            return

        writes = len(seen) + len(plan.prunes)
        if writes == 0:
            click.echo("Nothing to apply.")
            return
        if not store.has_any_apply() and not skip_confirm and not click.confirm(
            f"\nThis will push {len(seen)} releases to your Discogs wantlist and remove "
            f"{len(plan.prunes)} from it. First-time apply requires confirmation. Proceed?",
            default=False,
        ):
            click.echo("Cancelled.")
            return

        try:
            report = apply_run(client, store, username=cfg.discogs_username, run_id=run_id)
            click.echo(
                f"Applied run {display_id}: {report.successes} successes, "
                f"{report.failures} failures."
            )
            for rid, err in report.failed_picks:
                click.echo(f"  - release {rid}: {err}")

            removed = skipped = errors = 0
            for prune in plan.prunes:
                outcome = remove_from_wantlist(
                    client, username=cfg.discogs_username, release_id=prune.release_id,
                )
                if outcome.status == "removed":
                    removed += 1
                elif outcome.status == "skipped":
                    skipped += 1
                else:
                    errors += 1
                    click.echo(f"  - release {prune.release_id}: {outcome.error}")
            click.echo(f"Pruned: removed {removed}, skipped {skipped}, errors {errors}.")
        except BudgetExceeded as e:
            raise click.ClickException(
                f"Daily Discogs API budget exhausted ({e}). "
                f"Successes so far were saved. Re-run tomorrow, or raise "
                f"daily_api_budget in ~/.discogs/config.toml."
            ) from e
        digest_path.write_text(render_sync_digest(
            display_id, plan, resolved, unresolved, limit=limit, applied=True, partial=partial,
        ))
    finally:
        store.close()
