"""Derived modeling features, with leakage prevention enforced in code.

THE LEAKAGE PROBLEM THIS MODULE EXISTS TO SOLVE

The whole point of the eventual player-outcome model is to be provable through
held-out historical backtests. That proof is worthless if a feature for season Y
was computed from season Y's results. The failure is quiet -- nothing crashes,
the backtest just reports an edge that doesn't exist -- so it can't be left to
reviewer discipline.

So every column is classified into exactly one of two frozen sets:

- `PRE_SEASON_COLUMNS`: knowable before season Y kicks off. Legal model inputs.
- `OUTCOME_COLUMNS`: season Y's own results. Target variables only.

`preseason_frame()` is the only sanctioned way to build a model input frame, and
tests assert the sets are disjoint and jointly exhaustive -- a new column that
nobody classified fails the suite rather than quietly defaulting to "safe".

TWO CLASSIFICATIONS THAT LOOK CONSERVATIVE AND ARE DELIBERATE:

- `recent_team` is an OUTCOME. A team is usually known in August, but this
  column holds the player's *last* team of the season, so for anyone traded in
  October it encodes something that hadn't happened at draft time. `prev_recent_team`
  carries the honest version.
- `yoy_fantasy_change` and `yoy_target_change` are OUTCOMES, despite reading like
  features. Season Y's change is (Y minus Y-1), so it contains Y. The usable
  form is the lagged one, `prev_yoy_fantasy_change` -- the change *into* the
  prior season, which was fully observable before season Y.
"""
from __future__ import annotations

import pandas as pd

from fantasyprep.historical.dataset.canonical import _safe_divide
from fantasyprep.historical.dataset.metadata import METADATA_FEATURE_COLUMNS

# Prior-season values worth carrying forward as model inputs. Ranks are included
# because "last year's WR8" is exactly the kind of coarse prior a market uses.
LAG_SOURCE_COLUMNS = (
    "fantasy_points",
    "fantasy_points_per_game",
    "games",
    "targets",
    "carries",
    "receptions",
    "receiving_yards",
    "rushing_yards",
    "target_share",
    "air_yards_share",
    "wopr",
    "position_rank",
    "position_percentile",
    "recent_team",
    "targets_per_game",
    "carries_per_game",
    "yards_per_target",
    "yards_per_carry",
    "catch_rate",
    "fantasy_points_per_opportunity",
    "yoy_fantasy_change",
)

PER_GAME_COLUMNS = (
    "targets_per_game",
    "carries_per_game",
    "receptions_per_game",
    "receiving_yards_per_game",
    "rushing_yards_per_game",
    "total_yards_per_game",
    "touchdowns_per_game",
)

EFFICIENCY_COLUMNS = (
    "yards_per_target",
    "yards_per_carry",
    "yards_per_reception",
    "catch_rate",
    "touchdown_rate",
    "fantasy_points_per_opportunity",
    "opportunities",
)

YOY_COLUMNS = ("yoy_fantasy_change", "yoy_target_change")

# Knowable before the season starts: who the player is, and everything that
# already happened in prior seasons.
#
# `METADATA_FEATURE_COLUMNS` (age, rookie season, draft capital) join in here
# without a `prev_` lag, unlike everything else. That's not an oversight: a
# birth date and a draft slot are fixed facts, so age *entering* season Y is
# fully knowable before season Y kicks off. Note what is deliberately NOT here
# -- the source's `years_of_experience` is a career-to-date figure reflecting
# today rather than the row's season, so it would leak; `metadata.py` computes
# `seasons_since_rookie_year` against the row's own season instead.
PRE_SEASON_COLUMNS = frozenset(
    {"player_id", "player_name", "season", "position", "fantasy_position", "position_group"}
    | {f"prev_{c}" for c in LAG_SOURCE_COLUMNS}
    | {"prev_season", "seasons_of_history"}
    | set(METADATA_FEATURE_COLUMNS)
)


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Per-game rates, efficiency ratios, and leakage-safe prior-season lags.

    Expects a canonical frame that has already been through `ranks.add_ranks`,
    since prior-season rank is one of the lags.
    """
    df = df.copy()

    total_tds = _sum_columns(df, ["rushing_tds", "receiving_tds", "passing_tds"])
    total_yards = _sum_columns(df, ["rushing_yards", "receiving_yards"])
    # "Opportunity" is touches plus targets -- the volume a player was actually
    # given, which is the part of production least dependent on efficiency luck.
    df["opportunities"] = _sum_columns(df, ["carries", "targets"])

    df["targets_per_game"] = _safe_divide(df["targets"], df["games"]).round(3)
    df["carries_per_game"] = _safe_divide(df["carries"], df["games"]).round(3)
    df["receptions_per_game"] = _safe_divide(df["receptions"], df["games"]).round(3)
    df["receiving_yards_per_game"] = _safe_divide(df["receiving_yards"], df["games"]).round(3)
    df["rushing_yards_per_game"] = _safe_divide(df["rushing_yards"], df["games"]).round(3)
    df["total_yards_per_game"] = _safe_divide(total_yards, df["games"]).round(3)
    df["touchdowns_per_game"] = _safe_divide(total_tds, df["games"]).round(3)

    # Every ratio goes through `_safe_divide`, so a player with zero targets gets
    # NaN catch rate rather than a fabricated 0.0.
    df["yards_per_target"] = _safe_divide(df["receiving_yards"], df["targets"]).round(3)
    df["yards_per_carry"] = _safe_divide(df["rushing_yards"], df["carries"]).round(3)
    df["yards_per_reception"] = _safe_divide(df["receiving_yards"], df["receptions"]).round(3)
    df["catch_rate"] = _safe_divide(df["receptions"], df["targets"]).round(4)
    df["touchdown_rate"] = _safe_divide(total_tds, df["opportunities"]).round(4)
    df["fantasy_points_per_opportunity"] = _safe_divide(
        df["fantasy_points"], df["opportunities"]
    ).round(3)

    return _add_lags(df)


def _sum_columns(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    present = [c for c in columns if c in df.columns]
    if not present:
        return pd.Series(pd.NA, index=df.index, dtype="Float64")
    return df[present].apply(pd.to_numeric, errors="coerce").sum(axis=1, min_count=1)


def _add_lags(df: pd.DataFrame) -> pd.DataFrame:
    """Attach prior-season values, but only from a genuinely adjacent season.

    A player who misses 2019 entirely gets NaN for his 2020 `prev_*` values, not
    2018's numbers quietly relabelled as "last year". Carrying a two-year-old
    season forward under a `prev_` name would misrepresent both the recency and
    the fact that he missed a year -- which is itself signal a model should see
    (via `seasons_of_history`) rather than have smoothed away.
    """
    df = df.sort_values(["player_id", "season"]).reset_index(drop=True)
    grouped = df.groupby("player_id", sort=False)

    df["prev_season"] = grouped["season"].shift(1).astype("Int64")
    contiguous = df["prev_season"] == (df["season"] - 1)

    for column in LAG_SOURCE_COLUMNS:
        if column not in df.columns:
            continue
        df[f"prev_{column}"] = grouped[column].shift(1).where(contiguous)

    # How many seasons of any history this player has, adjacent or not -- lets a
    # model distinguish a rookie (0) from a veteran returning after a lost year.
    df["seasons_of_history"] = grouped.cumcount().astype("Int64")

    df["yoy_fantasy_change"] = (df["fantasy_points"] - df["prev_fantasy_points"]).round(2)
    df["yoy_target_change"] = (df["targets"] - df["prev_targets"]).round(2)

    return df.sort_values(["season", "player_id"]).reset_index(drop=True)


def outcome_columns(df: pd.DataFrame) -> frozenset[str]:
    """Everything that isn't pre-season is, by construction, an outcome.

    Defined as the complement rather than as a second hand-maintained list, so
    the two sets can't drift apart and no column can fall through the gap: a new
    stat column is an outcome until someone deliberately adds it to
    `PRE_SEASON_COLUMNS`. Failing closed is the whole point.
    """
    return frozenset(df.columns) - PRE_SEASON_COLUMNS


def preseason_frame(df: pd.DataFrame, season: int) -> pd.DataFrame:
    """Model inputs for `season`: its rows, stripped to pre-season columns only.

    The sanctioned entry point for anything that will later be graded on a
    held-out season. Going around it -- slicing the full feature frame by hand --
    is how a backtest ends up quietly reporting an edge it didn't earn.
    """
    columns = [c for c in df.columns if c in PRE_SEASON_COLUMNS]
    return df[df["season"] == season][columns].reset_index(drop=True)


def target_frame(df: pd.DataFrame, season: int, target: str = "fantasy_points") -> pd.DataFrame:
    """The matching labels for `preseason_frame(df, season)`."""
    return df[df["season"] == season][["player_id", "season", target]].reset_index(drop=True)
