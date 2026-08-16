from collections import Counter
from unittest.mock import patch

import pytest

from fantasyprep.draft_sim import backtest
from fantasyprep.draft_sim.backtest import (
    DEFAULT_BACKTEST_YEARS,
    ReplayResult,
    forced_fill_positions,
    baseline_pick,
    cluster_bootstrap_ci,
    confidence_weighted_pick_value,
    parse_args,
    positions_of_need,
    pure_adp_pick,
    replay_one,
    wilson_interval,
)
from fantasyprep.draft_sim.opponent import pick_weight_with_tail_floor
from fantasyprep.draft_sim.points_model import HistoricalBootstrapModel
from fantasyprep.historical.outcomes import OutcomeDistribution
from fantasyprep.historical.sources.ffc import FfcPlayer
from fantasyprep.league.settings import LeagueSettings, ScoringSettings
from fantasyprep.players.normalize import normalize_name

# --- fixtures -----------------------------------------------------------

SETTINGS = LeagueSettings(
    teams=4,
    scoring=ScoringSettings(),
    roster_slots={"QB": 1, "RB": 1, "WR": 1, "DST": 1},
    bench=1,
)

FLEX_SETTINGS = LeagueSettings(
    teams=10,
    scoring=ScoringSettings(),
    roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 2, "DST": 1},
    bench=6,
)


def _p(name, position, adp, stdev=1.0):
    return FfcPlayer(name=name, position=position, team="XXX", adp=adp, stdev=stdev, high=1, low=100)


# --- positions_of_need ---------------------------------------------------


def test_positions_of_need_empty_roster_wants_every_required_slot():
    needed = positions_of_need([], SETTINGS)
    assert needed == {"QB", "RB", "WR", "DST"}


def test_positions_of_need_drops_a_filled_position():
    needed = positions_of_need(["QB"], SETTINGS)
    assert needed == {"RB", "WR", "DST"}


def test_positions_of_need_flex_overflow_keeps_flex_eligible_needed():
    # 1 RB filled, but 2 FLEX slots still open -- RB/WR/TE still count as
    # "needed" since a 2nd RB could fill a FLEX slot.
    needed = positions_of_need(["QB", "RB", "WR", "WR", "TE", "DST"], FLEX_SETTINGS)
    assert needed == {"RB", "WR", "TE"}


def test_positions_of_need_flex_satisfied_falls_through_to_bench_open():
    # All fixed slots + both FLEX slots covered by overflow (1 extra RB, 1
    # extra WR) -- starting lineup is complete, but 6 bench slots are still
    # open, so this falls through to the bench-any set, not empty.
    drafted = ["QB", "RB", "RB", "RB", "WR", "WR", "WR", "TE", "DST"]
    needed = positions_of_need(drafted, FLEX_SETTINGS)
    assert needed == {"QB", "RB", "WR", "TE"}


def test_positions_of_need_bench_open_once_starting_lineup_full():
    drafted = ["QB", "RB", "WR", "DST"]  # every SETTINGS starting slot filled, bench (1) still open
    needed = positions_of_need(drafted, SETTINGS)
    assert needed == {"QB", "RB", "WR", "TE"}  # CANDIDATE_POSITIONS -- bench is skill-position-any


def test_positions_of_need_empty_once_full_roster_drafted():
    drafted = ["QB", "RB", "WR", "DST", "RB"]  # 4 starting + 1 bench = full for SETTINGS
    assert positions_of_need(drafted, SETTINGS) == set()


def test_positions_of_need_dst_only_when_everything_else_filled():
    drafted = ["QB", "RB", "WR"]  # only DST left among required starting slots
    assert positions_of_need(drafted, SETTINGS) == {"DST"}


# --- baseline_pick ---------------------------------------------------


def test_baseline_pick_takes_best_adp_among_needed_positions():
    pool = [
        _p("Best QB", "QB", 1.0),  # lowest ADP overall, but QB isn't needed
        _p("Worse RB", "RB", 5.0),
        _p("Best RB", "RB", 2.0),
    ]
    chosen = baseline_pick(pool, drafted_positions=["QB"], settings=SETTINGS)
    assert chosen.name == "Best RB"


def test_baseline_pick_falls_back_to_best_overall_if_no_needed_candidates():
    # Only DST is needed (QB/RB/WR already drafted), but the pool has none
    # left -- falls back to best-ADP-overall rather than returning nothing.
    pool = [_p("Only QB", "QB", 3.0)]
    chosen = baseline_pick(pool, drafted_positions=["QB", "RB", "WR"], settings=SETTINGS)
    assert chosen.name == "Only QB"


# --- pure_adp_pick ---------------------------------------------------


def test_pure_adp_pick_ignores_need_entirely():
    # Unlike baseline_pick, need is irrelevant -- always just best ADP,
    # even if that position is already massively overdrafted.
    pool = [
        _p("Best QB", "QB", 1.0),
        _p("Best RB", "RB", 2.0),
    ]
    assert pure_adp_pick(pool).name == "Best QB"


# --- confidence_weighted_pick_value ---------------------------------------------------


def test_confidence_weighted_pick_value_clear_winner_weights_almost_entirely_to_top():
    # Huge margin (100 pts) relative to a tight spread (10 pts IQR) -- not
    # remotely a close call, blended value should land right on the top pick.
    rows = [("RB", 200.0, 195.0, 205.0), ("WR", 100.0, 95.0, 105.0)]
    pool = [_p("Best RB", "RB", 1.0), _p("Best WR", "WR", 2.0)]
    actual_points = {"best rb": 50.0, "best wr": 10.0}

    blended, actual, weight_top = confidence_weighted_pick_value(rows, pool, actual_points)

    assert weight_top > 0.999
    assert actual == 50.0  # the real value of whatever the model would actually draft
    assert blended == pytest.approx(50.0, abs=0.1)


def test_confidence_weighted_pick_value_genuine_tie_blends_fifty_fifty():
    # Zero margin between the top two -- a genuine toss-up, should blend
    # exactly 50/50 regardless of spread.
    rows = [("RB", 150.0, 140.0, 160.0), ("WR", 150.0, 140.0, 160.0)]
    pool = [_p("Some RB", "RB", 1.0), _p("Some WR", "WR", 2.0)]
    actual_points = {"some rb": 40.0, "some wr": 20.0}

    blended, actual, weight_top = confidence_weighted_pick_value(rows, pool, actual_points)

    assert weight_top == pytest.approx(0.5)
    assert blended == pytest.approx((40.0 + 20.0) / 2)
    assert actual == 40.0  # top-ranked position (RB, first in rows) is what's actually drafted


def test_confidence_weighted_pick_value_single_candidate_no_blending():
    rows = [("QB", 100.0, 90.0, 110.0)]
    pool = [_p("Only QB", "QB", 1.0)]
    actual_points = {"only qb": 30.0}

    blended, actual, weight_top = confidence_weighted_pick_value(rows, pool, actual_points)

    assert weight_top == 1.0
    assert blended == actual == 30.0


def test_confidence_weighted_pick_value_same_wide_margin_scaled_by_spread():
    # Same raw margin (50 pts) as a "clear winner" case, but a much wider
    # spread -- should weight less confidently toward the top pick, since
    # the same point gap means less when the underlying uncertainty is bigger.
    tight_rows = [("RB", 150.0, 145.0, 155.0), ("WR", 100.0, 95.0, 105.0)]  # spread=10
    wide_rows = [("RB", 150.0, 50.0, 250.0), ("WR", 100.0, 0.0, 200.0)]  # spread=200
    pool = [_p("Some RB", "RB", 1.0), _p("Some WR", "WR", 2.0)]
    actual_points = {"some rb": 40.0, "some wr": 20.0}

    _, _, weight_tight = confidence_weighted_pick_value(tight_rows, pool, actual_points)
    _, _, weight_wide = confidence_weighted_pick_value(wide_rows, pool, actual_points)

    assert weight_tight > weight_wide > 0.5


# --- forced_fill_positions ---------------------------------------------------


def test_forced_fill_positions_is_dst_for_this_league():
    assert forced_fill_positions(SETTINGS) == {"DST"}
    assert forced_fill_positions(FLEX_SETTINGS) == {"DST"}


# --- replay_one: end-to-end structural correctness ---------------------------------------------------


def _synthetic_pool():
    # 6 players per position. Skill positions (RB/WR/QB) get low, interleaved
    # ADPs so "best available" isn't trivially always the same one; DST gets
    # uniformly the highest ADPs of all, mirroring real fantasy ADP (DST is
    # always drafted latest) -- this keeps the "DST only gets force-filled
    # once it's the sole remaining need" scenario realistic rather than
    # letting DST's low-ADP-by-construction accidentally jump the queue.
    players = []
    adp = 1.0
    for i in range(6):
        for position in ("RB", "WR", "QB"):
            players.append(_p(f"{position}{i}", position, adp))
            adp += 1.0
    for i in range(6):
        players.append(_p(f"DST{i}", "DST", adp))
        adp += 1.0
    return players


def _flat_distribution(position, points):
    # Single fixed outcome (no variance) for every bucket the synthetic
    # pool's ranks (1-6) can hit -- keeps HistoricalBootstrapModel.sample
    # deterministic given a fixed rng seed, mirroring test_simulate.py's
    # already_mine-bug regression trick.
    return {(position, 0): OutcomeDistribution(position, 0, [points]), (position, 1): OutcomeDistribution(position, 1, [points])}


def _distributions():
    dists = {}
    for position, points in (("QB", 50.0), ("RB", 60.0), ("WR", 40.0)):
        dists.update(_flat_distribution(position, points))
    return dists


def test_replay_one_produces_full_comparable_rosters_scored_on_real_points():
    live_pool = _synthetic_pool()
    points_model = HistoricalBootstrapModel(_distributions())
    # Real points only for skill positions -- DST is intentionally absent,
    # mirroring nfl_stats.py's actual gap (no DST in POSITION_MAP), so the
    # score_roster fallback-to-0.0 path is exercised for both conditions.
    actual_points = {
        normalize_name(p.name): 10.0 + i
        for i, p in enumerate(pl for pl in live_pool if pl.position != "DST")
    }

    result = replay_one(
        year=2023, my_slot=1, settings=SETTINGS, live_pool=live_pool,
        points_model=points_model, distributions=_distributions(), actual_points=actual_points, num_sims=5, seed=7,
    )

    for roster in (result.baseline_roster, result.model_roster, result.pure_adp_roster, result.vor_roster):
        assert len(roster) == 5  # 4 starting slots + 1 bench, this league's total_rounds

        positions = Counter(pos for _name, pos, _pts in roster)
        assert positions["DST"] == 1  # forced-fill worked for all four conditions
        assert positions["QB"] >= 1 and positions["RB"] >= 1 and positions["WR"] >= 1
        assert positions["QB"] + positions["RB"] + positions["WR"] == 4  # required 3 + 1 bench

        for name, position, points in roster:
            if position == "DST":
                assert points == 0.0  # no real DST scoring source -- documented, symmetric gap
            else:
                assert points == actual_points[normalize_name(name)]  # real injected points, not sampled

    # baseline_points/model_points/pure_adp_points must be internally
    # consistent with the returned roster detail (not some other
    # computation) -- recompute via the same starting_lineup_value the
    # implementation uses.
    from fantasyprep.draft_sim.roster import DraftedPlayer, starting_lineup_value

    def _recompute(roster):
        return starting_lineup_value(
            [DraftedPlayer(name=n, position=p, points=pts) for n, p, pts in roster], SETTINGS
        )

    assert result.baseline_points == _recompute(result.baseline_roster)
    assert result.model_points == _recompute(result.model_roster)
    assert result.pure_adp_points == _recompute(result.pure_adp_roster)
    assert result.vor_points == _recompute(result.vor_roster)
    assert result.delta == result.model_points - result.baseline_points
    assert result.delta_vs_pure_adp == result.model_points - result.pure_adp_points
    assert result.delta_vs_vor == result.model_points - result.vor_points

    # Confidence-weighted diagnostic: some genuine (non-forced) model
    # decisions happened (this league has more than just the DST slot to
    # fill), so both sums should be non-trivial, and the gap property
    # should be internally consistent.
    assert result.confidence_weighted_points >= 0.0
    assert result.confidence_weighted_actual_points >= 0.0
    assert result.confidence_weighted_gap == pytest.approx(
        result.confidence_weighted_points - result.confidence_weighted_actual_points
    )


def test_replay_one_threads_opponent_weight_fn_into_model_internal_lookahead():
    # Regression (fixed 2026-08-16): replay_one's model_strategy used to call
    # recommend_positions without opponent_weight_fn, so the model's own
    # internal Monte Carlo lookahead silently kept assuming the plain
    # Gaussian opponent model even when a non-default opponent_weight_fn
    # (e.g. pick_weight_with_tail_floor) was passed in for the outer
    # real-draft opponents -- see backtest.py's run_backtest docstring.
    live_pool = _synthetic_pool()
    points_model = HistoricalBootstrapModel(_distributions())
    actual_points = {
        normalize_name(p.name): 10.0 + i
        for i, p in enumerate(pl for pl in live_pool if pl.position != "DST")
    }

    seen_weight_fns = []
    real_recommend_positions = backtest.recommend_positions

    def spy(*args, **kwargs):
        seen_weight_fns.append(kwargs.get("opponent_weight_fn"))
        return real_recommend_positions(*args, **kwargs)

    with patch("fantasyprep.draft_sim.backtest.recommend_positions", side_effect=spy):
        replay_one(
            year=2023, my_slot=1, settings=SETTINGS, live_pool=live_pool,
            points_model=points_model, distributions=_distributions(), actual_points=actual_points,
            num_sims=3, seed=7, opponent_weight_fn=pick_weight_with_tail_floor,
        )

    assert seen_weight_fns  # recommend_positions was actually invoked at least once
    assert all(fn is pick_weight_with_tail_floor for fn in seen_weight_fns)


# --- wilson_interval ---------------------------------------------------


def test_wilson_interval_contains_the_point_estimate():
    lo, hi = wilson_interval(17, 30)
    assert lo < 17 / 30 < hi


def test_wilson_interval_narrows_with_more_observations():
    # Same win rate, far more observations -> tighter interval.
    lo_small, hi_small = wilson_interval(17, 30)
    lo_big, hi_big = wilson_interval(170, 300)
    assert (hi_big - lo_big) < (hi_small - lo_small)


def test_wilson_interval_bounded_within_zero_and_one():
    lo, hi = wilson_interval(30, 30)  # all wins
    assert 0.0 <= lo <= hi <= 1.0
    assert lo < 1.0  # still shouldn't claim exact certainty on the lower bound


def test_wilson_interval_empty_sample():
    assert wilson_interval(0, 0) == (0.0, 0.0)


# --- cluster_bootstrap_ci ---------------------------------------------------


def _fake_result(year, slot, seed_index, delta):
    # baseline_points/pure_adp_points fixed at 0 so delta == model_points
    # exactly, for easy-to-reason-about fixtures.
    return ReplayResult(
        year=year, my_slot=slot, seed_index=seed_index,
        baseline_points=0.0, model_points=delta, pure_adp_points=0.0,
        baseline_roster=[], model_roster=[], pure_adp_roster=[],
    )


def test_cluster_bootstrap_ci_collapses_when_all_deltas_identical():
    results = [_fake_result(2022, slot, i, 10.0) for slot in (1, 2, 3) for i in range(5)]
    lo, hi = cluster_bootstrap_ci(results, lambda xs: sum(xs) / len(xs))
    assert lo == hi == 10.0


def test_cluster_bootstrap_ci_reflects_between_cluster_spread_not_just_within():
    # Zero variance WITHIN each cluster, but clusters themselves differ a
    # lot -- a bootstrap that resampled individual replays (ignoring which
    # cluster they came from) would see this as low-variance and report a
    # falsely tight interval. Cluster resampling should not. Clusters are
    # by season now (not season+slot -- different slots in the same season
    # share that season's real outcomes, so they're the same cluster), so
    # this uses two different years to get two distinct clusters.
    results = (
        [_fake_result(2022, slot, i, 0.0) for slot in (1, 2, 3) for i in range(5)]
        + [_fake_result(2023, slot, i, 100.0) for slot in (1, 2, 3) for i in range(5)]
    )
    lo, hi = cluster_bootstrap_ci(results, lambda xs: sum(xs) / len(xs), num_iterations=500)
    assert hi - lo > 50.0  # wide -- driven by which cluster gets resampled, not fake within-cluster noise


def test_cluster_bootstrap_ci_empty_input():
    assert cluster_bootstrap_ci([], statistics_mean) == (0.0, 0.0)


def statistics_mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


# --- CLI ---------------------------------------------------


def test_parse_args_defaults():
    args = parse_args([])
    assert args.years == DEFAULT_BACKTEST_YEARS
    assert args.slots == list(range(1, 11))
    assert args.num_sims == 100
    assert args.num_seeds == 1
    assert args.seed == 0
    assert args.out is None
    assert args.experiment_name is None
    assert args.experiment_notes == ""
    assert args.scoring_mode == "season-total"


def test_parse_args_scoring_mode_waiver_adjusted():
    args = parse_args(["--scoring-mode", "waiver-adjusted"])
    assert args.scoring_mode == "waiver-adjusted"


def test_parse_args_scoring_mode_rejects_invalid_choice():
    with pytest.raises(SystemExit):
        parse_args(["--scoring-mode", "made-up-mode"])


def test_parse_args_overrides():
    args = parse_args(["--years", "2021", "2022", "--slots", "1", "2", "--num-sims", "10", "--num-seeds", "5"])
    assert args.years == [2021, 2022]
    assert args.slots == [1, 2]
    assert args.num_sims == 10
    assert args.num_seeds == 5


def test_parse_args_experiment_logging_flags():
    args = parse_args(["--experiment-name", "test-run", "--experiment-notes", "trying more seeds"])
    assert args.experiment_name == "test-run"
    assert args.experiment_notes == "trying more seeds"


# --- _compact_summary ---------------------------------------------------


def test_compact_summary_reports_both_comparisons():
    from fantasyprep.draft_sim.backtest import _compact_summary

    results = [
        _fake_result(2022, 1, 0, delta=10.0),
        _fake_result(2022, 2, 0, delta=-5.0),
    ]
    summary = _compact_summary(results)

    assert summary["n"] == 2
    assert summary["win_rate"] == 0.5
    assert summary["mean_delta"] == pytest.approx(2.5)
    assert "win_rate_vs_pure_adp" in summary
    assert "mean_delta_vs_pure_adp" in summary
