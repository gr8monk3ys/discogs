# Discogs Recommender — Design

**Date:** 2026-05-08
**Status:** Draft, awaiting user review
**Owner:** Lorenzo

## Goal

Build a Python library + CLI ("framework of scripts," per the repo README) that uses the Discogs API to its fullest extent and uses the user's existing collection and wantlist as a seed for discovering obscure, musically-deep releases — then automatically expands the wantlist with the best picks.

## Non-goals

- No web UI in v1. The library/CLI split keeps a future UI cheap to add.
- No multi-user support. This is a personal tool.
- No catalogue beyond Discogs. We do not pull from Spotify, RYM, MusicBrainz, etc. in v1.
- No real-time / streaming behavior. Runs are batch jobs invoked on demand.

## Definitions

**"Obscure"** — composite signal. A release ranks more obscure when:
- Discogs `community.have` count is low (e.g., < 1,000 stronger; < 250 strongest).
- `community.want / community.have` ratio is high (sought-after but rare).
- Released on a small or independent label (label has a small `releases` catalogue, or label is unfamiliar to the user's collection).
- Tagged with niche style/genre intersections relative to the broader Discogs corpus.

**"Musically deep"** — composite signal. A release ranks deeper when:
- Discogs `community.rating.average ≥ 4.0` with `community.rating.count` ≥ a meaningful threshold.
- It is a full album or EP (not a single, compilation, reissue, or DJ mix), inferred from `format` and `tracklist`.
- It has credit-graph proximity to artists already in the user's collection (side projects, sideman gigs, producer credits, label-mates).
- LLM editorial enrichment surfaces concrete reasons it matters (specific influence, scene context, hidden-classic status).

## Architecture

### Package layout

```
discogs/
├── pyproject.toml
├── README.md
├── docs/superpowers/specs/
├── src/discogs/
│   ├── __init__.py
│   ├── api/                       # thin domain wrapper around python3-discogs-client
│   │   ├── __init__.py
│   │   ├── client.py              # auth, rate-limit handling, retries, pagination
│   │   ├── collection.py
│   │   ├── wantlist.py            # fetch + add + remove
│   │   ├── releases.py            # release / master / artist / label lookups
│   │   └── search.py              # database search wrapper
│   ├── cache/
│   │   ├── __init__.py
│   │   ├── schema.sql
│   │   └── store.py               # SQLite read/write
│   ├── sync/
│   │   ├── __init__.py
│   │   └── syncer.py              # full + incremental collection/wantlist sync
│   ├── recommend/
│   │   ├── __init__.py
│   │   ├── graph.py               # credit-graph expansion
│   │   ├── scoring.py             # obscurity + depth scoring
│   │   ├── enrich.py              # Claude editorial layer
│   │   └── pipeline.py            # candidates → score → enrich → top-N
│   ├── wantlist_writer.py         # apply recommendations, undo support
│   ├── config.py                  # load ~/.discogs/config.toml + env overrides
│   ├── models.py                  # pydantic domain types
│   └── cli/
│       ├── __init__.py
│       ├── __main__.py            # `python -m discogs ...`
│       └── commands/              # one module per verb
└── tests/
    ├── unit/
    └── integration/               # vcrpy cassettes
```

### Layering

The CLI is a thin shell over the library. `api/`, `cache/`, `sync/`, `recommend/`, and `wantlist_writer` know nothing about Click or terminal output. Anything that wants to use the framework — a notebook, a future web app, a scheduled GitHub Action — imports the library directly.

### Storage

A single SQLite database at `~/.discogs/cache.db`. Schema (concrete shape, not exhaustive):

- `releases` — Discogs release id, master id, title, year, country, formats, styles, genres, community stats (have/want/avg rating/rating count), `fetched_at`. Source of truth for release-level data.
- `masters` — master id, title, year, main release id, `fetched_at`.
- `artists` — artist id, name, profile, `fetched_at`.
- `labels` — label id, name, parent label, releases count, `fetched_at`.
- `release_credits` — junction: release_id, artist_id, role (e.g., "Producer", "Bass", "Engineer"). Drives the credit graph.
- `release_labels` — junction: release_id, label_id, catalog number.
- `release_styles` / `release_genres` — junction tables for style/genre fingerprinting.
- `collection_items` — what the user owns (release_id, folder_id, instance_id, date_added).
- `wantlist_items` — what the user already wants (release_id, date_added, notes).
- `recommendation_history` — every release we've ever recommended (release_id, run_id, score, applied_to_wantlist bool, applied_at, removed_at, removed_reason). Drives "never re-suggest," audit trail, and undo. No separate audit table — this single table is the source of truth.
- `runs` — id (UUID), display_id (e.g. `2026-05-08-1830`), started_at, finished_at, args, summary stats. `display_id` is the human-friendly handle accepted by `discogs apply` and `discogs undo`.

Cache TTLs (configurable):
- Collection / wantlist: 24h.
- Release / master / artist / label: 30 days (these are slow-moving).
- A `discogs sync --force` flag invalidates everything for that scope.

### Config & secrets

- `~/.discogs/config.toml` (chmod 600) holds the Discogs personal access token, the Anthropic API key, and tunable knobs (score weights, max recs per run, LLM model, cache TTLs, daily API budget).
- `DISCOGS_TOKEN` and `ANTHROPIC_API_KEY` env vars override the file.
- The CLI never prints secrets; logs redact them.

### Dependencies

| Package | Purpose |
|---|---|
| `python3-discogs-client` | Discogs API client (rate-limit headers, OAuth/token, pagination) |
| `anthropic` | Claude SDK for editorial enrichment |
| `click` | CLI framework |
| `rich` | Pretty terminal output, tables, progress bars |
| `pydantic` | Typed domain models |
| `tomli` (or stdlib `tomllib`) | Config parsing |
| `pytest` + `pytest-cov` | Unit/integration tests |
| `vcrpy` | Record/replay HTTP cassettes for integration tests |
| `ruff` + `mypy` | Lint + type checking |

## Recommendation engine: credit-graph expansion

The core algorithm. Five stages. Each stage logs progress and is independently testable.

### Stage 1 — Seed selection

From the cached collection + wantlist, pick a "seed set" of artists. Default seeds: every artist who appears on ≥ 2 releases the user owns or wants.

Each seed gets a `seed_weight` used downstream in `connection_score`. The weight inversely correlates with how popular the artist is on Discogs (`1 / log(artist_release_count + 10)`), so collecting an obscure artist is a stronger signal of taste than collecting a globally popular one. Weights are normalized to `[0.1, 1.0]` across the seed set.

Tunable: `--seed-mode {collection, wantlist, both}`, `--min-occurrences N`.

### Stage 2 — Candidate generation (graph walk)

For each seed artist:

1. Fetch their full release credits (`/artists/{id}/releases`, paginated). Cache.
2. For every release in their credits, expand one extra hop: fetch the release's full credit list, harvest *other* artists credited (sidemen, producers, engineers).
3. Add those one-hop artists to a "neighbor pool" with edge weights (Producer/Performer credits get higher weights than Engineer/Mastering).
4. For each neighbor artist (capped at top-K by edge weight per seed), fetch *their* releases.

The result is a candidate set of release ids. Many will already be in the user's collection or wantlist — those are filtered out before scoring. Entries already in `recommendation_history` (regardless of whether they were applied) are also filtered out unless `--allow-rerecommend` is passed.

Bounded expansion to control API cost: configurable caps per seed (`max_neighbors_per_seed`, default 5) and per neighbor (`max_releases_per_neighbor`, default 25). The graph walk is breadth-first with a hard total-API-call budget (default 800 calls per run, ~13 minutes of rate-limited requests).

### Stage 3 — Scoring

Each candidate gets a score in `[0, 1]`, a weighted sum of normalized sub-scores:

Releases already owned or wanted, and releases already present in `recommendation_history`, are excluded *before* scoring — they never appear as candidates. The score table below covers ranking among the survivors.

| Sub-score | Weight | Source |
|---|---:|---|
| `connection_score` | 0.30 | Sum over seed artists of `seed_weight × edge_weight` for that seed→candidate path, normalized to `[0,1]` against the max in the candidate set |
| `rarity_score` | 0.20 | `1 - log(have_count + 1) / log(max_have_in_candidates + 1)` |
| `demand_ratio_score` | 0.10 | `min(1, (want / max(have, 1)) / 2)` |
| `label_obscurity_score` | 0.05 | `1 - log(label_release_count + 1) / log(max_label_count + 1)` |
| `style_niche_score` | 0.05 | `1 - (frequency of release's style set in user's collection)`. Style sets the user has never collected score 1.0; styles dominating the collection score near 0. Pure local computation — no extra API calls. |
| `rating_score` | 0.15 | `(avg_rating - 3.0) / 2.0` clipped to `[0, 1]`, gated by `rating_count ≥ 5` (otherwise 0) |
| `format_score` | 0.10 | 1.0 for Album/EP; 0.3 for Compilation; 0.0 for Single/DJ-Mix |
| `recency_match_score` | 0.05 | Cosine similarity of release's decade-bucket vs. the user's collection's decade distribution |

Weights sum to 1.00 and live in `config.toml` so the user can retune without code changes.

### Stage 4 — LLM enrichment

For the top `2 × N` candidates by raw score (default N = 25, so 50 candidates), call Claude with a structured prompt:

> System: You are a music historian writing concise, factual notes about specific Discogs releases. Use what you know about the artist, the label, and the era. Do not speculate. If you don't know, say so.
>
> User: Given these releases (artist, title, year, label, styles, key credits), for each one write a 2–3 sentence note explaining why it might matter to a collector. Highlight: notable personnel, scene/era context, what makes it distinctive. Output JSON: `[{release_id, note, confidence: "high" | "medium" | "low"}]`.

Confidence "low" notes get a small score penalty (-0.03); "high" gets a small boost (+0.05). The note is stored alongside the candidate.

Model: `claude-haiku-4-5-20251001` for cost (notes are short and factual). User can override to `claude-sonnet-4-6` in config.

LLM is opt-out via `--no-enrich` flag — the engine still produces ranked picks without notes.

### Stage 5 — Final selection

Sort by enriched score. Cap to `max_recs_per_run` (default 25). Diversity guard: no more than 3 recs from the same artist per run. Write to `recommendation_history`. Optionally apply to wantlist (next section).

## CLI surface & data flow

`python -m discogs <verb> [args]` (also installable as `discogs` console script).

| Command | Purpose |
|---|---|
| `discogs auth set` | Save the personal access token to `~/.discogs/config.toml`. Prompts for token, never echoes it. |
| `discogs sync` | Full sync of collection + wantlist into the cache. `--force` invalidates TTLs. `--scope {collection,wantlist,both}` to limit. |
| `discogs recommend` | Run the full pipeline: candidate gen → score → enrich → top-N. **Default is dry-run** — writes the digest, does NOT touch wantlist. Output: `digests/YYYY-MM-DD-HHMM-recommendations.md` plus a Rich-rendered table in the terminal. |
| `discogs recommend --apply` | Same, but also pushes the top-N to the user's Discogs wantlist after the digest is written. |
| `discogs apply <run-display-id>` | Apply a previous run's recommendations to the wantlist (lets you review the digest first, then commit). `<run-display-id>` is the timestamp form, e.g. `2026-05-08-1830`. |
| `discogs undo-last-batch` | Removes the most recent batch of pushed recommendations from the user's wantlist. Reads from `recommendation_history`. |
| `discogs undo <run-display-id>` | Same, but for a specific run. |
| `discogs status` | Print: token scope, cache size, last sync, last run, API quota used today. |
| `discogs config show` / `discogs config set <key> <value>` | Inspect/edit tunable knobs (weights, caps, model). |
| `discogs cache reset` | Wipe the local cache and rebuild on next sync. Used after schema migration or for debugging. Confirms interactively unless `--yes`. |

### Default flow for the user

```
$ discogs auth set                        # one-time
$ discogs sync                             # ~1 minute on first run; <10s after
$ discogs recommend                        # produces digest, no writes
$ less digests/2026-05-08-1830-...md       # human review
$ discogs apply 2026-05-08-1830            # commits to wantlist
$ discogs undo-last-batch                  # if you regret it
```

For the user's stated preference of "automatic," a single command does everything:

```
$ discogs recommend --apply
```

This is the headline command. It still writes the digest first (so there's always an audit trail), then pushes.

## Safety mechanisms

These exist because "automatic write to wantlist" is the chosen default and we need to make mistakes cheap to reverse.

1. **Hard cap per run.** `max_recs_per_run` defaults to 25 — a misfiring algorithm can pollute at most 25 wantlist entries per run.
2. **Dedup memory.** `recommendation_history` ensures no release is ever recommended twice, even across many runs and even if the user later removes it from their wantlist (unless `--allow-rerecommend`).
3. **Undo.** Every `--apply` writes a `wantlist_audit` row per push. `discogs undo-last-batch` is single-command rollback.
4. **Dry-run by default for `recommend`.** Only `--apply` writes. The auto-flow exists but is opt-in per invocation.
5. **Diversity guard.** No artist contributes > 3 picks in a run.
6. **Daily API budget.** Configurable cap (default 1500 calls/day across all commands). Exceeded → graceful exit with status, not an error.
7. **Confirmation on first apply.** First-ever `--apply` requires interactive `y/N`. Subsequent applies skip the prompt; bypass with `--yes` for scripted runs.

## Error handling, rate limits, auth

### Rate limiting

The Discogs API returns rate-limit headers on every response. The `api/client.py` wrapper:

- Tracks `X-Discogs-Ratelimit-Remaining`. When it hits ≤ 5, sleeps until the next minute boundary.
- Exponential backoff on `429` and `5xx` (max 5 retries, 30s ceiling).
- Required `User-Agent: discogs-recommender/<version> (+https://github.com/<user>/discogs)`.
- Never spawns parallel API calls — strict serial dispatch through one client.

### Auth

Personal access token only in v1. OAuth left for future work (only needed if we ever ship this for multiple users).

### Errors

- **Network / 5xx** → retry with backoff; if exhausted, fail the run with a clear message and no partial wantlist writes.
- **Auth failures** → halt immediately, point the user at `discogs auth set`.
- **Cache corruption** (rare; mainly schema migration) → `discogs cache reset` rebuilds; auto-detected on incompatible schema version.
- **LLM enrichment failure** → degrade gracefully; the run continues without notes and warns in the digest.
- **Wantlist write partial failure** → if 3 of 25 pushes fail, the 22 successes are recorded in `wantlist_audit`, the 3 failures are surfaced in the digest with retry hints. Run still succeeds overall.

We do not silently swallow exceptions. Every error path either retries deterministically, halts loudly, or degrades with an explicit warning that lands in both the terminal output and the digest file.

## Testing strategy

- **Unit tests** for: scoring functions (deterministic given mocked inputs), graph walk pruning, dedup logic, cache TTL math, config parsing/overrides, undo logic.
- **Integration tests** with `vcrpy` cassettes recorded against a real Discogs account: the full `sync` → `recommend` → `apply` → `undo` flow. Cassettes are committed; secrets scrubbed by `vcrpy` filters before commit.
- **No live API in CI.** All CI runs replay cassettes only.
- **LLM tests** stub the Anthropic client. We don't test Claude itself; we test that we call it correctly, parse its response correctly, and degrade correctly when it fails.
- **Coverage target:** 85% on `recommend/`, `cache/`, `wantlist_writer.py`. Lower bar elsewhere.

## Logging & observability

- Structured logs to `~/.discogs/logs/YYYY-MM-DD.jsonl` (one event per line).
- Each run gets a `run_id` UUID; all events for a run share it.
- `discogs status` summarises today's runs, API calls, errors.

## Open questions / future work

- **OAuth flow** — only worth doing if this becomes multi-user.
- **Scheduled runs** — once the CLI is solid, a thin GitHub Actions workflow can run `discogs recommend --apply` weekly. Out of v1 scope.
- **Cross-source enrichment** — MusicBrainz / RYM / Bandcamp could enrich the credit graph or provide independent obscurity signals. Postponed; the credit-graph from Discogs alone is a strong v1.
- **Web UI** — the library is shaped for it. Build later if the CLI proves the value.
- **Style histogram refresh** — currently a periodic background sample. If it goes stale, recommendations skew. Decide refresh cadence after first month of real use.

## Build sequence (preview, full plan to follow)

1. Project scaffold + config + auth + `discogs status`.
2. API client wrapper + rate limiting + cache schema.
3. `discogs sync` (collection + wantlist).
4. Graph walk (Stage 1–2) without scoring; just dump candidates.
5. Scoring (Stage 3).
6. LLM enrichment (Stage 4).
7. Digest generation + `discogs recommend`.
8. `discogs apply` + `undo-last-batch`.
9. Tests, polish, docs.

The implementation plan (forthcoming, via `superpowers:writing-plans`) breaks each of these into reviewable subtasks.
