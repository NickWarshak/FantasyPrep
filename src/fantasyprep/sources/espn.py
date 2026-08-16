"""Fetch ESPN's public fantasy football player pool (rankings + ADP).

Uses ESPN's undocumented but widely-relied-on `kona_player_info` endpoint
(the same one libraries like `espn_api` use). No login/cookies needed for
the default player universe. Endpoint shape isn't officially published by
ESPN and could change without notice.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import requests

from fantasyprep.league.settings import ScoringSettings

BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{year}/players"

# ESPN's defaultPositionId -> position abbreviation. Stable, widely documented.
POSITION_MAP = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "DST",
}

# ESPN's proTeamId -> NFL team abbreviation. Stable, widely documented.
TEAM_MAP = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL",
    7: "DEN", 8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV",
    14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
    21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB",
    28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}

FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DST"}


@dataclass(frozen=True)
class EspnPlayer:
    espn_id: int
    name: str
    position: str
    team: str
    espn_adp: float
    espn_expert_rank: int | None


def _request_filter() -> str:
    return json.dumps(
        {
            "players": {
                "filterActive": {"value": True},
                "sortDraftRanks": {
                    "sortPriority": 100,
                    "sortAsc": True,
                    "value": "STANDARD",
                },
            }
        }
    )


def _fetch_raw(year: int, cache_path: Path | None, force_refresh: bool, timeout: int) -> list[dict]:
    raw = _load_cached(cache_path) if cache_path and not force_refresh else None
    if raw is None:
        resp = requests.get(
            BASE_URL.format(year=year),
            params={"view": "kona_player_info"},
            headers={
                "X-Fantasy-Filter": _request_filter(),
                "x-fantasy-source": "kona",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        raw = resp.json()
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(raw), encoding="utf-8")
    return raw


def fetch_espn_players(
    year: int,
    cache_path: Path | None = None,
    force_refresh: bool = False,
    timeout: int = 30,
) -> list[EspnPlayer]:
    """Fetch and normalize ESPN's fantasy player pool for a season.

    The raw endpoint returns the full player universe (tens of MB, no
    server-side limit/sort applied despite the filter header), so results
    are cached to disk by default and only refetched on request.
    """
    raw = _fetch_raw(year, cache_path, force_refresh, timeout)
    return _normalize(raw)


# ESPN's numeric stat IDs (from the widely-used espn_api library's
# constant.py, cross-checked against a live payload). Keys in the raw
# `stats` dict are strings (parsed JSON), values are the stat's amount.
STAT_ID = {
    "pass_yard": "3",
    "pass_td": "4",
    "interception": "20",
    "rush_yard": "24",
    "rush_td": "25",
    # Receptions is unpopulated under id 41 in the season-total aggregate
    # block specifically (confirmed against a live payload -- 41 is None,
    # 53 has the real number there; both are documented as "receptions" in
    # espn_api's constant.py with no explanation for the split). Try both.
    "reception": ("41", "53"),
    "rec_yard": "42",
    "rec_td": "43",
    "fumble_lost": "72",
}

PROJECTION_POSITIONS = {"QB", "RB", "WR", "TE"}  # ScoringSettings doesn't model K/DST scoring


def _season_projection_raw_stats(player: dict) -> dict | None:
    """The one entry representing full-season projected stats.

    ESPN's convention (confirmed against a live payload): scoringPeriodId 0
    = season aggregate, statSourceId 1 = projected (0 = actual), statSplitTypeId
    0 = total (not a single week). Duplicates sometimes appear (repeated
    snapshots); any one of them is fine, they're redundant.
    """
    for entry in player.get("stats", []):
        if entry.get("scoringPeriodId") == 0 and entry.get("statSourceId") == 1 and entry.get("statSplitTypeId") == 0:
            return entry.get("stats") or {}
    return None


def _compute_points_from_espn_stats(stats: dict, scoring: ScoringSettings) -> float:
    def g(key: str) -> float:
        ids = STAT_ID[key]
        ids = ids if isinstance(ids, tuple) else (ids,)
        for stat_id in ids:
            value = stats.get(stat_id)
            if value is not None:
                return value
        return 0.0

    return (
        g("pass_yard") * scoring.pass_yard
        + g("pass_td") * scoring.pass_td
        + g("interception") * scoring.interception
        + g("rush_yard") * scoring.rush_yard
        + g("rush_td") * scoring.rush_td
        + g("reception") * scoring.reception
        + g("rec_yard") * scoring.rec_yard
        + g("rec_td") * scoring.rec_td
        + g("fumble_lost") * scoring.fumble_lost
    )


def fetch_espn_projected_points(
    year: int,
    scoring: ScoringSettings,
    cache_path: Path | None = None,
    force_refresh: bool = False,
    timeout: int = 30,
) -> dict[str, float]:
    """Player full name -> ESPN's own season-total projected fantasy points,
    computed under the league's own scoring rules (not ESPN's own scoring
    display, which may not match). Reuses the same cached raw payload as
    `fetch_espn_players` -- same endpoint, different fields extracted.
    """
    raw = _fetch_raw(year, cache_path, force_refresh, timeout)
    points_by_name: dict[str, float] = {}
    for p in raw:
        position = POSITION_MAP.get(p.get("defaultPositionId"))
        if position not in PROJECTION_POSITIONS:
            continue
        stats = _season_projection_raw_stats(p)
        if not stats:
            continue
        points_by_name[p["fullName"]] = round(_compute_points_from_espn_stats(stats, scoring), 2)
    return points_by_name


def _load_cached(cache_path: Path) -> list | None:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    return None


def _normalize(raw: list[dict]) -> list[EspnPlayer]:
    players: list[EspnPlayer] = []
    for p in raw:
        position = POSITION_MAP.get(p.get("defaultPositionId"))
        if position not in FANTASY_POSITIONS:
            continue

        ownership = p.get("ownership") or {}
        adp = ownership.get("averageDraftPosition") or 0.0
        if adp <= 0:
            continue  # never actually drafted in ESPN leagues -- no signal

        draft_ranks = (p.get("draftRanksByRankType") or {}).get("STANDARD") or {}
        expert_rank = draft_ranks.get("rank")

        players.append(
            EspnPlayer(
                espn_id=p["id"],
                name=p["fullName"],
                position=position,
                team=TEAM_MAP.get(p.get("proTeamId", 0), "FA"),
                espn_adp=float(adp),
                espn_expert_rank=expert_rank,
            )
        )

    players.sort(key=lambda pl: pl.espn_adp)
    return players
