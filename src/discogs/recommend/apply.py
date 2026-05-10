"""Apply and undo orchestrators for a recommendation run's wantlist writes."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore
from discogs.wantlist_writer import push_to_wantlist, remove_from_wantlist


@dataclass
class UndoReport:
    run_id: str
    removed: int = 0
    skipped: int = 0
    errors: int = 0
    failed_picks: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class ApplyReport:
    run_id: str
    successes: int = 0
    failures: int = 0
    skipped_already_applied: int = 0
    failed_picks: list[tuple[int, str]] = field(default_factory=list)
    # successful_picks not stored — caller can re-query store if needed.


def apply_run(
    client: DiscogsClient, store: CacheStore, *,
    username: str, run_id: str,
) -> ApplyReport:
    """Push every (not-yet-applied) pick for `run_id` to the user's wantlist.

    Each push attempt is independent. Successes are recorded immediately so a
    crash mid-batch doesn't lose work. Failures are collected; the caller decides
    how to surface them.
    """
    report = ApplyReport(run_id=run_id)
    rows = store.get_recommendations_for_run(run_id)

    for row in rows:
        release_id = int(row["release_id"])
        if row["applied_to_wantlist"]:
            report.skipped_already_applied += 1
            continue

        result = push_to_wantlist(client, username=username, release_id=release_id)
        if result.ok:
            store.mark_recommendation_applied(
                run_id=run_id, release_id=release_id, applied_at=datetime.now(UTC),
            )
            report.successes += 1
        else:
            report.failures += 1
            report.failed_picks.append((release_id, result.error or "unknown error"))

    return report


def undo_run(
    client: DiscogsClient, store: CacheStore, *,
    username: str, run_id: str, reason: str = "undo",
) -> UndoReport:
    """Remove every currently-applied pick of `run_id` from the user's wantlist.

    'Skipped' (already-not-wantlisted) and 'error' (HTTP error other than 404)
    are reported separately so the caller can distinguish a benign race from a
    real failure.
    """
    report = UndoReport(run_id=run_id)
    rows = store.get_recommendations_for_run(run_id)

    for row in rows:
        if not row["applied_to_wantlist"]:
            continue

        release_id = int(row["release_id"])
        result = remove_from_wantlist(client, username=username, release_id=release_id)

        if result.status == "removed":
            store.mark_recommendation_removed(
                run_id=run_id, release_id=release_id,
                removed_at=datetime.now(UTC), reason=reason,
            )
            report.removed += 1
        elif result.status == "skipped":
            # Treat as already-removed; clear the applied flag.
            store.mark_recommendation_removed(
                run_id=run_id, release_id=release_id,
                removed_at=datetime.now(UTC), reason=f"{reason} (was already off wantlist)",
            )
            report.skipped += 1
        else:
            report.errors += 1
            report.failed_picks.append((release_id, result.error or "unknown error"))

    return report
