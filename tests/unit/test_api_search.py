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
