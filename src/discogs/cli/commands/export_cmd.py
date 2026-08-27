"""`discogs export` — write collection + wantlist to ~/.music/discogs.json."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import click

from discogs.cache.store import CacheStore, init_db
from discogs.config import load_config
from discogs.export import build_export, write_export


@click.command("export")
@click.option(
    "--out",
    "out_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Where to write [default: $MUSIC_DIR/discogs.json, MUSIC_DIR=~/.music].",
)
def export_cmd(out_path: Path | None) -> None:
    """Export the cached collection and wantlist as discogs.json (no API calls)."""
    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    try:
        doc = build_export(store, cfg.discogs_username, datetime.now(UTC).isoformat())
        total_items = len(store.collection_release_ids()) + len(store.wantlist_release_ids())
    finally:
        store.close()

    target = write_export(doc, out_path or cfg.music_dir / "discogs.json")
    exported = len(doc["collection"]) + len(doc["wantlist"])
    click.echo(
        f"Exported {len(doc['collection'])} collection and {len(doc['wantlist'])} "
        f"wantlist item(s) to {target}"
    )
    skipped = total_items - exported
    if skipped > 0:
        click.echo(
            f"{skipped} item(s) skipped: release not cached — run discogs sync --force",
            err=True,
        )
