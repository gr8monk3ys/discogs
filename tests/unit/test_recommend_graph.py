from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.models import CollectionItem, Credit, Format, Release, WantlistItem
from discogs.recommend.graph import GraphPath, role_weight, walk_credit_graph
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


def test_role_weight_table() -> None:
    assert role_weight("Producer") == 1.0
    assert role_weight("Tenor Saxophone") == 1.0       # any "primary" credit
    assert role_weight("Engineer") == 0.5
    assert role_weight("Mastered By") == 0.3
    assert role_weight("Liner Notes") == 0.2
    assert role_weight("Some Unknown Role") == 0.5     # default


def test_walk_collects_direct_seed_releases(setup) -> None:
    store, client = setup
    seeds = [SeedArtist(artist_id=7, weight=0.9, sources=("collection",))]
    fake_release = _stub_release(101, credits=[Credit(release_id=101, artist_id=7, role="Saxophone")])

    with patch("discogs.recommend.graph.fetch_artist_releases") as far, \
         patch("discogs.recommend.graph.fetch_release") as fr:
        far.return_value = [101]
        fr.return_value = fake_release
        # Persist credits so the in-memory store mirrors what fetch_release would.
        store.replace_release_credits(101, [Credit(release_id=101, artist_id=7, role="Saxophone")])

        paths = walk_credit_graph(client, store, seeds, budget=100)

    assert 101 in paths
    assert paths[101][0].seed_artist_id == 7
    assert len(paths[101][0].edge_chain) == 1


def test_walk_expands_one_hop_through_neighbors(setup) -> None:
    store, client = setup
    seeds = [SeedArtist(artist_id=7, weight=0.9, sources=("collection",))]

    with patch("discogs.recommend.graph.fetch_artist_releases") as far, \
         patch("discogs.recommend.graph.fetch_release") as fr:

        def fake_fetch_artist_releases(_c, _s, artist_id, top_k=25):
            if artist_id == 7:
                return [101]
            if artist_id == 99:
                return [201, 202]
            return []

        def fake_fetch_release(_c, _s, release_id):
            return _stub_release(release_id, credits=[])

        far.side_effect = fake_fetch_artist_releases
        fr.side_effect = fake_fetch_release
        store.replace_release_credits(101, [
            Credit(release_id=101, artist_id=7, role="Saxophone"),
            Credit(release_id=101, artist_id=99, role="Producer"),
        ])
        store.replace_release_credits(201, [])
        store.replace_release_credits(202, [])

        paths = walk_credit_graph(client, store, seeds, max_neighbors_per_seed=3, budget=100)

    assert 101 in paths   # direct seed release
    assert 201 in paths   # one-hop via neighbor 99
    assert 202 in paths


def test_walk_excludes_collection_and_wantlist(setup) -> None:
    store, client = setup
    seeds = [SeedArtist(artist_id=7, weight=0.9, sources=("collection",))]

    store.replace_collection([
        CollectionItem(release_id=101, folder_id=0, instance_id=1, date_added=datetime.now(UTC)),
    ])
    store.replace_wantlist([
        WantlistItem(release_id=202, date_added=datetime.now(UTC), notes=None),
    ])

    with patch("discogs.recommend.graph.fetch_artist_releases") as far, \
         patch("discogs.recommend.graph.fetch_release") as fr:
        far.return_value = [101, 202, 999]
        fr.side_effect = lambda _c, _s, rid: _stub_release(rid, credits=[])
        store.replace_release_credits(999, [])

        paths = walk_credit_graph(client, store, seeds, budget=100)

    assert 101 not in paths
    assert 202 not in paths
    assert 999 in paths


def test_walk_excludes_previously_recommended(setup) -> None:
    store, client = setup
    seeds = [SeedArtist(artist_id=7, weight=0.9, sources=("collection",))]
    rid_prev, _ = store.start_run(args={})
    store.record_recommendation(rid_prev, release_id=101, score=0.5)
    store.finish_run(rid_prev, summary={})

    with patch("discogs.recommend.graph.fetch_artist_releases") as far, \
         patch("discogs.recommend.graph.fetch_release") as fr:
        far.return_value = [101, 102]
        fr.side_effect = lambda _c, _s, rid: _stub_release(rid, credits=[])
        store.replace_release_credits(101, [])
        store.replace_release_credits(102, [])

        paths = walk_credit_graph(client, store, seeds, budget=100)

    assert 101 not in paths
    assert 102 in paths


def test_walk_excludes_neighbor_release_in_library(setup) -> None:
    """A release reached via a one-hop neighbor that's already in the wantlist must not appear."""
    store, client = setup
    seeds = [SeedArtist(artist_id=7, weight=0.9, sources=("collection",))]

    # Release 999 will be the wantlisted neighbor release we want to verify is excluded.
    store.replace_wantlist([
        WantlistItem(release_id=999, date_added=datetime.now(UTC), notes=None),
    ])

    with patch("discogs.recommend.graph.fetch_artist_releases") as far, \
         patch("discogs.recommend.graph.fetch_release") as fr:

        def fake_fetch_artist_releases(_c, _s, artist_id, top_k=25):
            if artist_id == 7:
                return [101]      # seed's direct release
            if artist_id == 99:
                return [999, 998]  # neighbor's releases — 999 is in wantlist, 998 is not
            return []

        far.side_effect = fake_fetch_artist_releases
        fr.side_effect = lambda _c, _s, rid: _stub_release(rid, credits=[])
        store.replace_release_credits(101, [
            Credit(release_id=101, artist_id=7, role="Saxophone"),
            Credit(release_id=101, artist_id=99, role="Producer"),
        ])
        store.replace_release_credits(999, [])
        store.replace_release_credits(998, [])

        paths = walk_credit_graph(client, store, seeds, max_neighbors_per_seed=3, budget=100)

    assert 999 not in paths   # excluded because in wantlist
    assert 998 in paths        # included as one-hop candidate
    assert 101 in paths        # the direct seed release also included (not in any library set)


def test_walk_respects_budget(setup) -> None:
    store, client = setup
    seeds = [
        SeedArtist(artist_id=7, weight=0.9, sources=("collection",)),
        SeedArtist(artist_id=8, weight=0.8, sources=("collection",)),
    ]

    call_log: list[int] = []

    def far_side(_c, _s, artist_id, top_k=25):
        call_log.append(artist_id)
        store.increment_api_calls(1)
        return []

    with patch("discogs.recommend.graph.fetch_artist_releases", side_effect=far_side), \
         patch("discogs.recommend.graph.fetch_release") as fr:
        fr.return_value = _stub_release(0, credits=[])
        # budget=1 means we get exactly one fetch_artist_releases call before halting
        walk_credit_graph(client, store, seeds, budget=1)

    assert len(call_log) == 1
