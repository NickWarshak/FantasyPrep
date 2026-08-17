"""Derived features, prior-season joins, and the leakage guarantee.

The leakage tests are the important ones. A leak doesn't crash anything -- it
just makes a future backtest report an edge that isn't real -- so the pre-season
/ outcome split is asserted here rather than trusted to review.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fantasyprep.historical.dataset.features import (
    LAG_SOURCE_COLUMNS,
    PRE_SEASON_COLUMNS,
    add_features,
    outcome_columns,
    preseason_frame,
    target_frame,
)
from fantasyprep.historical.dataset.ranks import add_ranks


def _season(player_id: str, season: int, **overrides) -> dict:
    row = {
        "player_id": player_id,
        "player_name": f"Player {player_id}",
        "season": season,
        "position": "WR",
        "fantasy_position": "WR",
        "position_group": "WR",
        "recent_team": "KC",
        "games": 16,
        "fantasy_points": 200.0,
        "targets": 100,
        "receptions": 70,
        "receiving_yards": 900,
        "receiving_tds": 6,
        "carries": 0,
        "rushing_yards": 0,
        "rushing_tds": 0,
        "passing_tds": 0,
        "target_share": 0.25,
        "air_yards_share": 0.3,
        "wopr": 0.6,
    }
    row.update(overrides)
    return row


def _build(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["fantasy_points_per_game"] = df["fantasy_points"] / df["games"]
    return add_features(add_ranks(df))


# --- derived values ---------------------------------------------------------


def test_per_game_and_efficiency_math():
    features = _build([_season("a", 2020, games=10, targets=80, receptions=50,
                               receiving_yards=800, fantasy_points=150.0)])
    row = features.iloc[0]

    assert row["targets_per_game"] == pytest.approx(8.0)
    assert row["yards_per_target"] == pytest.approx(10.0)
    assert row["catch_rate"] == pytest.approx(0.625)
    assert row["opportunities"] == 80  # carries + targets
    assert row["fantasy_points_per_opportunity"] == pytest.approx(1.875)


def test_efficiency_is_nan_rather_than_zero_when_denominator_is_zero():
    features = _build([_season("a", 2020, targets=0, receptions=0, receiving_yards=0)])
    row = features.iloc[0]

    # A player with no targets has an *unknown* catch rate, not a 0% one.
    assert pd.isna(row["catch_rate"])
    assert pd.isna(row["yards_per_target"])


def test_missing_source_values_survive_as_nan():
    features = _build([_season("a", 2020, wopr=None, target_share=None)])

    assert pd.isna(features["wopr"].iloc[0])
    assert pd.isna(features["target_share"].iloc[0])


# --- prior-season joins -----------------------------------------------------


def test_prior_season_values_come_from_the_adjacent_season():
    features = _build(
        [
            _season("a", 2020, fantasy_points=100.0, targets=50),
            _season("a", 2021, fantasy_points=250.0, targets=120),
        ]
    )
    second = features[features["season"] == 2021].iloc[0]

    assert second["prev_fantasy_points"] == pytest.approx(100.0)
    assert second["prev_targets"] == pytest.approx(50)
    assert second["yoy_fantasy_change"] == pytest.approx(150.0)
    assert second["yoy_target_change"] == pytest.approx(70)


def test_first_season_has_no_prior_values():
    features = _build([_season("a", 2020)])

    assert pd.isna(features["prev_fantasy_points"].iloc[0])
    assert features["seasons_of_history"].iloc[0] == 0


def test_gap_year_does_not_carry_a_stale_season_forward():
    # A player who missed 2021 entirely. His 2022 "last year" must be NaN, not
    # 2020's numbers relabelled -- that would misstate both recency and the
    # fact that he lost a season.
    features = _build(
        [
            _season("a", 2020, fantasy_points=300.0),
            _season("a", 2022, fantasy_points=100.0),
        ]
    )
    later = features[features["season"] == 2022].iloc[0]

    assert pd.isna(later["prev_fantasy_points"])
    # ...but the fact that he has history is preserved.
    assert later["seasons_of_history"] == 1


def test_lags_do_not_leak_across_players():
    features = _build(
        [
            _season("a", 2020, fantasy_points=300.0),
            _season("b", 2021, fantasy_points=100.0),
        ]
    )
    player_b = features[features["player_id"] == "b"].iloc[0]

    assert pd.isna(player_b["prev_fantasy_points"])


def test_prior_position_rank_is_carried_forward():
    features = _build(
        [
            _season("a", 2020, fantasy_points=300.0),
            _season("b", 2020, fantasy_points=100.0),
            _season("a", 2021, fantasy_points=150.0),
        ]
    )
    a2021 = features[(features["player_id"] == "a") & (features["season"] == 2021)].iloc[0]

    assert a2021["prev_position_rank"] == 1


# --- leakage ----------------------------------------------------------------


def test_preseason_and_outcome_columns_are_disjoint_and_exhaustive():
    features = _build([_season("a", 2020), _season("a", 2021)])

    pre = PRE_SEASON_COLUMNS & set(features.columns)
    out = outcome_columns(features)

    assert pre & out == set()
    assert pre | out == set(features.columns)


def test_preseason_frame_contains_no_current_season_outcome():
    features = _build(
        [
            _season("a", 2020, fantasy_points=100.0, targets=50),
            _season("a", 2021, fantasy_points=999.0, targets=222),
        ]
    )

    inputs = preseason_frame(features, 2021)

    # None of 2021's own results may appear anywhere in the model inputs.
    assert "fantasy_points" not in inputs.columns
    assert "targets" not in inputs.columns
    assert "position_rank" not in inputs.columns
    assert not (inputs == 999.0).any().any()
    assert not (inputs == 222).any().any()
    # What *is* there is last year's, which was knowable before 2021 kicked off.
    assert inputs["prev_fantasy_points"].iloc[0] == pytest.approx(100.0)


def test_yoy_change_is_classified_as_an_outcome_not_a_feature():
    # It reads like a feature, but season Y's change contains season Y.
    assert "yoy_fantasy_change" not in PRE_SEASON_COLUMNS
    assert "prev_yoy_fantasy_change" in PRE_SEASON_COLUMNS


def test_recent_team_is_classified_as_an_outcome():
    # It holds the player's *last* team, so for a midseason trade it encodes
    # something that hadn't happened at draft time.
    assert "recent_team" not in PRE_SEASON_COLUMNS
    assert "prev_recent_team" in PRE_SEASON_COLUMNS


def test_unclassified_new_column_defaults_to_outcome():
    # Failing closed: an unreviewed column must never become a legal model input.
    features = _build([_season("a", 2020)])
    features["some_new_metric"] = 1.0

    assert "some_new_metric" in outcome_columns(features)
    assert "some_new_metric" not in preseason_frame(features, 2020).columns


def test_every_lag_source_has_a_matching_preseason_column():
    assert {f"prev_{c}" for c in LAG_SOURCE_COLUMNS} <= PRE_SEASON_COLUMNS


def test_target_frame_lines_up_with_preseason_frame():
    features = _build([_season("a", 2020), _season("a", 2021, fantasy_points=275.0)])

    inputs = preseason_frame(features, 2021)
    targets = target_frame(features, 2021)

    assert len(inputs) == len(targets) == 1
    assert inputs["player_id"].tolist() == targets["player_id"].tolist()
    assert targets["fantasy_points"].iloc[0] == pytest.approx(275.0)
