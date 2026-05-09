# Discogs Recommender — Phase 2: Recommendation MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver `discogs recommend` (dry-run only) — a working recommendation pipeline that walks the Discogs credit graph from seed artists, scores candidates with 8 sub-scores, and writes a top-N markdown digest. Phase 1's sync layer is the input; the wantlist remains untouched.

**Architecture:** The pipeline is five stages composed in `recommend/pipeline.py`: (1) seed selection from cached collection+wantlist, (2) bounded BFS through the credit graph using new release/artist/label fetchers, (3) scoring via weighted sum of 8 normalized sub-scores, (5) final selection with diversity guard and history filter, then digest rendering. Each stage is its own module so they're independently testable; the pipeline is a thin composer.

**Tech Stack:** Same as Phase 1 — Python 3.11+, python3-discogs-client, click, rich, pydantic, sqlite3.

**Spec reference:** `docs/superpowers/specs/2026-05-08-discogs-recommender-design.md` — this plan implements Build Sequence steps 4 (graph walk) + 6 (scoring, sans `influence_chain_score`) + 8 (digest) + 9-partial (the `discogs recommend` dry-run path; `--apply` is Phase 4).

**Phase 2 design decisions** (recorded from brainstorming, before writing this plan):

1. **`influence_chain_score` always = 0 in Phase 2.** Total Phase 2 scores fall in `[0, 0.85]` since that sub-score's weight (0.15) goes unused. Phase 3 will populate `artist_influences` and naturally extend the range to `[0, 1]`. Cleaner than renormalizing weights twice.

2. **Digest format** — markdown with: header (run id, seed count, candidate count); per-pick section with title/year, label, format, community stats, styles, and a "Connection:" line tracing seed→candidate; trailing run stats (API calls, wall time, diversity).

3. **Per-artist discography cap** — when fetching an artist's releases, take page-1 only (50 results), persist to `artist_top_releases` cache (TTL 30 days). For the graph walk we work with the first 25 by Discogs' default ordering. Never paginate past page 1.

**Out of scope (deferred):**
- Stage 1.5 influence expansion — Phase 3
- Stage 4 LLM editorial enrichment — Phase 3
- `--apply`, `discogs apply`, `discogs undo-last-batch` — Phase 4

---

## Task 1: Cache CRUD for artists

**Files:**
- Modify: `src/discogs/cache/store.py`
- Test: `tests/unit/test_cache_artists.py`

Add `upsert_artist`, `get_artist`, `artist_age` to `CacheStore`. Mirrors the release CRUD pattern from Phase 1 Task 5.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cache_artists.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import Artist


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def make_artist(aid: int = 1, *, fetched_at: datetime | None = None) -> Artist:
    return Artist(
        id=aid,
        name="Pharoah Sanders",
        profile="American jazz saxophonist",
        fetched_at=fetched_at or datetime.now(UTC),
    )


def test_upsert_and_get_artist(store: CacheStore) -> None:
    store.upsert_artist(make_artist())
    fetched = store.get_artist(1)
    assert fetched is not None
    assert fetched.name == "Pharoah Sanders"


def test_upsert_replaces_existing(store: CacheStore) -> None:
    store.upsert_artist(make_artist(aid=1))
    store.upsert_artist(
        Artist(id=1, name="Updated", profile=None, fetched_at=datetime.now(UTC))
    )
    fetched = store.get_artist(1)
    assert fetched is not None
    assert fetched.name == "Updated"


def test_get_artist_returns_none_when_missing(store: CacheStore) -> None:
    assert store.get_artist(999) is None


def test_artist_age(store: CacheStore) -> None:
    fetched_at = datetime.now(UTC) - timedelta(seconds=42)
    store.upsert_artist(make_artist(fetched_at=fetched_at))
    age = store.artist_age(1)
    assert age is not None
    assert 40 <= age.total_seconds() <= 60


def test_artist_age_returns_none_when_missing(store: CacheStore) -> None:
    assert store.artist_age(999) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cache_artists.py -v`
Expected: FAIL — methods not implemented.

- [ ] **Step 3: Update the TYPE_CHECKING import block in `src/discogs/cache/store.py`**

Locate the existing block:

```python
if TYPE_CHECKING:
    from discogs.models import CollectionItem, Release, WantlistItem
```

Replace with:

```python
if TYPE_CHECKING:
    from discogs.models import Artist, CollectionItem, Release, WantlistItem
```

- [ ] **Step 4: Append the artist CRUD methods to `CacheStore` in `src/discogs/cache/store.py`**

Append at the end of the class:

```python
    def upsert_artist(self, artist: Artist) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO artists (id, name, profile, fetched_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    profile=excluded.profile,
                    fetched_at=excluded.fetched_at
                """,
                (artist.id, artist.name, artist.profile, artist.fetched_at.isoformat()),
            )

    def get_artist(self, artist_id: int) -> Artist | None:
        from discogs.models import Artist
        row = self.conn.execute(
            "SELECT * FROM artists WHERE id = ?", (artist_id,)
        ).fetchone()
        if row is None:
            return None
        return Artist(
            id=row["id"],
            name=row["name"],
            profile=row["profile"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
        )

    def artist_age(self, artist_id: int) -> timedelta | None:
        row = self.conn.execute(
            "SELECT fetched_at FROM artists WHERE id = ?", (artist_id,)
        ).fetchone()
        if row is None:
            return None
        return datetime.now(UTC) - datetime.fromisoformat(row["fetched_at"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_cache_artists.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/discogs/cache/store.py tests/unit/test_cache_artists.py
git commit -m "feat(cache): artist upsert/get/age"
```

---

## Task 2: Cache CRUD for labels

**Files:**
- Modify: `src/discogs/cache/store.py`
- Test: `tests/unit/test_cache_labels.py`

Same shape as Task 1 but for the `labels` table. `releases_count` drives `label_obscurity_score`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cache_labels.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import Label


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def make_label(lid: int = 1, *, fetched_at: datetime | None = None) -> Label:
    return Label(
        id=lid,
        name="Impulse!",
        parent_label="ABC Records",
        releases_count=200,
        fetched_at=fetched_at or datetime.now(UTC),
    )


def test_upsert_and_get_label(store: CacheStore) -> None:
    store.upsert_label(make_label())
    fetched = store.get_label(1)
    assert fetched is not None
    assert fetched.name == "Impulse!"
    assert fetched.releases_count == 200


def test_upsert_replaces_existing(store: CacheStore) -> None:
    store.upsert_label(make_label(lid=1))
    store.upsert_label(
        Label(id=1, name="Impulse!", parent_label=None, releases_count=999, fetched_at=datetime.now(UTC))
    )
    fetched = store.get_label(1)
    assert fetched is not None
    assert fetched.releases_count == 999


def test_get_label_returns_none_when_missing(store: CacheStore) -> None:
    assert store.get_label(999) is None


def test_label_age(store: CacheStore) -> None:
    fetched_at = datetime.now(UTC) - timedelta(seconds=42)
    store.upsert_label(make_label(fetched_at=fetched_at))
    age = store.label_age(1)
    assert age is not None
    assert 40 <= age.total_seconds() <= 60


def test_label_age_returns_none_when_missing(store: CacheStore) -> None:
    assert store.label_age(999) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cache_labels.py -v`
Expected: FAIL.

- [ ] **Step 3: Extend the TYPE_CHECKING import block in `src/discogs/cache/store.py`**

Replace the existing block:

```python
if TYPE_CHECKING:
    from discogs.models import Artist, CollectionItem, Release, WantlistItem
```

with:

```python
if TYPE_CHECKING:
    from discogs.models import Artist, CollectionItem, Label, Release, WantlistItem
```

- [ ] **Step 4: Append the label CRUD methods to `CacheStore`**

```python
    def upsert_label(self, label: Label) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO labels (id, name, parent_label, releases_count, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    parent_label=excluded.parent_label,
                    releases_count=excluded.releases_count,
                    fetched_at=excluded.fetched_at
                """,
                (
                    label.id, label.name, label.parent_label,
                    label.releases_count, label.fetched_at.isoformat(),
                ),
            )

    def get_label(self, label_id: int) -> Label | None:
        from discogs.models import Label
        row = self.conn.execute(
            "SELECT * FROM labels WHERE id = ?", (label_id,)
        ).fetchone()
        if row is None:
            return None
        return Label(
            id=row["id"],
            name=row["name"],
            parent_label=row["parent_label"],
            releases_count=row["releases_count"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
        )

    def label_age(self, label_id: int) -> timedelta | None:
        row = self.conn.execute(
            "SELECT fetched_at FROM labels WHERE id = ?", (label_id,)
        ).fetchone()
        if row is None:
            return None
        return datetime.now(UTC) - datetime.fromisoformat(row["fetched_at"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_cache_labels.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/discogs/cache/store.py tests/unit/test_cache_labels.py
git commit -m "feat(cache): label upsert/get/age"
```

---

## Task 3: Cache CRUD for credits + release_labels

**Files:**
- Modify: `src/discogs/cache/store.py`
- Test: `tests/unit/test_cache_credits.py`

Replaces all credits / release-label rows for a given release atomically. The graph walk reads via `get_release_credits` and `get_release_label_ids`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cache_credits.py`:

```python
from collections.abc import Iterator
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import Credit


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def test_replace_release_credits_inserts(store: CacheStore) -> None:
    store.replace_release_credits(
        release_id=10,
        credits=[
            Credit(release_id=10, artist_id=1, role="Producer"),
            Credit(release_id=10, artist_id=2, role="Engineer"),
        ],
    )
    fetched = store.get_release_credits(10)
    assert {(c.artist_id, c.role) for c in fetched} == {(1, "Producer"), (2, "Engineer")}


def test_replace_release_credits_overwrites(store: CacheStore) -> None:
    store.replace_release_credits(
        release_id=10,
        credits=[Credit(release_id=10, artist_id=1, role="Producer")],
    )
    store.replace_release_credits(
        release_id=10,
        credits=[Credit(release_id=10, artist_id=99, role="Bass")],
    )
    fetched = store.get_release_credits(10)
    assert {c.artist_id for c in fetched} == {99}


def test_get_release_credits_empty_when_missing(store: CacheStore) -> None:
    assert store.get_release_credits(999) == []


def test_replace_release_labels_inserts(store: CacheStore) -> None:
    store.replace_release_labels(
        release_id=10,
        labels=[(101, "AS-9181"), (102, None)],
    )
    fetched = store.get_release_label_ids(10)
    assert set(fetched) == {101, 102}


def test_replace_release_labels_overwrites(store: CacheStore) -> None:
    store.replace_release_labels(release_id=10, labels=[(101, "X")])
    store.replace_release_labels(release_id=10, labels=[(202, "Y")])
    assert set(store.get_release_label_ids(10)) == {202}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cache_credits.py -v`
Expected: FAIL.

- [ ] **Step 3: Extend the TYPE_CHECKING import block in `src/discogs/cache/store.py`**

Replace the existing block with:

```python
if TYPE_CHECKING:
    from discogs.models import Artist, CollectionItem, Credit, Label, Release, WantlistItem
```

- [ ] **Step 4: Append the credit / release_label methods to `CacheStore`**

```python
    def replace_release_credits(self, release_id: int, credits: list[Credit]) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM release_credits WHERE release_id = ?", (release_id,)
            )
            self.conn.executemany(
                "INSERT INTO release_credits (release_id, artist_id, role) VALUES (?, ?, ?)",
                [(c.release_id, c.artist_id, c.role) for c in credits],
            )

    def get_release_credits(self, release_id: int) -> list[Credit]:
        from discogs.models import Credit
        rows = self.conn.execute(
            "SELECT release_id, artist_id, role FROM release_credits WHERE release_id = ?",
            (release_id,),
        )
        return [
            Credit(release_id=r["release_id"], artist_id=r["artist_id"], role=r["role"])
            for r in rows
        ]

    def replace_release_labels(
        self, release_id: int, labels: list[tuple[int, str | None]]
    ) -> None:
        """labels = list of (label_id, catalog_number)."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM release_labels WHERE release_id = ?", (release_id,)
            )
            self.conn.executemany(
                "INSERT INTO release_labels (release_id, label_id, catalog_number) "
                "VALUES (?, ?, ?)",
                [(release_id, lid, cat) for lid, cat in labels],
            )

    def get_release_label_ids(self, release_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT label_id FROM release_labels WHERE release_id = ?", (release_id,)
        )
        return [int(r["label_id"]) for r in rows]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_cache_credits.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/discogs/cache/store.py tests/unit/test_cache_credits.py
git commit -m "feat(cache): release_credits + release_labels replace/get"
```

---

## Task 4: Cache CRUD for runs + recommendation_history

**Files:**
- Modify: `src/discogs/cache/store.py`
- Test: `tests/unit/test_cache_runs.py`

Adds `start_run`, `finish_run`, `record_recommendation`, `previously_recommended_release_ids`. The recommendation_history FK to runs is enforced; `start_run` must precede `record_recommendation`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cache_runs.py`:

```python
import json
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

    run_b, _ = store.start_run(args={})
    store.record_recommendation(run_b, release_id=3, score=0.7)

    assert store.previously_recommended_release_ids() == {1, 2, 3}


def test_display_id_uses_utc_minute(store: CacheStore) -> None:
    run_id, display_id = store.start_run(args={})
    # YYYY-MM-DD-HHMM, e.g. 2026-05-08-1830
    assert len(display_id) == len("YYYY-MM-DD-HHMM")
    assert display_id[4] == "-" and display_id[7] == "-" and display_id[10] == "-"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cache_runs.py -v`
Expected: FAIL.

- [ ] **Step 3: Append the run / recommendation methods to `CacheStore`**

Add `import json` and `import uuid` at the top of `src/discogs/cache/store.py` if not present (the existing file already imports `json`; add `uuid`).

Append to the end of the class:

```python
    def start_run(self, args: dict[str, object]) -> tuple[str, str]:
        """Insert a new row in `runs`, return (uuid, display_id).

        display_id is YYYY-MM-DD-HHMM in UTC and serves as the human handle
        used by `discogs apply <run-id>` (Phase 4).
        """
        run_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        display_id = now.strftime("%Y-%m-%d-%H%M")
        with self.conn:
            self.conn.execute(
                "INSERT INTO runs (id, display_id, started_at, args_json) VALUES (?, ?, ?, ?)",
                (run_id, display_id, now.isoformat(), json.dumps(args)),
            )
        return run_id, display_id

    def finish_run(self, run_id: str, summary: dict[str, object]) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET finished_at = ?, summary_json = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), json.dumps(summary), run_id),
            )

    def record_recommendation(
        self, run_id: str, release_id: int, score: float
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO recommendation_history (release_id, run_id, score) VALUES (?, ?, ?)",
                (release_id, run_id, score),
            )

    def previously_recommended_release_ids(self) -> set[int]:
        return {
            int(r["release_id"])
            for r in self.conn.execute("SELECT DISTINCT release_id FROM recommendation_history")
        }
```

Add `import uuid` to the existing top-of-file imports (alphabetically, near `import json`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_cache_runs.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/cache/store.py tests/unit/test_cache_runs.py
git commit -m "feat(cache): runs + recommendation_history CRUD"
```

---

## Task 5: Cache CRUD for artist_top_releases

**Files:**
- Modify: `src/discogs/cache/store.py`
- Test: `tests/unit/test_cache_artist_top_releases.py`

Caches the top-K release IDs for an artist (per spec, TTL 30 days). Used by `fetch_artist_releases` (Task 9) so subsequent runs against the same seeds are nearly free.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cache_artist_top_releases.py`:

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


def test_replace_and_get_artist_top_releases(store: CacheStore) -> None:
    store.replace_artist_top_releases(artist_id=1, release_ids=[101, 102, 103])
    rids = store.get_artist_top_release_ids(1)
    assert rids == [101, 102, 103]


def test_replace_overwrites(store: CacheStore) -> None:
    store.replace_artist_top_releases(artist_id=1, release_ids=[101, 102])
    store.replace_artist_top_releases(artist_id=1, release_ids=[201])
    assert store.get_artist_top_release_ids(1) == [201]


def test_get_returns_empty_when_missing(store: CacheStore) -> None:
    assert store.get_artist_top_release_ids(999) == []


def test_artist_top_releases_age(store: CacheStore) -> None:
    store.replace_artist_top_releases(artist_id=1, release_ids=[101])
    age = store.artist_top_releases_age(1)
    assert age is not None
    assert age.total_seconds() < 5


def test_artist_top_releases_age_none_when_missing(store: CacheStore) -> None:
    assert store.artist_top_releases_age(999) is None


def test_old_entries_count_against_age(store: CacheStore) -> None:
    # Manually insert with old timestamp to verify age computation
    old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    with store.conn:
        store.conn.execute(
            "INSERT INTO artist_top_releases (artist_id, release_id, rank, fetched_at) VALUES (?, ?, ?, ?)",
            (1, 101, 0, old),
        )
    age = store.artist_top_releases_age(1)
    assert age is not None
    assert age > timedelta(days=30)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cache_artist_top_releases.py -v`
Expected: FAIL.

- [ ] **Step 3: Append methods to `CacheStore`**

```python
    def replace_artist_top_releases(self, artist_id: int, release_ids: list[int]) -> None:
        now = datetime.now(UTC).isoformat()
        with self.conn:
            self.conn.execute(
                "DELETE FROM artist_top_releases WHERE artist_id = ?", (artist_id,)
            )
            self.conn.executemany(
                "INSERT INTO artist_top_releases (artist_id, release_id, rank, fetched_at) "
                "VALUES (?, ?, ?, ?)",
                [(artist_id, rid, rank, now) for rank, rid in enumerate(release_ids)],
            )

    def get_artist_top_release_ids(self, artist_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT release_id FROM artist_top_releases "
            "WHERE artist_id = ? ORDER BY rank ASC",
            (artist_id,),
        )
        return [int(r["release_id"]) for r in rows]

    def artist_top_releases_age(self, artist_id: int) -> timedelta | None:
        row = self.conn.execute(
            "SELECT MIN(fetched_at) AS oldest FROM artist_top_releases WHERE artist_id = ?",
            (artist_id,),
        ).fetchone()
        if row is None or row["oldest"] is None:
            return None
        return datetime.now(UTC) - datetime.fromisoformat(row["oldest"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_cache_artist_top_releases.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/cache/store.py tests/unit/test_cache_artist_top_releases.py
git commit -m "feat(cache): artist_top_releases replace/get/age"
```

---

## Task 6: API fetch_release (full, with credits + labels)

**Files:**
- Create: `src/discogs/api/releases.py`
- Test: `tests/unit/test_api_releases.py`

Fetches a release from Discogs and persists everything we'll need for scoring: the release row, its formats/styles/genres, its credits (extra-artists + tracklist artists), and its labels. Cache TTL: 30 days. Returns the persisted `Release`.

The Discogs "extraartists" list contains personnel with roles. We harvest both top-level and per-track extra-artists.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_api_releases.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import DiscogsClient
from discogs.api.releases import fetch_release
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[Config, CacheStore, DiscogsClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db", daily_api_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    yield cfg, store, client
    store.close()


def _fake_raw_release(rid: int = 100) -> MagicMock:
    raw = MagicMock()
    raw.id = rid
    raw.master_id = 50
    raw.title = "Karma"
    raw.year = 1969
    raw.country = "US"
    raw.formats = [{"name": "Vinyl", "qty": "1", "descriptions": ["LP", "Album"]}]
    raw.styles = ["Spiritual Jazz", "Free Jazz"]
    raw.genres = ["Jazz"]
    raw.community.have = 2500
    raw.community.want = 8000
    raw.community.rating.average = 4.6
    raw.community.rating.count = 320

    p1 = MagicMock(); p1.id = 1; p1.role = "Tenor Saxophone"
    p2 = MagicMock(); p2.id = 2; p2.role = "Producer"
    raw.extraartists = [p1, p2]
    raw.tracklist = []

    label = MagicMock()
    label.id = 101
    label.data = {"catno": "AS-9181"}
    raw.labels = [label]

    return raw


def test_fetch_release_persists_release_row(setup) -> None:
    _, store, client = setup
    raw = _fake_raw_release(rid=100)
    client.upstream.release.return_value = raw

    release = fetch_release(client, store, 100)
    assert release.id == 100
    assert release.title == "Karma"
    assert release.community_have == 2500

    cached = store.get_release(100)
    assert cached is not None
    assert cached.title == "Karma"


def test_fetch_release_persists_credits(setup) -> None:
    _, store, client = setup
    client.upstream.release.return_value = _fake_raw_release(rid=100)

    fetch_release(client, store, 100)

    credits = store.get_release_credits(100)
    assert {(c.artist_id, c.role) for c in credits} == {
        (1, "Tenor Saxophone"),
        (2, "Producer"),
    }


def test_fetch_release_persists_labels(setup) -> None:
    _, store, client = setup
    client.upstream.release.return_value = _fake_raw_release(rid=100)

    fetch_release(client, store, 100)
    assert set(store.get_release_label_ids(100)) == {101}


def test_fetch_release_uses_cache_when_fresh(setup) -> None:
    _, store, client = setup
    raw = _fake_raw_release(rid=100)
    client.upstream.release.return_value = raw

    fetch_release(client, store, 100)
    initial_calls = store.api_calls_today()

    fetch_release(client, store, 100)
    assert store.api_calls_today() == initial_calls  # cache hit, no extra API call


def test_fetch_release_refreshes_when_stale(setup) -> None:
    _, store, client = setup
    raw = _fake_raw_release(rid=100)
    client.upstream.release.return_value = raw

    fetch_release(client, store, 100)
    initial_calls = store.api_calls_today()

    # Force the cached row to look 31 days old
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    with store.conn:
        store.conn.execute("UPDATE releases SET fetched_at = ? WHERE id = ?", (old, 100))

    fetch_release(client, store, 100)
    assert store.api_calls_today() == initial_calls + 1  # refetched


def test_fetch_release_includes_track_extraartists(setup) -> None:
    _, store, client = setup
    raw = _fake_raw_release(rid=100)
    track = MagicMock()
    p3 = MagicMock(); p3.id = 3; p3.role = "Bass"
    track.extraartists = [p3]
    raw.tracklist = [track]
    client.upstream.release.return_value = raw

    fetch_release(client, store, 100)
    credits = store.get_release_credits(100)
    assert (3, "Bass") in {(c.artist_id, c.role) for c in credits}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_api_releases.py -v`
Expected: FAIL — module not implemented.

- [ ] **Step 3: Implement `src/discogs/api/releases.py`**

```python
"""Fetch full Discogs release detail (with credits + labels) and persist."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore
from discogs.models import Credit, Format, Release

RELEASE_TTL = timedelta(days=30)


def fetch_release(
    client: DiscogsClient, store: CacheStore, release_id: int
) -> Release:
    """Return a Release for `release_id`, fetching from API if cache is missing or stale.

    Persists the release row, credits (extra-artists at the release and track levels),
    label associations, styles, and genres into the local cache.
    """
    age = store.release_age(release_id)
    if age is not None and age < RELEASE_TTL:
        cached = store.get_release(release_id)
        if cached is not None:
            return cached

    raw = client.call("release", release_id)
    release = _release_from_raw(raw)
    store.upsert_release(release)
    store.replace_release_credits(release_id, _credits_from_raw(raw, release_id))
    store.replace_release_labels(release_id, _labels_from_raw(raw))
    return release


def _release_from_raw(raw: Any) -> Release:
    return Release(
        id=int(raw.id),
        master_id=int(raw.master_id) if getattr(raw, "master_id", None) else None,
        title=str(raw.title),
        year=int(getattr(raw, "year", 0) or 0),
        country=getattr(raw, "country", None),
        formats=[
            Format(
                name=str(f.get("name", "")),
                qty=int(f.get("qty", 1) or 1),
                descriptions=list(f.get("descriptions", []) or []),
            )
            for f in (getattr(raw, "formats", None) or [])
        ],
        styles=list(getattr(raw, "styles", None) or []),
        genres=list(getattr(raw, "genres", None) or []),
        community_have=int(raw.community.have or 0),
        community_want=int(raw.community.want or 0),
        community_avg_rating=float(raw.community.rating.average or 0.0),
        community_rating_count=int(raw.community.rating.count or 0),
        fetched_at=datetime.now(UTC),
    )


def _credits_from_raw(raw: Any, release_id: int) -> list[Credit]:
    seen: set[tuple[int, str]] = set()
    credits: list[Credit] = []

    for ea in getattr(raw, "extraartists", None) or []:
        key = (int(ea.id), str(ea.role))
        if key in seen:
            continue
        seen.add(key)
        credits.append(Credit(release_id=release_id, artist_id=int(ea.id), role=str(ea.role)))

    for track in getattr(raw, "tracklist", None) or []:
        for ea in getattr(track, "extraartists", None) or []:
            key = (int(ea.id), str(ea.role))
            if key in seen:
                continue
            seen.add(key)
            credits.append(Credit(release_id=release_id, artist_id=int(ea.id), role=str(ea.role)))

    return credits


def _labels_from_raw(raw: Any) -> list[tuple[int, str | None]]:
    out: list[tuple[int, str | None]] = []
    for label in getattr(raw, "labels", None) or []:
        catno = label.data.get("catno") if hasattr(label, "data") else None
        out.append((int(label.id), catno))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_api_releases.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/api/releases.py tests/unit/test_api_releases.py
git commit -m "feat(api): fetch_release persists release + credits + labels with 30d TTL"
```

---

## Task 7: API fetch_artist

**Files:**
- Create: `src/discogs/api/artists.py`
- Test: `tests/unit/test_api_artists.py`

Same TTL pattern as fetch_release. Persists Artist into cache.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_api_artists.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.artists import fetch_artist
from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[Config, CacheStore, DiscogsClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db", daily_api_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    yield cfg, store, client
    store.close()


def _fake_raw_artist(aid: int = 7) -> MagicMock:
    raw = MagicMock()
    raw.id = aid
    raw.name = "Pharoah Sanders"
    raw.profile = "American jazz saxophonist"
    return raw


def test_fetch_artist_persists(setup) -> None:
    _, store, client = setup
    client.upstream.artist.return_value = _fake_raw_artist()
    a = fetch_artist(client, store, 7)
    assert a.name == "Pharoah Sanders"
    cached = store.get_artist(7)
    assert cached is not None and cached.name == "Pharoah Sanders"


def test_fetch_artist_uses_cache_when_fresh(setup) -> None:
    _, store, client = setup
    client.upstream.artist.return_value = _fake_raw_artist()
    fetch_artist(client, store, 7)
    initial = store.api_calls_today()
    fetch_artist(client, store, 7)
    assert store.api_calls_today() == initial


def test_fetch_artist_refreshes_when_stale(setup) -> None:
    _, store, client = setup
    client.upstream.artist.return_value = _fake_raw_artist()
    fetch_artist(client, store, 7)
    initial = store.api_calls_today()
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    with store.conn:
        store.conn.execute("UPDATE artists SET fetched_at = ? WHERE id = ?", (old, 7))
    fetch_artist(client, store, 7)
    assert store.api_calls_today() == initial + 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_api_artists.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `src/discogs/api/artists.py`**

```python
"""Fetch Artist detail and (later) discography."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore
from discogs.models import Artist

ARTIST_TTL = timedelta(days=30)


def fetch_artist(client: DiscogsClient, store: CacheStore, artist_id: int) -> Artist:
    age = store.artist_age(artist_id)
    if age is not None and age < ARTIST_TTL:
        cached = store.get_artist(artist_id)
        if cached is not None:
            return cached

    raw = client.call("artist", artist_id)
    artist = _artist_from_raw(raw)
    store.upsert_artist(artist)
    return artist


def _artist_from_raw(raw: Any) -> Artist:
    return Artist(
        id=int(raw.id),
        name=str(raw.name),
        profile=getattr(raw, "profile", None) or None,
        fetched_at=datetime.now(UTC),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_api_artists.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/api/artists.py tests/unit/test_api_artists.py
git commit -m "feat(api): fetch_artist with 30d TTL"
```

---

## Task 8: API fetch_label

**Files:**
- Create: `src/discogs/api/labels.py`
- Test: `tests/unit/test_api_labels.py`

Same shape; persists Label with `releases_count`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_api_labels.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import DiscogsClient
from discogs.api.labels import fetch_label
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[Config, CacheStore, DiscogsClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db", daily_api_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    yield cfg, store, client
    store.close()


def _fake_raw_label(lid: int = 101) -> MagicMock:
    raw = MagicMock()
    raw.id = lid
    raw.name = "Impulse!"
    raw.parent_label = None
    raw.data = {"releases_count": 200}
    return raw


def test_fetch_label_persists(setup) -> None:
    _, store, client = setup
    client.upstream.label.return_value = _fake_raw_label()
    label = fetch_label(client, store, 101)
    assert label.name == "Impulse!"
    assert label.releases_count == 200


def test_fetch_label_uses_cache_when_fresh(setup) -> None:
    _, store, client = setup
    client.upstream.label.return_value = _fake_raw_label()
    fetch_label(client, store, 101)
    initial = store.api_calls_today()
    fetch_label(client, store, 101)
    assert store.api_calls_today() == initial


def test_fetch_label_refreshes_when_stale(setup) -> None:
    _, store, client = setup
    client.upstream.label.return_value = _fake_raw_label()
    fetch_label(client, store, 101)
    initial = store.api_calls_today()
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    with store.conn:
        store.conn.execute("UPDATE labels SET fetched_at = ? WHERE id = ?", (old, 101))
    fetch_label(client, store, 101)
    assert store.api_calls_today() == initial + 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_api_labels.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `src/discogs/api/labels.py`**

```python
"""Fetch Label detail."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore
from discogs.models import Label

LABEL_TTL = timedelta(days=30)


def fetch_label(client: DiscogsClient, store: CacheStore, label_id: int) -> Label:
    age = store.label_age(label_id)
    if age is not None and age < LABEL_TTL:
        cached = store.get_label(label_id)
        if cached is not None:
            return cached

    raw = client.call("label", label_id)
    label = _label_from_raw(raw)
    store.upsert_label(label)
    return label


def _label_from_raw(raw: Any) -> Label:
    parent = getattr(raw, "parent_label", None)
    parent_str = parent.name if parent is not None and hasattr(parent, "name") else parent
    return Label(
        id=int(raw.id),
        name=str(raw.name),
        parent_label=parent_str,
        releases_count=int(raw.data.get("releases_count", 0) if hasattr(raw, "data") else 0),
        fetched_at=datetime.now(UTC),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_api_labels.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/api/labels.py tests/unit/test_api_labels.py
git commit -m "feat(api): fetch_label with 30d TTL"
```

---

## Task 9: API fetch_artist_releases (top-K via cache)

**Files:**
- Modify: `src/discogs/api/artists.py`
- Test: `tests/unit/test_api_artist_releases.py`

Returns the top-K release IDs for an artist using `artist_top_releases` cache (TTL 30 days). On cache miss, paginates page 1 of `client.artist(id).releases`, filters to release-type rows (excludes masters and bootlegs), takes the first `top_k`, persists.

Note: we deliberately do NOT page past page 1 nor sort by community.have here — that would require fetching every release individually (cost-prohibitive). The graph walk reads the cached ordering as Discogs returns it.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_api_artist_releases.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.artists import fetch_artist_releases
from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[Config, CacheStore, DiscogsClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db", daily_api_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    yield cfg, store, client
    store.close()


def _fake_release_ref(rid: int, type_: str = "release") -> MagicMock:
    r = MagicMock()
    r.id = rid
    r.type = type_
    return r


def test_fetch_artist_releases_caches(setup) -> None:
    _, store, client = setup
    refs = [_fake_release_ref(i) for i in range(1, 31)]
    artist = MagicMock()
    artist.releases = iter(refs)
    client.upstream.artist.return_value = artist

    rids = fetch_artist_releases(client, store, artist_id=7, top_k=10)

    assert rids == list(range(1, 11))
    assert store.get_artist_top_release_ids(7) == list(range(1, 11))


def test_fetch_artist_releases_filters_non_releases(setup) -> None:
    _, store, client = setup
    refs = [
        _fake_release_ref(1, "release"),
        _fake_release_ref(2, "master"),
        _fake_release_ref(3, "release"),
    ]
    artist = MagicMock()
    artist.releases = iter(refs)
    client.upstream.artist.return_value = artist

    rids = fetch_artist_releases(client, store, artist_id=7, top_k=10)
    assert rids == [1, 3]


def test_fetch_artist_releases_uses_cache_when_fresh(setup) -> None:
    _, store, client = setup
    store.replace_artist_top_releases(artist_id=7, release_ids=[10, 11, 12])
    rids = fetch_artist_releases(client, store, artist_id=7, top_k=2)
    assert rids == [10, 11]
    client.upstream.artist.assert_not_called()


def test_fetch_artist_releases_refreshes_when_stale(setup) -> None:
    _, store, client = setup
    store.replace_artist_top_releases(artist_id=7, release_ids=[10])
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    with store.conn:
        store.conn.execute(
            "UPDATE artist_top_releases SET fetched_at = ? WHERE artist_id = ?",
            (old, 7),
        )

    artist = MagicMock()
    artist.releases = iter([_fake_release_ref(99)])
    client.upstream.artist.return_value = artist

    rids = fetch_artist_releases(client, store, artist_id=7, top_k=5)
    assert rids == [99]


def test_top_k_respects_limit(setup) -> None:
    _, store, client = setup
    refs = [_fake_release_ref(i) for i in range(100)]
    artist = MagicMock()
    artist.releases = iter(refs)
    client.upstream.artist.return_value = artist

    rids = fetch_artist_releases(client, store, artist_id=7, top_k=5)
    assert rids == [0, 1, 2, 3, 4]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_api_artist_releases.py -v`
Expected: FAIL.

- [ ] **Step 3: Append `fetch_artist_releases` to `src/discogs/api/artists.py`**

Add at the bottom of the file:

```python
ARTIST_TOP_RELEASES_TTL = timedelta(days=30)


def fetch_artist_releases(
    client: DiscogsClient, store: CacheStore, artist_id: int, *, top_k: int = 25,
    page_size: int = 50,
) -> list[int]:
    """Return up to `top_k` release IDs for `artist_id` from page 1 of their discography.

    Uses the `artist_top_releases` cache (30d TTL). On miss, paginates page 1 only
    (capped at `page_size` items), filters to type='release', takes the first `top_k`,
    persists, returns.
    """
    age = store.artist_top_releases_age(artist_id)
    if age is not None and age < ARTIST_TOP_RELEASES_TTL:
        cached = store.get_artist_top_release_ids(artist_id)
        if cached:
            return cached[:top_k]

    raw = client.call("artist", artist_id)
    rids: list[int] = []
    for i, ref in enumerate(raw.releases):
        if i >= page_size:
            break
        if getattr(ref, "type", "release") != "release":
            continue
        rids.append(int(ref.id))
        if len(rids) >= top_k:
            break

    store.replace_artist_top_releases(artist_id, rids)
    return rids
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_api_artist_releases.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/api/artists.py tests/unit/test_api_artist_releases.py
git commit -m "feat(api): fetch_artist_releases — page-1 top-K with 30d cache"
```

---

## Task 10: Recommend — seed selection (Stage 1)

**Files:**
- Create: `src/discogs/recommend/__init__.py` (empty)
- Create: `src/discogs/recommend/seeds.py`
- Test: `tests/unit/test_recommend_seeds.py`

Stage 1 of the pipeline: pick seed artists from the user's collection + wantlist. A seed is any artist whose `artist_id` appears in `release_credits` rows for ≥ `min_occurrences` releases that are in the cached collection or wantlist. Each seed gets a `seed_weight` inversely proportional to that artist's discography size (more obscure = higher weight, capped to `[0.1, 1.0]`).

For Phase 2 we don't yet have artist popularity data (no `artist_release_count` populated). Substitute: count how many releases the artist has credits on **across the user's library** — a cheap, deterministic proxy. (Phase 3 can swap this for the real Discogs metric.)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_recommend_seeds.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import CollectionItem, Credit, WantlistItem
from discogs.recommend.seeds import SeedArtist, select_seeds


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def _seed_release(store: CacheStore, release_id: int, artist_credits: list[tuple[int, str]]) -> None:
    """Insert one library release and its credits without triggering full release upsert."""
    with store.conn:
        store.conn.execute(
            "INSERT OR IGNORE INTO releases ("
            "id, master_id, title, year, country, formats_json, "
            "community_have, community_want, community_avg_rating, community_rating_count, fetched_at"
            ") VALUES (?, NULL, ?, 1970, NULL, '[]', 0, 0, 0.0, 0, ?)",
            (release_id, f"r{release_id}", datetime.now(UTC).isoformat()),
        )
    store.replace_release_credits(
        release_id,
        [Credit(release_id=release_id, artist_id=aid, role=role) for aid, role in artist_credits],
    )


def test_seeds_filter_by_min_occurrences(store: CacheStore) -> None:
    # Two releases in collection, one in wantlist.
    _seed_release(store, 1, [(7, "Saxophone"), (99, "Engineer")])
    _seed_release(store, 2, [(7, "Saxophone")])
    _seed_release(store, 3, [(99, "Producer")])

    store.replace_collection([
        CollectionItem(release_id=1, folder_id=0, instance_id=10, date_added=datetime.now(UTC)),
        CollectionItem(release_id=2, folder_id=0, instance_id=20, date_added=datetime.now(UTC)),
    ])
    store.replace_wantlist([
        WantlistItem(release_id=3, date_added=datetime.now(UTC), notes=None),
    ])

    seeds = select_seeds(store, mode="both", min_occurrences=2)
    seed_ids = {s.artist_id for s in seeds}
    assert seed_ids == {7, 99}  # both appear ≥ 2x across library


def test_seeds_respect_mode(store: CacheStore) -> None:
    _seed_release(store, 1, [(7, "A"), (8, "B")])
    _seed_release(store, 2, [(7, "A")])
    _seed_release(store, 3, [(8, "B")])

    store.replace_collection([
        CollectionItem(release_id=1, folder_id=0, instance_id=10, date_added=datetime.now(UTC)),
        CollectionItem(release_id=2, folder_id=0, instance_id=20, date_added=datetime.now(UTC)),
    ])
    store.replace_wantlist([
        WantlistItem(release_id=3, date_added=datetime.now(UTC), notes=None),
    ])

    coll_only = select_seeds(store, mode="collection", min_occurrences=2)
    assert {s.artist_id for s in coll_only} == {7}

    want_only = select_seeds(store, mode="wantlist", min_occurrences=1)
    assert {s.artist_id for s in want_only} == {8}


def test_seed_weights_in_range(store: CacheStore) -> None:
    _seed_release(store, 1, [(7, "A"), (8, "A"), (9, "A")])
    _seed_release(store, 2, [(7, "A"), (8, "A")])
    _seed_release(store, 3, [(7, "A")])

    store.replace_collection([
        CollectionItem(release_id=r, folder_id=0, instance_id=10 * r, date_added=datetime.now(UTC))
        for r in (1, 2, 3)
    ])

    seeds = select_seeds(store, mode="collection", min_occurrences=1)
    weights = {s.artist_id: s.weight for s in seeds}
    assert all(0.1 <= w <= 1.0 for w in weights.values())
    # Artist 7 appears most often → smallest weight (least obscure within library)
    assert weights[7] <= weights[9]


def test_no_seeds_when_library_empty(store: CacheStore) -> None:
    seeds = select_seeds(store, mode="both", min_occurrences=2)
    assert seeds == []


def test_seed_artist_immutable() -> None:
    s = SeedArtist(artist_id=1, weight=0.5, sources=("collection",))
    with pytest.raises((AttributeError, TypeError)):
        s.weight = 0.9  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recommend_seeds.py -v`
Expected: FAIL.

- [ ] **Step 3: Create `src/discogs/recommend/__init__.py`**

```python
"""Recommendation pipeline: seeds → graph walk → scoring → selection → digest."""
```

- [ ] **Step 4: Implement `src/discogs/recommend/seeds.py`**

```python
"""Stage 1: pick seed artists from the user's library."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from discogs.cache.store import CacheStore

Mode = Literal["collection", "wantlist", "both"]


@dataclass(frozen=True)
class SeedArtist:
    artist_id: int
    weight: float            # in [0.1, 1.0]
    sources: tuple[str, ...] # subset of ("collection", "wantlist")


def select_seeds(
    store: CacheStore, *, mode: Mode = "both", min_occurrences: int = 2,
) -> list[SeedArtist]:
    """Return the seed-artist set from cached library data.

    An artist becomes a seed when their `artist_id` appears in the credits of
    at least `min_occurrences` releases the user owns or wants (per `mode`).
    """
    coll_ids = store.collection_release_ids() if mode in ("collection", "both") else set()
    want_ids = store.wantlist_release_ids() if mode in ("wantlist", "both") else set()
    library_ids = coll_ids | want_ids
    if not library_ids:
        return []

    placeholders = ",".join("?" for _ in library_ids)
    rows = store.conn.execute(
        f"SELECT release_id, artist_id FROM release_credits WHERE release_id IN ({placeholders})",
        tuple(library_ids),
    ).fetchall()

    occurrences: Counter[int] = Counter()
    sources: dict[int, set[str]] = {}
    for r in rows:
        rid = int(r["release_id"])
        aid = int(r["artist_id"])
        occurrences[aid] += 1
        bucket = sources.setdefault(aid, set())
        if rid in coll_ids:
            bucket.add("collection")
        if rid in want_ids:
            bucket.add("wantlist")

    eligible = [(aid, n) for aid, n in occurrences.items() if n >= min_occurrences]
    if not eligible:
        return []

    raw_weights = {aid: 1.0 / math.log(n + 10) for aid, n in eligible}
    lo, hi = min(raw_weights.values()), max(raw_weights.values())
    span = hi - lo

    def normalize(w: float) -> float:
        if span == 0:
            return 1.0
        return 0.1 + 0.9 * (w - lo) / span

    return [
        SeedArtist(
            artist_id=aid,
            weight=normalize(raw_weights[aid]),
            sources=tuple(sorted(sources.get(aid, set()))),
        )
        for aid, _ in sorted(eligible, key=lambda x: -x[1])
    ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_recommend_seeds.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/discogs/recommend/__init__.py src/discogs/recommend/seeds.py tests/unit/test_recommend_seeds.py
git commit -m "feat(recommend): Stage 1 — select seed artists with normalized weights"
```

---

## Task 11: Recommend — graph walk (Stage 2)

**Files:**
- Create: `src/discogs/recommend/graph.py`
- Test: `tests/unit/test_recommend_graph.py`

The bounded credit-graph walk. From each seed: fetch their top releases → for each release, fetch full detail (credits) → harvest one-hop neighbors → fetch each neighbor's top releases → all those are candidates. Each candidate is recorded with a `GraphPath` capturing seed → release → neighbor → candidate plus per-edge weights.

Hard caps: `max_neighbors_per_seed` (default 5), `max_releases_per_neighbor` (default 25), and `budget` (default 800 API calls). The walk halts gracefully when budget hits zero.

The `role_weight` table downweights peripheral roles (engineer, mastering) vs primary (producer, performer).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_recommend_graph.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.models import CollectionItem, Credit, Format, Release, WantlistItem
from discogs.recommend.graph import GraphPath, role_weight, walk_credit_graph
from discogs.recommend.seeds import SeedArtist


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[CacheStore, DiscogsClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db", daily_api_budget=10000,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    yield store, client
    store.close()


def _stub_release(release_id: int, credits: list[Credit]) -> Release:
    return Release(
        id=release_id, master_id=None, title=f"r{release_id}", year=1970,
        country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Jazz"], genres=["Jazz"],
        community_have=100, community_want=200,
        community_avg_rating=4.0, community_rating_count=10,
        fetched_at=datetime.now(UTC),
    )


def test_role_weight_table() -> None:
    assert role_weight("Producer") == 1.0
    assert role_weight("Tenor Saxophone") == 1.0       # any "primary" credit
    assert role_weight("Engineer") == 0.5
    assert role_weight("Mastered By") == 0.3
    assert role_weight("Liner Notes") == 0.2
    assert role_weight("Some Unknown Role") == 0.5     # default


def test_walk_collects_direct_seed_releases(setup) -> None:
    store, client = setup
    seeds = [SeedArtist(artist_id=7, weight=0.9, sources=("collection",))]
    fake_release = _stub_release(101, credits=[Credit(release_id=101, artist_id=7, role="Saxophone")])

    with patch("discogs.recommend.graph.fetch_artist_releases") as far, \
         patch("discogs.recommend.graph.fetch_release") as fr:
        far.return_value = [101]
        fr.return_value = fake_release
        # Persist credits so the in-memory store mirrors what fetch_release would.
        store.replace_release_credits(101, [Credit(release_id=101, artist_id=7, role="Saxophone")])

        paths = walk_credit_graph(client, store, seeds, budget=100)

    assert 101 in paths
    assert paths[101][0].seed_artist_id == 7
    assert len(paths[101][0].edge_chain) == 1


def test_walk_expands_one_hop_through_neighbors(setup) -> None:
    store, client = setup
    seeds = [SeedArtist(artist_id=7, weight=0.9, sources=("collection",))]

    with patch("discogs.recommend.graph.fetch_artist_releases") as far, \
         patch("discogs.recommend.graph.fetch_release") as fr:

        def fake_fetch_artist_releases(_c, _s, artist_id, top_k=25):
            if artist_id == 7:
                return [101]
            if artist_id == 99:
                return [201, 202]
            return []

        def fake_fetch_release(_c, _s, release_id):
            return _stub_release(release_id, credits=[])

        far.side_effect = fake_fetch_artist_releases
        fr.side_effect = fake_fetch_release
        store.replace_release_credits(101, [
            Credit(release_id=101, artist_id=7, role="Saxophone"),
            Credit(release_id=101, artist_id=99, role="Producer"),
        ])
        store.replace_release_credits(201, [])
        store.replace_release_credits(202, [])

        paths = walk_credit_graph(client, store, seeds, max_neighbors_per_seed=3, budget=100)

    assert 101 in paths   # direct seed release
    assert 201 in paths   # one-hop via neighbor 99
    assert 202 in paths


def test_walk_excludes_collection_and_wantlist(setup) -> None:
    store, client = setup
    seeds = [SeedArtist(artist_id=7, weight=0.9, sources=("collection",))]

    store.replace_collection([
        CollectionItem(release_id=101, folder_id=0, instance_id=1, date_added=datetime.now(UTC)),
    ])
    store.replace_wantlist([
        WantlistItem(release_id=202, date_added=datetime.now(UTC), notes=None),
    ])

    with patch("discogs.recommend.graph.fetch_artist_releases") as far, \
         patch("discogs.recommend.graph.fetch_release") as fr:
        far.return_value = [101, 202, 999]
        fr.side_effect = lambda _c, _s, rid: _stub_release(rid, credits=[])
        store.replace_release_credits(999, [])

        paths = walk_credit_graph(client, store, seeds, budget=100)

    assert 101 not in paths
    assert 202 not in paths
    assert 999 in paths


def test_walk_excludes_previously_recommended(setup) -> None:
    store, client = setup
    seeds = [SeedArtist(artist_id=7, weight=0.9, sources=("collection",))]
    rid_prev, _ = store.start_run(args={})
    store.record_recommendation(rid_prev, release_id=101, score=0.5)
    store.finish_run(rid_prev, summary={})

    with patch("discogs.recommend.graph.fetch_artist_releases") as far, \
         patch("discogs.recommend.graph.fetch_release") as fr:
        far.return_value = [101, 102]
        fr.side_effect = lambda _c, _s, rid: _stub_release(rid, credits=[])
        store.replace_release_credits(101, [])
        store.replace_release_credits(102, [])

        paths = walk_credit_graph(client, store, seeds, budget=100)

    assert 101 not in paths
    assert 102 in paths


def test_walk_respects_budget(setup) -> None:
    store, client = setup
    seeds = [
        SeedArtist(artist_id=7, weight=0.9, sources=("collection",)),
        SeedArtist(artist_id=8, weight=0.8, sources=("collection",)),
    ]

    call_log: list[int] = []

    def far_side(_c, _s, artist_id, top_k=25):
        call_log.append(artist_id)
        store.increment_api_calls(1)
        return []

    with patch("discogs.recommend.graph.fetch_artist_releases", side_effect=far_side), \
         patch("discogs.recommend.graph.fetch_release") as fr:
        fr.return_value = _stub_release(0, credits=[])
        # budget=1 means we get exactly one fetch_artist_releases call before halting
        walk_credit_graph(client, store, seeds, budget=1)

    assert len(call_log) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recommend_graph.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `src/discogs/recommend/graph.py`**

```python
"""Stage 2: bounded BFS through the credit graph."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from discogs.api.artists import fetch_artist_releases
from discogs.api.client import DiscogsClient
from discogs.api.releases import fetch_release
from discogs.cache.store import CacheStore
from discogs.recommend.seeds import SeedArtist


@dataclass(frozen=True)
class GraphPath:
    """One trace of how a candidate was reached.

    `edge_chain` is a list of (artist_id, release_id, role) tuples. For a direct
    seed release the chain has length 1 (role="direct"); for a one-hop neighbor
    it has length 2.
    """
    seed_artist_id: int
    seed_weight: float
    edge_chain: tuple[tuple[int, int, str], ...]
    edge_weight: float  # product of role weights along the chain


_PRIMARY_ROLES = {
    "producer", "co-producer", "executive producer",
    "vocals", "guitar", "bass", "drums", "piano", "synthesizer",
    "saxophone", "trumpet", "trombone", "violin", "performer",
}
_INSTRUMENT_HINTS = (
    "saxophone", "guitar", "bass", "vocals", "drums", "piano", "synth",
    "keys", "horn", "trumpet", "trombone", "percussion", "violin", "cello",
)


def role_weight(role: str) -> float:
    """Map a Discogs role string to a graph-edge weight in [0.2, 1.0]."""
    base = role.split("[", 1)[0].strip().lower()

    if any(hint in base for hint in _INSTRUMENT_HINTS):
        return 1.0
    if base in _PRIMARY_ROLES:
        return 1.0
    if base in {"engineer", "recording engineer", "mixed by"}:
        return 0.5
    if base in {"mastered by", "remastered by"}:
        return 0.3
    if base in {"liner notes", "design", "photography", "artwork", "illustration"}:
        return 0.2
    return 0.5


def walk_credit_graph(
    client: DiscogsClient,
    store: CacheStore,
    seeds: Sequence[SeedArtist],
    *,
    max_neighbors_per_seed: int = 5,
    max_releases_per_neighbor: int = 25,
    budget: int = 800,
) -> dict[int, list[GraphPath]]:
    """Walk the credit graph from `seeds`, returning candidate releases with their paths.

    Releases already in the user's collection, wantlist, or recommendation history
    are excluded from the result.
    """
    excluded = (
        store.collection_release_ids()
        | store.wantlist_release_ids()
        | store.previously_recommended_release_ids()
    )

    api_calls_at_start = store.api_calls_today()

    def remaining() -> int:
        spent = store.api_calls_today() - api_calls_at_start
        return budget - spent

    paths: dict[int, list[GraphPath]] = defaultdict(list)

    for seed in seeds:
        if remaining() <= 0:
            break

        seed_release_ids = fetch_artist_releases(
            client, store, seed.artist_id, top_k=max_releases_per_neighbor,
        )

        for release_id in seed_release_ids:
            if remaining() <= 0:
                break

            if release_id not in excluded:
                paths[release_id].append(GraphPath(
                    seed_artist_id=seed.artist_id,
                    seed_weight=seed.weight,
                    edge_chain=((seed.artist_id, release_id, "direct"),),
                    edge_weight=1.0,
                ))

            fetch_release(client, store, release_id)
            credits = store.get_release_credits(release_id)

            ranked_neighbors = _rank_neighbors(
                credits, exclude_artist_id=seed.artist_id, top=max_neighbors_per_seed,
            )

            for neighbor_id, neighbor_role in ranked_neighbors:
                if remaining() <= 0:
                    break

                neighbor_release_ids = fetch_artist_releases(
                    client, store, neighbor_id, top_k=max_releases_per_neighbor,
                )
                for nr_id in neighbor_release_ids:
                    if nr_id in excluded:
                        continue
                    paths[nr_id].append(GraphPath(
                        seed_artist_id=seed.artist_id,
                        seed_weight=seed.weight,
                        edge_chain=(
                            (seed.artist_id, release_id, "direct"),
                            (neighbor_id, nr_id, neighbor_role),
                        ),
                        edge_weight=role_weight(neighbor_role),
                    ))

    return dict(paths)


def _rank_neighbors(
    credits: Sequence, *, exclude_artist_id: int, top: int,
) -> list[tuple[int, str]]:
    """Return up to `top` (artist_id, role) pairs ranked by role weight."""
    seen: dict[int, tuple[float, str]] = {}
    for c in credits:
        if c.artist_id == exclude_artist_id:
            continue
        w = role_weight(c.role)
        if c.artist_id not in seen or w > seen[c.artist_id][0]:
            seen[c.artist_id] = (w, c.role)
    ranked = sorted(seen.items(), key=lambda item: -item[1][0])
    return [(aid, role) for aid, (_w, role) in ranked[:top]]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_recommend_graph.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/recommend/graph.py tests/unit/test_recommend_graph.py
git commit -m "feat(recommend): Stage 2 — bounded credit-graph BFS with budget guard"
```

---

## Task 12: Recommend — scoring (8 sub-scores)

**Files:**
- Create: `src/discogs/recommend/scoring.py`
- Test: `tests/unit/test_recommend_scoring.py`

Pure-math layer. Each candidate gets 8 sub-scores in `[0, 1]`, then a weighted sum (weights from spec, missing `influence_chain_score` always 0). Scoring is deterministic given the same candidate set; nothing here calls the network.

Inputs to the scorer are pre-resolved (the pipeline in Task 13 looks up release / label / collection style data and passes it in).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_recommend_scoring.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import Format, Release
from discogs.recommend.graph import GraphPath
from discogs.recommend.scoring import (
    DEFAULT_WEIGHTS,
    ScoredCandidate,
    score_candidates,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def _release(rid: int, *, have: int = 1000, want: int = 500, rating: float = 4.2,
             rating_count: int = 50, year: int = 1975,
             formats=None, styles=None) -> Release:
    return Release(
        id=rid, master_id=None, title=f"r{rid}", year=year, country="US",
        formats=formats or [Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=styles or ["Jazz"], genres=["Jazz"],
        community_have=have, community_want=want,
        community_avg_rating=rating, community_rating_count=rating_count,
        fetched_at=datetime.now(UTC),
    )


def test_score_in_range_and_total_le_085(store: CacheStore) -> None:
    paths = {
        100: [GraphPath(seed_artist_id=1, seed_weight=0.9,
                        edge_chain=((1, 100, "direct"),), edge_weight=1.0)],
    }
    scored = score_candidates(
        store=store,
        candidate_paths=paths,
        releases={100: _release(100)},
        label_release_counts={100: 50},
        weights=DEFAULT_WEIGHTS,
    )
    assert len(scored) == 1
    s = scored[0]
    assert isinstance(s, ScoredCandidate)
    assert 0.0 <= s.score <= 0.85   # influence_chain_score weight (0.15) is unused in Phase 2


def test_higher_have_lowers_rarity(store: CacheStore) -> None:
    paths = {
        1: [GraphPath(1, 0.9, ((1, 1, "direct"),), 1.0)],
        2: [GraphPath(1, 0.9, ((1, 2, "direct"),), 1.0)],
    }
    releases = {1: _release(1, have=10), 2: _release(2, have=100_000)}
    scored = {s.release_id: s for s in score_candidates(
        store=store, candidate_paths=paths, releases=releases,
        label_release_counts={1: 50, 2: 50}, weights=DEFAULT_WEIGHTS,
    )}
    assert scored[1].subscores["rarity"] > scored[2].subscores["rarity"]


def test_album_format_beats_single(store: CacheStore) -> None:
    paths = {
        1: [GraphPath(1, 0.9, ((1, 1, "direct"),), 1.0)],
        2: [GraphPath(1, 0.9, ((1, 2, "direct"),), 1.0)],
    }
    releases = {
        1: _release(1, formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])]),
        2: _release(2, formats=[Format(name="Vinyl", qty=1, descriptions=["7\""])]),
    }
    scored = {s.release_id: s for s in score_candidates(
        store=store, candidate_paths=paths, releases=releases,
        label_release_counts={1: 50, 2: 50}, weights=DEFAULT_WEIGHTS,
    )}
    assert scored[1].subscores["format"] > scored[2].subscores["format"]


def test_low_rating_count_zeros_rating_subscore(store: CacheStore) -> None:
    paths = {1: [GraphPath(1, 0.9, ((1, 1, "direct"),), 1.0)]}
    releases = {1: _release(1, rating=4.9, rating_count=2)}  # < threshold of 5
    scored = score_candidates(
        store=store, candidate_paths=paths, releases=releases,
        label_release_counts={1: 50}, weights=DEFAULT_WEIGHTS,
    )
    assert scored[0].subscores["rating"] == 0.0


def test_influence_chain_score_is_zero_in_phase_2(store: CacheStore) -> None:
    paths = {1: [GraphPath(1, 0.9, ((1, 1, "direct"),), 1.0)]}
    releases = {1: _release(1)}
    scored = score_candidates(
        store=store, candidate_paths=paths, releases=releases,
        label_release_counts={1: 50}, weights=DEFAULT_WEIGHTS,
    )
    assert scored[0].subscores["influence_chain"] == 0.0


def test_results_sorted_descending(store: CacheStore) -> None:
    paths = {
        1: [GraphPath(1, 0.9, ((1, 1, "direct"),), 1.0)],
        2: [GraphPath(1, 0.9, ((1, 2, "direct"),), 1.0)],
    }
    releases = {
        1: _release(1, have=10, want=500, rating=4.8, rating_count=200),
        2: _release(2, have=50_000, want=10, rating=2.5, rating_count=200),
    }
    scored = score_candidates(
        store=store, candidate_paths=paths, releases=releases,
        label_release_counts={1: 5, 2: 5_000}, weights=DEFAULT_WEIGHTS,
    )
    assert [s.release_id for s in scored] == [1, 2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recommend_scoring.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `src/discogs/recommend/scoring.py`**

```python
"""Stage 3: score the candidate set produced by the graph walk.

8 sub-scores in [0, 1]; the 9th (`influence_chain`) is always 0 in Phase 2.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from discogs.cache.store import CacheStore
from discogs.models import Release
from discogs.recommend.graph import GraphPath

DEFAULT_WEIGHTS: dict[str, float] = {
    "connection": 0.20,
    "influence_chain": 0.15,   # always 0 in Phase 2
    "rarity": 0.20,
    "demand_ratio": 0.05,
    "label_obscurity": 0.05,
    "style_niche": 0.05,
    "rating": 0.15,
    "format": 0.10,
    "recency_match": 0.05,
}

_RATING_COUNT_FLOOR = 5


@dataclass(frozen=True)
class ScoredCandidate:
    release_id: int
    score: float
    subscores: dict[str, float]
    paths: tuple[GraphPath, ...]


def score_candidates(
    *,
    store: CacheStore,
    candidate_paths: dict[int, list[GraphPath]],
    releases: dict[int, Release],
    label_release_counts: dict[int, int],   # release_id -> max(label.releases_count) for its labels
    weights: dict[str, float] = DEFAULT_WEIGHTS,
) -> list[ScoredCandidate]:
    """Score every candidate. Returns sorted descending by total score."""
    if not candidate_paths:
        return []

    user_decade_dist = _user_decade_distribution(store)
    user_style_freq = _user_style_frequency(store)

    raw_connections: dict[int, float] = {
        rid: sum(p.seed_weight * p.edge_weight for p in ps)
        for rid, ps in candidate_paths.items()
    }
    max_conn = max(raw_connections.values()) or 1.0

    have_values = [releases[rid].community_have for rid in candidate_paths if rid in releases]
    max_have = max(have_values) if have_values else 1
    max_label_count = max(label_release_counts.values()) if label_release_counts else 1

    scored: list[ScoredCandidate] = []

    for rid, ps in candidate_paths.items():
        rel = releases.get(rid)
        if rel is None:
            continue

        sub = {
            "connection": raw_connections[rid] / max_conn,
            "influence_chain": 0.0,
            "rarity": 1.0 - math.log(rel.community_have + 1) / math.log(max_have + 1),
            "demand_ratio": min(1.0, (rel.community_want / max(rel.community_have, 1)) / 2.0),
            "label_obscurity": 1.0 - math.log(label_release_counts.get(rid, 1) + 1) / math.log(max_label_count + 1),
            "style_niche": _style_niche(rel.styles, user_style_freq),
            "rating": _rating_score(rel),
            "format": _format_score(rel),
            "recency_match": _decade_match(rel.year, user_decade_dist),
        }
        total = sum(weights[k] * sub[k] for k in sub)
        scored.append(ScoredCandidate(
            release_id=rid, score=total, subscores=sub, paths=tuple(ps),
        ))

    scored.sort(key=lambda s: -s.score)
    return scored


def _rating_score(rel: Release) -> float:
    if rel.community_rating_count < _RATING_COUNT_FLOOR:
        return 0.0
    return max(0.0, min(1.0, (rel.community_avg_rating - 3.0) / 2.0))


def _format_score(rel: Release) -> float:
    if rel.is_compilation:
        return 0.3
    if rel.is_album_or_ep:
        return 1.0
    return 0.0


def _style_niche(styles: Iterable[str], user_freq: dict[str, float]) -> float:
    if not styles:
        return 0.5
    avg_freq = sum(user_freq.get(s, 0.0) for s in styles) / len(list(styles))
    return max(0.0, min(1.0, 1.0 - avg_freq))


def _user_style_frequency(store: CacheStore) -> dict[str, float]:
    rows = store.conn.execute(
        "SELECT style FROM release_styles WHERE release_id IN ("
        "  SELECT release_id FROM collection_items"
        ")"
    )
    counts = Counter(r["style"] for r in rows)
    if not counts:
        return {}
    total = sum(counts.values())
    return {style: n / total for style, n in counts.items()}


def _user_decade_distribution(store: CacheStore) -> dict[int, float]:
    rows = store.conn.execute(
        "SELECT year FROM releases WHERE id IN ("
        "  SELECT release_id FROM collection_items"
        ")"
    )
    years = [int(r["year"]) for r in rows if r["year"]]
    if not years:
        return {}
    decades = Counter((y // 10) * 10 for y in years)
    total = sum(decades.values())
    return {d: n / total for d, n in decades.items()}


def _decade_match(year: int, user_dist: dict[int, float]) -> float:
    if not user_dist or not year:
        return 0.5
    return user_dist.get((year // 10) * 10, 0.0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_recommend_scoring.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/recommend/scoring.py tests/unit/test_recommend_scoring.py
git commit -m "feat(recommend): Stage 3 — 8 sub-score weighted scoring"
```

---

## Task 13: Recommend — pipeline orchestration

**Files:**
- Create: `src/discogs/recommend/pipeline.py`
- Test: `tests/unit/test_recommend_pipeline.py`

Glue. Composes seeds → graph → scoring → final selection. The diversity guard caps any single seed-artist's contribution to 3 picks. After selection, persists each pick to `recommendation_history` linked to a fresh `runs` row. Returns a `RunResult` consumed by the digest renderer (Task 14).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_recommend_pipeline.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.models import CollectionItem, Credit, Format, Release
from discogs.recommend.graph import GraphPath
from discogs.recommend.pipeline import RunResult, run_recommend
from discogs.recommend.scoring import DEFAULT_WEIGHTS, ScoredCandidate
from discogs.recommend.seeds import SeedArtist


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[Config, CacheStore, DiscogsClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db", daily_api_budget=10000,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    yield cfg, store, client
    store.close()


def _release(rid: int, year: int = 1970) -> Release:
    return Release(
        id=rid, master_id=None, title=f"r{rid}", year=year, country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Jazz"], genres=["Jazz"],
        community_have=1000, community_want=500,
        community_avg_rating=4.0, community_rating_count=20,
        fetched_at=datetime.now(UTC),
    )


def _scored(rid: int, score: float, primary_artist_id: int) -> ScoredCandidate:
    p = GraphPath(
        seed_artist_id=primary_artist_id,
        seed_weight=1.0,
        edge_chain=((primary_artist_id, rid, "direct"),),
        edge_weight=1.0,
    )
    return ScoredCandidate(release_id=rid, score=score, subscores={"connection": 1.0}, paths=(p,))


def test_pipeline_writes_history_rows(setup) -> None:
    cfg, store, client = setup

    with patch("discogs.recommend.pipeline.select_seeds") as ss, \
         patch("discogs.recommend.pipeline.walk_credit_graph") as wg, \
         patch("discogs.recommend.pipeline.score_candidates") as sc:
        ss.return_value = [SeedArtist(artist_id=1, weight=1.0, sources=("collection",))]
        wg.return_value = {10: [GraphPath(1, 1.0, ((1, 10, "direct"),), 1.0)]}
        sc.return_value = [_scored(10, 0.7, primary_artist_id=1)]

        with patch("discogs.recommend.pipeline._load_releases") as lr, \
             patch("discogs.recommend.pipeline._load_label_counts") as ll:
            lr.return_value = {10: _release(10)}
            ll.return_value = {10: 50}
            result = run_recommend(client, store, cfg, max_recs=5)

    assert isinstance(result, RunResult)
    assert result.picks[0].release_id == 10
    assert result.run_display_id  # YYYY-MM-DD-HHMM
    assert store.previously_recommended_release_ids() == {10}


def test_diversity_guard_caps_per_seed_to_three(setup) -> None:
    cfg, store, client = setup

    five_picks = [_scored(rid=100 + i, score=0.9 - i * 0.01, primary_artist_id=42) for i in range(5)]
    one_other = _scored(rid=999, score=0.5, primary_artist_id=7)

    with patch("discogs.recommend.pipeline.select_seeds") as ss, \
         patch("discogs.recommend.pipeline.walk_credit_graph") as wg, \
         patch("discogs.recommend.pipeline.score_candidates") as sc, \
         patch("discogs.recommend.pipeline._load_releases") as lr, \
         patch("discogs.recommend.pipeline._load_label_counts") as ll:
        ss.return_value = [
            SeedArtist(artist_id=42, weight=1.0, sources=("collection",)),
            SeedArtist(artist_id=7, weight=0.8, sources=("collection",)),
        ]
        wg.return_value = {p.release_id: list(p.paths) for p in five_picks + [one_other]}
        sc.return_value = five_picks + [one_other]
        lr.return_value = {p.release_id: _release(p.release_id) for p in five_picks + [one_other]}
        ll.return_value = {p.release_id: 50 for p in five_picks + [one_other]}

        result = run_recommend(client, store, cfg, max_recs=10)

    primary_count = sum(1 for p in result.picks if p.paths[0].seed_artist_id == 42)
    assert primary_count == 3   # diversity cap
    assert any(p.paths[0].seed_artist_id == 7 for p in result.picks)


def test_max_recs_limits_picks(setup) -> None:
    cfg, store, client = setup
    candidates = [_scored(rid=i, score=1.0 - i * 0.01, primary_artist_id=i) for i in range(50)]

    with patch("discogs.recommend.pipeline.select_seeds") as ss, \
         patch("discogs.recommend.pipeline.walk_credit_graph") as wg, \
         patch("discogs.recommend.pipeline.score_candidates") as sc, \
         patch("discogs.recommend.pipeline._load_releases") as lr, \
         patch("discogs.recommend.pipeline._load_label_counts") as ll:
        ss.return_value = [SeedArtist(artist_id=i, weight=1.0, sources=("collection",)) for i in range(50)]
        wg.return_value = {c.release_id: list(c.paths) for c in candidates}
        sc.return_value = candidates
        lr.return_value = {c.release_id: _release(c.release_id) for c in candidates}
        ll.return_value = {c.release_id: 50 for c in candidates}

        result = run_recommend(client, store, cfg, max_recs=10)

    assert len(result.picks) == 10


def test_no_picks_when_no_seeds(setup) -> None:
    cfg, store, client = setup
    with patch("discogs.recommend.pipeline.select_seeds", return_value=[]):
        result = run_recommend(client, store, cfg, max_recs=10)
    assert result.picks == []
    assert result.candidate_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recommend_pipeline.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `src/discogs/recommend/pipeline.py`**

```python
"""Stage 5: orchestrate seeds → graph → scoring → final selection → history."""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field

from discogs.api.client import DiscogsClient
from discogs.api.releases import fetch_release
from discogs.cache.store import CacheStore
from discogs.config import Config
from discogs.models import Release
from discogs.recommend.graph import walk_credit_graph
from discogs.recommend.scoring import DEFAULT_WEIGHTS, ScoredCandidate, score_candidates
from discogs.recommend.seeds import select_seeds


@dataclass
class RunResult:
    run_id: str
    run_display_id: str
    picks: list[ScoredCandidate]
    seed_count: int
    candidate_count: int
    api_calls_used: int
    wall_seconds: float
    args: dict[str, object] = field(default_factory=dict)


def run_recommend(
    client: DiscogsClient,
    store: CacheStore,
    config: Config,
    *,
    max_recs: int = 25,
    max_per_artist: int = 3,
    seed_mode: str = "both",
    min_seed_occurrences: int = 2,
    max_neighbors_per_seed: int = 5,
    max_releases_per_neighbor: int = 25,
    budget: int = 800,
    weights: dict[str, float] | None = None,
) -> RunResult:
    """Run the full Phase 2 recommendation pipeline. Dry-run only (no wantlist writes)."""
    weights = weights or DEFAULT_WEIGHTS
    args = {
        "max_recs": max_recs, "max_per_artist": max_per_artist,
        "seed_mode": seed_mode, "min_seed_occurrences": min_seed_occurrences,
        "max_neighbors_per_seed": max_neighbors_per_seed,
        "max_releases_per_neighbor": max_releases_per_neighbor,
        "budget": budget,
    }
    run_id, display_id = store.start_run(args)

    started = time.monotonic()
    api_calls_at_start = store.api_calls_today()

    try:
        seeds = select_seeds(store, mode=seed_mode, min_occurrences=min_seed_occurrences)
        if not seeds:
            store.finish_run(run_id, summary={"seeds": 0, "candidates": 0, "selected": 0})
            return RunResult(
                run_id=run_id, run_display_id=display_id, picks=[],
                seed_count=0, candidate_count=0,
                api_calls_used=0, wall_seconds=time.monotonic() - started,
                args=args,
            )

        candidate_paths = walk_credit_graph(
            client, store, seeds,
            max_neighbors_per_seed=max_neighbors_per_seed,
            max_releases_per_neighbor=max_releases_per_neighbor,
            budget=budget,
        )

        releases = _load_releases(client, store, list(candidate_paths.keys()), budget_left=budget * 2)
        label_counts = _load_label_counts(store, list(candidate_paths.keys()))

        scored = score_candidates(
            store=store, candidate_paths=candidate_paths,
            releases=releases, label_release_counts=label_counts, weights=weights,
        )

        picks = _apply_diversity(scored, max_recs=max_recs, max_per_artist=max_per_artist)

        for p in picks:
            store.record_recommendation(run_id=run_id, release_id=p.release_id, score=p.score)

        api_calls_used = store.api_calls_today() - api_calls_at_start
        store.finish_run(run_id, summary={
            "seeds": len(seeds),
            "candidates": len(candidate_paths),
            "selected": len(picks),
            "api_calls_used": api_calls_used,
        })

        return RunResult(
            run_id=run_id, run_display_id=display_id, picks=picks,
            seed_count=len(seeds), candidate_count=len(candidate_paths),
            api_calls_used=api_calls_used,
            wall_seconds=time.monotonic() - started,
            args=args,
        )
    except Exception:
        store.finish_run(run_id, summary={"error": True})
        raise


def _apply_diversity(
    scored: list[ScoredCandidate], *, max_recs: int, max_per_artist: int,
) -> list[ScoredCandidate]:
    counts: Counter[int] = Counter()
    out: list[ScoredCandidate] = []
    for cand in scored:
        primary = cand.paths[0].seed_artist_id if cand.paths else -1
        if counts[primary] >= max_per_artist:
            continue
        out.append(cand)
        counts[primary] += 1
        if len(out) >= max_recs:
            break
    return out


def _load_releases(
    client: DiscogsClient, store: CacheStore, release_ids: list[int],
    *, budget_left: int,
) -> dict[int, Release]:
    """Load full Release objects for scoring. Cache hits cost 0; misses spend API budget."""
    out: dict[int, Release] = {}
    for rid in release_ids:
        if budget_left <= 0:
            break
        cached = store.get_release(rid)
        if cached is not None:
            out[rid] = cached
            continue
        out[rid] = fetch_release(client, store, rid)
        budget_left -= 1
    return out


def _load_label_counts(store: CacheStore, release_ids: list[int]) -> dict[int, int]:
    """For each candidate release, return the largest releases_count among its labels.

    Larger label = less obscure. Default 0 when no labels are known.
    """
    out: dict[int, int] = {}
    for rid in release_ids:
        label_ids = store.get_release_label_ids(rid)
        if not label_ids:
            out[rid] = 0
            continue
        placeholders = ",".join("?" for _ in label_ids)
        row = store.conn.execute(
            f"SELECT MAX(releases_count) AS rc FROM labels WHERE id IN ({placeholders})",
            tuple(label_ids),
        ).fetchone()
        out[rid] = int(row["rc"]) if row and row["rc"] is not None else 0
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_recommend_pipeline.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/recommend/pipeline.py tests/unit/test_recommend_pipeline.py
git commit -m "feat(recommend): pipeline composer with diversity guard + history writes"
```

---

## Task 14: Recommend — digest renderer

**Files:**
- Create: `src/discogs/recommend/digest.py`
- Test: `tests/unit/test_recommend_digest.py`

Renders the markdown digest. Read-only on the cache (looks up label names + release titles to embellish each pick). Returns a string the CLI writes to `~/.discogs/digests/<display_id>-recommendations.md`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_recommend_digest.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import Format, Label, Release
from discogs.recommend.digest import render_digest
from discogs.recommend.graph import GraphPath
from discogs.recommend.pipeline import RunResult
from discogs.recommend.scoring import ScoredCandidate


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def _setup_pick(store: CacheStore, release_id: int = 100) -> ScoredCandidate:
    rel = Release(
        id=release_id, master_id=None, title="Karma", year=1969, country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Spiritual Jazz", "Free Jazz"], genres=["Jazz"],
        community_have=2500, community_want=8000,
        community_avg_rating=4.6, community_rating_count=320,
        fetched_at=datetime.now(UTC),
    )
    store.upsert_release(rel)
    store.replace_release_labels(release_id, [(101, "AS-9181")])
    store.upsert_label(Label(
        id=101, name="Impulse!", parent_label=None, releases_count=200,
        fetched_at=datetime.now(UTC),
    ))
    from discogs.models import Artist
    store.upsert_artist(Artist(id=7, name="Pharoah Sanders", profile=None, fetched_at=datetime.now(UTC)))

    return ScoredCandidate(
        release_id=release_id, score=0.78,
        subscores={
            "connection": 0.92, "influence_chain": 0.0, "rarity": 0.5,
            "demand_ratio": 0.4, "label_obscurity": 0.4, "style_niche": 0.6,
            "rating": 0.8, "format": 1.0, "recency_match": 0.7,
        },
        paths=(GraphPath(
            seed_artist_id=7, seed_weight=0.94,
            edge_chain=((7, release_id, "direct"),), edge_weight=1.0,
        ),),
    )


def test_digest_includes_header_and_pick(store: CacheStore) -> None:
    pick = _setup_pick(store)
    result = RunResult(
        run_id="abc-123", run_display_id="2026-05-08-1830", picks=[pick],
        seed_count=8, candidate_count=247, api_calls_used=423, wall_seconds=494.0,
        args={"max_recs": 25},
    )
    md = render_digest(store, result)

    assert "# Discogs recommendations" in md
    assert "2026-05-08-1830" in md
    assert "Karma" in md
    assert "1969" in md
    assert "Impulse!" in md
    assert "0.78" in md
    assert "Pharoah Sanders" in md
    assert "Spiritual Jazz" in md


def test_digest_run_stats(store: CacheStore) -> None:
    pick = _setup_pick(store)
    result = RunResult(
        run_id="abc", run_display_id="2026-05-08-1830", picks=[pick],
        seed_count=8, candidate_count=247, api_calls_used=423, wall_seconds=494.0,
        args={},
    )
    md = render_digest(store, result)
    assert "423" in md
    assert "8m" in md or "494" in md  # wall time formatted
    assert "247" in md  # candidate count
    assert "8" in md   # seed count


def test_digest_handles_no_picks(store: CacheStore) -> None:
    result = RunResult(
        run_id="abc", run_display_id="2026-05-08-1830", picks=[],
        seed_count=0, candidate_count=0, api_calls_used=0, wall_seconds=1.0,
        args={},
    )
    md = render_digest(store, result)
    assert "no picks" in md.lower() or "0 selected" in md.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recommend_digest.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `src/discogs/recommend/digest.py`**

```python
"""Markdown digest renderer for a recommendation run."""
from __future__ import annotations

from discogs.cache.store import CacheStore
from discogs.recommend.pipeline import RunResult
from discogs.recommend.scoring import ScoredCandidate


def render_digest(store: CacheStore, result: RunResult) -> str:
    lines: list[str] = []
    lines.append(f"# Discogs recommendations — {result.run_display_id}\n")
    lines.append(f"Run: `{result.run_display_id}` (uuid: `{result.run_id}`)")
    lines.append(
        f"Seeds: {result.seed_count} artists  "
        f"Candidates: {result.candidate_count} considered → {len(result.picks)} selected\n"
    )

    if not result.picks:
        lines.append("_No picks this run._\n")
    else:
        for rank, pick in enumerate(result.picks, start=1):
            lines.append(_render_pick(store, rank, pick))

    lines.append("## Run stats\n")
    lines.append(f"- API calls: {result.api_calls_used}")
    lines.append(f"- Wall time: {_fmt_seconds(result.wall_seconds)}")
    if result.picks:
        primary_artists = {p.paths[0].seed_artist_id for p in result.picks if p.paths}
        lines.append(f"- Distinct seed artists in selection: {len(primary_artists)}")

    return "\n".join(lines) + "\n"


def _render_pick(store: CacheStore, rank: int, pick: ScoredCandidate) -> str:
    rel = store.get_release(pick.release_id)
    title = rel.title if rel else f"release #{pick.release_id}"
    year = rel.year if rel else 0
    fmt_str = _format_summary(rel) if rel else "?"
    have = rel.community_have if rel else 0
    want = rel.community_want if rel else 0
    rating = rel.community_avg_rating if rel else 0.0
    rating_count = rel.community_rating_count if rel else 0
    styles = ", ".join(rel.styles) if rel and rel.styles else ""

    label_ids = store.get_release_label_ids(pick.release_id)
    label_names: list[str] = []
    for lid in label_ids:
        lab = store.get_label(lid)
        if lab is not None:
            label_names.append(lab.name)
    label_str = ", ".join(label_names) if label_names else "—"

    primary_path = pick.paths[0] if pick.paths else None
    seed_artist = (
        store.get_artist(primary_path.seed_artist_id) if primary_path else None
    )
    seed_name = seed_artist.name if seed_artist else (
        f"artist #{primary_path.seed_artist_id}" if primary_path else "?"
    )
    chain_kind = "direct" if primary_path and len(primary_path.edge_chain) == 1 else "neighbor"

    parts = [
        f"## {rank}. {seed_name} — {title} ({year})  [score: {pick.score:.2f}]",
        f"- Label: {label_str}",
        f"- Format: {fmt_str}",
        f"- Discogs: {have:,} have / {want:,} want / {rating:.1f} avg ({rating_count} ratings)",
    ]
    if styles:
        parts.append(f"- Styles: {styles}")
    parts.append(f"- Connection: {seed_name} [{chain_kind}, weight {primary_path.seed_weight:.2f}]" if primary_path else "")
    parts.append("")
    return "\n".join(p for p in parts if p)


def _format_summary(rel) -> str:
    if not rel.formats:
        return "?"
    f = rel.formats[0]
    descs = ", ".join(f.descriptions) if f.descriptions else ""
    return f"{f.name}{f' ({descs})' if descs else ''}"


def _fmt_seconds(secs: float) -> str:
    secs_int = int(secs)
    if secs_int < 60:
        return f"{secs_int}s"
    return f"{secs_int // 60}m {secs_int % 60}s"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_recommend_digest.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/recommend/digest.py tests/unit/test_recommend_digest.py
git commit -m "feat(recommend): markdown digest renderer"
```

---

## Task 15: CLI — `discogs recommend`

**Files:**
- Create: `src/discogs/cli/commands/recommend.py`
- Modify: `src/discogs/cli/__main__.py` (register command)
- Modify: `src/discogs/config.py` (add `digests_dir`)
- Test: `tests/unit/test_cli_recommend.py`

Wires `run_recommend` → `render_digest` → write file. Phase 2 is dry-run only — no `--apply`. Adds `digests_dir` to Config (defaults to `~/.discogs/digests`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cli_recommend.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from discogs.cli.__main__ import cli
from discogs.recommend.pipeline import RunResult
from discogs.recommend.scoring import ScoredCandidate
from discogs.recommend.graph import GraphPath


def _seed_config(home: Path) -> None:
    cfg_dir = home / ".discogs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text('[discogs]\ntoken = "t"\nusername = "lorenzo"')


def test_recommend_writes_digest_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    pick = ScoredCandidate(
        release_id=42, score=0.7,
        subscores={"connection": 1.0, "influence_chain": 0.0},
        paths=(GraphPath(seed_artist_id=1, seed_weight=1.0,
                         edge_chain=((1, 42, "direct"),), edge_weight=1.0),),
    )
    fake_result = RunResult(
        run_id="u", run_display_id="2026-05-08-1830", picks=[pick],
        seed_count=1, candidate_count=10, api_calls_used=5, wall_seconds=1.5,
        args={},
    )

    with patch("discogs.cli.commands.recommend._build_pipeline_context") as bp, \
         patch("discogs.cli.commands.recommend.run_recommend", return_value=fake_result), \
         patch("discogs.cli.commands.recommend.render_digest", return_value="DIGEST_BODY"):
        bp.return_value = (MagicMock(), MagicMock(), MagicMock())   # client, store, cfg
        result = CliRunner().invoke(cli, ["recommend"])

    assert result.exit_code == 0, result.output
    digest_path = tmp_path / ".discogs" / "digests" / "2026-05-08-1830-recommendations.md"
    assert digest_path.exists()
    assert digest_path.read_text() == "DIGEST_BODY"
    assert "2026-05-08-1830" in result.output


def test_recommend_max_recs_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_result = RunResult(
        run_id="u", run_display_id="2026-05-08-1830", picks=[],
        seed_count=0, candidate_count=0, api_calls_used=0, wall_seconds=0.1,
        args={},
    )
    with patch("discogs.cli.commands.recommend._build_pipeline_context") as bp, \
         patch("discogs.cli.commands.recommend.run_recommend", return_value=fake_result) as rr, \
         patch("discogs.cli.commands.recommend.render_digest", return_value=""):
        bp.return_value = (MagicMock(), MagicMock(), MagicMock())
        CliRunner().invoke(cli, ["recommend", "--max-recs", "5"])

    rr.assert_called_once()
    kwargs = rr.call_args.kwargs
    assert kwargs["max_recs"] == 5


def test_recommend_does_not_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    result = CliRunner().invoke(cli, ["recommend", "--apply"])
    assert result.exit_code != 0
    assert "phase 4" in result.output.lower() or "not yet supported" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cli_recommend.py -v`
Expected: FAIL.

- [ ] **Step 3: Add `digests_dir` to `Config` in `src/discogs/config.py`**

Add a default-factory helper near the top:

```python
def _default_digests_dir() -> Path:
    return Path.home() / ".discogs" / "digests"
```

Add the field to the `Config` dataclass (alongside `cache_path`):

```python
    digests_dir: Path = field(default_factory=_default_digests_dir)
```

- [ ] **Step 4: Implement `src/discogs/cli/commands/recommend.py`**

```python
"""`discogs recommend` command — Phase 2 dry-run only."""
from __future__ import annotations

import click

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config, load_config
from discogs.recommend.digest import render_digest
from discogs.recommend.pipeline import run_recommend


def _build_pipeline_context() -> tuple[DiscogsClient, CacheStore, Config]:
    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store)
    return client, store, cfg


@click.command("recommend")
@click.option("--max-recs", type=int, default=25, show_default=True,
              help="Top-N picks per run after diversity guard.")
@click.option("--budget", type=int, default=800, show_default=True,
              help="Hard cap on API calls during the graph walk.")
@click.option("--scope", type=click.Choice(["collection", "wantlist", "both"]),
              default="both", show_default=True,
              help="Which library half supplies seed artists.")
@click.option("--apply", "apply_flag", is_flag=True,
              help="Push picks to your wantlist (NOT YET SUPPORTED — Phase 4).")
def recommend_cmd(max_recs: int, budget: int, scope: str, apply_flag: bool) -> None:
    """Generate top-N recommendations and write a markdown digest. Dry-run only."""
    if apply_flag:
        raise click.UsageError("--apply is not yet supported (Phase 4).")

    client, store, cfg = _build_pipeline_context()
    try:
        result = run_recommend(
            client, store, cfg,
            max_recs=max_recs, budget=budget, seed_mode=scope,  # type: ignore[arg-type]
        )

        digest_md = render_digest(store, result)

        cfg.digests_dir.mkdir(parents=True, exist_ok=True)
        digest_path = cfg.digests_dir / f"{result.run_display_id}-recommendations.md"
        digest_path.write_text(digest_md)

        click.echo(f"Wrote digest: {digest_path}")
        click.echo(
            f"  run_id: {result.run_display_id}  "
            f"seeds: {result.seed_count}  "
            f"candidates: {result.candidate_count}  "
            f"selected: {len(result.picks)}  "
            f"API calls: {result.api_calls_used}"
        )
    finally:
        store.close()
```

- [ ] **Step 5: Register the command in `src/discogs/cli/__main__.py`**

Add to the existing imports:

```python
from discogs.cli.commands.recommend import recommend_cmd
```

Add to the registrations:

```python
cli.add_command(recommend_cmd)
```

The full file becomes:

```python
"""`discogs` CLI root."""
from __future__ import annotations

import click

from discogs.cli.commands.auth import auth_group
from discogs.cli.commands.recommend import recommend_cmd
from discogs.cli.commands.status import status_cmd
from discogs.cli.commands.sync_cmd import sync_cmd


@click.group()
@click.version_option(package_name="discogs")
def cli() -> None:
    """Discogs collection sync and recommendation framework."""


cli.add_command(auth_group, name="auth")
cli.add_command(sync_cmd)
cli.add_command(status_cmd)
cli.add_command(recommend_cmd)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/unit/test_cli_recommend.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add src/discogs/cli/commands/recommend.py src/discogs/cli/__main__.py src/discogs/config.py tests/unit/test_cli_recommend.py
git commit -m "feat(cli): discogs recommend (dry-run, Phase 2)"
```

---

## Task 16: Phase 2 verification + docs update

**Files:**
- Modify: `README.md`

Final pass: ensure the full unit suite, ruff, and mypy all pass on the merged Phase 2 work, then update the README to reflect the new command.

- [ ] **Step 1: Run the full unit test suite**

Run: `pytest tests/unit/ -v`
Expected: all unit tests pass.

- [ ] **Step 2: Run lint**

Run: `ruff check src/ tests/`
Expected: 0 errors. If anything fires (most likely a missing `from __future__ import annotations` or an unused import in the new modules), run `ruff check --fix src/ tests/` and re-verify.

- [ ] **Step 3: Run mypy**

Run: `mypy src/`
Expected: 0 errors. The new modules use the same patterns as Phase 1 and inherit `ignore_missing_imports = True` from `mypy.ini`, so any failures are signal — fix them inline.

- [ ] **Step 4: Update README**

Replace the "Commands" table in `README.md` to add the `recommend` row:

```markdown
| Command | Purpose |
|---|---|
| `discogs auth set` | Save token to `~/.discogs/config.toml` (chmod 600) |
| `discogs sync [--scope collection\|wantlist\|both] [--force]` | Sync into local cache. 24h TTL by default. |
| `discogs status` | Show username, cache size, last sync, API budget |
| `discogs recommend [--max-recs 25] [--budget 800] [--scope ...]` | Generate top-N picks; writes a markdown digest under `~/.discogs/digests/`. Dry-run only in Phase 2. |
```

Add a "Recommend" section under Quickstart:

```markdown
## Recommendations (Phase 2)

After your first sync:

```bash
discogs recommend
# Wrote digest: ~/.discogs/digests/2026-05-08-1830-recommendations.md
```

Open the digest to review the top picks. Each pick lists the seed artist that surfaced it, the label, format, community stats, and styles. Phase 2 picks are scored on 8 sub-scores in `[0, 0.85]` — the missing 0.15 is `influence_chain_score`, populated in Phase 3.
```

- [ ] **Step 5: Smoke-test the recommend command (optional but recommended)**

Real-account test (requires an already-synced cache):

```bash
discogs recommend --max-recs 10 --budget 200
less ~/.discogs/digests/$(ls -t ~/.discogs/digests | head -1)
```

If the digest renders sensibly and the picks aren't already in your collection or wantlist, Phase 2 is functional.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: README describes discogs recommend (Phase 2)"
```

---

## Phase 2 verification checklist

After completing all tasks:

- [ ] `pytest` reports all tests passing (the integration cassette test from Phase 1 still skipped).
- [ ] `ruff check src/ tests/` reports 0 errors.
- [ ] `mypy src/` reports 0 errors.
- [ ] `discogs recommend --help` lists the new command and its flags.
- [ ] (Optional) Live smoke test: `discogs recommend --max-recs 5 --budget 100` writes a digest and the picks are sensible.

If all four pass, Phase 2 is complete. Phase 3 (influence expansion + LLM enrichment) is the next plan.

---

## Self-review notes

- **Spec coverage (Phase 2 portion):**
  - Stage 1 (Spec §"Recommendation engine / Stage 1") → Task 10.
  - Stage 2 graph walk (Spec §"Stage 2 — Candidate generation") → Task 11, with Tasks 1–9 building the cache + API surface it depends on.
  - Stage 3 scoring (Spec §"Stage 3 — Scoring") → Task 12. `influence_chain_score` is included in the score table at weight 0.15 but always evaluates to 0 in Phase 2 (decision recorded in plan header).
  - Stage 5 final selection (Spec §"Stage 5 — Final selection") → Task 13.
  - Digest format (Spec §"CLI surface" — recommend command output) → Task 14.
  - `discogs recommend` dry-run path (Spec §"CLI surface & data flow") → Task 15. `--apply` is intentionally rejected with a UsageError pointing at Phase 4.

- **Out of scope, deferred:**
  - Stage 1.5 influence expansion (artist_influences table, Claude calls) → Phase 3.
  - Stage 4 LLM enrichment (editorial notes, confidence boost/penalty) → Phase 3.
  - `--no-influences` / `--no-enrich` flags → Phase 3 (Phase 2's behavior is implicitly both: no influence walk, no LLM notes).
  - `discogs apply <run-id>`, `discogs undo-last-batch`, `discogs undo <run-id>` → Phase 4.

- **Future plans:**
  - **Plan 3 — Influences + Enrichment:** `artist_influences` cache table writes (already exists from Phase 1's schema), Stage 1.5 Claude prompt + search-resolution + 90-day cache, Stage 4 editorial notes (Claude-generated, confidence-tagged). New flags: `--no-influences`, `--no-enrich`. Score range extends from `[0, 0.85]` to `[0, 1]`.
  - **Plan 4 — Wantlist Writes:** `--apply` flag on `discogs recommend`, `discogs apply <run-id>`, `discogs undo-last-batch`, `discogs undo <run-id>`, `wantlist_audit` table for rollback. Confirmation-on-first-apply UX. Daily LLM budget, partial-failure handling on bulk wantlist writes.
