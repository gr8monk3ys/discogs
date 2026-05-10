"""`discogs recommend` command — generate picks; with --apply, push to wantlist."""
from __future__ import annotations

import click

from discogs.api.client import DiscogsClient
from discogs.api.llm import LLMClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config, load_config
from discogs.recommend.apply import apply_run
from discogs.recommend.digest import render_digest
from discogs.recommend.pipeline import run_recommend


def _build_pipeline_context() -> tuple[DiscogsClient, CacheStore, Config]:
    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store)
    return client, store, cfg


def _build_llm_client(cfg: Config, store: CacheStore) -> LLMClient:
    return LLMClient(cfg, store)


@click.command("recommend")
@click.option("--max-recs", type=int, default=25, show_default=True,
              help="Top-N picks per run after diversity guard.")
@click.option("--budget", type=int, default=800, show_default=True,
              help="Hard cap on Discogs API calls during the graph walk.")
@click.option("--scope", type=click.Choice(["collection", "wantlist", "both"]),
              default="both", show_default=True,
              help="Which library half supplies seed artists.")
@click.option("--no-influences", "no_influences", is_flag=True,
              help="Skip Stage 1.5 (Claude-derived influence expansion).")
@click.option("--no-enrich", "no_enrich", is_flag=True,
              help="Skip Stage 4 (Claude editorial notes per pick).")
@click.option("--apply", "apply_flag", is_flag=True,
              help="Push picks to your Discogs wantlist after writing the digest.")
@click.option("--yes", "skip_confirm", is_flag=True,
              help="Bypass the first-apply confirmation prompt.")
def recommend_cmd(
    max_recs: int, budget: int, scope: str,
    no_influences: bool, no_enrich: bool,
    apply_flag: bool, skip_confirm: bool,
) -> None:
    """Generate top-N recommendations and write a markdown digest."""
    client, store, cfg = _build_pipeline_context()
    try:
        llm: LLMClient | None = None
        with_influences = not no_influences
        with_enrichment = not no_enrich

        if cfg.anthropic_api_key:
            llm = _build_llm_client(cfg, store)
        else:
            if with_influences or with_enrichment:
                click.echo(
                    "WARNING: no Anthropic API key configured — LLM features disabled.\n"
                    "  Set [anthropic] api_key in ~/.discogs/config.toml or "
                    "ANTHROPIC_API_KEY env var to enable.",
                    err=True,
                )
            with_influences = False
            with_enrichment = False

        result = run_recommend(
            client, store, cfg,
            llm=llm,
            max_recs=max_recs, budget=budget, seed_mode=scope,
            with_influences=with_influences, with_enrichment=with_enrichment,
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

        if apply_flag:
            if not store.has_any_apply() and not skip_confirm and not click.confirm(
                f"\nThis will push {len(result.picks)} releases to your "
                f"Discogs wantlist. First-time apply requires confirmation. "
                f"Proceed?",
                default=False,
            ):
                click.echo("Apply cancelled. Digest written but no wantlist changes.")
                return

            click.echo(f"\nApplying picks for run {result.run_display_id}...")
            ar = apply_run(client, store, username=cfg.discogs_username, run_id=result.run_id)
            click.echo(f"  Applied {ar.successes} successes, {ar.failures} failures.")
            if ar.failures:
                click.echo("  Failed picks:")
                for rid, err in ar.failed_picks:
                    click.echo(f"    - release {rid}: {err}")
    finally:
        store.close()
