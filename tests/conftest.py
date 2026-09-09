"""Shared fixtures: fake Sleeper payloads and a client wired to a mock transport."""

from __future__ import annotations

import pytest

from sleeper_mcp.players import PlayerIndex

RAW_PLAYERS = {
    "4046": {
        "full_name": "Patrick Mahomes",
        "position": "QB",
        "team": "KC",
        "status": "Active",
        "fantasy_positions": ["QB"],
        "number": 15,
        "age": 30,
        "years_exp": 9,
    },
    "6794": {
        "first_name": "Amon-Ra",
        "last_name": "St. Brown",
        "position": "WR",
        "team": "DET",
        "status": "Active",
        "injury_status": "Questionable",
        "fantasy_positions": ["WR"],
        "age": 26,
        "years_exp": 5,
    },
    "5849": {
        "full_name": "Kyler Murray",
        "position": "QB",
        "team": "ARI",
        "status": "Active",
        "fantasy_positions": ["QB"],
    },
    "1234": {
        "full_name": "Patrick Ricard",
        "position": "RB",
        "team": "BAL",
        "status": "Inactive",
        "fantasy_positions": ["RB", "TE"],
    },
    "SF": {
        "first_name": "San Francisco",
        "last_name": "49ers",
        "position": "DEF",
        "team": "SF",
        "status": "Active",
        "fantasy_positions": ["DEF"],
    },
}

LEAGUE = {
    "league_id": "999",
    "name": "Dynasty Warriors",
    "season": "2025",
    "status": "in_season",
    "sport": "nfl",
    "total_rosters": 2,
    "roster_positions": ["QB", "RB", "WR", "TE", "BN"],
    "scoring_settings": {"rec": 1.0},
    "settings": {"playoff_week_start": 15},
    "previous_league_id": "888",
}

USERS = [
    {"user_id": "u1", "display_name": "borja", "metadata": {"team_name": "Team Ravens"}},
    {"user_id": "u2", "display_name": "sourknives", "metadata": {}},
]

ROSTERS = [
    {
        "roster_id": 1,
        "owner_id": "u1",
        "starters": ["4046", "6794"],
        "players": ["4046", "6794", "1234"],
        "settings": {
            "wins": 3,
            "losses": 1,
            "ties": 0,
            "fpts": 412,
            "fpts_decimal": 55,
            "fpts_against": 380,
            "fpts_against_decimal": 10,
        },
    },
    {
        "roster_id": 2,
        "owner_id": "u2",
        "starters": ["5849", "SF"],
        "players": ["5849", "SF"],
        "settings": {
            "wins": 1,
            "losses": 3,
            "ties": 0,
            "fpts": 350,
            "fpts_decimal": 0,
            "fpts_against": 400,
            "fpts_against_decimal": 25,
        },
    },
]

MATCHUPS = [
    {
        "roster_id": 1,
        "matchup_id": 1,
        "points": 120.5,
        "starters": ["4046", "6794"],
        "players_points": {"4046": 25.5, "6794": 18.0},
    },
    {
        "roster_id": 2,
        "matchup_id": 1,
        "points": 98.25,
        "starters": ["5849", "SF"],
        "players_points": {"5849": 15.0, "SF": 9.0},
    },
]

NFL_STATE = {
    "season": "2025",
    "season_type": "regular",
    "week": 5,
    "display_week": 5,
    "leg": 5,
    "season_start_date": "2025-09-04",
}


@pytest.fixture
def index() -> PlayerIndex:
    return PlayerIndex.from_raw(RAW_PLAYERS)
