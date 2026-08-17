"""Loader guarantees: the REG filter, row uniqueness, name resolution, and
era masking. These are regression tests for the two traps documented in
docs/HISTORICAL_DATA_AUDIT.md -- both silently corrupt everything downstream,
so each gets an explicit test rather than relying on the build looking fine.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fantasyprep.historical.dataset.loader import (
    AIR_YARDS_ERA_START,
    load_regular_season,
    mask_uncollected_eras,
)


class FrameSource:
    """In-memory stand-in for the CSV -- keeps these tests offline and fast."""

    def __init__(self, df: pd.DataFrame):
        self.df = df

    def load(self) -> pd.DataFrame:
        return self.df.copy()


def _row(**overrides) -> dict:
    row = {
        "season": 2020,
        "season_type": "REG",
        "player_id": "00-0000001",
        "player_name": None,
        "player_display_name": "Test Player",
        "position": "WR",
        "position_group": "WR",
        "headshot_url": None,
        "games": 16,
        "recent_team": "KC",
        "targets": 100,
        "receiving_air_yards": 900,
        "air_yards_share": 0.3,
        "wopr": 0.6,
        "receiving_yards_after_catch": 400,
        "racr": 1.1,
        "pacr": None,
        "passing_air_yards": 0,
        "passing_yards_after_catch": 0,
        "fantasy_points_ppr": 200.0,
    }
    row.update(overrides)
    return row


def test_keeps_only_regular_season_rows():
    # The real file carries three views of every player-season; REG+POST would
    # double-count the player and inflate the total with playoff production.
    source = FrameSource(
        pd.DataFrame(
            [
                _row(season_type="REG", fantasy_points_ppr=200.0),
                _row(season_type="POST", fantasy_points_ppr=40.0),
                _row(season_type="REG+POST", fantasy_points_ppr=240.0),
            ]
        )
    )

    result = load_regular_season(source)

    assert len(result) == 1
    assert result["season_type"].tolist() == ["REG"]
    assert result["fantasy_points_ppr"].iloc[0] == 200.0


def test_raises_on_duplicate_player_seasons():
    # Silent de-duplication could throw away half a traded player's season, so
    # a genuine schema change must fail loudly instead.
    source = FrameSource(pd.DataFrame([_row(recent_team="KC"), _row(recent_team="SF")]))

    with pytest.raises(ValueError, match="duplicate"):
        load_regular_season(source)


def test_uses_display_name_when_player_name_is_null():
    # player_name is 52% null in the real file.
    source = FrameSource(pd.DataFrame([_row(player_name=None, player_display_name="Real Name")]))

    result = load_regular_season(source)

    assert result["player_name"].iloc[0] == "Real Name"


def test_drops_orphan_rows_with_no_resolvable_name():
    rows = [_row(player_id=f"00-000000{i}", player_display_name=f"P{i}") for i in range(2000)]
    rows.append(_row(player_id="00-0009999", player_display_name=None, position=None))
    result = load_regular_season(FrameSource(pd.DataFrame(rows)))

    assert len(result) == 2000
    assert result["player_name"].notna().all()


def test_raises_when_unnamed_rows_are_systemic():
    # One orphan in 15,000 is noise; half the file is a broken source.
    source = FrameSource(
        pd.DataFrame(
            [
                _row(player_id="00-0000001", player_display_name="Named"),
                _row(player_id="00-0000002", player_display_name=None),
            ]
        )
    )

    with pytest.raises(ValueError, match="no usable player name"):
        load_regular_season(source)


def test_masks_fabricated_pre_2006_air_yards():
    # The trap: these are ZERO before 2006, not null, so they survive a null
    # check while carrying no information at all.
    df = pd.DataFrame(
        [
            _row(season=2003, receiving_air_yards=0, wopr=None, receiving_yards_after_catch=12),
            _row(season=2010, receiving_air_yards=900, wopr=0.6, receiving_yards_after_catch=400),
        ]
    )

    masked = mask_uncollected_eras(df)

    pre, post = masked.iloc[0], masked.iloc[1]
    assert pd.isna(pre["receiving_air_yards"])
    assert pd.isna(pre["receiving_yards_after_catch"])
    assert pd.isna(pre["racr"])
    assert post["receiving_air_yards"] == 900
    assert post["wopr"] == 0.6


def test_era_masking_boundary_is_inclusive_of_2006():
    df = pd.DataFrame(
        [
            _row(season=AIR_YARDS_ERA_START - 1, receiving_air_yards=500),
            _row(season=AIR_YARDS_ERA_START, receiving_air_yards=500),
        ]
    )

    masked = mask_uncollected_eras(df)

    assert pd.isna(masked.iloc[0]["receiving_air_yards"])
    assert masked.iloc[1]["receiving_air_yards"] == 500


def test_missing_values_are_not_silently_zero_filled():
    source = FrameSource(pd.DataFrame([_row(season=2020, target_share=None, wopr=None)]))

    result = load_regular_season(source)

    assert pd.isna(result["wopr"].iloc[0])
