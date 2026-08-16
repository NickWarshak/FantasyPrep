"""Post-hoc analysis of backtest.py's results: where does the edge (or
deficit) actually concentrate, by position? The natural follow-up once a
backtest run completes (GPT's suggested "analyze where the model
gains/loses" step) -- built ahead of time so it's ready to run the
instant a run finishes, using the roster detail every replay's JSON
output already captures.

Round-by-round shape (which position gets taken in which round) turns out
to be recoverable from the existing schema without any new instrumentation,
despite an earlier version of this docstring claiming otherwise: a roster
tuple list's *order* already reflects real draft order. `run_full_draft`
appends to `my_players` only when `pick_owner(...) == my_slot`, in strictly
increasing `pick_num` order, and a standard snake draft gives each slot
exactly one pick per round -- so index i (0-based) in a roster list is
always that team's round-(i+1) pick, for every condition. No pick-number
field needed.

Usage:
    python -m fantasyprep.draft_sim.backtest_analysis --in data/backtest_results_big.json
    python -m fantasyprep.draft_sim.backtest_analysis --in data/backtest_results_big.json --shape
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from fantasyprep.draft_sim.roster import DraftedPlayer, starting_lineup_value_by_position
from fantasyprep.league.settings import LeagueSettings, default_settings


def position_breakdown(
    results: list[dict], settings: LeagueSettings, other_roster_key: str = "baseline_roster"
) -> dict[str, dict]:
    """For each roster position, mean *starter* contribution (not raw
    roster points -- bench doesn't count, same discipline as the primary
    metric) under some other condition vs. the model across all replays,
    and the delta. `other_roster_key` selects which condition to compare
    against (default 'baseline_roster' = ADP+need; pass 'vor_roster' to
    see where the model's edge over VOR specifically concentrates --
    same output schema either way, output keys stay 'baseline_mean'/
    'model_mean' regardless of which roster was actually used as the
    'other' side). Uses starting_lineup_value_by_position so a FLEX
    starter is attributed to their real position, not a separate FLEX
    bucket."""
    positions = [p for p in settings.roster_slots if p != "FLEX"]
    baseline_by_pos: dict[str, list[float]] = defaultdict(list)
    model_by_pos: dict[str, list[float]] = defaultdict(list)

    for r in results:
        baseline_players = [DraftedPlayer(name=n, position=p, points=pts) for n, p, pts in r[other_roster_key]]
        model_players = [DraftedPlayer(name=n, position=p, points=pts) for n, p, pts in r["model_roster"]]
        baseline_breakdown = starting_lineup_value_by_position(baseline_players, settings)
        model_breakdown = starting_lineup_value_by_position(model_players, settings)

        for pos in positions:
            baseline_by_pos[pos].append(baseline_breakdown.get(pos, 0.0))
            model_by_pos[pos].append(model_breakdown.get(pos, 0.0))

    breakdown = {}
    for pos in positions:
        b_vals, m_vals = baseline_by_pos[pos], model_by_pos[pos]
        b_mean = statistics.mean(b_vals) if b_vals else 0.0
        m_mean = statistics.mean(m_vals) if m_vals else 0.0
        deltas = [m - b for m, b in zip(m_vals, b_vals)]
        breakdown[pos] = {
            "baseline_mean": b_mean,
            "model_mean": m_mean,
            "delta": m_mean - b_mean,
            "win_rate": sum(1 for d in deltas if d > 0) / len(deltas) if deltas else 0.0,
            "n": len(b_vals),
        }
    return breakdown


def draft_shape_by_round(results: list[dict], roster_key: str = "model_roster") -> dict[int, dict[str, float]]:
    """For each round, what fraction of replays took each position with
    that round's pick, under one condition (`roster_key`). Round = 1 +
    index in the roster tuple list -- see module docstring for why that's
    valid. Rosters shorter than the max round count (shouldn't normally
    happen, but e.g. an early-stopped draft) just don't contribute to
    rounds past their own length, rather than crashing."""
    by_round: dict[int, Counter] = defaultdict(Counter)
    n = len(results)

    for r in results:
        roster = r[roster_key]
        for i, (_name, position, _pts) in enumerate(roster):
            by_round[i + 1][position] += 1

    return {
        round_num: {pos: count / n for pos, count in counts.items()}
        for round_num, counts in sorted(by_round.items())
    }


def first_round_by_position(results: list[dict], roster_key: str = "model_roster") -> dict[str, dict]:
    """For each position, the average round of that condition's *first*
    pick at that position, and what fraction of replays ever took it at
    all (a position taken late/rarely still gets an honest average -- only
    over the replays that actually drafted it; the "how often" fraction is
    reported alongside so a rare-but-early position isn't misread as
    common-and-early)."""
    n = len(results)
    first_rounds: dict[str, list[int]] = defaultdict(list)

    for r in results:
        roster = r[roster_key]
        seen: set[str] = set()
        for i, (_name, position, _pts) in enumerate(roster):
            if position not in seen:
                seen.add(position)
                first_rounds[position].append(i + 1)

    return {
        pos: {"avg_first_round": statistics.mean(rounds), "frequency": len(rounds) / n}
        for pos, rounds in first_rounds.items()
    }


def picks_per_position(results: list[dict], roster_key: str = "model_roster") -> dict[str, float]:
    """Average number of picks spent on each position per full roster,
    under one condition."""
    n = len(results)
    counts: Counter = Counter()
    for r in results:
        for _name, position, _pts in r[roster_key]:
            counts[position] += 1
    return {pos: count / n for pos, count in counts.items()}


def _print_breakdown(breakdown: dict[str, dict]) -> None:
    print(f"\n{'position':<10}{'baseline':>12}{'model':>12}{'delta':>12}{'win rate':>12}")
    for pos, row in sorted(breakdown.items(), key=lambda kv: -abs(kv[1]["delta"])):
        print(f"{pos:<10}{row['baseline_mean']:>12.1f}{row['model_mean']:>12.1f}"
              f"{row['delta']:>+12.1f}{row['win_rate']:>11.0%}")
    print("\nEach row: mean *starter* contribution per replay (bench excluded, same discipline as "
          "the headline metric) for that position, under each strategy, and the delta -- where the "
          "edge (or deficit) actually concentrates, not just the aggregate number.")


_ROSTER_KEY = {"baseline": "baseline_roster", "pure_adp": "pure_adp_roster", "vor": "vor_roster", "model": "model_roster"}


def _print_shape(results: list[dict], roster_key: str, condition_label: str) -> None:
    shape = draft_shape_by_round(results, roster_key)
    first = first_round_by_position(results, roster_key)
    picks = picks_per_position(results, roster_key)
    positions = sorted(picks, key=lambda p: -picks[p])

    print(f"\n{condition_label} -- round-by-round position shape "
          f"({len(results)} replays, {len(shape)} rounds):")
    header = f"{'round':<7}" + "".join(f"{p:>7}" for p in positions)
    print(header)
    for round_num, dist in shape.items():
        row = f"{round_num:<7}" + "".join(f"{dist.get(p, 0.0):>6.0%} " for p in positions)
        print(row)

    print(f"\n{'position':<10}{'avg picks':>11}{'avg 1st rd':>12}{'freq taken':>12}")
    for pos in positions:
        f = first.get(pos, {"avg_first_round": float("nan"), "frequency": 0.0})
        print(f"{pos:<10}{picks[pos]:>11.2f}{f['avg_first_round']:>12.1f}{f['frequency']:>11.0%}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="in_path", type=Path, required=True)
    parser.add_argument("--vs", choices=[k for k in _ROSTER_KEY if k != "model"], default="baseline",
                         help="which condition to compare the model against (default: ADP+need baseline). "
                         "'vor' requires a results file with VOR as a 4th condition.")
    parser.add_argument("--shape", choices=list(_ROSTER_KEY), default=None,
                         help="instead of the position-breakdown table, print round-by-round draft shape "
                         "for one condition (which position gets taken in which round, avg first round per "
                         "position, avg picks per position).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    results = json.loads(args.in_path.read_text(encoding="utf-8"))
    settings = default_settings()

    if args.shape:
        roster_key = _ROSTER_KEY[args.shape]
        if results and roster_key not in results[0]:
            raise SystemExit(f"'{roster_key}' not present in this results file")
        _print_shape(results, roster_key, args.shape)
        return

    other_key = _ROSTER_KEY[args.vs]
    if results and other_key not in results[0]:
        raise SystemExit(f"'{other_key}' not present in this results file -- use --vs baseline instead")
    breakdown = position_breakdown(results, settings, other_key)
    print(f"Analyzed {len(results)} replays from {args.in_path} (model vs. {args.vs})")
    _print_breakdown(breakdown)


if __name__ == "__main__":
    main()
