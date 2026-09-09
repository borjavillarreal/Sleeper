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

### Network

The server talks to `api.sleeper.app` and `api.sleeper.com`. Both must be
reachable. In a sandboxed or proxied environment they have to be on the egress
allowlist, or every tool call fails at connect time.

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
