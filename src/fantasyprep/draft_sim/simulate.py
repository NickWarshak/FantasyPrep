"""Monte Carlo draft simulator: which position should I take at my current pick?

Usage:
    python -m fantasyprep.draft_sim.simulate --draft-state state.json --year 2026

draft-state JSON shape:
{
  "teams": 10,
  "my_draft_slot": 3,
  "picks": [
    {"pick": 1, "player": "Bijan Robinson"},
    {"pick": 2, "player": "Ja'Marr Chase"},
    {"pick": 47, "player": "Some Keeper"}
  ]
}

Each pick entry carries its own pick number, so entries don't need to be
in order and gaps are fine -- a "pick" ahead of the current one is a
keeper, pre-assigned before the live draft reaches that slot. "mine" is
derived from which team column a pick number belongs to (snake-draft
math via `pick_owner`), not a stored flag.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from fantasyprep.draft_sim.opponent import pick_weight, sample_pick_index
from fantasyprep.draft_sim.points_model import EspnProjectionModel, HistoricalBootstrapModel, PointsModel
from fantasyprep.draft_sim.roster import DraftedPlayer, starting_lineup_value
from fantasyprep.historical.outcomes import build_outcome_distributions
from fantasyprep.historical.sources import ffc
from fantasyprep.sources import espn
from fantasyprep.league.settings import LeagueSettings, default_settings
from fantasyprep.players.normalize import normalize_name

CANDIDATE_POSITIONS = ("QB", "RB", "WR", "TE")


def pick_owner(teams: int, pick_number: int) -> int:
    """Which team draft-slot (1-indexed) owns a given overall pick number, snake order."""
    round_num = (pick_number - 1) // teams + 1
    position_in_round = (pick_number - 1) % teams + 1
    if round_num % 2 == 1:
        return position_in_round
    return teams - position_in_round + 1


def my_pick_numbers(teams: int, my_slot: int, total_rounds: int) -> list[int]:
    total_picks = teams * total_rounds
    return [p for p in range(1, total_picks + 1) if pick_owner(teams, p) == my_slot]


def current_pick_number(picks: list[dict]) -> int:
    """Smallest pick number with no assigned player -- skips over keeper gaps."""
    assigned = {p["pick"] for p in picks}
    n = 1
    while n in assigned:
        n += 1
    return n


@dataclass(frozen=True)
class DraftState:
    teams: int
    my_draft_slot: int
    current_pick: int
    assigned: dict[int, str]  # pick number -> normalized player name
    drafted_names: set[str]  # normalized names, everyone off the board so far (any pick number)
    my_names: set[str]  # normalized names, subset owned by my_draft_slot (any pick number)


def state_from_picks(teams: int, my_draft_slot: int, picks: list[dict]) -> DraftState:
    assigned = {p["pick"]: normalize_name(p["player"]) for p in picks}
    drafted = set(assigned.values())
    mine = {name for pick_num, name in assigned.items() if pick_owner(teams, pick_num) == my_draft_slot}
    return DraftState(
        teams=teams,
        my_draft_slot=my_draft_slot,
        current_pick=current_pick_number(picks),
        assigned=assigned,
        drafted_names=drafted,
        my_names=mine,
    )


def load_draft_state(path: Path) -> DraftState:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return state_from_picks(raw["teams"], raw["my_draft_slot"], raw["picks"])


def _ffc_scoring_slug(settings: LeagueSettings) -> str:
    if settings.scoring.reception == 1.0:
        return "ppr"
    if settings.scoring.reception == 0.5:
        return "half-ppr"
    return "standard"


def simulate_position_choice(
    candidate_position: str,
    live_pool: list[ffc.FfcPlayer],
    state: DraftState,
    settings: LeagueSettings,
    points_model: PointsModel,
    num_sims: int,
    rng: random.Random,
    opponent_weight_fn=pick_weight,
) -> list[float] | None:
    # Position rank reflects each player's fixed standing in the full live
    # ADP universe -- not shifting as the draft progresses -- since that's
    # what maps to a stable historical outcome bucket.
    pos_ranks = ffc.position_ranks(live_pool)
    by_name = {normalize_name(p.name): p for p in live_pool}
    undrafted = [p for p in live_pool if normalize_name(p.name) not in state.drafted_names]

    candidates = [p for p in undrafted if p.position == candidate_position]
    if not candidates:
        return None
    my_pick_now = min(candidates, key=lambda p: p.adp)

    total_rounds = sum(settings.roster_slots.values()) + settings.bench
    my_picks = [n for n in my_pick_numbers(state.teams, state.my_draft_slot, total_rounds) if n >= state.current_pick]
    if not my_picks:
        return None
    last_relevant_pick = my_picks[-1]
    my_pick_set = set(my_picks[1:])  # first entry is the current pick, already handled

    # Every pick already assigned to my column -- past picks and future
    # keepers alike -- contributes deterministically, not via sampling.
    already_mine = [by_name[name] for name in state.my_names if name in by_name]

    # Fixed indexed pool representation, built once for this whole call and
    # reused across every sim and every simulated pick -- not rebuilt into
    # numpy arrays from Python objects on every single pick (profiling
    # 2026-08-16 found that rebuild was ~78% of runtime even after
    # vectorizing the weight math itself; see sample_pick_index).
    pool = [p for p in undrafted if p is not my_pick_now]
    pool_adp = np.array([p.adp for p in pool])
    pool_stdev = np.array([p.stdev for p in pool])

    results = []
    for _ in range(num_sims):
        available = np.ones(len(pool), dtype=bool)
        my_team = list(already_mine) + [my_pick_now]

        for pick_num in range(state.current_pick + 1, last_relevant_pick + 1):
            if pick_num in state.assigned:
                continue  # already fixed (a keeper) -- no sampling, already counted if mine
            if not available.any():
                break
            idx = sample_pick_index(pool_adp, pool_stdev, available, pick_num, rng, weight_fn=opponent_weight_fn)
            available[idx] = False
            if pick_num in my_pick_set:
                my_team.append(pool[idx])

        drafted = [
            DraftedPlayer(
                name=p.name,
                position=p.position,
                points=points_model.sample(p, pos_ranks, rng),
            )
            for p in my_team
        ]
        results.append(starting_lineup_value(drafted, settings))

    return results


def recommend_positions(
    live_pool: list[ffc.FfcPlayer],
    state: DraftState,
    settings: LeagueSettings,
    points_model: PointsModel,
    num_sims: int,
    rng: random.Random,
    opponent_weight_fn=pick_weight,
) -> list[tuple[str, float, float, float]]:
    """Position, expected starting-lineup points, P25, P75 -- sorted best first.

    `opponent_weight_fn` controls how the *internal* lookahead simulates
    hypothetical future picks (both opponents' and "mine" beyond the
    current decision) -- defaults to the plain Gaussian `pick_weight`, same
    as `opponent.sample_pick`'s own default. Pass
    `opponent.pick_weight_with_tail_floor` here to keep this internal
    assumption consistent with an outer `--opponent-model
    gaussian-tail-floor` backtest run (see `backtest.py`'s `run_full_draft`
    docstring for why the two used to diverge)."""
    rows = []
    for position in CANDIDATE_POSITIONS:
        results = simulate_position_choice(
            position, live_pool, state, settings, points_model, num_sims, rng,
            opponent_weight_fn=opponent_weight_fn,
        )
        if results is None:
            continue
        mean = statistics.mean(results)
        p25 = statistics.quantiles(results, n=4)[0] if len(results) >= 4 else min(results)
        p75 = statistics.quantiles(results, n=4)[2] if len(results) >= 4 else max(results)
        rows.append((position, mean, p25, p75))

    rows.sort(key=lambda r: r[1], reverse=True)
    return rows


def position_confidence(rows: list[tuple[str, float, float, float]]) -> float | None:
    """How lopsided the top-ranked position's edge is over the runner-up,
    as a 0.5-1.0 confidence that the argmax is the right call -- 0.5 means
    a true toss-up, 1.0 means the runner-up isn't a real contender.

    A logistic function of the margin between the top two candidates'
    means, scaled by their pooled P25-P75 spread (a wide spread needs a
    bigger raw-point margin before a pick counts as genuinely confident,
    rather than an arbitrary fixed point-margin cutoff). Only the top two
    matter for this framing -- how far the 3rd-best position trails isn't
    part of "was this pick close."

    Returns None if there's no second candidate to compare against (fewer
    than two viable positions this pick)."""
    if len(rows) < 2:
        return None
    _, top_mean, top_p25, top_p75 = rows[0]
    _, second_mean, second_p25, second_p75 = rows[1]
    margin = top_mean - second_mean
    spread = ((top_p75 - top_p25) + (second_p75 - second_p25)) / 2 or 1.0  # avoid div-by-zero
    return 1.0 / (1.0 + math.exp(-margin / spread))


def build_points_model(
    points_source: str,
    settings: LeagueSettings,
    year: int,
    raw_dir: Path,
    distributions,
    force_refresh: bool = False,
) -> PointsModel:
    historical = HistoricalBootstrapModel(distributions)
    if points_source == "historical":
        return historical

    espn_cache = raw_dir / f".espn_cache_{year}.json"
    espn_points = espn.fetch_espn_projected_points(
        year, settings.scoring, cache_path=espn_cache, force_refresh=force_refresh
    )
    return EspnProjectionModel(espn_points, fallback=historical)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft-state", type=Path, required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--num-sims", type=int, default=300)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--points-source", choices=["historical", "espn"], default="historical",
        help="'historical' bootstraps real outcomes by draft-rank tier (default); "
        "'espn' uses ESPN's own named-player season projection, falling back to "
        "historical for anyone ESPN doesn't project",
    )
    parser.add_argument("--refresh-live-adp", action="store_true")
    parser.add_argument("--refresh-historical", action="store_true")
    parser.add_argument("--seed", type=int, default=None)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> None:
    settings = default_settings()
    raw_dir = args.data_dir / "raw"
    rng = random.Random(args.seed)

    state = load_draft_state(args.draft_state)
    print(f"Draft state: pick {state.current_pick}, team {state.my_draft_slot} of {state.teams}")

    live_cache = raw_dir / f".ffc_{settings.teams}_{args.year}.json"
    print(f"Loading live {args.year} ADP ({settings.teams}-team, {_ffc_scoring_slug(settings)})...")
    live_pool = ffc.fetch_adp(
        args.year, teams=settings.teams, scoring=_ffc_scoring_slug(settings),
        cache_path=live_cache, force_refresh=args.refresh_live_adp,
    )
    print(f"  {len(live_pool)} players total")

    hist_cache = raw_dir / f".outcomes_{settings.teams}.json"
    print("Loading historical outcome distributions (2018-2024)...")
    distributions = build_outcome_distributions(
        settings, cache_path=hist_cache, adp_cache_dir=raw_dir, force_refresh=args.refresh_historical
    )
    print(f"  {len(distributions)} (position, bucket) distributions built")

    print(f"Points source: {args.points_source}")
    points_model = build_points_model(args.points_source, settings, args.year, raw_dir, distributions)

    print(f"\nSimulating {args.num_sims} drafts per candidate position...\n")
    rows = recommend_positions(live_pool, state, settings, points_model, args.num_sims, rng)

    print(f"{'Position':<10}{'Expected':>10}{'P25':>10}{'P75':>10}")
    for position, mean, p25, p75 in rows:
        print(f"{position:<10}{mean:>10.1f}{p25:>10.1f}{p75:>10.1f}")


def main(argv: list[str] | None = None) -> None:
    run(parse_args(argv))


if __name__ == "__main__":
    main()
