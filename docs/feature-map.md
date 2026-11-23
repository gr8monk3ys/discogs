# Feature map

Every command `discogs` exposes (`uv run discogs <command>`), in the user's
words. Checks assume a fresh `HOME` with no cache, so they run offline; the
"with data" half of each needs a real token (`discogs auth set`) and a synced
cache.

| Feature | Where | How to reach it | How to check it works |
|---|---|---|---|
| Save my Discogs login | `src/discogs/cli/commands/auth.py` | `discogs auth set` (prompts for token and username) | Prints `Saved config to ~/.discogs/config.toml`; the file exists with mode 600 |
| See config, cache and today's API budget | `commands/status.py` | `discogs status` | Table shows username, cache path, last syncs ("never" on a fresh cache), `0 / 1500` calls |
| Pull my collection and wantlist into the local cache | `commands/sync_cmd.py` | `discogs sync [--scope collection\|wantlist\|both] [--force]` | Needs a real token and network. `discogs status` then shows a last-sync time |
| See the eras, styles and labels in my library | `commands/stats_cmd.py` | `discogs stats [--scope ...] [--top N]` | Empty cache prints "No releases in both. Run `discogs sync` first."; after a sync, three distributions print |
| Get recommendations as a markdown digest | `commands/recommend.py` | `discogs recommend [--max-recs N] [--scope ...] [--no-influences] [--no-enrich] [--allow-rerecommend]` | Prints `Wrote digest: ~/.discogs/digests/<run_id>-recommendations.md` and a `run_id` line; the file exists |
| Push recommendations to my wantlist as I recommend | `commands/recommend.py` | `discogs recommend --apply [--yes]` | Digest is written, then picks appear on the Discogs wantlist (needs token) |
| Push an earlier run's picks to my wantlist | `commands/apply_cmd.py` | `discogs apply RUN_DISPLAY_ID [--yes]` | Unknown id prints `Error: No run found for display id '<id>'.` and exits 1; a real id adds the picks |
| Undo one run | `commands/undo_cmd.py` | `discogs undo RUN_DISPLAY_ID [--yes]` | Unknown id exits 1 with `No run found`; a real applied run's picks leave the wantlist |
| Undo the last applied batch | `commands/undo_cmd.py` | `discogs undo-last-batch [--yes]` | With nothing applied: `No applied runs in history — nothing to undo.` (exit 1) |
| Ask why a release was recommended | `commands/explain_cmd.py` | `discogs explain RELEASE_ID` | Never-recommended id prints `Release <id> has never been recommended.`; otherwise sub-scores and run history |
| Compare two runs | `commands/diff_cmd.py` | `discogs diff RUN_A RUN_B` | Unknown id prints `Error: No run with display id '<id>'.` (exit 1); real ids list added and dropped picks |
| Score how well the recommender predicts my wantlist | `commands/eval_cmd.py` | `discogs eval [--holdout N] [--k N] [--seed N] [--offline]` | Wantlist smaller than the holdout prints `wantlist has 0 items; need more than holdout=18` (exit 1); otherwise recall@k |
| Use my Spotify library as recommendation seeds | `commands/import_spotify_cmd.py` | `discogs import-spotify [--file F] [--limit N] [--refresh]` | Missing file prints `No interchange file at <path>. Run spotifyforge export library ...` (exit 1) |
| Plan wantlist adds and prunes from Spotify | `commands/sync_spotify_cmd.py` | `discogs sync-spotify [--file F] [--limit N] [--apply] [--yes]` | Same missing-file error; with a file it writes a digest, and changes only with `--apply` |
| Export my cache for the other media tools | `commands/export_cmd.py` | `discogs export [--out FILE]` | Prints `Exported N collection and M wantlist item(s) to $MUSIC_DIR/discogs.json`; the JSON file exists |
