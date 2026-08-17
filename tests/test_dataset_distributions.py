"""Bucket-study statistics: percentiles, bucket boundaries, and the
leakage-safe prior-rank pairing."""
from __future__ import annotations

import pandas as pd
import pytest

from fantasyprep.historical.dataset.distributions import (
    MAX_RANK,
    _bucket_label,
    bucket_study,
    prior_rank_pairs,
    summarize,
    width_comparison,
)


def test_summarize_reports_shape_not_just_centre():
    stats = summarize([float(v) for v in range(1, 101)])

    assert stats["n"] == 100
    assert stats["median"] == pytest.approx(50.5)
    assert stats["p10"] == pytest.approx(10.9)
    assert stats["p90"] == pytest.approx(90.1)
    assert stats["min"] == 1.0
    assert stats["max"] == 100.0


def test_summarize_reports_unknown_rather_than_zero_variance_for_one_sample():
    stats = summarize([42.0])

    # stdev None means "can't be computed", not "there is no variance".
    assert stats["n"] == 1
    assert stats["stdev"] is None
    assert stats["median"] == 42.0


def test_summarize_handles_empty_bucket():
    assert summarize([]) == {"n": 0}


def test_bucket_labels_match_the_simulator_convention():
    assert _bucket_label(0, 3) == "1-3"
    assert _bucket_label(1, 3) == "4-6"
    assert _bucket_label(0, 1) == "1"
    assert _bucket_label(2, 5) == "11-15"


def test_bucket_study_groups_ranks_by_width():
    pairs = {"WR": [(1, 300.0), (2, 280.0), (3, 260.0), (4, 200.0), (5, 190.0)]}

    study = bucket_study(pairs, widths=(3,))

    assert study["WR"]["width_3"]["1-3"]["n"] == 3
    assert study["WR"]["width_3"]["1-3"]["median"] == pytest.approx(280.0)
    assert study["WR"]["width_3"]["4-6"]["n"] == 2


def test_bucket_study_ignores_ranks_beyond_the_draftable_range():
    pairs = {"WR": [(1, 300.0), (MAX_RANK + 1, 10.0)]}

    study = bucket_study(pairs, widths=(1,))

    assert sum(b["n"] for b in study["WR"]["width_1"].values()) == 1


def test_width_comparison_trades_samples_against_structure():
    pairs = {"WR": [(rank, 300.0 - rank * 5.0) for rank in range(1, 31)]}

    comparison = width_comparison(bucket_study(pairs, widths=(1, 10)))

    narrow = comparison["WR"]["width_1"]
    wide = comparison["WR"]["width_10"]
    # Wider buckets buy samples per bucket and give up rank structure.
    assert wide["median_samples_per_bucket"] > narrow["median_samples_per_bucket"]
    assert wide["spread_of_bucket_medians"] < narrow["spread_of_bucket_medians"]


def test_prior_rank_pairs_skips_rows_without_a_prior_season():
    features = pd.DataFrame(
        [
            {"fantasy_position": "WR", "prev_position_rank": 5, "fantasy_points": 200.0},
            # A rookie: no prior rank, so no pair -- never a fabricated rank 0.
            {"fantasy_position": "WR", "prev_position_rank": None, "fantasy_points": 150.0},
        ]
    )

    pairs = prior_rank_pairs(features)

    assert pairs["WR"] == [(5, 200.0)]
