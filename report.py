"""Genera data/report.json con el estado de la liga Sleeper. Sin dependencias ni API keys."""
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LEAGUE_ID = "1394777298468618240"
MY_USER_ID = "1394790260143067136"
POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}
API = "https://api.sleeper.app/v1"


def get(path):
    req = urllib.request.Request(f"{API}{path}", headers={"User-Agent": "sleeper-fantasy-report"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def slim(pid, p):
    name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    return {
        "player_id": pid,
        "name": name,
        "position": p.get("position"),
        "team": p.get("team"),
        "injury_status": p.get("injury_status"),
    }


def main():
    # 1. Jugadores relevantes
    players = {
        pid: slim(pid, p)
        for pid, p in get("/players/nfl").items()
        if p.get("position") in POSITIONS and (p.get("active") or p.get("position") == "DEF")
    }

    def lookup(pid):
        return players.get(pid, {"player_id": pid, "name": None, "position": None, "team": None, "injury_status": None})

    # 2. Estado, rosters, usuarios, matchup
    state = get("/state/nfl")
    week = state.get("week") or state.get("display_week") or 1
    rosters = get(f"/league/{LEAGUE_ID}/rosters")
    users = {u["user_id"]: u for u in get(f"/league/{LEAGUE_ID}/users")}
    matchups = get(f"/league/{LEAGUE_ID}/matchups/{week}") or []

    def team_name(owner_id):
        u = users.get(owner_id) or {}
        return (u.get("metadata") or {}).get("team_name") or u.get("display_name")

    def roster_view(r):
        return {
            "roster_id": r["roster_id"],
            "owner_id": r.get("owner_id"),
            "team_name": team_name(r.get("owner_id")),
            "record": f"{r['settings'].get('wins', 0)}-{r['settings'].get('losses', 0)}-{r['settings'].get('ties', 0)}",
            "starters": [lookup(pid) for pid in (r.get("starters") or []) if pid != "0"],
            "players": [lookup(pid) for pid in (r.get("players") or [])],
            "injured_reserve": [lookup(pid) for pid in (r.get("reserve") or [])],
        }

    by_roster = {r["roster_id"]: r for r in rosters}
    mine = next(r for r in rosters if r.get("owner_id") == MY_USER_ID)

    # Rival: mismo matchup_id que mi roster
    my_m = next((m for m in matchups if m["roster_id"] == mine["roster_id"]), None)
    opp_m = next(
        (m for m in matchups if my_m and m.get("matchup_id") == my_m.get("matchup_id") and m["roster_id"] != mine["roster_id"]),
        None,
    )
    opponent = roster_view(by_roster[opp_m["roster_id"]]) if opp_m else None

    # 3. Agentes libres
    rostered = {pid for r in rosters for pid in (r.get("players") or []) + (r.get("reserve") or []) + (r.get("taxi") or [])}
    free_agents = {pid for pid in players if pid not in rostered}

    # 4. Trending adds disponibles
    trending = get("/players/nfl/trending/add?lookback_hours=48&limit=100")
    trending_fa = [{**players[t["player_id"]], "adds_48h": t["count"]} for t in trending if t["player_id"] in free_agents][:25]

    # 5. Transacciones de la semana
    roster_names = {r["roster_id"]: team_name(r.get("owner_id")) for r in rosters}
    transactions = []
    for t in get(f"/league/{LEAGUE_ID}/transactions/{week}") or []:
        transactions.append({
            "type": t.get("type"),
            "status": t.get("status"),
            "created": datetime.fromtimestamp(t["created"] / 1000, timezone.utc).isoformat() if t.get("created") else None,
            "teams": [roster_names.get(rid) for rid in t.get("roster_ids") or []],
            "adds": [{**lookup(pid), "to": roster_names.get(rid)} for pid, rid in (t.get("adds") or {}).items()],
            "drops": [{**lookup(pid), "from": roster_names.get(rid)} for pid, rid in (t.get("drops") or {}).items()],
            "waiver_bid": (t.get("settings") or {}).get("waiver_bid"),
        })

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "league_id": LEAGUE_ID,
        "season": state.get("season"),
        "week": week,
        "my_team": {**roster_view(mine), "points_this_week": my_m.get("points") if my_m else None},
        "opponent": {**opponent, "points_this_week": opp_m.get("points")} if opponent else None,
        "free_agents_total": len(free_agents),
        "trending_free_agents": trending_fa,
        "transactions": transactions,
    }

    out = Path(__file__).parent / "data" / "report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"week {week}: {len(trending_fa)} trending FAs, {len(transactions)} transactions -> {out}")


if __name__ == "__main__":
    main()
