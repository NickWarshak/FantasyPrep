"""Load raw nflverse player-season rows, from a frozen CSV or a live pull.

The frozen CSV (`data/historical/raw/combined_nfl_seasons_1999_2024.csv`) is
the default source of record: it covers 1999-2024, which is 11 more seasons
than `historical/outcomes.py`'s live 2010-2024 default, and being frozen it
makes anything built on top of it bit-for-bit reproducible across machines and
calendar days -- unlike a live pull, which drifts as nflverse revises upstream
data (the same drift already documented for FFC ADP in ROADMAP.md).

It is *the same upstream data* the existing pipeline already pulls via
`nfl_data_py.import_seasonal_data` (see historical/sources/nfl_stats.py), so
`LivePlayerSeasonSource` exists as a drop-in swap for when 2025+ is wanted --
the loader boundary is here precisely so nothing downstream has to change.

THREE THINGS ABOUT THE RAW DATA THAT WILL SILENTLY CORRUPT ANYTHING BUILT ON
IT IF IGNORED -- all three verified against the real file, see
docs/HISTORICAL_DATA_AUDIT.md:

1. `season_type` has THREE values, not one: REG, POST, and REG+POST. Every
   player-season appears in up to three rows, and REG+POST differs from REG for
   3,105 of them. Reading the file without filtering triple-counts players and
   inflates season totals. We keep REG only -- matching `nfl_stats.py`'s
   existing `s_type="REG"`.
2. `player_name` is 52% null; `player_display_name` is the real name column.
3. The air-yards-era columns are hard ZEROS, not nulls, before 2006 (nflfastR's
   air-yards charting starts in 2006). They pass a null check while being
   entirely fabricated -- a model trained across 1999-2024 would learn "nobody
   had air yards in 2003". `mask_uncollected_eras` converts them to NaN so
   they're honestly missing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pandas as pd

DEFAULT_CSV_PATH = Path("data/historical/raw/combined_nfl_seasons_1999_2024.csv")

# nflfastR's air-yards charting data begins in 2006. Everything derived from it
# is fabricated (zero or null) before then -- see the module docstring.
AIR_YARDS_ERA_START = 2006

# Columns that are entirely dead (all zero and/or null) for every player before
# AIR_YARDS_ERA_START, verified column-by-column against the real file rather
# than assumed from the nflfastR changelog.
#
# `receiving_yards_after_catch` is in here for a slightly different reason: it
# isn't uniformly zero pre-2006, it's *partial junk* -- 85 of 485 players have a
# nonzero value in 1999 and roughly 1-2% do in 2000-2005, with a season maximum
# of 185 yards versus 670 in 2006. That's not a real signal, it's fragmentary
# charting, and it's more dangerous than an honest zero because it looks alive.
UNCOLLECTED_BEFORE_2006 = (
    "passing_air_yards",
    "passing_yards_after_catch",
    "pacr",
    "receiving_air_yards",
    "receiving_yards_after_catch",
    "racr",
    "air_yards_share",
    "wopr",
)

# All-null in the source; carrying them forward implies data we don't have.
# `player_name` is dropped only after being replaced by `player_display_name`.
DROP_COLUMNS = ("headshot_url",)

# Rows with no resolvable name are dropped, but only up to this share -- above
# it, name resolution itself has broken and the build should fail rather than
# quietly discard players. The real file sits at 1 row in 15,102 (0.007%).
MAX_UNNAMED_SHARE = 0.001


class PlayerSeasonSource(Protocol):
    """Where raw player-season rows come from. The swap point between the
    frozen CSV and a live nflverse pull."""

    def load(self) -> pd.DataFrame: ...


class CsvPlayerSeasonSource:
    """The frozen 1999-2024 CSV. Default, and immutable -- never written to."""

    def __init__(self, path: Path | str = DEFAULT_CSV_PATH):
        self.path = Path(path)

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            raise FileNotFoundError(
                f"Raw player-season CSV not found at {self.path}. It ships with the repo "
                f"at {DEFAULT_CSV_PATH}; pass an explicit path if it lives elsewhere."
            )
        return pd.read_csv(self.path, low_memory=False)


class LivePlayerSeasonSource:
    """Live nflverse pull, for seasons the frozen CSV doesn't cover (2025+).

    Deliberately not the default: live data is revised upstream over time, so
    results built on it aren't reproducible across days. Note the shape
    difference -- `import_seasonal_data` returns no name/position columns, so
    this joins `import_seasonal_rosters` for them, exactly as
    `nfl_stats.actual_fantasy_points` already does.
    """

    def __init__(self, years: list[int]):
        self.years = years

    def load(self) -> pd.DataFrame:
        import nfl_data_py as nfl

        stats = nfl.import_seasonal_data(self.years, s_type="REG")
        rosters = nfl.import_seasonal_rosters(self.years)
        meta = (
            rosters.dropna(subset=["player_id"])
            .drop_duplicates(subset=["player_id", "season"], keep="last")
            .set_index(["player_id", "season"])[["player_name", "position", "team"]]
        )
        merged = stats.merge(meta, left_on=["player_id", "season"], right_index=True, how="inner")
        merged = merged.rename(
            columns={"player_name": "player_display_name", "team": "recent_team"}
        )
        merged["season_type"] = "REG"
        merged["position_group"] = merged["position"]
        return merged


def mask_uncollected_eras(df: pd.DataFrame) -> pd.DataFrame:
    """Replace fabricated pre-2006 air-yards values with NaN.

    Honest missingness beats a zero that looks like data. This is the only
    value-level change the loader makes -- nothing else is imputed, corrected,
    or filled anywhere in this pipeline.
    """
    df = df.copy()
    pre_era = df["season"] < AIR_YARDS_ERA_START
    for column in UNCOLLECTED_BEFORE_2006:
        if column in df.columns:
            df.loc[pre_era, column] = pd.NA
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def load_regular_season(
    source: PlayerSeasonSource | None = None, mask_eras: bool = True
) -> pd.DataFrame:
    """Raw rows reduced to one honest row per player-season.

    Asserts uniqueness on (player_id, season) rather than silently
    de-duplicating: if a future source genuinely has multiple rows per
    player-season (e.g. team-split lines after a midseason trade), that's a
    schema change that needs a deliberate decision, not a quiet `drop_duplicates`
    that throws half a season away.
    """
    source = source or CsvPlayerSeasonSource()
    df = source.load()

    if "season_type" not in df.columns:
        raise ValueError("Source is missing `season_type`; cannot verify the REG filter applied.")

    df = df[df["season_type"] == "REG"].copy()
    if df.empty:
        raise ValueError("No REG rows found in source -- check the season_type values.")

    duplicated = df.duplicated(subset=["player_id", "season"])
    if duplicated.any():
        examples = df.loc[duplicated, ["player_id", "season"]].head(5).to_dict("records")
        raise ValueError(
            f"{int(duplicated.sum())} duplicate (player_id, season) rows after the REG "
            f"filter, e.g. {examples}. Expected exactly one row per player-season."
        )

    if "player_display_name" in df.columns:
        df["player_name"] = df["player_display_name"]

    # One real 1999 row (player_id 00-0005532, 3 games for New Orleans) has no
    # name and no position -- an orphan record that can't be joined to ADP,
    # ranked, or identified. Dropping it is right; dropping it *silently* is not,
    # and neither is failing the whole build over it. So it's dropped with a
    # reported count, and the threshold still fails loudly if name resolution
    # ever breaks systemically (e.g. a source that stops populating the column).
    unnamed = df["player_name"].isna()
    n_unnamed = int(unnamed.sum())
    if n_unnamed:
        share = n_unnamed / len(df)
        if share > MAX_UNNAMED_SHARE:
            raise ValueError(
                f"{n_unnamed} of {len(df)} rows ({share:.1%}) have no usable player name "
                f"after the display-name fallback -- above the {MAX_UNNAMED_SHARE:.1%} "
                "threshold, so this looks like a source problem rather than stray orphans."
            )
        df = df[~unnamed].copy()

    df = df.drop(columns=[c for c in DROP_COLUMNS if c in df.columns])

    if mask_eras:
        df = mask_uncollected_eras(df)

    return df.sort_values(["season", "player_id"]).reset_index(drop=True)
