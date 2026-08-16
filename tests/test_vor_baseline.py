from fantasyprep.draft_sim.vor_baseline import replacement_level_points, vor_pick
from fantasyprep.historical.outcomes import OutcomeDistribution
from fantasyprep.historical.sources.ffc import FfcPlayer
from fantasyprep.league.settings import LeagueSettings, ScoringSettings

SETTINGS = LeagueSettings(
    teams=10,
    scoring=ScoringSettings(),
    roster_slots={"QB": 1, "RB": 1, "WR": 1},
    bench=1,
)


def _p(name, position, adp):
    return FfcPlayer(name=name, position=position, team="XXX", adp=adp, stdev=1.0, high=1, low=100)


# --- replacement_level_points ---------------------------------------------------


def test_replacement_level_points_uses_the_cutoff_bucket():
    # RB cutoff=30 -> bucket_for_rank(30) with BUCKET_WIDTH=3 -> bucket 9 (ranks 28-30).
    distributions = {
        ("RB", 0): OutcomeDistribution("RB", 0, [200.0]),  # elite tier, ranks 1-3
        ("RB", 9): OutcomeDistribution("RB", 9, [40.0, 50.0]),  # replacement tier, ranks 28-30
    }
    level = replacement_level_points(distributions, "RB", rank_cutoff={"RB": 30})
    assert level == 45.0  # mean of the replacement-tier bucket, not the elite one


# --- vor_pick ---------------------------------------------------


def test_vor_pick_prefers_bigger_replacement_gap_over_higher_raw_value():
    # Position A has higher raw expected value (100) but a small gap over
    # replacement (10). Position B has lower raw value (80) but a much
    # bigger gap (60) -- a scarcer position. VOR should pick B's player,
    # not the higher-raw-value A player and not the better-ADP one either.
    pool = [
        _p("Player A", "QB", adp=1.0),  # best ADP, best raw value, worst VOR
        _p("Player B", "RB", adp=5.0),
    ]
    distributions = {
        ("QB", 0): OutcomeDistribution("QB", 0, [100.0]),
        ("QB", 4): OutcomeDistribution("QB", 4, [90.0]),  # replacement rank 15 -> bucket 4
        ("RB", 0): OutcomeDistribution("RB", 0, [80.0]),
        ("RB", 9): OutcomeDistribution("RB", 9, [20.0]),  # replacement rank 30 -> bucket 9
    }
    pos_ranks = {"Player A": 1, "Player B": 1}

    chosen = vor_pick(pool, drafted_positions=[], settings=SETTINGS, distributions=distributions, pos_ranks=pos_ranks)
    assert chosen.name == "Player B"  # VOR 60 beats VOR 10, despite lower raw value and worse ADP


def test_vor_pick_only_considers_positions_of_need():
    pool = [
        _p("Best QB", "QB", adp=1.0),
        _p("Only RB", "RB", adp=10.0),
    ]
    distributions = {
        ("QB", 0): OutcomeDistribution("QB", 0, [500.0]),  # would win on VOR if it were eligible
        ("QB", 4): OutcomeDistribution("QB", 4, [1.0]),
        ("RB", 3): OutcomeDistribution("RB", 3, [50.0]),
        ("RB", 9): OutcomeDistribution("RB", 9, [10.0]),
    }
    pos_ranks = {"Best QB": 1, "Only RB": 10}

    # QB already drafted -- only RB is needed, so QB's huge VOR shouldn't matter.
    chosen = vor_pick(
        pool, drafted_positions=["QB"], settings=SETTINGS, distributions=distributions, pos_ranks=pos_ranks
    )
    assert chosen.name == "Only RB"


def test_vor_pick_falls_back_to_adp_when_no_historical_data_at_all():
    pool = [_p("Mystery DST", "DST", adp=100.0), _p("Other DST", "DST", adp=150.0)]
    chosen = vor_pick(
        pool, drafted_positions=[], settings=SETTINGS, distributions={}, pos_ranks={}
    )
    assert chosen.name == "Mystery DST"  # no VOR data anywhere -- falls back to best ADP


def test_vor_pick_skips_candidates_with_no_data_in_favor_of_ones_that_have_it():
    pool = [
        _p("No Data Guy", "DST", adp=1.0),  # best ADP, but zero historical data
        _p("Has Data Guy", "RB", adp=50.0),
    ]
    distributions = {
        ("RB", 16): OutcomeDistribution("RB", 16, [30.0]),  # rank 50 -> bucket 16
        ("RB", 9): OutcomeDistribution("RB", 9, [10.0]),
    }
    pos_ranks = {"Has Data Guy": 50}
    chosen = vor_pick(
        pool, drafted_positions=[], settings=SETTINGS, distributions=distributions, pos_ranks=pos_ranks
    )
    assert chosen.name == "Has Data Guy"
