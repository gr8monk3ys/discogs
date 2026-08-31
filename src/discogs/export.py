"""Write the collection and wantlist as `discogs.json` for other tools to read.

Built from the local cache only — no API calls — so it is exactly as fresh as
the last `discogs sync`. The schema string is the contract: consumers assert it
on load.

The envelope round the rows (`schema`, `generated_at`, `username`, the sections
in order) and the atomic write are `media_core.export`: five repos had written
the same two functions under the same two names. Building the rows stays here,
because that needs this repo's cache.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from media_core.export import build_export as _build_envelope
from media_core.export import write_export as _write_envelope

from discogs.cache.store import CacheStore

SCHEMA = "discogs/1"

# This repo writes ASCII-escaped, with no trailing newline. media_core defaults
# to the UTF-8-and-newline form the other tools use, so the choice is passed
# explicitly and the file's bytes do not move.
_JSON_STYLE: dict[str, Any] = {"indent": 1, "ensure_ascii": True, "newline": False}


def _item(store: CacheStore, release_id: int, added_at: datetime) -> dict[str, Any] | None:
    rel = store.get_release(release_id)
    if rel is None:
        return None
    return {
        "release_id": rel.id,
        "master_id": rel.master_id,
        "title": rel.title,
        "artists": list(rel.artists),
        "year": rel.year,
        "formats": [f.name for f in rel.formats],
        "added_at": added_at.isoformat(),
    }


def build_export(store: CacheStore, username: str, generated_at: str) -> dict[str, Any]:
    """The document, with any item whose release is not cached left out."""
    collection = [
        item
        for item in (_item(store, c.release_id, c.date_added) for c in store.iter_collection())
        if item is not None
    ]
    wantlist = [
        item
        for item in (_item(store, w.release_id, w.date_added) for w in store.iter_wantlist())
        if item is not None
    ]
    return _build_envelope(
        SCHEMA,
        generated_at,
        username=username,
        collection=collection,
        wantlist=wantlist,
    )


def write_export(doc: dict[str, Any], path: Path) -> Path:
    """Write atomically: a reader never sees a half-written file."""
    return _write_envelope(doc, path, **_JSON_STYLE)
