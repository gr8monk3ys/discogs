"""`discogs stats` — taste profile of your library (cached data only, no API)."""
from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from discogs.cache.store import CacheStore, init_db
from discogs.config import load_config


@click.command("stats")
@click.option("--scope", type=click.Choice(["collection", "wantlist", "both"]),
              default="both", show_default=True,
              help="Which library half to profile.")
@click.option("--top", type=int, default=10, show_default=True,
              help="How many styles/labels to list.")
def stats_cmd(scope: str, top: int) -> None:
    """Show era, style, and label distribution of your library."""
    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    try:
        size = store.library_size(scope)
        cached = store.cached_release_count(scope)
        if size == 0:
            click.echo(f"No releases in {scope}. Run `discogs sync` first.")
            return

        click.echo(
            f"Library ({scope}): {size} releases, {cached} with cached detail."
        )
        if cached == 0:
            click.echo(
                "No release detail cached yet — run `discogs recommend` once to "
                "populate it (stats read the same cache)."
            )
            return

        console = Console()

        decades = store.decade_distribution(scope)
        if decades:
            peak = max(n for _, n in decades)
            era = Table(title="By decade")
            era.add_column("decade")
            era.add_column("count", justify="right")
            era.add_column("")
            for decade, n in decades:
                bar = "█" * round(20 * n / peak)
                era.add_row(f"{decade}s", str(n), bar)
            console.print(era)

        _print_counts(console, "Top styles", store.top_styles(scope, top))
        _print_counts(console, "Top labels", store.top_labels(scope, top))
    finally:
        store.close()


def _print_counts(console: Console, title: str, rows: list[tuple[str, int]]) -> None:
    if not rows:
        return
    table = Table(title=title)
    table.add_column(title.split()[-1])
    table.add_column("count", justify="right")
    for name, n in rows:
        table.add_row(name, str(n))
    console.print(table)
