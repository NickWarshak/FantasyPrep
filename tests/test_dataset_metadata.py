"""Age/experience join: correctness, and that it stays leakage-safe.

Age is the highest-value feature the foundation gained, and it is also the one
most easily got subtly wrong -- an off-by-a-year age, or an experience figure
that reflects today rather than the row's season, would corrupt every
comparable-player query built on top.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fantasyprep.historical.dataset.features import PRE_SEASON_COLUMNS, outcome_columns
from fantasyprep.historical.dataset.metadata import (
    METADATA_FEATURE_COLUMNS,
    add_metadata,
    age_at_season_start,
    coverage_report,
)


def _metadata(**overrides) -> pd.DataFrame:
    row = {
        "gsis_id": "00-0000001",
        "birth_date": "1995-03-15",
        "rookie_season": 2017,
        "years_of_experience": 8,
        "draft_year": 2017,
        "draft_round": 1.0,
        "draft_pick": 5.0,
        "height": 72.0,
        "weight": 200.0,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _seasons(*seasons: int, player_id: str = "00-0000001") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"player_id": player_id, "season": s, "fantasy_position": "WR", "fantasy_points": 200.0}
            for s in seasons
        ]
    )


def test_age_is_computed_at_september_1_of_that_season():
    age = age_at_season_start(pd.Series(["1995-03-15"]), pd.Series([2020]))

    # 1995-03-15 -> 2020-09-01 is 25 years and ~170 days.
    assert age.iloc[0] == pytest.approx(25.46, abs=0.02)


def test_age_advances_by_one_per_season():
    joined = add_metadata(_seasons(2020, 2021, 2022), metadata=_metadata())

    ages = joined.sort_values("season")["age"].tolist()
    assert ages[1] - ages[0] == pytest.approx(1.0, abs=0.01)
    assert ages[2] - ages[1] == pytest.approx(1.0, abs=0.01)


def test_age_is_nan_when_birth_date_is_unknown():
    joined = add_metadata(_seasons(2020), metadata=_metadata(birth_date=None))

    # Never estimated from rookie year -- that would invent the variable.
    assert pd.isna(joined["age"].iloc[0])


def test_unmatched_player_keeps_his_row():
    # A left join, deliberately: a metadata gap must not delete a player from
    # the historical record.
    joined = add_metadata(_seasons(2020, player_id="00-0009999"), metadata=_metadata())

    assert len(joined) == 1
    assert pd.isna(joined["age"].iloc[0])
    assert joined["fantasy_points"].iloc[0] == 200.0


def test_seasons_since_rookie_year_uses_the_rows_own_season():
    joined = add_metadata(_seasons(2017, 2020), metadata=_metadata(rookie_season=2017))

    by_season = joined.set_index("season")["seasons_since_rookie_year"]
    assert by_season[2017] == 0
    assert by_season[2020] == 3


def test_career_to_date_experience_is_dropped_because_it_leaks():
    # `years_of_experience` reflects today, not the row's season -- a 2017 row
    # would carry an 8-year figure the 2017 season could not have known.
    joined = add_metadata(_seasons(2017), metadata=_metadata(years_of_experience=8))

    assert "years_of_experience" not in joined.columns


def test_undrafted_players_are_flagged_not_imputed():
    joined = add_metadata(
        _seasons(2020), metadata=_metadata(draft_pick=None, draft_round=None)
    )

    assert bool(joined["undrafted"].iloc[0]) is True
    # Left NaN rather than 0, which a model would read as "pick 0".
    assert pd.isna(joined["draft_pick"].iloc[0])


def test_drafted_players_are_not_flagged_undrafted():
    joined = add_metadata(_seasons(2020), metadata=_metadata(draft_pick=5.0))

    assert bool(joined["undrafted"].iloc[0]) is False
    assert joined["draft_pick"].iloc[0] == 5.0


def test_metadata_columns_are_all_classified_pre_season():
    # Fixed facts, so they need no `prev_` lag to be legal model inputs.
    assert set(METADATA_FEATURE_COLUMNS) <= PRE_SEASON_COLUMNS


def test_metadata_columns_are_not_outcomes():
    joined = add_metadata(_seasons(2020), metadata=_metadata())

    outcomes = outcome_columns(joined)
    for column in METADATA_FEATURE_COLUMNS:
        assert column not in outcomes


def test_coverage_report_counts_what_actually_joined():
    joined = add_metadata(_seasons(2020, 2021), metadata=_metadata())

    report = coverage_report(joined)
    assert report["rows"] == 2
    assert report["age_coverage"] == 1.0
    assert report["undrafted_share"] == 0.0
    assert report["age_by_position"]["WR"]["n"] == 2
