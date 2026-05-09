"""`discogs recommend` command — Phase 2 dry-run only."""
from __future__ import annotations

import click

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config, load_config
from discogs.recommend.digest import render_digest
from discogs.recommend.pipeline import run_recommend


def _build_pipeline_context() -> tuple[DiscogsClient, CacheStore, Config]:
    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store)
    return client, store, cfg


@click.command("recommend")
@click.option("--max-recs", type=int, default=25, show_default=True,
              help="Top-N picks per run after diversity guard.")
@click.option("--budget", type=int, default=800, show_default=True,
              help="Hard cap on API calls during the graph walk.")
@click.option("--scope", type=click.Choice(["collection", "wantlist", "both"]),
              default="both", show_default=True,
              help="Which library half supplies seed artists.")
@click.option("--apply", "apply_flag", is_flag=True,
              help="Push picks to your wantlist (NOT YET SUPPORTED — Phase 4).")
def recommend_cmd(max_recs: int, budget: int, scope: str, apply_flag: bool) -> None:
    """Generate top-N recommendations and write a markdown digest. Dry-run only."""
    if apply_flag:
        raise click.UsageError("--apply is not yet supported (Phase 4).")

    client, store, cfg = _build_pipeline_context()
    try:
        result = run_recommend(
            client, store, cfg,
            max_recs=max_recs, budget=budget, seed_mode=scope,  # type: ignore[arg-type]
        )

        digest_md = render_digest(store, result)

        cfg.digests_dir.mkdir(parents=True, exist_ok=True)
        digest_path = cfg.digests_dir / f"{result.run_display_id}-recommendations.md"
        digest_path.write_text(digest_md)

        click.echo(f"Wrote digest: {digest_path}")
        click.echo(
            f"  run_id: {result.run_display_id}  "
            f"seeds: {result.seed_count}  "
            f"candidates: {result.candidate_count}  "
            f"selected: {len(result.picks)}  "
            f"API calls: {result.api_calls_used}"
        )
    finally:
        store.close()
