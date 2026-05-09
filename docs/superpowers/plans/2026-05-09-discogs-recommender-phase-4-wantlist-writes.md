# Discogs Recommender — Phase 4: Wantlist Writes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add wantlist writes — `discogs recommend --apply`, `discogs apply <run-display-id>`, `discogs undo-last-batch`, `discogs undo <run-display-id>`. First-ever apply requires interactive `y/N` confirmation; `--yes` flag bypasses for scripts. Partial-failure handling: 22 of 25 push successes record, 3 failures surface with retry hints.

**Architecture:** New `src/discogs/wantlist_writer.py` wraps `client.upstream.user(username).wantlist.add(release_id)` / `.remove(release_id)` with retry, partial-failure capture, and `recommendation_history` updates. The `recommendation_history` table already has `applied_to_wantlist`, `applied_at`, `removed_at`, `removed_reason` columns from Phase 1's schema. Three new CLI commands plus `--apply` / `--yes` flags on `recommend`.

**Tech Stack:** Same as Phases 1-3.

**Spec reference:** `docs/superpowers/specs/2026-05-08-discogs-recommender-design.md` — this plan implements Build Sequence step 9 (apply + undo) and the spec's "Safety mechanisms" section (hard cap, dedup, undo, dry-run-by-default, confirm-on-first-apply).

**Phase 4 design decisions** (recorded from brainstorming, before writing this plan):

1. **First-ever-apply gate**: query `recommendation_history` for any row with `applied_at IS NOT NULL`. If empty, prompt for interactive `y/N` confirm before pushing. No new state — the table is the source of truth.

2. **Undo race handling** (user manually removed an applied item from wantlist before undoing): check membership before removing; skip-and-log silently. The undo report shows `"skipped X (already removed)"`. The `applied_to_wantlist` flag in `recommendation_history` is updated to reflect the actual state regardless of who removed it.

3. **`--yes` flag** bypasses the first-apply confirm (standard Unix convention, useful for scheduled jobs).

4. **Partial-failure semantics**: each push attempt is independent. Successes are recorded immediately (so a crash mid-batch doesn't lose work). Failures are collected, surfaced in the digest with the underlying error, and the run summary records both counts. The CLI exits 0 if any pushes succeeded; exits 1 only if all pushes failed.

**Out of scope (deferred):**
- Bulk operations beyond wantlist (e.g. updating collection folders) — separate feature
- Webhook / scheduled run integration (`discogs recommend --apply` from cron) — already works as a one-off; full daemon mode is out of scope
- Wantlist priority/notes editing — Discogs supports both but they aren't core to Phase 4

---

## Task 1: Cache helpers for run lookup + history updates

**Files:**
- Modify: `src/discogs/cache/store.py`
- Test: `tests/unit/test_cache_apply_helpers.py`

Adds five methods used by apply / undo:
- `get_run_by_display_id(display_id) -> str | None` (returns the UUID)
- `get_recommendations_for_run(run_id) -> list[(release_id, score, applied_to_wantlist, applied_at, removed_at)]`
- `mark_recommendation_applied(run_id, release_id, applied_at)` — sets `applied_to_wantlist=1, applied_at=...`
- `mark_recommendation_removed(run_id, release_id, removed_at, reason)` — sets `removed_at=..., removed_reason=...` and clears `applied_to_wantlist`
- `last_applied_run_id() -> str | None` — most-recent run with at least one applied row

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cache_apply_helpers.py`:

```python
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

    run_b, _ = store.start_run(args={})
    store.record_recommendation(run_b, 2, 0.6)
    store.mark_recommendation_applied(run_b, 2, datetime.now(UTC))

    assert store.last_applied_run_id() == run_b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cache_apply_helpers.py -v`
Expected: FAIL.

- [ ] **Step 3: Append methods to `CacheStore`**

```python
    def get_run_by_display_id(self, display_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT id FROM runs WHERE display_id = ?", (display_id,),
        ).fetchone()
        return str(row["id"]) if row else None

    def get_recommendations_for_run(self, run_id: str) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT release_id, score, applied_to_wantlist, applied_at, "
            "removed_at, removed_reason FROM recommendation_history "
            "WHERE run_id = ? ORDER BY score DESC",
            (run_id,),
        ).fetchall()
        return list(rows)

    def mark_recommendation_applied(
        self, run_id: str, release_id: int, applied_at: datetime,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE recommendation_history "
                "SET applied_to_wantlist = 1, applied_at = ?, "
                "    removed_at = NULL, removed_reason = NULL "
                "WHERE run_id = ? AND release_id = ?",
                (applied_at.isoformat(), run_id, release_id),
            )

    def mark_recommendation_removed(
        self, run_id: str, release_id: int, removed_at: datetime, reason: str,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE recommendation_history "
                "SET applied_to_wantlist = 0, removed_at = ?, removed_reason = ? "
                "WHERE run_id = ? AND release_id = ?",
                (removed_at.isoformat(), reason, run_id, release_id),
            )

    def last_applied_run_id(self) -> str | None:
        """Return the run_id of the most recently applied batch (latest applied_at)."""
        row = self.conn.execute(
            "SELECT run_id FROM recommendation_history "
            "WHERE applied_at IS NOT NULL "
            "ORDER BY applied_at DESC LIMIT 1"
        ).fetchone()
        return str(row["run_id"]) if row else None

    def has_any_apply(self) -> bool:
        """True iff any recommendation has ever been applied (drives first-apply confirm)."""
        row = self.conn.execute(
            "SELECT 1 FROM recommendation_history "
            "WHERE applied_at IS NOT NULL LIMIT 1"
        ).fetchone()
        return row is not None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_cache_apply_helpers.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/cache/store.py tests/unit/test_cache_apply_helpers.py
git commit -m "feat(cache): apply/undo helpers — get_run, mark_applied/removed, last_applied"
```

---

## Task 2: wantlist_writer — push to wantlist

**Files:**
- Create: `src/discogs/wantlist_writer.py`
- Test: `tests/unit/test_wantlist_writer_push.py`

Wraps `client.upstream.user(username).wantlist.add(release_id)`. Each push counts one API call. Returns a `PushResult` distinguishing success / failure-with-error so the pipeline can collect partial failures without aborting.

`python3-discogs-client`'s `Wantlist.add(release_id)` raises `discogs_client.exceptions.HTTPError` on API failure. We catch and convert to a structured failure rather than letting it propagate.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_wantlist_writer_push.py`:

```python
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.wantlist_writer import PushResult, push_to_wantlist


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


def test_push_success(setup) -> None:
    _, client = setup
    user = MagicMock()
    user.wantlist.add.return_value = None
    client.upstream.user.return_value = user

    result = push_to_wantlist(client, username="u", release_id=42)
    assert result == PushResult(release_id=42, ok=True, error=None)
    user.wantlist.add.assert_called_once_with(42)


def test_push_failure_captures_error(setup) -> None:
    _, client = setup
    user = MagicMock()
    user.wantlist.add.side_effect = RuntimeError("HTTP 500")
    client.upstream.user.return_value = user

    result = push_to_wantlist(client, username="u", release_id=42)
    assert result.ok is False
    assert result.release_id == 42
    assert "HTTP 500" in (result.error or "")


def test_push_increments_api_call_counter(setup) -> None:
    store, client = setup
    user = MagicMock()
    user.wantlist.add.return_value = None
    client.upstream.user.return_value = user

    initial = store.api_calls_today()
    push_to_wantlist(client, username="u", release_id=42)
    # one call for `user(username)`, one for `wantlist.add` — but discogs client wraps both.
    # We at minimum want >= 1 increment.
    assert store.api_calls_today() > initial
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_wantlist_writer_push.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `src/discogs/wantlist_writer.py`**

```python
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
```

NOTE: the `client._store.increment_api_calls(1)` reaches into a private attribute. Acceptable here because `wantlist_writer` is a sibling of `api/` and explicitly knows the call shape. If you want to avoid the underscore, expose `client.charge_call(n=1)` as a public method on `DiscogsClient` instead.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_wantlist_writer_push.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/wantlist_writer.py tests/unit/test_wantlist_writer_push.py
git commit -m "feat(wantlist): push_to_wantlist with structured PushResult"
```

---

## Task 3: wantlist_writer — remove from wantlist

**Files:**
- Modify: `src/discogs/wantlist_writer.py`
- Test: `tests/unit/test_wantlist_writer_remove.py`

Adds `remove_from_wantlist(client, username, release_id) -> RemoveResult`. Two outcomes that aren't errors: "removed" (was wantlisted, now isn't) and "skipped" (wasn't wantlisted in the first place — handles the manual-removal race).

`python3-discogs-client`'s `Wantlist.remove(release_id)` raises HTTP 404 when the release isn't wantlisted. We catch that as `skipped` rather than as an error.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_wantlist_writer_remove.py`:

```python
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.wantlist_writer import RemoveResult, remove_from_wantlist


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


def test_remove_success(setup) -> None:
    _, client = setup
    user = MagicMock()
    user.wantlist.remove.return_value = None
    client.upstream.user.return_value = user

    result = remove_from_wantlist(client, username="u", release_id=42)
    assert result == RemoveResult(release_id=42, status="removed", error=None)


def test_remove_skipped_when_not_in_wantlist(setup) -> None:
    """Discogs returns 404 when removing a release that isn't wantlisted; we
    treat that as 'skipped' rather than an error."""
    _, client = setup
    user = MagicMock()
    err = RuntimeError("404 Not Found")
    user.wantlist.remove.side_effect = err
    client.upstream.user.return_value = user

    result = remove_from_wantlist(client, username="u", release_id=42)
    assert result.status == "skipped"
    assert "404" in (result.error or "")


def test_remove_genuine_error(setup) -> None:
    _, client = setup
    user = MagicMock()
    user.wantlist.remove.side_effect = RuntimeError("HTTP 500 server error")
    client.upstream.user.return_value = user

    result = remove_from_wantlist(client, username="u", release_id=42)
    assert result.status == "error"
    assert "500" in (result.error or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_wantlist_writer_remove.py -v`
Expected: FAIL.

- [ ] **Step 3: Append to `src/discogs/wantlist_writer.py`**

Add the new dataclass + function:

```python
from typing import Literal

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
        client._store.increment_api_calls(1)
        return RemoveResult(release_id=release_id, status="removed", error=None)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "404" in msg:
            return RemoveResult(release_id=release_id, status="skipped", error=msg)
        return RemoveResult(release_id=release_id, status="error", error=msg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_wantlist_writer_remove.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/wantlist_writer.py tests/unit/test_wantlist_writer_remove.py
git commit -m "feat(wantlist): remove_from_wantlist with skipped-vs-error distinction"
```

---

## Task 4: Apply orchestrator — push picks for a run

**Files:**
- Create: `src/discogs/recommend/apply.py`
- Test: `tests/unit/test_recommend_apply.py`

The high-level orchestrator: take a `run_id`, look up its picks, push each one to the wantlist, mark each as applied (or capture the failure). Returns an `ApplyReport` with success/failure counts + per-release error details. Used by both `discogs recommend --apply` and `discogs apply <run-display-id>`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_recommend_apply.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discogs.api.client import DiscogsClient
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recommend_apply.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `src/discogs/recommend/apply.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_recommend_apply.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/recommend/apply.py tests/unit/test_recommend_apply.py
git commit -m "feat(recommend): apply_run — push picks with partial-failure capture"
```

---

## Task 5: Undo orchestrator — remove picks for a run

**Files:**
- Modify: `src/discogs/recommend/apply.py`
- Test: `tests/unit/test_recommend_undo.py`

Symmetric to apply: `undo_run(client, store, username, run_id, reason="undo") -> UndoReport`. Removes each *currently-applied* pick from the wantlist, updates the history row, captures partial failures.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_recommend_undo.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.recommend.apply import UndoReport, undo_run
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
    store.record_recommendation(run_id, 10, 0.5)  # never applied
    store.mark_recommendation_applied(run_id, 10, datetime.now(UTC))
    store.record_recommendation(run_id, 20, 0.5)  # never applied
    store.finish_run(run_id, summary={})

    with patch("discogs.recommend.apply.remove_from_wantlist") as rm:
        rm.return_value = RemoveResult(release_id=10, status="removed", error=None)
        report = undo_run(client, store, username="u", run_id=run_id)

    rm.assert_called_once()
    assert rm.call_args.kwargs["release_id"] == 10
    assert report.removed == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recommend_undo.py -v`
Expected: FAIL.

- [ ] **Step 3: Add to `src/discogs/recommend/apply.py`**

Update the imports at the top:

```python
from discogs.wantlist_writer import push_to_wantlist, remove_from_wantlist
```

Add the `UndoReport` dataclass:

```python
@dataclass
class UndoReport:
    run_id: str
    removed: int = 0
    skipped: int = 0
    errors: int = 0
    failed_picks: list[tuple[int, str]] = field(default_factory=list)
```

Add the function:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_recommend_undo.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/recommend/apply.py tests/unit/test_recommend_undo.py
git commit -m "feat(recommend): undo_run — remove picks with skipped/error distinction"
```

---

## Task 6: CLI — `discogs recommend --apply` (with confirm)

**Files:**
- Modify: `src/discogs/cli/commands/recommend.py`
- Test: `tests/unit/test_cli_recommend_apply.py`

Removes the Phase 2 UsageError. When `--apply` is passed: after writing the digest, call `apply_run(...)`; if `store.has_any_apply()` is False, prompt for `y/N` first (bypass with `--yes`).

The success/failure counts are appended to the digest output and printed to the console.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cli_recommend_apply.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from discogs.cli.__main__ import cli
from discogs.config import load_config
from discogs.recommend.apply import ApplyReport
from discogs.recommend.pipeline import RunResult


def _seed_config(home: Path) -> None:
    cfg_dir = home / ".discogs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text('[discogs]\ntoken = "t"\nusername = "u"')


def _empty_run(display_id: str = "2026-05-09-1830") -> RunResult:
    return RunResult(
        run_id="u-uuid", run_display_id=display_id, picks=[],
        seed_count=1, candidate_count=1, api_calls_used=0, wall_seconds=0.1,
        args={},
    )


def test_recommend_apply_first_time_requires_confirm(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.has_any_apply.return_value = False
    fake_store.last_applied_run_id.return_value = None

    with patch("discogs.cli.commands.recommend._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.recommend.run_recommend",
               return_value=_empty_run()), \
         patch("discogs.cli.commands.recommend.render_digest", return_value=""), \
         patch("discogs.cli.commands.recommend.apply_run") as ar:
        # Decline the prompt
        result = CliRunner().invoke(cli, ["recommend", "--apply"], input="n\n")

    assert ar.assert_not_called or not ar.called
    assert "skipped" in result.output.lower() or "cancelled" in result.output.lower()


def test_recommend_apply_yes_bypasses_confirm(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.has_any_apply.return_value = False

    with patch("discogs.cli.commands.recommend._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.recommend.run_recommend",
               return_value=_empty_run()), \
         patch("discogs.cli.commands.recommend.render_digest", return_value=""), \
         patch("discogs.cli.commands.recommend.apply_run") as ar:
        ar.return_value = ApplyReport(run_id="u-uuid", successes=0, failures=0)
        result = CliRunner().invoke(cli, ["recommend", "--apply", "--yes"])

    assert result.exit_code == 0, result.output
    ar.assert_called_once()


def test_recommend_apply_subsequent_no_confirm(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.has_any_apply.return_value = True  # already applied at least once before

    with patch("discogs.cli.commands.recommend._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.recommend.run_recommend",
               return_value=_empty_run()), \
         patch("discogs.cli.commands.recommend.render_digest", return_value=""), \
         patch("discogs.cli.commands.recommend.apply_run") as ar:
        ar.return_value = ApplyReport(run_id="u-uuid", successes=3, failures=0)
        result = CliRunner().invoke(cli, ["recommend", "--apply"])

    assert result.exit_code == 0, result.output
    ar.assert_called_once()
    assert "applied 3" in result.output.lower() or "3 successes" in result.output.lower()


def test_recommend_apply_reports_failures(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.has_any_apply.return_value = True

    with patch("discogs.cli.commands.recommend._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.recommend.run_recommend",
               return_value=_empty_run()), \
         patch("discogs.cli.commands.recommend.render_digest", return_value=""), \
         patch("discogs.cli.commands.recommend.apply_run") as ar:
        ar.return_value = ApplyReport(
            run_id="u-uuid", successes=2, failures=1,
            failed_picks=[(42, "HTTP 500")],
        )
        result = CliRunner().invoke(cli, ["recommend", "--apply", "--yes"])

    assert result.exit_code == 0  # partial success is still success
    assert "42" in result.output
    assert "HTTP 500" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cli_recommend_apply.py -v`
Expected: FAIL.

- [ ] **Step 3: Update `recommend_cmd` in `src/discogs/cli/commands/recommend.py`**

Add imports:

```python
from discogs.recommend.apply import apply_run
```

Change the `--apply` handler. The current code raises `UsageError`. Replace with the apply flow:

```python
@click.command("recommend")
@click.option("--max-recs", type=int, default=25, show_default=True,
              help="Top-N picks per run after diversity guard.")
@click.option("--budget", type=int, default=800, show_default=True,
              help="Hard cap on Discogs API calls during the graph walk.")
@click.option("--scope", type=click.Choice(["collection", "wantlist", "both"]),
              default="both", show_default=True,
              help="Which library half supplies seed artists.")
@click.option("--no-influences", "no_influences", is_flag=True,
              help="Skip Stage 1.5 (Claude-derived influence expansion).")
@click.option("--no-enrich", "no_enrich", is_flag=True,
              help="Skip Stage 4 (Claude editorial notes per pick).")
@click.option("--apply", "apply_flag", is_flag=True,
              help="Push picks to your Discogs wantlist after writing the digest.")
@click.option("--yes", "skip_confirm", is_flag=True,
              help="Bypass the first-apply confirmation prompt.")
def recommend_cmd(
    max_recs: int, budget: int, scope: str,
    no_influences: bool, no_enrich: bool,
    apply_flag: bool, skip_confirm: bool,
) -> None:
    """Generate top-N recommendations and write a markdown digest.

    With --apply, also push the picks to your Discogs wantlist.
    """
    client, store, cfg = _build_pipeline_context()
    try:
        # ...existing LLM build + run_recommend + render_digest + write file logic...
        # (keep all the existing code from Phase 3 up through writing digest_path)

        if apply_flag:
            if not store.has_any_apply() and not skip_confirm:
                if not click.confirm(
                    f"\nThis will push {len(result.picks)} releases to your "
                    f"Discogs wantlist. First-time apply requires confirmation. "
                    f"Proceed?",
                    default=False,
                ):
                    click.echo("Apply cancelled. Digest written but no wantlist changes.")
                    return

            click.echo(f"\nApplying picks for run {result.run_display_id}...")
            ar = apply_run(client, store, username=cfg.discogs_username, run_id=result.run_id)
            click.echo(f"  Applied {ar.successes} successes, {ar.failures} failures.")
            if ar.failures:
                click.echo("  Failed picks:")
                for rid, err in ar.failed_picks:
                    click.echo(f"    - release {rid}: {err}")
    finally:
        store.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_cli_recommend_apply.py tests/unit/test_cli_recommend.py -v`
Expected: all tests pass. The Phase 2 `test_recommend_does_not_apply` test (which expected `--apply` to raise UsageError) needs to be updated or removed — `--apply` now works. Update its body to verify `--apply --yes` runs successfully against an empty pick set instead.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/cli/commands/recommend.py tests/unit/test_cli_recommend_apply.py tests/unit/test_cli_recommend.py
git commit -m "feat(cli): --apply pushes to wantlist; --yes bypasses first-apply confirm"
```

---

## Task 7: CLI — `discogs apply <run-display-id>`

**Files:**
- Create: `src/discogs/cli/commands/apply_cmd.py`
- Modify: `src/discogs/cli/__main__.py`
- Test: `tests/unit/test_cli_apply.py`

Standalone command for the review-then-commit flow: user reviews a digest from a previous `discogs recommend` run, then commits with `discogs apply 2026-05-09-1830`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cli_apply.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from discogs.cli.__main__ import cli
from discogs.config import load_config
from discogs.recommend.apply import ApplyReport


def _seed_config(home: Path) -> None:
    cfg_dir = home / ".discogs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text('[discogs]\ntoken = "t"\nusername = "u"')


def test_apply_command_resolves_display_id(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.get_run_by_display_id.return_value = "u-uuid"
    fake_store.has_any_apply.return_value = True

    with patch("discogs.cli.commands.apply_cmd._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.apply_cmd.apply_run") as ar:
        ar.return_value = ApplyReport(run_id="u-uuid", successes=5, failures=0)
        result = CliRunner().invoke(cli, ["apply", "2026-05-09-1830"])

    assert result.exit_code == 0, result.output
    fake_store.get_run_by_display_id.assert_called_once_with("2026-05-09-1830")
    ar.assert_called_once()
    assert ar.call_args.kwargs["run_id"] == "u-uuid"


def test_apply_command_unknown_display_id(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.get_run_by_display_id.return_value = None

    with patch("discogs.cli.commands.apply_cmd._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())):
        result = CliRunner().invoke(cli, ["apply", "nonexistent"])

    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "no run" in result.output.lower()


def test_apply_command_first_time_prompts(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.get_run_by_display_id.return_value = "u-uuid"
    fake_store.has_any_apply.return_value = False
    fake_store.get_recommendations_for_run.return_value = [{"release_id": 1}]

    with patch("discogs.cli.commands.apply_cmd._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.apply_cmd.apply_run") as ar:
        result = CliRunner().invoke(cli, ["apply", "2026-05-09-1830"], input="n\n")

    ar.assert_not_called()
    assert "cancel" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cli_apply.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `src/discogs/cli/commands/apply_cmd.py`**

```python
"""`discogs apply <run-display-id>` — push a previous run's picks to wantlist."""
from __future__ import annotations

import click

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config, load_config
from discogs.recommend.apply import apply_run


def _build_pipeline_context() -> tuple[DiscogsClient, CacheStore, Config]:
    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store)
    return client, store, cfg


@click.command("apply")
@click.argument("run_display_id")
@click.option("--yes", "skip_confirm", is_flag=True,
              help="Bypass the first-apply confirmation prompt.")
def apply_cmd(run_display_id: str, skip_confirm: bool) -> None:
    """Push the picks of run RUN_DISPLAY_ID to your Discogs wantlist."""
    client, store, cfg = _build_pipeline_context()
    try:
        run_id = store.get_run_by_display_id(run_display_id)
        if run_id is None:
            raise click.ClickException(f"No run found for display id {run_display_id!r}.")

        picks = store.get_recommendations_for_run(run_id)
        if not picks:
            click.echo(f"Run {run_display_id} has no picks to apply.")
            return

        if not store.has_any_apply() and not skip_confirm:
            if not click.confirm(
                f"\nThis will push {len(picks)} releases from run {run_display_id} "
                f"to your Discogs wantlist. First-time apply requires confirmation. "
                f"Proceed?",
                default=False,
            ):
                click.echo("Cancelled.")
                return

        report = apply_run(client, store, username=cfg.discogs_username, run_id=run_id)
        click.echo(
            f"Applied run {run_display_id}: "
            f"{report.successes} successes, {report.failures} failures, "
            f"{report.skipped_already_applied} already-applied skipped."
        )
        if report.failures:
            click.echo("Failed picks:")
            for rid, err in report.failed_picks:
                click.echo(f"  - release {rid}: {err}")
    finally:
        store.close()
```

- [ ] **Step 4: Register the command in `src/discogs/cli/__main__.py`**

Add the import:

```python
from discogs.cli.commands.apply_cmd import apply_cmd
```

Add the registration:

```python
cli.add_command(apply_cmd)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_cli_apply.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/discogs/cli/commands/apply_cmd.py src/discogs/cli/__main__.py tests/unit/test_cli_apply.py
git commit -m "feat(cli): discogs apply <run-display-id> command"
```

---

## Task 8: CLI — `discogs undo-last-batch` and `discogs undo <run-id>`

**Files:**
- Create: `src/discogs/cli/commands/undo_cmd.py`
- Modify: `src/discogs/cli/__main__.py`
- Test: `tests/unit/test_cli_undo.py`

Two related commands:
- `discogs undo-last-batch` — looks up `store.last_applied_run_id()`, undoes it
- `discogs undo <run-display-id>` — undoes a specific run

Both share the same orchestrator and report shape.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cli_undo.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from discogs.cli.__main__ import cli
from discogs.config import load_config
from discogs.recommend.apply import UndoReport


def _seed_config(home: Path) -> None:
    cfg_dir = home / ".discogs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text('[discogs]\ntoken = "t"\nusername = "u"')


def test_undo_last_batch_resolves_via_helper(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.last_applied_run_id.return_value = "u-uuid"

    with patch("discogs.cli.commands.undo_cmd._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.undo_cmd.undo_run") as ur:
        ur.return_value = UndoReport(run_id="u-uuid", removed=3, skipped=0, errors=0)
        result = CliRunner().invoke(cli, ["undo-last-batch", "--yes"])

    assert result.exit_code == 0, result.output
    ur.assert_called_once()
    assert ur.call_args.kwargs["run_id"] == "u-uuid"


def test_undo_last_batch_no_history(tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.last_applied_run_id.return_value = None

    with patch("discogs.cli.commands.undo_cmd._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())):
        result = CliRunner().invoke(cli, ["undo-last-batch"])

    assert result.exit_code != 0
    assert "no" in result.output.lower() and ("apply" in result.output.lower()
                                              or "history" in result.output.lower())


def test_undo_specific_run(tmp_path: Path,
                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.get_run_by_display_id.return_value = "u-uuid"

    with patch("discogs.cli.commands.undo_cmd._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.undo_cmd.undo_run") as ur:
        ur.return_value = UndoReport(run_id="u-uuid", removed=2, skipped=1, errors=0)
        result = CliRunner().invoke(cli, ["undo", "2026-05-09-1830", "--yes"])

    assert result.exit_code == 0, result.output
    fake_store.get_run_by_display_id.assert_called_once_with("2026-05-09-1830")
    assert "removed 2" in result.output.lower()
    assert "skipped 1" in result.output.lower()


def test_undo_specific_run_unknown(tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.get_run_by_display_id.return_value = None

    with patch("discogs.cli.commands.undo_cmd._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())):
        result = CliRunner().invoke(cli, ["undo", "nonexistent", "--yes"])

    assert result.exit_code != 0


def test_undo_prompts_unless_yes(tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_store = MagicMock()
    fake_store.last_applied_run_id.return_value = "u-uuid"
    fake_store.get_recommendations_for_run.return_value = [{"release_id": 1}, {"release_id": 2}]

    with patch("discogs.cli.commands.undo_cmd._build_pipeline_context",
               return_value=(MagicMock(), fake_store, load_config())), \
         patch("discogs.cli.commands.undo_cmd.undo_run") as ur:
        result = CliRunner().invoke(cli, ["undo-last-batch"], input="n\n")

    ur.assert_not_called()
    assert "cancel" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cli_undo.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `src/discogs/cli/commands/undo_cmd.py`**

```python
"""`discogs undo-last-batch` and `discogs undo <run-display-id>` commands."""
from __future__ import annotations

import click

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config, load_config
from discogs.recommend.apply import undo_run


def _build_pipeline_context() -> tuple[DiscogsClient, CacheStore, Config]:
    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store)
    return client, store, cfg


def _confirm_and_undo(
    client: DiscogsClient, store: CacheStore, cfg: Config, *,
    run_id: str, label: str, skip_confirm: bool,
) -> None:
    picks = store.get_recommendations_for_run(run_id)
    if not skip_confirm:
        if not click.confirm(
            f"\nUndo will remove {len(picks)} picks from run {label} "
            f"from your Discogs wantlist. Proceed?",
            default=False,
        ):
            click.echo("Cancelled.")
            return

    report = undo_run(client, store, username=cfg.discogs_username, run_id=run_id)
    click.echo(
        f"Undone run {label}: "
        f"removed {report.removed}, skipped {report.skipped}, errors {report.errors}."
    )
    if report.errors:
        click.echo("Failed removals:")
        for rid, err in report.failed_picks:
            click.echo(f"  - release {rid}: {err}")


@click.command("undo-last-batch")
@click.option("--yes", "skip_confirm", is_flag=True, help="Bypass confirmation.")
def undo_last_batch_cmd(skip_confirm: bool) -> None:
    """Undo the most recently applied batch."""
    client, store, cfg = _build_pipeline_context()
    try:
        run_id = store.last_applied_run_id()
        if run_id is None:
            raise click.ClickException(
                "No applied runs in history — nothing to undo."
            )
        _confirm_and_undo(client, store, cfg, run_id=run_id, label="latest",
                          skip_confirm=skip_confirm)
    finally:
        store.close()


@click.command("undo")
@click.argument("run_display_id")
@click.option("--yes", "skip_confirm", is_flag=True, help="Bypass confirmation.")
def undo_cmd(run_display_id: str, skip_confirm: bool) -> None:
    """Undo a specific run by display id."""
    client, store, cfg = _build_pipeline_context()
    try:
        run_id = store.get_run_by_display_id(run_display_id)
        if run_id is None:
            raise click.ClickException(f"No run found for display id {run_display_id!r}.")
        _confirm_and_undo(client, store, cfg, run_id=run_id, label=run_display_id,
                          skip_confirm=skip_confirm)
    finally:
        store.close()
```

- [ ] **Step 4: Register both commands in `src/discogs/cli/__main__.py`**

```python
from discogs.cli.commands.undo_cmd import undo_cmd, undo_last_batch_cmd
# ...
cli.add_command(undo_cmd)
cli.add_command(undo_last_batch_cmd)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_cli_undo.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/discogs/cli/commands/undo_cmd.py src/discogs/cli/__main__.py tests/unit/test_cli_undo.py
git commit -m "feat(cli): discogs undo-last-batch and discogs undo <display-id>"
```

---

## Task 9: Digest reports apply / undo outcomes

**Files:**
- Modify: `src/discogs/recommend/digest.py`
- Test: `tests/unit/test_recommend_digest_apply.py`

When the digest is regenerated for a run that has applied / undone history (e.g. `discogs apply <id>` is called and we want the digest to reflect the apply outcome), the per-pick section gets an "Applied:" or "Removed:" line.

For Phase 4 v1 we don't regenerate digests — but we DO want the initial `recommend --apply` digest to include the apply report. Add an optional `apply_report: ApplyReport | None = None` parameter to `render_digest`. When present, append a section after "Run stats" titled "Apply outcome".

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_recommend_digest_apply.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.recommend.apply import ApplyReport
from discogs.recommend.digest import render_digest
from discogs.recommend.pipeline import RunResult


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def test_digest_includes_apply_outcome_when_provided(store: CacheStore) -> None:
    result = RunResult(
        run_id="u", run_display_id="2026-05-09-1830", picks=[],
        seed_count=0, candidate_count=0, api_calls_used=0, wall_seconds=0.1,
        args={},
    )
    apply_report = ApplyReport(
        run_id="u", successes=5, failures=2,
        failed_picks=[(42, "HTTP 500"), (43, "HTTP 429 rate limit")],
    )
    md = render_digest(store, result, apply_report=apply_report)
    assert "Apply outcome" in md
    assert "5 successes" in md
    assert "2 failures" in md
    assert "42" in md and "HTTP 500" in md


def test_digest_omits_apply_section_when_no_report(store: CacheStore) -> None:
    result = RunResult(
        run_id="u", run_display_id="2026-05-09-1830", picks=[],
        seed_count=0, candidate_count=0, api_calls_used=0, wall_seconds=0.1,
        args={},
    )
    md = render_digest(store, result)
    assert "Apply outcome" not in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recommend_digest_apply.py -v`
Expected: FAIL.

- [ ] **Step 3: Update `render_digest` in `src/discogs/recommend/digest.py`**

Add an import:

```python
from discogs.recommend.apply import ApplyReport
```

Update the signature:

```python
def render_digest(
    store: CacheStore,
    result: RunResult,
    *,
    apply_report: ApplyReport | None = None,
) -> str:
```

After the "Run stats" block, append:

```python
    if apply_report is not None:
        lines.append("")
        lines.append("## Apply outcome\n")
        lines.append(f"- {apply_report.successes} successes")
        lines.append(f"- {apply_report.failures} failures")
        if apply_report.skipped_already_applied:
            lines.append(f"- {apply_report.skipped_already_applied} skipped (already applied)")
        if apply_report.failed_picks:
            lines.append("\n**Failed picks:**")
            for rid, err in apply_report.failed_picks:
                lines.append(f"- release `{rid}`: {err}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_recommend_digest_apply.py tests/unit/test_recommend_digest.py -v`
Expected: all tests pass.

- [ ] **Step 5: Update `recommend_cmd` to pass apply_report into render_digest**

In `src/discogs/cli/commands/recommend.py`, after the `apply_run(...)` call when `--apply` is set, the digest has already been rendered + written. We want to re-render with the apply outcome and overwrite. Insert after the "Failed picks" loop:

```python
            digest_md_with_apply = render_digest(store, result, apply_report=ar)
            digest_path.write_text(digest_md_with_apply)
            click.echo(f"  Updated digest with apply outcome: {digest_path}")
```

- [ ] **Step 6: Commit**

```bash
git add src/discogs/recommend/digest.py src/discogs/cli/commands/recommend.py tests/unit/test_recommend_digest_apply.py
git commit -m "feat(digest): optional apply_report block; CLI updates digest after --apply"
```

---

## Task 10: Phase 4 verification + smoke test + README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the full unit test suite**

Run: `pytest tests/unit/ -v`
Expected: all tests pass.

- [ ] **Step 2: Run lint + types**

```bash
ruff check src/ tests/
mypy src/
```

Expected: 0 errors each.

- [ ] **Step 3: Smoke-test (RECOMMENDED — Phase 4 writes to your real wantlist!)**

Make a small test run with very narrow scope so the wantlist impact is bounded:

```bash
discogs recommend --max-recs 2 --budget 50 --no-influences --no-enrich --apply --yes
discogs status
discogs undo-last-batch --yes
```

This should:
1. Generate up to 2 picks, write a digest, push 2 releases to your wantlist
2. Show the run in `discogs status`
3. Remove the same 2 releases via undo

If the smoke test passes (and your wantlist is in the same state at the end as the beginning), Phase 4 is functional.

- [ ] **Step 4: Update README**

In the Commands table, add the three new commands:

```markdown
| `discogs recommend --apply [--yes]` | Generate picks AND push to wantlist. First-ever apply requires interactive confirm; `--yes` bypasses for scripts. |
| `discogs apply <run-display-id> [--yes]` | Apply a previously-generated run's picks to your wantlist. |
| `discogs undo-last-batch [--yes]` | Remove the most recently applied batch from your wantlist. |
| `discogs undo <run-display-id> [--yes]` | Remove a specific run's applied picks from your wantlist. |
```

In the Quickstart section, append a new subsection:

```markdown
## Apply / Undo (Phase 4)

The full review-then-commit flow:

```bash
discogs recommend                      # writes digest, no wantlist changes
less ~/.discogs/digests/2026-05-09-...md  # review picks
discogs apply 2026-05-09-091125        # commits to wantlist (first time prompts y/N)
discogs undo-last-batch                 # if you change your mind
```

Or the one-shot variant:

```bash
discogs recommend --apply --yes
```

Picks are tracked in `recommendation_history`; the same release is never re-recommended across runs (unless you pass `--allow-rerecommend`, planned for a later phase).
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: README describes apply/undo flow (Phase 4)"
```

---

## Phase 4 verification checklist

- [ ] `pytest tests/unit/` — all tests pass.
- [ ] `ruff check src/ tests/` — 0 errors.
- [ ] `mypy src/` — 0 errors.
- [ ] `discogs --help` lists `apply`, `undo-last-batch`, `undo`.
- [ ] `discogs recommend --help` shows `--apply` and `--yes` flags.
- [ ] **Live smoke test** (recommended): `discogs recommend --max-recs 2 --apply --yes` then `discogs undo-last-batch --yes` leaves wantlist unchanged.

---

## Self-review notes

- **Spec coverage:**
  - `discogs recommend --apply` (Spec §"CLI surface") → Task 6
  - `discogs apply <run-display-id>` (Spec §"CLI surface") → Task 7
  - `discogs undo-last-batch` and `discogs undo <run-id>` (Spec §"CLI surface") → Task 8
  - First-apply confirmation (Spec §"Safety mechanisms") → Task 6 + Task 7
  - Hard cap, dedup, dry-run-by-default (Spec §"Safety mechanisms") — already in place from Phases 2/3, Phase 4 just preserves them
  - Partial-failure handling (Spec §"Error handling") → Tasks 4, 6, 9
  - `recommendation_history.applied_to_wantlist` / `applied_at` / `removed_at` / `removed_reason` updates (Spec §"Storage") → Task 1
  - Wantlist audit trail — the `recommendation_history` table is the single source of truth (no separate `wantlist_audit` table per the spec's "no separate audit table" decision)

- **Out of scope, deferred:**
  - `--allow-rerecommend` flag → could go in Phase 5 polish if desired
  - Webhook / scheduled run support → out of scope; cron-based scheduling already works
  - Wantlist priority/notes editing → not core to "push and undo"

- **Risk highlights:**
  - **Live wantlist write surface**: the smoke test in Task 10 actually modifies the user's Discogs account. Reviewer / executor should verify the final state matches the initial state before declaring Phase 4 done.
  - **`client._store.increment_api_calls(1)`** in `wantlist_writer.py` reaches into a private attribute. Acceptable trade-off — see the NOTE in Task 2.
  - **404 detection in `remove_from_wantlist`**: we string-match on `"404"` in the exception message. If python3-discogs-client changes its error format (HTTPError subclasses, structured codes), we may misclassify a 404 as a generic error. Verify by triggering a real removal of a non-wantlisted release during smoke testing.
  - **Confirmation UX**: Click's `confirm()` defaults to abort on EOF, which is correct for non-interactive contexts (cron, CI) — they should be passing `--yes` anyway. Worth verifying with `echo | discogs recommend --apply` that the right thing happens (cancels rather than hangs).
