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
