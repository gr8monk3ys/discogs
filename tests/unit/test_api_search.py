from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import DiscogsClient
from discogs.api.search import resolve_artist_name
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[CacheStore, DiscogsClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db", daily_api_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    yield store, client
    store.close()


def _hit(rid: int, title: str, score: float) -> MagicMock:
    h = MagicMock()
    h.id = rid
    h.title = title
    h.data = {"score": score}
    return h


def test_resolve_returns_top_hit_above_threshold(setup) -> None:
    _, client = setup
    client.upstream.search.return_value = [
        _hit(1, "Pharoah Sanders", 0.95),
        _hit(2, "Pharoah Sanders Quartet", 0.7),
    ]

    result = resolve_artist_name(client, "Pharoah Sanders", min_score=0.85)
    assert result is not None
    assert result == (1, "Pharoah Sanders")


def test_resolve_returns_none_below_threshold(setup) -> None:
    _, client = setup
    client.upstream.search.return_value = [
        _hit(1, "Pharoah Sanders", 0.5),
    ]
    assert resolve_artist_name(client, "Pharoah Sanders", min_score=0.85) is None


def test_resolve_returns_none_when_no_hits(setup) -> None:
    _, client = setup
    client.upstream.search.return_value = []
    assert resolve_artist_name(client, "Imaginary Person", min_score=0.85) is None


def test_resolve_handles_missing_score_field(setup) -> None:
    """If the search API doesn't include a score field, treat as 0 and reject."""
    _, client = setup
    h = MagicMock()
    h.id = 1
    h.title = "X"
    h.data = {}
    client.upstream.search.return_value = [h]
    assert resolve_artist_name(client, "X", min_score=0.85) is None
