from pathlib import Path

from fantasyprep.adp_gap.report import parse_args


def test_exclude_positions_parses_comma_separated_list():
    args = parse_args(["--year", "2026", "--exclude-positions", "QB, K"])
    assert args.exclude_positions == {"QB", "K"}


def test_exclude_positions_defaults_empty():
    args = parse_args(["--year", "2026"])
    assert args.exclude_positions == set()


def test_sharp_source_defaults_to_csv():
    args = parse_args(["--year", "2026"])
    assert args.sharp_source == "csv"


def test_sharp_source_sleeper_flag():
    args = parse_args(["--year", "2026", "--sharp-source", "sleeper"])
    assert args.sharp_source == "sleeper"


def test_max_adp_defaults_to_220():
    args = parse_args(["--year", "2026"])
    assert args.max_adp == 220


def test_max_adp_can_be_disabled():
    args = parse_args(["--year", "2026", "--max-adp", "0"])
    assert args.max_adp == 0


def test_max_adp_custom_value():
    args = parse_args(["--year", "2026", "--max-adp", "150"])
    assert args.max_adp == 150
