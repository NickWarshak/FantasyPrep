"""ADP+stdev-weighted pick sampling: models how a draft opponent (or "your"
own future best-player-available pick) chooses from the remaining pool.

Uses each player's real per-player ADP standard deviation from FFC rather
than a guessed tolerance curve -- a player's pick probability at a given
draft slot is proportional to a Gaussian density centered on their ADP.
"""
from __future__ import annotations

import math
import random

from fantasyprep.historical.sources.ffc import FfcPlayer

MIN_STDEV = 0.5  # floor to avoid zero-variance blowups on near-universal first picks


def pick_weight(player: FfcPlayer, pick_number: int) -> float:
    std = max(player.stdev, MIN_STDEV)
    z = (pick_number - player.adp) / std
    return math.exp(-0.5 * z * z)


# A player who has fallen this many standard deviations past their real ADP is
# treated as "anomalously still available" -- past this point, a pure Gaussian
# keeps decaying toward zero (see below), which is not realistic drafter
# behavior: found and verified live (2026-08-16) that a player who falls this
# far (Christian McCaffrey, 2023 ADP 2.4/stdev 1.0: weight 0.84 at pick 3, ~0.0
# by pick 8) becomes numerically "stuck" undrafted for the rest of a simulated
# continuation, when in reality a real drafter's reaction to "why is a top-3
# player still here in round 1?" is closer to "obviously taking him now," not
# "even less likely than before."
TAIL_Z = 3.0
# How fast the tail hazard climbs back toward near-certain selection per
# additional standard deviation of overshoot past TAIL_Z. Chosen so a player
# roughly 7-8 total stdevs past ADP (a few stdevs past TAIL_Z) is already
# highly likely to be taken, matching "someone grabs the obvious value" -- not
# empirically fit to data (no historical record of what happens after a top
# player falls this far exists to fit against), just a deliberately smooth,
# monotonically-increasing replacement for a decay curve that was actively
# wrong in this regime.
TAIL_RISE_RATE = 0.5


def pick_weight_with_tail_floor(player: FfcPlayer, pick_number: int) -> float:
    """Same Gaussian as `pick_weight` up to TAIL_Z standard deviations past a
    player's ADP -- beyond that, replaces the Gaussian's continued decay with
    a rising hazard that climbs toward 1.0 the further past-ADP a player has
    anomalously fallen, instead of collapsing toward 0. Continuous at the
    TAIL_Z boundary (matches the Gaussian's value there exactly). Only
    changes the *late* tail -- a player who hasn't reached their ADP window
    yet (z <= TAIL_Z, which includes all z < 0) is completely unaffected, so
    "too early" still correctly means "still unlikely."

    Opt-in, not the default `pick_weight` -- see `sample_pick`'s `weight_fn`
    parameter. Existing backtest runs used the plain Gaussian; switching the
    default retroactively would silently change what's being measured
    mid-comparison, so this is available for a controlled A/B, the same
    pattern as `backtest.py`'s `vor_rank_cutoff_mode`."""
    std = max(player.stdev, MIN_STDEV)
    z = (pick_number - player.adp) / std
    if z <= TAIL_Z:
        return math.exp(-0.5 * z * z)

    base = math.exp(-0.5 * TAIL_Z * TAIL_Z)
    overshoot = z - TAIL_Z
    return base + (1.0 - base) * (1.0 - math.exp(-TAIL_RISE_RATE * overshoot))


def sample_pick(
    pool: list[FfcPlayer],
    pick_number: int,
    rng: random.Random | None = None,
    weight_fn=pick_weight,
) -> FfcPlayer:
    """Sample one player from the remaining pool for the given pick number.
    `weight_fn` defaults to the original pure-Gaussian `pick_weight` --
    pass `pick_weight_with_tail_floor` for the tail-floor variant."""
    if not pool:
        raise ValueError("Cannot sample a pick from an empty player pool")

    rng = rng or random
    weights = [weight_fn(p, pick_number) for p in pool]
    if sum(weights) == 0:
        return rng.choice(pool)
    return rng.choices(pool, weights=weights, k=1)[0]
