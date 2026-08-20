"""Tests for importing Spotify artists into the Discogs cache.

Resolution costs an API call and is cached forever, so the behaviour that
matters is what a *second* run does — and what a capped run leaves for
the next one.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.recommend.seeds import select_seeds
from discogs.spotify.importer import import_artists
from discogs.spotify.interchange import SpotifyArtist


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    init_db(tmp_path / "cache.db")
    s = CacheStore(tmp_path / "cache.db")
    yield s
    s.close()


class _Hit:
    """An artist search result as the Discogs client actually returns it."""

    def __init__(self, hit_id: int, title: str) -> None:
        self.id = hit_id
        self.title = None
        self.data = {"id": hit_id, "title": title, "type": "artist"}


def _client(by_name: dict[str, list[_Hit]]) -> MagicMock:
    client = MagicMock()
    client.call.side_effect = lambda _op, name, **_kw: by_name.get(name, [])
    return client


def test_resolves_and_caches(store: CacheStore) -> None:
    client = _client({"Nirvana": [_Hit(125246, "Nirvana")]})
    artists = [SpotifyArtist("art1", "Nirvana", 43)]

    result = import_artists(store, client, artists)

    assert (result.resolved, result.unresolved) == (1, 0)
    assert store.spotify_seed_weights() == {125246: 43}


def test_a_second_run_costs_nothing_but_refreshes_the_weight(store: CacheStore) -> None:
    """The library grows, so the seed weight has to — but the resolution
    is settled and must not be paid for twice."""
    client = _client({"Nirvana": [_Hit(125246, "Nirvana")]})
    import_artists(store, client, [SpotifyArtist("art1", "Nirvana", 43)])
    calls_after_first = client.call.call_count

    result = import_artists(store, client, [SpotifyArtist("art1", "Nirvana", 60)])

    assert result.skipped == 1
    assert client.call.call_count == calls_after_first  # no new API call
    assert store.spotify_seed_weights() == {125246: 60}  # weight moved


def test_an_unresolved_artist_is_recorded_not_forgotten(store: CacheStore) -> None:
    result = import_artists(store, _client({}), [SpotifyArtist("art9", "Nobody At All", 3)])

    assert (result.resolved, result.unresolved) == (0, 1)
    assert store.spotify_artist_resolutions() == {"art9": None}
    assert store.spotify_seed_weights() == {}  # never seeds anything


def test_a_capped_run_resolves_the_heaviest_and_leaves_the_rest(store: CacheStore) -> None:
    client = _client({"Big": [_Hit(1, "Big")], "Small": [_Hit(2, "Small")]})
    artists = [SpotifyArtist("a1", "Big", 50), SpotifyArtist("a2", "Small", 2)]

    result = import_artists(store, client, artists, limit=1)

    assert result.resolved == 1
    assert store.spotify_seed_weights() == {1: 50}
    # The skipped one is still on record, so the next run knows to do it.
    assert "a2" in store.spotify_artist_resolutions()


def test_two_spotify_artists_resolving_to_one_discogs_artist_sum(store: CacheStore) -> None:
    client = _client({"Magma": [_Hit(7, "Magma")], "MAGMA": [_Hit(7, "Magma")]})
    artists = [SpotifyArtist("a1", "Magma", 10), SpotifyArtist("a2", "MAGMA", 5)]

    import_artists(store, client, artists)

    assert store.spotify_seed_weights() == {7: 15}


def test_spotify_artists_become_seeds_without_owning_a_record(store: CacheStore) -> None:
    """The whole point: the collection is 101 records and the listening
    history is thousands, so a streamed artist must seed on its own
    evidence rather than on credits it does not have."""
    client = _client({"Magma": [_Hit(7, "Magma")]})
    import_artists(store, client, [SpotifyArtist("a1", "Magma", 12)])

    seeds = select_seeds(store, mode="spotify")

    assert [(s.artist_id, s.sources) for s in seeds] == [(7, ("spotify",))]


def test_collection_only_mode_ignores_the_spotify_library(store: CacheStore) -> None:
    client = _client({"Magma": [_Hit(7, "Magma")]})
    import_artists(store, client, [SpotifyArtist("a1", "Magma", 12)])

    assert select_seeds(store, mode="collection") == []


def test_the_most_listened_artist_is_the_strongest_seed(store: CacheStore) -> None:
    """Credit counts and liked-track counts mean opposite things. Being
    credited on everything makes an artist less distinctive; having many
    liked tracks makes them more central. Sharing one formula gave The
    Beatles, the heaviest artist in the real library at 135 liked tracks,
    the lowest weight of any seed."""
    client = _client(
        {"Beatles": [_Hit(82730, "Beatles")], "Ab-Soul": [_Hit(1, "Ab-Soul")]}
    )
    import_artists(
        store,
        client,
        [SpotifyArtist("a1", "Beatles", 135), SpotifyArtist("a2", "Ab-Soul", 15)],
    )

    seeds = select_seeds(store, mode="spotify")

    assert [s.artist_id for s in seeds] == [82730, 1]
    assert seeds[0].weight > seeds[1].weight


def test_an_artist_evidenced_both_ways_keeps_the_stronger_claim(store: CacheStore) -> None:
    client = _client({"Magma": [_Hit(7, "Magma")]})
    import_artists(store, client, [SpotifyArtist("a1", "Magma", 30)])

    (seed,) = select_seeds(store, mode="all")

    assert seed.artist_id == 7
    assert "spotify" in seed.sources
