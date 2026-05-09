"""`discogs auth ...` subcommands."""
from __future__ import annotations

from pathlib import Path

import click


@click.group()
def auth_group() -> None:
    """Manage Discogs authentication."""


@auth_group.command("set")
def set_token() -> None:
    """Store a Discogs personal access token in ~/.discogs/config.toml."""
    token = click.prompt("Discogs personal access token", hide_input=True)
    username = click.prompt("Discogs username")

    config_dir = Path.home() / ".discogs"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.toml"
    config_path.write_text(
        f'[discogs]\ntoken = "{token}"\nusername = "{username}"\n'
    )
    config_path.chmod(0o600)
    click.echo(f"Saved config to {config_path}")
