"""Tail pooling: starved deepest buckets must become one real distribution.

Regression tests for a live defect. In the production cache the deepest TE
bucket held a single sample (151.7), and `outcome_for_rank` falls back to the
deepest bucket for every rank past the end of the grid -- so every TE beyond
rank 21 sampled that one value deterministically, and it was *higher* than the
median of TE4-6. 2026's pool has 24 TEs, so this was reachable in the live tool.
"""
from __future__ import annotations

import pytest

from fantasyprep.historical.outcomes import (
    MIN_BUCKET_SAMPLES,
    OutcomeDistribution,
    _apply_tail_pooling,
    outcome_for_rank,
    pool_thin_tail,
)


def _dist(position: str, bucket: int, n: int, value: float = 100.0) -> OutcomeDistribution:
    return OutcomeDistribution(position=position, bucket=bucket, outcomes=[value] * n)


def _healthy_then_starved() -> dict:
    """Mirrors the real TE shape: full buckets, then a collapsing tail."""
    return {
        ("TE", 0): _dist("TE", 0, 45, 198.0),
        ("TE", 1): _dist("TE", 1, 45, 145.0),
        ("TE", 2): _dist("TE", 2, 36, 116.0),
        ("TE", 3): _dist("TE", 3, 18, 114.0),
        ("TE", 4): _dist("TE", 4, 1, 151.7),
    }


def test_starved_tail_is_merged_into_one_real_distribution():
    pooled = pool_thin_tail(_healthy_then_starved())

    deepest = max(b for (pos, b) in pooled if pos == "TE")
    assert len(pooled[("TE", deepest)].outcomes) >= MIN_BUCKET_SAMPLES


def test_pooling_walks_up_only_as_far_as_it_must():
    # 1 + 18 = 19, one short of 20, so bucket 2 (36) is pulled in and the walk
    # stops there -- buckets 0 and 1 are healthy and must be left alone.
    pooled = pool_thin_tail(_healthy_then_starved())

    assert sorted(b for (pos, b) in pooled if pos == "TE") == [0, 1, 2]
    assert len(pooled[("TE", 0)].outcomes) == 45
    assert len(pooled[("TE", 1)].outcomes) == 45
    assert len(pooled[("TE", 2)].outcomes) == 36 + 18 + 1


def test_healthy_tail_is_left_completely_untouched():
    healthy = {("WR", 0): _dist("WR", 0, 45), ("WR", 1): _dist("WR", 1, 40)}

    pooled = pool_thin_tail(healthy)

    assert sorted(b for (pos, b) in pooled if pos == "WR") == [0, 1]
    assert len(pooled[("WR", 1)].outcomes) == 40


def test_no_outcome_is_lost_or_duplicated_by_pooling():
    original = _healthy_then_starved()
    before = sorted(o for d in original.values() for o in d.outcomes)

    after = sorted(o for d in pool_thin_tail(original).values() for o in d.outcomes)

    assert before == after


def test_past_the_end_fallback_no_longer_returns_a_single_sample():
    # The actual defect: rank 40 is far past the grid, so outcome_for_rank
    # falls back to the deepest bucket.
    original = _healthy_then_starved()

    legacy = outcome_for_rank(original, "TE", rank=40)
    pooled = outcome_for_rank(pool_thin_tail(original), "TE", rank=40)

    assert len(legacy.outcomes) == 1  # deterministic -- zero variance
    assert len(pooled.outcomes) >= MIN_BUCKET_SAMPLES


def test_pooling_fixes_the_non_monotonic_deep_bucket():
    # The single deep sample (151.7) outranked the median of bucket 1 (145.0).
    import statistics

    original = _healthy_then_starved()
    pooled = pool_thin_tail(original)

    legacy_deep = statistics.median(outcome_for_rank(original, "TE", 40).outcomes)
    pooled_deep = statistics.median(outcome_for_rank(pooled, "TE", 40).outcomes)
    bucket_1 = statistics.median(original[("TE", 1)].outcomes)

    assert legacy_deep > bucket_1  # the bug
    assert pooled_deep < bucket_1  # deeper ranks now score below shallower ones


def test_pooling_handles_a_position_that_never_reaches_the_threshold():
    tiny = {("QB", 0): _dist("QB", 0, 2), ("QB", 1): _dist("QB", 1, 1)}

    pooled = pool_thin_tail(tiny)

    # Pools everything it has rather than raising or leaving a 1-sample bucket.
    assert sorted(b for (pos, b) in pooled if pos == "QB") == [0]
    assert len(pooled[("QB", 0)].outcomes) == 3


def test_positions_are_pooled_independently():
    mixed = {
        ("TE", 0): _dist("TE", 0, 45),
        ("TE", 1): _dist("TE", 1, 1),
        ("WR", 0): _dist("WR", 0, 45),
        ("WR", 1): _dist("WR", 1, 44),
    }

    pooled = pool_thin_tail(mixed)

    assert sorted(b for (pos, b) in pooled if pos == "TE") == [0]
    assert sorted(b for (pos, b) in pooled if pos == "WR") == [0, 1]


def test_legacy_mode_is_an_exact_passthrough():
    original = _healthy_then_starved()

    assert _apply_tail_pooling(original, "legacy") == original


def test_pooled_mode_matches_the_direct_call():
    original = _healthy_then_starved()

    assert _apply_tail_pooling(original, "pooled") == pool_thin_tail(original)


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="tail_pooling"):
        _apply_tail_pooling(_healthy_then_starved(), "sometimes")
