"""`discogs apply <run-display-id>` — push a previous run's picks to wantlist."""
from __future__ import annotations

import click

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config, load_config
from discogs.recommend.apply import apply_run


def _build_pipeline_context() -> tuple[DiscogsClient, CacheStore, Config]:
    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store)
    return client, store, cfg


@click.command("apply")
@click.argument("run_display_id")
@click.option("--yes", "skip_confirm", is_flag=True,
              help="Bypass the first-apply confirmation prompt.")
def apply_cmd(run_display_id: str, skip_confirm: bool) -> None:
    """Push the picks of run RUN_DISPLAY_ID to your Discogs wantlist."""
    client, store, cfg = _build_pipeline_context()
    try:
        run_id = store.get_run_by_display_id(run_display_id)
        if run_id is None:
            raise click.ClickException(f"No run found for display id {run_display_id!r}.")

        picks = store.get_recommendations_for_run(run_id)
        if not picks:
            click.echo(f"Run {run_display_id} has no picks to apply.")
            return

        if not store.has_any_apply() and not skip_confirm and not click.confirm(
            f"\nThis will push {len(picks)} releases from run {run_display_id} "
            f"to your Discogs wantlist. First-time apply requires confirmation. "
            f"Proceed?",
            default=False,
        ):
            click.echo("Cancelled.")
            return

        report = apply_run(client, store, username=cfg.discogs_username, run_id=run_id)
        click.echo(
            f"Applied run {run_display_id}: "
            f"{report.successes} successes, {report.failures} failures, "
            f"{report.skipped_already_applied} already-applied skipped."
        )
        if report.failures:
            click.echo("Failed picks:")
            for rid, err in report.failed_picks:
                click.echo(f"  - release {rid}: {err}")
    finally:
        store.close()
