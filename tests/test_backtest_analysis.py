from fantasyprep.draft_sim.backtest_analysis import (
    draft_shape_by_round,
    first_round_by_position,
    picks_per_position,
    position_breakdown,
)
from fantasyprep.league.settings import LeagueSettings, ScoringSettings

SETTINGS = LeagueSettings(
    teams=4,
    scoring=ScoringSettings(),
    roster_slots={"QB": 1, "RB": 1, "WR": 1},
    bench=1,
)


def _result(baseline_roster, model_roster):
    return {"baseline_roster": baseline_roster, "model_roster": model_roster}


def test_position_breakdown_reports_every_roster_position():
    results = [
        _result(
            baseline_roster=[("QB1", "QB", 100.0), ("RB1", "RB", 80.0), ("WR1", "WR", 60.0)],
            model_roster=[("QB2", "QB", 120.0), ("RB2", "RB", 70.0), ("WR2", "WR", 90.0)],
        )
    ]
    breakdown = position_breakdown(results, SETTINGS)
    assert set(breakdown.keys()) == {"QB", "RB", "WR"}


def test_position_breakdown_computes_correct_means_and_deltas():
    results = [
        _result(
            baseline_roster=[("QB1", "QB", 100.0)],
            model_roster=[("QB2", "QB", 120.0)],
        ),
        _result(
            baseline_roster=[("QB1", "QB", 80.0)],
            model_roster=[("QB2", "QB", 60.0)],
        ),
    ]
    breakdown = position_breakdown(results, SETTINGS)
    assert breakdown["QB"]["baseline_mean"] == 90.0  # (100+80)/2
    assert breakdown["QB"]["model_mean"] == 90.0  # (120+60)/2
    assert breakdown["QB"]["delta"] == 0.0
    assert breakdown["QB"]["win_rate"] == 0.5  # model won 1 of 2 (replay 1), lost the other


def test_position_breakdown_only_counts_starters_not_bench():
    # 2 QBs drafted, only 1 QB slot -- the worse one sits on the bench and
    # shouldn't be counted (matches starting_lineup_value's own discipline).
    results = [
        _result(
            baseline_roster=[("QB1", "QB", 100.0), ("QB1b", "QB", 40.0)],
            model_roster=[("QB2", "QB", 120.0)],
        ),
    ]
    breakdown = position_breakdown(results, SETTINGS)
    assert breakdown["QB"]["baseline_mean"] == 100.0  # not 140 -- the bench QB is excluded


def test_position_breakdown_missing_position_scores_zero_not_missing():
    results = [
        _result(
            baseline_roster=[("QB1", "QB", 100.0)],  # no RB or WR drafted at all this replay
            model_roster=[("QB2", "QB", 120.0), ("RB2", "RB", 50.0)],
        ),
    ]
    breakdown = position_breakdown(results, SETTINGS)
    assert breakdown["RB"]["baseline_mean"] == 0.0
    assert breakdown["RB"]["model_mean"] == 50.0
    assert breakdown["WR"]["baseline_mean"] == 0.0
    assert breakdown["WR"]["model_mean"] == 0.0


# --- draft shape: roster tuple *order* is real draft/round order (see
# backtest_analysis.py module docstring for why) -- these tests pin that
# down explicitly since it's the whole basis for round-level analysis. ---


def _shape_result(model_roster):
    return {"model_roster": model_roster}


def test_draft_shape_by_round_uses_list_index_as_round():
    results = [
        _shape_result([("QB1", "QB", 1.0), ("RB1", "RB", 1.0), ("WR1", "WR", 1.0)]),
    ]
    shape = draft_shape_by_round(results, "model_roster")
    assert shape == {1: {"QB": 1.0}, 2: {"RB": 1.0}, 3: {"WR": 1.0}}


def test_draft_shape_by_round_splits_fractions_across_replays():
    results = [
        _shape_result([("QB1", "QB", 1.0)]),
        _shape_result([("RB1", "RB", 1.0)]),
    ]
    shape = draft_shape_by_round(results, "model_roster")
    assert shape[1] == {"QB": 0.5, "RB": 0.5}


def test_first_round_by_position_ignores_later_repeats():
    # A 2nd RB in round 3 shouldn't move RB's "first round" off of round 1.
    results = [
        _shape_result([("RB1", "RB", 1.0), ("QB1", "QB", 1.0), ("RB2", "RB", 1.0)]),
    ]
    first = first_round_by_position(results, "model_roster")
    assert first["RB"]["avg_first_round"] == 1
    assert first["QB"]["avg_first_round"] == 2


def test_first_round_by_position_frequency_only_over_replays_that_drafted_it():
    results = [
        _shape_result([("RB1", "RB", 1.0)]),
        _shape_result([("QB1", "QB", 1.0)]),  # no RB this replay
    ]
    first = first_round_by_position(results, "model_roster")
    assert first["RB"]["avg_first_round"] == 1  # not diluted by the replay that never took one
    assert first["RB"]["frequency"] == 0.5


def test_picks_per_position_averages_across_replays():
    results = [
        _shape_result([("RB1", "RB", 1.0), ("RB2", "RB", 1.0)]),
        _shape_result([("RB1", "RB", 1.0)]),
    ]
    picks = picks_per_position(results, "model_roster")
    assert picks["RB"] == 1.5  # (2 + 1) / 2
