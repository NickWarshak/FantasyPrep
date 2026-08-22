"""An offense ranking that owes nothing to ADP.

The ADP-share table measures what the *draft market* thinks of a team's players.
This measures what a *projection model* thinks their offense will actually
produce, so the two can be compared. If they agreed perfectly the comparison
would be pointless; where they disagree is the interesting part.

Source is ESPN's own season projections, already cached in this repo, scored
under our league's rules rather than ESPN's display scoring.

Only the offensive core counts -- one quarterback, two running backs, three
receivers, one tight end, each the highest-projected at his position on that
team. Summing every projected player instead would rank teams partly by how
many bodies ESPN happens to list, which is a fact about ESPN's database rather
than about the offense.

Two honest limits, both stated on the page rather than buried:

1. **Fantasy points double-count passing offense.** A touchdown pass scores for
   the quarterback and again for the receiver. That inflates pass-heavy offenses
   relative to run-heavy ones. `skill_only` (the same sum without the
   quarterback) is reported alongside so the reader can see how much of a team's
   standing rests on that.
2. **It is one model's opinion.** Projections are not measurements, and ESPN's
   are not independent of the same consensus that shapes ADP -- so agreement
   between the two is weaker evidence than disagreement.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fantasyprep.league.settings import LeagueSettings, default_settings
from fantasyprep.sources.espn import fetch_espn_players, fetch_espn_projected_points

# The offensive core: what a real offense actually feeds.
#
# Quarterback is handled differently from the rest -- see QB_IS_SUMMED. At the
# other positions a committee is real (two backs genuinely split carries), so
# taking the top N mirrors how the offense is actually used.
STARTER_SLOTS = {"QB": 1, "RB": 2, "WR": 3, "TE": 1}

# Only one quarterback plays at a time, so a team's QB production is the *sum*
# of its quarterbacks, not its best one. Taking the max instead silently
# punishes every team with an unsettled job: ESPN splits the projection across
# the candidates, so the leader holds only a fraction of a season. Measured on
# this data, max-vs-sum was Atlanta 134 -> 269, Cleveland 139 -> 330, Arizona
# 228 -> 270 -- and under the max rule Atlanta ranked 29th on offense purely
# because Tua Tagovailoa and Michael Penix Jr. split its projection in half.
QB_IS_SUMMED = True

# ESPN spells Washington differently from the ADP source.
TEAM_ALIASES = {"WSH": "WAS"}

# Not a team.
EXCLUDED_TEAMS = {"FA"}


def normalize_team(team: str) -> str:
    return TEAM_ALIASES.get(team, team)


def compute(
    year: int = 2026,
    data_dir: Path = Path("data"),
    settings: LeagueSettings | None = None,
) -> dict:
    settings = settings or default_settings()
    cache = data_dir / "raw" / f".espn_cache_{year}.json"

    players = fetch_espn_players(year, cache_path=cache)
    projections = fetch_espn_projected_points(year, settings.scoring, cache_path=cache)
    by_name = {p.name: p for p in players}

    per_team: dict[str, dict[str, list[tuple[str, float]]]] = {}
    for name, points in projections.items():
        meta = by_name.get(name)
        if meta is None:
            continue
        team = normalize_team(meta.team)
        if team in EXCLUDED_TEAMS or meta.position not in STARTER_SLOTS:
            continue
        per_team.setdefault(team, {}).setdefault(meta.position, []).append((name, points))

    rows = []
    for team, positions in per_team.items():
        core: list[dict] = []
        for position, slots in STARTER_SLOTS.items():
            ranked = sorted(positions.get(position, []), key=lambda x: -x[1])
            if position == "QB" and QB_IS_SUMMED:
                if ranked:
                    core.append({
                        "name": ranked[0][0],
                        "position": "QB",
                        "points": round(sum(v for _, v in ranked), 1),
                        "shared_with": [n for n, _ in ranked[1:] if _ >= 20.0],
                    })
                continue
            core.extend(
                {"name": n, "position": position, "points": round(v, 1)}
                for n, v in ranked[:slots]
            )
        total = sum(p["points"] for p in core)
        skill_only = sum(p["points"] for p in core if p["position"] != "QB")
        rows.append({
            "team": team,
            "projected": round(total, 1),
            "skill_only": round(skill_only, 1),
            "qb_points": round(total - skill_only, 1),
            "core": sorted(core, key=lambda p: -p["points"]),
        })

    rows.sort(key=lambda r: -r["projected"])
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    skill_order = sorted(rows, key=lambda r: -r["skill_only"])
    skill_rank = {r["team"]: i for i, r in enumerate(skill_order, start=1)}
    for r in rows:
        r["skill_rank"] = skill_rank[r["team"]]

    return {
        "year": year,
        "source": "ESPN season projections, scored under league rules",
        "starter_slots": STARTER_SLOTS,
        "scoring": "PPR" if settings.scoring.reception == 1.0 else "non-PPR",
        "rows": rows,
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Rank NFL offenses by projected fantasy production.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    report = compute(args.year, args.data_dir)
    slots = ", ".join(f"{v} {k}" for k, v in STARTER_SLOTS.items())
    print(f"{args.year} projected offense -- core of {slots}\n")
    print(f"{'#':>3}  {'TM':<4} {'proj':>7} {'skill':>7} {'QB':>6}  top piece")
    for r in report["rows"]:
        best = r["core"][0]
        print(f"{r['rank']:>3}  {r['team']:<4} {r['projected']:>7.0f} {r['skill_only']:>7.0f} "
              f"{r['qb_points']:>6.0f}  {best['name']} ({best['points']:.0f})")

    if args.out:
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
