"""Two independent offense rankings, to compare draft capital against.

Neither is built from fantasy projections. That was the previous attempt and it
had a real flaw: summing player projections measures what a *fantasy* scoring
system says about a roster, which is close to circular when the thing you want
to check is a fantasy draft board. These two come from outside that world.

**Vegas implied team total.** For each game, the sportsbook posts a total
(over/under) and a spread. Those two numbers pin down how many points each side
is expected to score:

    implied total = over_under / 2 + margin / 2

where `margin` is +spread for the favourite and -spread for the underdog.
Averaged across the priced weeks, that is the market's expectation of a team's
scoring, with real money behind it. Free from ESPN's scoreboard endpoint,
DraftKings-sourced.

Two honest limits: only the opening weeks are priced, so each team gets a
handful of games and the schedule is not evened out; and a team total counts
*all* points, including defensive and special-teams scores, which no fantasy
offense collects.

**ESPN FPI offense component.** FPI decomposes into offense, defense and special
teams -- verified here rather than assumed: the three sum to the published FPI
for all 32 teams. The offense component is points above average per game,
already adjusted for opponent, and available before a snap is played. It is a
model rather than money, and it is a *rating*, not a point total.

They measure different things on purpose. Vegas says how many points a team
scores; FPI says how good its offense is relative to average, with the defense
it plays against stripped out. A team can rank high on one and low on the other,
and that gap is informative rather than a contradiction.
"""
from __future__ import annotations

import argparse
import json
import statistics
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    "?seasontype=2&week={week}&dates={year}"
)
POWER_INDEX = (
    "https://site.web.api.espn.com/apis/fitt/v3/sports/football/nfl/powerindex?season={year}"
)

# Weeks to pull lines for. Books price the opening stretch of the season well
# before it starts; beyond this they thin out.
VEGAS_WEEKS = range(1, 7)

# FPI's `fpi` category is a positional array. Index 0 is the overall rating and
# 1/2/3 are the offense, defense and special-teams contributions -- confirmed by
# checking that 1+2+3 == 0 for every team (see `verify_fpi_split`).
FPI_TOTAL, FPI_OFFENSE, FPI_DEFENSE, FPI_SPECIAL = 0, 1, 2, 3

TEAM_ALIASES = {"WSH": "WAS"}


def normalize_team(team: str) -> str:
    return TEAM_ALIASES.get(team, team)


def _get(url: str, timeout: int = 30) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def _cached(url: str, cache_path: Path | None, force: bool = False) -> dict:
    if cache_path and cache_path.exists() and not force:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    payload = _get(url)
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def vegas_implied_totals(
    year: int = 2026, raw_dir: Path | None = None, force_refresh: bool = False
) -> dict:
    per_team: dict[str, list[float]] = defaultdict(list)
    weeks_used = []
    for week in VEGAS_WEEKS:
        cache = raw_dir / f".espn_scoreboard_{year}_wk{week}.json" if raw_dir else None
        try:
            payload = _cached(SCOREBOARD.format(week=week, year=year), cache, force_refresh)
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
        priced = 0
        for event in payload.get("events", []):
            competition = event["competitions"][0]
            odds = competition.get("odds") or []
            if not odds:
                continue
            line = odds[0]
            over_under, spread = line.get("overUnder"), line.get("spread")
            if over_under is None or spread is None:
                continue
            # The spread is quoted from one side. Find which side is favoured so
            # the sign is applied to the right team; without this every team
            # would be handed the favourite's half of the margin.
            favourite = None
            if (line.get("homeTeamOdds") or {}).get("favorite"):
                favourite = "home"
            elif (line.get("awayTeamOdds") or {}).get("favorite"):
                favourite = "away"
            if favourite is None:
                favourite = "home" if spread < 0 else "away"
            for competitor in competition["competitors"]:
                team = normalize_team(competitor["team"]["abbreviation"])
                margin = abs(spread) / 2 if competitor["homeAway"] == favourite else -abs(spread) / 2
                per_team[team].append(over_under / 2 + margin)
            priced += 1
        if priced:
            weeks_used.append(week)

    rows = [
        {
            "team": team,
            "implied_points_per_game": round(statistics.mean(values), 2),
            "games_priced": len(values),
            "min": round(min(values), 1),
            "max": round(max(values), 1),
        }
        for team, values in per_team.items()
    ]
    rows.sort(key=lambda r: -r["implied_points_per_game"])
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return {
        "metric": "Vegas implied points per game",
        "source": "DraftKings lines via ESPN scoreboard",
        "weeks": list(weeks_used),
        "rows": rows,
    }


def verify_fpi_split(teams: list[dict]) -> tuple[int, int]:
    """How many teams have offense + defense + special teams == FPI.

    Returned rather than asserted so the page can state the check was run and
    what it found, instead of the reader taking the decomposition on trust.
    """
    ok = total = 0
    for team in teams:
        values = next(
            (c.get("values") for c in team.get("categories", []) if c.get("name") == "fpi"), None
        )
        if not values or len(values) <= FPI_SPECIAL:
            continue
        total += 1
        parts = values[FPI_OFFENSE] + values[FPI_DEFENSE] + values[FPI_SPECIAL]
        if abs(parts - values[FPI_TOTAL]) < 0.02:
            ok += 1
    return ok, total


def fpi_offense(
    year: int = 2026, raw_dir: Path | None = None, force_refresh: bool = False
) -> dict:
    cache = raw_dir / f".espn_fpi_{year}.json" if raw_dir else None
    payload = _cached(POWER_INDEX.format(year=year), cache, force_refresh)
    teams = payload.get("teams", [])
    verified, checked = verify_fpi_split(teams)

    rows = []
    for team in teams:
        values = next(
            (c.get("values") for c in team.get("categories", []) if c.get("name") == "fpi"), None
        )
        if not values or len(values) <= FPI_SPECIAL:
            continue
        rows.append({
            "team": normalize_team(team["team"].get("abbreviation", "")),
            "offense": round(values[FPI_OFFENSE], 2),
            "defense": round(values[FPI_DEFENSE], 2),
            "special": round(values[FPI_SPECIAL], 2),
            "fpi": round(values[FPI_TOTAL], 2),
        })
    rows.sort(key=lambda r: -r["offense"])
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return {
        "metric": "ESPN FPI offense component",
        "source": "ESPN Football Power Index",
        "split_verified": f"{verified}/{checked}",
        "rows": rows,
    }


def compute(year: int = 2026, data_dir: Path = Path("data"), force_refresh: bool = False) -> dict:
    raw_dir = data_dir / "raw"
    return {
        "year": year,
        "vegas": vegas_implied_totals(year, raw_dir, force_refresh),
        "fpi": fpi_offense(year, raw_dir, force_refresh),
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Vegas implied totals and FPI offense ratings.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--refresh", action="store_true", help="re-fetch instead of using cache")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    report = compute(args.year, args.data_dir, args.refresh)

    vegas = report["vegas"]
    print(f"{vegas['metric']} -- {vegas['source']}, weeks {vegas['weeks']}\n")
    print(f"{'#':>3}  {'TM':<4} {'pts/g':>7} {'games':>6}  range")
    for r in vegas["rows"]:
        print(f"{r['rank']:>3}  {r['team']:<4} {r['implied_points_per_game']:>7.1f} "
              f"{r['games_priced']:>6}  {r['min']:.1f}-{r['max']:.1f}")

    fpi = report["fpi"]
    print(f"\n{fpi['metric']} -- offense+defense+ST == FPI for {fpi['split_verified']} teams\n")
    print(f"{'#':>3}  {'TM':<4} {'off':>7} {'def':>7} {'st':>6} {'fpi':>7}")
    for r in fpi["rows"]:
        print(f"{r['rank']:>3}  {r['team']:<4} {r['offense']:>+7.2f} {r['defense']:>+7.2f} "
              f"{r['special']:>+6.2f} {r['fpi']:>+7.2f}")

    if args.out:
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
