import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime
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


def test_start_run_returns_ids(store: CacheStore) -> None:
    run_id, display_id = store.start_run(args={"max_recs": 25})
    assert isinstance(run_id, str) and len(run_id) > 0
    assert isinstance(display_id, str) and len(display_id) > 0
    row = store.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert row is not None
    assert row["finished_at"] is None
    assert json.loads(row["args_json"]) == {"max_recs": 25}


def test_finish_run_writes_summary(store: CacheStore) -> None:
    run_id, _ = store.start_run(args={})
    store.finish_run(run_id, summary={"candidates": 247, "selected": 25})
    row = store.conn.execute(
        "SELECT finished_at, summary_json FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row["finished_at"] is not None
    assert json.loads(row["summary_json"]) == {"candidates": 247, "selected": 25}


def test_record_recommendation_persists(store: CacheStore) -> None:
    run_id, _ = store.start_run(args={})
    store.record_recommendation(run_id=run_id, release_id=42, score=0.78)
    row = store.conn.execute(
        "SELECT * FROM recommendation_history WHERE release_id = ? AND run_id = ?",
        (42, run_id),
    ).fetchone()
    assert row is not None
    assert row["score"] == 0.78
    assert row["applied_to_wantlist"] == 0


def test_previously_recommended_release_ids_returns_all(store: CacheStore) -> None:
    run_a, _ = store.start_run(args={})
    store.record_recommendation(run_a, release_id=1, score=0.5)
    store.record_recommendation(run_a, release_id=2, score=0.6)
    store.finish_run(run_a, summary={})

    # Ensure second-precision display_id is different for run_b
    time.sleep(1.01)
    run_b, _ = store.start_run(args={})
    store.record_recommendation(run_b, release_id=3, score=0.7)

    assert store.previously_recommended_release_ids() == {1, 2, 3}


def test_display_id_uses_utc_second(store: CacheStore) -> None:
    run_id, display_id = store.start_run(args={})
    # YYYY-MM-DD-HHMMSS, e.g. 2026-05-08-183045 (17 chars)
    assert len(display_id) == len("YYYY-MM-DD-HHMMSS")
    assert display_id[4] == "-" and display_id[7] == "-" and display_id[10] == "-"
