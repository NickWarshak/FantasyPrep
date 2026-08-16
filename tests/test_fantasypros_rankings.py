from pathlib import Path

from fantasyprep.historical.sources.fantasypros_rankings import load_fantasypros_rankings

HEADER = '"RK",TIERS,"PLAYER NAME",TEAM,"POS","BYE","UPSIDE ","BUST ","SOS","ECR VS ADP"\n'


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "rankings.csv"
    path.write_text(HEADER + body, encoding="utf-8")
    return path


def test_parses_basic_row_and_strips_position_rank_suffix(tmp_path):
    csv_path = _write(tmp_path, '"1",1,"Ja\'Marr Chase",CIN,"WR1","6","5 out of 5","1 out of 5","-","+2"\n')
    players = load_fantasypros_rankings(csv_path)
    assert len(players) == 1
    p = players[0]
    assert p.name == "Ja'Marr Chase"
    assert p.team == "CIN"
    assert p.position == "WR"  # "WR1" -> "WR"
    assert p.adp == 1.0


def test_skips_tier_break_separator_rows(tmp_path):
    body = (
        '"1",1,"Player A",CIN,"WR1","6","-","-","-","+2"\n'
        '"",4\n'  # tier-break row, no player -- must not crash or produce a phantom entry
        '"2",1,"Player B",DAL,"RB1","6","-","-","-","-1"\n'
    )
    players = load_fantasypros_rankings(_write(tmp_path, body))
    assert [p.name for p in players] == ["Player A", "Player B"]


def test_dst_row_uses_team_name_as_player_name(tmp_path):
    csv_path = _write(tmp_path, '"157",9,"Houston Texans",HOU,"DST1","8","-","-","-","-58"\n')
    players = load_fantasypros_rankings(csv_path)
    assert players[0].name == "Houston Texans"
    assert players[0].position == "DST"


def test_free_agent_team_defaults_correctly(tmp_path):
    csv_path = _write(tmp_path, '"312",13,"Tyreek Hill",FA,"WR110","-","1 out of 5","5 out of 5","-","-120"\n')
    players = load_fantasypros_rankings(csv_path)
    assert players[0].team == "FA"


def test_sorted_by_rank_ascending(tmp_path):
    body = (
        '"5",1,"Later",CIN,"WR1","6","-","-","-","0"\n'
        '"1",1,"Earlier",DAL,"RB1","6","-","-","-","0"\n'
    )
    players = load_fantasypros_rankings(_write(tmp_path, body))
    assert [p.name for p in players] == ["Earlier", "Later"]


def test_narrow_tier_gets_tighter_stdev_than_wide_tier(tmp_path):
    body = (
        # Tier 1: ranks 1-2, width 1 (narrow)
        '"1",1,"Tight A",CIN,"WR1","6","-","-","-","0"\n'
        '"2",1,"Tight B",DAL,"WR2","6","-","-","-","0"\n'
        # Tier 2: ranks 3-20, width 17 (wide)
        '"3",2,"Loose A",MIA,"WR3","6","-","-","-","0"\n'
        '"20",2,"Loose B",NYJ,"WR4","6","-","-","-","0"\n'
    )
    players = load_fantasypros_rankings(_write(tmp_path, body))
    by_name = {p.name: p for p in players}
    assert by_name["Tight A"].stdev < by_name["Loose A"].stdev


def test_lone_player_in_a_tier_falls_back_to_min_stdev(tmp_path):
    csv_path = _write(tmp_path, '"1",1,"Solo",CIN,"WR1","6","-","-","-","0"\n')
    players = load_fantasypros_rankings(csv_path)
    assert players[0].stdev == 0.5  # MIN_STDEV floor, no tier-mates to derive a spread from
    assert players[0].high == players[0].low == 1


def test_unrecognized_position_format_is_skipped_not_crashed(tmp_path):
    # A malformed POS value (no trailing digit) shouldn't blow up parsing --
    # just skip that row rather than crash the whole load.
    body = (
        '"1",1,"Bad Row",CIN,"WR","6","-","-","-","0"\n'
        '"2",1,"Good Row",DAL,"RB1","6","-","-","-","0"\n'
    )
    players = load_fantasypros_rankings(_write(tmp_path, body))
    assert [p.name for p in players] == ["Good Row"]
