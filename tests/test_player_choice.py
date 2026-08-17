import random
from unittest.mock import patch

import pytest

from fantasyprep.draft_sim.opponent import pick_weight, pick_weight_with_tail_floor
from fantasyprep.draft_sim.opponent import OpponentSampler as real_opponent_sampler
from fantasyprep.draft_sim.player_choice import (
    generate_validation_samples,
    recommend_players,
    simulate_player_choice,
    validate_against_real_outcome,
    validate_against_real_outcome_averaged,
)
from fantasyprep.draft_sim.points_model import HistoricalBootstrapModel
from fantasyprep.draft_sim.simulate import state_from_picks
from fantasyprep.historical.outcomes import OutcomeDistribution
from fantasyprep.historical.sources.ffc import FfcPlayer
from fantasyprep.league.settings import LeagueSettings, ScoringSettings
from fantasyprep.players.normalize import normalize_name

SETTINGS = LeagueSettings(
    teams=4,
    scoring=ScoringSettings(),
    roster_slots={"QB": 1, "RB": 1, "WR": 1},
    bench=1,
)


def _p(name, position, adp, stdev=1.0):
    return FfcPlayer(name=name, position=position, team="XXX", adp=adp, stdev=stdev, high=1, low=100)


def _pool():
    # RB adps: 1, 4, 7, 10, 13, 16 -> position ranks 1..6 -> buckets 0,0,0,1,1,1
    players = []
    adp = 1.0
    for i in range(6):
        for position in ("RB", "WR", "QB"):
            players.append(_p(f"{position}{i}", position, adp))
            adp += 1.0
    return players


def _flat_distributions():
    # Every position/bucket scores the same fixed value -- no divergence
    # possible between same-bucket candidates, matching test_simulate.py's
    # deterministic-outcome trick.
    dists = {}
    for position, points in (("QB", 50.0), ("RB", 60.0), ("WR", 40.0)):
        dists[(position, 0)] = OutcomeDistribution(position, 0, [points])
        dists[(position, 1)] = OutcomeDistribution(position, 1, [points])
    return dists


def _skewed_distributions():
    # RB bucket 1 (ranks 4-6, e.g. RB3) is worth far MORE than bucket 0
    # (ranks 1-3, e.g. RB0/RB1/RB2, the naive best-ADP tier) -- the only
    # way this points model can ever prefer a deeper player, since same-
    # bucket candidates are statistically identical to it by construction
    # (see player_choice.py's module docstring).
    dists = {
        ("RB", 0): OutcomeDistribution("RB", 0, [10.0]),
        ("RB", 1): OutcomeDistribution("RB", 1, [500.0]),
        ("WR", 0): OutcomeDistribution("WR", 0, [40.0]),
        ("WR", 1): OutcomeDistribution("WR", 1, [40.0]),
        ("QB", 0): OutcomeDistribution("QB", 0, [50.0]),
        ("QB", 1): OutcomeDistribution("QB", 1, [50.0]),
    }
    return dists


# --- simulate_player_choice ---------------------------------------------------


def test_simulate_player_choice_none_when_player_not_undrafted():
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[{"pick": 1, "player": "RB0"}])
    points_model = HistoricalBootstrapModel(_flat_distributions())

    already_drafted = next(p for p in pool if p.name == "RB0")
    result = simulate_player_choice(already_drafted, pool, state, SETTINGS, points_model, num_sims=5, rng=random.Random(1))
    assert result is None


def test_simulate_player_choice_none_when_no_relevant_picks_left():
    pool = _pool()
    # 4-round league (QB+RB+WR+1 bench); pre-fill all 16 picks so
    # current_pick lands past every one of my_slot's relevant picks.
    filler_picks = [{"pick": n, "player": pool[(n - 1) % len(pool)].name} for n in range(1, 17)]
    state = state_from_picks(teams=4, my_draft_slot=1, picks=filler_picks)
    points_model = HistoricalBootstrapModel(_flat_distributions())

    candidate = _p("Extra RB", "RB", 99.0)
    result = simulate_player_choice(candidate, pool + [candidate], state, SETTINGS, points_model, num_sims=5, rng=random.Random(1))
    assert result is None


def test_simulate_player_choice_returns_num_sims_results_and_includes_the_player():
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_flat_distributions())

    candidate = next(p for p in pool if p.name == "RB2")  # rank 3, still bucket 0
    results = simulate_player_choice(candidate, pool, state, SETTINGS, points_model, num_sims=8, rng=random.Random(1))

    assert results is not None
    assert len(results) == 8
    # "My" future picks in this lookahead are ADP-weighted random sampling
    # (same as opponents'), not a smart fill -- the roster isn't guaranteed
    # to complete one of each position (matches simulate_position_choice's
    # own established behavior/tests). Every RB scores exactly 60 under
    # _flat_distributions regardless of rank, and the candidate itself is
    # always in the roster, so 60 is the one guaranteed floor.
    assert all(v >= 60.0 for v in results)


def test_simulate_player_choice_already_mine_contributes_deterministically():
    pool = _pool()
    picks = [{"pick": 1, "player": "RB0"}]  # my_slot=1 owns pick 1
    state = state_from_picks(teams=4, my_draft_slot=1, picks=picks)
    points_model = HistoricalBootstrapModel(_flat_distributions())

    candidate = next(p for p in pool if p.name == "WR0")
    results = simulate_player_choice(candidate, pool, state, SETTINGS, points_model, num_sims=5, rng=random.Random(2))

    assert results is not None
    # Already-mine RB0 (60, flat across every bucket) is guaranteed present
    # in the roster and always fills the single RB slot -- a reliable floor
    # regardless of how the rest of the roster (sampled, not guaranteed) fills.
    assert all(v >= 60.0 for v in results)


# --- recommend_players ---------------------------------------------------


def test_recommend_players_empty_when_no_candidates_at_position():
    pool = [_p("Only WR", "WR", 1.0)]
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_flat_distributions())

    rows = recommend_players("QB", pool, state, SETTINGS, points_model, num_sims=5, rng=random.Random(1))
    assert rows == []


def test_recommend_players_respects_top_n():
    pool = _pool()  # 6 RBs total
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_flat_distributions())

    rows = recommend_players("RB", pool, state, SETTINGS, points_model, num_sims=5, rng=random.Random(1), top_n=2)
    assert len(rows) == 2
    # top_n picks by ADP -- the two cheapest-ADP RBs (RB0 adp=1, RB1 adp=4).
    assert {r.player.name for r in rows} == {"RB0", "RB1"}


def test_recommend_players_sorted_best_first():
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_flat_distributions())

    rows = recommend_players("RB", pool, state, SETTINGS, points_model, num_sims=10, rng=random.Random(1), top_n=3)
    means = [r.mean for r in rows]
    assert means == sorted(means, reverse=True)


def test_recommend_players_can_prefer_a_deeper_player_over_naive_best_adp():
    # The whole point of this module: with a points model where a deeper
    # rank scores far higher, recommend_players should surface that player
    # over naive best-ADP -- proving the comparison isn't a no-op.
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_skewed_distributions())

    rows = recommend_players("RB", pool, state, SETTINGS, points_model, num_sims=20, rng=random.Random(3), top_n=4)
    assert rows[0].player.name == "RB3"  # rank 4 -> bucket 1 -> the 500pt tier
    naive_best_adp = min((p for p in pool if p.position == "RB"), key=lambda p: p.adp)
    assert naive_best_adp.name == "RB0"
    assert rows[0].player.name != naive_best_adp.name


# --- validate_against_real_outcome ---------------------------------------------------


def test_validate_against_real_outcome_none_when_no_position_candidates():
    # recommend_positions finds nothing (empty pool) -> rows is empty ->
    # this function returns None (the only real "no candidates" case --
    # a pool with any of QB/RB/WR/TE always gives recommend_positions
    # *something* to recommend, and this function to work with).
    pool: list = []
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_flat_distributions())

    result = validate_against_real_outcome(
        decision_pick=1, live_pool=pool, state=state, settings=SETTINGS, points_model=points_model,
        actual_points={}, num_sims=5, seed=1,
    )
    assert result is None


def test_validate_against_real_outcome_returns_two_scores_and_a_bool():
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_flat_distributions())
    actual_points = {normalize_name(p.name): 5.0 + i for i, p in enumerate(pool)}

    result = validate_against_real_outcome(
        decision_pick=1, live_pool=pool, state=state, settings=SETTINGS, points_model=points_model,
        actual_points=actual_points, num_sims=5, seed=1,
    )
    assert result is not None
    player_points, naive_points, differed = result
    assert player_points >= 0.0
    assert naive_points >= 0.0
    assert isinstance(differed, bool)


def test_validate_against_real_outcome_picks_differed_true_when_a_deeper_player_wins():
    # A single-pick, RB-only roster (no bench, no future "my" picks) so
    # the decision pick is the ONLY pick either branch makes -- isolates
    # the comparison cleanly (with a normal multi-round roster, a *later*
    # pick in either replay could independently also grab RB3 via genuine
    # recommend_positions-driven picking, contaminating the real-points
    # comparison with something unrelated to this one decision).
    single_pick_settings = LeagueSettings(
        teams=4, scoring=ScoringSettings(), roster_slots={"RB": 1}, bench=0,
    )
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_skewed_distributions())
    actual_points = {normalize_name(p.name): 1.0 for p in pool}
    # RB3 (the model's expected top choice under the skewed distribution)
    # gets a uniquely huge real score -- if the player-choice branch really
    # drafted RB3 at the decision pick, this dominates its real total.
    actual_points[normalize_name("RB3")] = 9999.0

    result = validate_against_real_outcome(
        decision_pick=1, live_pool=pool, state=state, settings=single_pick_settings, points_model=points_model,
        actual_points=actual_points, num_sims=20, seed=3, top_n=4,
    )
    assert result is not None
    player_points, naive_points, differed = result
    assert differed is True
    assert player_points >= 9999.0
    assert naive_points < 9999.0  # naive branch took RB0 (adp-best), not RB3


def test_validate_against_real_outcome_picks_differed_false_with_flat_distribution():
    # top_n=1 forces the "comparison" down to a single candidate, which by
    # construction must equal naive best-ADP.
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_flat_distributions())
    actual_points = {normalize_name(p.name): 5.0 + i for i, p in enumerate(pool)}

    result = validate_against_real_outcome(
        decision_pick=1, live_pool=pool, state=state, settings=SETTINGS, points_model=points_model,
        actual_points=actual_points, num_sims=5, seed=1, top_n=1,
    )
    assert result is not None
    _player_points, _naive_points, differed = result
    assert differed is False


def test_validate_against_real_outcome_defaults_to_plain_gaussian_opponent_model():
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_flat_distributions())
    actual_points = {normalize_name(p.name): 5.0 + i for i, p in enumerate(pool)}

    seen: list = []

    def spy_opponent_sampler(players, pick_numbers, weight_fn=pick_weight):
        seen.append(weight_fn)
        return real_opponent_sampler(players, pick_numbers, weight_fn=weight_fn)

    def spy_sample_pick(p, pick_number, rng=None, weight_fn=pick_weight):
        seen.append(weight_fn)
        from fantasyprep.draft_sim.opponent import sample_pick as real_sample_pick
        return real_sample_pick(p, pick_number, rng, weight_fn=weight_fn)

    # All opponent sampling in this path (backtest.run_full_draft's own
    # picks -> sample_pick; simulate.recommend_positions inside both the
    # decision logic and the post-decision _model_driven_strategy -> the
    # precomputed-weight OpponentSampler) -- both need to see the same
    # weight_fn, not just the ones this module calls directly.
    with patch("fantasyprep.draft_sim.backtest.sample_pick", side_effect=spy_sample_pick), \
         patch("fantasyprep.draft_sim.player_choice.sample_pick", side_effect=spy_sample_pick), \
         patch("fantasyprep.draft_sim.simulate.OpponentSampler", side_effect=spy_opponent_sampler):
        validate_against_real_outcome(
            decision_pick=1, live_pool=pool, state=state, settings=SETTINGS, points_model=points_model,
            actual_points=actual_points, num_sims=5, seed=1,
        )

    assert seen
    assert all(fn is pick_weight for fn in seen)


def test_validate_against_real_outcome_threads_custom_opponent_weight_fn_everywhere():
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_flat_distributions())
    actual_points = {normalize_name(p.name): 5.0 + i for i, p in enumerate(pool)}

    seen: list = []

    def spy_opponent_sampler(players, pick_numbers, weight_fn=pick_weight):
        seen.append(weight_fn)
        return real_opponent_sampler(players, pick_numbers, weight_fn=weight_fn)

    def spy_sample_pick(p, pick_number, rng=None, weight_fn=pick_weight):
        seen.append(weight_fn)
        from fantasyprep.draft_sim.opponent import sample_pick as real_sample_pick
        return real_sample_pick(p, pick_number, rng, weight_fn=weight_fn)

    with patch("fantasyprep.draft_sim.backtest.sample_pick", side_effect=spy_sample_pick), \
         patch("fantasyprep.draft_sim.player_choice.sample_pick", side_effect=spy_sample_pick), \
         patch("fantasyprep.draft_sim.simulate.OpponentSampler", side_effect=spy_opponent_sampler):
        validate_against_real_outcome(
            decision_pick=1, live_pool=pool, state=state, settings=SETTINGS, points_model=points_model,
            actual_points=actual_points, num_sims=5, seed=1,
            opponent_weight_fn=pick_weight_with_tail_floor,
        )

    assert seen
    assert all(fn is pick_weight_with_tail_floor for fn in seen)


# --- validate_against_real_outcome_averaged ---------------------------------------------------


def test_validate_against_real_outcome_averaged_matches_single_seed_when_num_replay_seeds_is_one():
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_flat_distributions())
    actual_points = {normalize_name(p.name): 5.0 + i for i, p in enumerate(pool)}

    single = validate_against_real_outcome(
        decision_pick=1, live_pool=pool, state=state, settings=SETTINGS, points_model=points_model,
        actual_points=actual_points, num_sims=5, seed=1,
    )
    averaged = validate_against_real_outcome_averaged(
        decision_pick=1, live_pool=pool, state=state, settings=SETTINGS, points_model=points_model,
        actual_points=actual_points, num_sims=5, base_seed=1, num_replay_seeds=1,
    )
    assert averaged == single


def test_validate_against_real_outcome_averaged_uses_distinct_spaced_seeds():
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_flat_distributions())
    actual_points = {normalize_name(p.name): 5.0 + i for i, p in enumerate(pool)}

    seen_seeds: list[int] = []

    def spy(*args, **kwargs):
        seen_seeds.append(args[7])  # seed is the 8th positional arg
        return 100.0, 50.0, False

    with patch("fantasyprep.draft_sim.player_choice.validate_against_real_outcome", side_effect=spy):
        player_avg, naive_avg, differed = validate_against_real_outcome_averaged(
            decision_pick=1, live_pool=pool, state=state, settings=SETTINGS, points_model=points_model,
            actual_points=actual_points, num_sims=5, base_seed=1, num_replay_seeds=3,
        )

    assert seen_seeds == [1, 1001, 2001]
    assert player_avg == 100.0
    assert naive_avg == 50.0
    assert differed is False


def test_validate_against_real_outcome_averaged_actually_averages_and_keeps_first_seeds_differed():
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_flat_distributions())
    actual_points = {normalize_name(p.name): 5.0 + i for i, p in enumerate(pool)}

    returns = iter([(0.0, 0.0, True), (30.0, 60.0, False), (60.0, 0.0, False)])

    with patch(
        "fantasyprep.draft_sim.player_choice.validate_against_real_outcome",
        side_effect=lambda *a, **k: next(returns),
    ):
        player_avg, naive_avg, differed = validate_against_real_outcome_averaged(
            decision_pick=1, live_pool=pool, state=state, settings=SETTINGS, points_model=points_model,
            actual_points=actual_points, num_sims=5, base_seed=1, num_replay_seeds=3,
        )

    assert player_avg == pytest.approx(30.0)  # mean of 0, 30, 60
    assert naive_avg == pytest.approx(20.0)  # mean of 0, 60, 0
    assert differed is True  # taken from the FIRST seed's result, not majority/last


def test_validate_against_real_outcome_averaged_none_when_any_seed_is_none():
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_flat_distributions())
    actual_points = {normalize_name(p.name): 5.0 + i for i, p in enumerate(pool)}

    returns = iter([(30.0, 60.0, False), None])

    with patch(
        "fantasyprep.draft_sim.player_choice.validate_against_real_outcome",
        side_effect=lambda *a, **k: next(returns),
    ):
        result = validate_against_real_outcome_averaged(
            decision_pick=1, live_pool=pool, state=state, settings=SETTINGS, points_model=points_model,
            actual_points=actual_points, num_sims=5, base_seed=1, num_replay_seeds=2,
        )

    assert result is None


# --- generate_validation_samples ---------------------------------------------------


def test_generate_validation_samples_covers_every_year_and_pick():
    samples = generate_validation_samples(years=[2020, 2021], picks=(10, 50), seed_start=1)
    assert samples == [(2020, 10, 1), (2020, 50, 2), (2021, 10, 3), (2021, 50, 4)]


def test_generate_validation_samples_seeds_are_unique():
    samples = generate_validation_samples(years=range(2015, 2025), picks=(10, 25, 45, 65, 90))
    seeds = [seed for _year, _pick, seed in samples]
    assert len(seeds) == len(set(seeds))
    assert len(samples) == 50
