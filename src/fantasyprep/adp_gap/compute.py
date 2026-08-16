"""Compute ESPN-vs-sharp ADP gaps, adjusted for typical variance at that draft stage.

v1 approximates "typical ADP variance" with a round-based bucket heuristic
(tighter tolerance early, wider late) rather than real ADP standard
deviation, since that requires many mock-draft samples we don't have yet.
FantasyFootballCalculator's free ADP API publishes real standard deviation
per player and is a candidate to swap in here later.
"""
from __future__ import annotations

from dataclasses import dataclass

from fantasyprep.players.normalize import MatchedPlayer

# (max_adp_in_bucket, tolerance_in_picks)
TOLERANCE_BUCKETS = [
    (24, 3.0),
    (60, 6.0),
    (120, 12.0),
    (float("inf"), 20.0),
]


def tolerance_for_adp(adp: float) -> float:
    for max_adp, tolerance in TOLERANCE_BUCKETS:
        if adp <= max_adp:
            return tolerance
    return TOLERANCE_BUCKETS[-1][1]


@dataclass(frozen=True)
class AdpGap:
    player_name: str
    position: str
    team: str
    espn_adp: float
    sharp_adp: float
    sharp_source: str
    raw_gap: float  # positive = sharp market drafts him earlier than ESPN
    adjusted_score: float  # raw_gap / typical tolerance at this draft stage
    match_confidence: int


def compute_gaps(matches: list[MatchedPlayer]) -> list[AdpGap]:
    gaps = []
    for m in matches:
        raw_gap = m.espn.espn_adp - m.sharp.adp
        avg_adp = (m.espn.espn_adp + m.sharp.adp) / 2
        adjusted = raw_gap / tolerance_for_adp(avg_adp)

        gaps.append(
            AdpGap(
                player_name=m.espn.name,
                position=m.espn.position,
                team=m.espn.team,
                espn_adp=m.espn.espn_adp,
                sharp_adp=m.sharp.adp,
                sharp_source=m.sharp.source,
                raw_gap=raw_gap,
                adjusted_score=adjusted,
                match_confidence=m.match_confidence,
            )
        )

    gaps.sort(key=lambda g: g.adjusted_score, reverse=True)
    return gaps
