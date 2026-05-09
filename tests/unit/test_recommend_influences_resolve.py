from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discogs.api.client import DiscogsClient
from discogs.api.llm import LLMClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.models import ArtistInfluence
from discogs.recommend.influences import (
    InfluenceCandidate,
    expand_influences,
)


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[CacheStore, DiscogsClient, LLMClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db",
        anthropic_api_key="sk-test", daily_llm_budget=10, daily_api_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    discogs_client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    llm = LLMClient(cfg, store, upstream_factory=lambda **kw: MagicMock())
    yield store, discogs_client, llm
    store.close()


def test_expand_uses_cache_when_fresh(setup) -> None:
    store, dc, llm = setup
    fresh = ArtistInfluence(source_artist_id=7, influence_artist_id=99,
                           confidence="high", source="claude",
                           fetched_at=datetime.now(UTC))
    store.replace_artist_influences(source_artist_id=7, edges=[fresh])

    with patch("discogs.recommend.influences.fetch_influences_from_claude") as f, \
         patch("discogs.recommend.influences.resolve_artist_name") as r:
        result = expand_influences(
            dc, store, llm, artist_id=7, artist_name="Test", primary_styles=[],
        )
        f.assert_not_called()
        r.assert_not_called()
    assert {e.influence_artist_id for e in result} == {99}


def test_expand_calls_claude_and_resolves(setup) -> None:
    store, dc, llm = setup
    candidates = [
        InfluenceCandidate(name="John Coltrane", confidence="high", note="lineage"),
        InfluenceCandidate(name="Sun Ra", confidence="medium", note="kinship"),
    ]
    with patch("discogs.recommend.influences.fetch_influences_from_claude",
               return_value=candidates) as f, \
         patch("discogs.recommend.influences.resolve_artist_name",
               side_effect=[(101, "John Coltrane"), (102, "Sun Ra")]) as r:
        result = expand_influences(
            dc, store, llm, artist_id=7, artist_name="Pharoah Sanders",
            primary_styles=["Spiritual Jazz"],
        )

    assert f.call_count == 1
    assert r.call_count == 2
    assert {(e.influence_artist_id, e.confidence) for e in result} == {
        (101, "high"), (102, "medium"),
    }
    cached = store.get_artist_influences(source_artist_id=7)
    assert {e.influence_artist_id for e in cached} == {101, 102}


def test_expand_drops_unresolved_names(setup) -> None:
    store, dc, llm = setup
    candidates = [
        InfluenceCandidate(name="X", confidence="high", note=""),
        InfluenceCandidate(name="Y", confidence="medium", note=""),
    ]
    with patch("discogs.recommend.influences.fetch_influences_from_claude",
               return_value=candidates), \
         patch("discogs.recommend.influences.resolve_artist_name",
               side_effect=[None, (200, "Y")]):
        result = expand_influences(dc, store, llm, artist_id=7, artist_name="Z",
                                   primary_styles=[])
    assert {e.influence_artist_id for e in result} == {200}


def test_expand_refreshes_when_stale(setup) -> None:
    store, dc, llm = setup
    stale = ArtistInfluence(source_artist_id=7, influence_artist_id=99,
                           confidence="high", source="claude",
                           fetched_at=datetime.now(UTC) - timedelta(days=100))
    store.replace_artist_influences(source_artist_id=7, edges=[stale])

    with patch("discogs.recommend.influences.fetch_influences_from_claude",
               return_value=[]) as f:
        expand_influences(dc, store, llm, artist_id=7, artist_name="X",
                         primary_styles=[])
        f.assert_called_once()
