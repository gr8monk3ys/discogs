"""`discogs sync` command."""
from __future__ import annotations

import click

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import load_config
from discogs.sync.syncer import Syncer


def _build_syncer() -> Syncer:
    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store)
    return Syncer(cfg, store, client)


@click.command("sync")
@click.option(
    "--scope",
    type=click.Choice(["collection", "wantlist", "both"]),
    default="both",
    show_default=True,
)
@click.option("--force", is_flag=True, help="Bypass the 24h TTL.")
def sync_cmd(scope: str, force: bool) -> None:
    """Sync collection and/or wantlist into the local cache."""
    syncer = _build_syncer()
    result = syncer.sync(scope=scope, force=force)  # type: ignore[arg-type]

    parts: list[str] = []
    if result.collection_synced is None and scope in ("collection", "both"):
        parts.append("collection: skipped (within TTL)")
    elif result.collection_synced is not None:
        parts.append(f"collection: {result.collection_synced} items")
    if result.wantlist_synced is None and scope in ("wantlist", "both"):
        parts.append("wantlist: skipped (within TTL)")
    elif result.wantlist_synced is not None:
        parts.append(f"wantlist: {result.wantlist_synced} items")

    click.echo(" / ".join(parts))
