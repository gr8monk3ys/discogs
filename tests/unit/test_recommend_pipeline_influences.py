from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discogs.api.client import DiscogsClient
from discogs.api.llm import LLMClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.models import ArtistInfluence
from discogs.recommend.pipeline import run_recommend
from discogs.recommend.scoring import ScoredCandidate
from discogs.recommend.seeds import SeedArtist


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[Config, CacheStore, DiscogsClient, LLMClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db",
        anthropic_api_key="sk-test", daily_api_budget=10000, daily_llm_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    llm = LLMClient(cfg, store, upstream_factory=lambda **kw: MagicMock())
    yield cfg, store, client, llm
    store.close()


def test_pipeline_invokes_expand_influences_by_default(setup) -> None:
    cfg, store, client, llm = setup

    seed = SeedArtist(artist_id=1, weight=1.0, sources=("collection",), seed_kind="direct")

    with patch("discogs.recommend.pipeline.select_seeds", return_value=[seed]), \
         patch("discogs.recommend.pipeline.expand_influences", return_value=[]) as ei, \
         patch("discogs.recommend.pipeline._prefetch_library_releases", return_value=0), \
         patch("discogs.recommend.pipeline.walk_credit_graph", return_value={}), \
         patch("discogs.recommend.pipeline.score_candidates", return_value=[]), \
         patch("discogs.recommend.pipeline._load_releases", return_value={}), \
         patch("discogs.recommend.pipeline._load_label_counts", return_value={}):
        run_recommend(client, store, cfg, llm=llm, max_recs=5)

    ei.assert_called()


def test_pipeline_skips_influences_when_disabled(setup) -> None:
    cfg, store, client, llm = setup

    seed = SeedArtist(artist_id=1, weight=1.0, sources=("collection",), seed_kind="direct")

    with patch("discogs.recommend.pipeline.select_seeds", return_value=[seed]), \
         patch("discogs.recommend.pipeline.expand_influences") as ei, \
         patch("discogs.recommend.pipeline._prefetch_library_releases", return_value=0), \
         patch("discogs.recommend.pipeline.walk_credit_graph", return_value={}), \
         patch("discogs.recommend.pipeline.score_candidates", return_value=[]), \
         patch("discogs.recommend.pipeline._load_releases", return_value={}), \
         patch("discogs.recommend.pipeline._load_label_counts", return_value={}):
        run_recommend(client, store, cfg, llm=llm, max_recs=5, with_influences=False)

    ei.assert_not_called()


def test_pipeline_adds_influence_seeds_with_decayed_weight(setup) -> None:
    cfg, store, client, llm = setup

    direct_seed = SeedArtist(artist_id=1, weight=1.0, sources=("collection",), seed_kind="direct")
    fake_influence = ArtistInfluence(
        source_artist_id=1, influence_artist_id=99, confidence="high",
        source="claude", fetched_at=datetime.now(UTC),
    )

    captured_seeds: list[list[SeedArtist]] = []

    def capture_walk(client, store, seeds, **kw):
        captured_seeds.append(list(seeds))
        return {}

    with patch("discogs.recommend.pipeline.select_seeds", return_value=[direct_seed]), \
         patch("discogs.recommend.pipeline.expand_influences",
               return_value=[fake_influence]), \
         patch("discogs.recommend.pipeline._prefetch_library_releases", return_value=0), \
         patch("discogs.recommend.pipeline.walk_credit_graph",
               side_effect=capture_walk), \
         patch("discogs.recommend.pipeline.score_candidates", return_value=[]), \
         patch("discogs.recommend.pipeline._load_releases", return_value={}), \
         patch("discogs.recommend.pipeline._load_label_counts", return_value={}):
        run_recommend(client, store, cfg, llm=llm, max_recs=5)

    seeds = captured_seeds[0]
    influence_seeds = [s for s in seeds if s.seed_kind == "influence"]
    assert len(influence_seeds) == 1
    inf = influence_seeds[0]
    assert inf.artist_id == 99
    # high confidence (1.0) * direct_seed weight (1.0) * 0.6 decay = 0.6
    assert abs(inf.weight - 0.6) < 0.01
