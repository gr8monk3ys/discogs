from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discogs.api.client import BudgetExceeded, DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.recommend.apply import undo_run
from discogs.wantlist_writer import RemoveResult


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


def _seed_applied_run(store: CacheStore, picks: list[int]) -> str:
    run_id, _ = store.start_run(args={})
    now = datetime.now(UTC)
    for rid in picks:
        store.record_recommendation(run_id, rid, 0.5)
        store.mark_recommendation_applied(run_id, rid, now)
    store.finish_run(run_id, summary={})
    return run_id


def test_undo_removes_each_applied_pick(setup) -> None:
    store, client = setup
    run_id = _seed_applied_run(store, picks=[10, 20])

    with patch("discogs.recommend.apply.remove_from_wantlist") as rm:
        rm.side_effect = [
            RemoveResult(release_id=10, status="removed", error=None),
            RemoveResult(release_id=20, status="removed", error=None),
        ]
        report = undo_run(client, store, username="u", run_id=run_id)

    assert report.removed == 2
    assert report.skipped == 0
    rows = store.get_recommendations_for_run(run_id)
    assert all(r["applied_to_wantlist"] == 0 for r in rows)
    assert all(r["removed_at"] is not None for r in rows)


def test_undo_counts_skipped_separately(setup) -> None:
    store, client = setup
    run_id = _seed_applied_run(store, picks=[10, 20, 30])

    with patch("discogs.recommend.apply.remove_from_wantlist") as rm:
        rm.side_effect = [
            RemoveResult(release_id=10, status="removed", error=None),
            RemoveResult(release_id=20, status="skipped", error="404"),
            RemoveResult(release_id=30, status="removed", error=None),
        ]
        report = undo_run(client, store, username="u", run_id=run_id)

    assert report.removed == 2
    assert report.skipped == 1
    # The skipped one's history row should still get its applied_to_wantlist cleared,
    # since it's no longer on the wantlist regardless.
    rows = store.get_recommendations_for_run(run_id)
    by_id = {r["release_id"]: r for r in rows}
    assert by_id[20]["applied_to_wantlist"] == 0


def test_undo_records_errors(setup) -> None:
    store, client = setup
    run_id = _seed_applied_run(store, picks=[10, 20])

    with patch("discogs.recommend.apply.remove_from_wantlist") as rm:
        rm.side_effect = [
            RemoveResult(release_id=10, status="removed", error=None),
            RemoveResult(release_id=20, status="error", error="HTTP 500"),
        ]
        report = undo_run(client, store, username="u", run_id=run_id)

    assert report.errors == 1
    assert report.failed_picks == [(20, "HTTP 500")]


def test_undo_ignores_picks_not_yet_applied(setup) -> None:
    """If a pick was never applied, undo doesn't try to remove it."""
    store, client = setup
    run_id, _ = store.start_run(args={})
    store.record_recommendation(run_id, 10, 0.5)
    store.mark_recommendation_applied(run_id, 10, datetime.now(UTC))
    store.record_recommendation(run_id, 20, 0.5)  # never applied
    store.finish_run(run_id, summary={})

    with patch("discogs.recommend.apply.remove_from_wantlist") as rm:
        rm.return_value = RemoveResult(release_id=10, status="removed", error=None)
        report = undo_run(client, store, username="u", run_id=run_id)

    rm.assert_called_once()
    assert rm.call_args.kwargs["release_id"] == 10
    assert report.removed == 1


def test_undo_propagates_budget_exceeded(setup) -> None:
    """BudgetExceeded must escape undo_run so the CLI can abort the batch."""
    store, client = setup
    run_id = _seed_applied_run(store, picks=[10])

    with patch("discogs.recommend.apply.remove_from_wantlist") as rm:
        rm.side_effect = BudgetExceeded("daily limit")
        with pytest.raises(BudgetExceeded):
            undo_run(client, store, username="u", run_id=run_id)
