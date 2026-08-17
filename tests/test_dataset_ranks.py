"""Rank methodology: ties, per-position pools, and the PPG games floor."""
from __future__ import annotations

import pandas as pd

from fantasyprep.historical.dataset.ranks import MIN_GAMES_FOR_PPG_RANK, add_ranks


def _frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["fantasy_points_per_game"] = df["fantasy_points"] / df["games"]
    return df


def test_position_rank_is_by_points_within_season_and_position():
    df = _frame(
        [
            {"season": 2020, "fantasy_position": "WR", "fantasy_points": 300.0, "games": 16},
            {"season": 2020, "fantasy_position": "WR", "fantasy_points": 200.0, "games": 16},
            {"season": 2020, "fantasy_position": "RB", "fantasy_points": 250.0, "games": 16},
        ]
    )

    ranked = add_ranks(df)

    assert ranked["position_rank"].tolist() == [1, 2, 1]
    # The RB is 2nd overall on points but WR1 at his own position.
    assert ranked["overall_rank"].tolist() == [1, 3, 2]


def test_ranks_are_scoped_per_season():
    df = _frame(
        [
            {"season": 2020, "fantasy_position": "WR", "fantasy_points": 300.0, "games": 16},
            {"season": 2021, "fantasy_position": "WR", "fantasy_points": 100.0, "games": 16},
        ]
    )

    ranked = add_ranks(df)

    # A weak season still ranks 1st if it's the only one that year.
    assert ranked["position_rank"].tolist() == [1, 1]


def test_ties_use_min_ranking():
    df = _frame(
        [
            {"season": 2020, "fantasy_position": "WR", "fantasy_points": 200.0, "games": 16},
            {"season": 2020, "fantasy_position": "WR", "fantasy_points": 200.0, "games": 16},
            {"season": 2020, "fantasy_position": "WR", "fantasy_points": 100.0, "games": 16},
        ]
    )

    ranked = add_ranks(df)

    # Two tied for 1st, next is 3rd -- the convention fantasy sites use.
    assert ranked["position_rank"].tolist() == [1, 1, 3]


def test_qualified_ppg_rank_excludes_short_seasons():
    df = _frame(
        [
            # A two-game cameo with the best per-game average in the pool.
            {"season": 2020, "fantasy_position": "WR", "fantasy_points": 60.0, "games": 2},
            {"season": 2020, "fantasy_position": "WR", "fantasy_points": 240.0, "games": 16},
        ]
    )

    ranked = add_ranks(df)

    # The raw PPG rank crowns the cameo (30.0/gm vs 15.0/gm) -- real, but useless.
    assert ranked["position_rank_ppg"].tolist() == [1, 2]
    # The qualified variant leaves him out entirely rather than reranking him low.
    assert pd.isna(ranked["position_rank_ppg_qualified"].iloc[0])
    assert ranked["position_rank_ppg_qualified"].iloc[1] == 1
    assert MIN_GAMES_FOR_PPG_RANK == 8


def test_position_percentile_is_one_for_the_best_player():
    df = _frame(
        [
            {"season": 2020, "fantasy_position": "WR", "fantasy_points": 300.0, "games": 16},
            {"season": 2020, "fantasy_position": "WR", "fantasy_points": 200.0, "games": 16},
            {"season": 2020, "fantasy_position": "WR", "fantasy_points": 100.0, "games": 16},
        ]
    )

    ranked = add_ranks(df)

    assert ranked["position_percentile"].iloc[0] == 1.0
    assert ranked["position_percentile"].iloc[2] < ranked["position_percentile"].iloc[1]
