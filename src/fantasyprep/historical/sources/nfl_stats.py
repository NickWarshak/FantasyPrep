"""Actual season fantasy points, computed from raw stats under our own scoring rules.

Uses nfl_data_py (nflverse/nflfastR data, CC-BY licensed, free, seasonal
stats back to 1999). We compute fantasy points ourselves from the raw
counting stats rather than trusting nfl_data_py's own fantasy_points_ppr
column, since that's someone else's assumed scoring format, not
necessarily the league's -- but that column is a near-exact independent
cross-check when our settings happen to match full PPR (see
historical/validate.py), and it's exactly what caught a real bug here:
special_teams_tds (return TDs) and 2pt conversions weren't credited until
this was checked against it.
"""
from __future__ import annotations

from dataclasses import dataclass

import nfl_data_py as nfl
import pandas as pd

from fantasyprep.league.settings import ScoringSettings

POSITION_MAP = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "K": "K"}


@dataclass(frozen=True)
class SeasonOutcome:
    name: str
    position: str
    team: str
    points: float


def actual_fantasy_points(year: int, scoring: ScoringSettings) -> list[SeasonOutcome]:
    """Actual fantasy points for every skill-position player in a given season."""
    stats = nfl.import_seasonal_data([year], s_type="REG")

    rosters = nfl.import_seasonal_rosters([year])
    id_to_meta = (
        rosters.dropna(subset=["player_id"])
        .drop_duplicates(subset=["player_id"], keep="last")
        .set_index("player_id")[["player_name", "position", "team"]]
    )

    merged = stats.merge(id_to_meta, left_on="player_id", right_index=True, how="inner")
    merged = merged[merged["position"].isin(POSITION_MAP)]

    outcomes = []
    for _, row in merged.iterrows():
        points = compute_points(row, scoring)
        outcomes.append(
            SeasonOutcome(
                name=row["player_name"],
                position=row["position"],
                team=row["team"] or "FA",
                points=round(points, 2),
            )
        )
    return outcomes


def compute_points(row: pd.Series, scoring: ScoringSettings) -> float:
    fumbles_lost = (
        (row.get("sack_fumbles_lost") or 0)
        + (row.get("rushing_fumbles_lost") or 0)
        + (row.get("receiving_fumbles_lost") or 0)
    )
    two_pt_conversions = (
        (row.get("passing_2pt_conversions") or 0)
        + (row.get("rushing_2pt_conversions") or 0)
        + (row.get("receiving_2pt_conversions") or 0)
    )
    return (
        row.get("passing_yards", 0) * scoring.pass_yard
        + row.get("passing_tds", 0) * scoring.pass_td
        + row.get("interceptions", 0) * scoring.interception
        + row.get("rushing_yards", 0) * scoring.rush_yard
        + row.get("rushing_tds", 0) * scoring.rush_td
        + row.get("receptions", 0) * scoring.reception
        + row.get("receiving_yards", 0) * scoring.rec_yard
        + row.get("receiving_tds", 0) * scoring.rec_td
        + fumbles_lost * scoring.fumble_lost
        + (row.get("special_teams_tds") or 0) * scoring.special_teams_td
        + two_pt_conversions * scoring.two_pt_conversion
    )
