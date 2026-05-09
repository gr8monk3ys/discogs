from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.llm import LLMClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.models import Format, Release
from discogs.recommend.enrich import enrich_candidates
from discogs.recommend.graph import GraphPath
from discogs.recommend.scoring import ScoredCandidate


@pytest.fixture
def llm(tmp_path: Path) -> Iterator[LLMClient]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db",
        anthropic_api_key="sk-test", daily_llm_budget=10,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    yield LLMClient(cfg, store, upstream_factory=lambda **kw: MagicMock())
    store.close()


def _candidate(rid: int, score: float = 0.7) -> ScoredCandidate:
    return ScoredCandidate(
        release_id=rid, score=score,
        subscores={"connection": 1.0, "influence_chain": 0.0,
                   "rarity": 0.5, "demand_ratio": 0.4, "label_obscurity": 0.4,
                   "style_niche": 0.5, "rating": 0.7, "format": 1.0,
                   "recency_match": 0.5},
        paths=(GraphPath(seed_artist_id=1, seed_weight=0.9,
                         edge_chain=((1, rid, "direct"),), edge_weight=1.0,
                         seed_kind="direct"),),
    )


def _release_lookup(rid: int) -> Release:
    return Release(
        id=rid, master_id=None, title=f"r{rid}", year=1970, country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Jazz"], genres=["Jazz"],
        community_have=100, community_want=200,
        community_avg_rating=4.0, community_rating_count=50,
        fetched_at=datetime.now(UTC),
    )


def test_enrich_attaches_notes(llm: LLMClient) -> None:
    cands = [_candidate(1, score=0.7), _candidate(2, score=0.6)]
    releases = {1: _release_lookup(1), 2: _release_lookup(2)}
    fake_response = (
        '{"items":['
        '{"release_id":1,"note":"important early-period work","confidence":"high"},'
        '{"release_id":2,"note":"interesting curio","confidence":"medium"}'
        ']}'
    )
    llm._upstream.messages.create.return_value = MagicMock(
        content=[MagicMock(text=fake_response)],
        usage=MagicMock(input_tokens=0, output_tokens=0,
                        cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )

    enriched = enrich_candidates(llm, cands, releases)
    e1 = next(c for c in enriched if c.release_id == 1)
    e2 = next(c for c in enriched if c.release_id == 2)
    assert e1.enrichment is not None and e1.enrichment.confidence == "high"
    assert e2.enrichment is not None and e2.enrichment.confidence == "medium"


def test_enrich_applies_score_boost_and_penalty(llm: LLMClient) -> None:
    cands = [_candidate(1, score=0.5), _candidate(2, score=0.5)]
    releases = {1: _release_lookup(1), 2: _release_lookup(2)}
    fake_response = (
        '{"items":['
        '{"release_id":1,"note":"hidden classic","confidence":"high"},'
        '{"release_id":2,"note":"unsure","confidence":"low"}'
        ']}'
    )
    llm._upstream.messages.create.return_value = MagicMock(
        content=[MagicMock(text=fake_response)],
        usage=MagicMock(input_tokens=0, output_tokens=0,
                        cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )

    enriched = enrich_candidates(llm, cands, releases)
    e1 = next(c for c in enriched if c.release_id == 1)
    e2 = next(c for c in enriched if c.release_id == 2)
    assert e1.score == pytest.approx(0.55, abs=0.001)   # +0.05
    assert e2.score == pytest.approx(0.47, abs=0.001)   # -0.03


def test_enrich_returns_originals_on_parse_failure(llm: LLMClient) -> None:
    cands = [_candidate(1)]
    releases = {1: _release_lookup(1)}
    llm._upstream.messages.create.return_value = MagicMock(
        content=[MagicMock(text="not json")],
        usage=MagicMock(input_tokens=0, output_tokens=0,
                        cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )
    enriched = enrich_candidates(llm, cands, releases)
    assert enriched[0].enrichment is None
    assert enriched[0].score == 0.7  # unchanged


def test_enrich_batches_into_chunks_of_10(llm: LLMClient) -> None:
    cands = [_candidate(i, score=0.5) for i in range(25)]
    releases = {i: _release_lookup(i) for i in range(25)}
    llm._upstream.messages.create.return_value = MagicMock(
        content=[MagicMock(text='{"items":[]}')],
        usage=MagicMock(input_tokens=0, output_tokens=0,
                        cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )

    enrich_candidates(llm, cands, releases)
    # 25 candidates → ceil(25/10) = 3 batches
    assert llm._upstream.messages.create.call_count == 3
