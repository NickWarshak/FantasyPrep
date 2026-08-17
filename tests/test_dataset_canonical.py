"""Canonical-table guarantees, above all the scoring ones.

The headline claim in docs/HISTORICAL_DATA_AUDIT.md is that the source's
`fantasy_points_ppr` column uses 4-point passing touchdowns while our league
uses 6. Everything downstream depends on that being right, so it's pinned here
rather than left as a finding in a document.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fantasyprep.historical.dataset.canonical import (
    _safe_divide,
    build_canonical,
    fantasy_position,
    skill_players,
)
from fantasyprep.historical.sources.nfl_stats import compute_points
from fantasyprep.league.settings import ScoringSettings, default_settings
from tests.test_dataset_loader import FrameSource, _row

SCORING = default_settings().scoring


def _qb_row(**overrides) -> dict:
    """A quarterback line with its nflverse points precomputed at 4pt per
    passing TD -- 4000 * 0.04 + 30 * 4 + 10 * -2 = 160 + 120 - 20 = 260."""
    row = _row(
        player_id="00-0000010",
        player_display_name="Test QB",
        position="QB",
        position_group="QB",
        passing_yards=4000,
        passing_tds=30,
        interceptions=10,
        fantasy_points_ppr=260.0,
        targets=0,
        receptions=0,
        receiving_yards=0,
        receiving_tds=0,
        rushing_yards=0,
        rushing_tds=0,
        carries=0,
    )
    row.update(overrides)
    return row


def test_source_column_is_four_point_passing_tds():
    # The finding the whole audit turns on, verified at 0.00 delta across all
    # 13,415 real rows and pinned here on a hand-computed line.
    row = pd.Series(_qb_row())

    at_four = compute_points(row, ScoringSettings(pass_td=4.0))

    assert at_four == pytest.approx(row["fantasy_points_ppr"])


def test_our_scoring_differs_by_exactly_two_points_per_passing_td():
    row = pd.Series(_qb_row())

    ours = compute_points(row, SCORING)
    theirs = compute_points(row, ScoringSettings(pass_td=4.0))

    assert SCORING.pass_td == 6.0
    assert ours - theirs == pytest.approx(2 * row["passing_tds"])


def test_canonical_scores_with_our_settings_and_keeps_source_as_crosscheck():
    canonical = build_canonical(FrameSource(pd.DataFrame([_qb_row()])))

    # Our column is the outcome variable; theirs is retained under a name that
    # makes its provenance impossible to misread at a call site.
    assert canonical["fantasy_points"].iloc[0] == pytest.approx(320.0)  # 260 + 2*30
    assert canonical["fantasy_points_nflverse_ppr"].iloc[0] == pytest.approx(260.0)
    assert "fantasy_points_ppr" not in canonical.columns


def test_per_game_is_nan_when_games_is_zero():
    canonical = build_canonical(FrameSource(pd.DataFrame([_qb_row(games=0)])))

    # Not 0.0 -- "no basis for a rate" is a different claim from "the rate is 0".
    assert pd.isna(canonical["fantasy_points_per_game"].iloc[0])


def test_per_game_divides_by_games_played():
    canonical = build_canonical(FrameSource(pd.DataFrame([_qb_row(games=10)])))

    assert canonical["fantasy_points_per_game"].iloc[0] == pytest.approx(32.0)


def test_fullbacks_map_to_rb_without_losing_source_position():
    mapped = fantasy_position(pd.Series(["FB", "HB", "RB", "WR", "QB"]))

    assert mapped.tolist() == ["RB", "RB", "RB", "WR", "QB"]


def test_canonical_preserves_source_position_alongside_fantasy_position():
    canonical = build_canonical(
        FrameSource(pd.DataFrame([_qb_row(position="FB", position_group="RB")]))
    )

    assert canonical["position"].iloc[0] == "FB"
    assert canonical["fantasy_position"].iloc[0] == "RB"


def test_skill_players_includes_fullbacks_and_excludes_punters():
    df = pd.DataFrame(
        {
            "fantasy_position": ["QB", "RB", "WR", "TE", "P", "CB"],
            "player_id": list("abcdef"),
        }
    )

    assert skill_players(df)["fantasy_position"].tolist() == ["QB", "RB", "WR", "TE"]


def test_safe_divide_returns_nan_for_zero_and_missing_denominators():
    result = _safe_divide(pd.Series([10.0, 10.0, 10.0]), pd.Series([2.0, 0.0, None]))

    assert result.iloc[0] == pytest.approx(5.0)
    assert pd.isna(result.iloc[1])
    assert pd.isna(result.iloc[2])


def test_player_id_is_stable_across_seasons():
    source = FrameSource(
        pd.DataFrame(
            [
                _qb_row(season=2020, passing_yards=4000),
                _qb_row(season=2021, passing_yards=3000),
            ]
        )
    )

    canonical = build_canonical(source)

    assert canonical["player_id"].nunique() == 1
    assert sorted(canonical["season"]) == [2020, 2021]
