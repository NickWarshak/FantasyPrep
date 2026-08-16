"""Value-Over-Replacement (VOR) baseline -- rung 2 of the baseline ladder
(ROADMAP.md Phase 2). Initially thought blocked on missing historical
projections (no archived pre-season projections to compute VOR from) --
turns out not to be: VOR doesn't need a *projection*, it needs an
*expected value*, and the outcome-bucket machinery the model itself
already draws from (`historical/outcomes.py`) already gives real,
leakage-safe, historical expected value per (position, draft-rank)
bucket. No new data source required.

A player's VOR = the real historical mean outcome for their draft-rank
bucket, minus a real historical replacement-level baseline for that
position (the mean outcome at a "replacement rank" bucket -- roughly the
streaming/waiver tier in a 10-team league, same `DEFAULT_RANK_CUTOFF`
concept as `historical/weekly_stats.py`, applied to season-total buckets
instead of weekly ones here).

This is a genuinely stronger baseline than ADP+need: it accounts for
positional scarcity (a position with a steep value dropoff after its
top tier will show bigger VOR gaps than a flat one), which pure ADP+need
does not -- ADP already implicitly prices scarcity into the market, but
VOR makes that reasoning explicit and inspectable, and answers a
different question than "does the model beat the market's own signal":
"does the model beat a drafter reasoning explicitly about replacement
value from real historical data."

The actual pick logic (`vor_pick`, `replacement_level_points`,
`DEFAULT_RANK_CUTOFF`) lives in `backtest.py` now, alongside
`baseline_pick`/`pure_adp_pick` -- VOR is an official 4th condition in
the main backtest, not just a side comparison. This module re-exports
them (so existing imports from here still work) and keeps the standalone
2-way (VOR vs. ADP+need only) comparison runner/CLI for quick spot-checks
that don't need the full 4-way backtest's cost.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass
from pathlib import Path

from fantasyprep.draft_sim.backtest import (
    DEFAULT_BACKTEST_YEARS,
    DEFAULT_RANK_CUTOFF,
    baseline_pick,
    leakage_safe_distributions,
    replacement_level_points,
    run_full_draft,
    score_roster,
    vor_pick,
)
from fantasyprep.historical.outcomes import OutcomeDistribution
from fantasyprep.historical.sources import ffc, nfl_stats
from fantasyprep.historical.sources.ffc import FfcPlayer
from fantasyprep.league.settings import LeagueSettings, default_settings
from fantasyprep.players.normalize import normalize_name


@dataclass(frozen=True)
class VorReplayResult:
    year: int
    my_slot: int
    baseline_points: float
    vor_points: float

    @property
    def delta(self) -> float:
        """VOR vs. ADP+need -- positive means reasoning explicitly about
        replacement value beat the market-implied (ADP) baseline."""
        return self.vor_points - self.baseline_points


def replay_vor_vs_baseline(
    year: int,
    my_slot: int,
    settings: LeagueSettings,
    live_pool: list[FfcPlayer],
    distributions: dict[tuple[str, int], OutcomeDistribution],
    actual_points: dict[str, float],
    seed: int,
) -> VorReplayResult:
    """One (season, slot) replay: ADP+need baseline vs. VOR, sharing an
    opponent RNG seed (common random numbers, same technique as
    backtest.py) so the comparison isolates the drafting strategy."""
    total_rounds = sum(settings.roster_slots.values()) + settings.bench
    teams = settings.teams
    pos_ranks = ffc.position_ranks(live_pool)

    baseline_result = run_full_draft(
        live_pool, teams, my_slot, total_rounds, settings,
        my_pick_strategy=lambda undrafted, my_positions, picks: baseline_pick(undrafted, my_positions, settings),
        opponent_rng=random.Random(seed),
    )
    vor_result = run_full_draft(
        live_pool, teams, my_slot, total_rounds, settings,
        my_pick_strategy=lambda undrafted, my_positions, picks: vor_pick(
            undrafted, my_positions, settings, distributions, pos_ranks
        ),
        opponent_rng=random.Random(seed),  # same seed -> CRN
    )

    baseline_points, _ = score_roster(baseline_result.my_players, actual_points, settings)
    vor_points, _ = score_roster(vor_result.my_players, actual_points, settings)

    return VorReplayResult(year=year, my_slot=my_slot, baseline_points=baseline_points, vor_points=vor_points)


def run_vor_comparison(
    years: list[int], slots: list[int], settings: LeagueSettings, data_dir: Path, seed: int
) -> list[VorReplayResult]:
    raw_dir = data_dir / "raw"
    results = []

    for year in years:
        live_pool = ffc.fetch_adp(year, teams=settings.teams, cache_path=raw_dir / f".ffc_{settings.teams}_{year}.json")
        distributions = leakage_safe_distributions(settings, year, raw_dir)
        season_outcomes = nfl_stats.actual_fantasy_points(year, settings.scoring)
        actual_points = {normalize_name(o.name): o.points for o in season_outcomes}

        for slot in slots:
            results.append(
                replay_vor_vs_baseline(
                    year, slot, settings, live_pool, distributions, actual_points, seed=seed + year * 100 + slot
                )
            )
    return results


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", default=DEFAULT_BACKTEST_YEARS)
    parser.add_argument("--slots", type=int, nargs="+", default=list(range(1, 11)))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    settings = default_settings()
    print(f"VOR vs ADP+need baseline: years={args.years} slots={args.slots}")
    results = run_vor_comparison(args.years, args.slots, settings, args.data_dir, args.seed)

    deltas = [r.delta for r in results]
    wins = sum(1 for d in deltas if d > 0)
    print(f"\n{len(results)} replays -- VOR beat ADP+need baseline in {wins} ({wins / len(deltas):.0%})")
    print(f"mean delta: {statistics.mean(deltas):+.1f}   median delta: {statistics.median(deltas):+.1f}")
    print(f"min / max: {min(deltas):+.1f} / {max(deltas):+.1f}")


if __name__ == "__main__":
    main()
