"""Auto-fill opponent picks in a live draft session, based on ESPN ADP.

Reuses the same ADP-weighted `sample_pick` the internal Monte Carlo
simulator uses for opponent modeling (draft_sim/opponent.py), just fed
ESPN's ADP instead of FFC's. ESPN doesn't publish a per-player standard
deviation the way FFC does, so a synthetic one is built from the ADP gap
tool's round-based tolerance heuristic (adp_gap/compute.py), scaled by a
user-tunable `randomness` multiplier: 0 is effectively chalk (always the
closest-to-ADP player), 1.0 is the heuristic's own baseline spread,
higher values wander further from ADP order.
"""
from __future__ import annotations

from fantasyprep.adp_gap.compute import tolerance_for_adp
from fantasyprep.historical.sources.ffc import FfcPlayer
from fantasyprep.sources.espn import EspnPlayer

AUTO_PICK_POSITIONS = {"QB", "RB", "WR", "TE", "DST"}
MIN_STDEV = 0.5
CHALK_STDEV = 0.05  # near-zero variance for randomness=0 -- effectively deterministic ADP order


def espn_pool_for_auto_pick(espn_players: list[EspnPlayer], randomness: float) -> list[FfcPlayer]:
    """Wrap ESPN players as FfcPlayer-shaped objects with a synthetic stdev,
    so the existing opponent.sample_pick can be reused unchanged."""
    pool = []
    for p in espn_players:
        if p.position not in AUTO_PICK_POSITIONS:
            continue
        if randomness <= 0:
            stdev = CHALK_STDEV
        else:
            stdev = max(tolerance_for_adp(p.espn_adp) * randomness, MIN_STDEV)
        pool.append(
            FfcPlayer(name=p.name, position=p.position, team=p.team, adp=p.espn_adp, stdev=stdev, high=0, low=0)
        )
    return pool
