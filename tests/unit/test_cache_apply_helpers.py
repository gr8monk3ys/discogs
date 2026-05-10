import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def test_get_run_by_display_id(store: CacheStore) -> None:
    run_id, display_id = store.start_run(args={})
    assert store.get_run_by_display_id(display_id) == run_id
    assert store.get_run_by_display_id("nonexistent") is None


def test_mark_recommendation_applied(store: CacheStore) -> None:
    run_id, _ = store.start_run(args={})
    store.record_recommendation(run_id, release_id=42, score=0.7)
    when = datetime.now(UTC)
    store.mark_recommendation_applied(run_id, release_id=42, applied_at=when)

    row = store.conn.execute(
        "SELECT applied_to_wantlist, applied_at FROM recommendation_history "
        "WHERE run_id = ? AND release_id = ?", (run_id, 42),
    ).fetchone()
    assert row["applied_to_wantlist"] == 1
    assert row["applied_at"] is not None
    assert datetime.fromisoformat(row["applied_at"]) == when


def test_mark_recommendation_removed(store: CacheStore) -> None:
    run_id, _ = store.start_run(args={})
    store.record_recommendation(run_id, release_id=42, score=0.7)
    store.mark_recommendation_applied(run_id, 42, datetime.now(UTC))
    when = datetime.now(UTC)
    store.mark_recommendation_removed(run_id, 42, removed_at=when, reason="undo")

    row = store.conn.execute(
        "SELECT applied_to_wantlist, removed_at, removed_reason "
        "FROM recommendation_history WHERE run_id = ? AND release_id = ?",
        (run_id, 42),
    ).fetchone()
    assert row["applied_to_wantlist"] == 0
    assert row["removed_at"] is not None
    assert datetime.fromisoformat(row["removed_at"]) == when
    assert row["removed_reason"] == "undo"


def test_get_recommendations_for_run(store: CacheStore) -> None:
    run_id, _ = store.start_run(args={})
    store.record_recommendation(run_id, release_id=10, score=0.9)
    store.record_recommendation(run_id, release_id=20, score=0.8)
    store.mark_recommendation_applied(run_id, 10, datetime.now(UTC))

    rows = store.get_recommendations_for_run(run_id)
    assert len(rows) == 2
    by_id = {r["release_id"]: r for r in rows}
    assert by_id[10]["applied_to_wantlist"] == 1
    assert by_id[20]["applied_to_wantlist"] == 0


def test_last_applied_run_id_with_no_applies(store: CacheStore) -> None:
    assert store.last_applied_run_id() is None


def test_last_applied_run_id_returns_most_recent(store: CacheStore) -> None:
    run_a, _ = store.start_run(args={})
    store.record_recommendation(run_a, 1, 0.5)
    store.mark_recommendation_applied(run_a, 1, datetime.now(UTC) - timedelta(days=2))

    # display_id has second-level granularity; sleep avoids UNIQUE conflict
    time.sleep(1.01)
    run_b, _ = store.start_run(args={})
    store.record_recommendation(run_b, 2, 0.6)
    store.mark_recommendation_applied(run_b, 2, datetime.now(UTC))

    assert store.last_applied_run_id() == run_b


def test_mark_recommendation_applied_clears_prior_removal(store: CacheStore) -> None:
    run_id, _ = store.start_run(args={})
    store.record_recommendation(run_id, release_id=42, score=0.7)
    store.mark_recommendation_applied(run_id, 42, datetime.now(UTC))
    store.mark_recommendation_removed(run_id, 42, datetime.now(UTC), reason="undo")
    # Re-apply: should clear removed_at/removed_reason
    store.mark_recommendation_applied(run_id, 42, datetime.now(UTC))

    row = store.conn.execute(
        "SELECT applied_to_wantlist, removed_at, removed_reason "
        "FROM recommendation_history WHERE run_id = ? AND release_id = ?",
        (run_id, 42),
    ).fetchone()
    assert row["applied_to_wantlist"] == 1
    assert row["removed_at"] is None
    assert row["removed_reason"] is None
