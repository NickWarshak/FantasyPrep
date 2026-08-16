from fantasyprep.historical.weekly_stats import (
    WeeklyOutcome,
    _waiver_adjusted_totals,
    replacement_level_by_week,
)


def _w(name, position, week, points):
    return WeeklyOutcome(name=name, position=position, team="XXX", week=week, points=points)


# --- replacement_level_by_week ---------------------------------------------------


def test_replacement_level_picks_the_cutoff_ranked_score():
    # 5 RBs in week 1, cutoff=3 -> the 3rd-best score.
    outcomes = [_w(f"RB{i}", "RB", 1, float(i)) for i in range(1, 6)]  # scores 1.0..5.0
    levels = replacement_level_by_week(outcomes, rank_cutoff={"RB": 3})
    assert levels[("RB", 1)] == 3.0  # 3rd best of [5,4,3,2,1] sorted desc


def test_replacement_level_falls_back_to_worst_available_if_fewer_than_cutoff():
    outcomes = [_w("RB1", "RB", 1, 10.0), _w("RB2", "RB", 1, 5.0)]
    levels = replacement_level_by_week(outcomes, rank_cutoff={"RB": 30})
    assert levels[("RB", 1)] == 5.0  # only 2 players, cutoff clamps to the worst of them


def test_replacement_level_never_negative():
    outcomes = [_w("RB1", "RB", 1, -5.0)]  # a real bad game (e.g. big fumble penalty)
    levels = replacement_level_by_week(outcomes, rank_cutoff={"RB": 1})
    assert levels[("RB", 1)] == 0.0


def test_replacement_level_separate_per_position_and_week():
    outcomes = [
        _w("RB1", "RB", 1, 20.0),
        _w("WR1", "WR", 1, 15.0),
        _w("RB2", "RB", 2, 8.0),
    ]
    levels = replacement_level_by_week(outcomes, rank_cutoff={"RB": 1, "WR": 1})
    assert levels[("RB", 1)] == 20.0
    assert levels[("WR", 1)] == 15.0
    assert levels[("RB", 2)] == 8.0
    assert ("WR", 2) not in levels  # no WR data for week 2 at all


# --- _waiver_adjusted_totals ---------------------------------------------------


def test_waiver_adjusted_totals_full_season_player_gets_raw_sum():
    outcomes = [
        _w("Full Season Guy", "RB", 1, 10.0),
        _w("Full Season Guy", "RB", 2, 12.0),
        _w("Bench Filler", "RB", 1, 3.0),
        _w("Bench Filler", "RB", 2, 4.0),
    ]
    totals = _waiver_adjusted_totals(outcomes, rank_cutoff={"RB": 30})
    assert totals["full season guy"] == 22.0  # played every week -- no replacement credit needed


def test_waiver_adjusted_totals_credits_replacement_for_missed_weeks():
    # Star player only plays week 1 (injured after); a replacement-level
    # backup covers week 2 for everyone else.
    outcomes = [
        _w("Star RB", "RB", 1, 25.0),
        _w("Replacement RB", "RB", 2, 6.0),
    ]
    totals = _waiver_adjusted_totals(outcomes, rank_cutoff={"RB": 1})
    # Star RB: real 25.0 (week 1) + replacement-level for week 2 (the only
    # RB score available that week, 6.0, since cutoff=1) = 31.0
    assert totals["star rb"] == 31.0


def test_waiver_adjusted_totals_beats_naive_raw_sum_for_an_injured_player():
    # Directly demonstrates the point of this module: an injured player's
    # waiver-adjusted total is higher than just summing their own played
    # weeks, because a real manager wouldn't eat a zero for the rest of
    # the season.
    outcomes = [
        _w("Injured Star", "QB", 1, 20.0),
        _w("Injured Star", "QB", 2, 22.0),
        _w("Streaming Option", "QB", 3, 12.0),
        _w("Streaming Option", "QB", 4, 14.0),
    ]
    totals = _waiver_adjusted_totals(outcomes, rank_cutoff={"QB": 1})
    raw_sum_played_weeks_only = 20.0 + 22.0
    assert totals["injured star"] > raw_sum_played_weeks_only
    assert totals["injured star"] == 20.0 + 22.0 + 12.0 + 14.0  # real weeks + replacement for weeks 3-4


def test_waiver_adjusted_totals_normalizes_names():
    outcomes = [_w("D.J. Chark Jr.", "WR", 1, 10.0)]
    totals = _waiver_adjusted_totals(outcomes, rank_cutoff={"WR": 1})
    assert "dj chark" in totals
