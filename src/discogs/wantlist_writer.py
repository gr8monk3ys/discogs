"""Push / remove releases to/from the user's Discogs wantlist."""
from __future__ import annotations

from dataclasses import dataclass

from discogs.api.client import DiscogsClient


@dataclass(frozen=True)
class PushResult:
    release_id: int
    ok: bool
    error: str | None


def push_to_wantlist(
    client: DiscogsClient, *, username: str, release_id: int,
) -> PushResult:
    """Add `release_id` to `username`'s wantlist. Returns a PushResult; never raises."""
    try:
        user = client.call("user", username)
        user.wantlist.add(release_id)
        client._store.increment_api_calls(1)  # the wantlist.add call itself
        return PushResult(release_id=release_id, ok=True, error=None)
    except Exception as e:  # noqa: BLE001 — convert any failure to structured result
        return PushResult(release_id=release_id, ok=False, error=str(e))
