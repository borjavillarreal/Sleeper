"""MCP server exposing the Sleeper fantasy football API as tools.

Every tool returns human-readable text rather than raw JSON.  That is the whole
point of the server: Sleeper answers in player ids and roster ids, and a model
handed ``{"starters": ["4046", "6794"]}`` cannot say anything useful about it.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, TypeVar

from mcp.server.mcpserver import MCPServer

from . import formatting
from .client import SleeperClient
from .errors import SleeperError, SleeperNotFound
from .players import PlayerIndex, PlayerIndexLoader

log = logging.getLogger(__name__)

T = TypeVar("T")

server = MCPServer(
    name="sleeper",
    version="0.1.0",
    instructions=(
        "Read-only access to the Sleeper fantasy football API. Leagues are "
        "addressed by league_id, which you can find with get_user_leagues. "
        "Season and week default to the current NFL state when omitted."
    ),
)


class AppState:
    """Process-wide client and player index, created on first use.

    The stdio server is a single process serving one client, so a module-level
    singleton is the right shape here; the lock only guards the racing first
    call.
    """

    def __init__(self) -> None:
        self._client: SleeperClient | None = None
        self._loader: PlayerIndexLoader | None = None
        self._lock = asyncio.Lock()

    async def client(self) -> SleeperClient:
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    self._client = SleeperClient()
                    self._loader = PlayerIndexLoader(self._client)
        return self._client

    async def players(self) -> PlayerIndex:
        await self.client()
        assert self._loader is not None
        return await self._loader.load()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            self._loader = None

    def reset(self, client: SleeperClient) -> None:
        """Swap in a client.  Tests use this; nothing else should."""
        self._client = client
        self._loader = PlayerIndexLoader(client)


state = AppState()


def handles_errors(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
    """Report Sleeper failures as tool text instead of raising.

    A tool that raises gives the model a protocol error and nothing to act on;
    a tool that says "no such league" lets it correct itself and retry.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return await fn(*args, **kwargs)
        except SleeperNotFound as exc:
            return f"Not found: {exc}"
        except SleeperError as exc:
            return f"Sleeper API error: {exc}"
        except ValueError as exc:
            return f"Invalid argument: {exc}"

    return wrapper


async def _current_state() -> Mapping[str, Any]:
    client = await state.client()
    return await client.get_nfl_state()


async def _resolve_season(season: str | None) -> str:
    if season:
        return str(season)
    return str((await _current_state()).get("season") or "")


async def _resolve_week(week: int | None) -> int:
    if week is not None:
        if week < 1 or week > 22:
            raise ValueError("week must be between 1 and 22")
        return week
    current = await _current_state()
    return int(current.get("week") or current.get("display_week") or 1)


async def _league_context(
    league_id: str,
) -> tuple[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]], dict[int, str], PlayerIndex]:
    """Fetch the three things nearly every league tool needs, concurrently."""
    client = await state.client()
    users, rosters, index = await asyncio.gather(
        client.get_league_users(league_id),
        client.get_league_rosters(league_id),
        state.players(),
    )
    return users, rosters, formatting.team_names(users, rosters), index


# --------------------------------------------------------------------- tools


@server.tool()
@handles_errors
async def get_nfl_state() -> str:
    """Current NFL season, week and season type (preseason/regular/post).

    Call this when a question depends on "now" -- which week it is, whether the
    season is live -- or to find the default season used by other tools.
    """
    current = await _current_state()
    return (
        f"Season {current.get('season')} ({current.get('season_type')})\n"
        f"Current week: {current.get('week')}\n"
        f"Display week: {current.get('display_week')}\n"
        f"Leg: {current.get('leg')}\n"
        f"Season start: {current.get('season_start_date')}"
    )


@server.tool()
@handles_errors
async def get_user(username: str) -> str:
    """Look up a Sleeper user by username (or user id).

    Returns the numeric user_id that league and draft lookups need.
    """
    client = await state.client()
    user = await client.get_user(username)
    return (
        f"Display name: {user.get('display_name')}\n"
        f"Username: {user.get('username')}\n"
        f"User ID: {user.get('user_id')}"
    )


@server.tool()
@handles_errors
async def get_user_leagues(username: str, season: str | None = None) -> str:
    """List every league a user is in for a season.

    Args:
        username: Sleeper username or user id.
        season: Four-digit year. Defaults to the current NFL season.
    """
    client = await state.client()
    season = await _resolve_season(season)
    user = await client.get_user(username)
    leagues = await client.get_user_leagues(str(user["user_id"]), season)
    if not leagues:
        return f"{username} is not in any leagues for {season}."

    lines = [f"# Leagues for {user.get('display_name')} in {season}", ""]
    lines.append("| League | Teams | Status | League ID |")
    lines.append("|---|---|---|---|")
    for league in leagues:
        lines.append(
            f"| {league.get('name')} | {league.get('total_rosters')} "
            f"| {league.get('status')} | {league.get('league_id')} |"
        )
    return "\n".join(lines)


@server.tool()
@handles_errors
async def get_league(league_id: str) -> str:
    """League settings plus the current standings.

    Args:
        league_id: The league's id, from get_user_leagues.
    """
    client = await state.client()
    league, (_, rosters, names, _) = await asyncio.gather(
        client.get_league(league_id), _league_context(league_id)
    )
    return "\n\n".join(
        [
            formatting.format_league(league),
            "## Standings",
            formatting.format_standings(rosters, names),
        ]
    )


@server.tool()
@handles_errors
async def get_rosters(league_id: str, roster_id: int | None = None) -> str:
    """Full rosters with player names, split into starters and bench.

    Args:
        league_id: The league's id.
        roster_id: Limit to one team. Omit for every team in the league.
    """
    _, rosters, names, index = await _league_context(league_id)
    if roster_id is not None:
        rosters = [r for r in rosters if int(r.get("roster_id", 0)) == roster_id]
        if not rosters:
            return f"League {league_id} has no roster {roster_id}."
    blocks = [
        formatting.format_roster(
            roster, index, names.get(int(roster.get("roster_id", 0)), "Unknown team")
        )
        for roster in sorted(rosters, key=lambda r: r.get("roster_id") or 0)
    ]
    return "\n\n".join(blocks)


@server.tool()
@handles_errors
async def get_matchups(
    league_id: str, week: int | None = None, include_starters: bool = False
) -> str:
    """Head-to-head matchups and scores for a week.

    Args:
        league_id: The league's id.
        week: NFL week 1-22. Defaults to the current week.
        include_starters: Also list each starter and the points they scored.
    """
    client = await state.client()
    week = await _resolve_week(week)
    matchups, (_, _, names, index) = await asyncio.gather(
        client.get_matchups(league_id, week), _league_context(league_id)
    )
    return formatting.format_matchups(
        matchups, index, names, week, detail=include_starters
    )


@server.tool()
@handles_errors
async def get_draft(
    league_id: str | None = None,
    draft_id: str | None = None,
    rounds: int | None = None,
) -> str:
    """Draft board with player names, team attribution and keeper flags.

    Args:
        league_id: Use the league's most recent draft. Ignored if draft_id is given.
        draft_id: Draft to fetch directly.
        rounds: Stop after this many rounds. Omit for the whole draft.
    """
    if not league_id and not draft_id:
        raise ValueError("pass either league_id or draft_id")

    client = await state.client()
    if not draft_id:
        drafts = await client.get_league_drafts(str(league_id))
        if not drafts:
            return f"League {league_id} has no drafts."
        draft_id = str(drafts[0]["draft_id"])

    draft, picks, index = await asyncio.gather(
        client.get_draft(draft_id), client.get_draft_picks(draft_id), state.players()
    )
    names: dict[int, str] = {}
    league_for_names = league_id or draft.get("league_id")
    if league_for_names:
        try:
            _, _, names, _ = await _league_context(str(league_for_names))
        except SleeperError:
            pass  # a mock or orphaned draft still renders, just without team names
    return formatting.format_draft_picks(draft, picks, index, names, rounds=rounds)


@server.tool()
@handles_errors
async def search_players(
    query: str,
    position: str | None = None,
    team: str | None = None,
    limit: int = 10,
) -> str:
    """Find NFL players by name, ranked by match quality.

    Args:
        query: Full or partial name, e.g. "mahomes" or "amon ra".
        position: Filter to QB, RB, WR, TE, K, DEF, and so on.
        team: Filter to a team abbreviation such as KC or SF.
        limit: Maximum rows to return (1-50).
    """
    limit = max(1, min(50, limit))
    index = await state.players()
    return formatting.format_players(
        index.search(query, position=position, team=team, limit=limit)
    )


@server.tool()
@handles_errors
async def get_trending_players(
    kind: str = "add", lookback_hours: int = 24, limit: int = 25
) -> str:
    """Players being added or dropped most across all Sleeper leagues.

    Args:
        kind: "add" for waiver targets, "drop" for players being cut.
        lookback_hours: Window to measure over, typically 24.
        limit: Maximum rows to return (1-100).
    """
    if kind not in ("add", "drop"):
        raise ValueError("kind must be 'add' or 'drop'")
    limit = max(1, min(100, limit))

    client = await state.client()
    trending, index = await asyncio.gather(
        client.get_trending_players(kind, lookback_hours=lookback_hours, limit=limit),
        state.players(),
    )
    if not trending:
        return f"No trending {kind}s in the last {lookback_hours} hours."

    verb = "added" if kind == "add" else "dropped"
    lines = [
        f"# Most {verb} players (last {lookback_hours}h)",
        "",
        "| Player | Count |",
        "|---|---|",
    ]
    for entry in trending:
        lines.append(
            f"| {index.label(str(entry.get('player_id')))} | {entry.get('count')} |"
        )
    return "\n".join(lines)


@server.tool()
@handles_errors
async def get_player_stats(
    player: str, season: str | None = None, season_type: str = "regular"
) -> str:
    """Weekly stats for one player in a season.

    Args:
        player: Player name or Sleeper player id. Names are resolved by search.
        season: Four-digit year. Defaults to the current season.
        season_type: "regular", "post" or "pre".
    """
    client = await state.client()
    season = await _resolve_season(season)
    index = await state.players()

    resolved = index.get(player)
    if resolved is None:
        matches = index.search(player, limit=1)
        if not matches:
            return f"No player matched {player!r}."
        resolved = matches[0]

    stats = await client.get_player_stats(
        resolved.player_id, season, season_type=season_type
    )
    if not stats:
        return f"No {season} {season_type} stats are published for {resolved.label()}."

    lines = [f"# {resolved.detail()} - {season} {season_type}", ""]
    weeks = stats if isinstance(stats, Mapping) else {"all": stats}
    for week_key in sorted(weeks, key=_week_sort_key):
        week_stats = weeks[week_key] or {}
        if not isinstance(week_stats, Mapping):
            continue
        interesting = {
            k: v for k, v in week_stats.items() if k.startswith(("pts_", "gp"))
        }
        summary = ", ".join(f"{k}={v}" for k, v in sorted(interesting.items()))
        lines.append(f"- Week {week_key}: {summary or 'no fantasy points recorded'}")
    return "\n".join(lines)


def _week_sort_key(key: Any) -> tuple[int, Any]:
    try:
        return (0, int(key))
    except (TypeError, ValueError):
        return (1, str(key))


@server.tool()
@handles_errors
async def get_transactions(league_id: str, week: int | None = None) -> str:
    """Trades, waiver claims and free-agent moves for a week.

    Args:
        league_id: The league's id.
        week: NFL week 1-22. Defaults to the current week.
    """
    client = await state.client()
    week = await _resolve_week(week)
    transactions, (_, _, names, index) = await asyncio.gather(
        client.get_transactions(league_id, week), _league_context(league_id)
    )
    return formatting.format_transactions(transactions, index, names, week)


@server.tool()
@handles_errors
async def get_playoff_bracket(league_id: str, bracket: str = "winners") -> str:
    """Playoff bracket rounds and results.

    Args:
        league_id: The league's id.
        bracket: "winners" for the championship bracket, "losers" for the toilet bowl.
    """
    client = await state.client()
    rounds, (_, _, names, _) = await asyncio.gather(
        client.get_bracket(league_id, bracket), _league_context(league_id)
    )
    if not rounds:
        return f"No {bracket} bracket has been generated for league {league_id}."

    lines = [f"# {bracket.capitalize()} bracket"]
    for match in sorted(rounds, key=lambda m: (m.get("r") or 0, m.get("m") or 0)):
        team_1 = _bracket_team(match.get("t1"), names)
        team_2 = _bracket_team(match.get("t2"), names)
        winner = _bracket_team(match.get("w"), names) if match.get("w") else "TBD"
        lines.append(
            f"  Round {match.get('r')} match {match.get('m')}: "
            f"{team_1} vs {team_2} -> {winner}"
        )
    return "\n".join(lines)


def _bracket_team(value: Any, names: Mapping[int, str]) -> str:
    """Bracket slots hold a roster id, or a ``{from: ...}`` placeholder."""
    if value is None:
        return "TBD"
    if isinstance(value, Mapping):
        source = value.get("w") or value.get("l")
        return f"winner of match {source}" if value.get("w") else f"loser of match {source}"
    try:
        return names.get(int(value), f"Roster {value}")
    except (TypeError, ValueError):
        return str(value)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    server.run("stdio")


if __name__ == "__main__":
    main()
