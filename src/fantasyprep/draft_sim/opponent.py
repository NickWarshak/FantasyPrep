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


def _draw_index(weights: np.ndarray, available: np.ndarray, rng: random.Random) -> int:
    """Shared weighted-draw core: given a full-size weights array and a
    same-length availability mask, zero the unavailable entries and draw
    one index with a single `rng.random()` call against a cumulative-weight
    array (`searchsorted(..., side="right")`) -- the same mechanism
    CPython's own `random.choices(k=1)` uses internally, so every existing
    seeded call site stays reproducible. Shared by `sample_pick_index`
    (computes weights fresh every call) and `OpponentSampler.sample`
    (looks up a precomputed weight row) -- the draw mechanics are
    identical either way, only where the weights come from differs.

    A size-based scalar/numpy dispatch was tried and reverted here
    (2026-08-16): `weights` is always the FULL pool size (deliberately
    never shrunk -- masking with zeros avoids the cost of boolean-slicing
    a new array every draw), so its length never reflects the shrinking
    `available` count within a sim. Real player pools in this project are
    always well above any sane scalar-beats-numpy threshold, so a
    length-based dispatch was structurally unreachable dead code that
    only added per-call overhead -- confirmed by profiling
    (`_draw_index_scalar` never appeared in a real profile) and by a
    direct before/after benchmark on the validation path (measurably
    slower with the dispatch than without: 74.86s vs. 60.64s for the same
    3-replay comparison). Making the underlying idea work for real would
    need dispatching on `available.sum()` instead, which conflicts with
    keeping the array un-shrunk -- a bigger redesign, not attempted
    without a concrete workload that would actually benefit from it."""
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
    idx = min(idx, len(weights) - 1)
    if not available[idx]:
        idx = int(np.flatnonzero(available)[0])
    return idx


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

    Still recomputes the weight vector fresh on every call, though --
    prefer `OpponentSampler` when the same (pool, pick_number) weights
    would otherwise be recomputed across many simulations, since the
    weight for a given player at a given pick number never depends on
    simulation history (see `OpponentSampler`'s docstring). Kept as its
    own function because it's still the right tool for a single one-off
    draw (e.g. `survival_probability`'s use, which doesn't repeat pick
    numbers across sims the way a full roster rollout does).

    Returns the chosen index into `adp`/`stdev` (not a player) -- the
    caller is responsible for mapping it back to a player object and
    setting `available[idx] = False`."""
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
    return _draw_index(weights, available, rng)


class OpponentSampler:
    """Precomputes opponent pick-selection weights for every (pick_number,
    player) pair once, instead of recomputing the same weight vector on
    every one of `num_sims` simulations.

    A player's selection weight at a given pick number depends only on
    their fixed `adp`/`stdev` and that pick number -- never on simulation
    history or which other players happen to be drafted in a given sim
    (availability only decides whether a player's already-known weight
    counts). Profiling (2026-08-16) found the weight computation was
    still the single largest remaining cost even after per-pick
    vectorization (`_vectorized_pick_weight_with_tail_floor` alone was
    ~50% of a live recommendation's runtime) -- almost entirely wasted
    work, since across `num_sims` simulations the exact same weight
    vector gets recomputed once per simulation for every pick number that
    recurs (which is every pick number, since the candidate pool and
    pick range are fixed for the whole call).

    Builds the full `(pick_numbers x players)` weight matrix in one
    broadcasted numpy call up front -- for a full draft this is at most a
    few hundred pick numbers x a few hundred players, a few hundred
    thousand floats, negligible memory -- then each `sample()` call is
    just an array lookup plus the same draw mechanics `sample_pick_index`
    uses (see `_draw_index`)."""

    def __init__(self, players: list[FfcPlayer], pick_numbers, weight_fn=pick_weight):
        self.players = list(players)
        adp = np.fromiter((p.adp for p in self.players), dtype=np.float64, count=len(self.players))
        stdev = np.fromiter((p.stdev for p in self.players), dtype=np.float64, count=len(self.players))
        unique_picks = np.array(sorted(set(int(p) for p in pick_numbers)), dtype=np.int64)

        vectorized = _VECTORIZED_WEIGHT_FNS.get(weight_fn)
        if vectorized is not None:
            # Broadcast pick_numbers as a column against adp/stdev as rows
            # -- one call computes weights for every (pick, player) pair
            # at once, instead of one call per pick number.
            self._weights = vectorized(unique_picks[:, None], adp[None, :], stdev[None, :])
        else:
            self._weights = np.array(
                [
                    [weight_fn(_IndexedPlayerView(a, s), int(pn)) for a, s in zip(adp, stdev)]
                    for pn in unique_picks
                ],
                dtype=np.float64,
            )
        self._row_for_pick = {int(pn): i for i, pn in enumerate(unique_picks)}

    def sample(self, pick_number: int, available: np.ndarray, rng: random.Random) -> int:
        row = self._row_for_pick[pick_number]
        return _draw_index(self._weights[row], available, rng)
