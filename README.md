# Sleeper

Fantasy football league analysis, backed by the
[sourknives/sleeper-mcp-server](https://github.com/sourknives/sleeper-mcp-server)
MCP server.

## Setup

`.mcp.json` at the repo root registers the server for project-scoped Claude Code
sessions. It runs the server straight from GitHub with `uvx`, pinned to a commit
— nothing to clone or `pip install`:

```json
{
  "mcpServers": {
    "sleeper": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/sourknives/sleeper-mcp-server@af7fd50", "sleeper-mcp-server"]
    }
  }
}
```

Requirements: [`uv`](https://docs.astral.sh/uv/) on `PATH` and Python 3.10+.

Start a Claude Code session in this directory and approve the `sleeper` server
when prompted (`/mcp` lists its status). MCP servers are loaded at session
start, so a session that was already running needs a restart to pick it up.

The server is read-only and unauthenticated — it wraps Sleeper's public API, so
there is no token to configure. It also means it can only see public league
data.

### No credentials required

The server sends no authentication of any kind — there is not a single
token, cookie, header or password handled anywhere in its source. Sleeper's
read API is public: league, roster, matchup and draft data for a league are
served to anonymous callers. A Sleeper username is all that is ever needed,
and it is used only as a lookup key to resolve a user ID.

Never hand a Sleeper account password to this tool, to Claude, or to anything
else. It would not be used, and it cannot unlock any data the public API does
not already serve.

### Network access

The server talks to `api.sleeper.app` (league data) and `api.sleeper.com`
(projections). Both must be reachable, or every tool call fails at connect
time with `403 Forbidden` from the egress proxy.

Claude Code cloud sessions are deny-by-default: the **Trusted** network level
allows package registries, GitHub and cloud SDKs, and nothing else — Sleeper is
not on that list. To use this server from a cloud session, open the environment
settings at [claude.ai/code](https://claude.ai/code) (**Add cloud environment**,
or the settings icon on an existing one), set **Network access** to **Custom**,
and list:

```text
api.sleeper.app
api.sleeper.com
```

Check **Also include default list of common package managers** so `uvx` can
still install the server from PyPI. The change applies to newly started
sessions, not to one already running. **Full** works too, and is a much broader
grant for no extra benefit here.

Running the server from a local terminal session sidesteps this entirely.

## What it can answer

Thirteen read-only tools: `get_nfl_state`, `get_user_leagues`, `get_league_info`,
`get_league_rosters`, `get_league_rosters_with_draft_info`, `get_league_users`,
`get_roster_user_mapping`, `get_league_draft`, `search_players`,
`get_trending_players`, `get_player_stats`, `get_matchups`, `get_matchup_scores`.

Start with your username — everything else keys off the league ID it returns:

```
Show me all leagues for username "<your-sleeper-username>"
```

See [docs/league-analysis.md](docs/league-analysis.md) for the analyses these
tools actually support, the ones they don't, and why.
