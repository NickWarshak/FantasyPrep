"""Weekly-volatility modelling: targets, joins, and leakage."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fantasyprep.research.variance_model import (
    FEATURES,
    MIN_WEEKS,
    TARGETS,
    attach_volatility,
)


def _volatility(rows):
    return pd.DataFrame(
        [{"join_name": n, "season": s, "weeks_played": 16,
          "weekly_mean": m, "weekly_stdev": sd, "weekly_cv": sd / m}
         for n, s, m, sd in rows]
    )


def _modeling(rows):
    return pd.DataFrame(
        [{"player_name": n, "season": s, "player_id": f"id-{n}"} for n, s in rows]
    )


def test_current_season_volatility_attaches_as_the_target():
    frame = attach_volatility(
        _modeling([("Alpha", 2021)]),
        _volatility([("alpha", 2021, 10.0, 5.0)]),
    )

    assert frame["weekly_stdev"].iloc[0] == pytest.approx(5.0)
    assert frame["weekly_cv"].iloc[0] == pytest.approx(0.5)


def test_prior_season_volatility_attaches_as_a_feature():
    frame = attach_volatility(
        _modeling([("Alpha", 2022)]),
        _volatility([("alpha", 2021, 10.0, 4.0)]),
    )

    # 2021's volatility is 2022's `prev_`, and 2022 itself has no target.
    assert frame["prev_weekly_stdev"].iloc[0] == pytest.approx(4.0)
    assert pd.isna(frame["weekly_stdev"].iloc[0])


def test_prior_join_does_not_reach_across_a_gap_year():
    frame = attach_volatility(
        _modeling([("Alpha", 2023)]),
        _volatility([("alpha", 2021, 10.0, 4.0)]),
    )

    # 2021 is two seasons back, so it must not become 2023's "last season".
    assert pd.isna(frame["prev_weekly_stdev"].iloc[0])


def test_prior_volatility_is_never_the_same_season():
    """The leakage check that matters: a player's own current-season volatility
    must never appear among his features."""
    frame = attach_volatility(
        _modeling([("Alpha", 2021), ("Alpha", 2022)]),
        _volatility([("alpha", 2021, 10.0, 4.0), ("alpha", 2022, 10.0, 9.0)]),
    )
    by_season = frame.set_index("season")

    assert by_season.loc[2022, "weekly_stdev"] == pytest.approx(9.0)
    assert by_season.loc[2022, "prev_weekly_stdev"] == pytest.approx(4.0)
    assert by_season.loc[2022, "prev_weekly_stdev"] != by_season.loc[2022, "weekly_stdev"]


def test_volatility_does_not_leak_between_players():
    frame = attach_volatility(
        _modeling([("Bravo", 2022)]),
        _volatility([("alpha", 2021, 10.0, 4.0)]),
    )

    assert pd.isna(frame["prev_weekly_stdev"].iloc[0])


def test_features_use_only_prior_season_volatility():
    # Current-season volatility is the target; including it would be circular.
    assert "weekly_stdev" not in FEATURES
    assert "weekly_cv" not in FEATURES
    assert "prev_weekly_stdev" in FEATURES
    assert "prev_weekly_cv" in FEATURES


def test_coefficient_of_variation_is_scale_free():
    """CV is the headline target precisely because raw stdev is mechanically
    larger for high scorers -- a model predicting raw stdev would largely be
    rediscovering ADP."""
    volatility = _volatility([("small", 2021, 5.0, 2.5), ("big", 2021, 50.0, 25.0)])

    # Ten times the scoring, ten times the stdev, identical consistency.
    assert volatility["weekly_stdev"].tolist() == [2.5, 25.0]
    assert volatility["weekly_cv"].tolist() == [0.5, 0.5]


def test_both_targets_are_declared():
    assert set(TARGETS) == {"weekly_stdev", "weekly_cv"}
    assert MIN_WEEKS >= 8


def _cached_frame(tmp_path, seasons):
    frame = pd.DataFrame(
        [{"join_name": "a", "season": s, "weeks_played": 16,
          "weekly_mean": 10.0, "weekly_stdev": 5.0, "weekly_cv": 0.5}
         for s in seasons]
    )
    path = tmp_path / "vol.parquet"
    frame.to_parquet(path, index=False)
    return path


def test_cache_is_used_when_it_covers_the_requested_seasons(tmp_path):
    from fantasyprep.research.variance_model import build_weekly_volatility

    path = _cached_frame(tmp_path, [2020, 2021, 2022])

    result = build_weekly_volatility([2020, 2021], cache_path=path)

    # Served from cache -- no network -- and trimmed to exactly what was asked.
    assert sorted(result["season"].unique()) == [2020, 2021]


def test_cache_is_not_honoured_when_it_lacks_a_requested_season(tmp_path, monkeypatch):
    """The quiet-wrongness guard: a caller asking for a season the cache does
    not contain must NOT silently receive the narrower cached range."""
    from fantasyprep.research import variance_model

    path = _cached_frame(tmp_path, [2020, 2021])
    rebuilt = {}

    def fake_weekly(season, scoring):
        rebuilt[season] = True
        return {"a": {w: 10.0 for w in range(1, 17)}}

    monkeypatch.setattr(variance_model.weekly_stats, "weekly_points_by_player", fake_weekly)

    variance_model.build_weekly_volatility([2020, 2021, 2022], cache_path=path)

    # It rebuilt rather than serving a cache that was missing 2022.
    assert 2022 in rebuilt
