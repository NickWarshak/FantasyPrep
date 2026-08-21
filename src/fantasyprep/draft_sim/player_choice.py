"""Player vs. Player (within position) -- a standalone diagnostic, not wired
into live recommendations yet, same posture as draft_now_vs_wait.py.

`recommend_positions`/`simulate_position_choice` always take the single
best-ADP player at a recommended position as "my pick now" -- an
unexamined assumption baked into every existing use of the simulator, not
something it has ever actually tested. This module asks the question
directly: among the top `top_n` undrafted players at a position (by ADP),
does simulating each one individually as "my pick now" ever produce a
different ranking than "best ADP wins," and if so, does picking the
simulator's actual top choice do better on real historical outcomes than
the naive best-ADP default?

One caveat baked in by the existing points model, not new here:
`HistoricalBootstrapModel` pools outcomes into buckets of 3 consecutive
draft-rank picks (`outcomes.py`'s `BUCKET_WIDTH`) -- two players in the
same bucket are statistically IDENTICAL to it (same distribution to
bootstrap a points sample from), so this diagnostic can only find a real
difference between players who land in *different* buckets. Pass
`--points-source espn` (`simulate.py`'s `EspnProjectionModel`, named
per-player projections) for a version where every candidate is genuinely
differentiated, not just by which 3-rank bucket they fall in.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass

from fantasyprep.draft_sim.opponent import pick_weight, pick_weight_with_tail_floor, sample_pick
from fantasyprep.draft_sim.points_model import HistoricalBootstrapModel, PointsModel
from fantasyprep.draft_sim.roster import (
    DraftedPlayer,
    best_marginal_player,
    starting_lineup_value,
)
from fantasyprep.draft_sim.simulate import DraftState, my_pick_numbers, state_from_picks
from fantasyprep.historical.sources import ffc
from fantasyprep.league.settings import LeagueSettings
from fantasyprep.players.normalize import normalize_name


def simulate_player_choice(
    candidate_player: ffc.FfcPlayer,
    live_pool: list[ffc.FfcPlayer],
    state: DraftState,
    settings: LeagueSettings,
    points_model: PointsModel,
    num_sims: int,
    rng: random.Random,
    opponent_weight_fn=pick_weight,
) -> list[float] | None:
    """Completed-roster value if I take this SPECIFIC player right now,
    instead of `simulate_position_choice`'s always-best-ADP-at-the-position
    default. A close variant of `simulate_position_choice` (identical
    lookahead mechanics), not a modification of it -- that function is
    validated and used by the live tool and backtest; this lives entirely
    separately so nothing here can affect it, the same posture
    `draft_now_vs_wait.py`'s `simulate_wait_and_target` already takes."""
    pos_ranks = ffc.position_ranks(live_pool)
    by_name = {normalize_name(p.name): p for p in live_pool}
    undrafted = [p for p in live_pool if normalize_name(p.name) not in state.drafted_names]
    if normalize_name(candidate_player.name) not in {normalize_name(p.name) for p in undrafted}:
        return None

    total_rounds = sum(settings.roster_slots.values()) + settings.bench
    my_picks = [n for n in my_pick_numbers(state.teams, state.my_draft_slot, total_rounds) if n >= state.current_pick]
    if not my_picks:
        return None
    last_relevant_pick = my_picks[-1]
    my_pick_set = set(my_picks[1:])  # first entry is the current pick, already handled

    already_mine = [by_name[name] for name in state.my_names if name in by_name]

    results = []
    for _ in range(num_sims):
        remaining = [p for p in undrafted if p is not candidate_player]
        my_team = list(already_mine) + [candidate_player]

        for pick_num in range(state.current_pick + 1, last_relevant_pick + 1):
            if pick_num in state.assigned:
                continue
            if not remaining:
                break
            chosen = sample_pick(remaining, pick_num, rng, weight_fn=opponent_weight_fn)
            remaining.remove(chosen)
            if pick_num in my_pick_set:
                my_team.append(chosen)

        drafted = [
            DraftedPlayer(name=p.name, position=p.position, points=points_model.sample(p, pos_ranks, rng))
            for p in my_team
        ]
        results.append(starting_lineup_value(drafted, settings))

    return results


@dataclass(frozen=True)
class PlayerRecommendation:
    player: ffc.FfcPlayer
    mean: float
    p25: float
    p75: float


def recommend_players(
    candidate_position: str,
    live_pool: list[ffc.FfcPlayer],
    state: DraftState,
    settings: LeagueSettings,
    points_model: PointsModel,
    num_sims: int,
    rng: random.Random,
    top_n: int = 3,
    opponent_weight_fn=pick_weight,
) -> list[PlayerRecommendation]:
    """Ranks the top `top_n` undrafted players (by ADP) at
    `candidate_position` by simulated completed-roster EV -- sorted best
    first. `top_n` caps this at the tier actually being considered rather
    than every undrafted player at the position (irrelevant this deep, and
    multiplies simulation cost linearly with however many are compared)."""
    undrafted = [p for p in live_pool if normalize_name(p.name) not in state.drafted_names]
    candidates = sorted((p for p in undrafted if p.position == candidate_position), key=lambda p: p.adp)[:top_n]

    rows = []
    for player in candidates:
        results = simulate_player_choice(
            player, live_pool, state, settings, points_model, num_sims, rng,
            opponent_weight_fn=opponent_weight_fn,
        )
        if results is None:
            continue
        mean = statistics.mean(results)
        p25 = statistics.quantiles(results, n=4)[0] if len(results) >= 4 else min(results)
        p75 = statistics.quantiles(results, n=4)[2] if len(results) >= 4 else max(results)
        rows.append(PlayerRecommendation(player=player, mean=mean, p25=p25, p75=p75))

    rows.sort(key=lambda r: r.mean, reverse=True)
    return rows


def make_player_choice_strategy(
    live_pool: list[ffc.FfcPlayer],
    teams: int,
    my_slot: int,
    settings: LeagueSettings,
    points_model: PointsModel,
    num_sims: int,
    seed: int,
    top_n: int = 3,
    opponent_weight_fn=pick_weight,
):
    """A `recommend_players`-driven strategy for `backtest.run_full_draft`:
    at every decision, `recommend_positions` still picks the position
    (unchanged), but the specific player comes from `recommend_players`
    instead of naive best-ADP. Mirrors
    `draft_now_vs_wait._model_driven_strategy`'s structure exactly (same
    seeding scheme: `seed + pick_num`) so a real full-draft replay using
    this strategy is directly comparable to one using the current model's
    strategy under common random numbers (same `opponent_rng` seed).

    A second, distinct RNG stream (`seed + pick_num + 500_000`, well clear
    of any `pick_num` this project's draft lengths could ever reach) drives
    `recommend_players`' own internal lookahead -- kept independent of the
    `recommend_positions` call's stream so evaluating extra player
    candidates doesn't perturb the position call's random draws relative
    to the current model's strategy, which only ever makes the one call."""
    from fantasyprep.draft_sim.backtest import baseline_pick
    from fantasyprep.draft_sim.simulate import recommend_positions

    def strategy(undrafted, my_positions, picks):
        pick_num = len(picks) + 1
        sub_state = state_from_picks(teams, my_slot, picks)
        rows = recommend_positions(
            live_pool, sub_state, settings, points_model, num_sims, random.Random(seed + pick_num),
            opponent_weight_fn=opponent_weight_fn,
        )
        if not rows:
            return baseline_pick(undrafted, my_positions, settings)
        top_position = rows[0][0]

        player_rows = recommend_players(
            top_position, live_pool, sub_state, settings, points_model, num_sims,
            random.Random(seed + pick_num + 500_000), top_n=top_n, opponent_weight_fn=opponent_weight_fn,
        )
        candidates = [p for p in undrafted if p.position == top_position]
        if not candidates:
            return baseline_pick(undrafted, my_positions, settings)
        if not player_rows:
            return min(candidates, key=lambda p: p.adp)

        chosen_name = normalize_name(player_rows[0].player.name)
        match = [p for p in candidates if normalize_name(p.name) == chosen_name]
        return match[0] if match else min(candidates, key=lambda p: p.adp)

    return strategy


def validate_against_real_outcome(
    decision_pick: int,
    live_pool: list[ffc.FfcPlayer],
    state: DraftState,
    settings: LeagueSettings,
    points_model: PointsModel,
    actual_points: dict[str, float],
    num_sims: int,
    seed: int,
    top_n: int = 3,
    opponent_weight_fn=pick_weight,
) -> tuple[float, float, bool] | None:
    """Runs two REAL, complete draft replays from `decision_pick`: one
    where the decision pick takes `recommend_players`' top choice (within
    `recommend_positions`' top-ranked position), one where it takes the
    naive best-ADP player at that same position -- both revert to normal
    `recommend_positions`-driven picking afterward (same
    `_model_driven_strategy` `draft_now_vs_wait.py` already uses). Scored
    on real historical points via CRN (same seed for both branches, so the
    comparison isolates this one decision).

    Returns (player_choice_points, naive_points, picks_differed).
    `picks_differed` is False whenever `recommend_players` agrees with
    best-ADP anyway -- under `HistoricalBootstrapModel`'s bucket pooling
    (see module docstring) that's the expected common case, and when it's
    False the two replays are literally identical, so that sample carries
    no signal about the player-level choice specifically."""
    from fantasyprep.draft_sim.backtest import run_full_draft, score_roster
    from fantasyprep.draft_sim.draft_now_vs_wait import _model_driven_strategy
    from fantasyprep.draft_sim.simulate import recommend_positions

    teams = state.teams
    my_slot = state.my_draft_slot
    total_rounds = sum(settings.roster_slots.values()) + settings.bench

    rows = recommend_positions(
        live_pool, state, settings, points_model, num_sims, random.Random(seed),
        opponent_weight_fn=opponent_weight_fn,
    )
    if not rows:
        return None
    top_position = rows[0][0]

    player_rows = recommend_players(
        top_position, live_pool, state, settings, points_model, num_sims, random.Random(seed),
        top_n=top_n, opponent_weight_fn=opponent_weight_fn,
    )
    if not player_rows:
        return None
    chosen_name = normalize_name(player_rows[0].player.name)

    undrafted_now = [p for p in live_pool if normalize_name(p.name) not in state.drafted_names]
    naive_candidates = [p for p in undrafted_now if p.position == top_position]
    if not naive_candidates:
        return None
    naive_name = normalize_name(min(naive_candidates, key=lambda p: p.adp).name)

    picks_differed = chosen_name != naive_name

    base_strategy = _model_driven_strategy(
        live_pool, teams, my_slot, settings, points_model, num_sims, seed,
        opponent_weight_fn=opponent_weight_fn,
    )

    def make_strategy(forced_name: str):
        def strategy(undrafted, my_positions, picks):
            pick_num = len(picks) + 1
            if pick_num == decision_pick:
                match = [p for p in undrafted if normalize_name(p.name) == forced_name]
                if match:
                    return match[0]
            return base_strategy(undrafted, my_positions, picks)
        return strategy

    player_choice_result = run_full_draft(
        live_pool, teams, my_slot, total_rounds, settings,
        my_pick_strategy=make_strategy(chosen_name),
        opponent_rng=random.Random(seed), opponent_weight_fn=opponent_weight_fn,
    )
    naive_result = run_full_draft(
        live_pool, teams, my_slot, total_rounds, settings,
        my_pick_strategy=make_strategy(naive_name),
        opponent_rng=random.Random(seed), opponent_weight_fn=opponent_weight_fn,  # same seed -> CRN
    )

    player_choice_points, _ = score_roster(player_choice_result.my_players, actual_points, settings)
    naive_points, _ = score_roster(naive_result.my_players, actual_points, settings)
    return player_choice_points, naive_points, picks_differed


def validate_against_real_outcome_averaged(
    decision_pick: int,
    live_pool: list[ffc.FfcPlayer],
    state: DraftState,
    settings: LeagueSettings,
    points_model: PointsModel,
    actual_points: dict[str, float],
    num_sims: int,
    base_seed: int,
    num_replay_seeds: int = 3,
    top_n: int = 3,
    opponent_weight_fn=pick_weight,
) -> tuple[float, float, bool] | None:
    """Averages `validate_against_real_outcome` over several independent
    replay seeds instead of trusting a single one -- same rationale as
    `draft_now_vs_wait.validate_against_real_outcome_averaged`: everything
    downstream of the decision pick is filled in stochastically, so a
    single replay's real point total is noisy independent of whether the
    decision itself was good. `picks_differed` is taken from the first
    seed (the decision-pick logic itself is deterministic given
    `decision_pick`/`top_n`/`num_sims` -- only what happens *after* it
    varies by seed, which is exactly what averaging is for).

    Seeds are spaced 1000 apart, matching `draft_now_vs_wait.py`'s
    convention, so they don't collide with `_model_driven_strategy`'s own
    internal `random.Random(seed + pick_num)` draws for a single replay."""
    player_total = 0.0
    naive_total = 0.0
    picks_differed = None
    for i in range(num_replay_seeds):
        result = validate_against_real_outcome(
            decision_pick, live_pool, state, settings, points_model, actual_points, num_sims,
            base_seed + i * 1000, top_n=top_n, opponent_weight_fn=opponent_weight_fn,
        )
        if result is None:
            return None
        player_points, naive_points, differed = result
        if picks_differed is None:
            picks_differed = differed
        player_total += player_points
        naive_total += naive_points
    return player_total / num_replay_seeds, naive_total / num_replay_seeds, bool(picks_differed)


def generate_validation_samples(years=range(2015, 2025), picks=(10, 25, 45, 65, 90), seed_start: int = 1):
    """Same systematic (year, pick, seed) sweep as
    `draft_now_vs_wait.generate_validation_samples` -- reimplemented here
    (not imported) so this module's CLI stays independently runnable, same
    reasoning `draft_now_vs_wait.py` gives for not importing `backtest.py`'s
    year list eagerly."""
    samples = []
    seed = seed_start
    for year in years:
        for pick in picks:
            samples.append((year, pick, seed))
            seed += 1
    return samples


QUICK_VALIDATION_SAMPLES = [
    (2023, 12, 1), (2023, 25, 2), (2023, 45, 3), (2023, 60, 4),
    (2024, 15, 5), (2024, 30, 6), (2024, 55, 7), (2024, 70, 8),
]

DEFAULT_VALIDATION_SAMPLES = generate_validation_samples()


def _cli_validate(
    samples: list[tuple[int, int, int]], num_sims: int, data_dir, top_n: int, num_replay_seeds: int,
    opponent_weight_fn=pick_weight,
) -> None:
    """Runs `validate_against_real_outcome_averaged` across real decision
    points and reports (a) how often the player-vs-player choice actually
    differs from naive best-ADP at all, and (b) among samples where it
    does, how often picking the simulator's actual top player beat naive
    best-ADP on real points -- the two questions this diagnostic needs
    answered before it's worth wiring into a live recommendation: does
    player-level discrimination ever matter, and when it does, is it
    right."""
    import random as _random

    from fantasyprep.draft_sim.backtest import leakage_safe_distributions
    from fantasyprep.draft_sim.convergence import simulate_to_pick
    from fantasyprep.draft_sim.simulate import pick_owner
    from fantasyprep.historical.sources import nfl_stats
    from fantasyprep.league.settings import default_settings

    settings = default_settings()
    raw_dir = data_dir / "raw"

    cache: dict[int, tuple] = {}
    all_results = []  # (year, pick, differed, player_won)

    for year, pick, seed in samples:
        if year not in cache:
            live_pool = ffc.fetch_adp(year, teams=settings.teams, cache_path=raw_dir / f".ffc_{settings.teams}_{year}.json")
            distributions = leakage_safe_distributions(settings, year, raw_dir)
            points_model = HistoricalBootstrapModel(distributions)
            season_outcomes = nfl_stats.actual_fantasy_points(year, settings.scoring)
            actual_points = {normalize_name(o.name): o.points for o in season_outcomes}
            cache[year] = (live_pool, points_model, actual_points)
        live_pool, points_model, actual_points = cache[year]

        picks = simulate_to_pick(live_pool, pick, seed=seed)
        my_slot = pick_owner(settings.teams, pick)
        state = state_from_picks(settings.teams, my_slot, picks)

        result = validate_against_real_outcome_averaged(
            pick, live_pool, state, settings, points_model, actual_points, num_sims, seed,
            num_replay_seeds=num_replay_seeds, top_n=top_n, opponent_weight_fn=opponent_weight_fn,
        )
        if result is None:
            print(f"{year} pick {pick}: skipped, no candidates/decision available")
            continue

        player_points, naive_points, differed = result
        player_won = player_points > naive_points
        all_results.append((year, pick, differed, player_won))

        status = "SAME PICK" if not differed else ("PLAYER-CHOICE WON" if player_won else "NAIVE WON")
        print(f"{year} pick {pick:3d}: player-choice={player_points:.1f}  naive-ADP={naive_points:.1f}  "
              f"delta={player_points - naive_points:+.1f}  {status}")

    n = len(all_results)
    differed = [r for r in all_results if r[2]]
    print(f"\n{n} samples, player-choice diverged from naive best-ADP in {len(differed)} "
          f"({len(differed) / n:.0%})" if n else "\nNo valid samples.")
    if differed:
        wins = sum(1 for r in differed if r[3])
        print(f"Among diverging samples: player-choice beat naive best-ADP in {wins}/{len(differed)} "
              f"({wins / len(differed):.0%}) -- 50% is coin-flip baseline.")


def _cli_run(year: int, pick: int, num_sims: int, seed: int, top_n: int, data_dir, opponent_weight_fn=pick_weight) -> None:
    """Standalone CLI: evaluate a real draft state at a given pick, rank
    the top `top_n` players at the recommended position by simulated EV."""
    import random as _random

    from fantasyprep.draft_sim.convergence import simulate_to_pick
    from fantasyprep.draft_sim.simulate import pick_owner, recommend_positions
    from fantasyprep.historical.outcomes import build_outcome_distributions
    from fantasyprep.league.settings import default_settings

    settings = default_settings()
    raw_dir = data_dir / "raw"
    live_pool = ffc.fetch_adp(year, teams=settings.teams, cache_path=raw_dir / f".ffc_{settings.teams}_{year}.json")
    distributions = build_outcome_distributions(
        settings, cache_path=raw_dir / f".outcomes_{settings.teams}.json", adp_cache_dir=raw_dir
    )
    points_model = HistoricalBootstrapModel(distributions)

    picks = simulate_to_pick(live_pool, pick, seed=seed)
    my_slot = pick_owner(settings.teams, pick)
    state = state_from_picks(settings.teams, my_slot, picks)
    print(f"Evaluating pick {pick} (team {my_slot}) after a realistic {len(picks)}-pick partial draft")

    rows = recommend_positions(
        live_pool, state, settings, points_model, num_sims, _random.Random(seed),
        opponent_weight_fn=opponent_weight_fn,
    )
    if not rows:
        print("No candidate positions -- nothing to do.")
        return
    top_position = rows[0][0]
    print(f"recommend_positions top pick: {top_position}")

    player_rows = recommend_players(
        top_position, live_pool, state, settings, points_model, num_sims, _random.Random(seed),
        top_n=top_n, opponent_weight_fn=opponent_weight_fn,
    )
    if not player_rows:
        print(f"No undrafted {top_position} candidates -- nothing to do.")
        return

    print(f"\n{'Player':<28}{'ADP':>8}{'Expected':>10}{'P25':>10}{'P75':>10}")
    for row in player_rows:
        print(f"{row.player.name:<28}{row.player.adp:>8.1f}{row.mean:>10.1f}{row.p25:>10.1f}{row.p75:>10.1f}")

    naive = min((p for p in live_pool if p.position == top_position and normalize_name(p.name) not in state.drafted_names),
                key=lambda p: p.adp)
    top_choice = player_rows[0].player
    if normalize_name(top_choice.name) == normalize_name(naive.name):
        print(f"\nTop simulated choice matches naive best-ADP ({naive.name}).")
    else:
        print(f"\nTop simulated choice ({top_choice.name}) DIFFERS from naive best-ADP ({naive.name}).")


def main(argv: list[str] | None = None) -> None:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, help="single-decision mode: which season")
    parser.add_argument("--pick", type=int, help="single-decision mode: overall pick number to evaluate")
    parser.add_argument("--validate", action="store_true",
                         help="run against real historical outcomes instead of a single live decision -- "
                         "50 systematically-generated samples by default, pass --quick for an 8-sample spot check")
    parser.add_argument("--quick", action="store_true", help="with --validate, use the 8-sample quick set")
    parser.add_argument("--top-n", type=int, default=3, help="how many top-ADP players per position to compare")
    parser.add_argument("--num-sims", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--opponent-model", choices=["gaussian", "gaussian-tail-floor"], default="gaussian-tail-floor",
        help="gaussian-tail-floor (default here) matches what the live tool actually uses; "
        "gaussian is the original plain model, for reproducibility with older runs",
    )
    parser.add_argument(
        "--replay-seeds", type=int, default=3,
        help="with --validate, how many independent real replays to average per sample (default 3) -- "
        "see validate_against_real_outcome_averaged's docstring for why one replay alone is too noisy",
    )
    args = parser.parse_args(argv)
    opponent_weight_fn = pick_weight_with_tail_floor if args.opponent_model == "gaussian-tail-floor" else pick_weight

    if args.validate:
        samples = QUICK_VALIDATION_SAMPLES if args.quick else DEFAULT_VALIDATION_SAMPLES
        _cli_validate(
            samples, args.num_sims, args.data_dir, args.top_n, args.replay_seeds,
            opponent_weight_fn=opponent_weight_fn,
        )
    elif args.year is not None and args.pick is not None:
        _cli_run(args.year, args.pick, args.num_sims, args.seed, args.top_n, args.data_dir, opponent_weight_fn=opponent_weight_fn)
    else:
        parser.error("either pass --year and --pick, or --validate")


if __name__ == "__main__":
    main()
