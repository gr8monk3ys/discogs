"""`discogs import-spotify` command."""
from __future__ import annotations

from pathlib import Path

import click

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import load_config
from discogs.spotify import interchange
from discogs.spotify.importer import import_artists


@click.command("import-spotify")
@click.option(
    "--file",
    "file_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help=f"Interchange file to read [default: {interchange.DEFAULT_PATH}]",
)
@click.option(
    "--limit",
    type=int,
    default=200,
    show_default=True,
    help="Maximum artists to resolve this run (each costs one API call).",
)
@click.option(
    "--refresh",
    is_flag=True,
    help="Re-resolve artists that already have a Discogs id.",
)
def import_spotify_cmd(file_path: Path | None, limit: int, refresh: bool) -> None:
    """Import the Spotify library so recommendations seed from it.

    Reads the `music-library.json` written by `spotifyforge export library`
    and resolves each credited artist to a Discogs artist, caching the
    result permanently. Re-running is cheap: only artists without a
    resolution cost an API call.
    """
    try:
        data = interchange.load(file_path)
    except interchange.InterchangeError as exc:
        raise click.ClickException(str(exc)) from exc

    artists = interchange.distinct_artists(data)
    if not artists:
        raise click.ClickException("The interchange file lists no artists.")

    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store)
    try:
        result = import_artists(store, client, artists, limit=limit, refresh=refresh)
        total, resolved_total = store.spotify_import_counts()
    finally:
        store.close()

    click.echo(
        f"{len(artists)} artist(s) in the library: "
        f"{result.resolved} newly resolved, {result.unresolved} unresolved, "
        f"{result.skipped} already known."
    )
    click.echo(f"{resolved_total} of {total} imported artists now map to a Discogs artist.")
    pending = total - resolved_total - result.unresolved
    if pending > 0:
        click.echo(f"{pending} still to resolve — re-run to continue.")
    click.echo("Seed recommendations from them with: discogs recommend --mode all")
