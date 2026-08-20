"""Per-player points model: sampling, fallback, and what it is for.

The player-choice experiment lost (39% win rate) under a points model that
could not tell same-bucket candidates apart. This model exists to re-run that
experiment under one that can, so its sampling has to actually differentiate.
"""
from __future__ import annotations

import random

import pytest

from fantasyprep.historical.sources.ffc import FfcPlayer
from fantasyprep.research.profile_points_model import ProfilePointsModel

LEVELS = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)


class _Fallback:
    """Stands in for HistoricalBootstrapModel."""

    def __init__(self, value=42.0):
        self.value = value
        self.calls = []

    def sample(self, player, pos_ranks, rng):
        self.calls.append(player.name)
        return self.value


def _player(name: str) -> FfcPlayer:
    return FfcPlayer(name=name, position="WR", team="KC", adp=10.0, stdev=3.0, high=1, low=20)


def _model(ladders, fallback=None):
    return ProfilePointsModel(LEVELS, ladders, fallback or _Fallback())


def test_sampling_stays_inside_the_players_own_ladder():
    ladder = [50.0, 80.0, 120.0, 180.0, 240.0, 300.0, 340.0]
    model = _model({"alpha": ladder})
    rng = random.Random(0)

    draws = [model.sample(_player("Alpha"), {}, rng) for _ in range(500)]

    assert min(draws) >= ladder[0]
    assert max(draws) <= ladder[-1]


def test_sampling_reproduces_the_fitted_median():
    ladder = [50.0, 80.0, 120.0, 180.0, 240.0, 300.0, 340.0]
    model = _model({"alpha": ladder})
    rng = random.Random(1)

    draws = sorted(model.sample(_player("Alpha"), {}, rng) for _ in range(4000))

    # The ladder's P50 is 180; the empirical median should land near it.
    assert draws[2000] == pytest.approx(180.0, rel=0.08)


def test_two_players_with_different_ladders_get_different_outcomes():
    """The entire reason this model exists -- the bucket model would have made
    these two statistically identical."""
    steady = [90.0, 100.0, 110.0, 120.0, 130.0, 140.0, 150.0]
    boom = [10.0, 20.0, 60.0, 120.0, 200.0, 260.0, 300.0]
    model = _model({"steady": steady, "boom": boom})
    rng = random.Random(2)

    steady_draws = [model.sample(_player("Steady"), {}, rng) for _ in range(2000)]
    boom_draws = [model.sample(_player("Boom"), {}, rng) for _ in range(2000)]

    # Same rough centre, very different spread.
    assert max(steady_draws) < max(boom_draws)
    assert min(steady_draws) > min(boom_draws)


def test_unknown_player_falls_back_rather_than_scoring_zero():
    fallback = _Fallback(value=77.0)
    model = _model({"alpha": [1.0] * 7}, fallback)

    result = model.sample(_player("Nobody"), {}, random.Random(3))

    assert result == 77.0
    assert fallback.calls == ["Nobody"]


def test_name_matching_is_normalized():
    model = _model({"jamarr chase": [100.0] * 7})

    result = model.sample(_player("Ja'Marr Chase"), {}, random.Random(4))

    assert result == 100.0


def test_coverage_reports_how_many_players_are_genuinely_modelled():
    """Reported so a run cannot be mostly-fallback and still look like a real
    test of per-player signal."""
    model = _model({"a": [1.0] * 7, "b": [2.0] * 7})

    assert model.coverage == 2


def test_empty_model_is_pure_fallback():
    fallback = _Fallback(value=5.0)
    model = _model({}, fallback)

    assert model.sample(_player("Anyone"), {}, random.Random(5)) == 5.0
    assert model.coverage == 0
