"""`discogs explain <release_id>` — show the score breakdown for a pick."""
from __future__ import annotations

import json

import click
from rich.console import Console
from rich.table import Table

from discogs.cache.store import CacheStore, init_db
from discogs.config import load_config
from discogs.recommend.scoring import DEFAULT_WEIGHTS


@click.command("explain")
@click.argument("release_id", type=int)
def explain_cmd(release_id: int) -> None:
    """Explain why RELEASE_ID was recommended: sub-score breakdown + run history."""
    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    try:
        rows = store.get_recommendations_for_release(release_id)
        if not rows:
            click.echo(f"Release {release_id} has never been recommended.")
            return

        rel = store.get_release(release_id)
        title = rel.title if rel else f"release #{release_id}"
        header = f"{title} ({rel.year})" if rel and rel.year else title
        click.echo(header)

        latest = rows[0]
        applied = " [applied to wantlist]" if latest["applied_to_wantlist"] else ""
        click.echo(
            f"Most recent: run {latest['display_id']} — "
            f"score {latest['score']:.3f}{applied}\n"
        )

        if latest["subscores_json"]:
            subscores = json.loads(latest["subscores_json"])
            table = Table(title="Score breakdown (most recent run)")
            table.add_column("sub-score")
            table.add_column("raw", justify="right")
            table.add_column("weight", justify="right")
            table.add_column("contribution", justify="right")
            base_total = 0.0
            for name, weight in DEFAULT_WEIGHTS.items():
                raw = float(subscores.get(name, 0.0))
                contribution = raw * weight
                base_total += contribution
                table.add_row(name, f"{raw:.3f}", f"{weight:.2f}", f"{contribution:.3f}")
            Console().print(table)
            click.echo(f"\nWeighted sum: {base_total:.3f}")
            delta = latest["score"] - base_total
            if abs(delta) > 1e-6:
                click.echo(
                    f"Final score:  {latest['score']:.3f}  "
                    f"(enrichment adjustment: {delta:+.3f})"
                )
        else:
            click.echo("(No sub-score breakdown stored — recommended before v2 schema.)")

        if len(rows) > 1:
            click.echo(f"\nRecommended across {len(rows)} runs:")
            for r in rows:
                click.echo(f"  - {r['display_id']}: score {r['score']:.3f}")
    finally:
        store.close()
