"""Structured audit of the raw player-season source.

Pure computation returning plain dicts -- `build.py` renders them into
docs/HISTORICAL_DATA_AUDIT.md. Split this way so the findings are testable and
re-runnable against a refreshed source, rather than being prose someone typed
once and that silently goes stale.

The audit reports on the RAW file, deliberately including the rows and columns
the loader filters or masks out. A report that only describes the cleaned data
can't tell you what the cleaning saved you from, and the two traps here (the
REG+POST triple-count and the fabricated pre-2006 air yards) are both invisible
after the fact.
"""
from __future__ import annotations

import pandas as pd

from fantasyprep.historical.dataset.loader import (
    AIR_YARDS_ERA_START,
    UNCOLLECTED_BEFORE_2006,
    CsvPlayerSeasonSource,
    PlayerSeasonSource,
)
from fantasyprep.historical.sources.nfl_stats import compute_points
from fantasyprep.league.settings import ScoringSettings, default_settings

SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

# Columns whose missingness actually matters for modeling, per the task brief.
KEY_COLUMNS = (
    "games",
    "fantasy_points_ppr",
    "targets",
    "receptions",
    "carries",
    "receiving_tds",
    "rushing_tds",
    "passing_tds",
    "target_share",
    "air_yards_share",
    "wopr",
    "receiving_epa",
    "rushing_epa",
    "passing_epa",
    "racr",
    "pacr",
    "dakota",
)


def audit_all(source: PlayerSeasonSource | None = None) -> dict:
    raw = (source or CsvPlayerSeasonSource()).load()
    return {
        "coverage": audit_coverage(raw),
        "identity": audit_identity(raw),
        "scoring": audit_scoring(raw),
        "missingness": audit_missingness(raw),
        "era_gaps": audit_era_gaps(raw),
    }


def audit_coverage(raw: pd.DataFrame) -> dict:
    reg = raw[raw["season_type"] == "REG"]
    skill = reg[reg["position"].isin(SKILL_POSITIONS)]
    return {
        "total_rows": len(raw),
        "columns": len(raw.columns),
        "season_type_counts": raw["season_type"].value_counts().to_dict(),
        "reg_rows": len(reg),
        "reg_skill_rows": len(skill),
        "earliest_season": int(raw["season"].min()),
        "latest_season": int(raw["season"].max()),
        "n_seasons": int(raw["season"].nunique()),
        "seasons": sorted(int(s) for s in raw["season"].unique()),
        "unique_players": int(raw["player_id"].nunique()),
        "unique_players_reg_skill": int(skill["player_id"].nunique()),
        "position_counts": reg["position"].value_counts().to_dict(),
        "rows_by_season": reg.groupby("season").size().to_dict(),
        "rows_by_season_position": (
            skill.groupby(["season", "position"]).size().unstack(fill_value=0).to_dict("index")
        ),
    }


def audit_identity(raw: pd.DataFrame) -> dict:
    """Whether player_id is a trustworthy join key, and what the three
    season_type views actually do to row uniqueness."""
    reg = raw[raw["season_type"] == "REG"]
    combined = raw[raw["season_type"] == "REG+POST"]

    # The specific trap: REG+POST is a *different aggregate of the same
    # player-season*, not extra players. Quantifying how often it disagrees with
    # REG is what makes "just filter it" a demonstrated requirement rather than
    # a stylistic preference.
    merged = reg.merge(combined, on=["player_id", "season"], suffixes=("_reg", "_combined"))
    disagreements = int(
        (merged["fantasy_points_ppr_reg"] != merged["fantasy_points_ppr_combined"]).sum()
    )

    multi_season = reg.groupby("player_id")["season"].nunique()
    unnamed = reg[reg["player_display_name"].isna()]
    return {
        "unnamed_reg_rows": len(unnamed),
        "unnamed_examples": unnamed[["season", "player_id", "recent_team", "games"]]
        .head(3)
        .to_dict("records"),
        "duplicate_player_season_rows_raw": int(raw.duplicated(["player_id", "season"]).sum()),
        "duplicate_player_season_rows_reg": int(reg.duplicated(["player_id", "season"]).sum()),
        "reg_plus_post_overlap_rows": len(merged),
        "reg_plus_post_disagreements": disagreements,
        "player_id_null_rows": int(reg["player_id"].isna().sum()),
        "player_name_null_frac": float(raw["player_name"].isna().mean()),
        "player_display_name_null_frac": float(raw["player_display_name"].isna().mean()),
        "players_in_multiple_seasons": int((multi_season > 1).sum()),
        "max_seasons_for_one_player": int(multi_season.max()),
        "id_format_example": str(reg["player_id"].iloc[0]),
        "teams_per_player_season_max": int(
            reg.groupby(["player_id", "season"])["recent_team"].nunique().max()
        ),
    }


def audit_scoring(raw: pd.DataFrame) -> dict:
    """Establish what `fantasy_points_ppr` actually means, by reproducing it.

    Not an assumption about the column name -- we run our own scoring function
    at candidate passing-TD values and report which one reproduces the column
    exactly. Whichever wins, we then know precisely how the source's scoring
    differs from our league's.
    """
    reg = raw[(raw["season_type"] == "REG") & raw["position"].isin(SKILL_POSITIONS)].fillna(0)
    ours = reg.apply(lambda row: compute_points(row, default_settings().scoring), axis=1)

    variants = {}
    for pass_td in (4.0, 6.0):
        computed = reg.apply(
            lambda row, td=pass_td: compute_points(row, ScoringSettings(pass_td=td)), axis=1
        )
        delta = (computed - reg["fantasy_points_ppr"]).abs()
        variants[f"pass_td_{int(pass_td)}"] = {
            "max_abs_delta": round(float(delta.max()), 4),
            "mean_abs_delta": round(float(delta.mean()), 4),
            "rows_over_half_point": int((delta > 0.5).sum()),
            "exact_match": bool(delta.max() < 1e-9),
        }

    gap = (ours - reg["fantasy_points_ppr"]).abs()
    worked_examples = []
    for idx in gap.nlargest(3).index:
        row = reg.loc[idx]
        worked_examples.append(
            {
                "player": row["player_display_name"],
                "season": int(row["season"]),
                "position": row["position"],
                "passing_tds": int(row["passing_tds"]),
                "our_points_6pt_td": round(float(ours[idx]), 2),
                "source_fantasy_points_ppr": round(float(row["fantasy_points_ppr"]), 2),
                "delta": round(float(ours[idx] - row["fantasy_points_ppr"]), 2),
            }
        )

    return {
        "n_rows_checked": len(reg),
        "variants": variants,
        "our_scoring_max_abs_delta": round(float(gap.max()), 2),
        "our_scoring_mean_abs_delta": round(float(gap.mean()), 4),
        "rows_over_two_points": int((gap > 2.0).sum()),
        "worked_examples": worked_examples,
    }


def audit_missingness(raw: pd.DataFrame) -> dict:
    reg = raw[(raw["season_type"] == "REG") & raw["position"].isin(SKILL_POSITIONS)]
    overall = {
        c: round(float(reg[c].isna().mean()), 4) for c in KEY_COLUMNS if c in reg.columns
    }
    by_season = {}
    for season, group in reg.groupby("season"):
        by_season[int(season)] = {
            c: round(float(group[c].isna().mean()), 4) for c in KEY_COLUMNS if c in group.columns
        }
    return {"overall_null_frac": overall, "null_frac_by_season": by_season}


def audit_era_gaps(raw: pd.DataFrame) -> dict:
    """The second trap: columns that are ZERO rather than null before 2006.

    A null-rate report alone gives these a clean bill of health. This measures
    the fraction of players with a nonzero value per era instead, which is what
    actually exposes them.
    """
    reg = raw[(raw["season_type"] == "REG") & raw["position"].isin(SKILL_POSITIONS)]
    pre = reg[reg["season"] < AIR_YARDS_ERA_START]
    post = reg[reg["season"] >= AIR_YARDS_ERA_START]

    findings = {}
    for column in UNCOLLECTED_BEFORE_2006:
        if column not in reg.columns:
            continue
        findings[column] = {
            "pre_2006_null_frac": round(float(pre[column].isna().mean()), 4),
            "pre_2006_nonzero_frac": round(float((pre[column].fillna(0) != 0).mean()), 4),
            "post_2006_nonzero_frac": round(float((post[column].fillna(0) != 0).mean()), 4),
            "pre_2006_max": _safe_max(pre[column]),
            "post_2006_max": _safe_max(post[column]),
        }
    return {"era_start": AIR_YARDS_ERA_START, "columns": findings}


def _safe_max(series: pd.Series) -> float | None:
    value = pd.to_numeric(series, errors="coerce").max()
    return None if pd.isna(value) else round(float(value), 2)
