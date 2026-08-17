"""Distributional scoring: the metrics have to be right before the verdict is.

Calibration is the scoreboard for the whole vNext architecture, so a bug in
pinball loss or coverage would silently pick the wrong model.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fantasyprep.research.distribution_benchmark import (
    MIN_BUCKET_SAMPLES,
    QUANTILES,
    _bucket_keys,
    _build_bucket_index,
    pinball_loss,
    predict_empirical_quantiles,
    score_distribution,
)


def test_pinball_loss_is_asymmetric_in_the_right_direction():
    # At q=0.9 under-predicting must cost more than over-predicting by the
    # same amount -- that asymmetry is the whole mechanism that pushes a high
    # quantile upward.
    under = pinball_loss(np.array([100.0]), np.array([80.0]), q=0.9)
    over = pinball_loss(np.array([100.0]), np.array([120.0]), q=0.9)

    assert under > over
    assert under == pytest.approx(0.9 * 20)
    assert over == pytest.approx(0.1 * 20)


def test_pinball_loss_flips_asymmetry_for_low_quantiles():
    under = pinball_loss(np.array([100.0]), np.array([80.0]), q=0.1)
    over = pinball_loss(np.array([100.0]), np.array([120.0]), q=0.1)

    assert over > under


def test_pinball_loss_is_zero_for_a_perfect_prediction():
    assert pinball_loss(np.array([100.0]), np.array([100.0]), q=0.5) == 0.0


def _predictions_from_quantiles(actual: np.ndarray) -> np.ndarray:
    """A perfectly calibrated forecaster: every row predicts the true
    unconditional quantiles of the sample."""
    row = np.quantile(actual, QUANTILES)
    return np.tile(row, (len(actual), 1))


def test_a_perfectly_calibrated_forecaster_scores_near_nominal_coverage():
    rng = np.random.default_rng(0)
    actual = rng.gamma(shape=2.0, scale=100.0, size=4000)

    scores = score_distribution(actual, _predictions_from_quantiles(actual))

    for q in QUANTILES:
        assert scores["coverage"][f"p{int(q * 100)}"] == pytest.approx(q, abs=0.02)
    assert scores["mean_abs_coverage_error"] < 0.02


def test_an_overconfident_forecaster_is_penalised_on_coverage():
    rng = np.random.default_rng(1)
    actual = rng.gamma(shape=2.0, scale=100.0, size=4000)

    honest = _predictions_from_quantiles(actual)
    # Squeeze every interval toward the median: same centre, false certainty.
    median = honest[:, QUANTILES.index(0.50)][:, None]
    overconfident = median + (honest - median) * 0.3

    honest_scores = score_distribution(actual, honest)
    tight_scores = score_distribution(actual, overconfident)

    assert tight_scores["mean_abs_coverage_error"] > honest_scores["mean_abs_coverage_error"]
    # ...and pinball loss must also reject it, so a model can't win by being
    # narrow. This is why pinball is the primary proper score here.
    assert tight_scores["mean_pinball"] > honest_scores["mean_pinball"]


def test_absurdly_wide_intervals_are_also_penalised():
    rng = np.random.default_rng(2)
    actual = rng.gamma(shape=2.0, scale=100.0, size=2000)

    honest = _predictions_from_quantiles(actual)
    median = honest[:, QUANTILES.index(0.50)][:, None]
    too_wide = median + (honest - median) * 5.0

    assert (
        score_distribution(actual, too_wide)["mean_pinball"]
        > score_distribution(actual, honest)["mean_pinball"]
    )


def test_crps_and_interval_width_are_reported():
    rng = np.random.default_rng(3)
    actual = rng.gamma(shape=2.0, scale=100.0, size=500)

    scores = score_distribution(actual, _predictions_from_quantiles(actual))

    assert scores["crps"] > 0
    assert scores["mean_interval_width_p10_p90"] > 0
    assert scores["n"] == 500


# --- bucket fallback chain --------------------------------------------------


def _row(adp_rank=5, prior_rank=8, position="WR") -> pd.Series:
    return pd.Series(
        {
            "fantasy_position": position,
            "adp_position_rank": adp_rank,
            "prev_position_rank": prior_rank,
        }
    )


def test_bucket_keys_go_from_specific_to_general():
    keys = _bucket_keys(_row(), use_prior=True)

    assert len(keys) == 3
    assert len(keys[0]) == 3  # position + adp bucket + prior bucket
    assert len(keys[1]) == 2  # position + adp bucket
    assert keys[2] == ("WR",)  # position only


def test_prior_key_is_skipped_for_a_rookie():
    keys = _bucket_keys(_row(prior_rank=None), use_prior=True)

    assert all(len(k) <= 2 for k in keys)


def test_prior_key_is_absent_when_not_requested():
    assert all(len(k) <= 2 for k in _bucket_keys(_row(), use_prior=False))


def test_thin_bucket_falls_back_instead_of_emitting_a_tiny_sample():
    # One WR in a specific ADP bucket, plenty at position level. The specific
    # cell must be skipped -- a 1-sample "distribution" is the exact failure
    # the tail-pooling fix repaired in the simulator.
    train = pd.DataFrame(
        [{"fantasy_position": "WR", "adp_position_rank": 1, "prev_position_rank": 1,
          "fantasy_points": 999.0}]
        + [{"fantasy_position": "WR", "adp_position_rank": 40 + i,
            "prev_position_rank": 40 + i, "fantasy_points": 100.0 + i}
           for i in range(MIN_BUCKET_SAMPLES + 5)]
    )
    test = pd.DataFrame([{"fantasy_position": "WR", "adp_position_rank": 1,
                          "prev_position_rank": 1, "fantasy_points": 0.0}])

    predicted = predict_empirical_quantiles(train, test, use_prior=False)

    # Never the lone 999.0 outlier.
    assert predicted[0, QUANTILES.index(0.50)] < 500.0


def test_bucket_index_accumulates_at_every_level_of_the_chain():
    train = pd.DataFrame(
        [{"fantasy_position": "WR", "adp_position_rank": 2, "prev_position_rank": 3,
          "fantasy_points": 200.0}]
    )

    index = _build_bucket_index(train, use_prior=True)

    assert index[("WR",)] == [200.0]
    assert any(len(k) == 3 for k in index)


def test_empirical_quantiles_are_monotonic():
    train = pd.DataFrame(
        [{"fantasy_position": "WR", "adp_position_rank": 5, "prev_position_rank": 5,
          "fantasy_points": float(v)} for v in range(50, 350, 5)]
    )
    test = pd.DataFrame([{"fantasy_position": "WR", "adp_position_rank": 5,
                          "prev_position_rank": 5, "fantasy_points": 0.0}])

    predicted = predict_empirical_quantiles(train, test, use_prior=False)

    assert np.all(np.diff(predicted[0]) >= 0)
