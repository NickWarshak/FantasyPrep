"""ADP join: the one place in this pipeline that can't use a player id.

Every other source shares the gsis `player_id`; FFC returns names only. A
silent failure here would make ADP look less informative than it is, and the
benchmark this feeds decides whether acquiring more ADP is worth doing -- so a
plumbing bug would produce exactly the wrong strategic conclusion.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fantasyprep.historical.dataset.market import (
    MARKET_FEATURE_COLUMNS,
    _drop_ambiguous_names,
    attach_adp,
)
from fantasyprep.historical.dataset.features import PRE_SEASON_COLUMNS


def _adp(**overrides) -> dict:
    row = {
        "season": 2020,
        "join_name": "justin jefferson",
        "fantasy_position": "WR",
        "adp": 45.0,
        "adp_position_rank": 20,
        "adp_stdev": 8.0,
    }
    row.update(overrides)
    return row


def _feature(name: str = "Justin Jefferson", season: int = 2020, position: str = "WR") -> dict:
    return {
        "player_id": "00-0000001",
        "player_name": name,
        "season": season,
        "fantasy_position": position,
        "fantasy_points": 250.0,
    }


def test_adp_attaches_on_normalized_name_and_position():
    frame, report = attach_adp(pd.DataFrame([_feature()]), adp=pd.DataFrame([_adp()]))

    assert frame["adp"].iloc[0] == 45.0
    assert frame["adp_position_rank"].iloc[0] == 20
    assert bool(frame["has_adp"].iloc[0]) is True
    assert report["overall_match_rate"] == 1.0


def test_name_normalization_bridges_punctuation_and_suffixes():
    # "Ja'Marr Chase" vs "JaMarr Chase", "Odell Beckham Jr." vs "Odell Beckham".
    frame, _ = attach_adp(
        pd.DataFrame([_feature(name="Odell Beckham Jr.")]),
        adp=pd.DataFrame([_adp(join_name="odell beckham")]),
    )

    assert bool(frame["has_adp"].iloc[0]) is True


def test_player_without_adp_keeps_his_row():
    # "The market never drafted this player" is information, not absence of it.
    frame, _ = attach_adp(
        pd.DataFrame([_feature(name="Some Deep Bench Guy")]), adp=pd.DataFrame([_adp()])
    )

    assert len(frame) == 1
    assert bool(frame["has_adp"].iloc[0]) is False
    assert pd.isna(frame["adp"].iloc[0])
    assert frame["fantasy_points"].iloc[0] == 250.0


def test_adp_does_not_leak_across_seasons():
    frame, _ = attach_adp(
        pd.DataFrame([_feature(season=2021)]), adp=pd.DataFrame([_adp(season=2020)])
    )

    assert bool(frame["has_adp"].iloc[0]) is False


def test_adp_does_not_leak_across_positions():
    frame, _ = attach_adp(
        pd.DataFrame([_feature(position="RB")]), adp=pd.DataFrame([_adp(fantasy_position="WR")])
    )

    assert bool(frame["has_adp"].iloc[0]) is False


def test_colliding_names_are_dropped_not_guessed():
    # Real case: two different Mike Williamses (Tampa Bay and Seattle) were
    # active receivers in 2010-11, with ADPs 114 picks apart. Assigning one
    # player's ADP to the other would inject a confidently-wrong market signal
    # into the exact benchmark meant to evaluate the market signal.
    ambiguous = pd.DataFrame(
        [
            _adp(join_name="mike williams", adp=42.1),
            _adp(join_name="mike williams", adp=156.0),
            _adp(join_name="justin jefferson", adp=45.0),
        ]
    )

    kept, report = _drop_ambiguous_names(ambiguous)

    assert report["n_entries_dropped"] == 2
    assert report["names"] == ["mike williams"]
    assert kept["join_name"].tolist() == ["justin jefferson"]


def test_ambiguous_player_falls_through_to_no_adp():
    frame, report = attach_adp(
        pd.DataFrame([_feature(name="Mike Williams")]),
        adp=pd.DataFrame(
            [
                _adp(join_name="mike williams", adp=42.1),
                _adp(join_name="mike williams", adp=156.0),
            ]
        ),
    )

    # Abstaining beats guessing: we genuinely don't know which player this is.
    assert bool(frame["has_adp"].iloc[0]) is False
    assert report["ambiguous_names_dropped"]["n_entries_dropped"] == 2


def test_duplicate_join_keys_would_raise_rather_than_fan_out():
    # The m:1 validation is a tripwire, not decoration -- if _drop_ambiguous_names
    # ever stops working, the merge must fail loudly instead of silently
    # duplicating player-seasons.
    duplicated = pd.DataFrame([_adp(), _adp()])

    with pytest.raises(Exception):
        pd.DataFrame([_feature()]).assign(join_name="justin jefferson").merge(
            duplicated, on=["season", "join_name", "fantasy_position"],
            how="left", validate="m:1",
        )


def test_match_report_is_measured_from_the_adp_side():
    # Measuring from the feature side would flatter the join, since most
    # player-seasons legitimately have no ADP at all.
    features = pd.DataFrame(
        [_feature(), _feature(name="Deep Bench A"), _feature(name="Deep Bench B")]
    )

    _, report = attach_adp(features, adp=pd.DataFrame([_adp()]))

    assert report["adp_entries"] == 1
    assert report["matched"] == 1
    assert report["overall_match_rate"] == 1.0
    assert report["player_seasons_total"] == 3
    assert report["player_seasons_with_adp"] == 1


def test_market_columns_are_classified_pre_season():
    # An ADP is measured before the season it describes -- that is precisely
    # why it is a legal model input.
    assert set(MARKET_FEATURE_COLUMNS) <= PRE_SEASON_COLUMNS
