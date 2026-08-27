"""Name normalisation for matching albums across libraries.

Copied from rym/src/rym/match.py on purpose — the repos share a file format,
not code — and the three copies must agree. A false match writes to a real
account, so the rules are conservative and limited to noise that is
provably not part of a title.
"""
from __future__ import annotations

import re

# Spotify decorates album titles in ways Discogs does not: "(Deluxe Edition)",
# "- Remastered 2011", "(feat. ...)". These are editions of one record,
# not different records.
_EDITION = re.compile(
    r"\s*[\(\[-]\s*("
    r"deluxe|expanded|remaster|remastered|reissue|anniversary|special|"
    r"bonus|deluxe edition|super deluxe|legacy|collector|mono|stereo|"
    r"explicit|clean|international|japan|uk|us"
    r")\b.*$",
    re.IGNORECASE,
)
_BRACKETS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")
_PUNCT = re.compile(r"[^\w\s]")
_SPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Strip edition noise, punctuation and casing from a title or artist."""
    cleaned = _EDITION.sub("", text)
    cleaned = _BRACKETS.sub("", cleaned)
    cleaned = _PUNCT.sub(" ", cleaned)
    return _SPACE.sub(" ", cleaned).strip().casefold()


def strip_article(name: str) -> str:
    """Normalise and drop a leading article: "The Beatles" and "Beatles" are one act."""
    stripped = normalise(name)
    for article in ("the ", "a ", "an "):
        if stripped.startswith(article):
            return stripped[len(article) :]
    return stripped


def key(artist: str, title: str) -> tuple[str, str]:
    """The identity of a record for matching purposes."""
    return (strip_article(artist), normalise(title))
