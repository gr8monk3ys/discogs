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

    def charge_call(self, n: int = 1) -> None:
        """Record `n` additional API calls that bypassed `call()`.

        Used by callers that invoke methods on cached `upstream` objects
        (e.g. `client.call("user", u).wantlist.add(release_id)`).
        """
        self._store.increment_api_calls(n)
