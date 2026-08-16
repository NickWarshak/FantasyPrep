"""ADP+stdev-weighted pick sampling: models how a draft opponent (or "your"
own future best-player-available pick) chooses from the remaining pool.

Uses each player's real per-player ADP standard deviation from FFC rather
than a guessed tolerance curve -- a player's pick probability at a given
draft slot is proportional to a Gaussian density centered on their ADP.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np

from fantasyprep.historical.sources.ffc import FfcPlayer

MIN_STDEV = 0.5  # floor to avoid zero-variance blowups on near-universal first picks


@dataclass
class _IndexedPlayerView:
    """Minimal `.adp`/`.stdev` shim so `sample_pick_index`'s fallback path
    (an unregistered custom weight_fn) can still call a scalar
    `weight_fn(player, pick_number)` without needing a full FfcPlayer."""

    adp: float
    stdev: float


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


def _vectorized_pick_weight(pick_number: int, adp: np.ndarray, stdev: np.ndarray) -> np.ndarray:
    """Array equivalent of `pick_weight` -- same formula, computed for
    every player in one numpy call instead of one Python call per player.
    Kept in lockstep with `pick_weight` by
    `test_opponent.py::test_vectorized_pick_weight_matches_scalar_*`."""
    std = np.maximum(stdev, MIN_STDEV)
    z = (pick_number - adp) / std
    return np.exp(-0.5 * z * z)


def _vectorized_pick_weight_with_tail_floor(pick_number: int, adp: np.ndarray, stdev: np.ndarray) -> np.ndarray:
    """Array equivalent of `pick_weight_with_tail_floor`. See that
    function's docstring for the two-regime formula this mirrors."""
    std = np.maximum(stdev, MIN_STDEV)
    z = (pick_number - adp) / std
    gaussian = np.exp(-0.5 * z * z)
    base = math.exp(-0.5 * TAIL_Z * TAIL_Z)
    overshoot = z - TAIL_Z
    tail = base + (1.0 - base) * (1.0 - np.exp(-TAIL_RISE_RATE * overshoot))
    return np.where(z <= TAIL_Z, gaussian, tail)


# Maps each known scalar weight function to its vectorized equivalent --
# sample_pick uses this to compute weights for the whole pool in one numpy
# call instead of one Python function call per player, which profiling
# (2026-08-16) showed was ~90-97% of total runtime in both live
# recommendations and Draft Now vs. Wait validation. A weight_fn not in
# this registry (e.g. a test double) falls back to the original
# per-player Python loop, so custom weight functions still work correctly,
# just without the speedup.
_VECTORIZED_WEIGHT_FNS = {
    pick_weight: _vectorized_pick_weight,
    pick_weight_with_tail_floor: _vectorized_pick_weight_with_tail_floor,
}


def sample_pick(
    pool: list[FfcPlayer],
    pick_number: int,
    rng: random.Random | None = None,
    weight_fn=pick_weight,
) -> FfcPlayer:
    """Sample one player from the remaining pool for the given pick number.
    `weight_fn` defaults to the original pure-Gaussian `pick_weight` --
    pass `pick_weight_with_tail_floor` for the tail-floor variant.

    Draws with exactly one `rng.random()` call against a cumulative-weight
    array (`u = rng.random() * total; searchsorted(cumulative, u,
    side="right")`) -- the same mechanism CPython's own
    `random.choices(..., k=1)` uses internally (a single `random()` call
    into `bisect.bisect_right`), so this consumes the shared `rng` stream
    identically to the original implementation and every existing seeded
    call site stays reproducible. Weight *values* are not guaranteed
    bit-for-bit identical to the old pure-Python computation (numpy's
    vectorized `exp`/summation can round differently) -- same
    distribution, not byte-identical floats; see
    `test_opponent.py`'s equivalence tests, not manual inspection, for
    the actual guarantee."""
    if not pool:
        raise ValueError("Cannot sample a pick from an empty player pool")

    rng = rng or random
    vectorized = _VECTORIZED_WEIGHT_FNS.get(weight_fn)
    if vectorized is not None:
        adp = np.fromiter((p.adp for p in pool), dtype=np.float64, count=len(pool))
        stdev = np.fromiter((p.stdev for p in pool), dtype=np.float64, count=len(pool))
        weights = vectorized(pick_number, adp, stdev)
    else:
        weights = np.array([weight_fn(p, pick_number) for p in pool], dtype=np.float64)

    total = weights.sum()
    if total == 0:
        return rng.choice(pool)
    cumulative = np.cumsum(weights)
    u = rng.random() * total
    idx = int(np.searchsorted(cumulative, u, side="right"))
    idx = min(idx, len(pool) - 1)  # guard the float-boundary case where u lands at/past the last cumulative value
    return pool[idx]


def sample_pick_index(
    adp: np.ndarray,
    stdev: np.ndarray,
    available: np.ndarray,
    pick_number: int,
    rng: random.Random,
    weight_fn=pick_weight,
) -> int:
    """Same sampling as `sample_pick`, but for a caller that already holds
    `adp`/`stdev` as fixed arrays (built once, e.g. per simulated draft)
    plus a mutable `available` boolean mask, instead of a shrinking Python
    list rebuilt into numpy arrays on every single pick.

    Profiling (2026-08-16) found that even after vectorizing the weight
    math, `sample_pick`'s own `np.fromiter` reconstruction -- called once
    per simulated pick, tens of thousands of times per live recommendation
    -- was ~78% of remaining runtime. This function exists so a caller
    that owns the pick loop (`simulate_position_choice`) can build the
    arrays exactly once and reuse them for the whole simulated draft,
    which `sample_pick` itself can't do since it only ever sees one pick's
    worth of a shrinking `pool` list at a time.

    Returns the chosen index into `adp`/`stdev` (not a player) -- the
    caller is responsible for mapping it back to a player object and
    setting `available[idx] = False`. Unlike `sample_pick`, this does NOT
    consume the RNG identically to `random.choices` when the zero-weight
    fallback path is hit (falls back to `rng.choice` over available
    indices, same as `sample_pick`'s own zero-weight fallback, but as an
    index rather than a pool slice)."""
    vectorized = _VECTORIZED_WEIGHT_FNS.get(weight_fn)
    if vectorized is not None:
        weights = vectorized(pick_number, adp, stdev)
    else:
        # Unknown weight_fn: no array-level equivalent to call, so this
        # path can't be vectorized -- correctness over speed, matching
        # sample_pick's own fallback.
        weights = np.array(
            [weight_fn(_IndexedPlayerView(a, s), pick_number) for a, s in zip(adp, stdev)], dtype=np.float64,
        )
    weights = np.where(available, weights, 0.0)

    total = weights.sum()
    if total == 0:
        candidates = np.flatnonzero(available)
        return int(rng.choice(candidates.tolist()))
    cumulative = np.cumsum(weights)
    u = rng.random() * total
    idx = int(np.searchsorted(cumulative, u, side="right"))
    # Guard the float-boundary case, and skip any (should-be-impossible,
    # since unavailable weights are zeroed) unavailable index a boundary
    # rounding error could land on.
    idx = min(idx, len(adp) - 1)
    if not available[idx]:
        idx = int(np.flatnonzero(available)[0])
    return idx
