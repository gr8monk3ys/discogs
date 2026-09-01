"""Load configuration from ~/.discogs/config.toml with env overrides."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# `MUSIC_DIR`, default ~/.music — the directory the collection tools share.
# Resolving it identically in each of them is the point of sharing the function:
# a tool that resolved it differently would read a stale file, or none, and
# report an empty library rather than an error.
from media_core.paths import music_dir as _default_music_dir


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
    music_dir: Path = field(default_factory=_default_music_dir)
    user_agent: str = "discogs-recommender/0.1.0 (+https://github.com/gr8monk3ys/discogs)"
    daily_api_budget: int = 1500
    daily_llm_budget: int = 100
    influences_model: str = "claude-haiku-4-5-20251001"
    enrich_model: str = "claude-haiku-4-5-20251001"
    # `discogs sync-spotify`: how much of a Spotify album must be liked before
    # it is worth a wantlist entry.
    wantlist_min_affinity: float = 0.6
    wantlist_min_liked: int = 4

    def __repr__(self) -> str:
        return (
            f"Config(discogs_token='***', discogs_username={self.discogs_username!r}, "
            f"anthropic_api_key={'***' if self.anthropic_api_key else None}, "
            f"cache_path={self.cache_path!r}, digests_dir={self.digests_dir!r}, "
            f"music_dir={self.music_dir!r}, "
            f"user_agent={self.user_agent!r}, "
            f"daily_api_budget={self.daily_api_budget}, "
            f"daily_llm_budget={self.daily_llm_budget}, "
            f"influences_model={self.influences_model!r}, "
            f"enrich_model={self.enrich_model!r})"
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

    llm_section = data.get("llm", {})
    daily_llm_budget = int(llm_section.get("daily_budget", 100))
    influences_model = str(llm_section.get("influences_model", "claude-haiku-4-5-20251001"))
    enrich_model = str(llm_section.get("enrich_model", "claude-haiku-4-5-20251001"))

    cache_path_str = data.get("cache", {}).get("path")
    cache_path = Path(cache_path_str).expanduser() if cache_path_str else _default_cache_path()

    spotify_section = data.get("spotify", {})
    wantlist_min_affinity = float(spotify_section.get("wantlist_min_affinity", 0.6))
    wantlist_min_liked = int(spotify_section.get("wantlist_min_liked", 4))

    return Config(
        discogs_token=token,
        discogs_username=username,
        anthropic_api_key=anthropic_key,
        cache_path=cache_path,
        daily_llm_budget=daily_llm_budget,
        influences_model=influences_model,
        enrich_model=enrich_model,
        wantlist_min_affinity=wantlist_min_affinity,
        wantlist_min_liked=wantlist_min_liked,
    )
