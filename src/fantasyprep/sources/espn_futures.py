"""Award-futures odds as a market-priced measure of a player's UPSIDE.

WHY THIS IS WORTH HAVING, given everything the research said

The modeling work established two things that together make this the right
signal to add rather than another projection input:

1. **The market already prices the median.** Prior production adds +0.0075
   R-squared on top of ADP. Anything aimed at a better central estimate is
   chasing an exhausted margin, so a Vegas-derived *projection* would be
   competing where there is nothing left to win.
2. **The system's biggest measured calibration defect is understated upside**,
   and it is worst exactly where drafts are decided. Elite players beat their
   bucket's stated P90 about 20% of the time instead of 10% (see
   research/calibration.py).

Offensive Player of the Year odds are a direct market read on the second thing.
ADP says where a player is expected to go; OPOY odds say what the market thinks
the chance of a genuinely exceptional season is. Those are different questions,
and only one of them is already answered well.

WHERE THE DATA COMES FROM, AND WHY IT IS FREE

ROADMAP.md's standing decision is free/scraped sources only, no paid odds API,
which caps the Vegas work to team-level implied totals -- player prop lines sit
behind paid feeds. Award futures turn out to be an exception: ESPN's public core
API publishes them, DraftKings-sourced, with no key and no scraping. Verified
live: 108 players carry 2026 OPOY odds, and **all 108 athlete IDs match the
ESPN player cache this project already keeps**, so the join needs no fuzzy name
matching at all.

DE-VIGGING IS NOT OPTIONAL HERE

A 108-runner futures market carries an enormous overround -- raw implied
probabilities sum to far more than 1, because the book's margin is spread across
every runner. Using raw implied probabilities would systematically overstate
every player's chance. Normalising to sum to 1 removes the book's margin under
the standard proportional assumption and yields something that behaves like a
probability.

The result is still a *relative* upside measure rather than a literal forecast:
OPOY is voted, not computed, so it rewards narrative and team success alongside
production. Treat it as the market's ranking of ceiling, not as P(monster season).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import requests

FUTURES_URL = (
    "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/{year}/futures"
)

OFFENSIVE_PLAYER_OF_THE_YEAR = "Offensive Player of the Year"
MVP = "Regular Season MVP"


@dataclass(frozen=True)
class FutureOdds:
    espn_id: str
    american_odds: int
    implied_probability: float
    devigged_probability: float


def american_to_probability(american: int) -> float:
    """Implied probability of an American-odds price, before removing the vig."""
    if american > 0:
        return 100.0 / (american + 100.0)
    return -american / (-american + 100.0)


def parse_american(value: str) -> int | None:
    """ESPN publishes prices as strings like '+550'. Anything unparseable is
    dropped rather than guessed -- a mispriced favourite would distort the whole
    normalised field."""
    try:
        return int(str(value).replace("+", "").strip())
    except (TypeError, ValueError):
        return None


def fetch_award_futures(
    year: int,
    award: str = OFFENSIVE_PLAYER_OF_THE_YEAR,
    cache_path: Path | None = None,
    force_refresh: bool = False,
    timeout: int = 30,
) -> list[FutureOdds]:
    """Every priced runner for one award, de-vigged.

    Cached the same way the other ESPN and FFC fetches are, so repeat calls and
    offline runs cost nothing.
    """
    raw = _load_cached(cache_path) if cache_path and not force_refresh else None
    if raw is None:
        response = requests.get(FUTURES_URL.format(year=year), params={"limit": 100}, timeout=timeout)
        response.raise_for_status()
        raw = response.json()
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(raw), encoding="utf-8")

    return _normalize(raw, award)


def _load_cached(cache_path: Path) -> dict | None:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    return None


def _normalize(raw: dict, award: str) -> list[FutureOdds]:
    items = [item for item in raw.get("items", []) if item.get("name") == award]
    if not items:
        return []

    priced: list[tuple[str, int, float]] = []
    for book in items[0].get("futures", []):
        for entry in book.get("books", []):
            espn_id = str(entry.get("athlete", {}).get("$ref", "")).split("/")[-1].split("?")[0]
            american = parse_american(entry.get("value"))
            if not espn_id or american is None:
                continue
            priced.append((espn_id, american, american_to_probability(american)))
        if priced:
            break  # one book is enough; mixing books would mix vig structures

    total = sum(p for _, _, p in priced)
    if total <= 0:
        return []

    return [
        FutureOdds(
            espn_id=espn_id,
            american_odds=american,
            implied_probability=round(probability, 5),
            devigged_probability=round(probability / total, 5),
        )
        for espn_id, american, probability in priced
    ]


def upside_by_name(
    futures: list[FutureOdds], name_by_espn_id: dict[str, str]
) -> dict[str, float]:
    """Map de-vigged probability onto player names.

    Unmatched ids are dropped rather than carried as a placeholder: a player
    with no odds is not a player with zero ceiling, he is a player the market
    did not price, and those are different claims. Callers should treat a
    missing name as "no signal", not as "no upside".
    """
    scores: dict[str, float] = {}
    for future in futures:
        name = name_by_espn_id.get(future.espn_id)
        if name:
            scores[name] = future.devigged_probability
    return scores


def market_overround(futures: list[FutureOdds]) -> float:
    """Sum of raw implied probabilities -- 1.0 would be a fair book.

    Reported rather than hidden so the size of the de-vig correction is visible;
    a large futures field routinely runs well above 1.3.
    """
    return round(sum(f.implied_probability for f in futures), 4)
