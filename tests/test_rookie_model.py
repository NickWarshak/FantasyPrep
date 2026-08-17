"""Rookie-specialist arm: feature scope and eligibility."""
from __future__ import annotations

import pandas as pd

from fantasyprep.research.rookie_model import ARMS, ROOKIE_FEATURES


def test_rookie_features_exclude_all_prior_production():
    # A rookie's prior-production columns are structurally absent, not merely
    # unobserved. Including them would feed the model a column of imputed
    # medians dressed up as data.
    assert not any(f.startswith("prev_") for f in ROOKIE_FEATURES)


def test_rookie_features_keep_what_a_rookie_actually_has():
    assert {"adp", "draft_pick", "age"} <= set(ROOKIE_FEATURES)


def test_incumbent_is_included_as_a_reference_arm():
    # The specialist has to be measured against what production already does,
    # not only against the other new model.
    assert "adp_bucket" in ARMS
    assert "shared_profile" in ARMS
    assert "rookie_specialist" in ARMS
