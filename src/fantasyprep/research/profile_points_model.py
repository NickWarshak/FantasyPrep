"""A `PointsModel` that gives every player his OWN distribution.

WHY THIS EXISTS

`player_choice.py` asked whether simulating each candidate at a position
individually beats just taking the best-ADP player. It was backtested and lost
decisively -- 39% win rate, mean -57.5, a confident negative.

But that test could not have succeeded, and the module's own docstring says so:

    HistoricalBootstrapModel pools outcomes into buckets of 3 consecutive draft
    ranks -- two players in the same bucket are statistically IDENTICAL to it.

So "simulate each candidate individually" was, for most candidate pairs,
simulating the same distribution twice and reporting whichever noise won.
Departing from ADP on a coin flip throws away real market information, which is
exactly the mechanism the negative result identified. ROADMAP.md and the status
page both record it as "worth revisiting only if paired with real per-player
signal."

That signal now exists. `research/distribution_benchmark.py` fits quantile
regression on a player's own preseason profile -- market rank, prior production,
opportunity, age, draft capital -- and beat the incumbent buckets on CRPS
(-3.3% overall, -8.8% on rookies). Two players at the same ADP get genuinely
different distributions from it.

This wraps that into the `PointsModel` protocol so the *unmodified*
player-choice backtest can be re-run against it. The question being tested is
narrow and worth stating precisely: **was player-choice a bad idea, or was it a
good idea starved of per-player signal?**

SAMPLING

The model holds a per-player quantile ladder (P5..P95). Sampling is
inverse-transform: draw u ~ U(0,1) and interpolate the ladder at u. That
reproduces the fitted distribution including its skew, rather than assuming a
shape around a point estimate -- fantasy outcomes are heavily right-skewed and
nothing like Gaussian.

Players with no fitted distribution -- no ADP, missing features, or positions
the profile model never saw -- fall back to the incumbent bucket model rather
than scoring zero or being dropped. A fallback keeps the comparison honest: the
two arms then differ only where real per-player signal actually exists.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np

from fantasyprep.draft_sim.points_model import PointsModel
from fantasyprep.historical.sources.ffc import FfcPlayer
from fantasyprep.players.normalize import normalize_name


@dataclass
class ProfilePointsModel:
    """Per-player quantile ladders, with a bucket-model fallback.

    `quantile_levels` is the ladder's probability grid; `ladders` maps a
    normalized player name to that player's fitted values at those levels.
    """

    quantile_levels: tuple[float, ...]
    ladders: dict[str, list[float]]
    fallback: PointsModel

    def sample(self, player: FfcPlayer, pos_ranks: dict[str, int], rng: random.Random) -> float:
        ladder = self.ladders.get(normalize_name(player.name))
        if ladder is None:
            return self.fallback.sample(player, pos_ranks, rng)
        return float(np.interp(rng.random(), self.quantile_levels, ladder))

    @property
    def coverage(self) -> int:
        """How many players actually have their own distribution -- reported so
        a run cannot silently be mostly-fallback and look like a real test."""
        return len(self.ladders)


def build_profile_points_model(
    year: int,
    fallback: PointsModel,
    frame=None,
) -> ProfilePointsModel:
    """Fit per-player distributions for `year` using only strictly-prior seasons.

    Leakage-safe by the same rule as `backtest.leakage_safe_distributions`:
    nothing from `year` or later is used to fit the model that predicts `year`.
    """
    from fantasyprep.research.benchmark import build_modeling_frame
    from fantasyprep.research.distribution_benchmark import (
        PROFILE_FEATURES,
        QUANTILES,
        predict_model_quantiles,
    )

    if frame is None:
        frame, _ = build_modeling_frame()

    train = frame[(frame["season"] < year) & frame["has_adp"]]
    test = frame[(frame["season"] == year) & frame["has_adp"]]
    if len(train) < 200 or test.empty:
        # Too little history to fit anything trustworthy -- degrade to the
        # incumbent rather than emit a model fitted on a handful of rows.
        return ProfilePointsModel(tuple(QUANTILES), {}, fallback)

    matrix = predict_model_quantiles(train, test, PROFILE_FEATURES)
    ladders = {
        normalize_name(name): [float(v) for v in row]
        for name, row in zip(test["player_name"], matrix)
    }
    return ProfilePointsModel(tuple(QUANTILES), ladders, fallback)
