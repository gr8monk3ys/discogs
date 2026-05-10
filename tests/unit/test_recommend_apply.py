from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discogs.api.client import BudgetExceeded, DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.recommend.apply import ApplyReport, apply_run
from discogs.wantlist_writer import PushResult


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[CacheStore, DiscogsClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db", daily_api_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    yield store, client
    store.close()


def _seed_run(store: CacheStore, picks: list[tuple[int, float]]) -> str:
    run_id, _ = store.start_run(args={})
    for rid, score in picks:
        store.record_recommendation(run_id, rid, score)
    store.finish_run(run_id, summary={})
    return run_id


def test_apply_pushes_each_pick_and_marks_applied(setup) -> None:
    store, client = setup
    run_id = _seed_run(store, picks=[(10, 0.8), (20, 0.7)])

    with patch("discogs.recommend.apply.push_to_wantlist") as push:
        push.side_effect = [
            PushResult(release_id=10, ok=True, error=None),
            PushResult(release_id=20, ok=True, error=None),
        ]
        report = apply_run(client, store, username="u", run_id=run_id)

    assert isinstance(report, ApplyReport)
    assert report.successes == 2
    assert report.failures == 0
    rows = store.get_recommendations_for_run(run_id)
    assert all(r["applied_to_wantlist"] == 1 for r in rows)
    assert all(r["applied_at"] is not None for r in rows)


def test_apply_records_failures_per_release(setup) -> None:
    store, client = setup
    run_id = _seed_run(store, picks=[(10, 0.8), (20, 0.7), (30, 0.6)])

    with patch("discogs.recommend.apply.push_to_wantlist") as push:
        push.side_effect = [
            PushResult(release_id=10, ok=True, error=None),
            PushResult(release_id=20, ok=False, error="HTTP 500"),
            PushResult(release_id=30, ok=True, error=None),
        ]
        report = apply_run(client, store, username="u", run_id=run_id)

    assert report.successes == 2
    assert report.failures == 1
    assert report.failed_picks == [(20, "HTTP 500")]

    rows = store.get_recommendations_for_run(run_id)
    by_id = {r["release_id"]: r for r in rows}
    assert by_id[10]["applied_to_wantlist"] == 1
    assert by_id[20]["applied_to_wantlist"] == 0
    assert by_id[30]["applied_to_wantlist"] == 1


def test_apply_skips_already_applied_picks(setup) -> None:
    store, client = setup
    run_id = _seed_run(store, picks=[(10, 0.8), (20, 0.7)])
    store.mark_recommendation_applied(run_id, 10, datetime.now(UTC))

    with patch("discogs.recommend.apply.push_to_wantlist") as push:
        push.return_value = PushResult(release_id=20, ok=True, error=None)
        report = apply_run(client, store, username="u", run_id=run_id)

    push.assert_called_once()
    assert push.call_args.kwargs["release_id"] == 20
    assert report.successes == 1
    assert report.skipped_already_applied == 1


def test_apply_returns_zero_when_run_has_no_picks(setup) -> None:
    store, client = setup
    run_id, _ = store.start_run(args={})
    store.finish_run(run_id, summary={})
    report = apply_run(client, store, username="u", run_id=run_id)
    assert report.successes == 0 and report.failures == 0


def test_apply_propagates_budget_exceeded(setup) -> None:
    """BudgetExceeded must escape apply_run so the CLI can abort the batch."""
    store, client = setup
    run_id = _seed_run(store, picks=[(10, 0.8)])

    with patch("discogs.recommend.apply.push_to_wantlist") as push:
        push.side_effect = BudgetExceeded("daily limit")
        with pytest.raises(BudgetExceeded):
            apply_run(client, store, username="u", run_id=run_id)
