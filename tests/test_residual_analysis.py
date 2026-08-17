"""Residual/dispersion analysis: the bootstrap has to be honest.

The headline claim -- that risk is predictable within an ADP tier -- rests
entirely on a confidence interval excluding zero. A bootstrap that reports
significance too readily would manufacture an edge that isn't there.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from fantasyprep.research.residual_analysis import (
    EARLY_ROUND_RANK_CUTOFF,
    _bootstrap_stdev_gap,
    feature_associations,
)


def _combined(n: int, high_scale: float, low_scale: float, seed: int = 0) -> pd.DataFrame:
    """Half the players predicted risky, half safe, with controllable truth."""
    rng = np.random.default_rng(seed)
    half = n // 2
    # Ranks cycle inside the early-round window so the whole fixture survives
    # the rank cutoff; the tier split then has real players on both sides.
    ranks = [(i % EARLY_ROUND_RANK_CUTOFF) + 1 for i in range(half)]
    return pd.DataFrame(
        {
            "adp_position_rank": ranks * 2,
            "predicted_risk": [1.0] * half + [0.0] * half,
            "residual": np.concatenate(
                [rng.normal(0, high_scale, half), rng.normal(0, low_scale, half)]
            ),
        }
    )


def test_bootstrap_detects_a_real_dispersion_gap():
    combined = _combined(600, high_scale=100.0, low_scale=50.0)

    result = _bootstrap_stdev_gap(combined)

    assert result["observed_stdev_gap"] > 0
    assert result["excludes_zero"] is True
    assert result["ci_low"] > 0


def test_bootstrap_reports_no_gap_when_the_split_is_noise():
    # Both halves drawn from the SAME distribution -- the split carries no
    # information, so the interval must span zero. This is the test that stops
    # the analysis from inventing an edge.
    combined = _combined(600, high_scale=80.0, low_scale=80.0, seed=7)

    result = _bootstrap_stdev_gap(combined)

    assert result["excludes_zero"] is False
    assert result["ci_low"] < 0 < result["ci_high"]


def test_bootstrap_is_deterministic_for_a_given_seed():
    combined = _combined(400, high_scale=90.0, low_scale=60.0)

    assert _bootstrap_stdev_gap(combined, seed=3) == _bootstrap_stdev_gap(combined, seed=3)


def test_bootstrap_only_uses_early_round_players():
    combined = _combined(600, high_scale=100.0, low_scale=50.0)
    combined["adp_position_rank"] = combined["adp_position_rank"] + 500  # all deep

    assert _bootstrap_stdev_gap(combined) == {}


def test_bootstrap_respects_the_rank_cutoff_boundary():
    combined = _combined(600, high_scale=100.0, low_scale=50.0)
    kept = combined[combined["adp_position_rank"] <= EARLY_ROUND_RANK_CUTOFF]

    result = _bootstrap_stdev_gap(combined)

    assert result["n_high_risk"] + result["n_low_risk"] == len(kept)


def test_feature_associations_skip_sparse_columns():
    residuals = pd.DataFrame(
        {
            "residual": np.arange(50.0),
            "abs_residual": np.abs(np.arange(50.0)),
            "age": np.arange(50.0),
        }
    )

    # Fewer than 100 usable rows -- reporting a correlation on 50 points would
    # be noise dressed as a finding.
    assert feature_associations(residuals) == {}
