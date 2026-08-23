"""Player projections from two market signals and nothing else.

The idea: Vegas prices *how big an offense is*, draft position prices *how it
divides*. Multiply and you have a player projection built entirely from markets
-- no stat model, no projection service.

    projection = team fantasy pool  x  player's share of it

Both halves are estimated rather than assumed, because a naive version of this
is badly wrong in two specific ways.

**The pool.** Vegas quotes real football points, not fantasy points, so the two
cannot be multiplied directly. Measured on 319 team-seasons (2015-2024, real
schedule scores against real fantasy production), team scoring predicts the
fantasy pool at **r = 0.842**, and each extra point per game is worth about
**+39 fantasy points** of team pool. That regression is what converts a Vegas
line into a pool.

**The share.** Draft capital is *far* more top-heavy than production, and it
systematically buries quarterbacks. Measured on the 2026 board: Jaxon
Smith-Njigba holds 72.1% of Seattle's draft capital but only about 21% of its
projected production, while Sam Darnold holds 3.5% of the capital and about 19%
of the production -- quarterbacks slide in a one-quarterback league, which says
nothing about how many points they score. Multiplying by raw capital share would
hand receivers three times their real share and write quarterbacks off entirely.

So the share is fitted, not taken raw:

    predicted share  ∝  position_multiplier  x  capital_weight ** alpha

`alpha` below 1 flattens the top-heaviness; the position multipliers undo the
format's distortion. Both are fitted on real historical seasons, and evaluated
walk-forward -- each test season is predicted using only seasons strictly before
it, the same discipline the rest of this project uses.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from fantasyprep.historical.sources import ffc
from fantasyprep.league.settings import LeagueSettings, default_settings
from fantasyprep.players.normalize import normalize_name

CAPITAL_POSITIONS = ("QB", "RB", "WR", "TE")
HALF_LIFE = 30.0

# Franchises that moved or are spelled differently between sources.
TEAM_ALIASES = {
    "LA": "LAR", "STL": "LAR", "SD": "LAC", "OAK": "LV",
    "WSH": "WAS", "JAC": "JAX",
}

# Candidate flattening exponents. 1.0 is "use capital share as-is" -- included
# so the fit can report how much better the calibration actually is than the
# naive version it replaces.
ALPHA_GRID = [round(0.05 * i, 2) for i in range(1, 21)]


def normalize_team(team: str) -> str:
    return TEAM_ALIASES.get(team, team)


def weight(adp: float, half_life: float = HALF_LIFE) -> float:
    return 0.5 ** ((adp - 1.0) / half_life)


def historical_frame(
    years: list[int], data_dir: Path, settings: LeagueSettings
) -> list[dict]:
    """(season, team, player, position, capital weight, real fantasy points).

    Teams come from the preseason ADP source rather than from where the player
    finished the year -- a drafter grouping players by team knows the former and
    not the latter.
    """
    import pandas as pd

    raw_dir = data_dir / "raw"
    seasons = pd.read_parquet(data_dir / "historical" / "player_seasons.parquet")
    seasons = seasons[seasons.fantasy_position.isin(CAPITAL_POSITIONS)]
    actual = {
        (int(r.season), normalize_name(r.player_name)): float(r.fantasy_points)
        for r in seasons.itertuples()
    }

    rows = []
    for year in years:
        cache = raw_dir / f".ffc_{settings.teams}_{year}.json"
        if not cache.exists():
            continue
        for player in ffc.fetch_adp(year, teams=settings.teams, cache_path=cache):
            if player.position not in CAPITAL_POSITIONS:
                continue
            points = actual.get((year, normalize_name(player.name)))
            if points is None:
                continue
            rows.append({
                "season": year,
                "team": normalize_team(player.team),
                "name": player.name,
                "position": player.position,
                "weight": weight(player.adp),
                "adp": player.adp,
                "actual": points,
            })
    return rows


def _shares(rows: list[dict], alpha: float, mult: dict[str, float]) -> list[float]:
    """Predicted share of team production for each row, normalized per team."""
    by_team: dict[tuple, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_team[(r["season"], r["team"])].append(i)
    out = [0.0] * len(rows)
    for idx in by_team.values():
        raw = [mult.get(rows[i]["position"], 1.0) * rows[i]["weight"] ** alpha for i in idx]
        total = sum(raw) or 1.0
        for i, v in zip(idx, raw):
            out[i] = v / total
    return out


def _actual_shares(rows: list[dict]) -> list[float]:
    by_team: dict[tuple, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_team[(r["season"], r["team"])].append(i)
    out = [0.0] * len(rows)
    for idx in by_team.values():
        total = sum(max(0.0, rows[i]["actual"]) for i in idx) or 1.0
        for i in idx:
            out[i] = max(0.0, rows[i]["actual"]) / total
    return out


def fit_shape(rows: list[dict]) -> dict:
    """Grid-search alpha; solve position multipliers by iterative proportional
    fitting at each alpha. Scored by mean absolute share error."""
    truth = _actual_shares(rows)
    best = None
    for alpha in ALPHA_GRID:
        mult = {p: 1.0 for p in CAPITAL_POSITIONS}
        for _ in range(12):
            pred = _shares(rows, alpha, mult)
            for position in CAPITAL_POSITIONS:
                idx = [i for i, r in enumerate(rows) if r["position"] == position]
                if not idx:
                    continue
                p = sum(pred[i] for i in idx)
                a = sum(truth[i] for i in idx)
                if p > 0:
                    mult[position] *= (a / p) ** 0.5  # damped, so it converges
            norm = statistics.fmean(mult.values())
            mult = {k: v / norm for k, v in mult.items()}
        pred = _shares(rows, alpha, mult)
        mae = statistics.fmean(abs(p - t) for p, t in zip(pred, truth))
        if best is None or mae < best["mae"]:
            best = {"alpha": alpha, "mult": dict(mult), "mae": mae}
    return best


def slot_calibration(rows: list[dict], truth: list[float] | None = None) -> list[dict]:
    """Mean capital share vs mean realised production share, by how early a
    player was drafted among his own teammates.

    This is the single most important table behind the model. The most-drafted
    player on a team holds about half his team's draft capital and captures only
    about a quarter of its production -- so a projection that multiplies by raw
    capital share roughly doubles every team's best player and starves the rest.
    """
    truth = truth if truth is not None else _actual_shares(rows)
    by_team: dict[tuple, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_team[(r["season"], r["team"])].append(i)
    buckets: dict[int, tuple[list, list]] = defaultdict(lambda: ([], []))
    for idx in by_team.values():
        ordered = sorted(idx, key=lambda i: rows[i]["adp"])
        total = sum(rows[i]["weight"] for i in ordered) or 1.0
        for slot, i in enumerate(ordered[:5], start=1):
            buckets[slot][0].append(rows[i]["weight"] / total)
            buckets[slot][1].append(truth[i])
    return [
        {
            "slot": slot,
            "capital_share": round(statistics.fmean(buckets[slot][0]), 4),
            "actual_share": round(statistics.fmean(buckets[slot][1]), 4),
            "n": len(buckets[slot][0]),
        }
        for slot in sorted(buckets)
    ]


def evaluate(rows: list[dict], first_test_year: int = 2018) -> dict:
    """Walk-forward: predict each season using only strictly earlier seasons."""
    years = sorted({r["season"] for r in rows})
    truth_all = _actual_shares(rows)
    index = {id(r): i for i, r in enumerate(rows)}

    fitted, naive, flat = [], [], []
    points_pred: dict[str, list[float]] = defaultdict(list)
    points_true: dict[str, list[float]] = defaultdict(list)
    for year in [y for y in years if y >= first_test_year]:
        train = [r for r in rows if r["season"] < year]
        test = [r for r in rows if r["season"] == year]
        if len(train) < 200 or not test:
            continue
        shape = fit_shape(train)
        pred = _shares(test, shape["alpha"], shape["mult"])
        raw = _shares(test, 1.0, {p: 1.0 for p in CAPITAL_POSITIONS})
        n_by_team: dict[tuple, int] = defaultdict(int)
        for r in test:
            n_by_team[(r["season"], r["team"])] += 1
        even = [1.0 / n_by_team[(r["season"], r["team"])] for r in test]
        truth = [truth_all[index[id(r)]] for r in test]
        fitted += [abs(a - b) for a, b in zip(pred, truth)]
        naive += [abs(a - b) for a, b in zip(raw, truth)]
        flat += [abs(a - b) for a, b in zip(even, truth)]

        # Shares are the thing fitted, but points are the thing anyone cares
        # about, so both get reported. The team pool is held at its true value
        # here so this measures the share model alone rather than blaming it for
        # error in the Vegas-to-pool step.
        pool: dict[tuple, float] = defaultdict(float)
        for r in test:
            pool[(r["season"], r["team"])] += r["actual"]
        for name, share in (("fitted", pred), ("raw", raw), ("even", even)):
            for r, sh in zip(test, share):
                points_pred[name].append(pool[(r["season"], r["team"])] * sh)
                points_true[name].append(r["actual"])

    def _corr(a, b):
        if len(a) < 3:
            return None
        n = len(a)
        ma, mb = sum(a) / n, sum(b) / n
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
        return round(num / den, 3) if den else None

    def _mae(name):
        pair = list(zip(points_pred[name], points_true[name]))
        return round(statistics.fmean(abs(a - b) for a, b in pair), 1) if pair else None

    return {
        "test_rows": len(fitted),
        "mae_share_fitted": round(statistics.fmean(fitted), 4) if fitted else None,
        "mae_share_raw_capital": round(statistics.fmean(naive), 4) if naive else None,
        "mae_share_even_split": round(statistics.fmean(flat), 4) if flat else None,
        "points": {
            name: {
                "mae": _mae(name),
                "corr": _corr(points_pred[name], points_true[name]),
            }
            for name in ("fitted", "raw", "even")
        },
    }


def project(
    year: int = 2026,
    data_dir: Path = Path("data"),
    settings: LeagueSettings | None = None,
    train_years: list[int] | None = None,
) -> dict:
    settings = settings or default_settings()
    train_years = train_years or list(range(2015, year))
    raw_dir = data_dir / "raw"

    frame = historical_frame(train_years, data_dir, settings)
    shape = fit_shape(frame)
    evaluation = evaluate(frame)

    # Team pool from the Vegas line, via the measured points -> pool regression.
    offense = json.loads((data_dir / f"offense_rankings_{year}.json").read_text(encoding="utf-8"))
    vegas = {r["team"]: r["implied_points_per_game"] for r in offense["vegas"]["rows"]}

    pool_by_team = defaultdict(float)
    for r in frame:
        pool_by_team[(r["season"], r["team"])] += r["actual"]
    league_mean_pool = statistics.fmean(pool_by_team.values())

    # Historical mean scoring, so a team is scaled by how it compares to a
    # typical offense rather than by its raw line.
    mean_line = statistics.fmean(vegas.values())
    # Slope measured separately (see module docstring): +1 point/game is worth
    # about +39 fantasy points of pool. Expressed relative to the mean so the
    # league total stays anchored to what teams really produce.
    points_to_pool = 39.1

    capital = json.loads((data_dir / f"adp_share_{year}.json").read_text(encoding="utf-8"))
    rows = []
    for team_row in capital["rows"]:
        team = normalize_team(team_row["team"])
        line = vegas.get(team)
        if line is None:
            continue
        pool = league_mean_pool + (line - mean_line) * points_to_pool
        members = [
            {"season": year, "team": team, "name": p["name"], "position": p["position"],
             "weight": p["weight"], "adp": p["adp"], "actual": 0.0}
            for p in team_row["players"]
        ]
        shares = _shares(members, shape["alpha"], shape["mult"])
        for member, share in zip(members, shares):
            rows.append({
                "name": member["name"],
                "position": member["position"],
                "team": team,
                "adp": member["adp"],
                "capital_share": round(member["weight"] / (team_row["capital"] or 1), 4),
                "predicted_share": round(share, 4),
                "team_pool": round(pool, 1),
                "projection": round(pool * share, 1),
            })

    rows.sort(key=lambda r: -r["projection"])
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    # Overall points order puts every starting quarterback above every receiver,
    # which is true and useless for drafting -- so rank within position too.
    seen: dict[str, int] = defaultdict(int)
    for r in rows:
        seen[r["position"]] += 1
        r["position_rank"] = seen[r["position"]]
    return {
        "year": year,
        "alpha": shape["alpha"],
        "position_multipliers": {k: round(v, 3) for k, v in shape["mult"].items()},
        "train_years": [min(train_years), max(train_years)],
        "train_rows": len(frame),
        "league_mean_pool": round(league_mean_pool, 1),
        "mean_line": round(mean_line, 2),
        "points_to_pool": points_to_pool,
        "evaluation": evaluation,
        "slot_calibration": slot_calibration(frame),
        "rows": rows,
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Market-only player projections.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    report = project(args.year, args.data_dir)
    ev = report["evaluation"]
    print(f"fitted on {report['train_rows']} player-seasons "
          f"{report['train_years'][0]}-{report['train_years'][1]}")
    print(f"  alpha = {report['alpha']}   multipliers = {report['position_multipliers']}")
    print(f"  walk-forward share MAE: fitted {ev['mae_share_fitted']}  "
          f"raw capital {ev['mae_share_raw_capital']}  even split {ev['mae_share_even_split']}")
    print("\n  predicting REAL points, walk-forward (team pool held true):")
    for name, label in (("fitted", "fitted share"), ("raw", "raw capital share"),
                        ("even", "even split")):
        p = ev["points"][name]
        print(f"    {label:<20} MAE {p['mae']:>6}   corr {p['corr']}")

    print("\n  how capital share compares to realised production share:")
    print(f"    {'slot':<20}{'capital':>10}{'actual':>10}")
    for s in report["slot_calibration"]:
        print(f"    #{s['slot']} most-drafted{'':<6}{s['capital_share']:>9.1%}{s['actual_share']:>10.1%}")

    for position in CAPITAL_POSITIONS:
        best = [r for r in report["rows"] if r["position"] == position][:8]
        print(f"\n  {position}")
        for r in best:
            print(f"    {position}{r['position_rank']:<3} {r['name'][:24]:<26}{r['team']:>4}"
                  f"  adp {r['adp']:>5.0f}  cap {r['capital_share'] * 100:>3.0f}%"
                  f"  pred {r['predicted_share'] * 100:>3.0f}%  proj {r['projection']:>6.1f}")

    if args.out:
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
