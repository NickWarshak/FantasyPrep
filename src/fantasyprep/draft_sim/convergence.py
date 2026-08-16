"""Convergence check: does `recommend_positions`' top pick actually
stabilize as `num_sims` increases, or is `num_sims=50` (used by both the
live tool's backtest evaluation and the backtest harness) too noisy to
trust? Flagged as an open item in the backtest report; this answers it
empirically before committing hours of overnight compute to a specific
setting, rather than leaving it unchecked.

For a fixed, realistic mid-draft decision scenario (simulated up to
`--pick` using the same ADP+stdev opponent model everywhere, not a
hand-picked state), repeats `recommend_positions` several times at each
of several `num_sims` levels (a different RNG seed each repeat) and
reports two things per level:

- **Top-position agreement rate**: how often the single top-ranked
  position is the SAME across repeats. This is the decision-relevant
  number -- a recommendation that flips depending on random luck isn't
  trustworthy even if the underlying expected values look "close."
- **Value stdev per position**: spread of each position's own estimated
  expected value across repeats, in points.

Usage:
    python -m fantasyprep.draft_sim.convergence --year 2026 --pick 25
"""
from __future__ import annotations

import argparse
import random
import statistics
from collections import Counter
from pathlib import Path

from fantasyprep.draft_sim.opponent import sample_pick
from fantasyprep.draft_sim.points_model import HistoricalBootstrapModel
from fantasyprep.draft_sim.simulate import pick_owner, recommend_positions, state_from_picks
from fantasyprep.historical.outcomes import build_outcome_distributions
from fantasyprep.historical.sources import ffc
from fantasyprep.historical.sources.ffc import FfcPlayer
from fantasyprep.league.settings import LeagueSettings, default_settings
from fantasyprep.players.normalize import normalize_name


def simulate_to_pick(live_pool: list[FfcPlayer], up_to_pick: int, seed: int) -> list[dict]:
    """A realistic partial-draft state -- every team (including 'mine')
    drafts via the same ADP+stdev opponent model, so the resulting state
    isn't hand-picked to favor any particular outcome."""
    rng = random.Random(seed)
    picks: list[dict] = []
    drafted_names: set[str] = set()

    for pick_num in range(1, up_to_pick):
        undrafted = [p for p in live_pool if normalize_name(p.name) not in drafted_names]
        if not undrafted:
            break
        chosen = sample_pick(undrafted, pick_num, rng)
        picks.append({"pick": pick_num, "player": chosen.name})
        drafted_names.add(normalize_name(chosen.name))

    return picks


def check_convergence(
    live_pool: list[FfcPlayer],
    state,
    settings: LeagueSettings,
    points_model,
    num_sims_levels: list[int],
    repeats: int,
    base_seed: int = 0,
) -> dict[int, dict]:
    results = {}
    for num_sims in num_sims_levels:
        top_positions = []
        value_by_position: dict[str, list[float]] = {}

        for repeat in range(repeats):
            rng = random.Random(base_seed + num_sims * 1_000_000 + repeat)
            rows = recommend_positions(live_pool, state, settings, points_model, num_sims, rng)
            if not rows:
                continue
            top_positions.append(rows[0][0])
            for position, mean, _p25, _p75 in rows:
                value_by_position.setdefault(position, []).append(mean)

        agreement = Counter(top_positions)
        top_position, top_count = agreement.most_common(1)[0] if agreement else (None, 0)
        results[num_sims] = {
            "agreement_rate": top_count / repeats if repeats else 0.0,
            "most_common_top_position": top_position,
            "top_position_counts": dict(agreement),
            "value_stdev_by_position": {
                pos: statistics.stdev(vals) if len(vals) > 1 else 0.0
                for pos, vals in value_by_position.items()
            },
        }
    return results


def _print_results(results: dict[int, dict]) -> None:
    print(f"\n{'num_sims':>10}  {'agreement':>10}  {'top position':<14}  value stdev by position")
    for num_sims, r in results.items():
        stdevs = ", ".join(f"{pos}=+/-{sd:.1f}" for pos, sd in sorted(r["value_stdev_by_position"].items()))
        print(f"{num_sims:>10}  {r['agreement_rate']:>9.0%}  {r['most_common_top_position'] or '-':<14}  {stdevs}")
    print("\nagreement_rate = how often the top-ranked position was the same across repeats at that "
          "num_sims -- this is the number that actually matters for trusting a recommendation, not "
          "just whether the point estimates look close.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--pick", type=int, required=True, help="overall pick number to evaluate the decision at")
    parser.add_argument("--num-sims-levels", type=int, nargs="+", default=[20, 50, 100, 200, 500])
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--seed", type=int, default=0)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> dict[int, dict]:
    settings = default_settings()
    raw_dir = args.data_dir / "raw"

    live_pool = ffc.fetch_adp(
        args.year, teams=settings.teams, cache_path=raw_dir / f".ffc_{settings.teams}_{args.year}.json"
    )
    distributions = build_outcome_distributions(
        settings, cache_path=raw_dir / f".outcomes_{settings.teams}.json", adp_cache_dir=raw_dir
    )
    points_model = HistoricalBootstrapModel(distributions)

    picks = simulate_to_pick(live_pool, args.pick, seed=args.seed)
    my_slot = pick_owner(settings.teams, args.pick)
    state = state_from_picks(settings.teams, my_slot, picks)
    print(f"Evaluating pick {args.pick} (team {my_slot}) after a realistic {len(picks)}-pick partial draft")

    results = check_convergence(live_pool, state, settings, points_model, args.num_sims_levels, args.repeats, args.seed)
    _print_results(results)
    return results


def main(argv: list[str] | None = None) -> None:
    run(parse_args(argv))


if __name__ == "__main__":
    main()
