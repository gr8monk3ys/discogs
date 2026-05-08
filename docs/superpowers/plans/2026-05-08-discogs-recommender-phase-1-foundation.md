# Discogs Recommender — Phase 1: Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a working Python CLI that authenticates to Discogs, syncs the user's collection and wantlist into a local SQLite cache, and reports status — with all the foundational machinery (config, models, API client, cache store) the recommendation engine in Phase 2 will build on.

**Architecture:** A `src/discogs` package split into `api/`, `cache/`, `sync/`, `cli/`, plus top-level `config.py` and `models.py`. The CLI is a thin Click shell over the library. SQLite at `~/.discogs/cache.db` is the local store. The API client wraps `python3-discogs-client` for rate-limit handling and pagination, and exposes typed Pydantic models to the rest of the codebase.

**Tech Stack:** Python 3.11+, `python3-discogs-client`, `click`, `rich`, `pydantic`, `tomli` (or stdlib `tomllib`), `pytest`, `vcrpy`, `ruff`, `mypy`.

**Spec reference:** `docs/superpowers/specs/2026-05-08-discogs-recommender-design.md` — this plan implements the "Foundation" build-sequence steps (1, 2, 3 from the spec's Build Sequence) and the parts of the recommendation pipeline they require.

**Out of scope for Phase 1:** the recommendation pipeline (Phase 2), influence expansion + LLM enrichment (Phase 3), wantlist writes + undo (Phase 4).

---

## Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `ruff.toml`
- Create: `mypy.ini`
- Create: `src/discogs/__init__.py`
- Create: `src/discogs/cli/__init__.py`
- Create: `src/discogs/cli/commands/__init__.py`
- Create: `src/discogs/api/__init__.py`
- Create: `src/discogs/cache/__init__.py`
- Create: `src/discogs/sync/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/integration/__init__.py`

- [ ] **Step 1: Create `pyproject.toml`**

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

[project.optional-dependencies]
dev = [
    "pytest>=7.4",
    "pytest-cov>=4.1",
    "vcrpy>=5.1",
    "ruff>=0.4",
    "mypy>=1.8",
]

[project.scripts]
discogs = "discogs.cli.__main__:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra -q"
```

- [ ] **Step 2: Create `.gitignore`**

```
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
.pytest_cache/
.coverage
htmlcov/
.mypy_cache/
.ruff_cache/
.venv/
dist/
build/
~/.discogs/
```

- [ ] **Step 3: Create `ruff.toml`**

```toml
line-length = 100
target-version = "py311"

[lint]
select = ["E", "F", "I", "B", "UP", "SIM", "RET"]
ignore = ["E501"]

[lint.per-file-ignores]
"tests/**" = ["B011"]
```

- [ ] **Step 4: Create `mypy.ini`**

```ini
[mypy]
python_version = 3.11
strict = True
ignore_missing_imports = True
explicit_package_bases = True
mypy_path = src

[mypy-tests.*]
disallow_untyped_defs = False
```

- [ ] **Step 5: Create empty `__init__.py` files for every package directory**

Run: `touch src/discogs/__init__.py src/discogs/cli/__init__.py src/discogs/cli/commands/__init__.py src/discogs/api/__init__.py src/discogs/cache/__init__.py src/discogs/sync/__init__.py tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py`

- [ ] **Step 6: Verify scaffold installs cleanly**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest --version
ruff --version
mypy --version
```

Expected: all three tools print versions; `pip install` exits 0.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore ruff.toml mypy.ini src/ tests/
git commit -m "chore: scaffold discogs package with src layout, ruff, mypy, pytest"
```

---

## Task 2: Config module

**Files:**
- Create: `src/discogs/config.py`
- Test: `tests/unit/test_config.py`

The config module loads `~/.discogs/config.toml`, merges env overrides (`DISCOGS_TOKEN`, `ANTHROPIC_API_KEY`), and exposes a typed `Config` object. Secrets must never appear in `repr()`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_config.py`:

```python
from pathlib import Path
import textwrap

import pytest

from discogs.config import Config, load_config


def test_load_config_reads_token_from_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(textwrap.dedent("""
        [discogs]
        token = "abc123"
        username = "lorenzo"
    """))

    cfg = load_config(config_path)

    assert cfg.discogs_token == "abc123"
    assert cfg.discogs_username == "lorenzo"


def test_env_token_overrides_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[discogs]\ntoken = "from-file"\nusername = "u"')
    monkeypatch.setenv("DISCOGS_TOKEN", "from-env")

    cfg = load_config(config_path)

    assert cfg.discogs_token == "from-env"


def test_repr_redacts_secrets(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[discogs]\ntoken = "supersecret"\nusername = "u"')

    cfg = load_config(config_path)
    rendered = repr(cfg)

    assert "supersecret" not in rendered
    assert "***" in rendered


def test_missing_file_raises_with_helpful_message(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="discogs auth set"):
        load_config(tmp_path / "does-not-exist.toml")


def test_missing_token_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[discogs]\nusername = "u"')

    with pytest.raises(ValueError, match="token"):
        load_config(config_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_config.py -v`
Expected: FAIL with `ImportError` (module doesn't exist yet).

- [ ] **Step 3: Implement `src/discogs/config.py`**

```python
"""Load configuration from ~/.discogs/config.toml with env overrides."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".discogs" / "config.toml"


def _default_cache_path() -> Path:
    return Path.home() / ".discogs" / "cache.db"


@dataclass
class Config:
    discogs_token: str = field(repr=False)
    discogs_username: str
    anthropic_api_key: str | None = field(default=None, repr=False)
    cache_path: Path = field(default_factory=_default_cache_path)
    user_agent: str = "discogs-recommender/0.1.0 (+local)"
    daily_api_budget: int = 1500

    def __repr__(self) -> str:
        return (
            f"Config(discogs_token='***', discogs_username={self.discogs_username!r}, "
            f"anthropic_api_key={'***' if self.anthropic_api_key else None}, "
            f"cache_path={self.cache_path!r}, user_agent={self.user_agent!r}, "
            f"daily_api_budget={self.daily_api_budget})"
        )


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    if not path.exists():
        raise FileNotFoundError(
            f"No config at {path}. Run `discogs auth set` to create one."
        )

    with path.open("rb") as f:
        data = tomllib.load(f)

    discogs = data.get("discogs", {})
    token = os.environ.get("DISCOGS_TOKEN") or discogs.get("token")
    if not token:
        raise ValueError(
            "Missing Discogs token. Set DISCOGS_TOKEN env or `[discogs] token = ...` in config."
        )
    username = discogs.get("username")
    if not username:
        raise ValueError("Missing `[discogs] username` in config.")

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or data.get("anthropic", {}).get("api_key")

    cache_path_str = data.get("cache", {}).get("path")
    cache_path = Path(cache_path_str).expanduser() if cache_path_str else _default_cache_path()

    return Config(
        discogs_token=token,
        discogs_username=username,
        anthropic_api_key=anthropic_key,
        cache_path=cache_path,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_config.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/config.py tests/unit/test_config.py
git commit -m "feat(config): load TOML config with env overrides and secret redaction"
```

---

## Task 3: Domain models

**Files:**
- Create: `src/discogs/models.py`
- Test: `tests/unit/test_models.py`

Pydantic models for Discogs entities. These are the typed boundary between the API client and the cache. Keep them flat and JSON-serializable.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_models.py`:

```python
from datetime import UTC, datetime

import pytest

from discogs.models import (
    Artist,
    CollectionItem,
    Credit,
    Format,
    Label,
    Master,
    Release,
    WantlistItem,
)


def test_release_minimum_fields() -> None:
    r = Release(
        id=123,
        title="Test Album",
        year=1975,
        styles=["Jazz", "Spiritual Jazz"],
        genres=["Jazz"],
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP"])],
        community_have=42,
        community_want=120,
        community_avg_rating=4.3,
        community_rating_count=18,
        fetched_at=datetime.now(UTC),
    )
    assert r.id == 123
    assert r.is_album_or_ep is True


def test_release_format_classification() -> None:
    single = Release(
        id=1, title="x", year=2000, styles=[], genres=[],
        formats=[Format(name="Vinyl", qty=1, descriptions=["7\""])],
        community_have=0, community_want=0,
        community_avg_rating=0.0, community_rating_count=0,
        fetched_at=datetime.now(UTC),
    )
    assert single.is_album_or_ep is False

    compilation = Release(
        id=2, title="x", year=2000, styles=[], genres=[],
        formats=[Format(name="CD", qty=1, descriptions=["Compilation"])],
        community_have=0, community_want=0,
        community_avg_rating=0.0, community_rating_count=0,
        fetched_at=datetime.now(UTC),
    )
    assert compilation.is_album_or_ep is False
    assert compilation.is_compilation is True


def test_credit_role_normalization() -> None:
    c = Credit(release_id=1, artist_id=2, role="Producer [Tracks A1, A2]")
    assert c.normalized_role == "Producer"


def test_collection_item_round_trip() -> None:
    item = CollectionItem(
        release_id=42, folder_id=0, instance_id=999,
        date_added=datetime.now(UTC),
    )
    assert item.release_id == 42


def test_artist_label_master_wantlist_construct() -> None:
    Artist(id=1, name="Pharoah Sanders", profile=None, fetched_at=datetime.now(UTC))
    Label(id=1, name="Impulse!", parent_label=None, releases_count=200, fetched_at=datetime.now(UTC))
    Master(id=1, title="Karma", year=1969, main_release_id=10, fetched_at=datetime.now(UTC))
    WantlistItem(release_id=1, date_added=datetime.now(UTC), notes=None)


def test_release_rejects_negative_year() -> None:
    with pytest.raises(ValueError):
        Release(
            id=1, title="x", year=-1, styles=[], genres=[],
            formats=[],
            community_have=0, community_want=0,
            community_avg_rating=0.0, community_rating_count=0,
            fetched_at=datetime.now(UTC),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_models.py -v`
Expected: FAIL with import errors.

- [ ] **Step 3: Implement `src/discogs/models.py`**

```python
"""Typed domain models for Discogs entities."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class Format(BaseModel):
    name: str
    qty: int = 1
    descriptions: list[str] = Field(default_factory=list)


class Release(BaseModel):
    id: int
    master_id: int | None = None
    title: str
    year: int
    country: str | None = None
    formats: list[Format] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    community_have: int
    community_want: int
    community_avg_rating: float
    community_rating_count: int
    fetched_at: datetime

    @field_validator("year")
    @classmethod
    def _year_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"year must be non-negative, got {v}")
        return v

    @property
    def is_album_or_ep(self) -> bool:
        for fmt in self.formats:
            descs = {d.lower() for d in fmt.descriptions}
            name = fmt.name.lower()
            if {"lp", "album", "ep"} & descs:
                return True
            if name in {"album"}:
                return True
            if descs & {"single", "compilation", "dj-mix", "dj mix"}:
                continue
        # Heuristic: a release with no obvious singles/comp markers and ≥1 format is treated as album.
        if self.formats and not self.is_compilation and not self._is_single():
            return True
        return False

    @property
    def is_compilation(self) -> bool:
        return any("compilation" in d.lower() for f in self.formats for d in f.descriptions)

    def _is_single(self) -> bool:
        for fmt in self.formats:
            descs = {d.lower() for d in fmt.descriptions}
            if {"single", "7\"", '7"'} & descs:
                return True
        return False


class Master(BaseModel):
    id: int
    title: str
    year: int
    main_release_id: int | None
    fetched_at: datetime


class Artist(BaseModel):
    id: int
    name: str
    profile: str | None
    fetched_at: datetime


class Label(BaseModel):
    id: int
    name: str
    parent_label: str | None
    releases_count: int
    fetched_at: datetime


class Credit(BaseModel):
    release_id: int
    artist_id: int
    role: str

    @property
    def normalized_role(self) -> str:
        # Discogs roles often have qualifiers in brackets: "Producer [Tracks A1]" -> "Producer"
        return self.role.split("[", 1)[0].strip()


class CollectionItem(BaseModel):
    release_id: int
    folder_id: int
    instance_id: int
    date_added: datetime


class WantlistItem(BaseModel):
    release_id: int
    date_added: datetime
    notes: str | None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_models.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/models.py tests/unit/test_models.py
git commit -m "feat(models): pydantic types for releases, artists, credits, collection, wantlist"
```

---

## Task 4: Cache schema and initialization

**Files:**
- Create: `src/discogs/cache/schema.sql`
- Create: `src/discogs/cache/store.py` (skeleton — only `init_db` for this task)
- Test: `tests/unit/test_cache_init.py`

Sets up the SQLite schema. The store grows in subsequent tasks; this task only opens the connection, runs the schema, and confirms the tables exist.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cache_init.py`:

```python
import sqlite3
from pathlib import Path

from discogs.cache.store import CacheStore, init_db


def test_init_db_creates_all_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = {r[0] for r in rows}

    expected = {
        "releases", "masters", "artists", "labels",
        "release_credits", "release_labels",
        "release_styles", "release_genres",
        "collection_items", "wantlist_items",
        "artist_influences", "artist_top_releases",
        "recommendation_history", "runs",
        "schema_version",
    }
    assert expected.issubset(table_names)


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    init_db(db_path)
    init_db(db_path)  # second call must not raise


def test_cache_store_opens_initialized_db(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    init_db(db_path)
    store = CacheStore(db_path)
    assert store.schema_version == 1
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cache_init.py -v`
Expected: FAIL with import errors.

- [ ] **Step 3: Write `src/discogs/cache/schema.sql`**

```sql
-- Schema version 1
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY,
    master_id INTEGER,
    title TEXT NOT NULL,
    year INTEGER NOT NULL,
    country TEXT,
    formats_json TEXT NOT NULL,
    community_have INTEGER NOT NULL,
    community_want INTEGER NOT NULL,
    community_avg_rating REAL NOT NULL,
    community_rating_count INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_releases_master ON releases(master_id);
CREATE INDEX IF NOT EXISTS idx_releases_have ON releases(community_have);

CREATE TABLE IF NOT EXISTS masters (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    year INTEGER NOT NULL,
    main_release_id INTEGER,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    profile TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS labels (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    parent_label TEXT,
    releases_count INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS release_credits (
    release_id INTEGER NOT NULL,
    artist_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY (release_id, artist_id, role)
);
CREATE INDEX IF NOT EXISTS idx_credits_artist ON release_credits(artist_id);

CREATE TABLE IF NOT EXISTS release_labels (
    release_id INTEGER NOT NULL,
    label_id INTEGER NOT NULL,
    catalog_number TEXT,
    PRIMARY KEY (release_id, label_id, catalog_number)
);

CREATE TABLE IF NOT EXISTS release_styles (
    release_id INTEGER NOT NULL,
    style TEXT NOT NULL,
    PRIMARY KEY (release_id, style)
);

CREATE TABLE IF NOT EXISTS release_genres (
    release_id INTEGER NOT NULL,
    genre TEXT NOT NULL,
    PRIMARY KEY (release_id, genre)
);

CREATE TABLE IF NOT EXISTS collection_items (
    release_id INTEGER NOT NULL,
    folder_id INTEGER NOT NULL,
    instance_id INTEGER NOT NULL,
    date_added TEXT NOT NULL,
    PRIMARY KEY (instance_id)
);
CREATE INDEX IF NOT EXISTS idx_collection_release ON collection_items(release_id);

CREATE TABLE IF NOT EXISTS wantlist_items (
    release_id INTEGER PRIMARY KEY,
    date_added TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS artist_influences (
    source_artist_id INTEGER NOT NULL,
    influence_artist_id INTEGER NOT NULL,
    confidence TEXT NOT NULL CHECK(confidence IN ('high','medium','low')),
    source TEXT NOT NULL DEFAULT 'claude',
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source_artist_id, influence_artist_id, source)
);

CREATE TABLE IF NOT EXISTS artist_top_releases (
    artist_id INTEGER NOT NULL,
    release_id INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (artist_id, release_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    display_id TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    args_json TEXT,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS recommendation_history (
    release_id INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    score REAL NOT NULL,
    applied_to_wantlist INTEGER NOT NULL DEFAULT 0,
    applied_at TEXT,
    removed_at TEXT,
    removed_reason TEXT,
    PRIMARY KEY (release_id, run_id),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS idx_history_run ON recommendation_history(run_id);
```

- [ ] **Step 4: Implement `src/discogs/cache/store.py`** (skeleton only — full CRUD added in later tasks)

```python
"""SQLite-backed cache for Discogs data and recommendation history."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_FILE = Path(__file__).parent / "schema.sql"
CURRENT_SCHEMA_VERSION = 1


def init_db(path: Path) -> None:
    """Create the cache database and apply the schema. Idempotent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = SCHEMA_FILE.read_text()
    with sqlite3.connect(path) as conn:
        conn.executescript(schema_sql)
        conn.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
            (CURRENT_SCHEMA_VERSION, datetime.now(UTC).isoformat()),
        )
        conn.commit()


class CacheStore:
    """Read/write API over the SQLite cache."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    @property
    def schema_version(self) -> int:
        row = self.conn.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()
        return int(row["v"]) if row and row["v"] is not None else 0

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "CacheStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_cache_init.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/discogs/cache/ tests/unit/test_cache_init.py
git commit -m "feat(cache): SQLite schema and idempotent init for all v1 tables"
```

---

## Task 5: Cache store — releases CRUD

**Files:**
- Modify: `src/discogs/cache/store.py`
- Test: `tests/unit/test_cache_releases.py`

Adds `upsert_release`, `get_release`, and `release_age` methods. Releases are denormalized: their formats live as JSON, but styles, genres, credits, and labels go into junction tables (set in subsequent tasks; in this task we just persist styles/genres alongside the release).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cache_releases.py`:

```python
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import Format, Release


@pytest.fixture
def store(tmp_path: Path) -> CacheStore:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def make_release(rid: int = 1, *, fetched_at: datetime | None = None) -> Release:
    return Release(
        id=rid,
        title="Karma",
        year=1969,
        country="US",
        formats=[Format(name="Vinyl", qty=1, descriptions=["LP", "Album"])],
        styles=["Spiritual Jazz", "Free Jazz"],
        genres=["Jazz"],
        community_have=2500,
        community_want=8000,
        community_avg_rating=4.6,
        community_rating_count=320,
        fetched_at=fetched_at or datetime.now(UTC),
    )


def test_upsert_and_get_release(store: CacheStore) -> None:
    r = make_release()
    store.upsert_release(r)

    fetched = store.get_release(r.id)
    assert fetched is not None
    assert fetched.title == "Karma"
    assert set(fetched.styles) == {"Spiritual Jazz", "Free Jazz"}
    assert set(fetched.genres) == {"Jazz"}


def test_upsert_replaces_existing(store: CacheStore) -> None:
    store.upsert_release(make_release(rid=1))
    updated = make_release(rid=1)
    updated_dict = updated.model_dump()
    updated_dict["community_have"] = 9999
    store.upsert_release(Release(**updated_dict))

    fetched = store.get_release(1)
    assert fetched is not None
    assert fetched.community_have == 9999


def test_get_release_returns_none_when_missing(store: CacheStore) -> None:
    assert store.get_release(99999) is None


def test_release_age_returns_seconds(store: CacheStore) -> None:
    fetched_at = datetime.now(UTC) - timedelta(seconds=42)
    store.upsert_release(make_release(fetched_at=fetched_at))
    age = store.release_age(1)
    assert age is not None
    assert 40 <= age.total_seconds() <= 60


def test_release_age_returns_none_when_missing(store: CacheStore) -> None:
    assert store.release_age(99999) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cache_releases.py -v`
Expected: FAIL — methods not implemented.

- [ ] **Step 3: Add release CRUD to `src/discogs/cache/store.py`**

Update the imports at the top of `src/discogs/cache/store.py` to include `json`, `timedelta`, and `TYPE_CHECKING`:

```python
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from discogs.models import Release
```

Then add these methods to the `CacheStore` class:

```python
    def upsert_release(self, release: "Release") -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO releases (
                    id, master_id, title, year, country, formats_json,
                    community_have, community_want, community_avg_rating,
                    community_rating_count, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    master_id=excluded.master_id,
                    title=excluded.title,
                    year=excluded.year,
                    country=excluded.country,
                    formats_json=excluded.formats_json,
                    community_have=excluded.community_have,
                    community_want=excluded.community_want,
                    community_avg_rating=excluded.community_avg_rating,
                    community_rating_count=excluded.community_rating_count,
                    fetched_at=excluded.fetched_at
                """,
                (
                    release.id, release.master_id, release.title, release.year,
                    release.country, json.dumps([f.model_dump() for f in release.formats]),
                    release.community_have, release.community_want,
                    release.community_avg_rating, release.community_rating_count,
                    release.fetched_at.isoformat(),
                ),
            )
            self.conn.execute("DELETE FROM release_styles WHERE release_id = ?", (release.id,))
            self.conn.executemany(
                "INSERT INTO release_styles (release_id, style) VALUES (?, ?)",
                [(release.id, s) for s in release.styles],
            )
            self.conn.execute("DELETE FROM release_genres WHERE release_id = ?", (release.id,))
            self.conn.executemany(
                "INSERT INTO release_genres (release_id, genre) VALUES (?, ?)",
                [(release.id, g) for g in release.genres],
            )

    def get_release(self, release_id: int) -> "Release | None":
        from discogs.models import Format, Release
        row = self.conn.execute(
            "SELECT * FROM releases WHERE id = ?", (release_id,)
        ).fetchone()
        if row is None:
            return None
        styles = [
            r["style"] for r in self.conn.execute(
                "SELECT style FROM release_styles WHERE release_id = ?", (release_id,)
            )
        ]
        genres = [
            r["genre"] for r in self.conn.execute(
                "SELECT genre FROM release_genres WHERE release_id = ?", (release_id,)
            )
        ]
        formats = [Format(**f) for f in json.loads(row["formats_json"])]
        return Release(
            id=row["id"],
            master_id=row["master_id"],
            title=row["title"],
            year=row["year"],
            country=row["country"],
            formats=formats,
            styles=styles,
            genres=genres,
            community_have=row["community_have"],
            community_want=row["community_want"],
            community_avg_rating=row["community_avg_rating"],
            community_rating_count=row["community_rating_count"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
        )

    def release_age(self, release_id: int) -> timedelta | None:
        row = self.conn.execute(
            "SELECT fetched_at FROM releases WHERE id = ?", (release_id,)
        ).fetchone()
        if row is None:
            return None
        return datetime.now(UTC) - datetime.fromisoformat(row["fetched_at"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_cache_releases.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/cache/store.py tests/unit/test_cache_releases.py
git commit -m "feat(cache): release upsert/get with styles, genres, formats persisted"
```

---

## Task 6: Cache store — collection and wantlist

**Files:**
- Modify: `src/discogs/cache/store.py`
- Test: `tests/unit/test_cache_collection.py`

Adds methods to bulk-replace the user's collection and wantlist after a sync.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cache_collection.py`:

```python
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db
from discogs.models import CollectionItem, WantlistItem


@pytest.fixture
def store(tmp_path: Path) -> CacheStore:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def test_replace_collection_inserts_all(store: CacheStore) -> None:
    items = [
        CollectionItem(release_id=1, folder_id=0, instance_id=10, date_added=datetime.now(UTC)),
        CollectionItem(release_id=2, folder_id=0, instance_id=20, date_added=datetime.now(UTC)),
    ]
    store.replace_collection(items)

    fetched = list(store.iter_collection())
    assert {i.release_id for i in fetched} == {1, 2}


def test_replace_collection_overwrites_previous(store: CacheStore) -> None:
    store.replace_collection([
        CollectionItem(release_id=1, folder_id=0, instance_id=10, date_added=datetime.now(UTC)),
    ])
    store.replace_collection([
        CollectionItem(release_id=2, folder_id=0, instance_id=20, date_added=datetime.now(UTC)),
    ])
    fetched = list(store.iter_collection())
    assert [i.release_id for i in fetched] == [2]


def test_replace_wantlist(store: CacheStore) -> None:
    items = [
        WantlistItem(release_id=42, date_added=datetime.now(UTC), notes="signed"),
    ]
    store.replace_wantlist(items)
    fetched = list(store.iter_wantlist())
    assert fetched[0].release_id == 42
    assert fetched[0].notes == "signed"


def test_collection_release_ids_excludes_wantlist(store: CacheStore) -> None:
    store.replace_collection([
        CollectionItem(release_id=1, folder_id=0, instance_id=10, date_added=datetime.now(UTC)),
    ])
    store.replace_wantlist([
        WantlistItem(release_id=2, date_added=datetime.now(UTC), notes=None),
    ])
    assert store.collection_release_ids() == {1}
    assert store.wantlist_release_ids() == {2}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cache_collection.py -v`
Expected: FAIL — methods not implemented.

- [ ] **Step 3: Add to `src/discogs/cache/store.py`**

Add `from collections.abc import Iterable, Iterator` to the imports at the top of the file. Extend the `TYPE_CHECKING` block to include `CollectionItem` and `WantlistItem`:

```python
if TYPE_CHECKING:
    from discogs.models import CollectionItem, Release, WantlistItem
```

Then add these methods to the `CacheStore` class:

```python
    def replace_collection(self, items: Iterable["CollectionItem"]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM collection_items")
            self.conn.executemany(
                "INSERT INTO collection_items (release_id, folder_id, instance_id, date_added) "
                "VALUES (?, ?, ?, ?)",
                [
                    (i.release_id, i.folder_id, i.instance_id, i.date_added.isoformat())
                    for i in items
                ],
            )

    def iter_collection(self) -> Iterator["CollectionItem"]:
        from discogs.models import CollectionItem
        rows = self.conn.execute(
            "SELECT * FROM collection_items ORDER BY date_added DESC"
        )
        for row in rows:
            yield CollectionItem(
                release_id=row["release_id"],
                folder_id=row["folder_id"],
                instance_id=row["instance_id"],
                date_added=datetime.fromisoformat(row["date_added"]),
            )

    def collection_release_ids(self) -> set[int]:
        return {
            int(r["release_id"])
            for r in self.conn.execute("SELECT release_id FROM collection_items")
        }

    def replace_wantlist(self, items: Iterable["WantlistItem"]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM wantlist_items")
            self.conn.executemany(
                "INSERT INTO wantlist_items (release_id, date_added, notes) VALUES (?, ?, ?)",
                [(i.release_id, i.date_added.isoformat(), i.notes) for i in items],
            )

    def iter_wantlist(self) -> Iterator["WantlistItem"]:
        from discogs.models import WantlistItem
        rows = self.conn.execute(
            "SELECT * FROM wantlist_items ORDER BY date_added DESC"
        )
        for row in rows:
            yield WantlistItem(
                release_id=row["release_id"],
                date_added=datetime.fromisoformat(row["date_added"]),
                notes=row["notes"],
            )

    def wantlist_release_ids(self) -> set[int]:
        return {
            int(r["release_id"])
            for r in self.conn.execute("SELECT release_id FROM wantlist_items")
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_cache_collection.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/cache/store.py tests/unit/test_cache_collection.py
git commit -m "feat(cache): replace_collection / replace_wantlist + iterators"
```

---

## Task 7: Cache store — sync metadata

**Files:**
- Modify: `src/discogs/cache/store.py`
- Test: `tests/unit/test_cache_sync_metadata.py`

Tracks the timestamp of the last successful collection/wantlist sync and an API call counter for the daily budget. We need both for the `sync` TTL check and `discogs status`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cache_sync_metadata.py`:

```python
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from discogs.cache.store import CacheStore, init_db


@pytest.fixture
def store(tmp_path: Path) -> CacheStore:
    db = tmp_path / "cache.db"
    init_db(db)
    s = CacheStore(db)
    yield s
    s.close()


def test_record_and_read_last_sync(store: CacheStore) -> None:
    assert store.last_sync_at("collection") is None
    now = datetime.now(UTC)
    store.record_sync("collection", now)
    fetched = store.last_sync_at("collection")
    assert fetched is not None
    assert abs((fetched - now).total_seconds()) < 1


def test_increment_daily_calls(store: CacheStore) -> None:
    today = datetime.now(UTC).date()
    assert store.api_calls_today() == 0
    store.increment_api_calls(3)
    store.increment_api_calls(2)
    assert store.api_calls_today() == 5

    # Yesterday's count should not interfere
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    store.conn.execute(
        "INSERT OR REPLACE INTO _api_call_counts(day, count) VALUES (?, ?)",
        (yesterday.isoformat(), 999),
    )
    store.conn.commit()
    assert store.api_calls_today() == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cache_sync_metadata.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the supporting tables and methods**

First, append these tables to `src/discogs/cache/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS _sync_metadata (
    scope TEXT PRIMARY KEY,
    last_sync_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS _api_call_counts (
    day TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0
);
```

Then add to `src/discogs/cache/store.py`:

```python
    def record_sync(self, scope: str, when: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO _sync_metadata(scope, last_sync_at) VALUES (?, ?)",
                (scope, when.isoformat()),
            )

    def last_sync_at(self, scope: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT last_sync_at FROM _sync_metadata WHERE scope = ?", (scope,)
        ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row["last_sync_at"])

    def increment_api_calls(self, n: int = 1) -> None:
        today = datetime.now(UTC).date().isoformat()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO _api_call_counts(day, count) VALUES (?, ?)
                ON CONFLICT(day) DO UPDATE SET count = count + excluded.count
                """,
                (today, n),
            )

    def api_calls_today(self) -> int:
        today = datetime.now(UTC).date().isoformat()
        row = self.conn.execute(
            "SELECT count FROM _api_call_counts WHERE day = ?", (today,)
        ).fetchone()
        return int(row["count"]) if row else 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_cache_sync_metadata.py -v`
Expected: 2 passed.

Also re-run `tests/unit/test_cache_init.py` to ensure the schema additions didn't break it.

Run: `pytest tests/unit/ -v`
Expected: all tests still passing.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/cache/schema.sql src/discogs/cache/store.py tests/unit/test_cache_sync_metadata.py
git commit -m "feat(cache): track last sync time and daily API call count"
```

---

## Task 8: API client wrapper — daily budget guard

**Files:**
- Create: `src/discogs/api/client.py`
- Test: `tests/unit/test_api_client.py`

Wraps `python3-discogs-client` to inject our User-Agent, increment the daily-call counter for every request, and refuse to dispatch when the budget is exceeded. Rate-limit pacing (the wait-on-low-remaining behavior) is delegated to the upstream client, which already parses `X-Discogs-Ratelimit-Remaining`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_api_client.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import BudgetExceeded, DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db",
        user_agent="discogs-test/0.0",
        daily_api_budget=3,
    )


@pytest.fixture
def store(cfg: Config) -> CacheStore:
    init_db(cfg.cache_path)
    s = CacheStore(cfg.cache_path)
    yield s
    s.close()


def test_client_uses_configured_user_agent(cfg: Config, store: CacheStore) -> None:
    upstream_factory = MagicMock()
    upstream_factory.return_value = MagicMock()
    DiscogsClient(cfg, store, upstream_factory=upstream_factory)
    upstream_factory.assert_called_once_with(cfg.user_agent, user_token=cfg.discogs_token)


def test_call_increments_budget(cfg: Config, store: CacheStore) -> None:
    upstream = MagicMock()
    upstream.identity.return_value = MagicMock(username="u")
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: upstream)

    assert store.api_calls_today() == 0
    client.call("identity")
    assert store.api_calls_today() == 1
    upstream.identity.assert_called_once()


def test_budget_exceeded_raises(cfg: Config, store: CacheStore) -> None:
    upstream = MagicMock()
    upstream.identity.return_value = None
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: upstream)
    client.call("identity")
    client.call("identity")
    client.call("identity")

    with pytest.raises(BudgetExceeded):
        client.call("identity")


def test_call_with_args_forwards(cfg: Config, store: CacheStore) -> None:
    upstream = MagicMock()
    upstream.release.return_value = MagicMock(id=42)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: upstream)

    client.call("release", 42)
    upstream.release.assert_called_once_with(42)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_api_client.py -v`
Expected: FAIL — module not implemented.

- [ ] **Step 3: Implement `src/discogs/api/client.py`**

```python
"""Wrapper around python3-discogs-client with budget tracking and User-Agent injection."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import discogs_client

from discogs.cache.store import CacheStore
from discogs.config import Config


class BudgetExceeded(RuntimeError):
    """Raised when the daily API call budget is exhausted."""


class DiscogsClient:
    def __init__(
        self,
        config: Config,
        store: CacheStore,
        *,
        upstream_factory: Callable[..., Any] = discogs_client.Client,
    ) -> None:
        self._config = config
        self._store = store
        self._upstream = upstream_factory(config.user_agent, user_token=config.discogs_token)

    @property
    def upstream(self) -> Any:
        """Direct access to the underlying client. Use sparingly — prefer `call`."""
        return self._upstream

    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Dispatch a call, incrementing the daily counter and enforcing the budget."""
        if self._store.api_calls_today() >= self._config.daily_api_budget:
            raise BudgetExceeded(
                f"Daily Discogs API budget of {self._config.daily_api_budget} exceeded. "
                "Wait until tomorrow or raise daily_api_budget in config."
            )
        try:
            attr = getattr(self._upstream, method)
        except AttributeError as e:
            raise AttributeError(f"DiscogsClient: no such method '{method}'") from e
        result = attr(*args, **kwargs) if callable(attr) else attr
        self._store.increment_api_calls(1)
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_api_client.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/api/client.py tests/unit/test_api_client.py
git commit -m "feat(api): DiscogsClient wrapper with daily budget tracking"
```

---

## Task 9: API — collection fetcher

**Files:**
- Create: `src/discogs/api/collection.py`
- Test: `tests/unit/test_api_collection.py`

Pages through `client.identity().collection_folders[0].releases` and yields `CollectionItem` objects. Each page counts as one API call (we let `python3-discogs-client` paginate; we only count the dispatched calls).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_api_collection.py`:

```python
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import DiscogsClient
from discogs.api.collection import fetch_collection
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config


@pytest.fixture
def client(tmp_path: Path) -> DiscogsClient:
    cfg = Config(
        discogs_token="t", discogs_username="u",
        cache_path=tmp_path / "cache.db",
        daily_api_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    return DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())


def _fake_collection_item(rid: int, instance: int) -> MagicMock:
    item = MagicMock()
    item.release.id = rid
    item.id = instance
    item.folder_id = 0
    item.date_added = datetime.now(UTC).isoformat()
    return item


def test_fetch_collection_yields_all_pages(client: DiscogsClient) -> None:
    folder = MagicMock()
    folder.releases.count = 3
    folder.releases.__iter__.return_value = iter([
        _fake_collection_item(1, 10),
        _fake_collection_item(2, 20),
        _fake_collection_item(3, 30),
    ])
    identity = MagicMock()
    identity.collection_folders = [folder]
    client.upstream.identity.return_value = identity

    items = list(fetch_collection(client))

    assert {i.release_id for i in items} == {1, 2, 3}
    assert {i.instance_id for i in items} == {10, 20, 30}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_api_collection.py -v`
Expected: FAIL — module not implemented.

- [ ] **Step 3: Implement `src/discogs/api/collection.py`**

```python
"""Fetch the authenticated user's full Discogs collection."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from discogs.api.client import DiscogsClient
from discogs.models import CollectionItem


def fetch_collection(client: DiscogsClient) -> Iterator[CollectionItem]:
    """Yield every CollectionItem in folder 0 ('All') of the authenticated user."""
    identity = client.call("identity")
    folder_zero = identity.collection_folders[0]
    for raw in folder_zero.releases:
        yield CollectionItem(
            release_id=int(raw.release.id),
            folder_id=int(raw.folder_id),
            instance_id=int(raw.id),
            date_added=_parse_dt(raw.date_added),
        )


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    # Discogs returns ISO strings without tz sometimes; assume UTC.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_api_collection.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/api/collection.py tests/unit/test_api_collection.py
git commit -m "feat(api): fetch_collection iterator over user's full collection"
```

---

## Task 10: API — wantlist fetcher

**Files:**
- Create: `src/discogs/api/wantlist.py`
- Test: `tests/unit/test_api_wantlist.py`

Same shape as collection but for the wantlist. Uses `client.upstream.user(username).wantlist`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_api_wantlist.py`:

```python
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import DiscogsClient
from discogs.api.wantlist import fetch_wantlist
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config


@pytest.fixture
def client(tmp_path: Path) -> DiscogsClient:
    cfg = Config(
        discogs_token="t", discogs_username="lorenzo",
        cache_path=tmp_path / "cache.db",
        daily_api_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    return DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())


def _fake_want(rid: int, notes: str | None = None) -> MagicMock:
    w = MagicMock()
    w.release.id = rid
    w.date_added = datetime.now(UTC).isoformat()
    w.notes = notes
    return w


def test_fetch_wantlist_yields_all(client: DiscogsClient) -> None:
    user = MagicMock()
    user.wantlist = iter([_fake_want(1), _fake_want(2, notes="signed copy")])
    client.upstream.user.return_value = user

    items = list(fetch_wantlist(client, "lorenzo"))

    assert [i.release_id for i in items] == [1, 2]
    assert items[1].notes == "signed copy"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_api_wantlist.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `src/discogs/api/wantlist.py`**

```python
"""Fetch the authenticated user's wantlist."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from discogs.api.client import DiscogsClient
from discogs.models import WantlistItem


def fetch_wantlist(client: DiscogsClient, username: str) -> Iterator[WantlistItem]:
    user = client.call("user", username)
    for raw in user.wantlist:
        yield WantlistItem(
            release_id=int(raw.release.id),
            date_added=_parse_dt(raw.date_added),
            notes=getattr(raw, "notes", None),
        )


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_api_wantlist.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/api/wantlist.py tests/unit/test_api_wantlist.py
git commit -m "feat(api): fetch_wantlist iterator"
```

---

## Task 11: Syncer — orchestration

**Files:**
- Create: `src/discogs/sync/syncer.py`
- Test: `tests/unit/test_syncer.py`

Glues fetchers + cache writes + sync metadata together. Honors a 24h TTL unless `force=True`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_syncer.py`:

```python
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.models import CollectionItem, WantlistItem
from discogs.sync.syncer import Syncer, SyncResult


@pytest.fixture
def setup(tmp_path: Path):
    cfg = Config(
        discogs_token="t", discogs_username="lorenzo",
        cache_path=tmp_path / "cache.db", daily_api_budget=100,
    )
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store, upstream_factory=lambda *a, **kw: MagicMock())
    yield cfg, store, client
    store.close()


def test_sync_collection_writes_to_cache(setup, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, store, client = setup
    items = [
        CollectionItem(release_id=1, folder_id=0, instance_id=10, date_added=datetime.now(UTC)),
        CollectionItem(release_id=2, folder_id=0, instance_id=20, date_added=datetime.now(UTC)),
    ]
    monkeypatch.setattr("discogs.sync.syncer.fetch_collection", lambda _c: iter(items))
    monkeypatch.setattr("discogs.sync.syncer.fetch_wantlist", lambda _c, _u: iter([]))

    syncer = Syncer(cfg, store, client)
    result = syncer.sync(scope="collection")

    assert result.collection_synced == 2
    assert result.wantlist_synced is None
    assert store.collection_release_ids() == {1, 2}
    assert store.last_sync_at("collection") is not None


def test_sync_skips_when_within_ttl(setup, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, store, client = setup
    store.record_sync("collection", datetime.now(UTC) - timedelta(hours=1))
    called = {"n": 0}

    def fake_fetch(_c):
        called["n"] += 1
        return iter([])

    monkeypatch.setattr("discogs.sync.syncer.fetch_collection", fake_fetch)
    monkeypatch.setattr("discogs.sync.syncer.fetch_wantlist", lambda _c, _u: iter([]))

    syncer = Syncer(cfg, store, client)
    result = syncer.sync(scope="collection")

    assert result.collection_synced is None  # skipped
    assert called["n"] == 0


def test_sync_force_bypasses_ttl(setup, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, store, client = setup
    store.record_sync("collection", datetime.now(UTC) - timedelta(hours=1))
    monkeypatch.setattr("discogs.sync.syncer.fetch_collection", lambda _c: iter([]))
    monkeypatch.setattr("discogs.sync.syncer.fetch_wantlist", lambda _c, _u: iter([]))

    syncer = Syncer(cfg, store, client)
    result = syncer.sync(scope="collection", force=True)

    assert result.collection_synced == 0


def test_sync_both_scope(setup, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, store, client = setup
    monkeypatch.setattr("discogs.sync.syncer.fetch_collection", lambda _c: iter([
        CollectionItem(release_id=1, folder_id=0, instance_id=10, date_added=datetime.now(UTC)),
    ]))
    monkeypatch.setattr("discogs.sync.syncer.fetch_wantlist", lambda _c, _u: iter([
        WantlistItem(release_id=99, date_added=datetime.now(UTC), notes=None),
    ]))

    syncer = Syncer(cfg, store, client)
    result = syncer.sync(scope="both")

    assert result.collection_synced == 1
    assert result.wantlist_synced == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_syncer.py -v`
Expected: FAIL — module not implemented.

- [ ] **Step 3: Implement `src/discogs/sync/syncer.py`**

```python
"""Orchestrate collection + wantlist sync into the local cache."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from discogs.api.client import DiscogsClient
from discogs.api.collection import fetch_collection
from discogs.api.wantlist import fetch_wantlist
from discogs.cache.store import CacheStore
from discogs.config import Config

Scope = Literal["collection", "wantlist", "both"]
DEFAULT_TTL = timedelta(hours=24)


@dataclass
class SyncResult:
    collection_synced: int | None  # None means skipped (within TTL)
    wantlist_synced: int | None


class Syncer:
    def __init__(
        self, config: Config, store: CacheStore, client: DiscogsClient,
        *, ttl: timedelta = DEFAULT_TTL,
    ) -> None:
        self._config = config
        self._store = store
        self._client = client
        self._ttl = ttl

    def sync(self, *, scope: Scope = "both", force: bool = False) -> SyncResult:
        coll = self._sync_collection(force) if scope in ("collection", "both") else None
        want = self._sync_wantlist(force) if scope in ("wantlist", "both") else None
        return SyncResult(collection_synced=coll, wantlist_synced=want)

    def _is_fresh(self, scope: str) -> bool:
        last = self._store.last_sync_at(scope)
        if last is None:
            return False
        return datetime.now(UTC) - last < self._ttl

    def _sync_collection(self, force: bool) -> int | None:
        if not force and self._is_fresh("collection"):
            return None
        items = list(fetch_collection(self._client))
        self._store.replace_collection(items)
        self._store.record_sync("collection", datetime.now(UTC))
        return len(items)

    def _sync_wantlist(self, force: bool) -> int | None:
        if not force and self._is_fresh("wantlist"):
            return None
        items = list(fetch_wantlist(self._client, self._config.discogs_username))
        self._store.replace_wantlist(items)
        self._store.record_sync("wantlist", datetime.now(UTC))
        return len(items)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_syncer.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/discogs/sync/syncer.py tests/unit/test_syncer.py
git commit -m "feat(sync): Syncer orchestrates collection + wantlist with 24h TTL"
```

---

## Task 12: CLI — root group and `auth set`

**Files:**
- Create: `src/discogs/cli/__main__.py`
- Create: `src/discogs/cli/commands/auth.py`
- Test: `tests/unit/test_cli_auth.py`

The CLI root group, plus the `auth set` subcommand. `auth set` writes `~/.discogs/config.toml` with `chmod 600` (or its TOML location override) using `getpass` so the token never echoes.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cli_auth.py`:

```python
from pathlib import Path

from click.testing import CliRunner

from discogs.cli.__main__ import cli


def test_auth_set_writes_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(
        cli, ["auth", "set"],
        input="my-secret-token\nlorenzo\n",
    )
    assert result.exit_code == 0, result.output

    config_path = tmp_path / ".discogs" / "config.toml"
    assert config_path.exists()
    contents = config_path.read_text()
    assert "my-secret-token" in contents
    assert 'username = "lorenzo"' in contents

    # File must be readable only by owner
    mode = config_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_auth_set_does_not_echo_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(
        cli, ["auth", "set"],
        input="hunter2\nlorenzo\n",
    )
    # The output stream should not contain the token
    assert "hunter2" not in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cli_auth.py -v`
Expected: FAIL — modules not implemented.

- [ ] **Step 3: Implement `src/discogs/cli/__main__.py`**

```python
"""`discogs` CLI root."""
from __future__ import annotations

import click

from discogs.cli.commands.auth import auth_group


@click.group()
@click.version_option(package_name="discogs")
def cli() -> None:
    """Discogs collection sync and recommendation framework."""


cli.add_command(auth_group, name="auth")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Implement `src/discogs/cli/commands/auth.py`**

```python
"""`discogs auth ...` subcommands."""
from __future__ import annotations

from pathlib import Path

import click


@click.group()
def auth_group() -> None:
    """Manage Discogs authentication."""


@auth_group.command("set")
def set_token() -> None:
    """Store a Discogs personal access token in ~/.discogs/config.toml."""
    token = click.prompt("Discogs personal access token", hide_input=True)
    username = click.prompt("Discogs username")

    config_dir = Path.home() / ".discogs"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.toml"
    config_path.write_text(
        f'[discogs]\ntoken = "{token}"\nusername = "{username}"\n'
    )
    config_path.chmod(0o600)
    click.echo(f"Saved config to {config_path}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_cli_auth.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/discogs/cli/ tests/unit/test_cli_auth.py
git commit -m "feat(cli): root group and `auth set` with chmod-600 storage"
```

---

## Task 13: CLI — `sync` command

**Files:**
- Create: `src/discogs/cli/commands/sync_cmd.py`
- Modify: `src/discogs/cli/__main__.py` (register command)
- Test: `tests/unit/test_cli_sync.py`

Wires the Syncer into the CLI. Defaults to `scope=both`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cli_sync.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from discogs.cli.__main__ import cli
from discogs.sync.syncer import SyncResult


def _seed_config(home: Path) -> None:
    cfg_dir = home / ".discogs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text('[discogs]\ntoken = "t"\nusername = "lorenzo"')


def test_sync_default_scope_both(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake_syncer = MagicMock()
    fake_syncer.sync.return_value = SyncResult(collection_synced=42, wantlist_synced=7)

    with patch("discogs.cli.commands.sync_cmd._build_syncer", return_value=fake_syncer):
        result = CliRunner().invoke(cli, ["sync"])

    assert result.exit_code == 0, result.output
    fake_syncer.sync.assert_called_once_with(scope="both", force=False)
    assert "42" in result.output
    assert "7" in result.output


def test_sync_force_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake = MagicMock()
    fake.sync.return_value = SyncResult(collection_synced=0, wantlist_synced=0)

    with patch("discogs.cli.commands.sync_cmd._build_syncer", return_value=fake):
        result = CliRunner().invoke(cli, ["sync", "--force", "--scope", "collection"])

    assert result.exit_code == 0
    fake.sync.assert_called_once_with(scope="collection", force=True)


def test_sync_reports_skipped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_config(tmp_path)

    fake = MagicMock()
    fake.sync.return_value = SyncResult(collection_synced=None, wantlist_synced=None)

    with patch("discogs.cli.commands.sync_cmd._build_syncer", return_value=fake):
        result = CliRunner().invoke(cli, ["sync"])

    assert "skipped" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cli_sync.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `src/discogs/cli/commands/sync_cmd.py`**

```python
"""`discogs sync` command."""
from __future__ import annotations

import click

from discogs.api.client import DiscogsClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import load_config
from discogs.sync.syncer import Syncer


def _build_syncer() -> Syncer:
    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store)
    return Syncer(cfg, store, client)


@click.command("sync")
@click.option(
    "--scope",
    type=click.Choice(["collection", "wantlist", "both"]),
    default="both",
    show_default=True,
)
@click.option("--force", is_flag=True, help="Bypass the 24h TTL.")
def sync_cmd(scope: str, force: bool) -> None:
    """Sync collection and/or wantlist into the local cache."""
    syncer = _build_syncer()
    result = syncer.sync(scope=scope, force=force)  # type: ignore[arg-type]

    parts: list[str] = []
    if result.collection_synced is None and scope in ("collection", "both"):
        parts.append("collection: skipped (within TTL)")
    elif result.collection_synced is not None:
        parts.append(f"collection: {result.collection_synced} items")
    if result.wantlist_synced is None and scope in ("wantlist", "both"):
        parts.append("wantlist: skipped (within TTL)")
    elif result.wantlist_synced is not None:
        parts.append(f"wantlist: {result.wantlist_synced} items")

    click.echo(" / ".join(parts))
```

- [ ] **Step 4: Register the command in `src/discogs/cli/__main__.py`**

Modify `src/discogs/cli/__main__.py` to import and register `sync_cmd`:

```python
"""`discogs` CLI root."""
from __future__ import annotations

import click

from discogs.cli.commands.auth import auth_group
from discogs.cli.commands.sync_cmd import sync_cmd


@click.group()
@click.version_option(package_name="discogs")
def cli() -> None:
    """Discogs collection sync and recommendation framework."""


cli.add_command(auth_group, name="auth")
cli.add_command(sync_cmd)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_cli_sync.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/discogs/cli/commands/sync_cmd.py src/discogs/cli/__main__.py tests/unit/test_cli_sync.py
git commit -m "feat(cli): `discogs sync` with --scope and --force"
```

---

## Task 14: CLI — `status` command

**Files:**
- Create: `src/discogs/cli/commands/status.py`
- Modify: `src/discogs/cli/__main__.py`
- Test: `tests/unit/test_cli_status.py`

Reports: username, cache path + size, last collection/wantlist sync, API calls used today, daily budget.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cli_status.py`:

```python
from datetime import UTC, datetime, timedelta
from pathlib import Path

from click.testing import CliRunner

from discogs.cache.store import CacheStore, init_db
from discogs.cli.__main__ import cli


def _seed(home: Path) -> None:
    cfg_dir = home / ".discogs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text('[discogs]\ntoken = "t"\nusername = "lorenzo"')
    init_db(cfg_dir / "cache.db")
    store = CacheStore(cfg_dir / "cache.db")
    store.record_sync("collection", datetime.now(UTC) - timedelta(hours=2))
    store.increment_api_calls(17)
    store.close()


def test_status_reports_key_facts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed(tmp_path)

    result = CliRunner().invoke(cli, ["status"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "lorenzo" in out
    assert "17" in out                 # API calls today
    assert "1500" in out               # default daily budget
    assert "collection" in out.lower() # last sync line
    assert "never" in out.lower() or "wantlist" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cli_status.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `src/discogs/cli/commands/status.py`**

```python
"""`discogs status` command."""
from __future__ import annotations

from datetime import UTC, datetime

import click
from rich.console import Console
from rich.table import Table

from discogs.cache.store import CacheStore, init_db
from discogs.config import load_config


def _humanize(when: datetime | None) -> str:
    if when is None:
        return "never"
    delta = datetime.now(UTC) - when
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


@click.command("status")
def status_cmd() -> None:
    """Show config, cache, and API-budget status."""
    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    try:
        cache_size = cfg.cache_path.stat().st_size if cfg.cache_path.exists() else 0
        last_collection = store.last_sync_at("collection")
        last_wantlist = store.last_sync_at("wantlist")
        calls_today = store.api_calls_today()
        budget = cfg.daily_api_budget

        table = Table(title="discogs status")
        table.add_column("key")
        table.add_column("value")
        table.add_row("username", cfg.discogs_username)
        table.add_row("cache path", str(cfg.cache_path))
        table.add_row("cache size", f"{cache_size / 1024:.1f} KiB")
        table.add_row("last collection sync", _humanize(last_collection))
        table.add_row("last wantlist sync", _humanize(last_wantlist))
        table.add_row("API calls today", f"{calls_today} / {budget}")
        Console().print(table)
    finally:
        store.close()
```

- [ ] **Step 4: Register the command in `src/discogs/cli/__main__.py`**

Add to imports and register:

```python
from discogs.cli.commands.status import status_cmd
# ...
cli.add_command(status_cmd)
```

The full file becomes:

```python
"""`discogs` CLI root."""
from __future__ import annotations

import click

from discogs.cli.commands.auth import auth_group
from discogs.cli.commands.status import status_cmd
from discogs.cli.commands.sync_cmd import sync_cmd


@click.group()
@click.version_option(package_name="discogs")
def cli() -> None:
    """Discogs collection sync and recommendation framework."""


cli.add_command(auth_group, name="auth")
cli.add_command(sync_cmd)
cli.add_command(status_cmd)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_cli_status.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add src/discogs/cli/commands/status.py src/discogs/cli/__main__.py tests/unit/test_cli_status.py
git commit -m "feat(cli): `discogs status` reports config, cache, sync, API budget"
```

---

## Task 15: Integration test — full sync with VCR cassette

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/integration/test_sync_integration.py`
- Create: `tests/integration/cassettes/.gitkeep`

End-to-end test using a `vcrpy` cassette. The cassette is **recorded once** by a developer with credentials (instructions in the task), then committed and replayed in CI.

- [ ] **Step 1: Create `tests/conftest.py`**

```python
"""Shared pytest fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run with HOME pointed at a temp dir; ensures no test touches the real ~/.discogs."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path
```

- [ ] **Step 2: Write the integration test**

Create `tests/integration/test_sync_integration.py`:

```python
"""End-to-end sync test against recorded HTTP cassettes.

To (re)record:
    DISCOGS_TOKEN=<your-token> DISCOGS_USERNAME=<you> \\
        python -m pytest tests/integration/test_sync_integration.py --record-mode=once

Cassettes are committed; CI replays them, never hits the live API.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import vcr

CASSETTE_DIR = Path(__file__).parent / "cassettes"

my_vcr = vcr.VCR(
    cassette_library_dir=str(CASSETTE_DIR),
    record_mode=os.environ.get("VCR_RECORD_MODE", "none"),  # `none` = replay-only by default
    filter_headers=["Authorization"],
    filter_query_parameters=["token"],
)


@pytest.mark.skipif(
    not (CASSETTE_DIR / "sync_collection.yaml").exists(),
    reason="Cassette not recorded yet. See module docstring for recording instructions.",
)
def test_full_sync_against_cassette(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_dir = tmp_path / ".discogs"
    cfg_dir.mkdir()
    token = os.environ.get("DISCOGS_TOKEN", "redacted-replay-token")
    username = os.environ.get("DISCOGS_USERNAME", "test-user")
    (cfg_dir / "config.toml").write_text(
        f'[discogs]\ntoken = "{token}"\nusername = "{username}"\n'
    )

    from discogs.api.client import DiscogsClient
    from discogs.cache.store import CacheStore, init_db
    from discogs.config import load_config
    from discogs.sync.syncer import Syncer

    cfg = load_config()
    init_db(cfg.cache_path)
    store = CacheStore(cfg.cache_path)
    client = DiscogsClient(cfg, store)
    syncer = Syncer(cfg, store, client)

    with my_vcr.use_cassette("sync_collection.yaml"):
        result = syncer.sync(scope="both", force=True)

    assert result.collection_synced is not None
    assert result.collection_synced >= 0
    assert result.wantlist_synced is not None
    store.close()
```

- [ ] **Step 3: Create cassette directory**

```bash
mkdir -p tests/integration/cassettes
touch tests/integration/cassettes/.gitkeep
```

- [ ] **Step 4: Run the integration test (will skip)**

Run: `pytest tests/integration/test_sync_integration.py -v`
Expected: 1 skipped (cassette not yet recorded). This is fine — recording is a one-time developer task documented in the test docstring.

- [ ] **Step 5: Run the full unit test suite**

Run: `pytest tests/unit/ -v`
Expected: all unit tests pass.

- [ ] **Step 6: Run lint and types**

Run: `ruff check src/ tests/ && mypy src/`
Expected: zero errors. If mypy complains about `python3-discogs-client` types, the `ignore_missing_imports = True` in `mypy.ini` should already cover it; if anything else fails, fix it before committing.

- [ ] **Step 7: Commit**

```bash
git add tests/conftest.py tests/integration/
git commit -m "test(sync): VCR-cassette-based integration test for full sync"
```

---

## Task 16: Quickstart documentation

**Files:**
- Modify: `README.md`

Replace the bare README with a quickstart that walks a fresh user from `pip install` to `discogs status`.

- [ ] **Step 1: Replace `README.md`**

```markdown
# discogs

A Python library + CLI for the Discogs API. Phase 1 ships collection/wantlist sync into a local cache. Recommendation features (Phase 2+) follow.

## Install

```bash
git clone <this-repo>
cd discogs
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quickstart

```bash
# 1. Save your Discogs personal access token + username
discogs auth set
# Prompts (token is hidden):
#   Discogs personal access token: ********
#   Discogs username: lorenzo

# 2. Sync collection + wantlist into ~/.discogs/cache.db (~1 minute on first run)
discogs sync

# 3. Check status
discogs status
```

Get a personal access token at <https://www.discogs.com/settings/developers>.

## Config

`~/.discogs/config.toml`:

```toml
[discogs]
token = "..."
username = "lorenzo"

[cache]
path = "~/.discogs/cache.db"   # optional override
```

Env overrides: `DISCOGS_TOKEN`, `ANTHROPIC_API_KEY`.

## Commands

| Command | Purpose |
|---|---|
| `discogs auth set` | Save token to `~/.discogs/config.toml` (chmod 600) |
| `discogs sync [--scope collection|wantlist|both] [--force]` | Sync into local cache. 24h TTL by default. |
| `discogs status` | Show username, cache size, last sync, API budget |

## Development

```bash
pytest                        # unit + integration (cassettes)
ruff check src/ tests/        # lint
mypy src/                     # types
```

See `docs/superpowers/specs/` for the full design and `docs/superpowers/plans/` for the implementation plan.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README quickstart for Phase 1 (auth, sync, status)"
```

---

## Phase 1 verification

After completing all tasks above, perform a final verification pass.

- [ ] **Step 1: Run the full test suite**

```bash
pytest -v
```

Expected: all unit tests pass, integration test skipped (cassette not recorded — fine for now).

- [ ] **Step 2: Run lint and types**

```bash
ruff check src/ tests/
mypy src/
```

Expected: zero errors.

- [ ] **Step 3: Smoke-test the CLI manually (optional but recommended)**

Real-account test (requires a Discogs personal access token):

```bash
discogs auth set       # paste your token
discogs sync           # should pull your real collection + wantlist
discogs status         # confirms last_sync, cache size, API budget
```

If the smoke test passes, Phase 1 is complete and the user has working software. Phase 2 (recommendation pipeline MVP) is the next plan to write.

---

## Self-review notes

- **Spec coverage (Phase 1 portion):** Auth (Spec §"Config & secrets") → Task 12. SQLite cache schema (Spec §"Storage") → Tasks 4, 5, 6, 7 with the influence-graph and recommendation tables created up front so future plans don't need migrations. API client wrapper (Spec §"Architecture / api/") → Task 8. Collection + wantlist fetch + sync (Spec §"Architecture / sync") → Tasks 9, 10, 11. CLI surface (Spec §"CLI surface" — auth, sync, status only for Phase 1) → Tasks 12, 13, 14. Test strategy (Spec §"Testing strategy" — unit + VCR integration) → Tasks 2–14 (unit), 15 (integration).
- **Out of scope, deferred to later phases:** recommendation pipeline (graph walk, scoring, enrichment), influence expansion, wantlist writes, undo, digest generation, full release/artist/label fetchers, daily LLM budget. These remain documented in the spec.
- **Future plans:**
  - Plan 2 — Recommend MVP: release/artist/label fetchers, credit graph walk, basic scoring, digest generation, `discogs recommend` (dry-run only).
  - Plan 3 — Influences + Enrichment: Stage 1.5 influence expansion, Stage 4 LLM enrichment.
  - Plan 4 — Wantlist Writes: `--apply` flag, `discogs apply`, `discogs undo-last-batch`, recommendation_history bookkeeping.
