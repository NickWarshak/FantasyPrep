"""The canonical player-season table: one row per player_id x season.

Scoring here reuses `nfl_stats.compute_points` -- the same function the live
pipeline and the backtest already score with -- rather than reimplementing the
formula. That matters more than it looks: `compute_points` is the thing that
already had a real bug found in it (return TDs and 2pt conversions scoring
zero), and a second copy of the formula would be a second place for that class
of bug to live, silently disagreeing with the backtest.

WHY WE DON'T JUST USE THE SOURCE'S OWN `fantasy_points_ppr` COLUMN: it uses
4-point passing touchdowns. Our league uses 6. Verified exactly, not estimated
-- running `compute_points` with `ScoringSettings(pass_td=4.0)` reproduces that
column to 0.00 maximum absolute delta across all 13,415 regular-season
skill-position rows. So it's kept as `fantasy_points_nflverse_ppr`, an
independent cross-check on our arithmetic, and never as the outcome variable.
Under our real settings the gap is large where it matters: Peyton Manning's
2013 is 519.98 for us against 409.98 there, a 110-point difference that is
exactly his 55 passing touchdowns times two.
"""
from __future__ import annotations

import pandas as pd

from fantasyprep.historical.dataset.loader import PlayerSeasonSource, load_regular_season
from fantasyprep.historical.sources.nfl_stats import compute_points
from fantasyprep.league.settings import ScoringSettings, default_settings

# Positions that fantasy platforms treat as running backs. Kept as an explicit
# mapping into a *separate* column rather than an in-place overwrite, so the
# source's real position is never lost and the choice stays reversible.
FANTASY_POSITION_MAP = {"FB": "RB", "HB": "RB"}

# The positions any of this project's fantasy logic actually cares about.
# Deliberately applied downstream (ranks, distributions) rather than in the
# canonical table -- the table keeps punters and linemen so the audit can report
# honest coverage, and so a future kicker/DST decision isn't blocked by a filter
# baked in at the wrong layer.
SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

IDENTITY_COLUMNS = [
    "player_id",
    "player_name",
    "season",
    "position",
    "fantasy_position",
    "position_group",
    "recent_team",
    "games",
]

OUTCOME_COLUMNS = [
    "fantasy_points",
    "fantasy_points_per_game",
    "fantasy_points_nflverse_ppr",
]


def fantasy_position(position: pd.Series) -> pd.Series:
    return position.replace(FANTASY_POSITION_MAP)


def build_canonical(
    source: PlayerSeasonSource | None = None,
    scoring: ScoringSettings | None = None,
) -> pd.DataFrame:
    """One row per player-season, scored under *our* league settings.

    Every statistical column the source carries is preserved -- EPA, air yards,
    first downs, opportunity shares -- rather than trimmed to what today's
    model happens to use. Missing stays missing; nothing is imputed.
    """
    scoring = scoring or default_settings().scoring
    df = load_regular_season(source)

    df["fantasy_position"] = fantasy_position(df["position"])

    # The source's own column, renamed to make its provenance unmistakable at
    # every call site. It is NOT our scoring -- see the module docstring.
    df = df.rename(columns={"fantasy_points_ppr": "fantasy_points_nflverse_ppr"})
    # `fantasy_points` in the source is nflverse's *standard* (non-PPR) scoring;
    # we overwrite it with ours, which is what every downstream consumer means
    # by the name.
    df["fantasy_points"] = df.apply(lambda row: round(compute_points(row, scoring), 2), axis=1)
    df["fantasy_points_per_game"] = _safe_divide(df["fantasy_points"], df["games"]).round(3)

    stat_columns = [
        c
        for c in df.columns
        if c not in IDENTITY_COLUMNS + OUTCOME_COLUMNS + ["season_type", "player_display_name"]
    ]
    return df[IDENTITY_COLUMNS + OUTCOME_COLUMNS + stat_columns].reset_index(drop=True)


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Divide, yielding NaN where the denominator is zero or missing.

    A zero denominator means "we have no basis for this rate", which is not the
    same claim as "this rate is zero" -- filling it with 0 would invent a data
    point, and every rate in this pipeline goes through here for that reason.
    """
    denominator = pd.to_numeric(denominator, errors="coerce")
    numerator = pd.to_numeric(numerator, errors="coerce")
    return (numerator / denominator.where(denominator > 0)).astype(float)


def skill_players(df: pd.DataFrame) -> pd.DataFrame:
    """Rows at the four positions this project drafts, by fantasy position (so
    fullbacks count as running backs)."""
    return df[df["fantasy_position"].isin(SKILL_POSITIONS)].copy()
