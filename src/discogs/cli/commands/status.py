"""`discogs status` command."""
from __future__ import annotations

from datetime import UTC, datetime

import click
from rich.console import Console
from rich.table import Table

from discogs.cache.store import CacheStore, init_db
from discogs.config import load_config


def _humanize(when: datetime | None) -> str:
    if when is None:
        return "never"
    delta = datetime.now(UTC) - when
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


@click.command("status")
def status_cmd() -> None:
    """Show config, cache, and API-budget status."""
    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    try:
        cache_size = cfg.cache_path.stat().st_size if cfg.cache_path.exists() else 0
        last_collection = store.last_sync_at("collection")
        last_wantlist = store.last_sync_at("wantlist")
        calls_today = store.api_calls_today()
        budget = cfg.daily_api_budget

        table = Table(title="discogs status")
        table.add_column("key")
        table.add_column("value")
        table.add_row("username", cfg.discogs_username)
        table.add_row("cache path", str(cfg.cache_path))
        table.add_row("cache size", f"{cache_size / 1024:.1f} KiB")
        table.add_row("last collection sync", _humanize(last_collection))
        table.add_row("last wantlist sync", _humanize(last_wantlist))
        table.add_row("API calls today", f"{calls_today} / {budget}")
        Console().print(table)
    finally:
        store.close()
