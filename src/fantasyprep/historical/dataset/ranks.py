"""Historical finish ranks -- what "the WR12 season" actually was, per year.

Ranked on `fantasy_points` under *our* league scoring, never on the source's
`fantasy_points_ppr` (4-point passing TDs -- see canonical.py). The distinction
is not cosmetic at quarterback: a 6-point-passing-TD league reorders the QB
board relative to a 4-point one, and QB finish rank is exactly the kind of thing
a future model would condition on.

METHODOLOGY, since ranking involves real choices:

- **Ties** are ranked "min" (two players tied for 5th are both QB5, next is
  QB7), the same convention fantasy sites use. Exact ties on a two-decimal point
  total are rare but real.
- **Rank is per season and per fantasy position** (fullbacks ranked as running
  backs), computed over every player at that position who recorded a regular
  season -- not just drafted ones. That's deliberate: replacement level is
  defined by who was actually available, and restricting the pool to drafted
  players would bias the tail.
- **PPG ranks come in two variants.** The raw one ranks everybody, which lets a
  two-game cameo outrank a full season. That's real data but useless as a
  ranking, so `position_rank_ppg_qualified` re-ranks only players clearing
  `MIN_GAMES_FOR_PPG_RANK`, leaving everyone else NaN. Both are kept rather
  than picking one, because which is right depends on the question being asked.
- **Percentiles** are within (season, fantasy position), 1.0 = best, so they're
  comparable across seasons of differing player-pool size.
"""
from __future__ import annotations

import pandas as pd

# Half a 16/17-game season. A floor has to be *some* number; this one is high
# enough to exclude cameo seasons and low enough to keep genuinely productive
# players who missed time, which is the population we most want represented.
MIN_GAMES_FOR_PPG_RANK = 8

RANK_COLUMNS = (
    "overall_rank",
    "position_rank",
    "position_rank_ppg",
    "position_rank_ppg_qualified",
    "position_percentile",
)


def add_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """Add finish-rank columns to a canonical (or skill-filtered) frame."""
    df = df.copy()

    df["overall_rank"] = (
        df.groupby("season")["fantasy_points"].rank(method="min", ascending=False).astype("Int64")
    )
    df["position_rank"] = (
        df.groupby(["season", "fantasy_position"])["fantasy_points"]
        .rank(method="min", ascending=False)
        .astype("Int64")
    )
    df["position_rank_ppg"] = (
        df.groupby(["season", "fantasy_position"])["fantasy_points_per_game"]
        .rank(method="min", ascending=False)
        .astype("Int64")
    )

    qualified = df["games"] >= MIN_GAMES_FOR_PPG_RANK
    df["position_rank_ppg_qualified"] = (
        df.where(qualified)
        .groupby(["season", "fantasy_position"])["fantasy_points_per_game"]
        .rank(method="min", ascending=False)
        .astype("Int64")
    )

    # 1.0 = best in that season/position pool, so cross-season comparisons don't
    # get distorted by pool size (a 2024 WR pool is bigger than a 1999 one).
    df["position_percentile"] = (
        df.groupby(["season", "fantasy_position"])["fantasy_points"]
        .rank(method="min", ascending=True, pct=True)
        .round(4)
    )

    return df
