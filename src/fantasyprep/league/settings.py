"""League configuration: team count, scoring rules, and starting lineup slots.

Nothing downstream should hardcode PPR or a specific roster shape -- it
should all flow from a LeagueSettings instance.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScoringSettings:
    pass_yard: float = 0.04  # 1 pt / 25 yards
    pass_td: float = 4.0
    interception: float = -2.0
    rush_yard: float = 0.1  # 1 pt / 10 yards
    rush_td: float = 6.0
    reception: float = 1.0  # full PPR
    rec_yard: float = 0.1
    rec_td: float = 6.0
    fumble_lost: float = -2.0
    special_teams_td: float = 6.0  # kick/punt return TDs -- standard convention
    two_pt_conversion: float = 2.0  # any of passing/rushing/receiving


@dataclass(frozen=True)
class LeagueSettings:
    teams: int
    scoring: ScoringSettings
    roster_slots: dict[str, int]  # e.g. {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}
    bench: int = 6

    FLEX_ELIGIBLE = ("RB", "WR", "TE")


def default_settings() -> LeagueSettings:
    """10-team full PPR -- confirmed league format as of 2026-08-15:
    1 QB, 2 RB, 2 WR, 1 TE, 2 FLEX, 6 bench, no kicker, 1 DST."""
    return LeagueSettings(
        teams=10,
        scoring=ScoringSettings(),
        roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 2, "DST": 1},
        bench=6,
    )
