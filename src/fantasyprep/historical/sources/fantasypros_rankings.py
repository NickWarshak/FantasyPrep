"""FantasyPros overall consensus rankings (ECR) as an alternative live draft
pool source to FFC ADP -- a CSV export, not a fetchable API, so this reads
a file on disk rather than making a network call.

Real vs. approximated, to be explicit about what this source actually gives
us: `adp` (mapped directly from FantasyPros' overall RK column) is real --
it's their actual expert consensus rank. `stdev`/`high`/`low` are NOT real
here the way they are for FFC (which aggregates real draft-position variance
across live drafts) -- this export has no per-player variance data, only a
TIERS column (experts grouping players they consider roughly interchangeable
in value). We approximate stdev/high/low from each player's tier width: a
narrow tier (experts agree closely) gets a tight stdev, a wide tier gets a
loose one. This is a real, documented approximation, not a measurement --
opponent-model realism for this source is weaker than FFC's until/unless a
source with real draft-position variance is used instead.
"""
from __future__ import annotations

import csv
import re
import statistics
from collections import defaultdict
from pathlib import Path

from fantasyprep.historical.sources.ffc import FfcPlayer

# Positions appear as e.g. "WR1", "RB23", "DST4", "K1" -- strip the trailing
# rank-within-position digits to get the bare position code.
_POS_RE = re.compile(r"^([A-Z]+)\d+$")

MIN_STDEV = 0.5  # same floor as opponent.py's MIN_STDEV, for consistency


def _parse_position(raw: str) -> str | None:
    match = _POS_RE.match(raw.strip())
    return match.group(1) if match else None


def load_fantasypros_rankings(csv_path: Path) -> list[FfcPlayer]:
    """Parse a FantasyPros "Draft ALL Rankings" CSV export into the same
    FfcPlayer shape used everywhere else, so it's a drop-in alternative
    pool source (backtest.py, webapp/app.py, etc. don't need to change
    how they consume a player list, only where it comes from)."""
    rows = []
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rk = (row.get("RK") or "").strip()
            name = (row.get("PLAYER NAME") or "").strip()
            if not rk or not name:
                continue  # tier-break separator rows (blank RK) or malformed lines
            position = _parse_position(row.get("POS") or "")
            if position is None:
                continue
            tier_raw = (row.get("TIERS") or "").strip()
            rows.append({
                "rank": float(rk),
                "name": name,
                "team": (row.get("TEAM") or "").strip() or "FA",
                "position": position,
                "tier": int(tier_raw) if tier_raw else None,
            })

    # Tier width -> synthetic stdev/high/low, per position (a QB tier and a
    # kicker tier spanning the same rank-width don't mean the same thing;
    # keeping this per-position matches how real ADP stdev tends to behave
    # too -- tighter at the top of a position, looser deeper down).
    ranks_by_tier: dict[tuple[str, int], list[float]] = defaultdict(list)
    for r in rows:
        if r["tier"] is not None:
            ranks_by_tier[(r["position"], r["tier"])].append(r["rank"])

    players = []
    for r in rows:
        if r["tier"] is not None and len(ranks_by_tier[(r["position"], r["tier"])]) > 1:
            tier_ranks = ranks_by_tier[(r["position"], r["tier"])]
            tier_low, tier_high = min(tier_ranks), max(tier_ranks)
            stdev = max(MIN_STDEV, (tier_high - tier_low) / 4)
            high, low = int(tier_low), int(tier_high)
        else:
            stdev = MIN_STDEV
            high = low = int(r["rank"])

        players.append(FfcPlayer(
            name=r["name"], position=r["position"], team=r["team"],
            adp=r["rank"], stdev=stdev, high=high, low=low,
        ))

    players.sort(key=lambda p: p.adp)
    return players
