"""Optimal starting-lineup value for a drafted roster.

Bench points don't count -- a team's value is what actually starts, not
raw roster point totals. Assignment is greedy (best player to each
required slot, then best remaining FLEX-eligible player to FLEX), which
is optimal for this simple slot structure: no player is eligible for more
than one non-FLEX position, so there's no assignment ambiguity to
optimize away.
"""
from __future__ import annotations

from dataclasses import dataclass

from collections import Counter

from fantasyprep.league.settings import LeagueSettings

# The positions the draft engine ever proposes. Lives here rather than in
# simulate.py because `positions_of_need` needs it and roster.py must stay
# import-free of simulate.py -- simulate imports roster, not the other way.
CANDIDATE_POSITIONS = ("QB", "RB", "WR", "TE")


@dataclass(frozen=True)
class DraftedPlayer:
    name: str
    position: str
    points: float


def starting_lineup_value(players: list[DraftedPlayer], settings: LeagueSettings) -> float:
    remaining = sorted(players, key=lambda p: p.points, reverse=True)
    total = 0.0

    for position, count in settings.roster_slots.items():
        if position == "FLEX":
            continue
        eligible = [p for p in remaining if p.position == position]
        taken = eligible[:count]
        total += sum(p.points for p in taken)
        for p in taken:
            remaining.remove(p)

    flex_count = settings.roster_slots.get("FLEX", 0)
    if flex_count:
        flex_eligible = [p for p in remaining if p.position in LeagueSettings.FLEX_ELIGIBLE]
        taken = flex_eligible[:flex_count]
        total += sum(p.points for p in taken)

    return total


def starting_lineup_value_by_position(players: list[DraftedPlayer], settings: LeagueSettings) -> dict[str, float]:
    """Same greedy assignment as `starting_lineup_value`, but returns the
    contribution broken out per position instead of one total -- a FLEX
    starter is attributed to their own real position (RB/WR/TE), not a
    separate 'FLEX' bucket, since "where does the edge come from" is a
    more useful question answered per real position. Values sum to
    exactly what `starting_lineup_value` returns for the same roster --
    this is a breakdown of the same computation, not a different one."""
    remaining = sorted(players, key=lambda p: p.points, reverse=True)
    by_position: dict[str, float] = {}

    for position, count in settings.roster_slots.items():
        if position == "FLEX":
            continue
        eligible = [p for p in remaining if p.position == position]
        taken = eligible[:count]
        by_position[position] = by_position.get(position, 0.0) + sum(p.points for p in taken)
        for p in taken:
            remaining.remove(p)

    flex_count = settings.roster_slots.get("FLEX", 0)
    if flex_count:
        flex_eligible = [p for p in remaining if p.position in LeagueSettings.FLEX_ELIGIBLE]
        taken = flex_eligible[:flex_count]
        for p in taken:
            by_position[p.position] = by_position.get(p.position, 0.0) + p.points

    return by_position


def positions_of_need(drafted_positions: list[str], settings: LeagueSettings) -> set[str]:
    """Which positions a roster still needs, given what's drafted so far.

    Fixed slots first, then FLEX-eligible overflow (RB/WR/TE beyond their
    own fixed count still count against FLEX), then -- once every starting
    slot including FLEX is filled -- "any skill position" for open bench
    spots. Empty once the whole roster (starting + bench) is full.
    """
    counts = Counter(drafted_positions)
    needed: set[str] = set()

    for position, required in settings.roster_slots.items():
        if position == "FLEX":
            continue
        if counts[position] < required:
            needed.add(position)

    flex_required = settings.roster_slots.get("FLEX", 0)
    if flex_required:
        flex_surplus = sum(
            max(0, counts[pos] - settings.roster_slots.get(pos, 0)) for pos in LeagueSettings.FLEX_ELIGIBLE
        )
        if flex_surplus < flex_required:
            needed.update(LeagueSettings.FLEX_ELIGIBLE)

    if not needed:
        total_roster = sum(settings.roster_slots.values()) + settings.bench
        if len(drafted_positions) < total_roster:
            needed.update(CANDIDATE_POSITIONS)

    return needed
