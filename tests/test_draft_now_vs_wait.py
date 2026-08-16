import random

from fantasyprep.draft_sim.draft_now_vs_wait import (
    compare_now_vs_wait,
    simulate_wait_and_target,
    survival_probability,
    validate_against_real_outcome,
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
    players = []
    adp = 1.0
    for i in range(6):
        for position in ("RB", "WR", "QB"):
            players.append(_p(f"{position}{i}", position, adp))
            adp += 1.0
    return players


def _distributions():
    dists = {}
    for position, points in (("QB", 50.0), ("RB", 60.0), ("WR", 40.0)):
        dists[(position, 0)] = OutcomeDistribution(position, 0, [points])
        dists[(position, 1)] = OutcomeDistribution(position, 1, [points])
    return dists


# --- simulate_wait_and_target ---------------------------------------------------


def test_simulate_wait_and_target_none_when_no_candidates_for_now_position():
    pool = [_p("Only RB", "RB", 1.0)]
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_distributions())

    result = simulate_wait_and_target(
        "QB", "RB", pool, state, SETTINGS, points_model, num_sims=5, rng=random.Random(1)
    )
    assert result is None  # no QB in the pool to take "now"


def test_simulate_wait_and_target_none_when_no_next_pick_exists():
    # teams=4, my_draft_slot=1, total_rounds implied by SETTINGS is small
    # (QB+RB+WR+1 bench = 4 rounds); starting at the very last relevant
    # pick leaves no "next pick" to target with.
    pool = _pool()
    picks = [{"pick": n, "player": pool[0].name} for n in range(1, 1)]  # no-op, just for current_pick=1 baseline
    state = state_from_picks(teams=4, my_draft_slot=1, picks=picks)
    points_model = HistoricalBootstrapModel(_distributions())

    # Force current_pick to the last of my 4 relevant picks (pick 16, see
    # test_backtest.py's established my_picks=[1,8,9,16] for this shape)
    # by pre-filling everything before it.
    filler_picks = [{"pick": n, "player": pool[(n - 1) % len(pool)].name} for n in range(1, 16)]
    state = state_from_picks(teams=4, my_draft_slot=1, picks=filler_picks)
    assert state.current_pick == 16

    result = simulate_wait_and_target(
        "QB", "RB", pool, state, SETTINGS, points_model, num_sims=5, rng=random.Random(1)
    )
    assert result is None


def test_simulate_wait_and_target_takes_alternative_position_now():
    # Deterministic outcomes make it easy to confirm the roster shape:
    # QB=50, RB=60, WR=40 always. Taking WR now (instead of RB) plus
    # eventually filling RB/QB should still reach the same 150 total
    # once the roster completes with one of each -- but we mainly check
    # this doesn't crash and returns sane values here since exact
    # composition depends on the stochastic future picks.
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_distributions())

    results = simulate_wait_and_target(
        "WR", "RB", pool, state, SETTINGS, points_model, num_sims=10, rng=random.Random(1)
    )
    assert results is not None
    assert len(results) == 10
    assert all(v >= 0 for v in results)


# --- survival_probability ---------------------------------------------------


def test_survival_probability_zero_when_no_candidates_at_position():
    pool = [_p("Only QB", "QB", 1.0)]
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    assert survival_probability("RB", pool, state, SETTINGS, num_sims=10, rng=random.Random(1)) == 0.0


def test_survival_probability_is_one_when_no_intervening_picks():
    # my_slot=1, current_pick already at my own pick 8 with pick 9 (also
    # mine) as the "next pick" -- zero opponent picks strictly between
    # them, so the tier can't possibly be touched. Build this by
    # pre-filling picks 1-7.
    pool = _pool()
    filler_picks = [{"pick": n, "player": pool[(n - 1) % len(pool)].name} for n in range(1, 8)]
    state = state_from_picks(teams=4, my_draft_slot=1, picks=filler_picks)
    assert state.current_pick == 8

    prob = survival_probability("RB", pool, state, SETTINGS, num_sims=20, rng=random.Random(1), tier_size=10)
    assert prob == 1.0


def test_survival_probability_between_zero_and_one_with_real_competition():
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    prob = survival_probability("RB", pool, state, SETTINGS, num_sims=50, rng=random.Random(1), tier_size=1)
    assert 0.0 <= prob <= 1.0


# --- compare_now_vs_wait ---------------------------------------------------


def test_compare_now_vs_wait_produces_a_full_result():
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_distributions())

    result = compare_now_vs_wait(
        "RB", "WR", pool, state, SETTINGS, points_model, num_sims=15, rng=random.Random(1)
    )

    assert result is not None
    assert result.position == "RB"
    assert result.wait_alternative_position == "WR"
    # P25 <= P75 always holds by definition of quantiles -- mean vs P25
    # ordering does NOT (a handful of low outliers in a small, skewed
    # sample can pull the mean below P25 -- a real statistical property,
    # confirmed hitting it here, not something to assert away).
    assert result.now_p25 <= result.now_p75
    assert result.wait_p25 <= result.wait_p75
    assert 0.0 <= result.survival_probability <= 1.0
    assert result.cost_of_waiting == result.now_mean - result.wait_mean


def test_compare_now_vs_wait_none_when_target_position_unavailable():
    pool = [_p("Only WR", "WR", 1.0)]
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_distributions())

    result = compare_now_vs_wait(
        "QB", "WR", pool, state, SETTINGS, points_model, num_sims=5, rng=random.Random(1)
    )
    assert result is None


# --- validate_against_real_outcome ---------------------------------------------------


def test_validate_against_real_outcome_returns_two_real_scores():
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_distributions())
    actual_points = {normalize_name(p.name): 5.0 + i for i, p in enumerate(pool)}

    now_points, wait_points = validate_against_real_outcome(
        "RB", "WR", decision_pick=1, live_pool=pool, state=state, settings=SETTINGS,
        points_model=points_model, actual_points=actual_points, num_sims=5, seed=1,
    )
    assert now_points >= 0.0
    assert wait_points >= 0.0


def test_validate_against_real_outcome_now_strategy_actually_drafts_target_position():
    # Deterministic real points make it possible to confirm structurally
    # that the "now" strategy really did take an RB at the decision pick
    # -- give RB a uniquely huge real value and confirm it shows up.
    pool = _pool()
    state = state_from_picks(teams=4, my_draft_slot=1, picks=[])
    points_model = HistoricalBootstrapModel(_distributions())
    actual_points = {normalize_name(p.name): 1.0 for p in pool}
    # RB0 is the best-ADP RB (adp=1.0) -- give it a huge, unique score.
    actual_points[normalize_name("RB0")] = 9999.0

    now_points, _wait_points = validate_against_real_outcome(
        "RB", "WR", decision_pick=1, live_pool=pool, state=state, settings=SETTINGS,
        points_model=points_model, actual_points=actual_points, num_sims=5, seed=1,
    )
    # If "now" drafted RB0 at the decision pick as expected, its huge
    # score should dominate the roster's real total.
    assert now_points >= 9999.0
