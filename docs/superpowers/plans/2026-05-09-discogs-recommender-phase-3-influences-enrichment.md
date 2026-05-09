# Discogs Recommender — Phase 3: Influences + LLM Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate Claude into the recommendation pipeline. Add Stage 1.5 (influence expansion via Claude → Discogs search resolution → cached `artist_influences` edges) and Stage 4 (LLM editorial notes per top-N candidate, with confidence-based score adjustment). Score range extends from `[0, 0.85]` to `[0, 1]` because `influence_chain_score` becomes non-zero.

**Architecture:** Two new pipeline stages bracketing the existing graph walk + scoring. New `api/llm.py` wraps the Anthropic SDK with daily-budget guard and prompt caching. New `api/search.py` resolves names → Discogs artist IDs. New `recommend/influences.py` (Stage 1.5) + `recommend/enrich.py` (Stage 4). Pipeline gets two opt-out flags (`--no-influences`, `--no-enrich`); both default to enabled. The graph walk's `GraphPath` gains a `seed_kind: Literal["direct", "influence"]` discriminator so scoring can compute `connection` (direct) and `influence_chain` (influence-derived) as separate normalized sub-scores.

**Tech Stack:** Python 3.11+, `anthropic` SDK, all existing deps.

**Spec reference:** `docs/superpowers/specs/2026-05-08-discogs-recommender-design.md` — this plan implements Build Sequence steps 5 (influence expansion) + 7 (LLM enrichment) and updates step 6 (scoring) to fully populate `influence_chain_score`.

**Phase 3 design decisions** (recorded from brainstorming, before writing this plan):

1. **Anthropic API mechanics**: use `anthropic.Anthropic().messages.create(...)` with prompt caching (system message marked `cache_control={"type": "ephemeral"}`). Structured output via JSON in the assistant's response, parsed with pydantic. No tool-use protocol — simpler.

2. **Search resolution gate**: accept the top search hit only if its `score ≥ 0.85`. **Skip the spec's "matching primary style" check** in v1; verifying it would cost an extra `client.artist(id)` per resolved name and the search-score gate alone is a strong filter. Revisit if we see false positives in real digests.

3. **Daily LLM budget**: default `100` Claude calls per day. Configurable via `~/.discogs/config.toml` `[llm] daily_budget`. Tracked in a new `_llm_call_counts` table mirroring the API call counter pattern.

4. **Model choice**: `claude-haiku-4-5-20251001` for both influence and enrichment by default (cheap, factual). User can override per-stage in config (`[llm] influences_model`, `[llm] enrich_model`).

5. **`influence_chain_score`** in scoring becomes a real value: same shape as `connection_score` but summed only over influence-derived paths, with a 0.6 decay factor baked into the seed weight at graph-walk time (per spec).

**Out of scope (deferred):**
- `--apply`, `discogs apply`, `discogs undo*` commands → Phase 4
- RYM v2 (replace Claude-derived influences with structured RYM data) → future
- LLM enrichment for influence rationales (e.g. "Pharoah Sanders → Alice Coltrane because…") → future, would require a separate prompt

---

## Task 1: Add anthropic dependency

**Files:**
- Modify: `pyproject.toml`

Add `anthropic` to runtime dependencies. Bump version to 0.2.0 to mark Phase 3.

- [ ] **Step 1: Update `pyproject.toml`**

Locate the existing `[project]` section. Change:

```toml
[project]
name = "discogs"
version = "0.1.0"
description = "Discogs collection sync and recommendation framework"
requires-python = ">=3.11"
dependencies = [
    "python3-discogs-client>=2.7",
    "click>=8.1",
    "rich>=13.0",
    "pydantic>=2.5",
]
```

to:

```toml
[project]
name = "discogs"
version = "0.2.0"
description = "Discogs collection sync and recommendation framework"
requires-python = ">=3.11"
dependencies = [
    "python3-discogs-client>=2.7",
    "click>=8.1",
    "rich>=13.0",
    "pydantic>=2.5",
    "anthropic>=0.40",
]
```

- [ ] **Step 2: Reinstall the dev environment**

```bash
source .venv/bin/activate
pip install -e ".[dev]" 2>&1 | tail -3
```

Expected: `Successfully installed ... anthropic-X.Y.Z ...` and exit 0.

- [ ] **Step 3: Verify the import works**

```bash
python3 -c "import anthropic; print(anthropic.__version__)"
```

Expected: a version string.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add anthropic SDK; bump to v0.2.0 for Phase 3"
```

---

## Task 2: Cache CRUD for LLM call counts

**Files:**
- Modify: `src/discogs/cache/schema.sql`
- Modify: `src/discogs/cache/store.py`
- Test: `tests/unit/test_cache_llm_calls.py`

Same shape as `_api_call_counts` from Phase 1. Drives the daily LLM budget guard.

- [ ] **Step 1: Append the new table to `src/discogs/cache/schema.sql`**

Add at the bottom of the file:

```sql
CREATE TABLE IF NOT EXISTS _llm_call_counts (
    day TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0
);
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_cache_llm_calls.py`:

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


def test_increment_llm_calls(store: CacheStore) -> None:
    assert store.llm_calls_today() == 0
    store.increment_llm_calls(3)
    store.increment_llm_calls(2)
    assert store.llm_calls_today() == 5


def test_yesterday_does_not_interfere(store: CacheStore) -> None:
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    store.conn.execute(
        "INSERT OR REPLACE INTO _llm_call_counts(day, count) VALUES (?, ?)",
        (yesterday.isoformat(), 999),
    )
    store.conn.commit()
    store.increment_llm_calls(7)
    assert store.llm_calls_today() == 7
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/unit/test_cache_llm_calls.py -v`
Expected: FAIL.

- [ ] **Step 4: Append methods to `CacheStore` in `src/discogs/cache/store.py`**

```python
    def increment_llm_calls(self, n: int = 1) -> None:
        today = datetime.now(UTC).date().isoformat()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO _llm_call_counts(day, count) VALUES (?, ?)
                ON CONFLICT(day) DO UPDATE SET count = count + excluded.count
                """,
                (today, n),
            )

    def llm_calls_today(self) -> int:
        today = datetime.now(UTC).date().isoformat()
        row = self.conn.execute(
            "SELECT count FROM _llm_call_counts WHERE day = ?", (today,)
        ).fetchone()
        return int(row["count"]) if row else 0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_cache_llm_calls.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/discogs/cache/schema.sql src/discogs/cache/store.py tests/unit/test_cache_llm_calls.py
git commit -m "feat(cache): _llm_call_counts table + increment/today methods"
```

---

## Task 3: Cache CRUD for artist_influences

**Files:**
- Modify: `src/discogs/cache/store.py`
- Test: `tests/unit/test_cache_influences.py`

The `artist_influences` table exists from Phase 1's schema. This task adds the read/write methods. Used by Stage 1.5 to dedupe Claude calls and surface influence edges to the graph walk.

Schema reminder:
```sql
CREATE TABLE artist_influences (
    source_artist_id INTEGER NOT NULL,
    influence_artist_id INTEGER NOT NULL,
    confidence TEXT NOT NULL CHECK(confidence IN ('high','medium','low')),
    source TEXT NOT NULL DEFAULT 'claude',
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source_artist_id, influence_artist_id, source)
);
```

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cache_influences.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import ArtistInfluence


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def test_replace_and_get_influences(store: CacheStore) -> None:
    edges = [
        ArtistInfluence(source_artist_id=1, influence_artist_id=2, confidence="high",
                        source="claude", fetched_at=datetime.now(UTC)),
        ArtistInfluence(source_artist_id=1, influence_artist_id=3, confidence="medium",
                        source="claude", fetched_at=datetime.now(UTC)),
    ]
    store.replace_artist_influences(source_artist_id=1, edges=edges)
    fetched = store.get_artist_influences(source_artist_id=1)
    assert {(e.influence_artist_id, e.confidence) for e in fetched} == {(2, "high"), (3, "medium")}


def test_replace_overwrites_same_source(store: CacheStore) -> None:
    e1 = ArtistInfluence(source_artist_id=1, influence_artist_id=2, confidence="high",
                         source="claude", fetched_at=datetime.now(UTC))
    e2 = ArtistInfluence(source_artist_id=1, influence_artist_id=99, confidence="low",
                         source="claude", fetched_at=datetime.now(UTC))
    store.replace_artist_influences(source_artist_id=1, edges=[e1])
    store.replace_artist_influences(source_artist_id=1, edges=[e2])
    fetched = store.get_artist_influences(source_artist_id=1)
    assert {e.influence_artist_id for e in fetched} == {99}


def test_get_influences_empty_when_missing(store: CacheStore) -> None:
    assert store.get_artist_influences(999) == []


def test_artist_influences_age(store: CacheStore) -> None:
    e = ArtistInfluence(source_artist_id=1, influence_artist_id=2, confidence="high",
                       source="claude",
                       fetched_at=datetime.now(UTC) - timedelta(days=10))
    store.replace_artist_influences(source_artist_id=1, edges=[e])
    age = store.artist_influences_age(source_artist_id=1)
    assert age is not None
    assert age > timedelta(days=9)


def test_artist_influences_age_none_when_missing(store: CacheStore) -> None:
    assert store.artist_influences_age(999) is None


def test_replace_does_not_touch_other_sources(store: CacheStore) -> None:
    """If we ever add a 'rym' source (Phase 4+), replacing 'claude' edges
    should not delete 'rym' edges for the same source artist."""
    claude_edge = ArtistInfluence(source_artist_id=1, influence_artist_id=2, confidence="high",
                                  source="claude", fetched_at=datetime.now(UTC))
    rym_edge = ArtistInfluence(source_artist_id=1, influence_artist_id=3, confidence="high",
                               source="rym", fetched_at=datetime.now(UTC))
    store.replace_artist_influences(source_artist_id=1, edges=[claude_edge, rym_edge])

    # Re-replacing only claude edges should leave rym_edge alone
    new_claude_edge = ArtistInfluence(source_artist_id=1, influence_artist_id=99, confidence="low",
                                      source="claude", fetched_at=datetime.now(UTC))
    store.replace_artist_influences(source_artist_id=1, edges=[new_claude_edge], source="claude")

    fetched = store.get_artist_influences(source_artist_id=1)
    assert {(e.influence_artist_id, e.source) for e in fetched} == {(99, "claude"), (3, "rym")}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cache_influences.py -v`
Expected: FAIL — `ArtistInfluence` model doesn't exist; methods don't exist.

- [ ] **Step 3: Add `ArtistInfluence` to `src/discogs/models.py`**

Append at the end of the file:

```python
class ArtistInfluence(BaseModel):
    source_artist_id: int
    influence_artist_id: int
    confidence: str  # 'high' | 'medium' | 'low'
    source: str = "claude"
    fetched_at: datetime
```

- [ ] **Step 4: Extend the TYPE_CHECKING import block in `src/discogs/cache/store.py`**

Locate the existing block and add `ArtistInfluence`:

```python
if TYPE_CHECKING:
    from discogs.models import (
        Artist, ArtistInfluence, CollectionItem, Credit, Label, Release, WantlistItem,
    )
```

- [ ] **Step 5: Append methods to `CacheStore` in `src/discogs/cache/store.py`**

```python
    def replace_artist_influences(
        self, source_artist_id: int, edges: list["ArtistInfluence"], *,
        source: str = "claude",
    ) -> None:
        """Replace influence edges for a (source_artist_id, source) pair atomically.

        edges with a different `source` value are inserted alongside (no delete).
        """
        with self.conn:
            self.conn.execute(
                "DELETE FROM artist_influences "
                "WHERE source_artist_id = ? AND source = ?",
                (source_artist_id, source),
            )
            self.conn.executemany(
                "INSERT INTO artist_influences "
                "(source_artist_id, influence_artist_id, confidence, source, fetched_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (e.source_artist_id, e.influence_artist_id, e.confidence, e.source,
                     e.fetched_at.isoformat())
                    for e in edges
                ],
            )

    def get_artist_influences(self, source_artist_id: int) -> list["ArtistInfluence"]:
        from discogs.models import ArtistInfluence
        rows = self.conn.execute(
            "SELECT source_artist_id, influence_artist_id, confidence, source, fetched_at "
            "FROM artist_influences WHERE source_artist_id = ?",
            (source_artist_id,),
        )
        return [
            ArtistInfluence(
                source_artist_id=r["source_artist_id"],
                influence_artist_id=r["influence_artist_id"],
                confidence=r["confidence"],
                source=r["source"],
                fetched_at=datetime.fromisoformat(r["fetched_at"]),
            )
            for r in rows
        ]

    def artist_influences_age(self, source_artist_id: int) -> timedelta | None:
        row = self.conn.execute(
            "SELECT MIN(fetched_at) AS oldest FROM artist_influences "
            "WHERE source_artist_id = ?",
            (source_artist_id,),
        ).fetchone()
        if row is None or row["oldest"] is None:
            return None
        return datetime.now(UTC) - datetime.fromisoformat(row["oldest"])
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/unit/test_cache_influences.py -v`
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add src/discogs/models.py src/discogs/cache/store.py tests/unit/test_cache_influences.py
git commit -m "feat(cache): ArtistInfluence model + replace/get/age (per-source)"
```

---

## Task 4: API search wrapper

**Files:**
- Create: `src/discogs/api/search.py`
- Test: `tests/unit/test_api_search.py`

Wraps `client.upstream.search(name, type='artist')` and returns the top hit's id + name + score, filtered by a minimum score threshold. Used by Stage 1.5 to resolve Claude-named influences to Discogs artist IDs.

`python3-discogs-client` exposes search via `client.search(query, type='artist')`. Each hit has `.id`, `.title` (which is the artist name), and the API includes a `score` field accessible via the underlying `data` dict.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_api_search.py`:

```python
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import DiscogsClient
from discogs.api.search import resolve_artist_name
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config


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


def _hit(rid: int, title: str, score: float) -> MagicMock:
    h = MagicMock()
    h.id = rid
    h.title = title
    h.data = {"score": score}
    return h


def test_resolve_returns_top_hit_above_threshold(setup) -> None:
    _, client = setup
    client.upstream.search.return_value = [
        _hit(1, "Pharoah Sanders", 0.95),
        _hit(2, "Pharoah Sanders Quartet", 0.7),
    ]

    result = resolve_artist_name(client, "Pharoah Sanders", min_score=0.85)
    assert result is not None
    assert result == (1, "Pharoah Sanders")


def test_resolve_returns_none_below_threshold(setup) -> None:
    _, client = setup
    client.upstream.search.return_value = [
        _hit(1, "Pharoah Sanders", 0.5),
    ]
    assert resolve_artist_name(client, "Pharoah Sanders", min_score=0.85) is None


def test_resolve_returns_none_when_no_hits(setup) -> None:
    _, client = setup
    client.upstream.search.return_value = []
    assert resolve_artist_name(client, "Imaginary Person", min_score=0.85) is None


def test_resolve_handles_missing_score_field(setup) -> None:
    """If the search API doesn't include a score field, treat as 0 and reject."""
    _, client = setup
    h = MagicMock()
    h.id = 1; h.title = "X"; h.data = {}
    client.upstream.search.return_value = [h]
    assert resolve_artist_name(client, "X", min_score=0.85) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_api_search.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `src/discogs/api/search.py`**

```python
"""Discogs database search wrapper."""
from __future__ import annotations

from discogs.api.client import DiscogsClient


def resolve_artist_name(
    client: DiscogsClient, name: str, *, min_score: float = 0.85,
) -> tuple[int, str] | None:
    """Search Discogs for an artist by name; return (id, canonical_name) for the
    top hit if its score >= min_score, else None.

    The caller is responsible for spending the API call budget — `client.call("search", ...)`
    increments the daily counter automatically.
    """
    hits = client.call("search", name, type="artist")
    for hit in hits:
        score = float(hit.data.get("score", 0)) if hasattr(hit, "data") else 0.0
        if score >= min_score:
            return int(hit.id), str(hit.title)
        return None
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_api_search.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/api/search.py tests/unit/test_api_search.py
git commit -m "feat(api): resolve_artist_name search wrapper with score gate"
```

---

## Task 5: Anthropic client wrapper (LLM budget + prompt caching)

**Files:**
- Create: `src/discogs/api/llm.py`
- Test: `tests/unit/test_api_llm.py`

Wraps `anthropic.Anthropic().messages.create(...)` with daily LLM budget tracking, default model selection, and prompt-caching defaults. Mirrors the shape of `DiscogsClient` for consistency.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_api_llm.py`:

```python
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.llm import LLMBudgetExceeded, LLMClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db",
        anthropic_api_key="sk-test",
        daily_llm_budget=3,
    )


@pytest.fixture
def store(cfg: Config) -> Iterator[CacheStore]:
    init_db(cfg.cache_path)
    s = CacheStore(cfg.cache_path)
    yield s
    s.close()


def _fake_response(text: str = '{"items":[]}') -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    msg.usage = MagicMock(input_tokens=10, output_tokens=20,
                          cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return msg


def test_call_increments_budget(cfg: Config, store: CacheStore) -> None:
    upstream = MagicMock()
    upstream.messages.create.return_value = _fake_response()
    client = LLMClient(cfg, store, upstream_factory=lambda **kw: upstream)

    assert store.llm_calls_today() == 0
    client.complete(system="sys", user="hi")
    assert store.llm_calls_today() == 1


def test_budget_exceeded_raises(cfg: Config, store: CacheStore) -> None:
    upstream = MagicMock()
    upstream.messages.create.return_value = _fake_response()
    client = LLMClient(cfg, store, upstream_factory=lambda **kw: upstream)
    client.complete(system="sys", user="a")
    client.complete(system="sys", user="b")
    client.complete(system="sys", user="c")
    with pytest.raises(LLMBudgetExceeded):
        client.complete(system="sys", user="d")


def test_complete_passes_cache_control(cfg: Config, store: CacheStore) -> None:
    upstream = MagicMock()
    upstream.messages.create.return_value = _fake_response()
    client = LLMClient(cfg, store, upstream_factory=lambda **kw: upstream)
    client.complete(system="long stable system prompt", user="q1")

    call = upstream.messages.create.call_args
    system_arg = call.kwargs["system"]
    # System should be a list of blocks with cache_control on the last one
    assert isinstance(system_arg, list)
    assert system_arg[-1]["cache_control"] == {"type": "ephemeral"}


def test_complete_returns_response_text(cfg: Config, store: CacheStore) -> None:
    upstream = MagicMock()
    upstream.messages.create.return_value = _fake_response(text="hello world")
    client = LLMClient(cfg, store, upstream_factory=lambda **kw: upstream)
    text = client.complete(system="sys", user="hi")
    assert text == "hello world"


def test_complete_uses_configured_model(cfg: Config, store: CacheStore) -> None:
    upstream = MagicMock()
    upstream.messages.create.return_value = _fake_response()
    client = LLMClient(cfg, store, upstream_factory=lambda **kw: upstream)
    client.complete(system="sys", user="hi", model="claude-sonnet-4-6")
    assert upstream.messages.create.call_args.kwargs["model"] == "claude-sonnet-4-6"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_api_llm.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `src/discogs/api/llm.py`**

```python
"""Wrapper around the Anthropic SDK with daily budget tracking and prompt caching."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import anthropic

from discogs.cache.store import CacheStore
from discogs.config import Config

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class LLMBudgetExceeded(RuntimeError):
    """Raised when the daily LLM call budget is exhausted."""


class LLMClient:
    def __init__(
        self,
        config: Config,
        store: CacheStore,
        *,
        upstream_factory: Callable[..., Any] = anthropic.Anthropic,
    ) -> None:
        self._config = config
        self._store = store
        self._upstream = upstream_factory(api_key=config.anthropic_api_key or "")

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 1024,
    ) -> str:
        """Send a single-turn message; return the assistant's text response.

        The system prompt is wrapped in a cache-control block so repeated calls with
        the same system message are nearly free after the first.
        """
        if self._store.llm_calls_today() >= self._config.daily_llm_budget:
            raise LLMBudgetExceeded(
                f"Daily LLM call budget of {self._config.daily_llm_budget} exceeded. "
                "Wait until tomorrow or raise daily_llm_budget in config."
            )

        msg = self._upstream.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        self._store.increment_llm_calls(1)

        text_blocks = [b.text for b in msg.content if hasattr(b, "text")]
        return "".join(text_blocks)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_api_llm.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/api/llm.py tests/unit/test_api_llm.py
git commit -m "feat(api): LLMClient — Anthropic wrapper with daily budget + caching"
```

---

## Task 6: Config — daily LLM budget + model overrides

**Files:**
- Modify: `src/discogs/config.py`
- Modify: `tests/unit/test_config.py`

Add three fields to Config and the corresponding TOML loaders. `daily_llm_budget` defaults to 100, `influences_model` and `enrich_model` both default to `claude-haiku-4-5-20251001`.

- [ ] **Step 1: Add the new test cases to `tests/unit/test_config.py`**

Append these tests:

```python
def test_load_config_default_llm_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[discogs]\ntoken = "abc"\nusername = "u"')
    cfg = load_config(config_path)
    assert cfg.daily_llm_budget == 100
    assert cfg.influences_model == "claude-haiku-4-5-20251001"
    assert cfg.enrich_model == "claude-haiku-4-5-20251001"


def test_load_config_llm_overrides(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[discogs]\ntoken = "abc"\nusername = "u"\n'
        '[llm]\ndaily_budget = 250\n'
        'influences_model = "claude-sonnet-4-6"\n'
        'enrich_model = "claude-opus-4-7"\n'
    )
    cfg = load_config(config_path)
    assert cfg.daily_llm_budget == 250
    assert cfg.influences_model == "claude-sonnet-4-6"
    assert cfg.enrich_model == "claude-opus-4-7"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_config.py -v`
Expected: 2 new tests fail with AttributeError.

- [ ] **Step 3: Add the fields and loader logic to `src/discogs/config.py`**

In the `Config` dataclass, add these fields (alongside `daily_api_budget`):

```python
    daily_llm_budget: int = 100
    influences_model: str = "claude-haiku-4-5-20251001"
    enrich_model: str = "claude-haiku-4-5-20251001"
```

In `load_config`, after the existing `anthropic_key = ...` line, add:

```python
    llm_section = data.get("llm", {})
    daily_llm_budget = int(llm_section.get("daily_budget", 100))
    influences_model = str(llm_section.get("influences_model", "claude-haiku-4-5-20251001"))
    enrich_model = str(llm_section.get("enrich_model", "claude-haiku-4-5-20251001"))
```

Then pass them into the `Config(...)` constructor at the bottom:

```python
    return Config(
        discogs_token=token,
        discogs_username=username,
        anthropic_api_key=anthropic_key,
        cache_path=cache_path,
        daily_llm_budget=daily_llm_budget,
        influences_model=influences_model,
        enrich_model=enrich_model,
    )
```

Update `Config.__repr__` to include the new fields (so they show up in `discogs status` and debug logs):

```python
    def __repr__(self) -> str:
        return (
            f"Config(discogs_token='***', discogs_username={self.discogs_username!r}, "
            f"anthropic_api_key={'***' if self.anthropic_api_key else None}, "
            f"cache_path={self.cache_path!r}, digests_dir={self.digests_dir!r}, "
            f"user_agent={self.user_agent!r}, "
            f"daily_api_budget={self.daily_api_budget}, "
            f"daily_llm_budget={self.daily_llm_budget}, "
            f"influences_model={self.influences_model!r}, "
            f"enrich_model={self.enrich_model!r})"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_config.py -v`
Expected: all tests pass (existing 5 + new 2 = 7).

- [ ] **Step 5: Commit**

```bash
git add src/discogs/config.py tests/unit/test_config.py
git commit -m "feat(config): daily_llm_budget + influences_model + enrich_model with TOML overrides"
```

---

## Task 7: Influences module — Claude prompt + parse

**Files:**
- Create: `src/discogs/recommend/influences.py`
- Test: `tests/unit/test_recommend_influences.py`

Asks Claude for influences of a given artist; parses the structured JSON response. Pure function — no Discogs search yet (that's Task 8).

The prompt template comes from the spec:

> Given the artist `<name>` (Discogs id `<id>`, primary styles `<styles>`), list 5–10 artists who clearly influenced them. For each: `{name, confidence: "high" | "medium" | "low", note: short justification}`. Only name artists you are confident exist on Discogs and that you have factual knowledge of. If unsure, return fewer names.

Output format: a JSON object with an `items` array.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_recommend_influences.py`:

```python
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.llm import LLMClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.recommend.influences import (
    InfluenceCandidate,
    fetch_influences_from_claude,
)


@pytest.fixture
def llm(tmp_path: Path) -> Iterator[LLMClient]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db",
        anthropic_api_key="sk-test", daily_llm_budget=10,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    upstream = MagicMock()
    yield LLMClient(cfg, store, upstream_factory=lambda **kw: upstream)
    store.close()


def test_parses_well_formed_json(llm: LLMClient) -> None:
    fake_text = (
        '{"items": ['
        '{"name": "John Coltrane", "confidence": "high", "note": "spiritual jazz lineage"},'
        '{"name": "Sun Ra", "confidence": "medium", "note": "experimental kinship"}'
        ']}'
    )
    llm._upstream.messages.create.return_value = MagicMock(
        content=[MagicMock(text=fake_text)],
        usage=MagicMock(input_tokens=0, output_tokens=0,
                        cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )

    result = fetch_influences_from_claude(
        llm, artist_name="Pharoah Sanders", artist_id=7,
        primary_styles=["Spiritual Jazz"],
    )
    assert {c.name for c in result} == {"John Coltrane", "Sun Ra"}
    assert {c.confidence for c in result} == {"high", "medium"}


def test_returns_empty_list_on_malformed_json(llm: LLMClient) -> None:
    llm._upstream.messages.create.return_value = MagicMock(
        content=[MagicMock(text="not valid json {{{")],
        usage=MagicMock(input_tokens=0, output_tokens=0,
                        cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )
    result = fetch_influences_from_claude(
        llm, artist_name="X", artist_id=1, primary_styles=[],
    )
    assert result == []


def test_drops_items_with_invalid_confidence(llm: LLMClient) -> None:
    fake_text = (
        '{"items": ['
        '{"name": "A", "confidence": "high", "note": "ok"},'
        '{"name": "B", "confidence": "very-high", "note": "bad confidence"}'
        ']}'
    )
    llm._upstream.messages.create.return_value = MagicMock(
        content=[MagicMock(text=fake_text)],
        usage=MagicMock(input_tokens=0, output_tokens=0,
                        cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )
    result = fetch_influences_from_claude(llm, artist_name="X", artist_id=1, primary_styles=[])
    assert {c.name for c in result} == {"A"}


def test_includes_styles_in_prompt(llm: LLMClient) -> None:
    llm._upstream.messages.create.return_value = MagicMock(
        content=[MagicMock(text='{"items":[]}')],
        usage=MagicMock(input_tokens=0, output_tokens=0,
                        cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )
    fetch_influences_from_claude(
        llm, artist_name="Pharoah Sanders", artist_id=7,
        primary_styles=["Spiritual Jazz", "Free Jazz"],
    )
    call = llm._upstream.messages.create.call_args
    user_msg = call.kwargs["messages"][0]["content"]
    assert "Pharoah Sanders" in user_msg
    assert "7" in user_msg
    assert "Spiritual Jazz" in user_msg
    assert "Free Jazz" in user_msg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recommend_influences.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `src/discogs/recommend/influences.py`**

```python
"""Stage 1.5: ask Claude for an artist's influences (no resolution yet)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from discogs.api.llm import LLMClient

Confidence = Literal["high", "medium", "low"]
_VALID_CONFIDENCES = {"high", "medium", "low"}

_SYSTEM_PROMPT = """You are a music historian assisting a record collector.
You answer with strict JSON only. No prose, no markdown, no preamble or
postscript — just the JSON object."""


@dataclass(frozen=True)
class InfluenceCandidate:
    name: str
    confidence: Confidence
    note: str


def fetch_influences_from_claude(
    llm: LLMClient, *, artist_name: str, artist_id: int,
    primary_styles: list[str],
) -> list[InfluenceCandidate]:
    """Ask Claude for 5-10 artists who influenced `artist_name`.

    Returns an empty list on parse failure or malformed items rather than raising —
    the caller wants to continue the run, just without this seed's influence edges.
    """
    styles_str = ", ".join(primary_styles) if primary_styles else "(unknown)"
    user = (
        f"Given the artist {artist_name!r} "
        f"(Discogs id {artist_id}, primary styles {styles_str}), "
        f"list 5-10 artists who clearly influenced them. For each, output an item "
        f'in the JSON array under key "items" with fields:\n'
        f'  name (string),\n'
        f'  confidence ("high" | "medium" | "low"),\n'
        f'  note (one short sentence justifying the influence).\n'
        f"Only include artists you are confident exist on Discogs and that you have "
        f"factual knowledge of. If unsure, return fewer names.\n\n"
        f'Output format: {{"items": [{{...}}, {{...}}]}}'
    )

    raw = llm.complete(system=_SYSTEM_PROMPT, user=user)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    items = data.get("items", []) if isinstance(data, dict) else []
    out: list[InfluenceCandidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        confidence = item.get("confidence")
        note = item.get("note", "")
        if not isinstance(name, str) or not isinstance(confidence, str):
            continue
        if confidence not in _VALID_CONFIDENCES:
            continue
        out.append(InfluenceCandidate(
            name=name, confidence=confidence,  # type: ignore[arg-type]
            note=str(note),
        ))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_recommend_influences.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/recommend/influences.py tests/unit/test_recommend_influences.py
git commit -m "feat(recommend): fetch_influences_from_claude — JSON-strict prompt + parse"
```

---

## Task 8: Influences module — resolve names + persist

**Files:**
- Modify: `src/discogs/recommend/influences.py`
- Test: `tests/unit/test_recommend_influences_resolve.py`

Adds the second half of Stage 1.5: take `InfluenceCandidate`s from Claude, resolve each name to a Discogs artist id via `resolve_artist_name` (Task 4), persist as `ArtistInfluence` edges, and return the resolved set. Cache TTL: 90 days (per spec). On a cache hit within TTL, skip the Claude call entirely.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_recommend_influences_resolve.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discogs.api.client import DiscogsClient
from discogs.api.llm import LLMClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.models import ArtistInfluence
from discogs.recommend.influences import (
    InfluenceCandidate,
    expand_influences,
)


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[CacheStore, DiscogsClient, LLMClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db",
        anthropic_api_key="sk-test", daily_llm_budget=10, daily_api_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    discogs_client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    llm = LLMClient(cfg, store, upstream_factory=lambda **kw: MagicMock())
    yield store, discogs_client, llm
    store.close()


def test_expand_uses_cache_when_fresh(setup) -> None:
    store, dc, llm = setup
    fresh = ArtistInfluence(source_artist_id=7, influence_artist_id=99,
                           confidence="high", source="claude",
                           fetched_at=datetime.now(UTC))
    store.replace_artist_influences(source_artist_id=7, edges=[fresh])

    with patch("discogs.recommend.influences.fetch_influences_from_claude") as f, \
         patch("discogs.recommend.influences.resolve_artist_name") as r:
        result = expand_influences(
            dc, store, llm, artist_id=7, artist_name="Test", primary_styles=[],
        )
        f.assert_not_called()
        r.assert_not_called()
    assert {e.influence_artist_id for e in result} == {99}


def test_expand_calls_claude_and_resolves(setup) -> None:
    store, dc, llm = setup
    candidates = [
        InfluenceCandidate(name="John Coltrane", confidence="high", note="lineage"),
        InfluenceCandidate(name="Sun Ra", confidence="medium", note="kinship"),
    ]
    with patch("discogs.recommend.influences.fetch_influences_from_claude",
               return_value=candidates) as f, \
         patch("discogs.recommend.influences.resolve_artist_name",
               side_effect=[(101, "John Coltrane"), (102, "Sun Ra")]) as r:
        result = expand_influences(
            dc, store, llm, artist_id=7, artist_name="Pharoah Sanders",
            primary_styles=["Spiritual Jazz"],
        )

    assert f.call_count == 1
    assert r.call_count == 2
    assert {(e.influence_artist_id, e.confidence) for e in result} == {
        (101, "high"), (102, "medium"),
    }
    cached = store.get_artist_influences(source_artist_id=7)
    assert {e.influence_artist_id for e in cached} == {101, 102}


def test_expand_drops_unresolved_names(setup) -> None:
    store, dc, llm = setup
    candidates = [
        InfluenceCandidate(name="X", confidence="high", note=""),
        InfluenceCandidate(name="Y", confidence="medium", note=""),
    ]
    with patch("discogs.recommend.influences.fetch_influences_from_claude",
               return_value=candidates), \
         patch("discogs.recommend.influences.resolve_artist_name",
               side_effect=[None, (200, "Y")]):
        result = expand_influences(dc, store, llm, artist_id=7, artist_name="Z",
                                   primary_styles=[])
    assert {e.influence_artist_id for e in result} == {200}


def test_expand_refreshes_when_stale(setup) -> None:
    store, dc, llm = setup
    stale = ArtistInfluence(source_artist_id=7, influence_artist_id=99,
                           confidence="high", source="claude",
                           fetched_at=datetime.now(UTC) - timedelta(days=100))
    store.replace_artist_influences(source_artist_id=7, edges=[stale])

    with patch("discogs.recommend.influences.fetch_influences_from_claude",
               return_value=[]) as f:
        expand_influences(dc, store, llm, artist_id=7, artist_name="X",
                         primary_styles=[])
        f.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recommend_influences_resolve.py -v`
Expected: FAIL — `expand_influences` not implemented.

- [ ] **Step 3: Append `expand_influences` to `src/discogs/recommend/influences.py`**

Add these imports at the top (alongside the existing imports):

```python
from datetime import UTC, datetime, timedelta

from discogs.api.client import DiscogsClient
from discogs.api.search import resolve_artist_name
from discogs.cache.store import CacheStore
from discogs.models import ArtistInfluence
```

Add this constant after `_VALID_CONFIDENCES`:

```python
INFLUENCES_TTL = timedelta(days=90)
```

Add this function at the bottom of the file:

```python
def expand_influences(
    discogs_client: DiscogsClient,
    store: CacheStore,
    llm: LLMClient,
    *,
    artist_id: int,
    artist_name: str,
    primary_styles: list[str],
) -> list[ArtistInfluence]:
    """Return influence edges for `artist_id`. Cache hit when entries are < 90 days
    old. On miss: ask Claude, resolve each candidate via Discogs search, persist
    the resolved set, and return it.

    Unresolved names are dropped silently (no edge persisted, no error raised).
    """
    age = store.artist_influences_age(source_artist_id=artist_id)
    if age is not None and age < INFLUENCES_TTL:
        return store.get_artist_influences(source_artist_id=artist_id)

    candidates = fetch_influences_from_claude(
        llm, artist_name=artist_name, artist_id=artist_id,
        primary_styles=primary_styles,
    )

    now = datetime.now(UTC)
    resolved: list[ArtistInfluence] = []
    for cand in candidates:
        hit = resolve_artist_name(discogs_client, cand.name)
        if hit is None:
            continue
        influence_id, _ = hit
        resolved.append(ArtistInfluence(
            source_artist_id=artist_id,
            influence_artist_id=influence_id,
            confidence=cand.confidence,
            source="claude",
            fetched_at=now,
        ))

    store.replace_artist_influences(source_artist_id=artist_id, edges=resolved,
                                    source="claude")
    return resolved
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_recommend_influences_resolve.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/recommend/influences.py tests/unit/test_recommend_influences_resolve.py
git commit -m "feat(recommend): expand_influences — resolve + persist + 90d cache"
```

---

## Task 9: GraphPath gains seed_kind discriminator

**Files:**
- Modify: `src/discogs/recommend/graph.py`
- Modify: `src/discogs/recommend/seeds.py`
- Test: `tests/unit/test_recommend_graph_kinds.py`

Adds `seed_kind: Literal["direct", "influence"]` to `GraphPath` and `SeedArtist`. The graph walk preserves the kind from seed → candidate. Scoring (Task 10) uses this to split paths into direct vs influence-derived contributions.

This is a backward-compatible-ish change: `seed_kind` defaults to `"direct"` so existing tests keep passing.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_recommend_graph_kinds.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.models import Credit, Format, Release
from discogs.recommend.graph import GraphPath, walk_credit_graph
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


def test_seed_artist_default_kind_is_direct() -> None:
    s = SeedArtist(artist_id=1, weight=0.5, sources=("collection",))
    assert s.seed_kind == "direct"


def test_seed_artist_can_be_influence() -> None:
    s = SeedArtist(artist_id=1, weight=0.5, sources=(), seed_kind="influence")
    assert s.seed_kind == "influence"


def test_graph_path_carries_seed_kind(setup) -> None:
    store, client = setup
    seeds = [SeedArtist(artist_id=7, weight=0.5, sources=("collection",), seed_kind="influence")]

    with patch("discogs.recommend.graph.fetch_artist_releases") as far, \
         patch("discogs.recommend.graph.fetch_release") as fr:
        far.return_value = [101]
        fr.return_value = _stub_release(101, credits=[])
        store.replace_release_credits(101, [])
        paths = walk_credit_graph(client, store, seeds, budget=10)

    assert 101 in paths
    assert paths[101][0].seed_kind == "influence"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recommend_graph_kinds.py -v`
Expected: FAIL.

- [ ] **Step 3: Update `SeedArtist` in `src/discogs/recommend/seeds.py`**

Find:

```python
@dataclass(frozen=True)
class SeedArtist:
    artist_id: int
    weight: float            # in [0.1, 1.0]
    sources: tuple[str, ...] # subset of ("collection", "wantlist")
```

Replace with:

```python
SeedKind = Literal["direct", "influence"]


@dataclass(frozen=True)
class SeedArtist:
    artist_id: int
    weight: float            # in [0.1, 1.0]
    sources: tuple[str, ...] # subset of ("collection", "wantlist")
    seed_kind: SeedKind = "direct"
```

- [ ] **Step 4: Update `GraphPath` in `src/discogs/recommend/graph.py`**

Find:

```python
@dataclass(frozen=True)
class GraphPath:
    """..."""
    seed_artist_id: int
    seed_weight: float
    edge_chain: tuple[tuple[int, int, str], ...]
    edge_weight: float  # product of role weights along the chain
```

Replace with:

```python
@dataclass(frozen=True)
class GraphPath:
    """..."""
    seed_artist_id: int
    seed_weight: float
    edge_chain: tuple[tuple[int, int, str], ...]
    edge_weight: float  # product of role weights along the chain
    seed_kind: str = "direct"  # "direct" | "influence"
```

In `walk_credit_graph`, when constructing GraphPath instances, propagate `seed_kind=seed.seed_kind`. Find both construction sites:

```python
paths[release_id].append(GraphPath(
    seed_artist_id=seed.artist_id,
    seed_weight=seed.weight,
    edge_chain=((seed.artist_id, release_id, "direct"),),
    edge_weight=1.0,
))
```

Add `seed_kind=seed.seed_kind,` to both.

```python
paths[nr_id].append(GraphPath(
    seed_artist_id=seed.artist_id,
    seed_weight=seed.weight,
    edge_chain=(...),
    edge_weight=role_weight(neighbor_role),
))
```

Same — add `seed_kind=seed.seed_kind,`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_recommend_graph_kinds.py tests/unit/test_recommend_graph.py tests/unit/test_recommend_seeds.py -v`
Expected: all tests pass (existing + 3 new).

- [ ] **Step 6: Commit**

```bash
git add src/discogs/recommend/graph.py src/discogs/recommend/seeds.py tests/unit/test_recommend_graph_kinds.py
git commit -m "feat(recommend): GraphPath + SeedArtist gain seed_kind discriminator"
```

---

## Task 10: Scoring — real influence_chain_score

**Files:**
- Modify: `src/discogs/recommend/scoring.py`
- Test: `tests/unit/test_recommend_scoring_influence.py`

`influence_chain_score` was hardcoded to 0.0 in Phase 2. Now it's computed: same shape as `connection_score` but summed only over paths whose `seed_kind == "influence"`, normalized against the per-set max.

`connection_score` is also tightened — now sums only over paths whose `seed_kind == "direct"`. (In Phase 2 it summed over all paths, but all paths were direct, so the behavior is unchanged for direct-only runs.)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_recommend_scoring_influence.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import Format, Release
from discogs.recommend.graph import GraphPath
from discogs.recommend.scoring import DEFAULT_WEIGHTS, score_candidates


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def _release(rid: int) -> Release:
    return Release(
        id=rid, master_id=None, title=f"r{rid}", year=1975, country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Jazz"], genres=["Jazz"],
        community_have=1000, community_want=500,
        community_avg_rating=4.2, community_rating_count=50,
        fetched_at=datetime.now(UTC),
    )


def _direct(rid: int) -> GraphPath:
    return GraphPath(seed_artist_id=1, seed_weight=0.9,
                     edge_chain=((1, rid, "direct"),), edge_weight=1.0,
                     seed_kind="direct")


def _influence(rid: int) -> GraphPath:
    return GraphPath(seed_artist_id=2, seed_weight=0.6,
                     edge_chain=((2, rid, "direct"),), edge_weight=1.0,
                     seed_kind="influence")


def test_influence_chain_score_nonzero_when_influence_paths_present(store: CacheStore) -> None:
    paths = {
        100: [_direct(100), _influence(100)],
    }
    scored = score_candidates(
        store=store, candidate_paths=paths,
        releases={100: _release(100)}, label_release_counts={100: 50},
        weights=DEFAULT_WEIGHTS,
    )
    assert scored[0].subscores["influence_chain"] > 0.0
    assert scored[0].subscores["connection"] > 0.0


def test_score_in_full_range_with_influence(store: CacheStore) -> None:
    """Scores can now reach up to 1.0 (no longer capped at 0.85)."""
    paths = {1: [_direct(1), _influence(1)]}
    scored = score_candidates(
        store=store, candidate_paths=paths, releases={1: _release(1)},
        label_release_counts={1: 50}, weights=DEFAULT_WEIGHTS,
    )
    # We can't easily prove >0.85 in a unit test, but the upper bound is now 1.0.
    assert scored[0].score <= 1.0


def test_pure_direct_paths_have_zero_influence_score(store: CacheStore) -> None:
    paths = {1: [_direct(1)]}
    scored = score_candidates(
        store=store, candidate_paths=paths, releases={1: _release(1)},
        label_release_counts={1: 50}, weights=DEFAULT_WEIGHTS,
    )
    assert scored[0].subscores["influence_chain"] == 0.0
    assert scored[0].subscores["connection"] > 0.0


def test_pure_influence_paths_have_zero_connection_score(store: CacheStore) -> None:
    paths = {1: [_influence(1)]}
    scored = score_candidates(
        store=store, candidate_paths=paths, releases={1: _release(1)},
        label_release_counts={1: 50}, weights=DEFAULT_WEIGHTS,
    )
    assert scored[0].subscores["connection"] == 0.0
    assert scored[0].subscores["influence_chain"] > 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recommend_scoring_influence.py -v`
Expected: FAIL — `influence_chain` is still 0 in the implementation.

- [ ] **Step 3: Update `score_candidates` in `src/discogs/recommend/scoring.py`**

Find these lines:

```python
    raw_connections: dict[int, float] = {
        rid: sum(p.seed_weight * p.edge_weight for p in ps)
        for rid, ps in candidate_paths.items()
    }
    max_conn = max(raw_connections.values()) or 1.0
```

Replace with:

```python
    raw_connections: dict[int, float] = {
        rid: sum(p.seed_weight * p.edge_weight for p in ps if p.seed_kind == "direct")
        for rid, ps in candidate_paths.items()
    }
    max_conn = max(raw_connections.values()) or 1.0

    raw_influences: dict[int, float] = {
        rid: sum(p.seed_weight * p.edge_weight for p in ps if p.seed_kind == "influence")
        for rid, ps in candidate_paths.items()
    }
    max_infl = max(raw_influences.values()) or 1.0
```

In the per-candidate sub-score dict, find:

```python
        sub = {
            "connection": raw_connections[rid] / max_conn,
            "influence_chain": 0.0,
            ...
        }
```

Replace `"influence_chain": 0.0,` with:

```python
            "influence_chain": raw_influences[rid] / max_infl,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_recommend_scoring_influence.py tests/unit/test_recommend_scoring.py -v`
Expected: all tests pass. Note that `test_score_in_range_and_total_le_085` from Phase 2 still asserts `≤ 0.85` and that holds for direct-only paths, so no change needed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/recommend/scoring.py tests/unit/test_recommend_scoring_influence.py
git commit -m "feat(recommend): real influence_chain_score from seed_kind=='influence' paths"
```

---

## Task 11: Pipeline integrates Stage 1.5

**Files:**
- Modify: `src/discogs/recommend/pipeline.py`
- Test: `tests/unit/test_recommend_pipeline_influences.py`

Adds an opt-out `with_influences: bool` parameter to `run_recommend`. When true (default), after seed selection: for the top-K seeds (default 20), call `expand_influences` to populate `artist_influences`, then add resolved influence artists as additional `SeedArtist`s with `seed_kind="influence"` and `seed_weight = original_seed_weight × confidence_factor × 0.6`. The 0.6 decay comes from the spec.

`confidence_factor`:
- `"high"` → 1.0
- `"medium"` → 0.7
- `"low"` → 0.4

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_recommend_pipeline_influences.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discogs.api.client import DiscogsClient
from discogs.api.llm import LLMClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.models import ArtistInfluence
from discogs.recommend.pipeline import run_recommend
from discogs.recommend.scoring import ScoredCandidate
from discogs.recommend.seeds import SeedArtist


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[Config, CacheStore, DiscogsClient, LLMClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db",
        anthropic_api_key="sk-test", daily_api_budget=10000, daily_llm_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    llm = LLMClient(cfg, store, upstream_factory=lambda **kw: MagicMock())
    yield cfg, store, client, llm
    store.close()


def test_pipeline_invokes_expand_influences_by_default(setup) -> None:
    cfg, store, client, llm = setup

    seed = SeedArtist(artist_id=1, weight=1.0, sources=("collection",), seed_kind="direct")

    with patch("discogs.recommend.pipeline.select_seeds", return_value=[seed]), \
         patch("discogs.recommend.pipeline.expand_influences", return_value=[]) as ei, \
         patch("discogs.recommend.pipeline._prefetch_library_releases", return_value=0), \
         patch("discogs.recommend.pipeline.walk_credit_graph", return_value={}), \
         patch("discogs.recommend.pipeline.score_candidates", return_value=[]), \
         patch("discogs.recommend.pipeline._load_releases", return_value={}), \
         patch("discogs.recommend.pipeline._load_label_counts", return_value={}):
        run_recommend(client, store, cfg, llm=llm, max_recs=5)

    ei.assert_called()


def test_pipeline_skips_influences_when_disabled(setup) -> None:
    cfg, store, client, llm = setup

    seed = SeedArtist(artist_id=1, weight=1.0, sources=("collection",), seed_kind="direct")

    with patch("discogs.recommend.pipeline.select_seeds", return_value=[seed]), \
         patch("discogs.recommend.pipeline.expand_influences") as ei, \
         patch("discogs.recommend.pipeline._prefetch_library_releases", return_value=0), \
         patch("discogs.recommend.pipeline.walk_credit_graph", return_value={}), \
         patch("discogs.recommend.pipeline.score_candidates", return_value=[]), \
         patch("discogs.recommend.pipeline._load_releases", return_value={}), \
         patch("discogs.recommend.pipeline._load_label_counts", return_value={}):
        run_recommend(client, store, cfg, llm=llm, max_recs=5, with_influences=False)

    ei.assert_not_called()


def test_pipeline_adds_influence_seeds_with_decayed_weight(setup) -> None:
    cfg, store, client, llm = setup

    direct_seed = SeedArtist(artist_id=1, weight=1.0, sources=("collection",), seed_kind="direct")
    fake_influence = ArtistInfluence(
        source_artist_id=1, influence_artist_id=99, confidence="high",
        source="claude", fetched_at=datetime.now(UTC),
    )

    captured_seeds: list[list[SeedArtist]] = []

    def capture_walk(client, store, seeds, **kw):
        captured_seeds.append(list(seeds))
        return {}

    with patch("discogs.recommend.pipeline.select_seeds", return_value=[direct_seed]), \
         patch("discogs.recommend.pipeline.expand_influences",
               return_value=[fake_influence]), \
         patch("discogs.recommend.pipeline._prefetch_library_releases", return_value=0), \
         patch("discogs.recommend.pipeline.walk_credit_graph",
               side_effect=capture_walk), \
         patch("discogs.recommend.pipeline.score_candidates", return_value=[]), \
         patch("discogs.recommend.pipeline._load_releases", return_value={}), \
         patch("discogs.recommend.pipeline._load_label_counts", return_value={}):
        run_recommend(client, store, cfg, llm=llm, max_recs=5)

    seeds = captured_seeds[0]
    influence_seeds = [s for s in seeds if s.seed_kind == "influence"]
    assert len(influence_seeds) == 1
    inf = influence_seeds[0]
    assert inf.artist_id == 99
    # high confidence (1.0) * direct_seed weight (1.0) * 0.6 decay = 0.6
    assert abs(inf.weight - 0.6) < 0.01
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recommend_pipeline_influences.py -v`
Expected: FAIL.

- [ ] **Step 3: Update `src/discogs/recommend/pipeline.py`**

Add new imports at the top:

```python
from discogs.api.llm import LLMClient
from discogs.recommend.influences import expand_influences
```

Add the influence-decay constants near the top of the module:

```python
_INFLUENCE_DECAY = 0.6
_CONFIDENCE_FACTOR = {"high": 1.0, "medium": 0.7, "low": 0.4}
```

Update `run_recommend`'s signature: add `llm: LLMClient | None = None` and `with_influences: bool = True` and `top_k_seeds_for_influences: int = 20` keyword-only parameters. Add them to the `args` dict so they're persisted on the run.

In the body, after `seeds = select_seeds(...)`, add:

```python
        if with_influences and llm is not None and seeds:
            seeds = _expand_seed_pool_with_influences(
                client, store, llm, seeds, top_k=top_k_seeds_for_influences,
            )
```

Add the helper at the bottom of the file:

```python
def _expand_seed_pool_with_influences(
    client: DiscogsClient,
    store: CacheStore,
    llm: LLMClient,
    seeds: list[SeedArtist],
    *,
    top_k: int,
) -> list[SeedArtist]:
    """For the top `top_k` direct seeds (by weight), fetch Claude-derived
    influences and append them as additional SeedArtists with seed_kind='influence'.

    Decayed weight = original_seed_weight * confidence_factor * 0.6.
    """
    direct_seeds = [s for s in seeds if s.seed_kind == "direct"]
    direct_seeds.sort(key=lambda s: -s.weight)
    pool = list(seeds)
    seen_influence_ids: set[int] = set()

    for seed in direct_seeds[:top_k]:
        artist = store.get_artist(seed.artist_id)
        artist_name = artist.name if artist is not None else f"artist-{seed.artist_id}"
        styles = []  # Phase 3 v1: skip per-artist style lookup; Phase 4 / future can wire it.

        edges = expand_influences(
            client, store, llm,
            artist_id=seed.artist_id,
            artist_name=artist_name,
            primary_styles=styles,
        )

        for edge in edges:
            if edge.influence_artist_id in seen_influence_ids:
                continue
            seen_influence_ids.add(edge.influence_artist_id)

            factor = _CONFIDENCE_FACTOR.get(edge.confidence, 0.4)
            decayed = seed.weight * factor * _INFLUENCE_DECAY
            decayed = max(0.05, min(1.0, decayed))

            pool.append(SeedArtist(
                artist_id=edge.influence_artist_id,
                weight=decayed,
                sources=(),
                seed_kind="influence",
            ))

    return pool
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_recommend_pipeline_influences.py tests/unit/test_recommend_pipeline.py -v`
Expected: all tests pass. The existing Phase 2 pipeline tests should still pass — they don't pass `llm=` so the influence branch is skipped.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/recommend/pipeline.py tests/unit/test_recommend_pipeline_influences.py
git commit -m "feat(recommend): pipeline expands seed pool with Claude-derived influences"
```

---

## Task 12: Enrich module — Claude editorial notes

**Files:**
- Create: `src/discogs/recommend/enrich.py`
- Test: `tests/unit/test_recommend_enrich.py`

Stage 4. For a list of top candidates: ask Claude for 2-3 sentence notes per release with confidence tags. Apply confidence-based score adjustment (high → +0.05, low → -0.03). Store note + confidence on the candidate as a `ScoredCandidate.enrichment` field (NEW field — backward-compat default `None`).

Batches up to 10 candidates per Claude call to amortize prompt overhead.

- [ ] **Step 1: Add `enrichment` field to `ScoredCandidate`**

Find in `src/discogs/recommend/scoring.py`:

```python
@dataclass(frozen=True)
class ScoredCandidate:
    release_id: int
    score: float
    subscores: dict[str, float]
    paths: tuple[GraphPath, ...]
```

Replace with:

```python
@dataclass(frozen=True)
class Enrichment:
    note: str
    confidence: str  # "high" | "medium" | "low"


@dataclass(frozen=True)
class ScoredCandidate:
    release_id: int
    score: float
    subscores: dict[str, float]
    paths: tuple[GraphPath, ...]
    enrichment: Enrichment | None = None
```

Run `pytest tests/unit/test_recommend_scoring.py -v` to confirm existing tests still pass with the new optional field. Expected: green.

- [ ] **Step 2: Write the failing test for enrich**

Create `tests/unit/test_recommend_enrich.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.llm import LLMClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.models import Format, Release
from discogs.recommend.enrich import enrich_candidates
from discogs.recommend.graph import GraphPath
from discogs.recommend.scoring import ScoredCandidate


@pytest.fixture
def llm(tmp_path: Path) -> Iterator[LLMClient]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db",
        anthropic_api_key="sk-test", daily_llm_budget=10,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    yield LLMClient(cfg, store, upstream_factory=lambda **kw: MagicMock())
    store.close()


def _candidate(rid: int, score: float = 0.7) -> ScoredCandidate:
    return ScoredCandidate(
        release_id=rid, score=score,
        subscores={"connection": 1.0, "influence_chain": 0.0,
                   "rarity": 0.5, "demand_ratio": 0.4, "label_obscurity": 0.4,
                   "style_niche": 0.5, "rating": 0.7, "format": 1.0,
                   "recency_match": 0.5},
        paths=(GraphPath(seed_artist_id=1, seed_weight=0.9,
                         edge_chain=((1, rid, "direct"),), edge_weight=1.0,
                         seed_kind="direct"),),
    )


def _release_lookup(rid: int) -> Release:
    return Release(
        id=rid, master_id=None, title=f"r{rid}", year=1970, country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Jazz"], genres=["Jazz"],
        community_have=100, community_want=200,
        community_avg_rating=4.0, community_rating_count=50,
        fetched_at=datetime.now(UTC),
    )


def test_enrich_attaches_notes(llm: LLMClient) -> None:
    cands = [_candidate(1, score=0.7), _candidate(2, score=0.6)]
    releases = {1: _release_lookup(1), 2: _release_lookup(2)}
    fake_response = (
        '{"items":['
        '{"release_id":1,"note":"important early-period work","confidence":"high"},'
        '{"release_id":2,"note":"interesting curio","confidence":"medium"}'
        ']}'
    )
    llm._upstream.messages.create.return_value = MagicMock(
        content=[MagicMock(text=fake_response)],
        usage=MagicMock(input_tokens=0, output_tokens=0,
                        cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )

    enriched = enrich_candidates(llm, cands, releases)
    e1 = next(c for c in enriched if c.release_id == 1)
    e2 = next(c for c in enriched if c.release_id == 2)
    assert e1.enrichment is not None and e1.enrichment.confidence == "high"
    assert e2.enrichment is not None and e2.enrichment.confidence == "medium"


def test_enrich_applies_score_boost_and_penalty(llm: LLMClient) -> None:
    cands = [_candidate(1, score=0.5), _candidate(2, score=0.5)]
    releases = {1: _release_lookup(1), 2: _release_lookup(2)}
    fake_response = (
        '{"items":['
        '{"release_id":1,"note":"hidden classic","confidence":"high"},'
        '{"release_id":2,"note":"unsure","confidence":"low"}'
        ']}'
    )
    llm._upstream.messages.create.return_value = MagicMock(
        content=[MagicMock(text=fake_response)],
        usage=MagicMock(input_tokens=0, output_tokens=0,
                        cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )

    enriched = enrich_candidates(llm, cands, releases)
    e1 = next(c for c in enriched if c.release_id == 1)
    e2 = next(c for c in enriched if c.release_id == 2)
    assert e1.score == pytest.approx(0.55, abs=0.001)   # +0.05
    assert e2.score == pytest.approx(0.47, abs=0.001)   # -0.03


def test_enrich_returns_originals_on_parse_failure(llm: LLMClient) -> None:
    cands = [_candidate(1)]
    releases = {1: _release_lookup(1)}
    llm._upstream.messages.create.return_value = MagicMock(
        content=[MagicMock(text="not json")],
        usage=MagicMock(input_tokens=0, output_tokens=0,
                        cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )
    enriched = enrich_candidates(llm, cands, releases)
    assert enriched[0].enrichment is None
    assert enriched[0].score == 0.7  # unchanged


def test_enrich_batches_into_chunks_of_10(llm: LLMClient) -> None:
    cands = [_candidate(i, score=0.5) for i in range(25)]
    releases = {i: _release_lookup(i) for i in range(25)}
    llm._upstream.messages.create.return_value = MagicMock(
        content=[MagicMock(text='{"items":[]}')],
        usage=MagicMock(input_tokens=0, output_tokens=0,
                        cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )

    enrich_candidates(llm, cands, releases)
    # 25 candidates → ceil(25/10) = 3 batches
    assert llm._upstream.messages.create.call_count == 3
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/unit/test_recommend_enrich.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement `src/discogs/recommend/enrich.py`**

```python
"""Stage 4: ask Claude for short editorial notes per top candidate."""
from __future__ import annotations

import json

from discogs.api.llm import LLMClient
from discogs.models import Release
from discogs.recommend.scoring import Enrichment, ScoredCandidate

BATCH_SIZE = 10
_BOOST = {"high": 0.05, "medium": 0.0, "low": -0.03}

_SYSTEM_PROMPT = """You are a music historian writing concise, factual notes about
specific Discogs releases. Use what you know about the artist, the label, and the era.
Do not speculate. If you don't know, say so.

Respond with strict JSON only — no prose, no markdown, no preamble."""


def enrich_candidates(
    llm: LLMClient,
    candidates: list[ScoredCandidate],
    releases: dict[int, Release],
) -> list[ScoredCandidate]:
    """For each candidate, attach an Enrichment (note + confidence) and adjust the
    score by ±0.05/±0.03 based on confidence. Original candidates with no Claude
    coverage are returned unchanged.
    """
    if not candidates:
        return []

    notes: dict[int, Enrichment] = {}
    for batch_start in range(0, len(candidates), BATCH_SIZE):
        batch = candidates[batch_start:batch_start + BATCH_SIZE]
        notes.update(_enrich_one_batch(llm, batch, releases))

    out: list[ScoredCandidate] = []
    for cand in candidates:
        ench = notes.get(cand.release_id)
        if ench is None:
            out.append(cand)
            continue
        adjusted = max(0.0, min(1.0, cand.score + _BOOST.get(ench.confidence, 0.0)))
        out.append(ScoredCandidate(
            release_id=cand.release_id, score=adjusted,
            subscores=cand.subscores, paths=cand.paths,
            enrichment=ench,
        ))
    return out


def _enrich_one_batch(
    llm: LLMClient,
    batch: list[ScoredCandidate],
    releases: dict[int, Release],
) -> dict[int, Enrichment]:
    items = []
    for cand in batch:
        rel = releases.get(cand.release_id)
        if rel is None:
            continue
        items.append({
            "release_id": cand.release_id,
            "title": rel.title,
            "year": rel.year,
            "styles": rel.styles,
        })

    if not items:
        return {}

    user = (
        f"For each of the following Discogs releases, write a 2-3 sentence note "
        f"explaining why it might matter to a collector. Highlight: notable "
        f"personnel when known, scene/era context, what makes it distinctive. "
        f'Output JSON: {{"items": [{{"release_id": <int>, "note": <str>, '
        f'"confidence": "high"|"medium"|"low"}}, ...]}}\n\n'
        f"Releases:\n{json.dumps(items, indent=2)}"
    )

    raw = llm.complete(system=_SYSTEM_PROMPT, user=user)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    items_out = data.get("items", []) if isinstance(data, dict) else []
    result: dict[int, Enrichment] = {}
    for item in items_out:
        if not isinstance(item, dict):
            continue
        rid = item.get("release_id")
        note = item.get("note")
        conf = item.get("confidence")
        if not isinstance(rid, int) or not isinstance(note, str):
            continue
        if conf not in {"high", "medium", "low"}:
            continue
        result[rid] = Enrichment(note=note, confidence=conf)
    return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_recommend_enrich.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/discogs/recommend/scoring.py src/discogs/recommend/enrich.py tests/unit/test_recommend_enrich.py
git commit -m "feat(recommend): Stage 4 — Claude editorial notes batched 10/call with score adjust"
```

---

## Task 13: Pipeline integrates Stage 4

**Files:**
- Modify: `src/discogs/recommend/pipeline.py`
- Test: `tests/unit/test_recommend_pipeline_enrich.py`

Adds `with_enrichment: bool = True` to `run_recommend`. When true and `llm` is provided, between scoring and diversity-guarded final selection: take the top `2 * max_recs` candidates, call `enrich_candidates`, re-sort by adjusted score, then apply diversity guard.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_recommend_pipeline_enrich.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discogs.api.client import DiscogsClient
from discogs.api.llm import LLMClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.models import Format, Release
from discogs.recommend.graph import GraphPath
from discogs.recommend.pipeline import run_recommend
from discogs.recommend.scoring import Enrichment, ScoredCandidate


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[Config, CacheStore, DiscogsClient, LLMClient]]:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db",
        anthropic_api_key="sk-test", daily_api_budget=10000, daily_llm_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    llm = LLMClient(cfg, store, upstream_factory=lambda **kw: MagicMock())
    yield cfg, store, client, llm
    store.close()


def _release(rid: int) -> Release:
    return Release(
        id=rid, master_id=None, title=f"r{rid}", year=1970, country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Jazz"], genres=["Jazz"],
        community_have=1000, community_want=500,
        community_avg_rating=4.0, community_rating_count=20,
        fetched_at=datetime.now(UTC),
    )


def _scored(rid: int, score: float) -> ScoredCandidate:
    return ScoredCandidate(
        release_id=rid, score=score,
        subscores={"connection": 1.0},
        paths=(GraphPath(seed_artist_id=1, seed_weight=1.0,
                         edge_chain=((1, rid, "direct"),), edge_weight=1.0,
                         seed_kind="direct"),),
    )


def test_pipeline_invokes_enrich_by_default(setup) -> None:
    cfg, store, client, llm = setup
    cands = [_scored(rid=i, score=0.5) for i in range(20)]

    with patch("discogs.recommend.pipeline._build_recommendation_inputs",
               return_value=(["seed"], {i: cands[i].paths for i in range(20)})), \
         patch("discogs.recommend.pipeline.score_candidates", return_value=cands), \
         patch("discogs.recommend.pipeline.enrich_candidates",
               return_value=cands) as enrich, \
         patch("discogs.recommend.pipeline._load_releases",
               return_value={i: _release(i) for i in range(20)}), \
         patch("discogs.recommend.pipeline._load_label_counts",
               return_value={i: 50 for i in range(20)}):
        run_recommend(client, store, cfg, llm=llm, max_recs=5)

    enrich.assert_called_once()


def test_pipeline_skips_enrich_when_disabled(setup) -> None:
    cfg, store, client, llm = setup
    cands = [_scored(rid=i, score=0.5) for i in range(5)]

    with patch("discogs.recommend.pipeline._build_recommendation_inputs",
               return_value=(["seed"], {i: cands[i].paths for i in range(5)})), \
         patch("discogs.recommend.pipeline.score_candidates", return_value=cands), \
         patch("discogs.recommend.pipeline.enrich_candidates") as enrich, \
         patch("discogs.recommend.pipeline._load_releases",
               return_value={i: _release(i) for i in range(5)}), \
         patch("discogs.recommend.pipeline._load_label_counts",
               return_value={i: 50 for i in range(5)}):
        run_recommend(client, store, cfg, llm=llm, max_recs=5, with_enrichment=False)

    enrich.assert_not_called()


def test_enrichment_resorts_picks(setup) -> None:
    """Enrichment can boost a lower-scored candidate above a higher-scored one."""
    cfg, store, client, llm = setup
    raw = [_scored(rid=1, score=0.6), _scored(rid=2, score=0.5)]
    enriched = [
        ScoredCandidate(release_id=1, score=0.6, subscores=raw[0].subscores,
                        paths=raw[0].paths,
                        enrichment=Enrichment(note="meh", confidence="low")),  # 0.6 -> 0.57
        ScoredCandidate(release_id=2, score=0.55, subscores=raw[1].subscores,
                        paths=raw[1].paths,
                        enrichment=Enrichment(note="great", confidence="high")),  # 0.5 -> 0.55
    ]

    with patch("discogs.recommend.pipeline._build_recommendation_inputs",
               return_value=(["seed"], {i: raw[i-1].paths for i in (1, 2)})), \
         patch("discogs.recommend.pipeline.score_candidates", return_value=raw), \
         patch("discogs.recommend.pipeline.enrich_candidates",
               return_value=enriched), \
         patch("discogs.recommend.pipeline._load_releases",
               return_value={1: _release(1), 2: _release(2)}), \
         patch("discogs.recommend.pipeline._load_label_counts",
               return_value={1: 50, 2: 50}):
        result = run_recommend(client, store, cfg, llm=llm, max_recs=5)

    assert result.picks[0].release_id == 2  # boosted above 1
```

NOTE on `_build_recommendation_inputs`: this is a refactor we'll introduce in Step 3 to consolidate seeds + graph walk into a single helper that's easier to mock as a unit. If you'd rather keep the existing structure, patch `select_seeds` + `_expand_seed_pool_with_influences` + `walk_credit_graph` + `_prefetch_library_releases` individually.

For simplicity, this test uses the consolidated helper. If you don't refactor, adapt the test to patch the four individual stages instead.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recommend_pipeline_enrich.py -v`
Expected: FAIL.

- [ ] **Step 3: Update `run_recommend` in `src/discogs/recommend/pipeline.py`**

Add the `enrich_candidates` import:

```python
from discogs.recommend.enrich import enrich_candidates
```

Add `with_enrichment: bool = True` to `run_recommend`'s keyword-only parameters and persist it in the `args` dict.

In the body, between `scored = score_candidates(...)` and `picks = _apply_diversity(...)`, add:

```python
        if with_enrichment and llm is not None and scored:
            head = scored[: max_recs * 2]
            tail = scored[max_recs * 2 :]
            enriched_head = enrich_candidates(llm, head, releases)
            enriched_head.sort(key=lambda s: -s.score)
            scored = enriched_head + tail
```

Also extract the seeds + influence-expansion + graph-walk into a private helper `_build_recommendation_inputs(client, store, llm, ..., with_influences) -> tuple[list[SeedArtist], dict[int, list[GraphPath]]]` so the pipeline test in Task 13 can mock it cleanly. (Optional refactor — the test in Step 1 uses this helper. If you skip the refactor, change the test patches.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_recommend_pipeline_enrich.py tests/unit/test_recommend_pipeline.py tests/unit/test_recommend_pipeline_influences.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/recommend/pipeline.py tests/unit/test_recommend_pipeline_enrich.py
git commit -m "feat(recommend): pipeline runs Stage 4 enrichment between scoring and selection"
```

---

## Task 14: Digest renders editorial notes

**Files:**
- Modify: `src/discogs/recommend/digest.py`
- Test: `tests/unit/test_recommend_digest_enrichment.py`

When a pick has `pick.enrichment is not None`, render the note as a "> " quote line under the pick. Tag with confidence (e.g., `[Claude, confidence: high]`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_recommend_digest_enrichment.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import Format, Release
from discogs.recommend.digest import render_digest
from discogs.recommend.graph import GraphPath
from discogs.recommend.pipeline import RunResult
from discogs.recommend.scoring import Enrichment, ScoredCandidate


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CacheStore]:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def test_digest_renders_enrichment_note(store: CacheStore) -> None:
    rel = Release(
        id=100, master_id=None, title="Karma", year=1969, country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Spiritual Jazz"], genres=["Jazz"],
        community_have=2500, community_want=8000,
        community_avg_rating=4.6, community_rating_count=320,
        fetched_at=datetime.now(UTC),
    )
    store.upsert_release(rel)

    pick = ScoredCandidate(
        release_id=100, score=0.78,
        subscores={"connection": 1.0, "influence_chain": 0.0, "rarity": 0.5,
                   "demand_ratio": 0.4, "label_obscurity": 0.4, "style_niche": 0.5,
                   "rating": 0.7, "format": 1.0, "recency_match": 0.5},
        paths=(GraphPath(seed_artist_id=7, seed_weight=0.94,
                         edge_chain=((7, 100, "direct"),), edge_weight=1.0,
                         seed_kind="direct"),),
        enrichment=Enrichment(
            note="A landmark of spiritual jazz. Sanders' tenor leads a long-form modal "
                 "meditation backed by Lonnie Liston Smith's piano.",
            confidence="high",
        ),
    )

    result = RunResult(
        run_id="abc", run_display_id="2026-05-09-1830", picks=[pick],
        seed_count=1, candidate_count=1, api_calls_used=5, wall_seconds=2.0,
        args={},
    )
    md = render_digest(store, result)

    assert "spiritual jazz" in md.lower()
    assert "Lonnie Liston Smith" in md
    assert "confidence: high" in md.lower() or "[high]" in md.lower()


def test_digest_skips_enrichment_when_absent(store: CacheStore) -> None:
    rel = Release(
        id=100, master_id=None, title="Karma", year=1969, country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Spiritual Jazz"], genres=["Jazz"],
        community_have=2500, community_want=8000,
        community_avg_rating=4.6, community_rating_count=320,
        fetched_at=datetime.now(UTC),
    )
    store.upsert_release(rel)

    pick = ScoredCandidate(
        release_id=100, score=0.78,
        subscores={"connection": 1.0, "influence_chain": 0.0},
        paths=(GraphPath(seed_artist_id=7, seed_weight=0.94,
                         edge_chain=((7, 100, "direct"),), edge_weight=1.0,
                         seed_kind="direct"),),
        enrichment=None,
    )
    result = RunResult(
        run_id="abc", run_display_id="2026-05-09-1830", picks=[pick],
        seed_count=1, candidate_count=1, api_calls_used=5, wall_seconds=2.0,
        args={},
    )
    md = render_digest(store, result)
    assert "confidence:" not in md.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recommend_digest_enrichment.py -v`
Expected: FAIL.

- [ ] **Step 3: Update `_render_pick` in `src/discogs/recommend/digest.py`**

Find the `parts.append("")` near the bottom of `_render_pick`. Just before that line, add:

```python
    if pick.enrichment is not None:
        note = pick.enrichment.note.strip()
        confidence = pick.enrichment.confidence
        parts.append(f"> {note}")
        parts.append(f"> *(Claude editorial — confidence: {confidence})*")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_recommend_digest_enrichment.py tests/unit/test_recommend_digest.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/recommend/digest.py tests/unit/test_recommend_digest_enrichment.py
git commit -m "feat(recommend): digest renders Claude editorial notes when present"
```

---

## Task 15: CLI flags for influences and enrichment

**Files:**
- Modify: `src/discogs/cli/commands/recommend.py`
- Test: `tests/unit/test_cli_recommend_phase3.py`

Adds `--no-influences` and `--no-enrich` flags. When neither is passed, build an LLMClient and pass it into `run_recommend`. When the user has no `anthropic_api_key` configured, fall back to `with_influences=False, with_enrichment=False` and print a warning.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cli_recommend_phase3.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from discogs.cli.__main__ import cli
from discogs.config import load_config
from discogs.recommend.pipeline import RunResult


def _seed_config(home: Path, *, with_anthropic: bool = True) -> None:
    cfg_dir = home / ".discogs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    body = '[discogs]\ntoken = "t"\nusername = "u"\n'
    if with_anthropic:
        body += '[anthropic]\napi_key = "sk-test"\n'
    (cfg_dir / "config.toml").write_text(body)


def _empty_run(display_id: str = "2026-05-09-1830") -> RunResult:
    return RunResult(
        run_id="u", run_display_id=display_id, picks=[],
        seed_count=0, candidate_count=0, api_calls_used=0, wall_seconds=0.1,
        args={},
    )


def test_recommend_passes_llm_when_configured(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path, with_anthropic=True)

    with patch("discogs.cli.commands.recommend._build_pipeline_context",
               return_value=(MagicMock(), MagicMock(), load_config())), \
         patch("discogs.cli.commands.recommend._build_llm_client") as build_llm, \
         patch("discogs.cli.commands.recommend.run_recommend",
               return_value=_empty_run()) as rr, \
         patch("discogs.cli.commands.recommend.render_digest", return_value="DIGEST"):
        build_llm.return_value = MagicMock()
        result = CliRunner().invoke(cli, ["recommend"])

    assert result.exit_code == 0, result.output
    assert rr.call_args.kwargs.get("llm") is build_llm.return_value
    assert rr.call_args.kwargs.get("with_influences") is True
    assert rr.call_args.kwargs.get("with_enrichment") is True


def test_recommend_no_influences_flag(tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path, with_anthropic=True)

    with patch("discogs.cli.commands.recommend._build_pipeline_context",
               return_value=(MagicMock(), MagicMock(), load_config())), \
         patch("discogs.cli.commands.recommend._build_llm_client",
               return_value=MagicMock()), \
         patch("discogs.cli.commands.recommend.run_recommend",
               return_value=_empty_run()) as rr, \
         patch("discogs.cli.commands.recommend.render_digest", return_value=""):
        CliRunner().invoke(cli, ["recommend", "--no-influences"])

    assert rr.call_args.kwargs["with_influences"] is False


def test_recommend_no_enrich_flag(tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path, with_anthropic=True)

    with patch("discogs.cli.commands.recommend._build_pipeline_context",
               return_value=(MagicMock(), MagicMock(), load_config())), \
         patch("discogs.cli.commands.recommend._build_llm_client",
               return_value=MagicMock()), \
         patch("discogs.cli.commands.recommend.run_recommend",
               return_value=_empty_run()) as rr, \
         patch("discogs.cli.commands.recommend.render_digest", return_value=""):
        CliRunner().invoke(cli, ["recommend", "--no-enrich"])

    assert rr.call_args.kwargs["with_enrichment"] is False


def test_recommend_warns_and_disables_llm_when_no_api_key(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path, with_anthropic=False)

    with patch("discogs.cli.commands.recommend._build_pipeline_context",
               return_value=(MagicMock(), MagicMock(), load_config())), \
         patch("discogs.cli.commands.recommend.run_recommend",
               return_value=_empty_run()) as rr, \
         patch("discogs.cli.commands.recommend.render_digest", return_value=""):
        result = CliRunner().invoke(cli, ["recommend"])

    assert "anthropic" in result.output.lower() or "llm disabled" in result.output.lower()
    assert rr.call_args.kwargs.get("llm") is None
    assert rr.call_args.kwargs["with_influences"] is False
    assert rr.call_args.kwargs["with_enrichment"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cli_recommend_phase3.py -v`
Expected: FAIL.

- [ ] **Step 3: Update `src/discogs/cli/commands/recommend.py`**

Add the LLMClient import:

```python
from discogs.api.llm import LLMClient
```

Add the helper:

```python
def _build_llm_client(cfg: Config, store: CacheStore) -> LLMClient:
    return LLMClient(cfg, store)
```

Update the click command. Add two flags and update the body:

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
              help="Push picks to your wantlist (NOT YET SUPPORTED — Phase 4).")
def recommend_cmd(
    max_recs: int, budget: int, scope: str,
    no_influences: bool, no_enrich: bool, apply_flag: bool,
) -> None:
    """Generate top-N recommendations and write a markdown digest. Dry-run only."""
    if apply_flag:
        raise click.UsageError("--apply is not yet supported (Phase 4).")

    client, store, cfg = _build_pipeline_context()
    try:
        llm: LLMClient | None = None
        with_influences = not no_influences
        with_enrichment = not no_enrich

        if cfg.anthropic_api_key:
            llm = _build_llm_client(cfg, store)
        else:
            if with_influences or with_enrichment:
                click.echo(
                    "WARNING: no Anthropic API key configured — LLM features disabled.\n"
                    "  Set [anthropic] api_key in ~/.discogs/config.toml or "
                    "ANTHROPIC_API_KEY env var to enable.",
                    err=True,
                )
            with_influences = False
            with_enrichment = False

        result = run_recommend(
            client, store, cfg,
            llm=llm,
            max_recs=max_recs, budget=budget, seed_mode=scope,
            with_influences=with_influences, with_enrichment=with_enrichment,
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_cli_recommend_phase3.py tests/unit/test_cli_recommend.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/cli/commands/recommend.py tests/unit/test_cli_recommend_phase3.py
git commit -m "feat(cli): --no-influences, --no-enrich; auto-disable LLM if no api_key"
```

---

## Task 16: Phase 3 verification + smoke test + README

**Files:**
- Modify: `README.md`

Final pass: full test suite green, ruff clean, mypy clean. Smoke-test against the real account if possible. Update the README to mention the LLM features.

- [ ] **Step 1: Run the full unit test suite**

Run: `pytest tests/unit/ -v`
Expected: all tests pass (Phase 2's 113 + Phase 3's new tests).

- [ ] **Step 2: Run lint**

Run: `ruff check src/ tests/`
Expected: 0 errors. Auto-fix with `ruff check --fix src/ tests/` if needed.

- [ ] **Step 3: Run mypy**

Run: `mypy src/`
Expected: 0 errors.

- [ ] **Step 4: Update README**

In the existing `Config` section, add the `[llm]` block to the example:

```toml
[discogs]
token = "..."
username = "lorenzo"

[anthropic]
api_key = "sk-..."

[llm]
daily_budget = 100
influences_model = "claude-haiku-4-5-20251001"
enrich_model = "claude-haiku-4-5-20251001"

[cache]
path = "~/.discogs/cache.db"   # optional override
```

In the existing Commands table, replace the recommend row with:

```markdown
| `discogs recommend [--max-recs 25] [--budget 800] [--scope ...] [--no-influences] [--no-enrich]` | Generate top-N picks; writes a markdown digest under `~/.discogs/digests/`. With Claude influence expansion + editorial notes when an Anthropic key is configured. Dry-run only in Phase 3. |
```

In the "Recommendations" section added in Phase 2, append a paragraph:

```markdown
With an Anthropic API key configured, two extra stages run by default:

- **Stage 1.5 — Influence expansion**: For your top 20 seed artists, Claude lists 5–10 artists who influenced them. We resolve each name to a Discogs ID via search and add the resolved set to the seed pool with a decayed weight. Cached for 90 days per artist.
- **Stage 4 — Editorial notes**: For the top 50 candidates, Claude writes 2–3 sentence notes explaining why each release matters (with a confidence tag). High-confidence notes get a small score boost; low-confidence ones get a small penalty.

Disable either with `--no-influences` / `--no-enrich`. Total Phase 3 score range: `[0, 1]`.
```

- [ ] **Step 5: Smoke-test the new behaviour (optional but recommended)**

If you have an Anthropic key in `~/.discogs/config.toml`:

```bash
discogs recommend --max-recs 5 --budget 150
less ~/.discogs/digests/$(ls -t ~/.discogs/digests | head -1)
```

You should see editorial notes inline in the digest and a higher seed count than the Phase 2 baseline (because of influence-derived seeds).

If smoke-testing surfaces bugs (Phase 1 and Phase 2 both did at this step), fix them before declaring Phase 3 done.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: README describes Stage 1.5 + Stage 4 LLM features (Phase 3)"
```

---

## Phase 3 verification checklist

After completing all tasks:

- [ ] `pytest tests/unit/` — all tests pass.
- [ ] `ruff check src/ tests/` — 0 errors.
- [ ] `mypy src/` — 0 errors.
- [ ] `discogs recommend --help` — lists `--no-influences` and `--no-enrich`.
- [ ] (Optional) Live smoke test: `discogs recommend --max-recs 5 --budget 150` produces a digest with editorial notes when Anthropic key is configured.

---

## Self-review notes

- **Spec coverage:**
  - Stage 1.5 influence expansion (Spec §"Stage 1.5") → Tasks 7, 8, 11
  - Stage 4 LLM enrichment (Spec §"Stage 4") → Tasks 12, 13
  - Score ceiling extension `[0, 0.85]` → `[0, 1]` (Spec §"Stage 3 — Scoring") → Task 10
  - `--no-influences`, `--no-enrich` flags (Spec §"CLI surface") → Task 15
  - `artist_influences` cache writes (Spec §"Storage") → Tasks 3, 8
  - Daily LLM budget (Spec §"Open questions" — implicit) → Tasks 2, 5, 6
  - Confidence-based score adjustment (Spec §"Stage 4") → Task 12

- **Out of scope, deferred:**
  - `--apply`, `apply <run-id>`, `undo*` commands → Phase 4
  - RYM v2 (replace Claude-derived influences with structured RYM data) → future
  - Per-artist style fetch in influence expansion (we pass `[]` for primary_styles in Task 11) — could be added if false-positive rates are bad

- **Risk highlights:**
  - **Live API drift:** Phase 1 and Phase 2 each surfaced bugs where test mocks didn't match real `python3-discogs-client` shape. Task 4 (search) and Task 5 (LLM) are net-new API integrations — expect smoke-test to find issues. The `_safe_fetch` pattern from Phase 2 `releases.py` is a good template.
  - **Claude JSON-only prompts:** the `_SYSTEM_PROMPT` in `influences.py` and `enrich.py` insists on strict JSON. Real Claude responses occasionally include markdown fences (```json ... ```). Consider adding a `_strip_code_fences()` helper if smoke testing reveals this.
  - **Influence cache TTL of 90 days:** if Claude's understanding of an artist's influences shifts (model upgrade), users won't see the new edges for up to 90 days. `discogs cache reset` (out of scope here) would be the user-visible escape hatch.
