"""Pluggable points models for the draft simulator.

Every simulated player needs a "how many points will they score" answer.
`HistoricalBootstrapModel` (the original v1 approach) draws a random real
historical outcome from players who went at a similar draft rank --
captures real variance/bust risk, but is deliberately generic per rank
tier rather than player-specific. `EspnProjectionModel` uses ESPN's own
named-player season projection instead -- more specific, but a single
number per player (no simulated variance on it), with a historical-model
fallback for anyone ESPN doesn't have a projection for.
"""
from __future__ import annotations

import random
from typing import Protocol

from fantasyprep.historical.outcomes import outcome_for_rank
from fantasyprep.historical.sources.ffc import FfcPlayer
from fantasyprep.players.normalize import normalize_name


class PointsModel(Protocol):
    def sample(self, player: FfcPlayer, pos_ranks: dict[str, int], rng: random.Random) -> float: ...


class HistoricalBootstrapModel:
    def __init__(self, distributions):
        self.distributions = distributions

    def sample(self, player: FfcPlayer, pos_ranks: dict[str, int], rng: random.Random) -> float:
        rank = pos_ranks.get(player.name, 999)
        try:
            dist = outcome_for_rank(self.distributions, player.position, rank)
        except KeyError:
            # Positions with no real historical scoring data anywhere in this
            # codebase (DST -- nfl_stats.py's POSITION_MAP doesn't include it)
            # score 0 instead of crashing a simulation that happens to sample
            # one into a hypothetical future pick.
            return 0.0
        return rng.choice(dist.outcomes)


class EspnProjectionModel:
    """ESPN's own named-player season projection, falling back to a
    historical bootstrap for anyone ESPN doesn't have a projection for
    (name mismatches, deep bench players ESPN doesn't project, etc)."""

    def __init__(self, espn_points: dict[str, float], fallback: PointsModel):
        self.espn_points = {normalize_name(name): pts for name, pts in espn_points.items()}
        self.fallback = fallback

    def sample(self, player: FfcPlayer, pos_ranks: dict[str, int], rng: random.Random) -> float:
        points = self.espn_points.get(normalize_name(player.name))
        if points is not None:
            return points
        return self.fallback.sample(player, pos_ranks, rng)
