"""`discogs eval` — measure recommendation quality via held-out wantlist recall."""
from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from discogs.cache.store import init_db
from discogs.config import load_config
from discogs.eval.heldout import run_heldout_eval


@click.command("eval")
@click.option("--holdout", type=int, default=18, show_default=True,
              help="How many wantlist items to hide as the held-out test set.")
@click.option("--k", "k", type=int, default=50, show_default=True,
              help="Top-k cutoff for recall@k.")
@click.option("--seed", type=int, default=42, show_default=True,
              help="RNG seed for the holdout split (reproducible).")
@click.option("--min-occurrences", type=int, default=2, show_default=True,
              help="Min library appearances for an artist to become a seed.")
@click.option("--budget", type=int, default=1500, show_default=True,
              help="Max API calls allowed to fill cache gaps during the run.")
@click.option("--offline", is_flag=True,
              help="Forbid all network calls; fail loudly if the cache is incomplete.")
def eval_cmd(
    holdout: int, k: int, seed: int, min_occurrences: int, budget: int, offline: bool
) -> None:
    """Hide part of your wantlist, recommend over the rest, and score the recall.

    The wantlist is ground truth: things you've said you want. A held-out item that
    resurfaces near the top means the recommender captured your taste.
    """
    cfg = load_config()
    init_db(cfg.cache_path)

    try:
        result = run_heldout_eval(
            cfg, cfg.cache_path,
            holdout=holdout, k=k, seed=seed,
            min_seed_occurrences=min_occurrences, budget=budget,
            offline=offline,
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    console = Console()
    table = Table(title=f"Held-out wantlist recall (seed={seed})")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("wantlist size", str(result.wantlist_total))
    table.add_row("held out", str(result.holdout_size))
    table.add_row("candidates ranked", str(result.candidates_ranked))
    table.add_row(f"reachable / {result.holdout_size}", str(result.reachable))
    table.add_row(f"hits@{result.k}", str(result.hits_at_k))
    table.add_row(f"recall@{result.k}", f"{result.recall_at_k:.3f}")
    table.add_row("MRR", f"{result.mrr:.3f}")
    table.add_row("API calls used", str(result.api_calls_used))
    console.print(table)

    if result.reachable == 0:
        click.echo(
            "\nNothing held-out was even reachable. Run `discogs recommend` once "
            "first so the credit graph is cached, then re-run eval."
        )
    elif result.reachable < result.holdout_size:
        cap = result.reachable / result.holdout_size
        click.echo(
            f"\nReachability caps recall at {cap:.3f} — "
            f"{result.holdout_size - result.reachable} held-out item(s) never "
            "appeared as candidates (graph coverage, not scoring)."
        )
