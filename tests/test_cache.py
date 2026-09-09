"""Cache behaviour: expiry, eviction, and single-flight under concurrency."""

from __future__ import annotations

import asyncio

import pytest

from sleeper_mcp.cache import AsyncTTLCache, TTLCache


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_hit_then_expiry() -> None:
    clock = FakeClock()
    cache = TTLCache(default_ttl=10, clock=clock)
    cache.set("k", "v")

    assert cache.get("k") == "v"
    clock.advance(9.9)
    assert cache.get("k") == "v"
    clock.advance(0.2)
    assert cache.get("k") is None
    assert cache.stats.expirations == 1


def test_lru_eviction_keeps_recently_read_entries() -> None:
    cache = TTLCache(default_ttl=100, max_entries=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")          # 'a' becomes most-recently-used
    cache.set("c", 3)       # so 'b' is the one evicted

    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.get("c") == 3
    assert cache.stats.evictions == 1


def test_rejects_nonsense_configuration() -> None:
    with pytest.raises(ValueError):
        TTLCache(default_ttl=0)
    with pytest.raises(ValueError):
        TTLCache(max_entries=0)


async def test_concurrent_misses_share_one_fetch() -> None:
    cache = AsyncTTLCache(default_ttl=100)
    calls = 0

    async def fetch() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return "payload"

    results = await asyncio.gather(*(cache.get_or_fetch("k", fetch) for _ in range(5)))

    assert results == ["payload"] * 5
    assert calls == 1, "the whole point is that four callers wait rather than refetch"


async def test_failed_fetch_is_not_cached() -> None:
    cache = AsyncTTLCache(default_ttl=100)
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("boom")
        return "ok"

    with pytest.raises(RuntimeError):
        await cache.get_or_fetch("k", flaky)
    assert await cache.get_or_fetch("k", flaky) == "ok"
    assert attempts == 2


async def test_one_caller_giving_up_does_not_kill_the_fetch() -> None:
    cache = AsyncTTLCache(default_ttl=100)
    calls = 0

    async def slow() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return "value"

    quitter = asyncio.ensure_future(cache.get_or_fetch("k", slow))
    stayer = asyncio.ensure_future(cache.get_or_fetch("k", slow))
    await asyncio.sleep(0.01)
    quitter.cancel()

    assert await stayer == "value"
    assert calls == 1
