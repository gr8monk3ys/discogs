"""`discogs undo-last-batch` and `discogs undo <run-display-id>` commands."""
from __future__ import annotations

import click

from discogs.api.client import BudgetExceeded, DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config, load_config
from discogs.recommend.apply import undo_run


def _build_pipeline_context() -> tuple[DiscogsClient, CacheStore, Config]:
    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store)
    return client, store, cfg


def _confirm_and_undo(
    client: DiscogsClient, store: CacheStore, cfg: Config, *,
    run_id: str, label: str, skip_confirm: bool,
) -> None:
    picks = store.get_recommendations_for_run(run_id)
    applied = [p for p in picks if p["applied_to_wantlist"]]
    if not applied:
        click.echo(f"Run {label}: nothing currently applied to undo.")
        return

    if not skip_confirm and not click.confirm(
        f"\nUndo will remove {len(applied)} picks from run {label} "
        f"from your Discogs wantlist. Proceed?",
        default=False,
    ):
        click.echo("Cancelled.")
        return

    try:
        report = undo_run(client, store, username=cfg.discogs_username, run_id=run_id)
    except BudgetExceeded as e:
        raise click.ClickException(
            f"Daily Discogs API budget exhausted ({e}). "
            f"Removals so far were saved. Re-run tomorrow, or raise "
            f"daily_api_budget in ~/.discogs/config.toml."
        ) from e
    click.echo(
        f"Undone run {label}: "
        f"removed {report.removed}, skipped {report.skipped}, errors {report.errors}."
    )
    if report.errors:
        click.echo("Failed removals:")
        for rid, err in report.failed_picks:
            click.echo(f"  - release {rid}: {err}")


@click.command("undo-last-batch")
@click.option("--yes", "skip_confirm", is_flag=True, help="Bypass confirmation.")
def undo_last_batch_cmd(skip_confirm: bool) -> None:
    """Undo the most recently applied batch."""
    client, store, cfg = _build_pipeline_context()
    try:
        run_id = store.last_applied_run_id()
        if run_id is None:
            raise click.ClickException(
                "No applied runs in history — nothing to undo."
            )
        _confirm_and_undo(client, store, cfg, run_id=run_id, label="latest",
                          skip_confirm=skip_confirm)
    finally:
        store.close()


@click.command("undo")
@click.argument("run_display_id")
@click.option("--yes", "skip_confirm", is_flag=True, help="Bypass confirmation.")
def undo_cmd(run_display_id: str, skip_confirm: bool) -> None:
    """Undo a specific run by display id."""
    client, store, cfg = _build_pipeline_context()
    try:
        run_id = store.get_run_by_display_id(run_display_id)
        if run_id is None:
            raise click.ClickException(f"No run found for display id {run_display_id!r}.")
        _confirm_and_undo(client, store, cfg, run_id=run_id, label=run_display_id,
                          skip_confirm=skip_confirm)
    finally:
        store.close()
