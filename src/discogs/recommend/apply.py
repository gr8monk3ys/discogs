"""Apply orchestrator: push a run's picks to the user's Discogs wantlist."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore
from discogs.wantlist_writer import push_to_wantlist


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
