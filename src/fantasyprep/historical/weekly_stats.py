"""Real weekly-level actual fantasy points, and a "waiver-wire adjusted"
alternative to season-total scoring.

Motivation: the backtest's default scoring implicitly assumes a manager
rides an empty/injured roster spot for zero points the rest of the
season -- concretely wrong for the 2023-slot-4 replay traced this session
(Kirk Cousins tore his Achilles in week 8; a real manager streams a
replacement for the rest of the year rather than eating a zero). This
module credits realistic in-season roster management instead, without
requiring the full weekly-simulation outcome-model rearchitecture that's
still future work (see ROADMAP.md Phase 4) -- it's a scoring-methodology
adjustment layered on top of the existing season-total backtest, not a
replacement for it.

Missing-week detection: nfl_data_py's weekly data simply has no row for a
player in a week they didn't play -- this is the injury/inactive signal
used here. Deliberately not using official injury designations: checked
live, that data source is spotty (came back completely empty for
Cousins's real, well-documented season-ending injury), while "did they
have a real production row that week" is unambiguous and always present.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import nfl_data_py as nfl

from fantasyprep.historical.sources.nfl_stats import POSITION_MAP, compute_points
from fantasyprep.league.settings import ScoringSettings
from fantasyprep.players.normalize import normalize_name

# A guessed fallback only -- real callers should pass a `rank_cutoff` derived
# from ffc.derive_rank_cutoff() instead, which counts actual real-ADP draft
# depth per position rather than assuming a fixed FLEX/bench-stash split.
# Checked against real FFC ADP for a 10-team league: this flat guess badly
# understates real RB/WR draft depth (real depth runs 50-60+, not 30) --
# see derive_rank_cutoff's docstring in historical/sources/ffc.py.
DEFAULT_RANK_CUTOFF = {"QB": 15, "RB": 30, "WR": 30, "TE": 15}


@dataclass(frozen=True)
class WeeklyOutcome:
    name: str
    position: str
    team: str
    week: int
    points: float


def weekly_actual_points(year: int, scoring: ScoringSettings) -> list[WeeklyOutcome]:
    """Every skill-position player's real per-week points for a season.
    Reuses the exact same scoring formula as season totals
    (`nfl_stats.compute_points`) -- weekly rows have the same stat
    columns as seasonal ones, so there's no separate formula to drift out
    of sync with the validated season-total one."""
    stats = nfl.import_weekly_data([year], columns=None)
    stats = stats[stats["season_type"] == "REG"]
    stats = stats[stats["position"].isin(POSITION_MAP)]

    outcomes = []
    for _, row in stats.iterrows():
        points = compute_points(row, scoring)
        outcomes.append(
            WeeklyOutcome(
                name=row["player_display_name"],
                position=row["position"],
                team=row["recent_team"] or "FA",
                week=int(row["week"]),
                points=round(points, 2),
            )
        )
    return outcomes


def replacement_level_by_week(
    weekly_outcomes: list[WeeklyOutcome], rank_cutoff: dict[str, int] | None = None
) -> dict[tuple[str, int], float]:
    """Real replacement-level points per (position, week) -- the
    rank_cutoff-th best real performance at that position that week,
    computed from every player who played, not just drafted ones."""
    rank_cutoff = rank_cutoff or DEFAULT_RANK_CUTOFF
    by_pos_week: dict[tuple[str, int], list[float]] = defaultdict(list)
    for o in weekly_outcomes:
        by_pos_week[(o.position, o.week)].append(o.points)

    levels = {}
    for (position, week), points_list in by_pos_week.items():
        points_list.sort(reverse=True)
        cutoff = rank_cutoff.get(position, 30)
        idx = min(cutoff - 1, len(points_list) - 1)
        levels[(position, week)] = max(0.0, points_list[idx])
    return levels


def _waiver_adjusted_totals(
    weekly_outcomes: list[WeeklyOutcome], rank_cutoff: dict[str, int] | None = None
) -> dict[str, float]:
    """Pure computation half of `waiver_adjusted_actual_points`, split out
    so it's testable without a network call -- normalized player name ->
    season total, crediting real points for weeks played and
    replacement-level points for weeks the data shows they didn't."""
    replacement = replacement_level_by_week(weekly_outcomes, rank_cutoff)

    by_player: dict[str, dict[int, float]] = defaultdict(dict)
    position_by_player: dict[str, str] = {}
    for o in weekly_outcomes:
        key = normalize_name(o.name)
        by_player[key][o.week] = o.points
        position_by_player[key] = o.position

    all_weeks = sorted({o.week for o in weekly_outcomes})

    totals: dict[str, float] = {}
    for key, played_weeks in by_player.items():
        position = position_by_player[key]
        total = 0.0
        for week in all_weeks:
            if week in played_weeks:
                total += played_weeks[week]
            else:
                total += replacement.get((position, week), 0.0)
        totals[key] = round(total, 2)
    return totals


def waiver_adjusted_actual_points(
    year: int, scoring: ScoringSettings, rank_cutoff: dict[str, int] | None = None
) -> dict[str, float]:
    """Normalized player name -> season total, crediting real points for
    weeks played and replacement-level points for weeks the season data
    shows they didn't. Same dict shape as
    `nfl_stats.actual_fantasy_points`'s name->points mapping (just built
    differently), so it's a drop-in alternative wherever that's used to
    score a roster -- e.g. `backtest.py`'s `--scoring-mode`.

    DST has no weekly data any more than it has season-total data (not in
    `POSITION_MAP`) -- same documented, symmetric gap as the rest of this
    codebase, not new here.
    """
    weekly = weekly_actual_points(year, scoring)
    return _waiver_adjusted_totals(weekly, rank_cutoff)


# How many weeks of real production a manager needs before he'll rank a player
# on his own average rather than on where he was drafted. Before this, draft
# order stands in -- which is what a manager actually does in September.
MIN_WEEKS_FOR_EXPECTATION = 3


def weekly_points_by_player(
    year: int, scoring: ScoringSettings
) -> dict[str, dict[int, float]]:
    """normalized player name -> {week: points} for a whole season."""
    table: dict[str, dict[int, float]] = defaultdict(dict)
    for outcome in weekly_actual_points(year, scoring):
        table[normalize_name(outcome.name)][outcome.week] = outcome.points
    return table


def realistic_weekly_roster_points(
    roster: list[tuple[str, str]],
    weekly: dict[str, dict[int, float]],
    settings,
    hindsight: bool = False,
) -> float:
    """Score a roster by summing weekly lineups a manager could actually set.

    WHY THIS EXISTS, and why it is not just a refinement of season-total scoring:
    the default scorer takes one season total per player and starts the best.
    That is a lineup chosen with perfect hindsight -- it benches a player because
    of how his whole season turned out, which nobody can do in September.

    Measured consequence (see research/lineup_hindsight.py): hindsight scoring
    REWARDS volatility, realistic scoring PENALISES it, and the sign genuinely
    flips. A mean-preserving spread of 2x on weekly outcomes is worth +76.6
    points under hindsight and -21.8 under realistic management. So any
    player-variance work built on hindsight scoring would push recommendations
    toward exactly the players a real manager should avoid.

    Here a player is ranked each week by what he has averaged over weeks
    *already played*, and then scored on what he actually did. A player who ends
    up busting still occupies a starting slot for the weeks before anyone could
    have known -- which is the cost hindsight scoring erases.

    `hindsight=True` ranks by that week's actual points instead, giving the
    unattainable upper bound. Useful for measuring the premium, not for scoring
    a real comparison.

    Missing week = did not play, the same signal the rest of this module uses,
    so an injured player simply cannot be started.
    """
    weeks = sorted({w for points in weekly.values() for w in points})
    history: dict[str, list[float]] = defaultdict(list)
    total = 0.0

    for week in weeks:
        candidates = []
        for order, (name, position) in enumerate(roster):
            actual = weekly.get(name, {}).get(week)
            if actual is None:
                continue
            if hindsight:
                rank_value = actual
            else:
                past = history[name]
                rank_value = (
                    sum(past) / len(past)
                    if len(past) >= MIN_WEEKS_FOR_EXPECTATION
                    else float(len(roster) - order)
                )
            candidates.append((rank_value, actual, name, position))

        total += sum(actual for _, actual, _, _ in _weekly_lineup(candidates, settings))
        for _, actual, name, _ in candidates:
            history[name].append(actual)

    return total


def _weekly_lineup(candidates: list[tuple], settings) -> list[tuple]:
    """Greedy slot fill by rank value, mirroring `roster.starting_lineup_value`
    so the two scorers differ only in *what they rank on*, never in how slots
    are assigned."""
    from fantasyprep.league.settings import LeagueSettings

    remaining = sorted(candidates, key=lambda c: c[0], reverse=True)
    started = []
    for position, count in settings.roster_slots.items():
        if position == "FLEX":
            continue
        eligible = [c for c in remaining if c[3] == position][:count]
        started.extend(eligible)
        for c in eligible:
            remaining.remove(c)
    flex_count = settings.roster_slots.get("FLEX", 0)
    if flex_count:
        started.extend(
            [c for c in remaining if c[3] in LeagueSettings.FLEX_ELIGIBLE][:flex_count]
        )
    return started
