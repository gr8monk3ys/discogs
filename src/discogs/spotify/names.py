"""Name normalisation for matching albums across libraries.

These four functions were a verbatim copy of the same four in two other
collection repos — the repos share a file format, not a process, so nothing at
import time made them agree. A golden corpus, duplicated into three test suites,
did. There is one implementation now, in `media_core.names`, and this module is
the name this repo has always imported it under.

Re-exported rather than replaced at every call site: `key` and `strip_edition`
are used across the API and sync layers, and moving them would have turned a
provable no-op into a diff nobody could check.
"""
from __future__ import annotations

from media_core.names import key, normalise, strip_article, strip_edition

__all__ = ["key", "normalise", "strip_article", "strip_edition"]
