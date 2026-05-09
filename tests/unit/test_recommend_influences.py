from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs.api.llm import LLMClient
from discogs.cache.store import CacheStore, init_db
from discogs.config import Config
from discogs.recommend.influences import (
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
