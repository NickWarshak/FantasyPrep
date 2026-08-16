"""Cross-check our computed fantasy points against independent sources.

Two checks, for the two places we compute points ourselves from raw
stats: historical actuals (nfl_data_py) and current-year ESPN projections.
Both exist because "the code ran without errors" isn't the same claim as
"the numbers are right" -- this is what actually caught the missing
special_teams_td/2pt-conversion bug in historical/sources/nfl_stats.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import nfl_data_py as nfl

from fantasyprep.historical.sources.nfl_stats import compute_points
from fantasyprep.league.settings import ScoringSettings
from fantasyprep.sources import espn as espn_source

ACTUALS_OUTLIER_THRESHOLD = 2.0  # points
ESPN_OUTLIER_THRESHOLD = 15.0  # points -- looser, since best-fit-variant isn't exact by nature


@dataclass(frozen=True)
class ActualsCheckRow:
    name: str
    position: str
    year: int
    our_points: float
    nflverse_points: float
    delta: float

    @property
    def is_outlier(self) -> bool:
        return abs(self.delta) > ACTUALS_OUTLIER_THRESHOLD


def cross_check_actuals(years: list[int], scoring: ScoringSettings) -> list[ActualsCheckRow]:
    """Compare our computed historical points against nflverse's own
    fantasy_points_ppr column -- a near-exact match is expected when
    `scoring` is close to full PPR (see historical/validate.py docstring
    and the 2023 spot-check that found the special_teams_td bug)."""
    rows = []
    for year in years:
        stats = nfl.import_seasonal_data([year], s_type="REG")
        rosters = nfl.import_seasonal_rosters([year])
        id_to_meta = (
            rosters.dropna(subset=["player_id"])
            .drop_duplicates(subset=["player_id"], keep="last")
            .set_index("player_id")[["player_name", "position"]]
        )
        merged = stats.merge(id_to_meta, left_on="player_id", right_index=True, how="inner")
        merged = merged[merged["position"].isin({"QB", "RB", "WR", "TE"})]

        for _, row in merged.iterrows():
            ours = compute_points(row, scoring)
            theirs = float(row.get("fantasy_points_ppr") or 0.0)
            rows.append(
                ActualsCheckRow(
                    name=row["player_name"],
                    position=row["position"],
                    year=year,
                    our_points=round(ours, 2),
                    nflverse_points=round(theirs, 2),
                    delta=round(ours - theirs, 2),
                )
            )
    return rows


PLAUSIBLE_APPLIED_AVERAGE_CEILING = 45.0  # pts/game -- generously above any real single-season average


@dataclass(frozen=True)
class EspnCheckRow:
    name: str
    position: str
    full_ppr: float
    half_ppr: float
    standard: float
    applied_total: float
    best_fit_label: str
    best_fit_delta: float

    @property
    def is_outlier(self) -> bool:
        return abs(self.best_fit_delta) > ESPN_OUTLIER_THRESHOLD


@dataclass(frozen=True)
class EspnDataAnomaly:
    """A player where ESPN's own `appliedTotal`/`appliedAverage` is itself
    implausible (e.g. 63-102 pts/game average -- confirmed via live spot
    check on Joe Burrow and Saquon Barkley, both impossible for any real
    single game let alone a season average). Not our extraction bug --
    ESPN's own data is unreliable for these specific entries -- so these
    are reported separately rather than folded into real outliers."""
    name: str
    position: str
    applied_average: float
    applied_total: float


def cross_check_espn_projections(
    year: int, cache_path=None
) -> tuple[list[EspnCheckRow], list[EspnDataAnomaly]]:
    """Compare our ESPN-projection extraction against ESPN's own
    `appliedTotal` field, under three scoring variants -- ESPN's default
    view doesn't match any specific league's settings, so this looks for
    the best-fitting variant rather than expecting equality (confirmed:
    ESPN's default is closer to half-PPR than full PPR for target-heavy
    receivers, not a bug, a scoring-format difference). Entries where
    ESPN's own applied average is itself implausible are set aside as
    EspnDataAnomaly rather than compared, since there's nothing to
    validate against there."""
    variants = {
        "full_ppr": ScoringSettings(reception=1.0),
        "half_ppr": ScoringSettings(reception=0.5),
        "standard": ScoringSettings(reception=0.0),
    }
    points_by_variant = {
        label: espn_source.fetch_espn_projected_points(year, s, cache_path=cache_path)
        for label, s in variants.items()
    }

    raw = espn_source._fetch_raw(year, cache_path, force_refresh=False, timeout=30)
    rows = []
    anomalies = []
    for p in raw:
        position = espn_source.POSITION_MAP.get(p.get("defaultPositionId"))
        if position not in espn_source.PROJECTION_POSITIONS:
            continue

        # appliedTotal/appliedAverage (ESPN's own computed point figures)
        # live as sibling keys on the season-projection stat entry itself,
        # not inside its nested `stats` dict.
        applied_total = None
        applied_average = None
        for entry in p.get("stats", []):
            if entry.get("scoringPeriodId") == 0 and entry.get("statSourceId") == 1 and entry.get("statSplitTypeId") == 0:
                if "appliedTotal" in entry:
                    applied_total = entry["appliedTotal"]
                    applied_average = entry.get("appliedAverage")
                    break
        if applied_total is None:
            continue

        name = p["fullName"]
        if applied_average is not None and applied_average > PLAUSIBLE_APPLIED_AVERAGE_CEILING:
            anomalies.append(
                EspnDataAnomaly(
                    name=name, position=position,
                    applied_average=round(applied_average, 2), applied_total=round(applied_total, 2),
                )
            )
            continue

        by_variant = {label: pts.get(name) for label, pts in points_by_variant.items()}
        if any(v is None for v in by_variant.values()):
            continue

        best_label = min(by_variant, key=lambda label: abs(by_variant[label] - applied_total))
        rows.append(
            EspnCheckRow(
                name=name,
                position=position,
                full_ppr=by_variant["full_ppr"],
                half_ppr=by_variant["half_ppr"],
                standard=by_variant["standard"],
                applied_total=round(applied_total, 2),
                best_fit_label=best_label,
                best_fit_delta=round(by_variant[best_label] - applied_total, 2),
            )
        )
    return rows, anomalies
