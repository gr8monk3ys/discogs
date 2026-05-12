from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.artists import fetch_artist_releases
from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[Config, CacheStore, DiscogsClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db", daily_api_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    yield cfg, store, client
    store.close()


def _fake_release_ref(rid: int, type_: str = "release") -> MagicMock:
    r = MagicMock()
    r.id = rid
    r.type = type_
    return r


def _fake_artist(artist_id: int = 7, name: str = "Some Artist") -> MagicMock:
    a = MagicMock()
    a.id = artist_id
    a.name = name
    a.profile = None
    return a


def test_fetch_artist_releases_caches(setup) -> None:
    _, store, client = setup
    refs = [_fake_release_ref(i) for i in range(1, 31)]
    artist = _fake_artist()
    artist.releases = iter(refs)
    client.upstream.artist.return_value = artist

    rids = fetch_artist_releases(client, store, artist_id=7, top_k=10)

    assert rids == list(range(1, 11))
    assert store.get_artist_top_release_ids(7) == list(range(1, 11))


def test_fetch_artist_releases_filters_non_releases(setup) -> None:
    _, store, client = setup
    refs = [
        _fake_release_ref(1, "release"),
        _fake_release_ref(2, "master"),
        _fake_release_ref(3, "release"),
    ]
    artist = _fake_artist()
    artist.releases = iter(refs)
    client.upstream.artist.return_value = artist

    rids = fetch_artist_releases(client, store, artist_id=7, top_k=10)
    assert rids == [1, 3]


def test_fetch_artist_releases_uses_cache_when_fresh(setup) -> None:
    _, store, client = setup
    store.replace_artist_top_releases(artist_id=7, release_ids=[10, 11, 12])
    rids = fetch_artist_releases(client, store, artist_id=7, top_k=2)
    assert rids == [10, 11]
    client.upstream.artist.assert_not_called()


def test_fetch_artist_releases_refreshes_when_stale(setup) -> None:
    _, store, client = setup
    store.replace_artist_top_releases(artist_id=7, release_ids=[10])
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    with store.conn:
        store.conn.execute(
            "UPDATE artist_top_releases SET fetched_at = ? WHERE artist_id = ?",
            (old, 7),
        )

    artist = _fake_artist()
    artist.releases = iter([_fake_release_ref(99)])
    client.upstream.artist.return_value = artist

    rids = fetch_artist_releases(client, store, artist_id=7, top_k=5)
    assert rids == [99]


def test_fetch_artist_releases_persists_artist_record(setup) -> None:
    """Regression: graph walk shouldn't leave artists nameless. fetch_artist_releases
    already calls the artist endpoint, so it should piggyback the artist row."""
    _, store, client = setup
    artist = _fake_artist(artist_id=42, name="Pharoah Sanders")
    artist.releases = iter([_fake_release_ref(1)])
    client.upstream.artist.return_value = artist

    fetch_artist_releases(client, store, artist_id=42, top_k=5)
    cached = store.get_artist(42)
    assert cached is not None
    assert cached.name == "Pharoah Sanders"


def test_top_k_respects_limit(setup) -> None:
    _, store, client = setup
    refs = [_fake_release_ref(i) for i in range(100)]
    artist = _fake_artist()
    artist.releases = iter(refs)
    client.upstream.artist.return_value = artist

    rids = fetch_artist_releases(client, store, artist_id=7, top_k=5)
    assert rids == [0, 1, 2, 3, 4]
