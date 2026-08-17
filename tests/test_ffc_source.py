import json
from pathlib import Path

from fantasyprep.historical.sources.ffc import (
    FfcPlayer,
    ambiguous_names,
    derive_rank_cutoff,
    fetch_adp,
    position_ranks,
    ranked_players,
)
from fantasyprep.league.settings import LeagueSettings, ScoringSettings

RAW_FIXTURE = {
    "status": "Success",
    "meta": {"type": "PPR", "teams": 10, "rounds": 15},
    "players": [
        {"player_id": 1, "name": "Elite RB", "position": "RB", "team": "AAA", "adp": 1.2, "stdev": 0.5, "high": 1, "low": 3},
        {"player_id": 2, "name": "Elite WR", "position": "WR", "team": "BBB", "adp": 2.4, "stdev": 0.8, "high": 1, "low": 4},
        {"player_id": 3, "name": "Second WR", "position": "WR", "team": "CCC", "adp": 8.0, "stdev": 2.0, "high": 5, "low": 12},
        {"player_id": 4, "name": "Some Lineman", "position": "OL", "team": "DDD", "adp": 300.0, "stdev": 5.0, "high": 250, "low": 350},
        {"player_id": 5, "name": "Some Defense", "position": "DEF", "team": "EEE", "adp": 145.0, "stdev": 12.0, "high": 100, "low": 190},
        {"player_id": 6, "name": "Some Kicker", "position": "PK", "team": "FFF", "adp": 150.0, "stdev": 10.0, "high": 110, "low": 195},
    ],
}


def test_fetch_adp_normalizes_and_filters_positions(tmp_path: Path):
    cache_path = tmp_path / "ffc_cache.json"
    cache_path.write_text(json.dumps(RAW_FIXTURE), encoding="utf-8")

    players = fetch_adp(year=2023, teams=10, cache_path=cache_path)

    names = {p.name for p in players}
    assert "Elite RB" in names
    assert "Some Lineman" not in names  # not a fantasy-relevant position


def test_fetch_adp_sorted_by_adp(tmp_path: Path):
    cache_path = tmp_path / "ffc_cache.json"
    cache_path.write_text(json.dumps(RAW_FIXTURE), encoding="utf-8")

    players = fetch_adp(year=2023, teams=10, cache_path=cache_path)

    adps = [p.adp for p in players]
    assert adps == sorted(adps)


def test_position_ranks_are_per_position_not_global(tmp_path: Path):
    cache_path = tmp_path / "ffc_cache.json"
    cache_path.write_text(json.dumps(RAW_FIXTURE), encoding="utf-8")

    players = fetch_adp(year=2023, teams=10, cache_path=cache_path)
    ranks = position_ranks(players)

    assert ranks["Elite RB"] == 1  # only RB -> rank 1
    assert ranks["Elite WR"] == 1  # earliest WR -> rank 1
    assert ranks["Second WR"] == 2  # second-earliest WR -> rank 2


def test_fetch_adp_empty_player_list(tmp_path: Path):
    cache_path = tmp_path / "ffc_cache.json"
    cache_path.write_text(json.dumps({"status": "Success", "players": []}), encoding="utf-8")

    players = fetch_adp(year=2023, teams=10, cache_path=cache_path)
    assert players == []


# --- Regression: FFC's own position codes for defense/kicker ("DEF"/"PK")
# don't match ours ("DST"/"K"). Silently dropped both from every fetch this
# whole project until FFC_POSITION_MAP was added -- opponents never drafted
# a DST/K in any simulation, and the live search picker couldn't find one
# either. ---


def test_fetch_adp_maps_ffc_defense_code_to_dst(tmp_path: Path):
    cache_path = tmp_path / "ffc_cache.json"
    cache_path.write_text(json.dumps(RAW_FIXTURE), encoding="utf-8")

    players = fetch_adp(year=2023, teams=10, cache_path=cache_path)

    defense = next(p for p in players if p.name == "Some Defense")
    assert defense.position == "DST"


def test_fetch_adp_maps_ffc_kicker_code_to_k(tmp_path: Path):
    cache_path = tmp_path / "ffc_cache.json"
    cache_path.write_text(json.dumps(RAW_FIXTURE), encoding="utf-8")

    players = fetch_adp(year=2023, teams=10, cache_path=cache_path)

    kicker = next(p for p in players if p.name == "Some Kicker")
    assert kicker.position == "K"


# --- derive_rank_cutoff: real draft depth per position, not a guessed split ---


def _player(name: str, position: str, adp: float) -> FfcPlayer:
    return FfcPlayer(name=name, position=position, team="AAA", adp=adp, stdev=1.0, high=1, low=1)


def test_derive_rank_cutoff_counts_real_draft_depth_per_position():
    # 2 teams, roster_slots sum=5 + bench=1 -> 6 rounds -> total_picks = 12.
    settings = LeagueSettings(
        teams=2,
        scoring=ScoringSettings(),
        roster_slots={"QB": 1, "RB": 1, "WR": 1, "FLEX": 1, "DST": 1},
        bench=1,
    )
    # ADP 1-12 drafted (5 RB, 4 WR, 1 QB, 1 TE, 1 DST); ADP 13-14 undrafted.
    positions_by_adp = ["RB", "WR", "QB", "RB", "WR", "RB", "WR", "TE", "RB", "WR", "DST", "RB", "QB", "WR"]
    players = [_player(f"P{i}", pos, adp=float(i)) for i, pos in enumerate(positions_by_adp, start=1)]

    cutoff = derive_rank_cutoff(players, settings)

    # cutoff = (real drafted count at that position) + 1 -- one past the
    # last player who actually gets drafted, i.e. the first true free agent.
    assert cutoff == {"RB": 6, "WR": 5, "QB": 2, "TE": 2, "DST": 2}


def test_derive_rank_cutoff_ignores_undrafted_tail():
    settings = LeagueSettings(
        teams=2,
        scoring=ScoringSettings(),
        roster_slots={"QB": 1, "RB": 1, "WR": 1, "FLEX": 1, "DST": 1},
        bench=1,
    )
    positions_by_adp = ["RB", "WR", "QB", "RB", "WR", "RB", "WR", "TE", "RB", "WR", "DST", "RB", "QB", "WR"]
    players = [_player(f"P{i}", pos, adp=float(i)) for i, pos in enumerate(positions_by_adp, start=1)]

    cutoff = derive_rank_cutoff(players, settings)

    # Only 1 QB is actually drafted (adp 3); the undrafted 2nd QB (adp 13)
    # must not inflate the count.
    assert cutoff["QB"] == 2


def _wr(name: str, team: str, adp: float) -> FfcPlayer:
    return FfcPlayer(name=name, position="WR", team=team, adp=adp, stdev=5.0, high=1, low=99)


def test_two_players_sharing_a_name_each_keep_their_own_rank():
    # Real case: two different Mike Williamses (Tampa Bay and Seattle) were
    # both active receivers in 2010 and 2011, with ADPs 114 picks apart.
    # A name-keyed dict cannot represent this, so the original implementation
    # let one silently overwrite the other -- both came back rank 62.
    players = [
        _wr("Mike Williams", "TB", 42.1),
        _wr("Other Guy", "KC", 80.0),
        _wr("Mike Williams", "SEA", 156.0),
    ]

    ranked = ranked_players(players)

    by_team = {(p.name, p.team): rank for p, rank in ranked}
    assert by_team[("Mike Williams", "TB")] == 1
    assert by_team[("Other Guy", "KC")] == 2
    assert by_team[("Mike Williams", "SEA")] == 3


def test_ambiguous_names_are_detected():
    players = [
        _wr("Mike Williams", "TB", 42.1),
        _wr("Mike Williams", "SEA", 156.0),
        _wr("Unique Guy", "KC", 80.0),
    ]

    assert ambiguous_names(players) == {"Mike Williams"}


def test_position_ranks_abstains_on_ambiguous_names():
    players = [
        _wr("Mike Williams", "TB", 42.1),
        _wr("Mike Williams", "SEA", 156.0),
        _wr("Unique Guy", "KC", 80.0),
    ]

    ranks = position_ranks(players)

    # Omitted rather than resolved arbitrarily: callers treat a miss as
    # "unknown, assume deep", which is honest. A confidently wrong rank is not.
    assert "Mike Williams" not in ranks
    assert ranks["Unique Guy"] == 2


def test_same_name_at_different_positions_is_not_ambiguous():
    # Only a name+position clash is genuinely unresolvable; a WR and a TE
    # sharing a name are separable by the position the caller already knows.
    players = [
        _wr("Mike Williams", "TB", 42.1),
        FfcPlayer(name="Mike Williams", position="TE", team="SEA", adp=90.0,
                  stdev=5.0, high=1, low=99),
    ]

    assert ambiguous_names(players) == set()
    assert position_ranks(players)["Mike Williams"] == 1
