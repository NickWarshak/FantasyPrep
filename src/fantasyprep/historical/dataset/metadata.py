"""Player identity metadata: age, experience, and draft capital.

Age was the one input the eventual player-outcome model was meant to condition
on that had no source anywhere in the project. It has one now, essentially for
free: `nfl_data_py.import_players` joins to our canonical table on `gsis_id`,
which *is* our `player_id` -- **100% of 3,462 skill-position players matched**,
with `birth_date`, `rookie_season` and `years_of_experience` present for all of
them, in every era including 1999-2005. No fuzzy name matching anywhere.

Why this matters more than another stat column: it enables the comparable-player
framing the whole foundation is for. "What happened to 29-year-old running backs
coming off a 250-touch season" is a far better prior than a rank bucket, and it
is a query this data supports today.

DESIGN NOTES

- **Every column here is leakage-safe by construction.** A birth date is fixed,
  so age entering season Y is fully knowable before season Y. Same for rookie
  season and draft position. That's why these join into `PRE_SEASON_COLUMNS`
  rather than needing a lag.
- **Undrafted is not missing.** Draft position is absent for ~27% of
  player-seasons because those players went undrafted, which is real signal --
  an undrafted profile is genuinely different from a first-round one. It's
  encoded as an explicit `undrafted` flag, and the pick number is left NaN
  rather than filled with a sentinel that a model would read as "pick 0".
- **The network pull is cached and optional.** The core build is deterministic
  and offline; this enrichment is layered on top and caches to `data/raw/` the
  same way FFC fetches do, so a rebuild doesn't depend on nflverse being up.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

DEFAULT_CACHE_PATH = Path("data/raw/.players_metadata.parquet")

# Reference date for age. September 1 is a defensible stand-in for "start of the
# season" that doesn't shift with the schedule, so ages stay comparable across
# 26 seasons of differing week-1 dates. Precision beyond this is false: what a
# model wants is "how old, roughly, in football-aging terms", not days.
AGE_REFERENCE_MONTH = 9
AGE_REFERENCE_DAY = 1

SOURCE_COLUMNS = [
    "gsis_id",
    "birth_date",
    "rookie_season",
    "years_of_experience",
    "draft_year",
    "draft_round",
    "draft_pick",
    "height",
    "weight",
]

# Added to PRE_SEASON_COLUMNS -- see the module docstring on why no lag is needed.
METADATA_FEATURE_COLUMNS = (
    "age",
    "rookie_season",
    "seasons_since_rookie_year",
    "draft_round",
    "draft_pick",
    "undrafted",
    "height",
    "weight",
)


def fetch_player_metadata(
    cache_path: Path | None = DEFAULT_CACHE_PATH, force_refresh: bool = False
) -> pd.DataFrame:
    """One row per `gsis_id`, cached to parquet so rebuilds stay offline."""
    if cache_path and cache_path.exists() and not force_refresh:
        return pd.read_parquet(cache_path)

    import nfl_data_py as nfl

    players = nfl.import_players()
    available = [c for c in SOURCE_COLUMNS if c in players.columns]
    meta = (
        players[available]
        .dropna(subset=["gsis_id"])
        .drop_duplicates(subset=["gsis_id"], keep="last")
        .reset_index(drop=True)
    )

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        meta.to_parquet(cache_path, index=False)
    return meta


def age_at_season_start(birth_date: pd.Series, season: pd.Series) -> pd.Series:
    """Age in years on September 1 of that season. NaN where birth date is
    unknown -- never estimated from rookie year, which would silently invent
    the very variable the model is meant to learn from."""
    birth = pd.to_datetime(birth_date, errors="coerce")
    reference = pd.to_datetime(
        dict(year=season, month=AGE_REFERENCE_MONTH, day=AGE_REFERENCE_DAY), errors="coerce"
    )
    return ((reference - birth).dt.days / 365.25).round(2)


def add_metadata(
    df: pd.DataFrame,
    metadata: pd.DataFrame | None = None,
    cache_path: Path | None = DEFAULT_CACHE_PATH,
) -> pd.DataFrame:
    """Left-join age/experience/draft capital onto a player-season frame.

    Left join, deliberately: an unmatched player keeps his row with NaN
    metadata. An inner join would silently delete players from the historical
    record because a *metadata* source didn't know them, which is a much worse
    failure than a missing age.
    """
    metadata = fetch_player_metadata(cache_path) if metadata is None else metadata

    keep = [c for c in SOURCE_COLUMNS if c in metadata.columns and c != "gsis_id"]
    indexed = metadata.set_index("gsis_id")[keep]
    joined = df.merge(indexed, left_on="player_id", right_index=True, how="left")

    joined["age"] = age_at_season_start(joined["birth_date"], joined["season"])

    # Distinct from `years_of_experience`, which is a career-to-date figure from
    # the source and therefore reflects *today*, not the season in question --
    # using it as a per-season feature would leak the future. This one is
    # computed against the row's own season, so it's honest for every row.
    joined["seasons_since_rookie_year"] = (
        joined["season"] - pd.to_numeric(joined["rookie_season"], errors="coerce")
    ).astype("Int64")

    # Absent draft position means undrafted, not unknown -- see module docstring.
    joined["undrafted"] = joined["draft_pick"].isna()

    return joined.drop(columns=["birth_date", "years_of_experience", "draft_year"], errors="ignore")


def coverage_report(joined: pd.DataFrame) -> dict:
    """Join quality, for the audit doc. Reported rather than assumed, so a
    future source regression shows up as a number instead of silent NaNs."""
    return {
        "rows": len(joined),
        "age_coverage": round(float(joined["age"].notna().mean()), 4),
        "rookie_season_coverage": round(float(joined["rookie_season"].notna().mean()), 4),
        "draft_pick_coverage": round(float(joined["draft_pick"].notna().mean()), 4),
        "undrafted_share": round(float(joined["undrafted"].mean()), 4),
        "age_by_position": {
            str(pos): {
                "n": int(g["age"].notna().sum()),
                "mean": round(float(g["age"].mean()), 1),
                "min": round(float(g["age"].min()), 1),
                "max": round(float(g["age"].max()), 1),
            }
            for pos, g in joined.groupby("fantasy_position")
        },
    }
