import random
from collections import Counter

import numpy as np
import pytest

from fantasyprep.draft_sim.opponent import (
    TAIL_Z,
    _vectorized_pick_weight,
    _vectorized_pick_weight_with_tail_floor,
    pick_weight,
    pick_weight_with_tail_floor,
    sample_pick,
)
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


# --- Vectorized weight-computation equivalence (2026-08-16 performance work) ---
# sample_pick's internals were rewritten to compute weights for the whole
# pool with numpy instead of one Python call per player (profiling showed
# this was ~90-97% of runtime in both live recommendations and Draft Now
# vs. Wait validation). These tests are the actual guarantee that the
# vectorized path computes the same distribution as the original scalar
# pick_weight/pick_weight_with_tail_floor -- not bit-for-bit identical
# floats (numpy's summation/exp can round differently), but the same
# distribution within a tight tolerance.

def _random_players(rng, n):
    players = []
    for _ in range(n):
        adp = rng.uniform(1.0, 200.0)
        stdev = rng.choice([0.1, 0.3, 1.0, 3.0, 10.0, 30.0])  # tiny to large, including sub-MIN_STDEV
        players.append(_p(f"P{len(players)}", adp=adp, stdev=stdev))
    return players


def test_vectorized_pick_weight_matches_scalar_across_random_players_and_picks():
    rng = random.Random(12345)
    players = _random_players(rng, 500)
    pick_numbers = [rng.randint(1, 250) for _ in range(20)]  # spans Gaussian zone, boundary, and deep tail

    adp = np.array([p.adp for p in players])
    stdev = np.array([p.stdev for p in players])

    for pick_number in pick_numbers:
        vectorized = _vectorized_pick_weight(pick_number, adp, stdev)
        scalar = np.array([pick_weight(p, pick_number) for p in players])
        assert np.allclose(vectorized, scalar, rtol=1e-9, atol=1e-12)


def test_vectorized_pick_weight_with_tail_floor_matches_scalar_across_random_players_and_picks():
    rng = random.Random(54321)
    players = _random_players(rng, 500)
    # Deliberately include picks that land every player in each of the
    # three regimes at some point: well before ADP, near ADP, and far past
    # the TAIL_Z boundary into the rising-hazard tail.
    pick_numbers = [1, 5, 25, 50, 100, 150, 200, 250]

    adp = np.array([p.adp for p in players])
    stdev = np.array([p.stdev for p in players])

    for pick_number in pick_numbers:
        vectorized = _vectorized_pick_weight_with_tail_floor(pick_number, adp, stdev)
        scalar = np.array([pick_weight_with_tail_floor(p, pick_number) for p in players])
        assert np.allclose(vectorized, scalar, rtol=1e-9, atol=1e-12)


def test_vectorized_tail_floor_boundary_exact_agreement():
    # The boundary itself (z == TAIL_Z exactly) is where a piecewise
    # formula is most likely to disagree by an off-by-one regime error --
    # test it explicitly, not just as one random draw among many.
    player = _p("Boundary", adp=10.0, stdev=1.0)
    pick_at_boundary = 10 + TAIL_Z  # z == TAIL_Z exactly
    adp = np.array([player.adp])
    stdev = np.array([player.stdev])

    vectorized = _vectorized_pick_weight_with_tail_floor(pick_at_boundary, adp, stdev)[0]
    scalar = pick_weight_with_tail_floor(player, pick_at_boundary)
    assert vectorized == pytest.approx(scalar, rel=1e-9)


# --- Sampling-distribution correctness (does sample_pick draw from the
# distribution its own weights imply, not just "does it run") ---


def test_sample_pick_empirical_frequencies_match_theoretical_weights():
    rng = random.Random(999)
    pool = [_p(f"P{i}", adp=float(1 + i * 3), stdev=2.0) for i in range(20)]
    pick_number = 25

    weights = [pick_weight(p, pick_number) for p in pool]
    total = sum(weights)
    expected = {p.name: w / total for p, w in zip(pool, weights)}

    n_draws = 100_000
    counts = Counter(sample_pick(pool, pick_number, rng=rng).name for _ in range(n_draws))
    for name, expected_prob in expected.items():
        observed_prob = counts.get(name, 0) / n_draws
        # Generous tolerance (this is a stochastic test) -- still tight
        # enough to catch a real distributional bug, not just noise.
        assert observed_prob == pytest.approx(expected_prob, abs=0.01), (
            f"{name}: expected {expected_prob:.4f}, observed {observed_prob:.4f}"
        )


# --- No double-draft: exhausting a pool via repeated sample_pick + removal
# must never repeat a player. Not new behavior (callers, not sample_pick,
# own the removal) -- confirming the vectorized rewrite didn't break the
# contract callers rely on. ---


def test_repeated_sample_and_remove_never_repeats_a_player():
    rng = random.Random(2026)
    pool = [_p(f"P{i}", adp=float(1 + i), stdev=rng.choice([0.5, 1.0, 5.0])) for i in range(80)]
    remaining = list(pool)
    drafted_names = []
    pick_number = 1
    while remaining:
        chosen = sample_pick(remaining, pick_number, rng=rng)
        assert chosen.name not in drafted_names
        drafted_names.append(chosen.name)
        remaining.remove(chosen)
        pick_number += 1
    assert len(drafted_names) == len(pool)
    assert set(drafted_names) == {p.name for p in pool}


# --- sample_pick_index: the deeper optimization -- fixed arrays built once,
# an availability mask instead of rebuilding numpy arrays from Python
# objects on every single pick (profiling showed THIS was ~78% of
# remaining runtime even after vectorizing the weight math itself). ---

from fantasyprep.draft_sim.opponent import sample_pick_index


def _arrays(players):
    return np.array([p.adp for p in players]), np.array([p.stdev for p in players])


def test_sample_pick_index_empirical_frequencies_match_theoretical_weights():
    rng = random.Random(999)
    pool = [_p(f"P{i}", adp=float(1 + i * 3), stdev=2.0) for i in range(20)]
    adp, stdev = _arrays(pool)
    available = np.ones(len(pool), dtype=bool)
    pick_number = 25

    weights = [pick_weight(p, pick_number) for p in pool]
    total = sum(weights)
    expected = {i: w / total for i, w in enumerate(weights)}

    n_draws = 100_000
    counts = Counter(
        sample_pick_index(adp, stdev, available, pick_number, rng) for _ in range(n_draws)
    )
    for idx, expected_prob in expected.items():
        observed_prob = counts.get(idx, 0) / n_draws
        assert observed_prob == pytest.approx(expected_prob, abs=0.01), (
            f"index {idx}: expected {expected_prob:.4f}, observed {observed_prob:.4f}"
        )


def test_sample_pick_index_never_returns_an_unavailable_index():
    rng = random.Random(2026)
    pool = [_p(f"P{i}", adp=float(1 + i), stdev=1.0) for i in range(30)]
    adp, stdev = _arrays(pool)
    available = np.ones(len(pool), dtype=bool)

    chosen_indices = []
    for pick_number in range(1, len(pool) + 1):
        idx = sample_pick_index(adp, stdev, available, pick_number, rng)
        assert available[idx]
        assert idx not in chosen_indices
        chosen_indices.append(idx)
        available[idx] = False

    assert sorted(chosen_indices) == list(range(len(pool)))  # every index drafted exactly once
    assert not available.any()


def test_sample_pick_index_matches_sample_pick_distribution():
    # sample_pick (shrinking Python list) and sample_pick_index (fixed
    # arrays + mask) are two different implementations of the same
    # sampling operation -- confirm they actually agree, not just that
    # each independently looks reasonable in isolation.
    rng_list = random.Random(42)
    rng_index = random.Random(42)
    pool = [_p(f"P{i}", adp=float(1 + i * 2), stdev=1.5) for i in range(15)]
    pick_number = 10

    list_counts = Counter(sample_pick(list(pool), pick_number, rng=rng_list).name for _ in range(50_000))

    adp, stdev = _arrays(pool)
    available = np.ones(len(pool), dtype=bool)
    index_counts = Counter()
    for _ in range(50_000):
        idx = sample_pick_index(adp, stdev, available, pick_number, rng_index)
        index_counts[pool[idx].name] += 1

    for player in pool:
        list_prob = list_counts.get(player.name, 0) / 50_000
        index_prob = index_counts.get(player.name, 0) / 50_000
        assert list_prob == pytest.approx(index_prob, abs=0.015)


def test_sample_pick_index_zero_weight_fallback_picks_only_among_available():
    # Every player anomalously far past pick_number=1 under plain Gaussian
    # underflows to exactly 0 -- forces the zero-weight fallback path.
    rng = random.Random(1)
    pool = [_p(f"P{i}", adp=500.0, stdev=1.0) for i in range(5)]
    adp, stdev = _arrays(pool)
    available = np.array([True, False, True, False, True])

    for _ in range(50):
        idx = sample_pick_index(adp, stdev, available, pick_number=1, rng=rng, weight_fn=pick_weight)
        assert available[idx]
