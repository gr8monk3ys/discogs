"""`discogs` CLI root."""
from __future__ import annotations

import click

from discogs.cli.commands.apply_cmd import apply_cmd
from discogs.cli.commands.auth import auth_group
from discogs.cli.commands.diff_cmd import diff_cmd
from discogs.cli.commands.explain_cmd import explain_cmd
from discogs.cli.commands.import_spotify_cmd import import_spotify_cmd
from discogs.cli.commands.recommend import recommend_cmd
from discogs.cli.commands.stats_cmd import stats_cmd
from discogs.cli.commands.status import status_cmd
from discogs.cli.commands.sync_cmd import sync_cmd
from discogs.cli.commands.undo_cmd import undo_cmd, undo_last_batch_cmd


@click.group()
@click.version_option(package_name="discogs")
def cli() -> None:
    """Discogs collection sync and recommendation framework."""


cli.add_command(auth_group, name="auth")
cli.add_command(sync_cmd)
cli.add_command(status_cmd)
cli.add_command(recommend_cmd)
cli.add_command(apply_cmd)
cli.add_command(undo_cmd)
cli.add_command(undo_last_batch_cmd)
cli.add_command(explain_cmd)
cli.add_command(diff_cmd)
cli.add_command(stats_cmd)
cli.add_command(import_spotify_cmd)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
