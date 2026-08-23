"""Condensed offenses: a lot of points, shared between very few drafted players.

Two things make a team's players valuable, and they pull in different
directions. A big pie -- the offense scores a lot -- and few mouths at the
table, so each drafted player gets a large slice of it. A high-scoring offense
that spreads the ball over six draftable players can be a worse place to own a
player than a mediocre one funnelling everything through two.

**Size of the pie** is the Vegas implied points per game already computed in
`offense_rankings` -- the market's expectation, with money behind it.

**Number of mouths** is not a raw count of drafted players. A raw count is
dominated by irrelevant depth: a team with four bench fliers ranked past 300
would look twice as "spread" as one with two, when in truth neither matters.
Instead this uses the *effective* number of players, from the inverse
Herfindahl index over each team's share of its own draft capital:

    share_i           = weight_i / team capital
    HHI               = sum of share_i squared
    effective players = 1 / HHI

That number answers "how many players is this offense, really?". A team whose
capital is one superstar scores 1.0. A team splitting evenly between three
scores 3.0. Deep fliers carry almost no weight, so they barely move it --
which is the point, and why this is used instead of counting heads.

**Condensed scoring** puts them together:

    points per effective player = implied points per game / effective players

High means a lot of expected scoring reaching few owned players. That is the
shape a fantasy manager wants.

The honest limit: draft capital is the market's *opinion* of how an offense
divides, not a measurement of touches. Where the market is wrong about a
committee, this inherits that error.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def concentration(weights: list[float]) -> dict:
    """Effective player count and top-heaviness for one team."""
    total = sum(weights) or 1.0
    shares = sorted((w / total for w in weights), reverse=True)
    hhi = sum(s * s for s in shares)
    return {
        "hhi": round(hhi, 4),
        "effective_players": round(1 / hhi, 2) if hhi else 0.0,
        "top1_share": round(shares[0], 4) if shares else 0.0,
        "top2_share": round(sum(shares[:2]), 4) if shares else 0.0,
        "shares": [round(s, 4) for s in shares],
    }


def compute(
    capital_path: Path = Path("data/adp_share_2026.json"),
    offense_path: Path = Path("data/offense_rankings_2026.json"),
) -> dict:
    capital = json.loads(capital_path.read_text(encoding="utf-8"))
    offense = json.loads(offense_path.read_text(encoding="utf-8"))
    vegas = {r["team"]: r for r in offense["vegas"]["rows"]}

    rows = []
    for team_row in capital["rows"]:
        team = team_row["team"]
        if team not in vegas:
            continue
        weights = [p["weight"] for p in team_row["players"]]
        conc = concentration(weights)
        points = vegas[team]["implied_points_per_game"]
        eff = conc["effective_players"] or 1.0
        rows.append({
            "team": team,
            "implied_points_per_game": points,
            "vegas_rank": vegas[team]["rank"],
            "capital_rank": team_row["rank"],
            "capital": team_row["capital"],
            "drafted": team_row["n_players"],
            "effective_players": conc["effective_players"],
            "top1_share": conc["top1_share"],
            "top2_share": conc["top2_share"],
            "points_per_effective_player": round(points / eff, 2),
            # The top players by capital, for labelling the visual.
            "core": [
                {"name": p["name"], "position": p["position"], "adp": p["adp"],
                 "share": round(p["weight"] / (team_row["capital"] or 1), 4)}
                for p in team_row["players"][:6]
            ],
        })

    rows.sort(key=lambda r: -r["points_per_effective_player"])
    for i, r in enumerate(rows, start=1):
        r["rank"] = i

    points = [r["implied_points_per_game"] for r in rows]
    effective = [r["effective_players"] for r in rows]
    median_points = sorted(points)[len(points) // 2]
    median_effective = sorted(effective)[len(effective) // 2]

    # The ratio on its own is not the answer to "condensed offenses that score
    # a lot", and reporting it alone would mislead. A weak offense funnelling
    # everything through one player maximises it: Miami tops the ratio on 18.9
    # implied points, the third-lowest in the league, purely because De'Von
    # Achane holds 89% of its draft capital. That is a real signal about Achane
    # and a poor answer to the question asked.
    #
    # So each team is also placed against both medians. "Condensed scoring" is
    # the quadrant that is genuinely both -- above-median points AND
    # fewer-than-median mouths.
    for r in rows:
        big_pie = r["implied_points_per_game"] >= median_points
        few_mouths = r["effective_players"] <= median_effective
        r["quadrant"] = (
            "condensed scoring" if big_pie and few_mouths
            else "spread scoring" if big_pie
            else "condensed but low scoring" if few_mouths
            else "spread and low scoring"
        )

    return {
        "metric": "Implied points per effective drafted player",
        "median_points": round(median_points, 2),
        "median_effective": round(median_effective, 2),
        "sweet_spot": [r["team"] for r in rows if r["quadrant"] == "condensed scoring"],
        "rows": rows,
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capital", type=Path, default=Path("data/adp_share_2026.json"))
    parser.add_argument("--offense", type=Path, default=Path("data/offense_rankings_2026.json"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    report = compute(args.capital, args.offense)
    print(f"{report['metric']}\n")
    print(f"{'#':>3}  {'TM':<4} {'pts/g':>6} {'eff':>5} {'pts/eff':>8} "
          f"{'top1':>6} {'top2':>6}  core")
    for r in report["rows"]:
        core = " + ".join(c["name"].split()[-1] for c in r["core"][:3])
        print(f"{r['rank']:>3}  {r['team']:<4} {r['implied_points_per_game']:>6.1f} "
              f"{r['effective_players']:>5.2f} {r['points_per_effective_player']:>8.2f} "
              f"{r['top1_share'] * 100:>5.0f}% {r['top2_share'] * 100:>5.0f}%  {core}")
    print(f"\nmedian points {report['median_points']}, "
          f"median effective players {report['median_effective']}")

    if args.out:
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
