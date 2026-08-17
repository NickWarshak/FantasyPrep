"""Mean-preserving spread must preserve the mean exactly.

The whole experiment rests on attributing objective movement to variance alone.
If the transform shifted the mean even slightly, that attribution collapses.
"""
from __future__ import annotations

import statistics

import pytest

from fantasyprep.historical.outcomes import OutcomeDistribution
from fantasyprep.research.variance_sensitivity import _verdict, mean_preserving_spread


def _dists():
    return {
        ("WR", 0): OutcomeDistribution("WR", 0, [100.0, 200.0, 300.0, 400.0]),
        ("RB", 0): OutcomeDistribution("RB", 0, [150.0, 250.0]),
    }


def test_spread_preserves_the_mean_exactly():
    original = _dists()[("WR", 0)].outcomes
    spread = mean_preserving_spread(_dists(), "WR", 2.0)[("WR", 0)].outcomes

    assert statistics.mean(spread) == pytest.approx(statistics.mean(original))


def test_spread_scales_the_standard_deviation_by_the_factor():
    original = _dists()[("WR", 0)].outcomes
    spread = mean_preserving_spread(_dists(), "WR", 2.0)[("WR", 0)].outcomes

    assert statistics.stdev(spread) == pytest.approx(2.0 * statistics.stdev(original))


def test_spread_leaves_other_positions_untouched():
    result = mean_preserving_spread(_dists(), "WR", 2.0)

    assert result[("RB", 0)].outcomes == [150.0, 250.0]


def test_factor_of_one_is_an_identity():
    result = mean_preserving_spread(_dists(), "WR", 1.0)

    assert result[("WR", 0)].outcomes == pytest.approx([100.0, 200.0, 300.0, 400.0])


def test_verdict_detects_variance_seeking():
    rows = [
        {"spread_factor": 1.0, "top_recommendation": "RB", "spread_position_value": 1700.0},
        {"spread_factor": 1.5, "top_recommendation": "WR", "spread_position_value": 1760.0},
    ]

    verdict = _verdict(rows, "WR")

    assert verdict["objective_is_variance_seeking"] is True
    assert verdict["value_gain_from_pure_variance"] == {1.5: 60.0}
    assert verdict["recommendation_flipped_at_factors"] == [1.5]


def test_verdict_reports_no_variance_seeking_when_value_does_not_move():
    rows = [
        {"spread_factor": 1.0, "top_recommendation": "RB", "spread_position_value": 1700.0},
        {"spread_factor": 1.5, "top_recommendation": "RB", "spread_position_value": 1699.0},
    ]

    verdict = _verdict(rows, "WR")

    assert verdict["objective_is_variance_seeking"] is False
    assert verdict["recommendation_flipped_at_factors"] == []
