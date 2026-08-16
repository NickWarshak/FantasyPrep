import pytest

from fantasyprep.historical.sources.nfl_stats import compute_points
from fantasyprep.league.settings import ScoringSettings

SETTINGS = ScoringSettings()  # full PPR defaults


def testcompute_points_basic_stat_lines():
    row = {
        "passing_yards": 0, "passing_tds": 0, "interceptions": 0,
        "rushing_yards": 100, "rushing_tds": 1,
        "receptions": 5, "receiving_yards": 50, "receiving_tds": 0,
    }
    # 100*0.1 + 1*6 + 5*1 + 50*0.1 = 10 + 6 + 5 + 5 = 26
    assert compute_points(row, SETTINGS) == 26.0


def testcompute_points_credits_special_teams_td():
    # Regression test for the real Gunner Olszewski 2023 case: a return TD
    # was silently worth 0 points before this was caught by cross-checking
    # against nflverse's own fantasy_points_ppr column.
    row = {"receptions": 4, "receiving_yards": 29, "receiving_tds": 1, "special_teams_tds": 1}
    with_st_td = compute_points(row, SETTINGS)

    row_without = dict(row, special_teams_tds=0)
    without_st_td = compute_points(row_without, SETTINGS)

    assert with_st_td - without_st_td == pytest.approx(SETTINGS.special_teams_td)
    assert SETTINGS.special_teams_td == 6.0


def testcompute_points_credits_two_pt_conversions():
    row_base = {"rushing_yards": 0, "rushing_tds": 0}
    row_with_2pt = dict(row_base, rushing_2pt_conversions=1)

    base = compute_points(row_base, SETTINGS)
    with_2pt = compute_points(row_with_2pt, SETTINGS)

    assert with_2pt - base == SETTINGS.two_pt_conversion == 2.0


def testcompute_points_sums_two_pt_conversions_across_types():
    row = {
        "passing_2pt_conversions": 1,
        "rushing_2pt_conversions": 1,
        "receiving_2pt_conversions": 1,
    }
    assert compute_points(row, SETTINGS) == 3 * SETTINGS.two_pt_conversion


def testcompute_points_handles_missing_keys_gracefully():
    # A row missing every key entirely (not just zero) should still compute cleanly.
    assert compute_points({}, SETTINGS) == 0.0


def testcompute_points_fumbles_lost_still_penalized():
    row = {"rushing_fumbles_lost": 1}
    assert compute_points(row, SETTINGS) == SETTINGS.fumble_lost == -2.0
