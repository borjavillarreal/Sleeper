"""TTL cache with single-flight fetching.

Sleeper's player catalog is a multi-megabyte document that the API docs ask
callers to fetch at most once per day.  A plain cache is not enough: if three
tool calls miss at the same moment, all three fetch it.  ``AsyncTTLCache``
holds a per-key lock so concurrent misses share one request.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")

_MISSING = object()


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class TTLCache:
    """A bounded, time-to-live cache with LRU eviction.

    ``clock`` is injectable so tests can advance time without sleeping.
    """

    def __init__(
        self,
        *,
        default_ttl: float = 60.0,
        max_entries: int = 512,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if default_ttl <= 0:
            raise ValueError("default_ttl must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self.stats = CacheStats()

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: Hashable, default: Any = None) -> Any:
        entry = self._entries.get(key, _MISSING)
        if entry is _MISSING:
            self.stats.misses += 1
            return default
        expires_at, value = entry  # type: ignore[misc]
        if expires_at <= self._clock():
            del self._entries[key]
            self.stats.expirations += 1
            self.stats.misses += 1
            return default
        self._entries.move_to_end(key)
        self.stats.hits += 1
        return value

    def set(self, key: Hashable, value: Any, ttl: float | None = None) -> None:
        ttl = self._default_ttl if ttl is None else ttl
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        self._entries[key] = (self._clock() + ttl, value)
        self._entries.move_to_end(key)
        self._evict()

    def invalidate(self, key: Hashable) -> bool:
        return self._entries.pop(key, _MISSING) is not _MISSING

    def clear(self) -> None:
        self._entries.clear()

    def _evict(self) -> None:
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
            self.stats.evictions += 1


class AsyncTTLCache(TTLCache):
    """``TTLCache`` plus :meth:`get_or_fetch`, which collapses concurrent misses."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._inflight: dict[Hashable, asyncio.Task[Any]] = {}

    async def get_or_fetch(
        self,
        key: Hashable,
        factory: Callable[[], Awaitable[T]],
        ttl: float | None = None,
    ) -> T:
        """Return the cached value for ``key``, fetching it at most once.

        Callers that arrive while a fetch is in flight await that same fetch
        rather than starting their own.  If the fetch raises, every waiter sees
        the exception and nothing is cached.
        """
        cached = self.get(key, _MISSING)
        if cached is not _MISSING:
            return cached  # type: ignore[return-value]

        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._fetch_and_store(key, factory, ttl))
            self._inflight[key] = task
            task.add_done_callback(self._clear_inflight)

        # The store happens inside the task, and every caller shields, so one
        # caller giving up never cancels the fetch the others are waiting on.
        return await asyncio.shield(task)

    async def _fetch_and_store(
        self,
        key: Hashable,
        factory: Callable[[], Awaitable[T]],
        ttl: float | None,
    ) -> T:
        value = await factory()
        self.set(key, value, ttl)
        return value

    def _clear_inflight(self, task: asyncio.Task[Any]) -> None:
        for key, running in list(self._inflight.items()):
            if running is task:
                del self._inflight[key]
                break
