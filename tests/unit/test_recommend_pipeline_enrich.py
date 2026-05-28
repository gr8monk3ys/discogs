from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discogs.api.client import DiscogsClient
from discogs.api.llm import LLMClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.models import Format, Release
from discogs.recommend.graph import GraphPath
from discogs.recommend.pipeline import RecommendParams, run_recommend
from discogs.recommend.scoring import Enrichment, ScoredCandidate
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


def _release(rid: int) -> Release:
    return Release(
        id=rid, master_id=None, title=f"r{rid}", year=1970, country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Jazz"], genres=["Jazz"],
        community_have=1000, community_want=500,
        community_avg_rating=4.0, community_rating_count=20,
        fetched_at=datetime.now(UTC),
    )


def _scored(rid: int, score: float) -> ScoredCandidate:
    return ScoredCandidate(
        release_id=rid, score=score,
        subscores={"connection": 1.0},
        paths=(GraphPath(seed_artist_id=1, seed_weight=1.0,
                         edge_chain=((1, rid, "direct"),), edge_weight=1.0,
                         seed_kind="direct"),),
    )


def test_pipeline_invokes_enrich_by_default(setup) -> None:
    cfg, store, client, llm = setup
    cands = [_scored(rid=i, score=0.5) for i in range(20)]
    seed = SeedArtist(artist_id=1, weight=1.0, sources=("collection",), seed_kind="direct")

    with patch("discogs.recommend.pipeline.select_seeds", return_value=[seed]), \
         patch("discogs.recommend.pipeline.expand_influences", return_value=[]), \
         patch("discogs.recommend.pipeline._prefetch_library_releases", return_value=0), \
         patch("discogs.recommend.pipeline.walk_credit_graph",
               return_value={i: cands[i].paths for i in range(20)}), \
         patch("discogs.recommend.pipeline.score_candidates", return_value=cands), \
         patch("discogs.recommend.pipeline.enrich_candidates",
               return_value=cands) as enrich, \
         patch("discogs.recommend.pipeline._load_releases",
               return_value={i: _release(i) for i in range(20)}), \
         patch("discogs.recommend.pipeline._load_label_counts",
               return_value={i: 50 for i in range(20)}):
        run_recommend(client, store, cfg, RecommendParams(max_recs=5), llm=llm)

    enrich.assert_called_once()


def test_pipeline_skips_enrich_when_disabled(setup) -> None:
    cfg, store, client, llm = setup
    cands = [_scored(rid=i, score=0.5) for i in range(5)]
    seed = SeedArtist(artist_id=1, weight=1.0, sources=("collection",), seed_kind="direct")

    with patch("discogs.recommend.pipeline.select_seeds", return_value=[seed]), \
         patch("discogs.recommend.pipeline.expand_influences", return_value=[]), \
         patch("discogs.recommend.pipeline._prefetch_library_releases", return_value=0), \
         patch("discogs.recommend.pipeline.walk_credit_graph",
               return_value={i: cands[i].paths for i in range(5)}), \
         patch("discogs.recommend.pipeline.score_candidates", return_value=cands), \
         patch("discogs.recommend.pipeline.enrich_candidates") as enrich, \
         patch("discogs.recommend.pipeline._load_releases",
               return_value={i: _release(i) for i in range(5)}), \
         patch("discogs.recommend.pipeline._load_label_counts",
               return_value={i: 50 for i in range(5)}):
        run_recommend(client, store, cfg, RecommendParams(max_recs=5, with_enrichment=False), llm=llm)

    enrich.assert_not_called()


def test_enrichment_resorts_picks(setup) -> None:
    """Enrichment can boost a lower-scored candidate above a higher-scored one."""
    cfg, store, client, llm = setup
    raw = [_scored(rid=1, score=0.6), _scored(rid=2, score=0.5)]
    enriched = [
        ScoredCandidate(release_id=1, score=0.57, subscores=raw[0].subscores,  # 0.6 - 0.03 (low)
                        paths=raw[0].paths,
                        enrichment=Enrichment(note="meh", confidence="low")),
        ScoredCandidate(release_id=2, score=0.60, subscores=raw[1].subscores,  # 0.5 + 0.10 boosted above 1
                        paths=raw[1].paths,
                        enrichment=Enrichment(note="great", confidence="high")),
    ]
    seed = SeedArtist(artist_id=1, weight=1.0, sources=("collection",), seed_kind="direct")

    with patch("discogs.recommend.pipeline.select_seeds", return_value=[seed]), \
         patch("discogs.recommend.pipeline.expand_influences", return_value=[]), \
         patch("discogs.recommend.pipeline._prefetch_library_releases", return_value=0), \
         patch("discogs.recommend.pipeline.walk_credit_graph",
               return_value={i: raw[i-1].paths for i in (1, 2)}), \
         patch("discogs.recommend.pipeline.score_candidates", return_value=raw), \
         patch("discogs.recommend.pipeline.enrich_candidates",
               return_value=enriched), \
         patch("discogs.recommend.pipeline._load_releases",
               return_value={1: _release(1), 2: _release(2)}), \
         patch("discogs.recommend.pipeline._load_label_counts",
               return_value={1: 50, 2: 50}):
        result = run_recommend(client, store, cfg, RecommendParams(max_recs=5), llm=llm)

    assert result.picks[0].release_id == 2  # boosted above 1
