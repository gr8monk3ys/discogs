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
