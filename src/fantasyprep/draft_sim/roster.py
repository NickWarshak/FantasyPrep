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
    """Points from the optimal starting lineup. Bench contributes nothing.

    Rewritten for speed after profiling showed it was 22,750 calls and 61% of a
    single recommendation, driven by `best_marginal_player` evaluating a
    candidate window at every simulated pick. The original sorted whole
    DraftedPlayer objects and then called `list.remove` per assigned starter --
    125,000 O(n) removals per position per run.

    This buckets points by position once and slices, which is the same
    assignment by construction: taking the top N of a position out of a
    globally sorted list is identical to taking the top N of that position's
    own sorted list. Equivalence against the original is pinned by tests.
    """
    by_position: dict[str, list[float]] = {}
    for player in players:
        by_position.setdefault(player.position, []).append(player.points)
    for values in by_position.values():
        values.sort(reverse=True)

    total = 0.0
    leftovers: list[float] = []
    for position, count in settings.roster_slots.items():
        if position == "FLEX":
            continue
        values = by_position.get(position)
        if not values:
            continue
        total += sum(values[:count])
        if position in LeagueSettings.FLEX_ELIGIBLE:
            leftovers.extend(values[count:])

    # A FLEX-eligible position with no fixed slot of its own contributes all of
    # its players to the FLEX pool.
    for position in LeagueSettings.FLEX_ELIGIBLE:
        if position not in settings.roster_slots:
            leftovers.extend(by_position.get(position, ()))

    flex_count = settings.roster_slots.get("FLEX", 0)
    if flex_count and leftovers:
        leftovers.sort(reverse=True)
        total += sum(leftovers[:flex_count])
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


# How many ADP-ranked candidates to evaluate for marginal value at each of my
# simulated future picks. A real drafter is not reaching 30 players past ADP,
# and cost grows linearly with this.
MARGINAL_CANDIDATE_WINDOW = 14


def lineup_context(points_by_position: dict[str, list[float]], settings: LeagueSettings) -> dict:
    """Precompute what a new player would have to beat, per position.

    Built once per pick so the marginal value of each candidate is then O(1)
    instead of a full lineup recomputation. Profiling put the old approach at
    61% of a single recommendation -- 15 lineup evaluations per simulated pick,
    every pick, every simulation.

    Returns, per position, the weakest current starter in that position's fixed
    slots (None when a slot is still open), plus the weakest current FLEX
    starter (None when FLEX has room).
    """
    weakest_fixed: dict[str, float | None] = {}
    flex_pool: list[float] = []

    for position, count in settings.roster_slots.items():
        if position == "FLEX":
            continue
        values = sorted(points_by_position.get(position, ()), reverse=True)
        starters = values[:count]
        weakest_fixed[position] = starters[-1] if len(starters) == count else None
        if position in LeagueSettings.FLEX_ELIGIBLE:
            flex_pool.extend(values[count:])

    for position in LeagueSettings.FLEX_ELIGIBLE:
        if position not in settings.roster_slots:
            flex_pool.extend(points_by_position.get(position, ()))

    flex_count = settings.roster_slots.get("FLEX", 0)
    flex_pool.sort(reverse=True)
    flex_starters = flex_pool[:flex_count]
    weakest_flex = flex_starters[-1] if len(flex_starters) == flex_count and flex_count else None

    return {"weakest_fixed": weakest_fixed, "weakest_flex": weakest_flex}


def marginal_gain(context: dict, position: str, points: float, settings: LeagueSettings) -> float:
    """How much this player would add to the starting lineup, in O(1).

    Case analysis, which is what the full recomputation was doing the slow way:

      * an open fixed slot at his position -> he simply starts, gain = points
      * otherwise, if he beats the weakest starter there, he takes that slot and
        the displaced player falls into the FLEX pool. If the displaced player
        then beats the weakest FLEX starter, the FLEX starter is the one who
        actually leaves the lineup, so the gain is measured against HIM
      * if he cannot crack his own position, he may still crack FLEX directly
      * otherwise he is a bench player and adds nothing
    """
    weakest_fixed = context["weakest_fixed"]
    weakest_flex = context["weakest_flex"]
    flex_eligible = position in LeagueSettings.FLEX_ELIGIBLE
    has_flex = settings.roster_slots.get("FLEX", 0) > 0

    if position not in settings.roster_slots and not flex_eligible:
        return 0.0  # no slot exists for this position at all

    fixed_weakest = weakest_fixed.get(position, None) if position in settings.roster_slots else None
    open_fixed = position in settings.roster_slots and fixed_weakest is None

    if open_fixed:
        return points

    if fixed_weakest is not None and points > fixed_weakest:
        # He takes the fixed slot; the displaced starter competes for FLEX.
        if not (flex_eligible and has_flex):
            return points - fixed_weakest
        if weakest_flex is None:
            return points  # displaced player drops into an open FLEX slot
        if fixed_weakest > weakest_flex:
            return points - weakest_flex
        return points - fixed_weakest

    # Cannot beat his own position's starters -- try FLEX directly.
    if flex_eligible and has_flex:
        if weakest_flex is None:
            return points
        if points > weakest_flex:
            return points - weakest_flex
    return 0.0


def best_marginal_player(candidates, my_team, settings, expected_points, fallback):
    """The candidate who most increases my STARTING-LINEUP value.

    This is what makes positional scarcity fall out of the simulation instead of
    having to be bolted on beside it.

    Selecting my simulated future picks by ADP -- even ADP-among-needs -- cannot
    express a cliff. On a real board with Jahmyr Gibbs (ADP 2.0) still available
    at pick 89, Gibbs projects 243.4 while the next RB projects 146.5, a 96.9
    point drop, whereas Justin Herbert projects 291.5 against the next QB's
    299.0 -- an actively NEGATIVE drop. Ranking by ADP treats those two
    situations the same. Ranking by marginal lineup value does not.

    Because the branch that skips Gibbs is then forced to fill that slot with
    whoever is genuinely left, the opportunity cost shows up in the branch
    comparison on its own -- no separate drop-off term to weigh by hand.

    `expected_points` maps a player to his expected points, deterministically
    (the bucket mean), so this adds no RNG and no re-sampling.
    """
    if not candidates:
        return fallback
    window = sorted(candidates, key=lambda p: p.adp)[:MARGINAL_CANDIDATE_WINDOW]

    points_by_position: dict[str, list[float]] = {}
    for player in my_team:
        points_by_position.setdefault(player.position, []).append(expected_points(player))
    context = lineup_context(points_by_position, settings)

    best, best_gain = fallback, None
    for candidate in window:
        gain = marginal_gain(context, candidate.position, expected_points(candidate), settings)
        if best_gain is None or gain > best_gain:
            best, best_gain = candidate, gain
    return best
