from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.models import CollectionItem, Credit, Format, Release
from discogs.recommend.graph import GraphPath
from discogs.recommend.pipeline import RunResult, run_recommend
from discogs.recommend.scoring import DEFAULT_WEIGHTS, ScoredCandidate
from discogs.recommend.seeds import SeedArtist


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[Config, CacheStore, DiscogsClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db", daily_api_budget=10000,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    yield cfg, store, client
    store.close()


def _release(rid: int, year: int = 1970) -> Release:
    return Release(
        id=rid, master_id=None, title=f"r{rid}", year=year, country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Jazz"], genres=["Jazz"],
        community_have=1000, community_want=500,
        community_avg_rating=4.0, community_rating_count=20,
        fetched_at=datetime.now(UTC),
    )


def _scored(rid: int, score: float, primary_artist_id: int) -> ScoredCandidate:
    p = GraphPath(
        seed_artist_id=primary_artist_id,
        seed_weight=1.0,
        edge_chain=((primary_artist_id, rid, "direct"),),
        edge_weight=1.0,
    )
    return ScoredCandidate(release_id=rid, score=score, subscores={"connection": 1.0}, paths=(p,))


def test_pipeline_writes_history_rows(setup) -> None:
    cfg, store, client = setup

    with patch("discogs.recommend.pipeline.select_seeds") as ss, \
         patch("discogs.recommend.pipeline.walk_credit_graph") as wg, \
         patch("discogs.recommend.pipeline.score_candidates") as sc:
        ss.return_value = [SeedArtist(artist_id=1, weight=1.0, sources=("collection",))]
        wg.return_value = {10: [GraphPath(1, 1.0, ((1, 10, "direct"),), 1.0)]}
        sc.return_value = [_scored(10, 0.7, primary_artist_id=1)]

        with patch("discogs.recommend.pipeline._load_releases") as lr, \
             patch("discogs.recommend.pipeline._load_label_counts") as ll:
            lr.return_value = {10: _release(10)}
            ll.return_value = {10: 50}
            result = run_recommend(client, store, cfg, max_recs=5)

    assert isinstance(result, RunResult)
    assert result.picks[0].release_id == 10
    assert result.run_display_id  # YYYY-MM-DD-HHMMSS
    assert store.previously_recommended_release_ids() == {10}


def test_diversity_guard_caps_per_seed_to_three(setup) -> None:
    cfg, store, client = setup

    five_picks = [_scored(rid=100 + i, score=0.9 - i * 0.01, primary_artist_id=42) for i in range(5)]
    one_other = _scored(rid=999, score=0.5, primary_artist_id=7)

    with patch("discogs.recommend.pipeline.select_seeds") as ss, \
         patch("discogs.recommend.pipeline.walk_credit_graph") as wg, \
         patch("discogs.recommend.pipeline.score_candidates") as sc, \
         patch("discogs.recommend.pipeline._load_releases") as lr, \
         patch("discogs.recommend.pipeline._load_label_counts") as ll:
        ss.return_value = [
            SeedArtist(artist_id=42, weight=1.0, sources=("collection",)),
            SeedArtist(artist_id=7, weight=0.8, sources=("collection",)),
        ]
        wg.return_value = {p.release_id: list(p.paths) for p in five_picks + [one_other]}
        sc.return_value = five_picks + [one_other]
        lr.return_value = {p.release_id: _release(p.release_id) for p in five_picks + [one_other]}
        ll.return_value = {p.release_id: 50 for p in five_picks + [one_other]}

        result = run_recommend(client, store, cfg, max_recs=10)

    primary_count = sum(1 for p in result.picks if p.paths[0].seed_artist_id == 42)
    assert primary_count == 3   # diversity cap
    assert any(p.paths[0].seed_artist_id == 7 for p in result.picks)


def test_max_recs_limits_picks(setup) -> None:
    cfg, store, client = setup
    candidates = [_scored(rid=i, score=1.0 - i * 0.01, primary_artist_id=i) for i in range(50)]

    with patch("discogs.recommend.pipeline.select_seeds") as ss, \
         patch("discogs.recommend.pipeline.walk_credit_graph") as wg, \
         patch("discogs.recommend.pipeline.score_candidates") as sc, \
         patch("discogs.recommend.pipeline._load_releases") as lr, \
         patch("discogs.recommend.pipeline._load_label_counts") as ll:
        ss.return_value = [SeedArtist(artist_id=i, weight=1.0, sources=("collection",)) for i in range(50)]
        wg.return_value = {c.release_id: list(c.paths) for c in candidates}
        sc.return_value = candidates
        lr.return_value = {c.release_id: _release(c.release_id) for c in candidates}
        ll.return_value = {c.release_id: 50 for c in candidates}

        result = run_recommend(client, store, cfg, max_recs=10)

    assert len(result.picks) == 10


def test_no_picks_when_no_seeds(setup) -> None:
    cfg, store, client = setup
    with patch("discogs.recommend.pipeline.select_seeds", return_value=[]):
        result = run_recommend(client, store, cfg, max_recs=10)
    assert result.picks == []
    assert result.candidate_count == 0
