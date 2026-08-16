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

from fantasyprep.league.settings import LeagueSettings


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
