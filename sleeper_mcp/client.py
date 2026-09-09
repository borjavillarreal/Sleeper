"""Async HTTP client for the read-only Sleeper API.

Sleeper's v1 API needs no authentication and exposes no write endpoints.  The
published guidance is to stay under 1000 calls per minute and to pull the
player catalog at most once per day; this client enforces the first with a
token bucket and supports the second through per-endpoint cache TTLs.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from .cache import AsyncTTLCache
from .errors import (
    SleeperBadResponse,
    SleeperNotFound,
    SleeperRateLimited,
    SleeperUnavailable,
)

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.sleeper.app/v1"

#: Cache lifetimes in seconds, chosen against how fast each resource moves.
TTL_NFL_STATE = 300.0
TTL_USER = 3600.0
TTL_LEAGUE_LIST = 600.0
TTL_LEAGUE = 600.0
TTL_LEAGUE_USERS = 600.0
TTL_ROSTERS = 120.0
TTL_MATCHUPS = 60.0        # live scoring on Sundays
TTL_DRAFT_LIST = 600.0
TTL_DRAFT = 300.0
TTL_DRAFT_PICKS = 60.0     # picks land in real time while a draft is running
TTL_TRANSACTIONS = 120.0
TTL_BRACKET = 600.0
TTL_TRADED_PICKS = 300.0
TTL_TRENDING = 900.0
TTL_PLAYERS = 86400.0      # the API asks for at most one call per day
TTL_PLAYER_STATS = 900.0

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class RateLimiter:
    """Token bucket.  ``rate`` tokens accrue per second, up to ``burst``."""

    def __init__(self, rate: float, burst: int, clock=time.monotonic) -> None:
        if rate <= 0 or burst <= 0:
            raise ValueError("rate and burst must be positive")
        self._rate = rate
        self._burst = burst
        self._clock = clock
        self._tokens = float(burst)
        self._updated = clock()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = self._clock()
                self._tokens = min(
                    self._burst, self._tokens + (now - self._updated) * self._rate
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            await asyncio.sleep(wait)


class SleeperClient:
    """Thin, cached wrapper over the Sleeper v1 endpoints.

    Every method returns plain decoded JSON.  Turning that into something a
    language model can read is :mod:`sleeper_mcp.formatting`'s job -- keeping
    the two apart means the client stays trivially testable against fixtures.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 20.0,
        max_retries: int = 3,
        calls_per_minute: int = 900,
        cache: AsyncTTLCache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._cache = cache if cache is not None else AsyncTTLCache(max_entries=256)
        self._limiter = RateLimiter(rate=calls_per_minute / 60.0, burst=30)
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "sleeper-mcp/0.1"},
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> SleeperClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ---------------------------------------------------------------- transport

    async def _request(self, path: str, params: Mapping[str, Any] | None) -> Any:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            await self._limiter.acquire()
            try:
                response = await self._client.get(path, params=params)
            except httpx.HTTPError as exc:  # timeouts, DNS, connection resets
                last_error = exc
                if attempt == self._max_retries:
                    break
                await asyncio.sleep(self._backoff(attempt))
                continue

            if response.status_code == 404:
                raise SleeperNotFound(f"Sleeper has no resource at {path}")

            if response.status_code in _RETRYABLE_STATUS:
                retry_after = self._retry_after(response)
                if attempt == self._max_retries:
                    if response.status_code == 429:
                        raise SleeperRateLimited(
                            "Sleeper rate limit hit and retries were exhausted",
                            retry_after=retry_after,
                        )
                    raise SleeperUnavailable(
                        f"Sleeper returned HTTP {response.status_code} for {path}"
                    )
                delay = retry_after if retry_after is not None else self._backoff(attempt)
                log.warning(
                    "Sleeper %s for %s; retrying in %.1fs",
                    response.status_code,
                    path,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            if response.status_code >= 400:
                raise SleeperUnavailable(
                    f"Sleeper returned HTTP {response.status_code} for {path}"
                )

            if not response.content.strip():
                # An empty 200 is Sleeper's other way of saying "nothing here".
                return None

            try:
                return response.json()
            except ValueError as exc:
                raise SleeperBadResponse(f"Sleeper sent non-JSON for {path}") from exc

        raise SleeperUnavailable(f"Could not reach Sleeper for {path}") from last_error

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Exponential backoff with jitter, capped so a tool call still returns."""
        return min(8.0, 0.5 * (2**attempt)) * (0.5 + random.random() / 2)

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return max(0.0, min(30.0, float(raw)))
        except ValueError:
            return None  # HTTP-date form; the backoff schedule covers us

    async def _get(
        self,
        path: str,
        *,
        ttl: float,
        params: Mapping[str, Any] | None = None,
        allow_missing: bool = False,
    ) -> Any:
        """Fetch ``path``, sharing both the cache and any in-flight request.

        Sleeper answers some unknown-but-well-formed lookups with a ``200`` and
        a literal ``null`` rather than a 404, so both shapes end up as
        :class:`SleeperNotFound` unless ``allow_missing`` is set.
        """
        key = (path, tuple(sorted((params or {}).items())))
        try:
            data = await self._cache.get_or_fetch(
                key, lambda: self._request(path, params), ttl
            )
        except SleeperNotFound:
            if allow_missing:
                return None
            raise
        if data is None and not allow_missing:
            raise SleeperNotFound(f"Sleeper has no resource at {path}")
        return data

    # ------------------------------------------------------------------- state

    async def get_nfl_state(self) -> dict[str, Any]:
        return await self._get("/state/nfl", ttl=TTL_NFL_STATE)

    # -------------------------------------------------------------------- user

    async def get_user(self, username_or_id: str) -> dict[str, Any]:
        return await self._get(f"/user/{username_or_id}", ttl=TTL_USER)

    async def get_user_leagues(
        self, user_id: str, season: str, sport: str = "nfl"
    ) -> list[dict[str, Any]]:
        return await self._get(
            f"/user/{user_id}/leagues/{sport}/{season}", ttl=TTL_LEAGUE_LIST
        )

    async def get_user_drafts(
        self, user_id: str, season: str, sport: str = "nfl"
    ) -> list[dict[str, Any]]:
        return await self._get(
            f"/user/{user_id}/drafts/{sport}/{season}", ttl=TTL_DRAFT_LIST
        )

    # ------------------------------------------------------------------ league

    async def get_league(self, league_id: str) -> dict[str, Any]:
        return await self._get(f"/league/{league_id}", ttl=TTL_LEAGUE)

    async def get_league_users(self, league_id: str) -> list[dict[str, Any]]:
        return await self._get(f"/league/{league_id}/users", ttl=TTL_LEAGUE_USERS)

    async def get_league_rosters(self, league_id: str) -> list[dict[str, Any]]:
        return await self._get(f"/league/{league_id}/rosters", ttl=TTL_ROSTERS)

    async def get_matchups(self, league_id: str, week: int) -> list[dict[str, Any]]:
        return await self._get(
            f"/league/{league_id}/matchups/{week}", ttl=TTL_MATCHUPS
        )

    async def get_transactions(self, league_id: str, week: int) -> list[dict[str, Any]]:
        return await self._get(
            f"/league/{league_id}/transactions/{week}", ttl=TTL_TRANSACTIONS
        )

    async def get_traded_picks(self, league_id: str) -> list[dict[str, Any]]:
        return await self._get(
            f"/league/{league_id}/traded_picks", ttl=TTL_TRADED_PICKS
        )

    async def get_bracket(
        self, league_id: str, kind: str = "winners"
    ) -> list[dict[str, Any]]:
        if kind not in ("winners", "losers"):
            raise ValueError("kind must be 'winners' or 'losers'")
        return await self._get(
            f"/league/{league_id}/{kind}_bracket", ttl=TTL_BRACKET
        )

    # ------------------------------------------------------------------- draft

    async def get_league_drafts(self, league_id: str) -> list[dict[str, Any]]:
        return await self._get(f"/league/{league_id}/drafts", ttl=TTL_DRAFT_LIST)

    async def get_draft(self, draft_id: str) -> dict[str, Any]:
        return await self._get(f"/draft/{draft_id}", ttl=TTL_DRAFT)

    async def get_draft_picks(self, draft_id: str) -> list[dict[str, Any]]:
        return await self._get(f"/draft/{draft_id}/picks", ttl=TTL_DRAFT_PICKS)

    # ----------------------------------------------------------------- players

    async def get_all_players(self, sport: str = "nfl") -> dict[str, Any]:
        """The full player catalog.  Several megabytes; cached for a day."""
        return await self._get(f"/players/{sport}", ttl=TTL_PLAYERS)

    async def get_trending_players(
        self,
        kind: str = "add",
        *,
        sport: str = "nfl",
        lookback_hours: int = 24,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        if kind not in ("add", "drop"):
            raise ValueError("kind must be 'add' or 'drop'")
        return await self._get(
            f"/players/{sport}/trending/{kind}",
            ttl=TTL_TRENDING,
            params={"lookback_hours": lookback_hours, "limit": limit},
        )

    async def get_player_stats(
        self,
        player_id: str,
        season: str,
        *,
        season_type: str = "regular",
        grouping: str = "week",
    ) -> Any:
        return await self._get(
            f"/stats/nfl/player/{player_id}",
            ttl=TTL_PLAYER_STATS,
            params={
                "season": season,
                "season_type": season_type,
                "grouping": grouping,
            },
            allow_missing=True,
        )


__all__: Sequence[str] = ("SleeperClient", "RateLimiter", "DEFAULT_BASE_URL")
