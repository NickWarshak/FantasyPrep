import pytest
from fantasyprep.draft_sim.roster import (
    DraftedPlayer,
    lineup_context,
    marginal_gain,
    starting_lineup_value,
    starting_lineup_value_by_position,
)
from fantasyprep.league.settings import LeagueSettings, ScoringSettings, default_settings

SETTINGS = LeagueSettings(
    teams=10,
    scoring=ScoringSettings(),
    roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1},
    bench=6,
)


def _p(name, position, points):
    return DraftedPlayer(name=name, position=position, points=points)


def test_fills_required_slots_with_best_players():
    players = [
        _p("QB1", "QB", 300),
        _p("RB1", "RB", 200),
        _p("RB2", "RB", 180),
        _p("WR1", "WR", 220),
        _p("WR2", "WR", 190),
        _p("TE1", "TE", 150),
    ]
    value = starting_lineup_value(players, SETTINGS)
    assert value == 300 + 200 + 180 + 220 + 190 + 150


def test_flex_takes_best_remaining_eligible_player():
    players = [
        _p("QB1", "QB", 300),
        _p("RB1", "RB", 200),
        _p("RB2", "RB", 180),
        _p("RB3", "RB", 170),  # should fill FLEX
        _p("WR1", "WR", 220),
        _p("WR2", "WR", 190),
        _p("TE1", "TE", 150),
    ]
    value = starting_lineup_value(players, SETTINGS)
    assert value == 300 + 200 + 180 + 220 + 190 + 150 + 170


def test_flex_ignores_non_eligible_positions():
    players = [
        _p("QB1", "QB", 300),
        _p("QB2", "QB", 250),  # extra QB, not FLEX-eligible, should not fill FLEX
        _p("RB1", "RB", 200),
        _p("RB2", "RB", 180),
        _p("WR1", "WR", 220),
        _p("WR2", "WR", 190),
        _p("TE1", "TE", 150),
    ]
    value = starting_lineup_value(players, SETTINGS)
    # QB2 sits on the bench -- doesn't count
    assert value == 300 + 200 + 180 + 220 + 190 + 150


def test_bench_points_do_not_count():
    players = [
        _p("QB1", "QB", 300),
        _p("RB1", "RB", 200),
        _p("RB2", "RB", 180),
        _p("RB3", "RB", 9999),  # would blow up the total if it counted twice or leaked to bench
        _p("WR1", "WR", 220),
        _p("WR2", "WR", 190),
        _p("TE1", "TE", 150),
    ]
    value = starting_lineup_value(players, SETTINGS)
    # RB3(9999) and RB1(200) fill the two RB slots; RB2(180) is the next-best
    # RB-eligible player and flows into FLEX rather than being dropped.
    assert value == 300 + 9999 + 200 + 220 + 190 + 150 + 180


def test_missing_position_leaves_slot_empty():
    players = [
        _p("QB1", "QB", 300),
        _p("RB1", "RB", 200),
        _p("WR1", "WR", 220),
        _p("WR2", "WR", 190),
        _p("TE1", "TE", 150),
    ]
    # Only one RB drafted -- second RB slot and FLEX both go unfilled
    value = starting_lineup_value(players, SETTINGS)
    assert value == 300 + 200 + 220 + 190 + 150


# --- starting_lineup_value_by_position ---------------------------------------------------


def test_by_position_sums_to_the_same_total_as_starting_lineup_value():
    # Consistency check across every fixture roster already used above --
    # the breakdown must always sum to exactly what the original function
    # returns for the same roster, since it's a breakdown of the same
    # computation, not a different one.
    rosters = [
        [_p("QB1", "QB", 300), _p("RB1", "RB", 200), _p("RB2", "RB", 180),
         _p("RB3", "RB", 170), _p("WR1", "WR", 220), _p("WR2", "WR", 190), _p("TE1", "TE", 150)],
        [_p("QB1", "QB", 300), _p("QB2", "QB", 250), _p("RB1", "RB", 200),
         _p("RB2", "RB", 180), _p("WR1", "WR", 220), _p("WR2", "WR", 190), _p("TE1", "TE", 150)],
        [_p("QB1", "QB", 300), _p("RB1", "RB", 200), _p("WR1", "WR", 220),
         _p("WR2", "WR", 190), _p("TE1", "TE", 150)],
    ]
    for players in rosters:
        total = starting_lineup_value(players, SETTINGS)
        breakdown = starting_lineup_value_by_position(players, SETTINGS)
        assert sum(breakdown.values()) == total


def test_by_position_attributes_flex_starter_to_their_real_position_not_flex():
    players = [
        _p("QB1", "QB", 300),
        _p("RB1", "RB", 200),
        _p("RB2", "RB", 180),
        _p("RB3", "RB", 170),  # fills FLEX -- should show up under "RB", not a separate "FLEX" key
        _p("WR1", "WR", 220),
        _p("WR2", "WR", 190),
        _p("TE1", "TE", 150),
    ]
    breakdown = starting_lineup_value_by_position(players, SETTINGS)
    assert "FLEX" not in breakdown
    assert breakdown["RB"] == 200 + 180 + 170  # both required RB slots plus the FLEX-filling 3rd RB


def test_by_position_bench_points_excluded_same_as_starting_lineup_value():
    players = [
        _p("QB1", "QB", 300),
        _p("RB1", "RB", 200),
        _p("RB2", "RB", 180),
        _p("WR1", "WR", 220),
        _p("WR2", "WR", 190),
        _p("TE1", "TE", 150),
        _p("Best WR", "WR", 9999),  # 3rd WR, best of all -- starts (WR slot), bumping a weaker one
        _p("Truly Bench WR", "WR", 1),  # 4th WR, worst of all -- every other slot/FLEX already spoken for
    ]
    breakdown = starting_lineup_value_by_position(players, SETTINGS)
    # 2 WR slots + 1 FLEX (RB/WR/TE-eligible) = 3 WR starters possible here
    # given no other RB/TE surplus competes for FLEX: Best WR, WR1, WR2
    # all start; Truly Bench WR (1 pt) is worst of 4 WRs and the only one
    # left once every slot including FLEX is claimed -- excluded, same as
    # starting_lineup_value would exclude it from the total.
    assert breakdown["WR"] == 9999 + 220 + 190
    assert breakdown["WR"] == starting_lineup_value(players, SETTINGS) - (300 + 200 + 180 + 150)


# --- O(1) marginal gain -----------------------------------------------------

def _pbp(team):
    out = {}
    for p in team:
        out.setdefault(p.position, []).append(p.points)
    return out


def _brute_gain(team, position, points, settings):
    """The full recomputation the O(1) path replaced."""
    base = starting_lineup_value(team, settings)
    return starting_lineup_value(team + [DraftedPlayer("new", position, points)], settings) - base


def test_marginal_gain_matches_full_recomputation_on_an_open_slot():
    s = default_settings()
    team = [DraftedPlayer("wr1", "WR", 200.0)]

    fast = marginal_gain(lineup_context(_pbp(team), s), "QB", 300.0, s)

    assert fast == pytest.approx(300.0)
    assert fast == pytest.approx(_brute_gain(team, "QB", 300.0, s))


def test_marginal_gain_measures_against_the_displaced_starter():
    """Beating your position's weakest starter only earns the difference."""
    s = default_settings()
    team = [DraftedPlayer("qb1", "QB", 250.0)]

    fast = marginal_gain(lineup_context(_pbp(team), s), "QB", 300.0, s)

    assert fast == pytest.approx(50.0)
    assert fast == pytest.approx(_brute_gain(team, "QB", 300.0, s))


def test_marginal_gain_is_zero_for_a_bench_player():
    s = default_settings()
    team = [DraftedPlayer(f"qb{i}", "QB", 300.0) for i in range(2)]

    fast = marginal_gain(lineup_context(_pbp(team), s), "QB", 100.0, s)

    assert fast == pytest.approx(0.0)
    assert fast == pytest.approx(_brute_gain(team, "QB", 100.0, s))


def test_marginal_gain_accounts_for_the_flex_cascade():
    """Displacing an RB starter does not lose him -- he falls into FLEX and
    pushes someone else out instead, so the gain is measured against THAT
    player. Getting this wrong was the main risk in the O(1) rewrite."""
    s = default_settings()  # QB1 RB2 WR2 TE1 FLEX2 DST1
    team = (
        [DraftedPlayer(f"rb{i}", "RB", pts) for i, pts in enumerate([250.0, 200.0])]
        + [DraftedPlayer(f"wr{i}", "WR", pts) for i, pts in enumerate([240.0, 230.0, 120.0, 110.0])]
    )

    fast = marginal_gain(lineup_context(_pbp(team), s), "RB", 260.0, s)

    assert fast == pytest.approx(_brute_gain(team, "RB", 260.0, s))


def test_marginal_gain_matches_brute_force_over_random_rosters():
    """The equivalence that licenses using the fast path at all."""
    import random

    rng = random.Random(11)
    s = default_settings()
    positions = ["QB", "RB", "WR", "TE", "DST"]
    for _ in range(2000):
        team = [
            DraftedPlayer(f"p{i}", rng.choice(positions), round(rng.uniform(0, 400), 2))
            for i in range(rng.randint(0, 14))
        ]
        position = rng.choice(positions)
        points = round(rng.uniform(0, 400), 2)

        fast = marginal_gain(lineup_context(_pbp(team), s), position, points, s)

        assert fast == pytest.approx(_brute_gain(team, position, points, s), abs=1e-9)
