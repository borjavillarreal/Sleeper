"""End-to-end tool calls: MCP dispatch -> client -> mocked Sleeper -> text out."""

from __future__ import annotations

import httpx
import pytest
import respx

from sleeper_mcp.client import DEFAULT_BASE_URL, SleeperClient
from sleeper_mcp.server import server, state

from .conftest import LEAGUE, MATCHUPS, NFL_STATE, RAW_PLAYERS, ROSTERS, USERS

LEAGUE_ID = "999"


def _text(result: object) -> str:
    """Flatten a CallToolResult down to the text the model would see."""
    return "\n".join(
        block.text for block in result.content if getattr(block, "type", "") == "text"
    )


async def call(name: str, **arguments: object) -> str:
    return _text(await server.call_tool(name, dict(arguments)))


@pytest.fixture(autouse=True)
async def wired_state():
    """Point the server's singleton at a client with a fresh, empty cache."""
    client = SleeperClient(calls_per_minute=60_000)
    state.reset(client)
    try:
        yield
    finally:
        await client.aclose()


@pytest.fixture(autouse=True)
def sleeper_api():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{DEFAULT_BASE_URL}/state/nfl").mock(
            return_value=httpx.Response(200, json=NFL_STATE)
        )
        mock.get(f"{DEFAULT_BASE_URL}/players/nfl").mock(
            return_value=httpx.Response(200, json=RAW_PLAYERS)
        )
        mock.get(f"{DEFAULT_BASE_URL}/league/{LEAGUE_ID}").mock(
            return_value=httpx.Response(200, json=LEAGUE)
        )
        mock.get(f"{DEFAULT_BASE_URL}/league/{LEAGUE_ID}/users").mock(
            return_value=httpx.Response(200, json=USERS)
        )
        mock.get(f"{DEFAULT_BASE_URL}/league/{LEAGUE_ID}/rosters").mock(
            return_value=httpx.Response(200, json=ROSTERS)
        )
        mock.get(f"{DEFAULT_BASE_URL}/league/{LEAGUE_ID}/matchups/5").mock(
            return_value=httpx.Response(200, json=MATCHUPS)
        )
        yield mock


async def test_nfl_state() -> None:
    out = await call("get_nfl_state")
    assert "Season 2025 (regular)" in out
    assert "Current week: 5" in out


async def test_user_lookup(sleeper_api: respx.MockRouter) -> None:
    sleeper_api.get(f"{DEFAULT_BASE_URL}/user/borja").mock(
        return_value=httpx.Response(
            200, json={"user_id": "u1", "username": "borja", "display_name": "borja"}
        )
    )
    assert "User ID: u1" in await call("get_user", username="borja")


async def test_unknown_user_reports_cleanly_instead_of_raising(
    sleeper_api: respx.MockRouter,
) -> None:
    """A tool that raises gives the model nothing to correct; text does."""
    sleeper_api.get(f"{DEFAULT_BASE_URL}/user/ghost").mock(
        return_value=httpx.Response(404)
    )
    out = await call("get_user", username="ghost")
    assert out.startswith("Not found:")


async def test_user_leagues_defaults_to_current_season(
    sleeper_api: respx.MockRouter,
) -> None:
    sleeper_api.get(f"{DEFAULT_BASE_URL}/user/borja").mock(
        return_value=httpx.Response(200, json={"user_id": "u1", "display_name": "borja"})
    )
    route = sleeper_api.get(f"{DEFAULT_BASE_URL}/user/u1/leagues/nfl/2025").mock(
        return_value=httpx.Response(200, json=[LEAGUE])
    )
    out = await call("get_user_leagues", username="borja")
    assert route.call_count == 1, "season should come from /state/nfl"
    assert "Dynasty Warriors" in out and "| 999 |" in out


async def test_league_tool_merges_settings_and_standings() -> None:
    out = await call("get_league", league_id=LEAGUE_ID)
    assert "Dynasty Warriors" in out
    assert "full PPR" in out
    assert "## Standings" in out
    assert "Team Ravens (borja)" in out


async def test_rosters_resolve_player_ids_to_names() -> None:
    out = await call("get_rosters", league_id=LEAGUE_ID)
    assert "Patrick Mahomes (KC - QB)" in out
    assert "4046" not in out, "raw ids are exactly what this server exists to remove"


async def test_rosters_can_be_filtered_to_one_team() -> None:
    out = await call("get_rosters", league_id=LEAGUE_ID, roster_id=2)
    assert "sourknives" in out
    assert "Team Ravens" not in out

    missing = await call("get_rosters", league_id=LEAGUE_ID, roster_id=42)
    assert "no roster 42" in missing


async def test_matchups_default_to_the_current_week() -> None:
    out = await call("get_matchups", league_id=LEAGUE_ID)
    assert "# Week 5 matchups" in out
    assert "Team Ravens (borja) leads by 22.25" in out
    assert "Patrick Mahomes" not in out, "starter detail is opt-in"


async def test_matchups_with_starter_detail() -> None:
    out = await call("get_matchups", league_id=LEAGUE_ID, include_starters=True)
    assert "Patrick Mahomes (KC - QB): 25.5" in out


async def test_out_of_range_week_is_rejected_before_any_request() -> None:
    out = await call("get_matchups", league_id=LEAGUE_ID, week=99)
    assert out.startswith("Invalid argument:")


async def test_draft_uses_the_leagues_latest_draft(
    sleeper_api: respx.MockRouter,
) -> None:
    sleeper_api.get(f"{DEFAULT_BASE_URL}/league/{LEAGUE_ID}/drafts").mock(
        return_value=httpx.Response(200, json=[{"draft_id": "d1"}])
    )
    sleeper_api.get(f"{DEFAULT_BASE_URL}/draft/d1").mock(
        return_value=httpx.Response(
            200,
            json={"draft_id": "d1", "season": "2025", "type": "snake",
                  "status": "complete", "league_id": LEAGUE_ID,
                  "settings": {"rounds": 1, "teams": 2}},
        )
    )
    sleeper_api.get(f"{DEFAULT_BASE_URL}/draft/d1/picks").mock(
        return_value=httpx.Response(
            200,
            json=[{"round": 1, "pick_no": 1, "player_id": "4046",
                   "roster_id": 1, "is_keeper": True}],
        )
    )
    out = await call("get_draft", league_id=LEAGUE_ID)
    assert "Patrick Mahomes (KC - QB) -> Team Ravens (borja) [keeper]" in out


async def test_draft_requires_an_identifier() -> None:
    out = await call("get_draft")
    assert out.startswith("Invalid argument:")


async def test_search_players_returns_a_table() -> None:
    out = await call("search_players", query="mahomes")
    assert "| Patrick Mahomes | QB | KC |" in out


async def test_search_players_clamps_absurd_limits() -> None:
    out = await call("search_players", query="patrick", limit=10_000)
    assert out.count("\n") <= 12  # header, separator, and at most the real matches


async def test_trending_players_resolves_names(sleeper_api: respx.MockRouter) -> None:
    sleeper_api.get(f"{DEFAULT_BASE_URL}/players/nfl/trending/add").mock(
        return_value=httpx.Response(200, json=[{"player_id": "5849", "count": 4210}])
    )
    out = await call("get_trending_players")
    assert "| Kyler Murray (ARI - QB) | 4210 |" in out


async def test_trending_rejects_a_bad_kind() -> None:
    out = await call("get_trending_players", kind="sideways")
    assert out.startswith("Invalid argument:")


async def test_player_stats_accepts_a_name_not_just_an_id(
    sleeper_api: respx.MockRouter,
) -> None:
    route = sleeper_api.get(f"{DEFAULT_BASE_URL}/stats/nfl/player/4046").mock(
        return_value=httpx.Response(200, json={"1": {"pts_ppr": 21.4, "gp": 1}})
    )
    out = await call("get_player_stats", player="mahomes")
    assert route.call_count == 1
    assert "Patrick Mahomes (KC - QB)" in out
    assert "Week 1: gp=1, pts_ppr=21.4" in out


async def test_player_stats_for_an_unmatchable_name() -> None:
    out = await call("get_player_stats", player="zzzz nobody")
    assert "No player matched" in out


async def test_transactions(sleeper_api: respx.MockRouter) -> None:
    sleeper_api.get(f"{DEFAULT_BASE_URL}/league/{LEAGUE_ID}/transactions/5").mock(
        return_value=httpx.Response(
            200,
            json=[{"type": "trade", "status": "complete", "roster_ids": [1, 2],
                   "adds": {"5849": 1}, "drops": {"4046": 2}}],
        )
    )
    out = await call("get_transactions", league_id=LEAGUE_ID)
    assert "trade (complete) - Team Ravens (borja), sourknives" in out
    assert "Added: Kyler Murray (ARI - QB) -> Team Ravens (borja)" in out


async def test_playoff_bracket_resolves_placeholders(
    sleeper_api: respx.MockRouter,
) -> None:
    sleeper_api.get(f"{DEFAULT_BASE_URL}/league/{LEAGUE_ID}/winners_bracket").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"r": 1, "m": 1, "t1": 1, "t2": 2, "w": 1, "l": 2},
                {"r": 2, "m": 2, "t1": {"w": 1}, "t2": None},
            ],
        )
    )
    out = await call("get_playoff_bracket", league_id=LEAGUE_ID)
    assert "Team Ravens (borja) vs sourknives -> Team Ravens (borja)" in out
    assert "winner of match 1 vs TBD -> TBD" in out


async def test_player_catalog_is_downloaded_once_across_tools(
    sleeper_api: respx.MockRouter,
) -> None:
    """The catalog is multi-megabyte; three tool calls must not mean three pulls."""
    catalog = sleeper_api.get(f"{DEFAULT_BASE_URL}/players/nfl")
    await call("get_rosters", league_id=LEAGUE_ID)
    await call("search_players", query="mahomes")
    await call("get_matchups", league_id=LEAGUE_ID)
    assert catalog.call_count == 1
