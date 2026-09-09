"""Client transport behaviour against a mocked Sleeper host."""

from __future__ import annotations

import httpx
import pytest
import respx

from sleeper_mcp.cache import AsyncTTLCache
from sleeper_mcp.client import DEFAULT_BASE_URL, RateLimiter, SleeperClient
from sleeper_mcp.errors import SleeperNotFound, SleeperRateLimited, SleeperUnavailable


@pytest.fixture
def client() -> SleeperClient:
    # A huge burst and no sleeping: these tests are about HTTP, not pacing.
    return SleeperClient(max_retries=2, calls_per_minute=60_000)


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("sleeper_mcp.client.asyncio.sleep", instant)


@respx.mock
async def test_get_decodes_json(client: SleeperClient) -> None:
    route = respx.get(f"{DEFAULT_BASE_URL}/state/nfl").mock(
        return_value=httpx.Response(200, json={"week": 5})
    )
    assert await client.get_nfl_state() == {"week": 5}
    assert route.call_count == 1
    await client.aclose()


@respx.mock
async def test_second_call_is_served_from_cache(client: SleeperClient) -> None:
    route = respx.get(f"{DEFAULT_BASE_URL}/league/999").mock(
        return_value=httpx.Response(200, json={"league_id": "999"})
    )
    await client.get_league("999")
    await client.get_league("999")
    assert route.call_count == 1
    await client.aclose()


@respx.mock
async def test_query_params_are_part_of_the_cache_key(client: SleeperClient) -> None:
    route = respx.get(f"{DEFAULT_BASE_URL}/players/nfl/trending/add").mock(
        return_value=httpx.Response(200, json=[])
    )
    await client.get_trending_players("add", limit=10)
    await client.get_trending_players("add", limit=25)
    assert route.call_count == 2, "different limits must not collide in the cache"
    await client.aclose()


@respx.mock
async def test_404_becomes_not_found(client: SleeperClient) -> None:
    respx.get(f"{DEFAULT_BASE_URL}/user/nobody").mock(
        return_value=httpx.Response(404)
    )
    with pytest.raises(SleeperNotFound):
        await client.get_user("nobody")
    await client.aclose()


@respx.mock
async def test_null_body_becomes_not_found(client: SleeperClient) -> None:
    """Sleeper answers some unknown lookups with 200 and a literal null."""
    respx.get(f"{DEFAULT_BASE_URL}/league/000").mock(
        return_value=httpx.Response(
            200, content=b"null", headers={"content-type": "application/json"}
        )
    )
    with pytest.raises(SleeperNotFound):
        await client.get_league("000")
    await client.aclose()


@respx.mock
async def test_null_body_is_tolerated_where_allowed(client: SleeperClient) -> None:
    respx.get(f"{DEFAULT_BASE_URL}/stats/nfl/player/4046").mock(
        return_value=httpx.Response(
            200, content=b"null", headers={"content-type": "application/json"}
        )
    )
    assert await client.get_player_stats("4046", "2025") is None
    await client.aclose()


@respx.mock
async def test_transient_500_is_retried_then_succeeds(client: SleeperClient) -> None:
    route = respx.get(f"{DEFAULT_BASE_URL}/state/nfl").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"week": 7}),
        ]
    )
    assert await client.get_nfl_state() == {"week": 7}
    assert route.call_count == 2
    await client.aclose()


@respx.mock
async def test_persistent_500_raises_unavailable(client: SleeperClient) -> None:
    route = respx.get(f"{DEFAULT_BASE_URL}/state/nfl").mock(
        return_value=httpx.Response(502)
    )
    with pytest.raises(SleeperUnavailable):
        await client.get_nfl_state()
    assert route.call_count == 3, "initial attempt plus max_retries"
    await client.aclose()


@respx.mock
async def test_persistent_429_raises_rate_limited_with_retry_after(
    client: SleeperClient,
) -> None:
    respx.get(f"{DEFAULT_BASE_URL}/state/nfl").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "3"})
    )
    with pytest.raises(SleeperRateLimited) as excinfo:
        await client.get_nfl_state()
    assert excinfo.value.retry_after == 3.0
    await client.aclose()


@respx.mock
async def test_network_error_is_retried_then_reported(client: SleeperClient) -> None:
    route = respx.get(f"{DEFAULT_BASE_URL}/state/nfl").mock(
        side_effect=httpx.ConnectTimeout("no route")
    )
    with pytest.raises(SleeperUnavailable):
        await client.get_nfl_state()
    assert route.call_count == 3
    await client.aclose()


@respx.mock
async def test_concurrent_identical_requests_hit_the_wire_once() -> None:
    import asyncio

    client = SleeperClient(cache=AsyncTTLCache(), calls_per_minute=60_000)
    route = respx.get(f"{DEFAULT_BASE_URL}/players/nfl").mock(
        return_value=httpx.Response(200, json={"4046": {}})
    )
    await asyncio.gather(*(client.get_all_players() for _ in range(4)))
    assert route.call_count == 1
    await client.aclose()


@respx.mock
async def test_empty_body_is_treated_as_missing(client: SleeperClient) -> None:
    """A 200 with no body at all means the resource is absent, not corrupt."""
    respx.get(f"{DEFAULT_BASE_URL}/stats/nfl/player/4046").mock(
        return_value=httpx.Response(200, content=b"")
    )
    assert await client.get_player_stats("4046", "2025") is None
    await client.aclose()


async def test_bad_bracket_kind_raises() -> None:
    client = SleeperClient()
    with pytest.raises(ValueError):
        await client.get_bracket("999", "sideways")
    await client.aclose()


async def test_rate_limiter_lets_the_burst_through_immediately() -> None:
    limiter = RateLimiter(rate=1.0, burst=3)
    for _ in range(3):
        await limiter.acquire()  # would hang if the bucket were not pre-filled
