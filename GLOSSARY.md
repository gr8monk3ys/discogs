# Discogs

A CLI and library that syncs a Discogs collection and wantlist into a local cache, recommends releases from the credit graph, and writes accepted picks back to the wantlist.

## Language

### Discogs entities

**Release**:
One specific pressing on Discogs, with its formats, artists, styles, genres and community statistics. The unit that is owned, wanted and recommended.
_Avoid_: album, record, record (when a pressing is meant)

**Master**:
The Discogs grouping of every pressing of one album. A Spotify album resolves to a Master, not a Release.
_Avoid_: album

**Credit**:
One artist's role on one Release. The role's bracketed qualifier is dropped to give its normalised role.
_Avoid_: contribution, appearance

**Collection item**:
A Release the user owns, in a folder, as one instance.
_Avoid_: owned record

**Wantlist item**:
A Release the user has said they want. The only thing `discogs` ever writes back to Discogs.
_Avoid_: wish, favourite

### Local state

**Cache**:
The SQLite database (`~/.discogs/cache.db`) holding synced Discogs data, imported Spotify artists, Runs and recommendation history. Every command except sync reads it without network calls.
_Avoid_: store (that is the class), database

**Budget**:
The cap on Discogs API calls (and, separately, the daily cap on Claude calls) a command may spend. Exceeding it stops the work rather than failing it.
_Avoid_: quota, rate limit

### Recommending

**Seed**:
An artist the recommender starts from. Direct seeds come from the user's own library; influence seeds are artists Claude names as influences on a direct seed.
_Avoid_: root, source artist

**Mode**:
Which part of the user's library supplies Seeds: collection, wantlist, both, spotify or all. Sync commands call the same idea a scope.

**Graph path**:
One traced route from a Seed to a candidate Release through Credits, weighted by the roles along it.
_Avoid_: edge chain (that is a field of it), link

**Sub-score**:
One of the nine named components (connection, influence_chain, rarity, demand_ratio, label_obscurity, style_niche, rating, format, recency_match) that are weighted into a candidate's total score.
_Avoid_: feature, signal

**Enrichment**:
Claude's short editorial note on a candidate with a high, medium or low confidence; high nudges the score up, low nudges it down.
_Avoid_: annotation, explanation

**Run**:
One execution of the recommender, identified for humans by its display id (`YYYY-MM-DD-HHMMSS`, suffixed on collision). Everything a Run picked is kept as recommendation history.
_Avoid_: session, batch (a batch is one apply of a Run's picks)

**Apply** / **Undo**:
Apply pushes a Run's picks to the wantlist; Undo removes them again. Recommending is dry by default and never applies on its own.
_Avoid_: commit, publish

### Spotify and export

**Candidate** (Spotify):
A Spotify favourite that is neither owned nor already wanted, proposed for the wantlist when its affinity and liked-track count clear the configured minimums. Anything matching zero or several Masters is unresolved and never guessed.

**Prune**:
Removing a Wantlist item whose Master is now in the collection.

**Export**:
`discogs.json` in the shared music directory, built from the Cache only. Its `schema` string (`discogs/1`) is the contract consumers assert on.

### Measuring quality

**Held-out eval**:
Hiding a random part of the wantlist, recommending over the rest, and reporting recall@k, MRR and reachable. Reachable separates a graph-coverage problem from a scoring problem. It runs on a temp copy of the Cache.
