"""`discogs` CLI root."""
from __future__ import annotations

import click

from discogs.cli.commands.auth import auth_group
from discogs.cli.commands.recommend import recommend_cmd
from discogs.cli.commands.status import status_cmd
from discogs.cli.commands.sync_cmd import sync_cmd


@click.group()
@click.version_option(package_name="discogs")
def cli() -> None:
    """Discogs collection sync and recommendation framework."""


cli.add_command(auth_group, name="auth")
cli.add_command(sync_cmd)
cli.add_command(status_cmd)
cli.add_command(recommend_cmd)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
