---
status: accepted
date: 2026-11-22
---

# One local SQLite cache, upgraded by versioned migrations

Discogs allows few API calls per minute, so every command other than sync reads a local SQLite Cache and spends no Budget. The Cache also holds Runs and recommendation history, which exist nowhere else.

A fresh database is created straight from `schema.sql` and stamped at `CURRENT_SCHEMA_VERSION`. An existing database is upgraded by applying `migrations/v{N}.sql` for each version between its stored version and the current one, because `CREATE TABLE IF NOT EXISTS` in `schema.sql` cannot add a column to a table that already exists.

## Consequences

- A schema change means editing `schema.sql`, adding a `migrations/v{N}.sql`, and bumping `CURRENT_SCHEMA_VERSION` together.
- Run history is local to one machine; losing the file loses it, while synced Discogs data can be re-fetched.
