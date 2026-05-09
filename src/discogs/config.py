"""Load configuration from ~/.discogs/config.toml with env overrides."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def _default_config_path() -> Path:
    return Path.home() / ".discogs" / "config.toml"


def _default_cache_path() -> Path:
    return Path.home() / ".discogs" / "cache.db"


def _default_digests_dir() -> Path:
    return Path.home() / ".discogs" / "digests"


@dataclass
class Config:
    discogs_token: str = field(repr=False)
    discogs_username: str
    anthropic_api_key: str | None = field(default=None, repr=False)
    cache_path: Path = field(default_factory=_default_cache_path)
    digests_dir: Path = field(default_factory=_default_digests_dir)
    user_agent: str = "discogs-recommender/0.1.0 (+https://github.com/gr8monk3ys/discogs)"
    daily_api_budget: int = 1500

    def __repr__(self) -> str:
        return (
            f"Config(discogs_token='***', discogs_username={self.discogs_username!r}, "
            f"anthropic_api_key={'***' if self.anthropic_api_key else None}, "
            f"cache_path={self.cache_path!r}, user_agent={self.user_agent!r}, "
            f"daily_api_budget={self.daily_api_budget})"
        )


def load_config(path: Path | None = None) -> Config:
    if path is None:
        path = _default_config_path()
    if not path.exists():
        raise FileNotFoundError(
            f"No config at {path}. Run `discogs auth set` to create one."
        )

    with path.open("rb") as f:
        data = tomllib.load(f)

    discogs = data.get("discogs", {})
    token = os.environ.get("DISCOGS_TOKEN") or discogs.get("token")
    if not token:
        raise ValueError(
            "Missing Discogs token. Set DISCOGS_TOKEN env or `[discogs] token = ...` in config."
        )
    username = discogs.get("username")
    if not username:
        raise ValueError("Missing `[discogs] username` in config.")

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or data.get("anthropic", {}).get("api_key")

    cache_path_str = data.get("cache", {}).get("path")
    cache_path = Path(cache_path_str).expanduser() if cache_path_str else _default_cache_path()

    return Config(
        discogs_token=token,
        discogs_username=username,
        anthropic_api_key=anthropic_key,
        cache_path=cache_path,
    )
