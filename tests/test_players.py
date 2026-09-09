"""Name normalisation, slimming and search ranking."""

from __future__ import annotations

from sleeper_mcp.players import PlayerIndex, normalize

from .conftest import RAW_PLAYERS


def test_normalize_strips_accents_punctuation_and_extra_space() -> None:
    assert normalize("Amon-Ra St. Brown") == "amonra st brown"
    assert normalize("D'Andre  Swift") == "dandre swift"
    assert normalize("José  Ramírez") == "jose ramirez"


def test_slimming_builds_names_from_parts_and_keeps_ids(index: PlayerIndex) -> None:
    defense = index.get("SF")
    assert defense is not None
    assert defense.name == "San Francisco 49ers"
    assert defense.position == "DEF"
    assert len(index) == len(RAW_PLAYERS)


def test_label_and_detail_surface_availability(index: PlayerIndex) -> None:
    assert index.get("4046").label() == "Patrick Mahomes (KC - QB)"
    assert index.get("6794").detail() == "Amon-Ra St. Brown (DET - WR) [Questionable]"
    assert index.get("1234").detail() == "Patrick Ricard (BAL - RB) [Inactive]"


def test_unknown_id_degrades_instead_of_raising(index: PlayerIndex) -> None:
    assert index.get("nope") is None
    assert index.label("nope") == "Unknown player nope"
    assert index.labels(["4046", "nope"]) == [
        "Patrick Mahomes (KC - QB)",
        "Unknown player nope",
    ]
    assert index.labels(None) == []


def test_exact_name_outranks_a_shared_first_name(index: PlayerIndex) -> None:
    """'patrick' matches two players; the QB wins on being active and rostered."""
    results = index.search("patrick mahomes")
    assert results[0].player_id == "4046"

    both = index.search("patrick")
    assert {p.player_id for p in both} == {"4046", "1234"}
    assert both[0].player_id == "4046"


def test_surname_only_search_works(index: PlayerIndex) -> None:
    assert index.search("mahomes")[0].player_id == "4046"


def test_token_prefix_search_handles_punctuated_names(index: PlayerIndex) -> None:
    assert index.search("amon ra st")[0].player_id == "6794"


def test_position_and_team_filters(index: PlayerIndex) -> None:
    assert [p.player_id for p in index.search("patrick", position="RB")] == ["1234"]
    assert [p.player_id for p in index.search("patrick", team="KC")] == ["4046"]
    assert index.search("patrick", position="TE")[0].player_id == "1234", (
        "fantasy_positions must count, not just the primary position"
    )


def test_active_only_filter(index: PlayerIndex) -> None:
    assert [p.player_id for p in index.search("patrick", active_only=True)] == ["4046"]


def test_limit_and_empty_query(index: PlayerIndex) -> None:
    assert len(index.search("patrick", limit=1)) == 1
    assert index.search("") == []
    assert index.search("   ") == []
    assert index.search("zzzzz") == []


def test_non_mapping_entries_are_skipped() -> None:
    built = PlayerIndex.from_raw({"4046": RAW_PLAYERS["4046"], "junk": "not a dict"})
    assert len(built) == 1
