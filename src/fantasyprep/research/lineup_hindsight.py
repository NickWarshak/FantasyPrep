"""How much of the engine's variance-seeking comes from hindsight lineups?

THE PROBLEM THIS SIZES

`research/variance_sensitivity.py` measured that the draft objective is
structurally variance-seeking: a mean-preserving spread of 1.25x on WR -- mean
unchanged to the cent -- flips the top recommendation and buys +29.7 points of
expected starting-lineup value. That artefact is several times larger than the
real dispersion signal the residual analysis found, so any player-specific
variance model fed into the current engine would be swamped by it.

The suspected cause is not the objective alone but *how rosters are scored*.
`roster.starting_lineup_value` takes one season total per player, sorts, and
starts the best. That is a lineup chosen with perfect hindsight: it benches a
player because of how his whole season turned out, which no real manager can
do. Hindsight plus a selection operator is what turns variance into free upside.

WHAT THIS MEASURES

Three scorers on the same rosters and the same real weekly data:

  season_hindsight   sort by SEASON TOTAL, start the best. What the engine does.
  weekly_hindsight   each week, start whoever actually scored most that week.
                     Still impossible, but isolates weekly-vs-seasonal.
  weekly_realistic   each week, start whoever was EXPECTED to score most, using
                     only weeks already played. This is what a manager can
                     actually do, and the only one of the three with no
                     hindsight in the lineup decision.

The gap between the first and the third is the hindsight premium. Its size tells
you how much of the engine's roster valuation is unearned, and re-running the
mean-preserving spread under each scorer tells you whether realistic weekly
scoring removes the variance-seeking bias or merely shrinks it.

Uses the project's existing weekly machinery (`historical/weekly_stats.py`), so
scoring stays consistent with the validated season-total formula.

Usage:
    python -m fantasyprep.research.lineup_hindsight --year 2023
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

from fantasyprep.draft_sim.roster import DraftedPlayer, starting_lineup_value
from fantasyprep.historical import weekly_stats
from fantasyprep.league.settings import LeagueSettings, default_settings
from fantasyprep.players.normalize import normalize_name

DEFAULT_OUT_PATH = Path("data/historical/lineup_hindsight.json")

# Weeks used to form an expectation before the realistic scorer will trust it.
# Before this, it falls back to preseason order, which is what a manager
# actually does in September.
MIN_WEEKS_FOR_EXPECTATION = 3

SPREAD_FACTORS = (1.0, 1.25, 1.5, 2.0)


def weekly_points_by_player(year: int, settings: LeagueSettings) -> dict[str, dict[int, float]]:
    """normalized name -> {week: points} for a season."""
    outcomes = weekly_stats.weekly_actual_points(year, settings.scoring)
    table: dict[str, dict[int, float]] = defaultdict(dict)
    for outcome in outcomes:
        table[normalize_name(outcome.name)][outcome.week] = outcome.points
    return table


def score_season_hindsight(
    roster: list[tuple[str, str]], weekly: dict[str, dict[int, float]], settings: LeagueSettings
) -> float:
    """What the engine does today: one season total per player, hindsight lineup."""
    players = [
        DraftedPlayer(name=name, position=position, points=sum(weekly.get(name, {}).values()))
        for name, position in roster
    ]
    return starting_lineup_value(players, settings)


def score_weekly(
    roster: list[tuple[str, str]],
    weekly: dict[str, dict[int, float]],
    settings: LeagueSettings,
    realistic: bool,
) -> float:
    """Sum of weekly lineups, chosen either with hindsight or without.

    The realistic variant ranks players by what they had averaged over weeks
    already played -- information a manager genuinely has on Sunday morning --
    and then scores whatever those starters actually did. A player who ends up
    busting still occupies a starting slot for the weeks before anyone could
    have known, which is precisely the cost hindsight scoring erases.
    """
    weeks = sorted({w for points in weekly.values() for w in points})
    total = 0.0
    history: dict[str, list[float]] = defaultdict(list)

    for week in weeks:
        candidates = []
        for order, (name, position) in enumerate(roster):
            actual = weekly.get(name, {}).get(week)
            if actual is None:
                continue  # didn't play: cannot be started
            if realistic:
                past = history[name]
                # Preseason draft order stands in for expectation until enough
                # weeks exist to form one -- a manager's real September default.
                rank_value = (
                    statistics.mean(past)
                    if len(past) >= MIN_WEEKS_FOR_EXPECTATION
                    else float(len(roster) - order)
                )
            else:
                rank_value = actual
            candidates.append((rank_value, actual, name, position))

        started = _pick_lineup(candidates, settings)
        total += sum(actual for _, actual, _, _ in started)

        for _, actual, name, _ in candidates:
            history[name].append(actual)

    return total


def _pick_lineup(candidates: list[tuple], settings: LeagueSettings) -> list[tuple]:
    """Greedy slot fill by `rank_value`, mirroring `starting_lineup_value`."""
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
        eligible = [c for c in remaining if c[3] in LeagueSettings.FLEX_ELIGIBLE][:flex_count]
        started.extend(eligible)
    return started


def spread_weekly(
    weekly: dict[str, dict[int, float]], names: set[str], factor: float
) -> dict[str, dict[int, float]]:
    """Mean-preserving spread applied to weekly outcomes.

    Scales each week's deviation from that player's own weekly mean, so his
    season total and per-week average are unchanged and only the volatility
    moves. Any score difference is therefore variance alone.
    """
    out: dict[str, dict[int, float]] = {}
    for name, points in weekly.items():
        if name not in names or not points:
            out[name] = points
            continue
        mean = statistics.mean(points.values())
        out[name] = {w: mean + (p - mean) * factor for w, p in points.items()}
    return out


def build_rosters(
    weekly: dict[str, dict[int, float]], positions: dict[str, str],
    settings: LeagueSettings, n_rosters: int, seed: int,
) -> list[list[tuple[str, str]]]:
    """Random plausible rosters drawn from players who actually played.

    Random rather than drafted: the question is how the SCORERS behave on a
    roster, and sampling real drafted rosters would confound that with draft
    strategy.
    """
    rng = random.Random(seed)
    by_position: dict[str, list[str]] = defaultdict(list)
    for name, position in positions.items():
        if name in weekly and len(weekly[name]) >= 8:
            by_position[position].append(name)

    slots = [("QB", 2), ("RB", 5), ("WR", 5), ("TE", 2)]
    rosters = []
    for _ in range(n_rosters):
        roster = []
        for position, count in slots:
            pool = by_position.get(position, [])
            if len(pool) < count:
                continue
            roster.extend((name, position) for name in rng.sample(pool, count))
        if roster:
            rosters.append(roster)
    return rosters


def run(year: int = 2023, n_rosters: int = 200, seed: int = 0) -> dict:
    settings = default_settings()
    outcomes = weekly_stats.weekly_actual_points(year, settings.scoring)
    weekly = weekly_points_by_player(year, settings)
    positions = {normalize_name(o.name): o.position for o in outcomes}

    rosters = build_rosters(weekly, positions, settings, n_rosters, seed)
    if not rosters:
        raise ValueError(f"No rosters could be built for {year}")

    baseline = {
        "season_hindsight": _mean(rosters, weekly, settings, score_season_hindsight),
        "weekly_hindsight": _mean(
            rosters, weekly, settings, lambda r, w, s: score_weekly(r, w, s, realistic=False)
        ),
        "weekly_realistic": _mean(
            rosters, weekly, settings, lambda r, w, s: score_weekly(r, w, s, realistic=True)
        ),
    }

    # Variance sensitivity of each scorer: spread every WR's weekly outcomes,
    # leaving each player's own season total untouched.
    wr_names = {n for n, p in positions.items() if p == "WR"}
    sensitivity = {scorer: {} for scorer in baseline}
    for factor in SPREAD_FACTORS:
        spread = spread_weekly(weekly, wr_names, factor)
        for scorer, fn in [
            ("season_hindsight", score_season_hindsight),
            ("weekly_hindsight", lambda r, w, s: score_weekly(r, w, s, realistic=False)),
            ("weekly_realistic", lambda r, w, s: score_weekly(r, w, s, realistic=True)),
        ]:
            sensitivity[scorer][factor] = round(_mean(rosters, spread, settings, fn), 1)

    return {
        "year": year,
        "n_rosters": len(rosters),
        "baseline_mean_score": {k: round(v, 1) for k, v in baseline.items()},
        "hindsight_premium": {
            "season_over_realistic": round(
                baseline["season_hindsight"] - baseline["weekly_realistic"], 1
            ),
            "weekly_hindsight_over_realistic": round(
                baseline["weekly_hindsight"] - baseline["weekly_realistic"], 1
            ),
        },
        "variance_sensitivity": sensitivity,
        "variance_gain": {
            scorer: {
                str(f): round(values[f] - values[1.0], 1) for f in SPREAD_FACTORS if f != 1.0
            }
            for scorer, values in sensitivity.items()
        },
    }


def _mean(rosters, weekly, settings, fn) -> float:
    return statistics.mean(fn(roster, weekly, settings) for roster in rosters)


def summarize(results: dict) -> str:
    lines = [f"{results['year']}, {results['n_rosters']} random rosters", ""]
    lines.append("Mean roster score by scorer")
    for scorer, value in results["baseline_mean_score"].items():
        lines.append(f"  {scorer:20s} {value:9.1f}")
    premium = results["hindsight_premium"]
    lines.append("")
    lines.append(f"  hindsight premium (season vs realistic): "
                 f"{premium['season_over_realistic']:+.1f}")
    lines.append(f"  weekly hindsight vs realistic:           "
                 f"{premium['weekly_hindsight_over_realistic']:+.1f}")

    lines.append("")
    lines.append("Variance-seeking: score gain from a mean-preserving WR spread")
    lines.append(f"  {'scorer':20s} " + "  ".join(f"x{f:<5.2f}" for f in SPREAD_FACTORS if f != 1.0))
    for scorer, gains in results["variance_gain"].items():
        row = "  ".join(f"{gains[str(f)]:+6.1f}" for f in SPREAD_FACTORS if f != 1.0)
        lines.append(f"  {scorer:20s} {row}")
    return "\n".join(lines)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2023)
    parser.add_argument("--n-rosters", type=int, default=200)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    args = parser.parse_args(argv)

    results = run(year=args.year, n_rosters=args.n_rosters)
    print(summarize(results))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
