"""`discogs` CLI root."""
from __future__ import annotations

import click

from discogs.cli.commands.auth import auth_group
from discogs.cli.commands.sync_cmd import sync_cmd


@click.group()
@click.version_option(package_name="discogs")
def cli() -> None:
    """Discogs collection sync and recommendation framework."""


cli.add_command(auth_group, name="auth")
cli.add_command(sync_cmd)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
