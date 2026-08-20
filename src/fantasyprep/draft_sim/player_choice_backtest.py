"""Does player-vs-player-in-position (`player_choice.py`) actually move the
flagship number, or does it just occasionally pick a different guy without
it mattering?

Every other idea in this project (VOR, the tail-floor opponent fix, Draft
Now vs. Wait) eventually gets checked against the same bar: does it beat
the CURRENT model on real historical outcomes, with the same season-level-
clustered confidence intervals as the headline backtest (`backtest.py`).
This module runs that exact comparison for player-vs-player: two full,
real draft replays per (year, slot, seed) under common random numbers (same
opponent-room seed) -- one using the current model's strategy
(`draft_now_vs_wait._model_driven_strategy`, naive best-ADP-in-position),
one using `player_choice.make_player_choice_strategy` (simulates the top
`--top-n` players at the recommended position individually) -- scored on
real points.

Reuses `backtest.py`'s `wilson_interval`/`cluster_bootstrap_ci` directly
(duck-typed: `PlayerChoiceReplayResult` exposes the same `.cluster_key`
attribute those functions read) rather than reimplementing the same
statistics a second time.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from fantasyprep.draft_sim.backtest import (
    DEFAULT_BACKTEST_YEARS,
    cluster_bootstrap_ci,
    leakage_safe_distributions,
    run_full_draft,
    score_roster,
)
from fantasyprep.draft_sim.draft_now_vs_wait import _model_driven_strategy
from fantasyprep.draft_sim.opponent import pick_weight, pick_weight_with_tail_floor
from fantasyprep.draft_sim.player_choice import make_player_choice_strategy
from fantasyprep.draft_sim.points_model import HistoricalBootstrapModel
from fantasyprep.historical import weekly_stats
from fantasyprep.historical.sources import ffc, nfl_stats
from fantasyprep.league.settings import LeagueSettings, default_settings
from fantasyprep.players.normalize import normalize_name

OPPONENT_WEIGHT_FN = {"gaussian": pick_weight, "gaussian-tail-floor": pick_weight_with_tail_floor}


@dataclass(frozen=True)
class PlayerChoiceReplayResult:
    year: int
    my_slot: int
    seed_index: int
    model_points: float  # current model: naive best-ADP-in-position
    player_choice_points: float  # new: player-vs-player-in-position
    model_roster: list[tuple[str, str, float]]
    player_choice_roster: list[tuple[str, str, float]]

    @property
    def delta(self) -> float:
        """Player-choice model vs. the current model -- the one comparison
        this module exists to answer."""
        return self.player_choice_points - self.model_points

    @property
    def cluster_key(self) -> int:
        """Same season-level clustering rationale as backtest.py's
        ReplayResult.cluster_key -- slots within a season share that
        season's real outcomes, so the season (not the (season, slot)
        cell) is the independent unit of evidence."""
        return self.year


def replay_one(
    year: int,
    my_slot: int,
    settings: LeagueSettings,
    live_pool: list[ffc.FfcPlayer],
    points_model: HistoricalBootstrapModel,
    actual_points: dict[str, float],
    num_sims: int,
    seed: int,
    seed_index: int = 0,
    top_n: int = 3,
    opponent_weight_fn=pick_weight,
    weekly: dict[str, dict[int, float]] | None = None,
) -> PlayerChoiceReplayResult:
    total_rounds = sum(settings.roster_slots.values()) + settings.bench
    teams = settings.teams

    model_result = run_full_draft(
        live_pool, teams, my_slot, total_rounds, settings,
        my_pick_strategy=_model_driven_strategy(
            live_pool, teams, my_slot, settings, points_model, num_sims, seed,
            opponent_weight_fn=opponent_weight_fn,
        ),
        opponent_rng=random.Random(seed), opponent_weight_fn=opponent_weight_fn,
    )
    player_choice_result = run_full_draft(
        live_pool, teams, my_slot, total_rounds, settings,
        my_pick_strategy=make_player_choice_strategy(
            live_pool, teams, my_slot, settings, points_model, num_sims, seed, top_n=top_n,
            opponent_weight_fn=opponent_weight_fn,
        ),
        opponent_rng=random.Random(seed), opponent_weight_fn=opponent_weight_fn,  # same seed -> CRN
    )

    model_points, model_detail = score_roster(model_result.my_players, actual_points, settings, weekly)
    player_choice_points, player_choice_detail = score_roster(
        player_choice_result.my_players, actual_points, settings, weekly
    )

    return PlayerChoiceReplayResult(
        year=year, my_slot=my_slot, seed_index=seed_index,
        model_points=model_points, player_choice_points=player_choice_points,
        model_roster=model_detail, player_choice_roster=player_choice_detail,
    )


def run_backtest(
    years: list[int],
    slots: list[int],
    settings: LeagueSettings,
    data_dir: Path,
    num_sims: int,
    seed: int,
    num_seeds: int = 1,
    scoring_mode: str = "season-total",
    top_n: int = 3,
    opponent_model: str = "gaussian-tail-floor",
    points_source: str = "historical",
) -> list[PlayerChoiceReplayResult]:
    """Same year/slot/seed loop and seeding scheme as `backtest.run_backtest`
    (`seed + year * 10_000 + slot * 100 + seed_index`) so results from the
    two modules use comparable, independently-reproducible opponent-room
    draws. `opponent_model` defaults to `gaussian-tail-floor` here (not
    `gaussian`, unlike `backtest.py`) since this is a new comparison with no
    existing runs to stay bit-comparable with -- may as well start from the
    more realistic opponent model rather than the one already documented as
    a known bug."""
    raw_dir = data_dir / "raw"
    results = []
    opponent_weight_fn = OPPONENT_WEIGHT_FN[opponent_model]

    for year in years:
        live_cache = raw_dir / f".ffc_{settings.teams}_{year}.json"
        live_pool = ffc.fetch_adp(year, teams=settings.teams, cache_path=live_cache)

        distributions = leakage_safe_distributions(settings, year, raw_dir)
        points_model = HistoricalBootstrapModel(distributions)

        if points_source == "profile":
            # The whole point of re-running this experiment. Under the bucket
            # model two candidates in the same 3-rank bucket are statistically
            # IDENTICAL, so "simulate each individually" was comparing noise --
            # which is exactly what the original negative result concluded.
            # Profile distributions are fitted on strictly-prior seasons only,
            # so this stays leakage-safe.
            from fantasyprep.research.profile_points_model import build_profile_points_model

            points_model = build_profile_points_model(year, fallback=points_model)
            print(f"  {year}: {points_model.coverage} players have their own distribution")

        # Why this matters here specifically: the objective is structurally
        # variance-seeking under hindsight scoring, and a per-player points model
        # gives candidates DIFFERENT variances. Under season-total scoring the
        # profile arm could therefore win for a purely mechanical reason rather
        # than because it picks better players.
        weekly_table = (
            weekly_stats.weekly_points_by_player(year, settings.scoring)
            if scoring_mode == "weekly-realistic"
            else None
        )

        if scoring_mode == "waiver-adjusted":
            weekly_rank_cutoff = ffc.derive_rank_cutoff(live_pool, settings)
            actual_points = weekly_stats.waiver_adjusted_actual_points(year, settings.scoring, weekly_rank_cutoff)
        else:
            season_outcomes = nfl_stats.actual_fantasy_points(year, settings.scoring)
            actual_points = {normalize_name(o.name): o.points for o in season_outcomes}

        for slot in slots:
            for seed_index in range(num_seeds):
                result = replay_one(
                    year, slot, settings, live_pool, points_model, actual_points, num_sims,
                    weekly=weekly_table,
                    seed=seed + year * 10_000 + slot * 100 + seed_index, seed_index=seed_index,
                    top_n=top_n, opponent_weight_fn=opponent_weight_fn,
                )
                results.append(result)

    return results


def _percentile(sorted_values: list[float], pct: float) -> float:
    idx = min(len(sorted_values) - 1, max(0, int(pct * len(sorted_values))))
    return sorted_values[idx]


def _win_rate_stat(pooled_deltas: list[float]) -> float:
    return sum(1 for d in pooled_deltas if d > 0) / len(pooled_deltas)


def _summarize(results: list[PlayerChoiceReplayResult]) -> None:
    n_seasons = len({r.cluster_key for r in results})
    n_cells = len({(r.year, r.my_slot) for r in results})
    print(f"\n{len(results)} replays across {n_cells} (year, slot) cells in {n_seasons} seasons")
    print("Player-choice model (simulates top-N players within the recommended position) vs. "
          "the CURRENT model (naive best-ADP-in-position) -- same season-level-clustered 95% CIs "
          "as backtest.py's headline numbers.")

    deltas = sorted(r.delta for r in results)
    n = len(deltas)
    wins = sum(1 for d in deltas if d > 0)
    losses = sum(1 for d in deltas if d < 0)
    ties = n - wins - losses
    win_lo, win_hi = cluster_bootstrap_ci(results, _win_rate_stat, seed=0, value_fn=lambda r: r.delta)
    mean_lo, mean_hi = cluster_bootstrap_ci(results, statistics.mean, seed=1, value_fn=lambda r: r.delta)
    median_lo, median_hi = cluster_bootstrap_ci(results, statistics.median, seed=2, value_fn=lambda r: r.delta)

    print(f"\n--- player-choice vs current model ---")
    print(f"player-choice beat current model in {wins} ({wins / n:.0%}, 95% CI {win_lo:.0%}-{win_hi:.0%}), "
          f"lost {losses}, tied {ties}")
    print(f"mean delta:   {statistics.mean(deltas):+.1f}  (95% CI {mean_lo:+.1f} to {mean_hi:+.1f})")
    print(f"median delta: {statistics.median(deltas):+.1f}  (95% CI {median_lo:+.1f} to {median_hi:+.1f})")
    print(f"min / P10 / P90 / max: "
          f"{deltas[0]:+.1f} / {_percentile(deltas, 0.1):+.1f} / {_percentile(deltas, 0.9):+.1f} / {deltas[-1]:+.1f}")

    identical = sum(1 for r in results if r.model_roster == r.player_choice_roster)
    print(f"\n{identical}/{n} replays ended with an IDENTICAL roster to the current model "
          f"({identical / n:.0%}) -- the ceiling on how often this feature could possibly matter, "
          "given HistoricalBootstrapModel's bucket-pooling (see player_choice.py's module docstring).")

    print("\nBiggest swings:")
    for r in sorted(results, key=lambda r: abs(r.delta), reverse=True)[:5]:
        print(f"  {r.year} slot {r.my_slot} seed {r.seed_index}: current {r.model_points:.1f} vs "
              f"player-choice {r.player_choice_points:.1f} (delta {r.delta:+.1f})")


def _result_to_dict(r: PlayerChoiceReplayResult) -> dict:
    return {
        "year": r.year, "my_slot": r.my_slot, "seed_index": r.seed_index,
        "model_points": r.model_points, "player_choice_points": r.player_choice_points,
        "delta": r.delta,
        "model_roster": r.model_roster, "player_choice_roster": r.player_choice_roster,
    }


def _compact_summary(results: list[PlayerChoiceReplayResult]) -> dict:
    n = len(results)
    deltas = [r.delta for r in results]
    identical = sum(1 for r in results if r.model_roster == r.player_choice_roster)
    return {
        "n": n,
        "win_rate": sum(1 for d in deltas if d > 0) / n,
        "mean_delta": statistics.mean(deltas),
        "median_delta": statistics.median(deltas),
        "identical_roster_rate": identical / n,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", default=DEFAULT_BACKTEST_YEARS)
    parser.add_argument("--slots", type=int, nargs="+", default=list(range(1, 11)))
    parser.add_argument("--num-sims", type=int, default=100)
    parser.add_argument("--num-seeds", type=int, default=1)
    parser.add_argument("--top-n", type=int, default=3, help="how many top-ADP players per position to compare")
    parser.add_argument("--scoring-mode",
                        choices=["season-total", "waiver-adjusted", "weekly-realistic"],
                        default="season-total")
    parser.add_argument("--points-source", choices=["historical", "profile"], default="historical",
                        help="'historical' (default) is the bucket bootstrap, under which two "
                             "candidates in the same 3-rank bucket are statistically IDENTICAL -- "
                             "the condition the original negative result was measured under. "
                             "'profile' gives every player his own quantile distribution, fitted "
                             "on strictly-prior seasons, so candidates are genuinely different.")
    parser.add_argument(
        "--opponent-model", choices=list(OPPONENT_WEIGHT_FN), default="gaussian-tail-floor",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--experiment-name", type=str, default=None)
    parser.add_argument("--experiment-notes", type=str, default="")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> list[PlayerChoiceReplayResult]:
    settings = default_settings()
    print(f"Player-choice backtest: years={args.years} slots={args.slots} num_sims={args.num_sims} "
          f"num_seeds={args.num_seeds} top_n={args.top_n} scoring_mode={args.scoring_mode} "
          f"opponent_model={args.opponent_model} points_source={args.points_source}")
    results = run_backtest(
        args.years, args.slots, settings, args.data_dir, args.num_sims, args.seed, args.num_seeds,
        scoring_mode=args.scoring_mode, top_n=args.top_n, opponent_model=args.opponent_model,
        points_source=args.points_source,
    )
    _summarize(results)

    if args.out:
        args.out.write_text(json.dumps([_result_to_dict(r) for r in results], indent=2), encoding="utf-8")
        print(f"\nWrote {len(results)} replay results to {args.out}")

    if args.experiment_name:
        from fantasyprep.draft_sim.experiment_registry import log_experiment

        params = {
            "years": args.years, "slots": args.slots, "num_sims": args.num_sims,
            "num_seeds": args.num_seeds, "top_n": args.top_n, "seed": args.seed,
            "scoring_mode": args.scoring_mode, "opponent_model": args.opponent_model,
            "points_source": args.points_source,
        }
        path = log_experiment(
            args.data_dir, args.experiment_name, args.experiment_notes, params, _compact_summary(results),
            reproducible=(os.environ.get("PYTHONHASHSEED") == "0"),
        )
        print(f"Logged experiment '{args.experiment_name}' to {path}")

    return results


def main(argv: list[str] | None = None) -> None:
    _ensure_fixed_hash_seed(argv)
    run(parse_args(argv))


def _ensure_fixed_hash_seed(argv: list[str] | None) -> None:
    """Same fix as `backtest.py`'s `_ensure_fixed_hash_seed` (see its
    docstring for the full story) -- Python's per-process string-hash
    randomization leaks into set/dict iteration order used throughout this
    codebase, so `--seed` alone doesn't make a run reproducible without
    also pinning `PYTHONHASHSEED`."""
    if os.environ.get("PYTHONHASHSEED") != "0":
        real_args = sys.argv[1:] if argv is None else argv
        env = {**os.environ, "PYTHONHASHSEED": "0"}
        result = subprocess.run(
            [sys.executable, "-m", "fantasyprep.draft_sim.player_choice_backtest", *real_args], env=env
        )
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
