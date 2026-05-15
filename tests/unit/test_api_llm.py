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


def test_llm_client_configures_timeout(cfg: Config, store: CacheStore) -> None:
    """LLM client should set a finite timeout so a stalled Claude call fails fast."""
    captured: dict = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    LLMClient(cfg, store, upstream_factory=factory)
    assert captured.get("timeout") == 60.0


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
