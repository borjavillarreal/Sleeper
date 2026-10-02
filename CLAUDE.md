# Weekly league playbook

Diego (Sleeper username `vvaallddeess`) plays in **Epstein's Minor League**
(league ID `1394777298468618240`, roster ID `5`). Whenever Diego asks what to
do with their team — "what should I do", "who do I pick up", "who do I start" —
run the full weekly review below, not just the narrow question. They want every
option checked, every week.

## League settings

- 10 teams, full PPR (`rec: 1`), 6 playoff teams.
- Starters: QB, RB, RB, WR, WR, TE, FLEX, FLEX, K, DEF. 5 bench, 1 IR slot.
- Waivers clear after 1 day; FAAB is not used (rolling waiver priority).
- A healthy player left in the IR slot blocks adds/drops — check it first.

## Weekly review

1. **Week and timing** — `get_nfl_state`; look up this week's schedule (web)
   for Thursday/Saturday/Monday games and byes. Players lock at their own
   kickoff, so say which of Diego's players play early.
2. **Diego's roster** — `get_league_rosters`. For every player: season PPR per
   game (`get_player_stats`), snap share, and **current injury news (web
   search, every week)**. Flag IR-slot problems and anyone out or on bye.
3. **Matchup** — `get_matchups` for the week: opponent's roster, their likely
   points, and who on their side is injured.
4. **Free agents** — the MCP has no free-agent list. Build it by taking
   `get_trending_players` (add) plus targeted `search_players` lookups
   (backups of injured starters, players other teams just dropped) and
   excluding everyone on any roster. Check the news on why each is trending.
5. **Defense and kicker streaming** — compare Diego's DEF and K against
   available ones by this week's opponent (weak offenses, turnover-prone
   QBs, injured QBs, home/away, weather). Recommend a swap when the edge is real.
6. **Lineup** — best possible starters, both FLEX spots, and what to do with
   game-time decisions (check inactives ~90 min before kickoff).
7. **Trades** — Diego is WR-heavy and RB-thin; name teams with surplus at
   their weak positions and what to offer.

## Answer format

- Start with "Diego, " and lead with the most important move.
- Tag claims `[Certain]`, `[Likely]` or `[Guessing]`; cite news sources.
- Give a prioritized action list: each add with its matching drop, lineup
  changes, DEF/K swaps, and what to re-check before kickoff.
- When a player name is ambiguous (e.g. "Davis"), resolve it before advising.
- Hold a recommendation under pushback unless new information arrives.
