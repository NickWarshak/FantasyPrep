from pathlib import Path

from fantasyprep.players.normalize import (
    MatchedPlayer,
    load_aliases,
    match_players,
    normalize_name,
)
from fantasyprep.sources.espn import EspnPlayer
from fantasyprep.sources.manual_adp import SharpAdpEntry


def test_normalize_name_strips_suffixes_and_punctuation():
    assert normalize_name("Michael Pittman Jr.") == "michael pittman"
    assert normalize_name("Odell Beckham III") == "odell beckham"
    assert normalize_name("Ja'Marr Chase") == "jamarr chase"
    assert normalize_name("D'Andre Swift") == "dandre swift"
    assert normalize_name("Amon-Ra St. Brown") == "amon ra st brown"


def test_normalize_name_collapses_whitespace():
    assert normalize_name("  Tee   Higgins  ") == "tee higgins"


def _espn_player(espn_id, name, position, team, adp) -> EspnPlayer:
    return EspnPlayer(espn_id=espn_id, name=name, position=position, team=team, espn_adp=adp, espn_expert_rank=None)


def _sharp_entry(name, position, team, adp, source="test") -> SharpAdpEntry:
    return SharpAdpEntry(player_name=name, team=team, position=position, adp=adp, source=source)


def test_match_players_exact_match():
    espn = [_espn_player(1, "Tee Higgins", "WR", "CIN", 58.0)]
    sharp = [_sharp_entry("Tee Higgins", "WR", "CIN", 40.0)]

    matched, unmatched_sharp, unmatched_espn = match_players(espn, sharp)

    assert len(matched) == 1
    assert matched[0].match_confidence == 100
    assert not unmatched_sharp
    assert not unmatched_espn


def test_match_players_uses_alias(tmp_path: Path):
    espn = [_espn_player(1, "Gabriel Davis", "WR", "JAX", 120.0)]
    sharp = [_sharp_entry("Gabe Davis", "WR", "JAX", 95.0)]

    alias_csv = tmp_path / "aliases.csv"
    alias_csv.write_text("sharp_name,position,espn_name\nGabe Davis,WR,Gabriel Davis\n", encoding="utf-8")
    aliases = load_aliases(alias_csv)

    matched, unmatched_sharp, _ = match_players(espn, sharp, aliases)

    assert len(matched) == 1
    assert matched[0].espn.name == "Gabriel Davis"
    assert not unmatched_sharp


def test_match_players_fuzzy_fallback():
    espn = [_espn_player(1, "DJ Chark Jr.", "WR", "GB", 180.0)]
    sharp = [_sharp_entry("D.J. Chark", "WR", "GB", 150.0)]

    matched, unmatched_sharp, _ = match_players(espn, sharp)

    assert len(matched) == 1
    assert matched[0].match_confidence >= 85


def test_match_players_no_match_left_unmatched():
    espn = [_espn_player(1, "Christian McCaffrey", "RB", "SF", 6.0)]
    sharp = [_sharp_entry("Some Rookie Nobody Tracks", "RB", "XXX", 200.0)]

    matched, unmatched_sharp, unmatched_espn = match_players(espn, sharp)

    assert not matched
    assert len(unmatched_sharp) == 1
    assert len(unmatched_espn) == 1


def test_match_players_position_mismatch_not_matched():
    espn = [_espn_player(1, "Taysom Hill", "QB", "NO", 120.0)]
    sharp = [_sharp_entry("Taysom Hill", "TE", "NO", 130.0)]

    matched, unmatched_sharp, unmatched_espn = match_players(espn, sharp)

    assert not matched
    assert len(unmatched_sharp) == 1


def test_match_players_team_breaks_name_collision():
    espn = [
        _espn_player(1, "Michael Thomas", "WR", "NO", 90.0),
        _espn_player(2, "Michael Thomas", "WR", "HOU", 250.0),
    ]
    sharp = [_sharp_entry("Michael Thomas", "WR", "HOU", 230.0)]

    matched, _, _ = match_players(espn, sharp)

    assert len(matched) == 1
    assert matched[0].espn.espn_id == 2
