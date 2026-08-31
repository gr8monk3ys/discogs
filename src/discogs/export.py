"""Write the collection and wantlist as `discogs.json` for other tools to read.

Built from the local cache only — no API calls — so it is exactly as fresh as
the last `discogs sync`. The schema string is the contract: consumers (the rym
and spotify repos) assert it on load.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from discogs.cache.store import CacheStore

SCHEMA = "discogs/1"


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
    return {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "username": username,
        "collection": collection,
        "wantlist": wantlist,
    }


def write_export(doc: dict[str, Any], path: Path) -> Path:
    """Write atomically: a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path
