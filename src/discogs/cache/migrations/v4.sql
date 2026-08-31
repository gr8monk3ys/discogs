-- v4: remember the main credited artists on each release, so the collection
-- and wantlist can be matched against other libraries (Spotify) by name
-- without an API call per release.
ALTER TABLE releases ADD COLUMN artists_json TEXT NOT NULL DEFAULT '[]';
