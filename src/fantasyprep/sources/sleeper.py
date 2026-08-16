"""Fetch Sleeper's player rank data via their official, documented public API.

`/v1/players/nfl` doesn't have a field literally named "adp", but its
`search_rank` lines up with real Sleeper mock-draft picks for skill
positions (spot-checked: Ja'Marr Chase search_rank 3 / Tee Higgins
search_rank 34 matched a live Sleeper mock draft), so it's treated here as
an ADP-equivalent rank -- with one confirmed exception: QB. search_rank has
Josh Allen at 4 overall; real single-QB Sleeper ADP has him around 20.
search_rank looks like it tracks player value/production rather than
market draft cost, and QB is exactly where those two diverge in single-QB
leagues (plenty of replacement-level starters, so real drafters wait on
even high-value passers). Callers in single-QB formats should exclude QB
(see report.py's --exclude-positions flag) -- this hasn't been verified
position-by-position beyond WR/QB, so treat it as directionally useful
rather than precise ADP.

Unlike the ESPN and Underdog sources, this endpoint is fully documented
and requires no auth -- but per Sleeper's docs it's a ~5-11MB payload
intended to be fetched at most once a day, so results are cached to disk
like the ESPN source.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

from fantasyprep.sources.manual_adp import SharpAdpEntry

BASE_URL = "https://api.sleeper.app/v1/players/nfl"

FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K"}


def fetch_sleeper_players(
    cache_path: Path | None = None,
    force_refresh: bool = False,
    timeout: int = 30,
) -> list[SharpAdpEntry]:
    """Fetch Sleeper's active player pool, normalized to SharpAdpEntry records."""
    raw = _load_cached(cache_path) if cache_path and not force_refresh else None
    if raw is None:
        resp = requests.get(BASE_URL, params={"active": "true"}, timeout=timeout)
        resp.raise_for_status()
        raw = resp.json()
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(raw), encoding="utf-8")

    return _normalize(raw)


def _load_cached(cache_path: Path) -> dict | None:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    return None


def _normalize(raw: dict) -> list[SharpAdpEntry]:
    entries = []
    for p in raw.values():
        position = p.get("position")
        if position not in FANTASY_POSITIONS:
            continue

        rank = p.get("search_rank")
        if rank is None:
            continue  # unranked -- no real draft signal

        full_name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        if not full_name:
            continue

        entries.append(
            SharpAdpEntry(
                player_name=full_name,
                team=(p.get("team") or "FA").upper(),
                position=position,
                adp=float(rank),
                source="sleeper",
            )
        )

    entries.sort(key=lambda e: e.adp)
    return entries
