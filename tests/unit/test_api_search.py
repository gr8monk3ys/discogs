"""Tests for artist-name resolution.

Resolution decides which Discogs artist a Spotify artist *is*, and every
recommendation seeded from it inherits that decision. So most of these
are about refusing to guess.

The payload shape here is the real one, confirmed against the live API:
the name lives in `data["title"]`, `hit.title` is None, and there is no
score field anywhere.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from discogs.api.search import resolve_artist_name


class _Hit:
    """An artist search result as the Discogs client actually returns it."""

    def __init__(self, hit_id: int, title: str) -> None:
        self.id = hit_id
        self.title = None  # genuinely None for artist results
        self.data = {"id": hit_id, "title": title, "type": "artist"}


def _client(hits: list[_Hit]) -> MagicMock:
    client = MagicMock()
    client.call.return_value = hits
    return client


def test_resolves_an_artist_that_has_no_score_field() -> None:
    """The defect this replaces: the resolver demanded `score >= 0.85`
    from a key the API never sends, so every lookup returned None."""
    client = _client([_Hit(125246, "Nirvana")])

    assert resolve_artist_name(client, "Nirvana") == (125246, "Nirvana")


def test_reads_the_name_from_the_payload_not_the_model() -> None:
    """`hit.title` is None for artist results; comparing against it
    matched nothing."""
    hit = _Hit(307, "Boards Of Canada")
    assert hit.title is None

    assert resolve_artist_name(_client([hit]), "Boards of Canada") == (307, "Boards Of Canada")


def test_the_unsuffixed_entry_wins_over_its_namesakes() -> None:
    """Discogs disambiguates shared names with "(2)", "(10)" and treats
    the plain one as primary — and it is not always listed first."""
    client = _client([_Hit(1, "Nirvana (2)"), _Hit(125246, "Nirvana"), _Hit(3, "Nirvana (10)")])

    assert resolve_artist_name(client, "Nirvana") == (125246, "Nirvana")


def test_only_suffixed_namesakes_stays_unresolved() -> None:
    """Several distinct acts share the name and nothing in the query says
    which. Unresolved is recoverable; wrong is not."""
    client = _client([_Hit(179749, "Magma (6)"), _Hit(762706, "Magma (12)")])

    assert resolve_artist_name(client, "Magma") is None


def test_a_single_suffixed_hit_is_accepted() -> None:
    """One candidate, no competition — the suffix only means Discogs
    numbered it, not that it is ambiguous here."""
    client = _client([_Hit(179749, "Zeuhl Orchestra (2)")])

    assert resolve_artist_name(client, "Zeuhl Orchestra") == (179749, "Zeuhl Orchestra (2)")


def test_a_near_miss_is_not_a_match() -> None:
    client = _client([_Hit(1, "Boards Of Canada Tribute"), _Hit(2, "The Boards")])

    assert resolve_artist_name(client, "Boards of Canada") is None


def test_matching_ignores_case_and_padding() -> None:
    client = _client([_Hit(9, "Boards Of Canada")])

    assert resolve_artist_name(client, "  boards of canada ") == (9, "Boards Of Canada")


def test_no_hits_at_all_is_not_an_error() -> None:
    assert resolve_artist_name(_client([]), "Nobody") is None


def test_an_empty_name_never_searches_for_everything() -> None:
    assert resolve_artist_name(_client([]), "   ") is None


# --- resolve_release -------------------------------------------------------

from discogs.api.search import ResolvedRelease, resolve_release  # noqa: E402


class _MasterHit:
    """A master search result: `title` is "Artist - Title" in the payload."""

    def __init__(self, hit_id: int, title: str, main: int | None) -> None:
        self.id = hit_id
        self.title = None
        self.data = {"id": hit_id, "title": title, "type": "master"}
        if main is not None:
            self.data["main_release"] = main


def test_resolve_release_matches_normalised_artist_dash_title() -> None:
    client = _client([_MasterHit(7, "Nirvana (2) - In Utero", 1)])

    r = resolve_release(client, "Nirvana", "In Utero (Deluxe Edition)")

    assert r == ResolvedRelease(release_id=1, master_id=7, canonical="Nirvana (2) - In Utero")
    client.call.assert_called_once_with("search", "Nirvana In Utero", type="master")


def test_resolve_release_refuses_ambiguity() -> None:
    client = _client([_MasterHit(7, "X - Y", 1), _MasterHit(8, "X - Y", 2)])

    assert resolve_release(client, "X", "Y") is None


def test_resolve_release_refuses_a_different_title() -> None:
    client = _client([_MasterHit(7, "Nirvana - Nevermind", 1)])

    assert resolve_release(client, "Nirvana", "In Utero") is None


def test_resolve_release_treats_hits_sharing_a_main_release_as_one_answer() -> None:
    client = _client([
        _MasterHit(7, "The Beatles - Abbey Road", 1), _MasterHit(8, "Beatles - Abbey Road", 1),
    ])

    r = resolve_release(client, "Beatles", "Abbey Road")

    assert r is not None and r.release_id == 1


def test_resolve_release_fetches_the_master_when_main_release_is_absent() -> None:
    client = _client([_MasterHit(7, "X - Y", None)])
    master = MagicMock()
    master.main_release.id = 42
    client.call.side_effect = [[_MasterHit(7, "X - Y", None)], master]

    r = resolve_release(client, "X", "Y")

    assert r == ResolvedRelease(release_id=42, master_id=7, canonical="X - Y")
    assert client.call.call_args_list[1].args == ("master", 7)


def test_resolve_release_with_no_hits_is_none() -> None:
    assert resolve_release(_client([]), "Nobody", "Nothing") is None


def test_resolve_release_treats_a_vanished_master_as_unresolved() -> None:
    """Search can return a master that 404s on fetch (deleted/merged).
    That hit is dropped, not fatal."""
    from discogs_client.exceptions import HTTPError

    client = _client([_MasterHit(7, "X - Y", None)])
    client.call.side_effect = [[_MasterHit(7, "X - Y", None)], HTTPError("gone", 404)]

    assert resolve_release(client, "X", "Y") is None


def test_resolve_release_drops_a_master_whose_fetch_returns_no_json() -> None:
    """A throttled Discogs fetch comes back with an empty body, which the
    client library raises as JSONDecodeError (a ValueError). The hit is
    dropped like a 404 rather than ending the run."""
    import json

    client = MagicMock()
    client.call.side_effect = [
        [_MasterHit(7, "X - Y", None)],
        json.JSONDecodeError("Expecting value", "", 0),
    ]
    assert resolve_release(client, "X", "Y") is None
