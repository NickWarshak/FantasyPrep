from pathlib import Path

from fantasyprep.historical.outcomes import (
    OutcomeDistribution,
    bucket_for_rank,
    outcome_for_rank,
    _load_cached,
    _save_cache,
)


def test_bucket_for_rank_groups_in_threes():
    assert bucket_for_rank(1) == 0
    assert bucket_for_rank(2) == 0
    assert bucket_for_rank(3) == 0
    assert bucket_for_rank(4) == 1
    assert bucket_for_rank(6) == 1
    assert bucket_for_rank(7) == 2


def test_outcome_for_rank_exact_bucket_match():
    distributions = {
        ("WR", 0): OutcomeDistribution(position="WR", bucket=0, outcomes=[300.0, 280.0]),
        ("WR", 1): OutcomeDistribution(position="WR", bucket=1, outcomes=[200.0]),
    }
    result = outcome_for_rank(distributions, "WR", rank=2)  # rank 2 -> bucket 0
    assert result.outcomes == [300.0, 280.0]


def test_outcome_for_rank_falls_back_to_deepest_bucket():
    distributions = {
        ("WR", 0): OutcomeDistribution(position="WR", bucket=0, outcomes=[300.0]),
        ("WR", 1): OutcomeDistribution(position="WR", bucket=1, outcomes=[150.0]),
    }
    # rank 100 -> bucket 33, far beyond anything observed -> falls back to bucket 1
    result = outcome_for_rank(distributions, "WR", rank=100)
    assert result.outcomes == [150.0]


def test_outcome_for_rank_missing_position_raises():
    distributions = {("WR", 0): OutcomeDistribution(position="WR", bucket=0, outcomes=[300.0])}
    try:
        outcome_for_rank(distributions, "QB", rank=1)
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_cache_roundtrip(tmp_path: Path):
    distributions = {
        ("WR", 0): OutcomeDistribution(position="WR", bucket=0, outcomes=[300.0, 280.5]),
        ("RB", 2): OutcomeDistribution(position="RB", bucket=2, outcomes=[110.25]),
    }
    cache_path = tmp_path / "outcomes.json"
    _save_cache(cache_path, distributions)
    loaded = _load_cached(cache_path)

    assert loaded[("WR", 0)].outcomes == [300.0, 280.5]
    assert loaded[("RB", 2)].outcomes == [110.25]
