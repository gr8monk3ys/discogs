-- v3: seed recommendations from the Spotify library rather than from the
-- 101 records in the local collection and wantlist. One row per distinct
-- Spotify artist, with its resolved Discogs id cached permanently.
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
