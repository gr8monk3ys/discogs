from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.models import Credit, Format, Release
from discogs.recommend.graph import GraphPath, walk_credit_graph
from discogs.recommend.seeds import SeedArtist


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[CacheStore, DiscogsClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db", daily_api_budget=10000,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    yield store, client
    store.close()


def _stub_release(release_id: int, credits: list[Credit]) -> Release:
    return Release(
        id=release_id, master_id=None, title=f"r{release_id}", year=1970,
        country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Jazz"], genres=["Jazz"],
        community_have=100, community_want=200,
        community_avg_rating=4.0, community_rating_count=10,
        fetched_at=datetime.now(UTC),
    )


def test_seed_artist_default_kind_is_direct() -> None:
    s = SeedArtist(artist_id=1, weight=0.5, sources=("collection",))
    assert s.seed_kind == "direct"


def test_seed_artist_can_be_influence() -> None:
    s = SeedArtist(artist_id=1, weight=0.5, sources=(), seed_kind="influence")
    assert s.seed_kind == "influence"


def test_graph_path_carries_seed_kind(setup) -> None:
    store, client = setup
    seeds = [SeedArtist(artist_id=7, weight=0.5, sources=("collection",), seed_kind="influence")]

    with patch("discogs.recommend.graph.fetch_artist_releases") as far, \
         patch("discogs.recommend.graph.fetch_release") as fr:
        far.return_value = [101]
        fr.return_value = _stub_release(101, credits=[])
        store.replace_release_credits(101, [])
        paths = walk_credit_graph(client, store, seeds, budget=10)

    assert 101 in paths
    assert paths[101][0].seed_kind == "influence"
