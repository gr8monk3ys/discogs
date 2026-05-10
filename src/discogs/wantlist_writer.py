"""Push / remove releases to/from the user's Discogs wantlist."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from discogs_client.exceptions import HTTPError

from discogs.api.client import BudgetExceeded, DiscogsClient


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
        client.charge_call(1)
        return PushResult(release_id=release_id, ok=True, error=None)
    except BudgetExceeded:
        raise
    except Exception as e:  # noqa: BLE001 — convert any failure to structured result
        return PushResult(release_id=release_id, ok=False, error=str(e))


RemoveStatus = Literal["removed", "skipped", "error"]


@dataclass(frozen=True)
class RemoveResult:
    release_id: int
    status: RemoveStatus
    error: str | None


def remove_from_wantlist(
    client: DiscogsClient, *, username: str, release_id: int,
) -> RemoveResult:
    """Remove `release_id` from `username`'s wantlist. Returns a RemoveResult.

    A 404 from Discogs (release isn't wantlisted) is reported as `status="skipped"`,
    not an error — handles the case where the user manually removed the item before
    calling undo.
    """
    try:
        user = client.call("user", username)
        user.wantlist.remove(release_id)
        client.charge_call(1)
        return RemoveResult(release_id=release_id, status="removed", error=None)
    except BudgetExceeded:
        raise
    except HTTPError as e:
        if e.status_code == 404:
            return RemoveResult(release_id=release_id, status="skipped", error=str(e))
        return RemoveResult(release_id=release_id, status="error", error=str(e))
    except Exception as e:  # noqa: BLE001 — convert any unexpected failure to structured result
        return RemoveResult(release_id=release_id, status="error", error=str(e))
