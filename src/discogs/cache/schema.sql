-- Schema version 2
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY,
    master_id INTEGER,
    title TEXT NOT NULL,
    year INTEGER NOT NULL,
    country TEXT,
    formats_json TEXT NOT NULL,
    artists_json TEXT NOT NULL DEFAULT '[]',
    community_have INTEGER NOT NULL,
    community_want INTEGER NOT NULL,
    community_avg_rating REAL NOT NULL,
    community_rating_count INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_releases_master ON releases(master_id);
CREATE INDEX IF NOT EXISTS idx_releases_have ON releases(community_have);

CREATE TABLE IF NOT EXISTS masters (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    year INTEGER NOT NULL,
    main_release_id INTEGER,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    profile TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS labels (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    parent_label TEXT,
    releases_count INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS release_credits (
    release_id INTEGER NOT NULL,
    artist_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY (release_id, artist_id, role)
);
CREATE INDEX IF NOT EXISTS idx_credits_artist ON release_credits(artist_id);

CREATE TABLE IF NOT EXISTS release_labels (
    release_id INTEGER NOT NULL,
    label_id INTEGER NOT NULL,
    catalog_number TEXT,
    PRIMARY KEY (release_id, label_id, catalog_number)
);

CREATE TABLE IF NOT EXISTS release_styles (
    release_id INTEGER NOT NULL,
    style TEXT NOT NULL,
    PRIMARY KEY (release_id, style)
);

CREATE TABLE IF NOT EXISTS release_genres (
    release_id INTEGER NOT NULL,
    genre TEXT NOT NULL,
    PRIMARY KEY (release_id, genre)
);

CREATE TABLE IF NOT EXISTS collection_items (
    release_id INTEGER NOT NULL,
    folder_id INTEGER NOT NULL,
    instance_id INTEGER NOT NULL,
    date_added TEXT NOT NULL,
    PRIMARY KEY (instance_id)
);
CREATE INDEX IF NOT EXISTS idx_collection_release ON collection_items(release_id);

CREATE TABLE IF NOT EXISTS wantlist_items (
    release_id INTEGER PRIMARY KEY,
    date_added TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS artist_influences (
    source_artist_id INTEGER NOT NULL,
    influence_artist_id INTEGER NOT NULL,
    confidence TEXT NOT NULL CHECK(confidence IN ('high','medium','low')),
    source TEXT NOT NULL DEFAULT 'claude',
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source_artist_id, influence_artist_id, source)
);

CREATE TABLE IF NOT EXISTS artist_top_releases (
    artist_id INTEGER NOT NULL,
    release_id INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (artist_id, release_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    display_id TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    args_json TEXT,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS recommendation_history (
    release_id INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    score REAL NOT NULL,
    subscores_json TEXT,
    applied_to_wantlist INTEGER NOT NULL DEFAULT 0,
    applied_at TEXT,
    removed_at TEXT,
    removed_reason TEXT,
    PRIMARY KEY (release_id, run_id),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS idx_history_run ON recommendation_history(run_id);

CREATE TABLE IF NOT EXISTS _sync_metadata (
    scope TEXT PRIMARY KEY,
    last_sync_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS _api_call_counts (
    day TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS _llm_call_counts (
    day TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0
);

-- Spotify artists imported from the music-library interchange file. The
-- resolution to a Discogs artist is cached permanently: it costs an API
-- call to learn and never changes, so a re-import is free for anything
-- already resolved. A NULL discogs_artist_id records "we looked and could
-- not tell", which is deliberately different from "not looked at yet".
CREATE TABLE IF NOT EXISTS spotify_artists (
    spotify_artist_id TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    discogs_artist_id INTEGER,
    liked_track_count INTEGER NOT NULL,
    match_method      TEXT NOT NULL,
    resolved_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spotify_artists_discogs
    ON spotify_artists(discogs_artist_id);
