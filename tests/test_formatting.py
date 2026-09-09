"""Rendering: the layer that decides what the model actually reads."""

from __future__ import annotations

from sleeper_mcp import formatting
from sleeper_mcp.players import PlayerIndex

from .conftest import LEAGUE, MATCHUPS, ROSTERS, USERS


def names() -> dict[int, str]:
    return formatting.team_names(USERS, ROSTERS)


def test_points_recombines_split_fields() -> None:
    assert formatting.points({"fpts": 412, "fpts_decimal": 55}, "fpts", "fpts_decimal") == 412.55
    assert formatting.points({}, "fpts", "fpts_decimal") == 0.0
    assert formatting.points({"fpts": "x"}, "fpts", "fpts_decimal") == 0.0


def test_team_names_prefer_team_name_then_handle() -> None:
    mapping = names()
    assert mapping[1] == "Team Ravens (borja)"
    assert mapping[2] == "sourknives", "no team_name set, so fall back to the handle"


def test_orphaned_roster_is_labelled_not_dropped() -> None:
    mapping = formatting.team_names(USERS, [{"roster_id": 9, "owner_id": None}])
    assert mapping[9] == "Unowned team"


def test_league_summary_reports_scoring_and_playoffs() -> None:
    out = formatting.format_league(LEAGUE)
    assert "Dynasty Warriors" in out
    assert "full PPR" in out
    assert "Playoffs start week 15" in out
    assert "Previous season league ID: 888" in out


def test_standings_sort_by_wins_then_points() -> None:
    out = formatting.format_standings(ROSTERS, names())
    rows = [line for line in out.splitlines() if line.startswith("| 1 ")]
    assert "Team Ravens (borja)" in rows[0]
    assert "412.55" in rows[0]


def test_empty_league_standings() -> None:
    assert formatting.format_standings([], {}) == "This league has no rosters."


def test_roster_splits_starters_from_bench(index: PlayerIndex) -> None:
    out = formatting.format_roster(ROSTERS[0], index, "Team Ravens (borja)")
    starters, bench = out.split("Bench:")
    assert "Patrick Mahomes (KC - QB)" in starters
    assert "Patrick Ricard" in bench
    assert "Patrick Ricard" not in starters


def test_matchups_pair_and_pick_a_leader(index: PlayerIndex) -> None:
    out = formatting.format_matchups(MATCHUPS, index, names(), 5)
    assert "Team Ravens (borja): 120.5" in out
    assert "sourknives: 98.25" in out
    assert "Team Ravens (borja) leads by 22.25" in out


def test_matchup_detail_lists_starter_scores(index: PlayerIndex) -> None:
    out = formatting.format_matchups(MATCHUPS, index, names(), 5, detail=True)
    assert "Patrick Mahomes (KC - QB): 25.5" in out
    assert "San Francisco 49ers (SF - DEF): 9.0" in out


def test_bye_week_roster_is_reported_not_swallowed(index: PlayerIndex) -> None:
    out = formatting.format_matchups(
        [{"roster_id": 1, "matchup_id": None, "points": 0}], index, names(), 5
    )
    assert "Bye / unscheduled: Team Ravens (borja)" in out


def test_empty_week(index: PlayerIndex) -> None:
    assert formatting.format_matchups([], index, {}, 9) == "No matchups are posted for week 9."


def test_draft_board_flags_keepers_and_attributes_teams(index: PlayerIndex) -> None:
    draft = {"draft_id": "d1", "season": "2025", "type": "snake", "status": "complete",
             "settings": {"rounds": 1, "teams": 2}}
    picks = [
        {"round": 1, "pick_no": 1, "player_id": "4046", "roster_id": 1, "is_keeper": True},
        {"round": 1, "pick_no": 2, "player_id": "5849", "roster_id": 2},
    ]
    out = formatting.format_draft_picks(draft, picks, index, names())
    assert "1. Patrick Mahomes (KC - QB) -> Team Ravens (borja) [keeper]" in out
    assert "2. Kyler Murray (ARI - QB) -> sourknives" in out


def test_draft_falls_back_to_pick_metadata_for_unknown_players(index: PlayerIndex) -> None:
    draft = {"draft_id": "d1", "season": "2025", "settings": {}}
    picks = [{"round": 1, "pick_no": 1, "player_id": "0000", "roster_id": 1,
              "metadata": {"first_name": "Rookie", "last_name": "Unknown"}}]
    out = formatting.format_draft_picks(draft, picks, index, names())
    assert "Rookie Unknown" in out


def test_draft_round_limit(index: PlayerIndex) -> None:
    draft = {"draft_id": "d1", "season": "2025", "settings": {}}
    picks = [
        {"round": 1, "pick_no": 1, "player_id": "4046", "roster_id": 1},
        {"round": 2, "pick_no": 3, "player_id": "5849", "roster_id": 1},
    ]
    out = formatting.format_draft_picks(draft, picks, index, names(), rounds=1)
    assert "Round 1" in out and "Round 2" not in out


def test_empty_draft(index: PlayerIndex) -> None:
    out = formatting.format_draft_picks({"draft_id": "d1", "settings": {}}, [], index, {})
    assert "No picks have been made yet." in out


def test_transactions_render_adds_drops_and_bids(index: PlayerIndex) -> None:
    txns = [{
        "type": "waiver", "status": "complete", "roster_ids": [1],
        "adds": {"5849": 1}, "drops": {"1234": 1},
        "settings": {"waiver_bid": 17},
    }]
    out = formatting.format_transactions(txns, index, names(), 5)
    assert "Added: Kyler Murray (ARI - QB) -> Team Ravens (borja)" in out
    assert "Dropped: Patrick Ricard (BAL - RB) -> Team Ravens (borja)" in out
    assert "Waiver bid: $17" in out


def test_no_transactions(index: PlayerIndex) -> None:
    assert formatting.format_transactions([], index, {}, 3) == (
        "No transactions were recorded in week 3."
    )


def test_player_table(index: PlayerIndex) -> None:
    out = formatting.format_players(index.search("patrick"))
    assert "| Patrick Mahomes | QB | KC |" in out
    assert formatting.format_players([]) == "No players matched."
