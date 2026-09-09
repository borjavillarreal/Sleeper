# League analysis: what these tools support

An honest capability map for the 13 tools in
[sourknives/sleeper-mcp-server](https://github.com/sourknives/sleeper-mcp-server)
@ `af7fd50`, written after reading the server source rather than the README.

The headline: **every analysis below is backward-looking.** The server exposes no
projections, so it can tell you who has been good and who has been lucky — it
cannot tell you who will score next Sunday. Treat its output as evidence for your
own judgment, not as a start/sit oracle.

## Before any of this runs

Two preconditions, in order:

1. **Egress.** `api.sleeper.app` and `api.sleeper.com` must be on the session's
   network allowlist. See "Network access" in the [README](../README.md). Cloud
   sessions at the default **Trusted** level cannot reach either, and every tool
   returns `403 Forbidden` from the proxy — a policy denial, not a Sleeper error
   and not an authentication problem.
2. **A username.** Pass it to `get_user_leagues` to resolve the league ID that
   every other tool takes. No password, no login, no token — the server holds no
   credential-handling code at all.

## Supported end-to-end

### 1. Bench-points audit — the highest-value one

`get_matchups(league_id, week)` returns `starters`, `starters_points` and
`players_points` (every rostered player's actual score) for each team. Loop weeks
1..current and you get, per manager, the points they left on the bench and how
often the optimal lineup differed from the one they started.

This is the only analysis here that maps directly to a repeatable behavioral fix.
Ask for it first.

```
For league <id>, pull weeks 1 through <current week> and tell me,
per manager, total points left on the bench and the three worst
individual start/sit calls of the season.
```

### 2. Luck audit / expected record

`get_league_rosters` carries `settings.wins`, `losses`, `fpts`, `fpts_against`.
Combined with weekly scores from `get_matchups`, you can compute each team's
all-play win percentage and compare it to its actual record. The gap is schedule
luck.

Useful because it tells you whether a 3-6 team should be selling or holding.

### 3. Draft value audit

`get_league_draft` gives every pick with round, slot, keeper flag and drafting
team. `get_league_rosters_with_draft_info` attributes each currently-rostered
player to its draft position or marks it `free_agent`. Cross that with
season-to-date points from `get_matchups` and you get hit rate by round, best and
worst picks, and which managers add value after the draft versus at it.

### 4. Positional strength and trade fit

Rosters plus per-player weekly points give a per-position points-per-start table
for every team. Surplus at one position crossed with a rival's deficit at another
is a trade shortlist.

Caveat: this ranks by *past* production, so it systematically overvalues players
who have already had their best weeks. It finds conversations to start, not deals
to accept.

### 5. Playoff-path sketch

Future weeks' `matchup_id` pairings are available from `get_matchups` before those
games are played, so remaining schedules are known. With each team's scoring mean
and variance from completed weeks, a Monte Carlo gives rough playoff odds and
identifies which remaining games actually swing the seed.

## Not supported — and what it costs you

| Gap | Consequence |
|---|---|
| **No projections tool.** `sleeper_client.py` implements `get_projections` (weekly) and `get_season_projections` (season, with ADP), but `server.py` registers neither as an MCP tool. | No forward-looking start/sit, no "is this waiver add better than my WR3". The single biggest limitation. |
| **No free-agent list.** No tool returns "players on no roster in this league". `search_players` is a name query only. | "Who should I pick up?" can't be answered directly. Workaround: `get_trending_players` for league-agnostic add counts, then check each name against `get_league_rosters` by hand. Slow and incomplete. |
| **No transactions endpoint.** `/league/{id}/transactions/{week}` is not implemented anywhere in the codebase. | No waiver history, no remaining FAAB budgets, no trade history. Blocks "how much should I bid" and any read on rival behavior. |
| **No injury or bye data.** The `Player` model has `injury_status` but `search_players` strips it from its output, and there is no bye-week field at all. | Lineup advice can't account for byes or questionable tags. You must check the Sleeper app for those. |
| **Playoff brackets not exposed.** The client has `get_winners_bracket` / `get_losers_bracket`; the server registers neither. | Postseason analysis stops at the regular season. |
| **`get_player_stats` is per-player.** It takes a single `player_id`. | Whole-league stat sweeps go through `get_matchups` week by week instead, which is many calls. The server caches (1h for completed matchups), so re-runs are cheap; the first pass is not. |

## Closing the projections gap

Cheapest real improvement: the client work is already done, so exposing
projections is roughly a 20-line addition to `sleeper_mcp_server/server.py` —
two `@mcp.tool` wrappers around `client.get_projections` and
`client.get_season_projections`. That converts every "who has been good"
answer above into a "who will be good" answer.

Second: a `get_available_players(league_id)` tool that diffs the `/players/nfl`
catalog against all rostered player IDs, then ranks the remainder by projection.
That is the actual waiver-wire recommendation engine, and it needs the first one
to be worth anything.

Both belong upstream as pull requests to `sourknives/sleeper-mcp-server` rather
than as a fork here.
