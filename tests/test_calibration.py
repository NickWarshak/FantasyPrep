"""Quantile recalibration: PIT, the median anchor, and the smooth correction."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fantasyprep.research.distribution_benchmark import QUANTILES
from fantasyprep.research.calibration import (
    anchor_median,
    assign_tier,
    fit_recalibration,
    fit_smooth_recalibration,
)


def test_uniform_pit_yields_an_identity_mapping():
    # Perfectly calibrated distributions produce uniform PIT values, and a
    # correction fitted on them must do essentially nothing.
    pit = np.linspace(0.0, 1.0, 2000)

    mapping = fit_recalibration(pit)

    for q in QUANTILES:
        assert mapping[q] == pytest.approx(q, abs=0.02)


def test_compressed_tails_produce_an_outward_correction():
    # PIT piled up at the ends means actual outcomes keep landing outside the
    # stated interval -- the distribution is too narrow. Only 87% of outcomes
    # fall below the nominal p90, so to actually cover 90% the correction must
    # read at a HIGHER raw level, pushing the emitted quantile outward.
    rng = np.random.default_rng(0)
    pit = np.clip(rng.normal(0.5, 0.45, 4000), 0, 1)

    mapping = fit_recalibration(pit)

    assert mapping[0.90] > 0.90  # reach further out to cover more
    assert mapping[0.10] < 0.10


def test_too_little_data_falls_back_to_the_identity():
    # A thin segment must degrade to the incumbent, never to a noisy correction.
    assert fit_recalibration(np.array([0.2, 0.5, 0.8])) == {q: q for q in QUANTILES}


def test_anchor_median_pins_the_median():
    mapping = {0.05: 0.02, 0.10: 0.05, 0.25: 0.18, 0.50: 0.42,
               0.75: 0.70, 0.90: 0.86, 0.95: 0.93}

    anchored = anchor_median(mapping)

    assert anchored[0.50] == 0.50


def test_anchor_median_keeps_the_mapping_monotonic():
    mapping = {0.05: 0.02, 0.10: 0.05, 0.25: 0.18, 0.50: 0.42,
               0.75: 0.70, 0.90: 0.86, 0.95: 0.93}

    anchored = anchor_median(mapping)
    levels = [anchored[q] for q in sorted(mapping)]

    assert levels == sorted(levels)


def test_anchor_median_stays_inside_the_unit_interval():
    mapping = {q: 0.99 for q in QUANTILES} | {0.50: 0.01}

    anchored = anchor_median(mapping)

    assert all(0.0 < v < 1.0 for v in anchored.values())


def test_smooth_correction_varies_with_rank():
    # Early ranks are badly miscalibrated, deep ranks are fine. The smooth
    # correction must reflect that WITHOUT either group being binned away.
    rng = np.random.default_rng(1)
    early_ranks = rng.uniform(1, 12, 800)
    deep_ranks = rng.uniform(40, 60, 800)
    early_pit = np.clip(rng.normal(0.5, 0.5, 800), 0, 1)   # heavily miscalibrated
    deep_pit = rng.uniform(0, 1, 800)                       # well calibrated

    pit = np.concatenate([early_pit, deep_pit])
    ranks = np.concatenate([early_ranks, deep_ranks])

    early = fit_smooth_recalibration(pit, ranks, target_rank=3.0)
    deep = fit_smooth_recalibration(pit, ranks, target_rank=50.0)

    # The early correction must reach further into the tail than the deep one.
    assert early[0.90] > deep[0.90]


def test_smooth_correction_uses_every_observation():
    # The point of kernel weighting: no cell can be empty, so even a rank with
    # no nearby training data still gets a usable mapping rather than a
    # fallback to identity.
    rng = np.random.default_rng(2)
    pit = np.clip(rng.normal(0.5, 0.45, 500), 0, 1)
    ranks = rng.uniform(1, 20, 500)

    far = fit_smooth_recalibration(pit, ranks, target_rank=200.0)

    assert set(far) == set(QUANTILES)
    assert all(0.0 < v < 1.0 for v in far.values())


def test_smooth_correction_anchors_the_median_too():
    rng = np.random.default_rng(3)
    pit = np.clip(rng.normal(0.6, 0.4, 500), 0, 1)
    ranks = rng.uniform(1, 30, 500)

    assert fit_smooth_recalibration(pit, ranks, 10.0)[0.50] == 0.50


def test_tier_assignment_matches_the_diagnosis_boundaries():
    tiers = assign_tier(pd.Series([1, 6, 7, 12, 13, 24, 25, 48, 49, 500]))

    assert list(tiers.astype(str)) == [
        "1-6", "1-6", "7-12", "7-12", "13-24", "13-24", "25-48", "25-48", "49+", "49+"
    ]
