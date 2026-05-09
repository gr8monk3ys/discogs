"""Shared pytest fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run with HOME pointed at a temp dir; ensures no test touches the real ~/.discogs."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path
