from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import DiscogsClient
from discogs.api.releases import fetch_release
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


def _fake_raw_release(rid: int = 100) -> MagicMock:
    raw = MagicMock()
    raw.id = rid
    raw.master_id = 50
    raw.title = "Karma"
    raw.year = 1969
    raw.country = "US"
    raw.formats = [{"name": "Vinyl", "qty": "1", "descriptions": ["LP", "Album"]}]
    raw.styles = ["Spiritual Jazz", "Free Jazz"]
    raw.genres = ["Jazz"]
    raw.community.have = 2500
    raw.community.want = 8000
    raw.community.rating.average = 4.6
    raw.community.rating.count = 320

    p1 = MagicMock()
    p1.id = 1
    p1.role = "Tenor Saxophone"
    p2 = MagicMock()
    p2.id = 2
    p2.role = "Producer"
    raw.extraartists = [p1, p2]
    raw.tracklist = []

    label = MagicMock()
    label.id = 101
    label.name = "Impulse!"
    label.data = {"catno": "AS-9181"}
    label.catno = "AS-9181"
    raw.labels = [label]

    return raw


def test_fetch_release_persists_release_row(setup) -> None:
    _, store, client = setup
    raw = _fake_raw_release(rid=100)
    client.upstream.release.return_value = raw

    release = fetch_release(client, store, 100)
    assert release.id == 100
    assert release.title == "Karma"
    assert release.community_have == 2500

    cached = store.get_release(100)
    assert cached is not None
    assert cached.title == "Karma"


def test_fetch_release_persists_credits(setup) -> None:
    _, store, client = setup
    client.upstream.release.return_value = _fake_raw_release(rid=100)

    fetch_release(client, store, 100)

    credits = store.get_release_credits(100)
    assert {(c.artist_id, c.role) for c in credits} == {
        (1, "Tenor Saxophone"),
        (2, "Producer"),
    }


def test_fetch_release_persists_labels(setup) -> None:
    _, store, client = setup
    client.upstream.release.return_value = _fake_raw_release(rid=100)

    fetch_release(client, store, 100)
    assert set(store.get_release_label_ids(100)) == {101}


def test_fetch_release_persists_label_records(setup) -> None:
    """fetch_release should populate the labels table so digests can show names."""
    _, store, client = setup
    client.upstream.release.return_value = _fake_raw_release(rid=100)

    fetch_release(client, store, 100)
    label = store.get_label(101)
    assert label is not None
    assert label.name == "Impulse!"


def test_fetch_release_skips_label_with_none_id(setup) -> None:
    """Discogs occasionally returns label entries with id=None — must skip, not crash."""
    _, store, client = setup
    raw = _fake_raw_release(rid=100)
    stub = MagicMock()
    stub.id = None
    stub.catno = None
    raw.labels = [*raw.labels, stub]
    client.upstream.release.return_value = raw

    fetch_release(client, store, 100)
    assert set(store.get_release_label_ids(100)) == {101}


def test_fetch_release_uses_cache_when_fresh(setup) -> None:
    _, store, client = setup
    raw = _fake_raw_release(rid=100)
    client.upstream.release.return_value = raw

    fetch_release(client, store, 100)
    initial_calls = store.api_calls_today()

    fetch_release(client, store, 100)
    assert store.api_calls_today() == initial_calls  # cache hit, no extra API call


def test_fetch_release_refreshes_when_stale(setup) -> None:
    _, store, client = setup
    raw = _fake_raw_release(rid=100)
    client.upstream.release.return_value = raw

    fetch_release(client, store, 100)
    initial_calls = store.api_calls_today()

    # Force the cached row to look 31 days old
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    with store.conn:
        store.conn.execute("UPDATE releases SET fetched_at = ? WHERE id = ?", (old, 100))

    fetch_release(client, store, 100)
    assert store.api_calls_today() == initial_calls + 1  # refetched


def test_fetch_release_includes_track_extraartists(setup) -> None:
    _, store, client = setup
    raw = _fake_raw_release(rid=100)
    track = MagicMock()
    p3 = MagicMock()
    p3.id = 3
    p3.role = "Bass"
    track.extraartists = [p3]
    raw.tracklist = [track]
    client.upstream.release.return_value = raw

    fetch_release(client, store, 100)
    credits = store.get_release_credits(100)
    assert (3, "Bass") in {(c.artist_id, c.role) for c in credits}
