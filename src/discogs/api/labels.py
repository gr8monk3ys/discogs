"""Fetch Label detail."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore
from discogs.models import Label

LABEL_TTL = timedelta(days=30)


def fetch_label(client: DiscogsClient, store: CacheStore, label_id: int) -> Label:
    age = store.label_age(label_id)
    if age is not None and age < LABEL_TTL:
        cached = store.get_label(label_id)
        if cached is not None:
            return cached

    raw = client.call("label", label_id)
    label = _label_from_raw(raw)
    store.upsert_label(label)
    return label


def _label_from_raw(raw: Any) -> Label:
    parent = getattr(raw, "parent_label", None)
    parent_str = parent.name if parent is not None and hasattr(parent, "name") else parent
    return Label(
        id=int(raw.id),
        name=str(raw.name),
        parent_label=parent_str,
        releases_count=int(raw.data.get("releases_count", 0) if hasattr(raw, "data") else 0),
        fetched_at=datetime.now(UTC),
    )
