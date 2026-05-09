"""End-to-end sync test against recorded HTTP cassettes.

To (re)record:
    DISCOGS_TOKEN=<your-token> DISCOGS_USERNAME=<you> \\
        VCR_RECORD_MODE=once \\
        python -m pytest tests/integration/test_sync_integration.py

Cassettes are committed; CI replays them, never hits the live API.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import vcr

CASSETTE_DIR = Path(__file__).parent / "cassettes"

my_vcr = vcr.VCR(
    cassette_library_dir=str(CASSETTE_DIR),
    record_mode=os.environ.get("VCR_RECORD_MODE", "none"),
    filter_headers=["Authorization"],
    filter_query_parameters=["token"],
)


@pytest.mark.skipif(
    not (CASSETTE_DIR / "sync_collection.yaml").exists(),
    reason="Cassette not recorded yet. See module docstring for recording instructions.",
)
def test_full_sync_against_cassette(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_dir = tmp_path / ".discogs"
    cfg_dir.mkdir()
    token = os.environ.get("DISCOGS_TOKEN", "redacted-replay-token")
    username = os.environ.get("DISCOGS_USERNAME", "test-user")
    (cfg_dir / "config.toml").write_text(
        f'[discogs]\ntoken = "{token}"\nusername = "{username}"\n'
    )

    from discogs.api.client import DiscogsClient
    from discogs.cache.store import CacheStore, init_db
    from discogs.config import load_config
    from discogs.sync.syncer import Syncer

    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store)
    syncer = Syncer(cfg, store, client)

    with my_vcr.use_cassette("sync_collection.yaml"):
        result = syncer.sync(scope="both", force=True)

    assert result.collection_synced is not None
    assert result.collection_synced >= 0
    assert result.wantlist_synced is not None
    store.close()
