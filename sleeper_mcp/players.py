"""Player catalog: slimming, lookup and search.

Rosters, matchups and draft picks all refer to players by opaque ids
(``"4046"``, or ``"SF"`` for a team defense).  Handing those to a language
model is useless, so every tool resolves them through :class:`PlayerIndex`.

The raw catalog carries ~50 fields per player across ~11k players.  We keep the
dozen that matter, which is the difference between holding a few megabytes in
memory and holding a hundred.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .cache import AsyncTTLCache
from .client import TTL_PLAYERS, SleeperClient

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_SPACE = re.compile(r"\s+")

#: Positions that can be started in a typical Sleeper league.
FANTASY_POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K", "DEF", "DL", "LB", "DB"})


def normalize(text: str) -> str:
    """Fold a name to a comparable form: no accents, no punctuation, one space.

    ``"D'Andre Swift"`` and ``"DAndre  Swift"`` both become ``"dandre swift"``.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _SPACE.sub(" ", _PUNCT.sub("", stripped.lower())).strip()


@dataclass(frozen=True, slots=True)
class Player:
    player_id: str
    name: str
    position: str | None
    team: str | None
    status: str | None
    injury_status: str | None
    number: int | None
    age: int | None
    years_exp: int | None
    fantasy_positions: tuple[str, ...]

    @property
    def is_active(self) -> bool:
        return self.status == "Active"

    def label(self) -> str:
        """A one-line identity string, e.g. ``Josh Allen (BUF - QB)``."""
        bits = " - ".join(b for b in (self.team or "FA", self.position) if b)
        return f"{self.name} ({bits})" if bits else self.name

    def detail(self) -> str:
        """``label()`` plus whatever injury or inactive note applies."""
        out = self.label()
        if self.injury_status:
            out += f" [{self.injury_status}]"
        elif self.status and self.status != "Active":
            out += f" [{self.status}]"
        return out


def _slim(player_id: str, raw: Mapping[str, Any]) -> Player:
    name = (
        raw.get("full_name")
        or " ".join(
            part for part in (raw.get("first_name"), raw.get("last_name")) if part
        ).strip()
        or player_id
    )
    positions = raw.get("fantasy_positions") or []
    return Player(
        player_id=player_id,
        name=name,
        position=raw.get("position"),
        team=raw.get("team"),
        status=raw.get("status"),
        injury_status=raw.get("injury_status"),
        number=_as_int(raw.get("number")),
        age=_as_int(raw.get("age")),
        years_exp=_as_int(raw.get("years_exp")),
        fantasy_positions=tuple(p for p in positions if isinstance(p, str)),
    )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class PlayerIndex:
    """A searchable, slimmed view of the Sleeper player catalog."""

    def __init__(self, players: Mapping[str, Player]) -> None:
        self._players = dict(players)
        self._by_normalized: dict[str, list[Player]] = {}
        self._by_last: dict[str, list[Player]] = {}
        for player in self._players.values():
            key = normalize(player.name)
            if key:
                self._by_normalized.setdefault(key, []).append(player)
            tokens = key.split()
            if tokens:
                self._by_last.setdefault(tokens[-1], []).append(player)

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> PlayerIndex:
        players = {
            pid: _slim(pid, record)
            for pid, record in raw.items()
            if isinstance(record, Mapping)
        }
        return cls(players)

    def __len__(self) -> int:
        return len(self._players)

    def get(self, player_id: str) -> Player | None:
        return self._players.get(str(player_id))

    def label(self, player_id: str) -> str:
        """Resolve an id for display, degrading to the bare id if unknown."""
        player = self.get(player_id)
        return player.label() if player else f"Unknown player {player_id}"

    def labels(self, player_ids: Iterable[str] | None) -> list[str]:
        return [self.label(pid) for pid in (player_ids or [])]

    def search(
        self,
        query: str,
        *,
        position: str | None = None,
        team: str | None = None,
        limit: int = 10,
        active_only: bool = False,
    ) -> list[Player]:
        """Rank players against ``query``, best match first.

        Scoring is deliberately simple and explainable: exact name beats
        prefix, prefix beats surname, surname beats "all query words appear
        somewhere".  Ties break toward players who are active and rostered on a
        real team, because that is nearly always who the question is about.
        """
        needle = normalize(query)
        if not needle:
            return []
        wanted_pos = position.upper() if position else None
        wanted_team = team.upper() if team else None
        tokens = needle.split()

        scored: list[tuple[int, int, Player]] = []
        for player in self._players.values():
            if wanted_pos and wanted_pos not in self._positions_of(player):
                continue
            if wanted_team and (player.team or "").upper() != wanted_team:
                continue
            if active_only and not player.is_active:
                continue
            score = self._score(player, needle, tokens)
            if score:
                scored.append((score, self._tiebreak(player), player))

        scored.sort(key=lambda item: (-item[0], -item[1], item[2].name))
        return [player for _, _, player in scored[:limit]]

    @staticmethod
    def _positions_of(player: Player) -> set[str]:
        positions = set(player.fantasy_positions)
        if player.position:
            positions.add(player.position)
        return positions

    @staticmethod
    def _score(player: Player, needle: str, tokens: list[str]) -> int:
        name = normalize(player.name)
        if name == needle:
            return 100
        if name.startswith(needle):
            return 80
        name_tokens = name.split()
        if name_tokens and name_tokens[-1] == needle:
            return 70
        if all(any(nt.startswith(tok) for nt in name_tokens) for tok in tokens):
            return 60

        # Hyphenated names collapse to one token ("Amon-Ra" -> "amonra"), so a
        # user typing "amon ra" fails every word-boundary rule above.  Compare
        # the space-free forms to catch it.
        squashed_name = name.replace(" ", "")
        squashed_needle = needle.replace(" ", "")
        if len(squashed_needle) >= 4:
            if squashed_name.startswith(squashed_needle):
                return 75
            if squashed_needle in squashed_name:
                return 50

        if needle in name:
            return 40
        return 0

    @staticmethod
    def _tiebreak(player: Player) -> int:
        """Prefer active, rostered, fantasy-relevant players on equal scores."""
        return (
            (2 if player.is_active else 0)
            + (1 if player.team else 0)
            + (1 if set(player.fantasy_positions) & FANTASY_POSITIONS else 0)
        )


class PlayerIndexLoader:
    """Builds and caches the :class:`PlayerIndex` for a client.

    The catalog download is expensive enough that it gets its own single-flight
    cache rather than riding on the client's response cache -- we want to keep
    the *parsed* index, not re-slim 11k records on every call.
    """

    def __init__(self, client: SleeperClient, *, ttl: float = TTL_PLAYERS) -> None:
        self._client = client
        self._ttl = ttl
        self._cache = AsyncTTLCache(default_ttl=ttl, max_entries=4)

    async def load(self, sport: str = "nfl") -> PlayerIndex:
        async def build() -> PlayerIndex:
            raw = await self._client.get_all_players(sport)
            return PlayerIndex.from_raw(raw)

        return await self._cache.get_or_fetch(("index", sport), build, self._ttl)
