"""Validation of RecommendParams.__post_init__."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from discogs.recommend.pipeline import RecommendParams


def test_defaults_construct_without_error() -> None:
    p = RecommendParams()
    assert p.max_recs == 25
    assert p.seed_mode == "both"


def test_budget_zero_is_allowed() -> None:
    """budget=0 means cached-data-only — supported since commit 52bd84d."""
    RecommendParams(budget=0)


@pytest.mark.parametrize(
    "kwargs, bad_field",
    [
        ({"max_recs": 0}, "max_recs"),
        ({"max_per_artist": 0}, "max_per_artist"),
        ({"min_seed_occurrences": 0}, "min_seed_occurrences"),
        ({"max_neighbors_per_seed": 0}, "max_neighbors_per_seed"),
        ({"max_releases_per_neighbor": 0}, "max_releases_per_neighbor"),
        ({"top_k_seeds_for_influences": 0}, "top_k_seeds_for_influences"),
        ({"budget": -1}, "budget"),
    ],
)
def test_positive_int_fields_reject_invalid(kwargs: dict, bad_field: str) -> None:
    with pytest.raises(ValueError, match=bad_field):
        RecommendParams(**kwargs)


def test_seed_mode_must_be_in_whitelist() -> None:
    with pytest.raises(ValueError, match="seed_mode"):
        RecommendParams(seed_mode="garbage")


def test_frozen_blocks_mutation() -> None:
    p = RecommendParams()
    with pytest.raises(FrozenInstanceError):
        p.max_recs = 100  # type: ignore[misc]
