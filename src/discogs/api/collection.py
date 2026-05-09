"""Fetch the authenticated user's full Discogs collection."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from discogs.api.client import DiscogsClient
from discogs.models import CollectionItem


def fetch_collection(client: DiscogsClient) -> Iterator[CollectionItem]:
    """Yield every CollectionItem in folder 0 ('All') of the authenticated user."""
    identity = client.call("identity")
    folder_zero = identity.collection_folders[0]
    for raw in folder_zero.releases:
        yield CollectionItem(
            release_id=int(raw.release.id),
            folder_id=int(raw.folder_id),
            instance_id=int(raw.instance_id),
            date_added=_parse_dt(raw.date_added),
        )


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
