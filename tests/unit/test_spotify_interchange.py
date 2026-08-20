"""Tests for reading the Spotify interchange file.

Two repos consume this file, so the reader asserts its schema rather than
trusting it: a silently-changed shape would surface as wrong
recommendations, not as an error.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from discogs.spotify import interchange


def _doc(**overrides: object) -> dict:
    base = {
        "schema": "music-library/1",
        "generated_at": "2026-08-19T00:00:00Z",
        "source": {"platform": "spotify", "user": "gr8monk3ys"},
        "albums": [
            {
                "spotify_album_id": "alb1",
                "title": "In Utero",
                "artists": [{"spotify_id": "art1", "name": "Nirvana"}],
                "year": 1993,
                "liked_track_count": 9,
                "total_tracks": 12,
                "affinity": 0.75,
                "isrcs": ["USGF19953601"],
            }
        ],
        "discoveries": [],
    }
    base.update(overrides)
    return base


def _write(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "music-library.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_reads_albums_and_artists(tmp_path: Path) -> None:
    data = interchange.load(_write(tmp_path, _doc()))
    album = interchange.albums(data)[0]

    assert album.title == "In Utero"
    assert album.year == 1993
    assert album.affinity == 0.75
    assert album.artists[0].name == "Nirvana"


def test_a_missing_file_says_how_to_make_one(tmp_path: Path) -> None:
    with pytest.raises(interchange.InterchangeError, match="spotifyforge export library"):
        interchange.load(tmp_path / "nope.json")


def test_an_unknown_schema_is_refused_not_guessed_at(tmp_path: Path) -> None:
    path = _write(tmp_path, _doc(schema="music-library/99"))

    with pytest.raises(interchange.InterchangeError, match="music-library/99"):
        interchange.load(path)


def test_malformed_json_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "music-library.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(interchange.InterchangeError, match="not valid JSON"):
        interchange.load(path)


def test_an_artist_on_several_albums_is_one_seed_with_summed_weight(tmp_path: Path) -> None:
    """The weight means "how much of this library is theirs", so credits
    across albums add up rather than the largest one winning."""
    doc = _doc(
        albums=[
            {
                "spotify_album_id": "a1",
                "title": "One",
                "artists": [{"spotify_id": "art1", "name": "Magma"}],
                "year": 1973,
                "liked_track_count": 4,
                "total_tracks": 6,
                "affinity": 0.66,
                "isrcs": [],
            },
            {
                "spotify_album_id": "a2",
                "title": "Two",
                "artists": [{"spotify_id": "art1", "name": "Magma"}],
                "year": 1974,
                "liked_track_count": 3,
                "total_tracks": 8,
                "affinity": 0.375,
                "isrcs": [],
            },
        ]
    )
    artists = interchange.distinct_artists(interchange.load(_write(tmp_path, doc)))

    assert [(a.name, a.liked_track_count) for a in artists] == [("Magma", 7)]


def test_artists_arrive_heaviest_first_so_a_capped_run_does_the_ones_that_matter(
    tmp_path: Path,
) -> None:
    doc = _doc(
        albums=[
            {
                "spotify_album_id": "a1", "title": "Light",
                "artists": [{"spotify_id": "small", "name": "Minor"}],
                "year": 2001, "liked_track_count": 1, "total_tracks": 10,
                "affinity": 0.1, "isrcs": [],
            },
            {
                "spotify_album_id": "a2", "title": "Heavy",
                "artists": [{"spotify_id": "big", "name": "Major"}],
                "year": 2002, "liked_track_count": 20, "total_tracks": 20,
                "affinity": 1.0, "isrcs": [],
            },
        ]
    )
    artists = interchange.distinct_artists(interchange.load(_write(tmp_path, doc)))

    assert [a.name for a in artists] == ["Major", "Minor"]


def test_an_artist_without_a_spotify_id_is_dropped(tmp_path: Path) -> None:
    doc = _doc(
        albums=[
            {
                "spotify_album_id": "a1", "title": "Anon",
                "artists": [{"name": "No Id"}],
                "year": 1999, "liked_track_count": 2, "total_tracks": 4,
                "affinity": 0.5, "isrcs": [],
            }
        ]
    )
    assert interchange.distinct_artists(interchange.load(_write(tmp_path, doc))) == []
