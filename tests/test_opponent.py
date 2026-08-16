import random
from collections import Counter

from fantasyprep.draft_sim.opponent import TAIL_Z, pick_weight, pick_weight_with_tail_floor, sample_pick
from fantasyprep.historical.sources.ffc import FfcPlayer


def _p(name, adp, stdev=1.0, position="WR"):
    return FfcPlayer(name=name, position=position, team="XXX", adp=adp, stdev=stdev, high=1, low=100)


def test_pick_weight_peaks_at_players_own_adp():
    player = _p("Test", adp=10.0, stdev=2.0)
    assert pick_weight(player, 10) > pick_weight(player, 15)
    assert pick_weight(player, 10) > pick_weight(player, 5)


def test_pick_weight_symmetric_around_adp():
    player = _p("Test", adp=10.0, stdev=2.0)
    assert pick_weight(player, 8) == pick_weight(player, 12)


def test_low_stdev_sharpens_the_peak():
    tight = _p("Tight", adp=10.0, stdev=0.5)
    loose = _p("Loose", adp=10.0, stdev=5.0)
    # far from ADP, the tight player's weight should drop off faster
    assert pick_weight(tight, 20) < pick_weight(loose, 20)


def test_sample_pick_favors_player_closest_to_pick_number():
    pool = [_p("Early", adp=1.0, stdev=1.0), _p("Late", adp=100.0, stdev=1.0)]
    rng = random.Random(42)
    counts = Counter(sample_pick(pool, pick_number=1, rng=rng).name for _ in range(500))
    assert counts["Early"] > counts["Late"]


def test_sample_pick_empty_pool_raises():
    try:
        sample_pick([], pick_number=1)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_sample_pick_deterministic_with_seeded_rng():
    pool = [_p("A", adp=5.0), _p("B", adp=6.0), _p("C", adp=50.0)]
    rng1 = random.Random(7)
    rng2 = random.Random(7)
    picks1 = [sample_pick(pool, 5, rng1).name for _ in range(20)]
    picks2 = [sample_pick(pool, 5, rng2).name for _ in range(20)]
    assert picks1 == picks2


# --- pick_weight_with_tail_floor: fixes the "stuck player" bug -- a real
# top-of-draft player who has anomalously fallen many rounds past their ADP
# should become MORE likely to be taken, not collapse toward zero. ---


def test_tail_floor_matches_plain_gaussian_within_tail_z():
    # Within TAIL_Z stdevs of ADP, the two functions must agree exactly --
    # the tail floor only changes behavior *beyond* the boundary.
    player = _p("Test", adp=10.0, stdev=2.0)
    for pick in (5, 8, 10, 12, 10 + 2 * TAIL_Z):  # up to and including the boundary
        assert pick_weight_with_tail_floor(player, pick) == pick_weight(player, pick)


def test_tail_floor_rises_instead_of_collapsing_far_past_adp():
    # Reproduces the concrete case that surfaced this bug: a top player
    # (real 2023 ADP/stdev for Christian McCaffrey) still on the board many
    # picks past where they should've gone.
    cmc = _p("Christian McCaffrey", adp=2.4, stdev=1.0, position="RB")
    plain_weight_pick10 = pick_weight(cmc, 10)
    floor_weight_pick10 = pick_weight_with_tail_floor(cmc, 10)
    assert plain_weight_pick10 < 0.001  # confirms the bug: plain Gaussian is ~dead here
    assert floor_weight_pick10 > 0.5  # tail floor: should be highly likely to go, not stuck


def test_tail_floor_is_monotonically_increasing_beyond_tail_z():
    player = _p("Test", adp=1.0, stdev=1.0)
    picks = [1 + TAIL_Z + step for step in range(0, 20)]
    weights = [pick_weight_with_tail_floor(player, p) for p in picks]
    assert weights == sorted(weights)  # never dips back down once past the boundary
    assert weights[-1] > weights[0]


def test_tail_floor_approaches_high_confidence_for_a_far_fallen_player():
    player = _p("Test", adp=1.0, stdev=1.0)
    far_weight = pick_weight_with_tail_floor(player, pick_number=30)
    assert far_weight > 0.99  # extreme overshoot saturates toward 1.0 (float underflow, expected)


def test_tail_floor_does_not_affect_players_not_yet_at_their_adp():
    # A player who hasn't reached their ADP window is still correctly
    # unlikely -- the fix only changes the *late* tail, not the early side.
    player = _p("Test", adp=50.0, stdev=2.0)
    assert pick_weight_with_tail_floor(player, pick_number=1) == pick_weight(player, pick_number=1)
    assert pick_weight_with_tail_floor(player, pick_number=1) < 0.01


def test_sample_pick_accepts_custom_weight_fn():
    # Same "stuck" player, same pick number, only the weight function
    # differs -- the tail floor should select them far more often than the
    # plain Gaussian does, directly demonstrating the fix changes real
    # sampling behavior, not just the raw weight numbers.
    pool = [_p("Stuck", adp=1.0, stdev=1.0), _p("Competitor", adp=50.0, stdev=5.0)]

    def count_stuck(weight_fn):
        rng = random.Random(3)
        counts = Counter(
            sample_pick(pool, pick_number=50, rng=rng, weight_fn=weight_fn).name for _ in range(500)
        )
        return counts["Stuck"]

    assert count_stuck(pick_weight_with_tail_floor) > count_stuck(pick_weight)
