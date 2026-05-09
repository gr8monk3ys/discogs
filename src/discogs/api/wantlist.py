"""Fetch the authenticated user's wantlist."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from discogs.api.client import DiscogsClient
from discogs.models import WantlistItem


def fetch_wantlist(client: DiscogsClient, username: str) -> Iterator[WantlistItem]:
    user = client.call("user", username)
    for raw in user.wantlist:
        yield WantlistItem(
            release_id=int(raw.release.id),
            date_added=_parse_dt(raw.date_added),
            notes=getattr(raw, "notes", None),
        )


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
