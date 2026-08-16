import json
import random
import statistics
from pathlib import Path
from unittest.mock import patch

from fantasyprep.draft_sim.opponent import pick_weight, pick_weight_with_tail_floor
from fantasyprep.draft_sim.opponent import sample_pick as real_sample_pick
from fantasyprep.draft_sim.points_model import EspnProjectionModel, HistoricalBootstrapModel
from fantasyprep.draft_sim.simulate import (
    build_points_model,
    current_pick_number,
    load_draft_state,
    my_pick_numbers,
    parse_args,
    pick_owner,
    recommend_positions,
    simulate_position_choice,
    state_from_picks,
)
from fantasyprep.historical.outcomes import OutcomeDistribution
from fantasyprep.historical.sources.ffc import FfcPlayer
from fantasyprep.league.settings import LeagueSettings, ScoringSettings


def test_pick_owner_snake_draft():
    # 10-team, slot 3: picks 3, 18 (round 2 reverses), 23, 38, ...
    assert pick_owner(teams=10, pick_number=3) == 3
    assert pick_owner(teams=10, pick_number=18) == 3
    assert pick_owner(teams=10, pick_number=23) == 3
    assert pick_owner(teams=10, pick_number=38) == 3


def test_pick_owner_first_and_last_pick_of_a_round():
    assert pick_owner(teams=12, pick_number=1) == 1
    assert pick_owner(teams=12, pick_number=12) == 12
    assert pick_owner(teams=12, pick_number=13) == 12  # round 2 starts reversed
    assert pick_owner(teams=12, pick_number=24) == 1


def test_my_pick_numbers_snake_draft():
    picks = my_pick_numbers(teams=10, my_slot=3, total_rounds=4)
    assert picks == [3, 18, 23, 38]


def test_my_pick_numbers_first_slot():
    picks = my_pick_numbers(teams=12, my_slot=1, total_rounds=3)
    assert picks == [1, 24, 25]


def test_my_pick_numbers_last_slot():
    picks = my_pick_numbers(teams=12, my_slot=12, total_rounds=3)
    assert picks == [12, 13, 36]


def test_current_pick_number_sequential():
    picks = [{"pick": 1, "player": "A"}, {"pick": 2, "player": "B"}]
    assert current_pick_number(picks) == 3


def test_current_pick_number_no_picks():
    assert current_pick_number([]) == 1


def test_current_pick_number_skips_keeper_gap():
    # picks 1-2 open, pick 5 pre-filled by a keeper -- current pick is still 1
    picks = [{"pick": 5, "player": "Keeper Guy"}]
    assert current_pick_number(picks) == 1


def test_current_pick_number_finds_gap_between_filled_picks():
    picks = [{"pick": 1, "player": "A"}, {"pick": 3, "player": "B"}]
    assert current_pick_number(picks) == 2


def test_load_draft_state_computes_current_pick(tmp_path: Path):
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "teams": 10,
                "my_draft_slot": 3,
                "picks": [
                    {"pick": 1, "player": "Bijan Robinson"},
                    {"pick": 2, "player": "Ja'Marr Chase"},
                    {"pick": 3, "player": "Jahmyr Gibbs"},
                ],
            }
        ),
        encoding="utf-8",
    )

    state = load_draft_state(state_file)

    assert state.current_pick == 4
    assert state.teams == 10
    assert state.my_draft_slot == 3
    # pick 3 belongs to slot 3 (this draft's my_draft_slot) by snake math -- mine is derived
    assert "jahmyr gibbs" in state.my_names
    assert "bijan robinson" in state.drafted_names
    assert "bijan robinson" not in state.my_names


def test_load_draft_state_empty_picks(tmp_path: Path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"teams": 12, "my_draft_slot": 1, "picks": []}), encoding="utf-8")

    state = load_draft_state(state_file)

    assert state.current_pick == 1
    assert state.drafted_names == set()
    assert state.my_names == set()


def test_state_from_picks_keeper_ahead_of_current_pick_is_mine():
    # 10-team, slot 1: picks 1, 20, 21, ... A keeper at pick 20 (round 2, mine)
    # is assigned even though picks 1-19 are still open.
    picks = [{"pick": 20, "player": "Keeper Guy"}]
    state = state_from_picks(teams=10, my_draft_slot=1, picks=picks)

    assert state.current_pick == 1
    assert "keeper guy" in state.my_names
    assert state.assigned[20] == "keeper guy"


# --- Regression test for the already_mine bug: a player already drafted to
# my column (including a keeper) must contribute non-zero to the simulated
# roster value, not silently vanish because they'd been filtered out of the
# undrafted pool before reaching the "already mine" lookup. ---

SETTINGS = LeagueSettings(
    teams=10,
    scoring=ScoringSettings(),
    roster_slots={"QB": 1, "RB": 1, "WR": 1, "TE": 1, "FLEX": 1, "K": 0, "DST": 0},
    bench=2,
)


def _p(name, position, adp, stdev=1.0):
    return FfcPlayer(name=name, position=position, team="XXX", adp=adp, stdev=stdev, high=1, low=100)


LIVE_POOL = [
    _p("My Keeper RB", "RB", 1.0),  # already mine, drafted at pick 1
    _p("RB Two", "RB", 5.0),
    _p("WR One", "WR", 2.0),
    _p("WR Two", "WR", 6.0),
    _p("QB One", "QB", 20.0),
    _p("TE One", "TE", 15.0),
]

DISTRIBUTIONS = {
    ("RB", 0): OutcomeDistribution(position="RB", bucket=0, outcomes=[300.0]),  # fixed, no variance -> deterministic
    ("WR", 0): OutcomeDistribution(position="WR", bucket=0, outcomes=[100.0]),
    ("QB", 0): OutcomeDistribution(position="QB", bucket=0, outcomes=[50.0]),
    ("TE", 0): OutcomeDistribution(position="TE", bucket=0, outcomes=[50.0]),
}


def test_already_mine_player_contributes_to_roster_value():
    # 10-team, slot 1: pick 1 is mine, already drafted "My Keeper RB" (300 pts).
    # Deciding pick 11 (my next pick, round 2) -- WR is the only sane choice
    # here (RB Two is worse, and we already have an RB). Either way, the
    # already-drafted 300-point RB must show up in the total.
    picks = [{"pick": 1, "player": "My Keeper RB"}]
    state = state_from_picks(teams=10, my_draft_slot=1, picks=picks)
    assert state.current_pick == 2

    results = simulate_position_choice(
        "WR", LIVE_POOL, state, SETTINGS, HistoricalBootstrapModel(DISTRIBUTIONS), num_sims=5, rng=random.Random(1)
    )

    assert results is not None
    # My Keeper RB (300) must be included -- a roster with just WR(100)+QB(50)+TE(50)
    # style totals would never reach anywhere near 300+ without it.
    assert min(results) >= 300.0


# --- opponent_weight_fn threading: regression for the gap documented in
# backtest.py's run_backtest docstring -- the internal Monte Carlo lookahead
# used to hardcode the plain Gaussian pick_weight regardless of what a caller
# (e.g. backtest.py's opponent_model flag) wanted the rest of the draft to
# assume. ---


def test_simulate_position_choice_threads_custom_opponent_weight_fn_to_sample_pick():
    picks = [{"pick": 1, "player": "My Keeper RB"}]
    state = state_from_picks(teams=10, my_draft_slot=1, picks=picks)

    seen_weight_fns = []

    def spy_sample_pick(pool, pick_number, rng=None, weight_fn=pick_weight):
        seen_weight_fns.append(weight_fn)
        return real_sample_pick(pool, pick_number, rng, weight_fn=weight_fn)

    with patch("fantasyprep.draft_sim.simulate.sample_pick", side_effect=spy_sample_pick):
        results = simulate_position_choice(
            "WR", LIVE_POOL, state, SETTINGS, HistoricalBootstrapModel(DISTRIBUTIONS),
            num_sims=1, rng=random.Random(1), opponent_weight_fn=pick_weight_with_tail_floor,
        )

    assert results is not None
    assert seen_weight_fns  # sample_pick was actually invoked at least once
    assert all(fn is pick_weight_with_tail_floor for fn in seen_weight_fns)


def test_recommend_positions_default_opponent_weight_fn_is_plain_gaussian():
    picks = [{"pick": 1, "player": "My Keeper RB"}]
    state = state_from_picks(teams=10, my_draft_slot=1, picks=picks)

    seen_weight_fns = []

    def spy_sample_pick(pool, pick_number, rng=None, weight_fn=pick_weight):
        seen_weight_fns.append(weight_fn)
        return real_sample_pick(pool, pick_number, rng, weight_fn=weight_fn)

    with patch("fantasyprep.draft_sim.simulate.sample_pick", side_effect=spy_sample_pick):
        rows = recommend_positions(
            LIVE_POOL, state, SETTINGS, HistoricalBootstrapModel(DISTRIBUTIONS),
            num_sims=1, rng=random.Random(1),
        )

    assert rows
    assert seen_weight_fns
    assert all(fn is pick_weight for fn in seen_weight_fns)


# --- HistoricalBootstrapModel: real crash risk this session -- a position with
# zero recorded historical distributions (DST, since nfl_stats.py's
# POSITION_MAP never computes it) used to raise KeyError from outcome_for_rank
# whenever the inner Monte Carlo sampled one into a hypothetical future pick.
# Live-reachable with the default points_source=historical webapp path. ---


def test_historical_bootstrap_model_scores_zero_for_position_with_no_data():
    model = HistoricalBootstrapModel(DISTRIBUTIONS)  # DISTRIBUTIONS has no "DST" entries
    player = _p("Some DST", "DST", 140.0)
    assert model.sample(player, pos_ranks={}, rng=random.Random(1)) == 0.0


# --- EspnProjectionModel: named-player projection, falling back to historical ---


def test_espn_projection_model_uses_named_projection():
    model = EspnProjectionModel({"WR One": 250.0}, fallback=HistoricalBootstrapModel(DISTRIBUTIONS))
    player = _p("WR One", "WR", 2.0)
    assert model.sample(player, pos_ranks={}, rng=random.Random(1)) == 250.0


def test_espn_projection_model_falls_back_when_unprojected():
    model = EspnProjectionModel({}, fallback=HistoricalBootstrapModel(DISTRIBUTIONS))
    player = _p("WR One", "WR", 2.0)
    pos_ranks = {"WR One": 1}
    assert model.sample(player, pos_ranks, rng=random.Random(1)) == 100.0  # from DISTRIBUTIONS WR bucket 0


def test_espn_projection_model_name_matching_is_normalized():
    model = EspnProjectionModel({"D.J. Chark Jr.": 180.0}, fallback=HistoricalBootstrapModel(DISTRIBUTIONS))
    player = _p("DJ Chark", "WR", 50.0)
    assert model.sample(player, pos_ranks={}, rng=random.Random(1)) == 180.0


# --- CLI --points-source flag ---


def test_parse_args_points_source_defaults_to_historical():
    args = parse_args(["--draft-state", "x.json", "--year", "2026"])
    assert args.points_source == "historical"


def test_parse_args_points_source_accepts_espn():
    args = parse_args(["--draft-state", "x.json", "--year", "2026", "--points-source", "espn"])
    assert args.points_source == "espn"


def test_parse_args_points_source_rejects_invalid_choice():
    try:
        parse_args(["--draft-state", "x.json", "--year", "2026", "--points-source", "vegas"])
        assert False, "expected SystemExit from argparse"
    except SystemExit:
        pass


def test_build_points_model_historical_does_not_need_network(tmp_path: Path):
    model = build_points_model("historical", SETTINGS, year=2026, raw_dir=tmp_path, distributions=DISTRIBUTIONS)
    assert isinstance(model, HistoricalBootstrapModel)
