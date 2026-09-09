# sleeper-mcp

An MCP server that gives Claude read-only access to the [Sleeper](https://sleeper.com)
fantasy football API — leagues, rosters, matchups, drafts, transactions and players.

## Why it resolves names

Sleeper answers in identifiers. A roster comes back as
`{"starters": ["4046", "6794"], "points": 120.5}`, which a model cannot say anything
useful about. Every tool here joins those ids against the player catalog and returns
readable text instead:

```
## Team Ravens (borja) (roster 1)
Record: 3-1  |  PF: 412.55  |  PA: 380.1

Starters:
  - Patrick Mahomes (KC - QB)
  - Amon-Ra St. Brown (DET - WR) [Questionable]
```

## Install

Requires Python 3.10+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Configure your MCP client

Point it at the module. No API key — Sleeper's read endpoints need no authentication.

```json
{
  "mcpServers": {
    "sleeper": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["-m", "sleeper_mcp"]
    }
  }
}
```

Every tool is read-only, so auto-approving all of them is safe:

```json
"autoApprove": [
  "get_nfl_state", "get_user", "get_user_leagues", "get_league", "get_rosters",
  "get_matchups", "get_draft", "search_players", "get_trending_players",
  "get_player_stats", "get_transactions", "get_playoff_bracket"
]
```

## Tools

| Tool | What it answers |
|---|---|
| `get_nfl_state` | What season and week is it right now? |
| `get_user` | Resolve a username to the user id other tools need |
| `get_user_leagues` | Which leagues is someone in this season? |
| `get_league` | League settings and current standings |
| `get_rosters` | Full rosters by name, starters split from bench |
| `get_matchups` | Head-to-head scores for a week, optionally per-starter |
| `get_draft` | Draft board with team attribution and keeper flags |
| `search_players` | Find a player by partial name, filtered by position or team |
| `get_trending_players` | Who is being added and dropped league-wide |
| `get_player_stats` | Weekly stats for one player, by name or id |
| `get_transactions` | Trades, waivers and free-agent moves for a week |
| `get_playoff_bracket` | Winners or losers bracket, round by round |

`season` and `week` default to the live NFL state, so "who won last week" needs no
arguments beyond the league id.

Typical flow:

```
get_user_leagues("borja")        -> league_id
get_league(league_id)            -> settings + standings
get_matchups(league_id)          -> this week's scores
```

## Design notes

**Single-flight caching.** `/players/nfl` is a multi-megabyte document the API asks
you to fetch at most once a day. A plain cache is not enough: three tools missing at
the same instant would trigger three downloads. `AsyncTTLCache.get_or_fetch` holds a
per-key in-flight task so concurrent misses share one request, and the fetch survives
any single caller being cancelled.

**Per-endpoint TTLs.** Live scoring changes by the minute and league settings do not,
so matchups cache for 60s, rosters for 120s, league metadata for 10 minutes and the
player catalog for a day. All of them are constants at the top of `client.py`.

**Slimmed catalog.** The raw catalog carries ~50 fields across ~11k players. The index
keeps twelve, which is the difference between holding a few megabytes and a hundred.

**Errors come back as text.** A tool that raises hands the model a protocol error it
cannot act on. `Not found: Sleeper has no resource at /user/ghost` lets it correct the
username and retry.

**Rate limiting and retries.** A token bucket keeps the server under Sleeper's
guidance of 1000 calls per minute. 429s and 5xx are retried with exponential backoff
plus jitter, honouring `Retry-After` when present.

## Development

```bash
pytest          # 70 tests, no network required
ruff check .
```

Tests mock the Sleeper host with `respx`, so the suite is deterministic and offline.
`tests/conftest.py` holds the fixture payloads.

## Verification status

The test suite (70 tests) passes, `ruff` is clean, and the server completes a real MCP
stdio handshake advertising all 12 tools.

**Nothing here has been exercised against the live Sleeper API** — it was developed in
a sandbox where `api.sleeper.app` is unreachable. Endpoint paths and response shapes
come from Sleeper's public documentation. Before trusting it, run it against a real
league and check the tools whose response shapes are least documented:
`get_player_stats`, `get_transactions` and `get_playoff_bracket`.

## License

MIT
