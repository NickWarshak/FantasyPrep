from fantasyprep.draft_sim.roster import DraftedPlayer, starting_lineup_value, starting_lineup_value_by_position
from fantasyprep.league.settings import LeagueSettings, ScoringSettings

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
