from collections.abc import Iterator
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
