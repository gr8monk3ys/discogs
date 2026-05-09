# discogs

A Python library + CLI for the Discogs API. Phase 1 ships collection/wantlist sync into a local cache. Recommendation features (Phase 2+) follow.

## Install

```bash
git clone <this-repo>
cd discogs
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quickstart

```bash
# 1. Save your Discogs personal access token + username
discogs auth set
# Prompts (token is hidden):
#   Discogs personal access token: ********
#   Discogs username: lorenzo

# 2. Sync collection + wantlist into ~/.discogs/cache.db (~1 minute on first run)
discogs sync

# 3. Check status
discogs status
```

Get a personal access token at <https://www.discogs.com/settings/developers>.

## Config

`~/.discogs/config.toml`:

```toml
[discogs]
token = "..."
username = "lorenzo"

[cache]
path = "~/.discogs/cache.db"   # optional override
```

Env overrides: `DISCOGS_TOKEN`, `ANTHROPIC_API_KEY`.

## Commands

| Command | Purpose |
|---|---|
| `discogs auth set` | Save token to `~/.discogs/config.toml` (chmod 600) |
| `discogs sync [--scope collection\|wantlist\|both] [--force]` | Sync into local cache. 24h TTL by default. |
| `discogs status` | Show username, cache size, last sync, API budget |

## Development

```bash
pytest                        # unit + integration (cassettes)
ruff check src/ tests/        # lint
mypy src/                     # types
```

See `docs/superpowers/specs/` for the full design and `docs/superpowers/plans/` for the implementation plan.
