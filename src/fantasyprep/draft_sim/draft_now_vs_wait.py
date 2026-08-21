"""Draft Now vs. Wait -- a standalone diagnostic, not wired into live
recommendations yet (deliberately -- see ROADMAP.md Phase 5). Answers:
"what's my expected completed-roster value if I take this position now,
versus if I intentionally take my best alternative now and specifically
target this position again at my very next pick?"

Built as a counterfactual comparison the simulator answers directly,
rather than a hand-engineered formula like
`urgency = value * (1 - survival) + cliff` -- that kind of formula looks
quantitative but hides a lot of unstated assumptions about how much a
given survival probability is "worth." Letting the existing Monte Carlo
machinery answer the actual question (completed-roster EV under each
strategy) means the simulator determines the tradeoff, not a hand-tuned
weight.

Two pieces, both reusing existing infrastructure rather than new
techniques:

1. **Completed-roster EV for each branch** -- `simulate_position_choice`
   (unmodified, imported) for "take now"; `simulate_wait_and_target`
   (new here, a close variant, NOT a modification of the original --
   zero risk to the already-validated live tool/backtest) for "wait,
   then deliberately target the position again next pick."
2. **Survival probability** -- does NOT need its own expensive roster
   simulation. It's a narrower question ("does at least one player from
   this tier survive to my next pick") answered by repeatedly sampling
   opponent picks between now and then with the exact same ADP+stdev
   model (`opponent.sample_pick`) already used everywhere else in this
   project -- reusing the mechanism, not building a new one.

This is explicitly a diagnostic tool for now: run it against real
historical decision points and see whether its "cost of waiting" signal
actually correlates with which choice historically scored better, before
ever letting it change what the live tool recommends.
"""
from __future__ import annotations

import random
import statistics
import math
from dataclasses import dataclass

from fantasyprep.draft_sim.opponent import pick_weight, pick_weight_with_tail_floor, sample_pick
from fantasyprep.draft_sim.points_model import HistoricalBootstrapModel, PointsModel
from fantasyprep.draft_sim.roster import (
    DraftedPlayer,
    best_marginal_player,
    starting_lineup_value,
)
from fantasyprep.draft_sim.simulate import (
    DraftState,
    my_pick_numbers,
    simulate_position_choice,
    state_from_picks,
)
from fantasyprep.historical.outcomes import outcome_for_rank
from fantasyprep.historical.sources import ffc
from fantasyprep.league.settings import LeagueSettings
from fantasyprep.players.normalize import normalize_name


def simulate_wait_and_target(
    take_now_position: str,
    target_position: str,
    live_pool: list[ffc.FfcPlayer],
    state: DraftState,
    settings: LeagueSettings,
    points_model: PointsModel,
    num_sims: int,
    rng: random.Random,
    opponent_weight_fn=pick_weight,
    need_aware_future: bool = False,
) -> list[float] | None:
    """Completed-roster value if I take `take_now_position` right now and
    deliberately target `target_position` at my very next pick (best
    available there if anyone's left, else fall back to normal
    ADP-weighted sampling like every other future pick). A close variant
    of `simulate_position_choice`, not a modification of it -- that
    function is validated and used by the live tool and backtest; this
    lives entirely separately so nothing here can affect it."""
    pos_ranks = ffc.position_ranks(live_pool)
    by_name = {normalize_name(p.name): p for p in live_pool}
    undrafted = [p for p in live_pool if normalize_name(p.name) not in state.drafted_names]

    now_candidates = [p for p in undrafted if p.position == take_now_position]
    if not now_candidates:
        return None
    my_pick_now = min(now_candidates, key=lambda p: p.adp)

    total_rounds = sum(settings.roster_slots.values()) + settings.bench
    my_picks = [n for n in my_pick_numbers(state.teams, state.my_draft_slot, total_rounds) if n >= state.current_pick]
    if len(my_picks) < 2:
        return None  # no next pick to target with
    next_pick = my_picks[1]
    last_relevant_pick = my_picks[-1]
    my_pick_set = set(my_picks[1:])

    already_mine = [by_name[name] for name in state.my_names if name in by_name]

    # Same deterministic bucket-mean lookahead `simulate_position_choice` uses.
    # Cached across sims: the mapping doesn't change, so this adds no RNG.
    _expected_cache: dict[str, float] = {}

    def expected_points(player) -> float:
        cached = _expected_cache.get(player.name)
        if cached is None:
            try:
                dist = outcome_for_rank(
                    points_model.distributions, player.position, pos_ranks.get(player.name, 999)
                )
                cached = statistics.mean(dist.outcomes)
            except (AttributeError, KeyError):
                cached = 0.0
            _expected_cache[player.name] = cached
        return cached

    results = []
    for _ in range(num_sims):
        remaining = [p for p in undrafted if p is not my_pick_now]
        my_team = list(already_mine) + [my_pick_now]

        for pick_num in range(state.current_pick + 1, last_relevant_pick + 1):
            if pick_num in state.assigned:
                continue
            if not remaining:
                break
            if pick_num == next_pick:
                target_candidates = [p for p in remaining if p.position == target_position]
                chosen = (
                    min(target_candidates, key=lambda p: p.adp)
                    if target_candidates
                    else sample_pick(remaining, pick_num, rng, weight_fn=opponent_weight_fn)
                )
            else:
                chosen = sample_pick(remaining, pick_num, rng, weight_fn=opponent_weight_fn)
            if pick_num in my_pick_set and need_aware_future and pick_num != next_pick:
                # My own later picks pick by marginal lineup value, matching the
                # recommendation panel. Leaving them as plain ADP draws is what
                # let the two panels disagree by ~190 points about the same
                # quantity. `next_pick` is exempt: targeting the position there
                # is the entire premise of this branch.
                chosen = best_marginal_player(
                    remaining, my_team, settings, expected_points, chosen
                )
            remaining.remove(chosen)
            if pick_num in my_pick_set:
                my_team.append(chosen)

        drafted = [
            DraftedPlayer(name=p.name, position=p.position, points=points_model.sample(p, pos_ranks, rng))
            for p in my_team
        ]
        results.append(starting_lineup_value(drafted, settings))

    return results


def survival_probability(
    target_position: str,
    live_pool: list[ffc.FfcPlayer],
    state: DraftState,
    settings: LeagueSettings,
    num_sims: int,
    rng: random.Random,
    tier_size: int = 3,
    opponent_weight_fn=pick_weight,
    specific_player: ffc.FfcPlayer | None = None,
) -> float:
    """Empirical probability that at least one of the current top
    `tier_size` undrafted players at `target_position` is still
    undrafted by my next pick -- repeatedly samples the intervening
    opponent picks with the same ADP+stdev model used everywhere else in
    this project. Deliberately not a full roster simulation: this is a
    narrower question and doesn't need one."""
    undrafted = [p for p in live_pool if normalize_name(p.name) not in state.drafted_names]
    if specific_player is not None:
        # Ask about THAT player, not his tier. The difference is not academic:
        # on a real board with 41 RBs left, the top-3 tier survived 100.0% of
        # 4,000 sims, so the card read "100% chance a top RB is still there next
        # round" directly above a recommendation to draft Jahmyr Gibbs -- who,
        # asked about by name, survives 13.2%. The drafter is not waiting for
        # "a top RB"; he is waiting for the player named on the card, so that is
        # what gets measured.
        tier_names = {normalize_name(specific_player.name)}
    else:
        tier = sorted(
            (p for p in undrafted if p.position == target_position), key=lambda p: p.adp
        )[:tier_size]
        if not tier:
            return 0.0
        tier_names = {normalize_name(p.name) for p in tier}

    total_rounds = sum(settings.roster_slots.values()) + settings.bench
    my_picks = [n for n in my_pick_numbers(state.teams, state.my_draft_slot, total_rounds) if n >= state.current_pick]
    if len(my_picks) < 2:
        return 0.0
    next_pick = my_picks[1]

    survived = 0
    for _ in range(num_sims):
        remaining = list(undrafted)
        for pick_num in range(state.current_pick + 1, next_pick):
            if pick_num in state.assigned or not remaining:
                continue
            chosen = sample_pick(remaining, pick_num, rng, weight_fn=opponent_weight_fn)
            remaining.remove(chosen)
        if tier_names & {normalize_name(p.name) for p in remaining}:
            survived += 1
    return survived / num_sims


# Below this, the EV edge is not big enough relative to the outcome spread to
# call -- see NowVsWaitResult.verdict. 0.55 corresponds to a margin of roughly a
# fifth of the pooled P25-P75 spread.
MIN_DECISIVENESS = 0.55

# At or above this survival probability, waiting is effectively free.
NEAR_CERTAIN_SURVIVAL = 0.90


@dataclass(frozen=True)
class NowVsWaitResult:
    position: str
    now_mean: float
    now_p25: float
    now_p75: float
    wait_alternative_position: str
    wait_mean: float
    wait_p25: float
    wait_p75: float
    survival_probability: float

    @property
    def cost_of_waiting(self) -> float:
        """Positive means waiting is worse (draft now); negative means
        waiting is actually better (the alternative pick now, plus
        successfully targeting this position next pick, beats taking it
        now) -- can genuinely happen, e.g. if the alternative position is
        itself scarce this exact pick."""
        return self.now_mean - self.wait_mean

    @property
    def decisiveness(self) -> float:
        """How much bigger the edge is than the noise, as 0.5-1.0.

        Same logistic-of-margin-over-pooled-spread shape as
        `simulate.position_confidence`, deliberately: a wide outcome spread
        should demand a bigger raw-point edge before a call counts as
        confident, and the two surfaces of this app should not use two
        different notions of "close".
        """
        margin = abs(self.cost_of_waiting)
        spread = ((self.now_p75 - self.now_p25) + (self.wait_p75 - self.wait_p25)) / 2 or 1.0
        return 1.0 / (1.0 + math.exp(-margin / spread))

    @property
    def verdict(self) -> str:
        """'draft_now', 'safe_to_wait', or 'too_close'.

        This used to be a bare sign test on `cost_of_waiting`, which produced a
        genuine contradiction on screen: a +4 edge on a ~1845 roster (0.2%)
        rendered as a confident "Draft TE now" directly above the sentence
        "100% chance a top TE is still there next round". Both statements came
        from the same result object, and only one of them could be acted on.

        A sign test is the wrong instrument here because the measured decision
        margins in this project are tiny -- the median is about 1.3% of roster
        value -- so the sign of a small difference is mostly simulation noise.
        Below MIN_DECISIVENESS the honest answer is that it does not matter.

        Survival is the second guard. If the player is nearly certain to still
        be there, waiting is close to free whatever a marginal EV edge says,
        so an indecisive "now" collapses to "safe to wait" rather than
        pretending urgency.
        """
        if self.decisiveness < MIN_DECISIVENESS:
            if self.survival_probability >= NEAR_CERTAIN_SURVIVAL:
                return "safe_to_wait"
            return "too_close"
        return "draft_now" if self.cost_of_waiting > 0 else "safe_to_wait"


def compare_now_vs_wait(
    target_position: str,
    wait_alternative_position: str,
    live_pool: list[ffc.FfcPlayer],
    state: DraftState,
    settings: LeagueSettings,
    points_model: PointsModel,
    num_sims: int,
    rng: random.Random,
    opponent_weight_fn=pick_weight,
    need_aware_future: bool = False,
) -> NowVsWaitResult | None:
    """The full comparison: draft `target_position` now, vs. take
    `wait_alternative_position` now and deliberately target
    `target_position` again next pick. `wait_alternative_position` is a
    parameter, not inferred here -- callers should pass the position
    `recommend_positions` ranks 2nd, matching what a real drafter would
    actually consider skipping to."""
    now_results = simulate_position_choice(
        target_position, live_pool, state, settings, points_model, num_sims, rng,
        opponent_weight_fn=opponent_weight_fn, need_aware_future=need_aware_future,
    )
    if now_results is None:
        return None

    wait_results = simulate_wait_and_target(
        wait_alternative_position, target_position, live_pool, state, settings, points_model, num_sims, rng,
        opponent_weight_fn=opponent_weight_fn, need_aware_future=need_aware_future,
    )
    if wait_results is None:
        return None

    # The panel names a specific player, so ask about that specific player.
    _undrafted = [p for p in live_pool if normalize_name(p.name) not in state.drafted_names]
    _targets = sorted(
        (p for p in _undrafted if p.position == target_position), key=lambda p: p.adp
    )
    survival = survival_probability(
        target_position, live_pool, state, settings, num_sims, rng,
        opponent_weight_fn=opponent_weight_fn,
        specific_player=_targets[0] if _targets else None,
    )

    def _p25(vals):
        return statistics.quantiles(vals, n=4)[0] if len(vals) >= 4 else min(vals)

    def _p75(vals):
        return statistics.quantiles(vals, n=4)[2] if len(vals) >= 4 else max(vals)

    return NowVsWaitResult(
        position=target_position,
        now_mean=statistics.mean(now_results), now_p25=_p25(now_results), now_p75=_p75(now_results),
        wait_alternative_position=wait_alternative_position,
        wait_mean=statistics.mean(wait_results), wait_p25=_p25(wait_results), wait_p75=_p75(wait_results),
        survival_probability=survival,
    )


def _model_driven_strategy(
    live_pool, teams, my_slot, settings, points_model, num_sims, seed, opponent_weight_fn=pick_weight,
):
    """A recommend_positions-driven strategy for run_full_draft -- the
    same logic backtest.py's model condition uses, factored out here so
    validate_against_real_outcomes can build on/override it for specific
    picks."""
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
        candidates = [p for p in undrafted if p.position == top_position]
        if not candidates:
            return baseline_pick(undrafted, my_positions, settings)
        return min(candidates, key=lambda p: p.adp)

    return strategy


def validate_against_real_outcome(
    target_position: str,
    wait_alternative_position: str,
    decision_pick: int,
    live_pool,
    state: DraftState,
    settings: LeagueSettings,
    points_model: PointsModel,
    actual_points: dict[str, float],
    num_sims: int,
    seed: int,
    opponent_weight_fn=pick_weight,
) -> tuple[float, float]:
    """Runs the 'always take target_position now' and 'wait once, target
    target_position at my next pick' strategies as REAL, complete draft
    replays (not hypothetical simulation), scored on REAL historical
    points -- both strategies revert to normal recommend_positions-driven
    picking after the decision (and its follow-up, for 'wait') is made.
    This is what actually validates the diagnostic's pre-decision
    prediction against what really happened, not just structural
    sanity-checking. Returns (now_real_points, wait_real_points).

    `opponent_weight_fn` controls both the post-decision recommend_positions
    calls AND the opponents' own picks in the replay -- pass
    `opponent.pick_weight_with_tail_floor` to validate against the same
    opponent model the live tool actually uses, rather than the plain
    Gaussian this defaults to (matching every other call in this module)."""
    from fantasyprep.draft_sim.backtest import run_full_draft, score_roster

    teams = state.teams
    my_slot = state.my_draft_slot
    total_rounds = sum(settings.roster_slots.values()) + settings.bench
    my_picks = [n for n in my_pick_numbers(teams, my_slot, total_rounds) if n >= decision_pick]
    next_pick = my_picks[1] if len(my_picks) > 1 else None

    def make_strategy(first_pick_position, next_pick_target):
        base_strategy = _model_driven_strategy(
            live_pool, teams, my_slot, settings, points_model, num_sims, seed,
            opponent_weight_fn=opponent_weight_fn,
        )

        def strategy(undrafted, my_positions, picks):
            pick_num = len(picks) + 1
            if pick_num == decision_pick:
                candidates = [p for p in undrafted if p.position == first_pick_position]
                if candidates:
                    return min(candidates, key=lambda p: p.adp)
            if next_pick_target and pick_num == next_pick:
                candidates = [p for p in undrafted if p.position == next_pick_target]
                if candidates:
                    return min(candidates, key=lambda p: p.adp)
            return base_strategy(undrafted, my_positions, picks)

        return strategy

    now_result = run_full_draft(
        live_pool, teams, my_slot, total_rounds, settings,
        my_pick_strategy=make_strategy(target_position, None),
        opponent_rng=random.Random(seed), opponent_weight_fn=opponent_weight_fn,
    )
    wait_result = run_full_draft(
        live_pool, teams, my_slot, total_rounds, settings,
        my_pick_strategy=make_strategy(wait_alternative_position, target_position),
        opponent_rng=random.Random(seed), opponent_weight_fn=opponent_weight_fn,  # same seed -> CRN
    )

    now_points, _ = score_roster(now_result.my_players, actual_points, settings)
    wait_points, _ = score_roster(wait_result.my_players, actual_points, settings)
    return now_points, wait_points


def validate_against_real_outcome_averaged(
    target_position: str,
    wait_alternative_position: str,
    decision_pick: int,
    live_pool,
    state: DraftState,
    settings: LeagueSettings,
    points_model: PointsModel,
    actual_points: dict[str, float],
    num_sims: int,
    base_seed: int,
    num_replay_seeds: int = 3,
    opponent_weight_fn=pick_weight,
) -> tuple[float, float]:
    """Averages `validate_against_real_outcome` over several independent
    replay seeds instead of trusting a single one.

    A single real replay is a genuinely noisy ground truth: everything
    downstream of the decision pick (every other pick, mine and every
    opponent's) is filled in stochastically, and that noise moves the real
    point total independently of whether the decision itself was good.
    Measured directly (2026-08-16, one real 2023 decision): the same
    decision's real_delta swung from -216.6 to +210.1 -- sign and all --
    across just 4 replay seeds. "Was now or wait better, from this
    decision point" is inherently a question about an expectation over how
    the rest of the draft could unfold, not about one arbitrary draw of
    it -- so averaging several replay seeds isn't a variance-reduction
    trick bolted on after the fact, it's what the question already meant.
    CRN (same seed for both branches, see `validate_against_real_outcome`)
    still isolates the decision from opponent-room noise *within* each
    individual replay; averaging across *several* seeds on top of that
    removes the remaining downstream-pick noise CRN alone can't cancel,
    since CRN pairs "now" against "wait" but doesn't fix what those
    downstream picks actually were.

    Seeds are spaced 1000 apart (`base_seed`, `base_seed + 1000`, ...) so
    they don't collide with the `random.Random(seed + pick_num)` draws
    `_model_driven_strategy` already makes internally off the same base
    seed for a single replay."""
    now_total = 0.0
    wait_total = 0.0
    for i in range(num_replay_seeds):
        now_points, wait_points = validate_against_real_outcome(
            target_position, wait_alternative_position, decision_pick, live_pool, state, settings,
            points_model, actual_points, num_sims, base_seed + i * 1000,
            opponent_weight_fn=opponent_weight_fn,
        )
        now_total += now_points
        wait_total += wait_points
    return now_total / num_replay_seeds, wait_total / num_replay_seeds


def _cli_run(year: int, pick: int, num_sims: int, seed: int, data_dir, opponent_weight_fn=pick_weight) -> None:
    """Standalone CLI: evaluate a real draft state at a given pick,
    compare the top-ranked position (take now) against the 2nd-ranked
    (wait, target the top pick next time)."""
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
    if len(rows) < 2:
        print("Not enough candidate positions to compare -- nothing to do.")
        return
    print("recommend_positions ranking:", [(pos, round(mean, 1)) for pos, mean, _p25, _p75 in rows])

    top_position, alt_position = rows[0][0], rows[1][0]
    result = compare_now_vs_wait(
        top_position, alt_position, live_pool, state, settings, points_model, num_sims, _random.Random(seed),
        opponent_weight_fn=opponent_weight_fn,
    )
    if result is None:
        print("Comparison unavailable (no next pick to target with, or no candidates left).")
        return

    print(f"\nTake {result.position} now:  EV {result.now_mean:.1f}  "
          f"(P25 {result.now_p25:.1f}, P75 {result.now_p75:.1f})")
    print(f"Wait (take {result.wait_alternative_position} now, target {result.position} next pick):  "
          f"EV {result.wait_mean:.1f}  (P25 {result.wait_p25:.1f}, P75 {result.wait_p75:.1f})")
    print(f"{result.position} tier survival to next pick: {result.survival_probability:.0%}")
    print(f"\nCost of waiting: {result.cost_of_waiting:+.1f} "
          f"({'draft now' if result.cost_of_waiting > 0 else 'safe to wait'})")


def generate_validation_samples(
    years=range(2015, 2025),
    picks=(10, 25, 45, 65, 90),
    seed_start: int = 1,
) -> list[tuple[int, int, int]]:
    """A systematic (year, pick, seed) sweep -- same discipline
    `backtest.py` already uses for its own replay scope (every
    backtest-eligible year, 2015-2024, matching `DEFAULT_BACKTEST_YEARS`
    there -- not re-imported here to keep this module's dependency on
    backtest.py lazy/scoped, same as everywhere else in this file), not a
    hand-picked handful. `picks` spans roughly round 1, 3, 5, 7, 9 so
    early/mid/late draft decisions are all represented, not just
    early-to-mid like the original 8-sample set. Each (year, pick) gets
    its own seed so results are independent, not correlated by reusing
    one opponent-room draw across the whole sweep."""
    samples = []
    seed = seed_start
    for year in years:
        for pick in picks:
            samples.append((year, pick, seed))
            seed += 1
    return samples


# The original hand-picked spot check -- fast (8 samples), useful for a
# quick sanity check (`--validate --quick`), but never had real statistical
# power: at n=8, a single flipped sample swings the headline agreement rate
# by 12.5 points, which is exactly what happened between the 6/8 (75%)
# originally recorded and the 4/8 (50%) found once the validation harness
# was fixed to match the live opponent model (see ROADMAP.md Phase 5,
# 2026-08-16). Kept only for fast iteration, not for trusting the result.
QUICK_VALIDATION_SAMPLES = [
    (2023, 12, 1), (2023, 25, 2), (2023, 45, 3), (2023, 60, 4),
    (2024, 15, 5), (2024, 30, 6), (2024, 55, 7), (2024, 70, 8),
]

# The real default: 10 years x 5 draft depths = 50 samples, systematically
# generated rather than cherry-picked. --validate uses this unless --quick
# is passed.
DEFAULT_VALIDATION_SAMPLES = generate_validation_samples()


def _cli_validate(
    samples: list[tuple[int, int, int]], num_sims: int, data_dir, opponent_weight_fn=pick_weight,
    num_replay_seeds: int = 3,
) -> None:
    """Runs `validate_against_real_outcome_averaged` across several real
    decision points and reports how often the pre-decision predicted
    direction ('now' vs 'wait') matches which one actually scored better in
    real points -- the check this diagnostic needs to pass before it should
    ever influence a live recommendation (see module docstring).

    `opponent_weight_fn` defaults to the plain Gaussian for reproducibility
    with earlier runs -- pass `opponent.pick_weight_with_tail_floor` to
    validate against the same opponent model the live tool actually uses
    (the live tool has used the tail-floor fix since 2026-08-16; this
    default hasn't been swapped, matching the opt-in convention everywhere
    else in this project).

    `num_replay_seeds` controls how many independent real replays get
    averaged per sample for the ground truth (see
    `validate_against_real_outcome_averaged`'s docstring for why a single
    replay isn't trustworthy on its own -- measured directly, the same
    decision's real_delta swung sign across just 4 replay seeds)."""
    import random as _random

    from fantasyprep.draft_sim.backtest import leakage_safe_distributions
    from fantasyprep.draft_sim.convergence import simulate_to_pick
    from fantasyprep.draft_sim.simulate import pick_owner, recommend_positions
    from fantasyprep.historical.sources import nfl_stats
    from fantasyprep.league.settings import default_settings

    settings = default_settings()
    raw_dir = data_dir / "raw"

    cache: dict[int, tuple] = {}
    agreements = []
    # Tracked separately so a position-specific pattern (e.g. the QB misses
    # found in the original 8-sample run) can be confirmed or ruled out at
    # real sample size instead of eyeballing a handful of printed lines.
    agreements_by_position: dict[str, list[bool]] = {}

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

        rows = recommend_positions(
            live_pool, state, settings, points_model, num_sims, _random.Random(seed),
            opponent_weight_fn=opponent_weight_fn,
        )
        if len(rows) < 2:
            print(f"{year} pick {pick}: skipped, not enough candidates")
            continue
        top_position, alt_position = rows[0][0], rows[1][0]

        prediction = compare_now_vs_wait(
            top_position, alt_position, live_pool, state, settings, points_model, num_sims, _random.Random(seed),
            opponent_weight_fn=opponent_weight_fn,
        )
        if prediction is None:
            print(f"{year} pick {pick}: skipped, no prediction available")
            continue

        now_real, wait_real = validate_against_real_outcome_averaged(
            top_position, alt_position, pick, live_pool, state, settings, points_model, actual_points, num_sims, seed,
            num_replay_seeds=num_replay_seeds, opponent_weight_fn=opponent_weight_fn,
        )
        real_delta = now_real - wait_real
        predicted_sign = "now" if prediction.cost_of_waiting > 0 else "wait"
        real_sign = "now" if real_delta > 0 else "wait"
        agree = predicted_sign == real_sign
        agreements.append(agree)
        agreements_by_position.setdefault(top_position, []).append(agree)

        print(f"{year} pick {pick:3d} ({top_position} vs {alt_position}): "
              f"predicted={predicted_sign} (cost {prediction.cost_of_waiting:+.1f}, "
              f"survival {prediction.survival_probability:.0%})  real={real_sign} (delta {real_delta:+.1f})  "
              f"{'AGREE' if agree else 'DISAGREE'}")

    n = len(agreements)
    if n:
        print(f"\n{sum(agreements)}/{n} agreement ({sum(agreements) / n:.0%}) -- "
              f"50% is coin-flip baseline, this should be well above it before trusting the signal.")
        print("\nBy target position (the position the diagnostic was deciding whether to draft now):")
        for position in sorted(agreements_by_position):
            results = agreements_by_position[position]
            print(f"  {position}: {sum(results)}/{len(results)} ({sum(results) / len(results):.0%})")
    else:
        print("\nNo valid samples.")


def main(argv: list[str] | None = None) -> None:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, help="single-decision mode: which season")
    parser.add_argument("--pick", type=int, help="single-decision mode: overall pick number to evaluate")
    parser.add_argument("--validate", action="store_true",
                         help="run against real historical outcomes instead of a single live decision "
                         "-- 50 systematically-generated samples by default (see DEFAULT_VALIDATION_SAMPLES), "
                         "pass --quick for the original fast-but-underpowered 8-sample spot check")
    parser.add_argument("--quick", action="store_true",
                         help="with --validate, use QUICK_VALIDATION_SAMPLES (8 samples, fast) instead of the "
                         "full 50-sample sweep -- for iteration, not for trusting the resulting number")
    parser.add_argument("--num-sims", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--opponent-model", choices=["gaussian", "gaussian-tail-floor"], default="gaussian",
        help="gaussian matches every earlier run of this CLI (default, for reproducibility); "
        "gaussian-tail-floor matches what the live tool actually uses since 2026-08-16",
    )
    parser.add_argument(
        "--replay-seeds", type=int, default=3,
        help="with --validate, how many independent real replays to average per sample for the ground truth "
        "(default 3) -- a single replay is measurably too noisy to trust alone, see "
        "validate_against_real_outcome_averaged's docstring",
    )
    args = parser.parse_args(argv)
    opponent_weight_fn = pick_weight_with_tail_floor if args.opponent_model == "gaussian-tail-floor" else pick_weight

    if args.validate:
        samples = QUICK_VALIDATION_SAMPLES if args.quick else DEFAULT_VALIDATION_SAMPLES
        _cli_validate(
            samples, args.num_sims, args.data_dir, opponent_weight_fn=opponent_weight_fn,
            num_replay_seeds=args.replay_seeds,
        )
    elif args.year is not None and args.pick is not None:
        _cli_run(args.year, args.pick, args.num_sims, args.seed, args.data_dir, opponent_weight_fn=opponent_weight_fn)
    else:
        parser.error("either pass --year and --pick, or --validate")


if __name__ == "__main__":
    main()
