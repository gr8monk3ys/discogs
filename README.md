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

## Recommendations (Phase 2)

After your first sync:

```bash
discogs recommend
# Wrote digest: ~/.discogs/digests/2026-05-08-183045-recommendations.md
```

Open the digest to review the top picks. Each pick lists the seed artist that surfaced it, the label, format, community stats, and styles. Phase 2 picks are scored on 8 sub-scores in `[0, 0.85]` — the missing 0.15 is `influence_chain_score`, populated in Phase 3.

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
| `discogs recommend [--max-recs 25] [--budget 800] [--scope ...]` | Generate top-N picks; writes a markdown digest under `~/.discogs/digests/`. Dry-run only in Phase 2. |

## Development

```bash
pytest                        # unit + integration (cassettes)
ruff check src/ tests/        # lint
mypy src/                     # types
```

See `docs/superpowers/specs/` for the full design and `docs/superpowers/plans/` for the implementation plan.
