"""Turn Sleeper's JSON into text a language model can actually reason over.

Everything here is pure: dicts in, string out.  No I/O, no client, no cache --
which is why these are the easiest parts of the server to test.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .players import PlayerIndex

_UNOWNED = "Unowned team"


def points(settings: Mapping[str, Any], whole: str, decimal: str) -> float:
    """Recombine Sleeper's split point fields (``fpts`` + ``fpts_decimal``)."""
    base = settings.get(whole) or 0
    frac = settings.get(decimal) or 0
    try:
        return round(float(base) + float(frac) / 100.0, 2)
    except (TypeError, ValueError):
        return 0.0


def team_names(
    users: Sequence[Mapping[str, Any]], rosters: Sequence[Mapping[str, Any]]
) -> dict[int, str]:
    """Map ``roster_id`` to the most human name available for that team.

    Sleeper keeps the manager's handle on the user and the (optional) team name
    in ``user.metadata.team_name``; the roster only carries ``owner_id``.  Both
    halves are needed to say "Team Ravens (borja)" instead of "roster 4".
    """
    by_user: dict[str, str] = {}
    for user in users or []:
        user_id = str(user.get("user_id", ""))
        handle = user.get("display_name") or "unknown manager"
        metadata = user.get("metadata") or {}
        team = metadata.get("team_name")
        by_user[user_id] = f"{team} ({handle})" if team else handle

    names: dict[int, str] = {}
    for roster in rosters or []:
        roster_id = roster.get("roster_id")
        if roster_id is None:
            continue
        owner = str(roster.get("owner_id") or "")
        names[int(roster_id)] = by_user.get(owner, _UNOWNED)
    return names


def format_league(league: Mapping[str, Any]) -> str:
    settings = league.get("settings") or {}
    scoring = league.get("scoring_settings") or {}
    ppr = scoring.get("rec")
    scoring_label = (
        "standard" if not ppr else "full PPR" if float(ppr) >= 1 else f"{ppr} PPR"
    )
    lines = [
        f"# {league.get('name', 'Unnamed league')}",
        f"League ID: {league.get('league_id')}",
        f"Season: {league.get('season')} ({league.get('status', 'unknown status')})",
        f"Sport: {league.get('sport', 'nfl')}  |  Teams: {league.get('total_rosters')}",
        f"Scoring: {scoring_label}",
        f"Roster slots: {', '.join(league.get('roster_positions') or []) or 'unknown'}",
    ]
    if settings.get("playoff_week_start"):
        lines.append(f"Playoffs start week {settings['playoff_week_start']}")
    if league.get("previous_league_id"):
        lines.append(f"Previous season league ID: {league['previous_league_id']}")
    return "\n".join(lines)


def format_standings(
    rosters: Sequence[Mapping[str, Any]], names: Mapping[int, str]
) -> str:
    rows = []
    for roster in rosters or []:
        settings = roster.get("settings") or {}
        roster_id = int(roster.get("roster_id", 0))
        wins = settings.get("wins", 0)
        losses = settings.get("losses", 0)
        ties = settings.get("ties", 0)
        record = f"{wins}-{losses}" + (f"-{ties}" if ties else "")
        rows.append(
            (
                wins,
                points(settings, "fpts", "fpts_decimal"),
                names.get(roster_id, f"Roster {roster_id}"),
                record,
                points(settings, "fpts", "fpts_decimal"),
                points(settings, "fpts_against", "fpts_against_decimal"),
                roster_id,
            )
        )
    rows.sort(key=lambda row: (-row[0], -row[1]))

    lines = ["| # | Team | Record | PF | PA | Roster |", "|---|---|---|---|---|---|"]
    for rank, (_, _, name, record, pf, pa, roster_id) in enumerate(rows, start=1):
        lines.append(f"| {rank} | {name} | {record} | {pf} | {pa} | {roster_id} |")
    return "\n".join(lines) if rows else "This league has no rosters."


def format_roster(
    roster: Mapping[str, Any],
    index: PlayerIndex,
    name: str,
) -> str:
    settings = roster.get("settings") or {}
    starters = [pid for pid in (roster.get("starters") or []) if pid and pid != "0"]
    all_players = [pid for pid in (roster.get("players") or []) if pid]
    bench = [pid for pid in all_players if pid not in set(starters)]

    wins = settings.get("wins", 0)
    losses = settings.get("losses", 0)
    ties = settings.get("ties", 0)
    record = f"{wins}-{losses}" + (f"-{ties}" if ties else "")

    lines = [
        f"## {name} (roster {roster.get('roster_id')})",
        f"Record: {record}  |  "
        f"PF: {points(settings, 'fpts', 'fpts_decimal')}  |  "
        f"PA: {points(settings, 'fpts_against', 'fpts_against_decimal')}",
        "",
        "Starters:",
    ]
    lines.extend(f"  - {index.get(pid).detail() if index.get(pid) else pid}" for pid in starters)
    if not starters:
        lines.append("  (none set)")
    lines.append("")
    lines.append("Bench:")
    lines.extend(f"  - {index.get(pid).detail() if index.get(pid) else pid}" for pid in bench)
    if not bench:
        lines.append("  (empty)")
    return "\n".join(lines)


def format_matchups(
    matchups: Sequence[Mapping[str, Any]],
    index: PlayerIndex,
    names: Mapping[int, str],
    week: int,
    *,
    detail: bool = False,
) -> str:
    """Pair rosters by ``matchup_id`` and render each head-to-head.

    A roster with a null ``matchup_id`` is on a bye (or the week has not been
    scheduled), so it is reported separately rather than silently dropped.
    """
    paired: dict[Any, list[Mapping[str, Any]]] = {}
    byes: list[Mapping[str, Any]] = []
    for entry in matchups or []:
        matchup_id = entry.get("matchup_id")
        if matchup_id is None:
            byes.append(entry)
        else:
            paired.setdefault(matchup_id, []).append(entry)

    if not paired and not byes:
        return f"No matchups are posted for week {week}."

    lines = [f"# Week {week} matchups"]
    for matchup_id in sorted(paired, key=lambda k: (k is None, k)):
        sides = paired[matchup_id]
        lines.append("")
        lines.append(f"## Matchup {matchup_id}")
        for side in sides:
            roster_id = int(side.get("roster_id", 0))
            name = names.get(roster_id, f"Roster {roster_id}")
            lines.append(f"  {name}: {_score(side)}")
        if len(sides) == 2:
            lines.append(f"  -> {_verdict(sides, names)}")
        if detail:
            for side in sides:
                lines.append("")
                lines.extend(_starter_lines(side, index, names))

    for side in byes:
        roster_id = int(side.get("roster_id", 0))
        lines.append("")
        lines.append(
            f"Bye / unscheduled: {names.get(roster_id, f'Roster {roster_id}')} "
            f"({_score(side)})"
        )
    return "\n".join(lines)


def _score(side: Mapping[str, Any]) -> float:
    try:
        return round(float(side.get("points") or 0.0), 2)
    except (TypeError, ValueError):
        return 0.0


def _verdict(sides: Sequence[Mapping[str, Any]], names: Mapping[int, str]) -> str:
    left, right = sides
    left_score, right_score = _score(left), _score(right)
    if left_score == right_score:
        return f"tied at {left_score}"
    winner, loser = (left, right) if left_score > right_score else (right, left)
    margin = round(abs(left_score - right_score), 2)
    name = names.get(int(winner.get("roster_id", 0)), "unknown team")
    return f"{name} leads by {margin}"


def _starter_lines(
    side: Mapping[str, Any], index: PlayerIndex, names: Mapping[int, str]
) -> list[str]:
    roster_id = int(side.get("roster_id", 0))
    per_player = side.get("players_points") or {}
    lines = [f"  {names.get(roster_id, f'Roster {roster_id}')} starters:"]
    starters = [pid for pid in (side.get("starters") or []) if pid and pid != "0"]
    if not starters:
        lines.append("    (no starters set)")
        return lines
    for pid in starters:
        player = index.get(pid)
        scored = per_player.get(pid)
        label = player.detail() if player else f"player {pid}"
        lines.append(f"    - {label}: {round(float(scored or 0.0), 2)}")
    return lines


def format_draft_picks(
    draft: Mapping[str, Any],
    picks: Sequence[Mapping[str, Any]],
    index: PlayerIndex,
    names: Mapping[int, str],
    *,
    rounds: int | None = None,
) -> str:
    """Render a draft board, round by round, flagging keepers.

    ``draft_order`` maps user ids to slots, but picks already carry
    ``roster_id``, so team attribution comes from the roster map and falls back
    to the pick's own ``picked_by`` when a roster is missing.
    """
    settings = draft.get("settings") or {}
    header = [
        f"# {draft.get('season')} {draft.get('type', 'draft')} "
        f"({draft.get('status', 'unknown')})",
        f"Draft ID: {draft.get('draft_id')}  |  "
        f"Rounds: {settings.get('rounds', '?')}  |  "
        f"Teams: {settings.get('teams', '?')}",
    ]
    if not picks:
        header.append("")
        header.append("No picks have been made yet.")
        return "\n".join(header)

    by_round: dict[int, list[Mapping[str, Any]]] = {}
    for pick in picks:
        by_round.setdefault(int(pick.get("round") or 0), []).append(pick)

    lines = header
    for round_no in sorted(by_round):
        if rounds is not None and round_no > rounds:
            break
        lines.append("")
        lines.append(f"## Round {round_no}")
        for pick in sorted(by_round[round_no], key=lambda p: p.get("pick_no") or 0):
            lines.append(f"  {_pick_line(pick, index, names)}")
    return "\n".join(lines)


def _pick_line(
    pick: Mapping[str, Any], index: PlayerIndex, names: Mapping[int, str]
) -> str:
    pick_no = pick.get("pick_no")
    player_id = str(pick.get("player_id") or "")
    player = index.get(player_id)
    if player is not None:
        label = player.label()
    else:
        metadata = pick.get("metadata") or {}
        fallback = " ".join(
            part
            for part in (metadata.get("first_name"), metadata.get("last_name"))
            if part
        ).strip()
        label = fallback or f"player {player_id or '?'}"
    roster_id = pick.get("roster_id")
    team = names.get(int(roster_id)) if roster_id is not None else None
    team = team or f"user {pick.get('picked_by') or 'unknown'}"
    keeper = " [keeper]" if pick.get("is_keeper") else ""
    return f"{pick_no}. {label} -> {team}{keeper}"


def format_transactions(
    transactions: Sequence[Mapping[str, Any]],
    index: PlayerIndex,
    names: Mapping[int, str],
    week: int,
) -> str:
    if not transactions:
        return f"No transactions were recorded in week {week}."

    lines = [f"# Week {week} transactions ({len(transactions)})"]
    for txn in transactions:
        kind = txn.get("type", "unknown")
        status = txn.get("status", "unknown")
        involved = ", ".join(
            names.get(int(rid), f"Roster {rid}")
            for rid in (txn.get("roster_ids") or [])
        )
        lines.append("")
        lines.append(f"## {kind} ({status}) - {involved or 'no rosters listed'}")
        for verb, moves in (("Added", txn.get("adds")), ("Dropped", txn.get("drops"))):
            for player_id, roster_id in (moves or {}).items():
                team = names.get(int(roster_id), f"Roster {roster_id}")
                lines.append(f"  {verb}: {index.label(player_id)} -> {team}")
        bid = (txn.get("settings") or {}).get("waiver_bid")
        if bid is not None:
            lines.append(f"  Waiver bid: ${bid}")
        for draft_pick in txn.get("draft_picks") or []:
            lines.append(
                f"  Pick: {draft_pick.get('season')} round "
                f"{draft_pick.get('round')} "
                f"({names.get(int(draft_pick.get('owner_id') or 0), 'unknown')})"
            )
    return "\n".join(lines)


def format_players(players: Iterable[Any]) -> str:
    rows = list(players)
    if not rows:
        return "No players matched."
    lines = ["| Player | Pos | Team | Status | Age | Exp | ID |", "|---|---|---|---|---|---|---|"]
    for player in rows:
        lines.append(
            f"| {player.name} | {player.position or '-'} | {player.team or 'FA'} "
            f"| {player.injury_status or player.status or '-'} "
            f"| {player.age if player.age is not None else '-'} "
            f"| {player.years_exp if player.years_exp is not None else '-'} "
            f"| {player.player_id} |"
        )
    return "\n".join(lines)
