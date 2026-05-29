"""`discogs diff <run-A> <run-B>` — compare the picks of two runs."""
from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from discogs.cache.store import CacheStore, init_db
from discogs.config import load_config


def _title(store: CacheStore, release_id: int) -> str:
    rel = store.get_release(release_id)
    return rel.title if rel else f"release #{release_id}"


@click.command("diff")
@click.argument("run_a")
@click.argument("run_b")
def diff_cmd(run_a: str, run_b: str) -> None:
    """Show how the picks of RUN_A and RUN_B differ (by display id)."""
    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    try:
        id_a = store.get_run_by_display_id(run_a)
        id_b = store.get_run_by_display_id(run_b)
        if id_a is None:
            raise click.ClickException(f"No run with display id {run_a!r}.")
        if id_b is None:
            raise click.ClickException(f"No run with display id {run_b!r}.")

        scores_a = {r["release_id"]: r["score"] for r in store.get_recommendations_for_run(id_a)}
        scores_b = {r["release_id"]: r["score"] for r in store.get_recommendations_for_run(id_b)}

        dropped = sorted(scores_a.keys() - scores_b.keys())
        added = sorted(scores_b.keys() - scores_a.keys())
        common = scores_a.keys() & scores_b.keys()

        console = Console()
        table = Table(title=f"diff {run_a} → {run_b}")
        table.add_column("change")
        table.add_column("release")
        table.add_column(run_a, justify="right")
        table.add_column(run_b, justify="right")

        for rid in added:
            table.add_row("+ added", _title(store, rid), "—", f"{scores_b[rid]:.3f}")
        for rid in dropped:
            table.add_row("- dropped", _title(store, rid), f"{scores_a[rid]:.3f}", "—")
        changed = sorted(
            (rid for rid in common if abs(scores_a[rid] - scores_b[rid]) > 1e-6),
            key=lambda rid: -abs(scores_a[rid] - scores_b[rid]),
        )
        for rid in changed:
            table.add_row(
                "~ rescored", _title(store, rid),
                f"{scores_a[rid]:.3f}", f"{scores_b[rid]:.3f}",
            )
        console.print(table)
        click.echo(
            f"{len(added)} added, {len(dropped)} dropped, "
            f"{len(changed)} rescored, {len(common) - len(changed)} unchanged."
        )
    finally:
        store.close()
