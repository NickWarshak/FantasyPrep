"""Fetch ADP (current or historical) from FantasyFootballCalculator's free public API.

Free for personal/commercial use, no auth, historical by year (spot
checked back to 2018), and gives real per-player draft variance (stdev,
high, low) rather than a guessed tolerance -- used here for both live
opponent modeling and historical outcome calibration, same endpoint,
different `year`.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import requests

from fantasyprep.league.settings import LeagueSettings

BASE_URL = "https://fantasyfootballcalculator.com/api/v1/adp/{scoring}"

# FFC's own position codes differ from ours for two positions -- DEF for
# team defense, PK for kicker. Everything else matches. Getting this wrong
# silently drops the position entirely (see _normalize): DST/K were dropped
# from every fetch this way for the whole project until this map was added.
FFC_POSITION_MAP = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "PK": "K", "DEF": "DST"}


@dataclass(frozen=True)
class FfcPlayer:
    name: str
    position: str
    team: str
    adp: float
    stdev: float
    high: int
    low: int


def fetch_adp(
    year: int,
    teams: int,
    scoring: str = "ppr",
    cache_path: Path | None = None,
    force_refresh: bool = False,
    timeout: int = 30,
) -> list[FfcPlayer]:
    """Fetch one season's ADP for a given team count / scoring format."""
    raw = _load_cached(cache_path) if cache_path and not force_refresh else None
    if raw is None:
        resp = requests.get(
            BASE_URL.format(scoring=scoring),
            params={"teams": teams, "year": year},
            timeout=timeout,
        )
        resp.raise_for_status()
        raw = resp.json()
        if raw.get("status") != "Success":
            raise ValueError(f"FFC API returned non-success status for year={year}: {raw}")
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(raw), encoding="utf-8")

    return _normalize(raw)


def _load_cached(cache_path: Path) -> dict | None:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    return None


def _normalize(raw: dict) -> list[FfcPlayer]:
    players = []
    for p in raw.get("players", []):
        position = FFC_POSITION_MAP.get(p.get("position"))
        if position is None:
            continue
        players.append(
            FfcPlayer(
                name=p["name"],
                position=position,
                team=p.get("team", "FA"),
                adp=float(p["adp"]),
                stdev=float(p.get("stdev") or 0.0),
                high=int(p.get("high") or p["adp"]),
                low=int(p.get("low") or p["adp"]),
            )
        )
    players.sort(key=lambda pl: pl.adp)
    return players


def position_ranks(players: list[FfcPlayer]) -> dict[str, int]:
    """Map player name -> rank within their position by ADP (1 = earliest at that position)."""
    by_position: dict[str, list[FfcPlayer]] = {}
    for p in players:
        by_position.setdefault(p.position, []).append(p)

    ranks: dict[str, int] = {}
    for position, plist in by_position.items():
        plist.sort(key=lambda pl: pl.adp)
        for i, p in enumerate(plist, start=1):
            ranks[p.name] = i
    return ranks


def derive_rank_cutoff(players: list[FfcPlayer], settings: LeagueSettings) -> dict[str, int]:
    """Real replacement rank per position, derived from actual draft
    behavior instead of a guessed formula. A hardcoded split (e.g. "30
    RB, 30 WR") implicitly assumes a fixed division of FLEX slots and a
    fixed bench-stash rate per position -- neither is true in practice
    (RB/WR bench-stashing is heavy and FLEX competition shifts with
    relative depth each year; verified against real FFC ADP: actual
    draft depth for RB/WR runs 50-60+ in a 10-team league, not 30).

    Real ADP already encodes what actually happens: FLEX competition,
    position-specific bench-stash rates, and their interaction, because
    real drafters made those tradeoffs. So the replacement rank for a
    position is just "one past the last player of that position who
    realistically gets drafted" -- the player at ADP rank `total_picks`
    (rosters * teams) is the last one someone owns; `total_picks + 1` is
    the first true free agent. Counting by position within the real
    top-`total_picks` ADP set gives that boundary directly, no guessing
    which position each FLEX/bench slot "belongs" to.
    """
    total_picks = (sum(settings.roster_slots.values()) + settings.bench) * settings.teams
    drafted = sorted(players, key=lambda p: p.adp)[:total_picks]
    counts = Counter(p.position for p in drafted)
    return {position: count + 1 for position, count in counts.items()}
