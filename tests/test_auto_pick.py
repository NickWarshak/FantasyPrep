from fantasyprep.adp_gap.compute import tolerance_for_adp
from fantasyprep.draft_sim.auto_pick import CHALK_STDEV, MIN_STDEV, espn_pool_for_auto_pick
from fantasyprep.sources.espn import EspnPlayer


def _p(name, position, adp):
    return EspnPlayer(espn_id=1, name=name, position=position, team="XXX", espn_adp=adp, espn_expert_rank=None)


def test_filters_to_fantasy_relevant_positions():
    players = [_p("QB Guy", "QB", 20.0), _p("Kicker Guy", "K", 180.0), _p("DST Guy", "DST", 150.0)]
    pool = espn_pool_for_auto_pick(players, randomness=1.0)
    names = {p.name for p in pool}
    assert "QB Guy" in names
    assert "DST Guy" in names
    assert "Kicker Guy" not in names  # no K roster slot, not auto-pickable


def test_randomness_zero_is_near_chalk():
    pool = espn_pool_for_auto_pick([_p("A", "RB", 5.0)], randomness=0)
    assert pool[0].stdev == CHALK_STDEV


def test_negative_randomness_treated_as_chalk():
    pool = espn_pool_for_auto_pick([_p("A", "RB", 5.0)], randomness=-1.0)
    assert pool[0].stdev == CHALK_STDEV


def test_randomness_one_matches_tolerance_heuristic():
    pool = espn_pool_for_auto_pick([_p("A", "RB", 5.0)], randomness=1.0)
    assert pool[0].stdev == tolerance_for_adp(5.0)


def test_randomness_scales_stdev():
    pool_1x = espn_pool_for_auto_pick([_p("A", "RB", 50.0)], randomness=1.0)
    pool_2x = espn_pool_for_auto_pick([_p("A", "RB", 50.0)], randomness=2.0)
    assert pool_2x[0].stdev == pool_1x[0].stdev * 2


def test_stdev_never_falls_below_floor_for_small_randomness():
    pool = espn_pool_for_auto_pick([_p("A", "RB", 5.0)], randomness=0.001)
    assert pool[0].stdev >= MIN_STDEV
