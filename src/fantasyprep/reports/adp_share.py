"""ADP share: which NFL teams carry the most fantasy draft capital.

Rank every NFL team by how much high draft capital sits on its roster, using
nothing but ADP.

Counting a team's top-100 players is the obvious first answer, and it is
reported -- but a count says Jahmyr Gibbs at ADP 1.8 and a receiver at ADP 95
are the same thing, and any cutoff you pick is arbitrary. So each player gets a
weight that falls off as his ADP gets later:

    weight(player) = 0.5 ** ((adp - 1) / HALF_LIFE)

One knob, and it is readable in plain English: every HALF_LIFE picks, a player
is worth half as much. At a 30-pick half life, the 1.01 overall is worth 1.000,
the 31st pick 0.500, the 61st 0.250, the 121st 0.0625.

Exponential rather than linear because linear gets the top of the draft badly
wrong: `201 - adp` makes the first pick worth 1.005x the second, when the whole
premise of a draft is that it is worth much more than that. Exponential decay
is also the rough shape of every real draft-pick value chart.

HALF_LIFE is a choice, so `compute` also reports the ranking under several
other half lives and under linear weighting. If the ordering held only at one
setting it would be an artifact of the knob rather than a fact about rosters --
see `sensitivity` in the output.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from fantasyprep.historical.sources import ffc
from fantasyprep.league.settings import LeagueSettings, default_settings

# Positions that count as fantasy draft capital. Kickers and defenses are
# excluded: nobody means them by "highly drafted players", and their ADPs say
# more about where convention puts them than about roster strength.
CAPITAL_POSITIONS = ("QB", "RB", "WR", "TE")

# Picks over which a player's weight halves.
HALF_LIFE = 30.0

# Alternates used only for the stability check, never for the headline.
ALTERNATE_HALF_LIVES = (15.0, 45.0, 60.0)

# Count thresholds reported next to the weighted number. Three rather than one,
# because any single cutoff is arbitrary -- if a team swings across all three,
# the count view is not telling you anything stable.
COUNT_THRESHOLDS = (24, 50, 100)


def weight(adp: float, half_life: float = HALF_LIFE) -> float:
    """Draft capital of one player, from his ADP alone. 1.00 at pick 1."""
    return 0.5 ** ((adp - 1.0) / half_life)


def linear_weight(adp: float, last_pick: float = 200.0) -> float:
    """Straight-line alternative, for the stability check only."""
    return max(0.0, (last_pick + 1.0 - adp) / last_pick)


@dataclass
class TeamCapital:
    team: str
    players: list[ffc.FfcPlayer] = field(default_factory=list)

    def capital(self, half_life: float = HALF_LIFE) -> float:
        return sum(weight(p.adp, half_life) for p in self.players)

    def linear_capital(self) -> float:
        return sum(linear_weight(p.adp) for p in self.players)

    def count_within(self, threshold: int) -> int:
        return sum(1 for p in self.players if p.adp <= threshold)

    @property
    def best(self) -> ffc.FfcPlayer | None:
        return min(self.players, key=lambda p: p.adp) if self.players else None


def spearman(a: list[float], b: list[float]) -> float:
    """Rank correlation. No scipy -- not worth an optional dependency here."""

    def ranks(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        out = [0.0] * len(xs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            shared = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = shared
            i = j + 1
        return out

    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else 0.0


def compute(
    year: int = 2026, data_dir: Path = Path("data"), settings: LeagueSettings | None = None
) -> dict:
    settings = settings or default_settings()
    raw_dir = data_dir / "raw"
    pool = ffc.fetch_adp(
        year, teams=settings.teams, cache_path=raw_dir / f".ffc_{settings.teams}_{year}.json"
    )

    teams: dict[str, TeamCapital] = {}
    for p in pool:
        if p.position not in CAPITAL_POSITIONS:
            continue
        teams.setdefault(p.team, TeamCapital(p.team)).players.append(p)
    for t in teams.values():
        t.players.sort(key=lambda p: p.adp)

    ordered = sorted(teams.values(), key=lambda t: t.capital(), reverse=True)
    total = sum(t.capital() for t in ordered) or 1.0

    rows = []
    for i, t in enumerate(ordered, start=1):
        best = t.best
        rows.append({
            "rank": i,
            "team": t.team,
            "capital": round(t.capital(), 3),
            "share": t.capital() / total,
            "counts": {str(k): t.count_within(k) for k in COUNT_THRESHOLDS},
            "n_players": len(t.players),
            "best_player": best.name if best else None,
            "best_adp": best.adp if best else None,
            "players": [
                {
                    "name": p.name, "position": p.position, "adp": p.adp,
                    "weight": round(weight(p.adp), 3),
                }
                for p in t.players
            ],
        })

    # Stability: does the ordering survive a different knob?
    headline = [t.capital() for t in ordered]
    sensitivity = {
        f"half_life_{int(h)}": round(spearman(headline, [t.capital(h) for t in ordered]), 3)
        for h in ALTERNATE_HALF_LIVES
    }
    sensitivity["linear"] = round(spearman(headline, [t.linear_capital() for t in ordered]), 3)
    sensitivity["top100_count"] = round(
        spearman(headline, [float(t.count_within(100)) for t in ordered]), 3
    )

    # Same question, phrased as movement: how far does any team travel when the
    # half life changes? A correlation near 1.0 can still hide one team moving
    # a long way, and that is worth surfacing rather than averaging away.
    alt_rank_moves = {}
    for h in ALTERNATE_HALF_LIVES:
        alt_order = sorted(ordered, key=lambda t: t.capital(h), reverse=True)
        pos = {t.team: i for i, t in enumerate(alt_order, start=1)}
        alt_rank_moves[f"half_life_{int(h)}"] = max(
            abs(pos[t.team] - i) for i, t in enumerate(ordered, start=1)
        )

    return {
        "year": year,
        "source": "Fantasy Football Calculator consensus ADP",
        "teams_in_league_settings": settings.teams,
        "scoring": "PPR" if settings.scoring.reception == 1.0 else "non-PPR",
        "half_life": HALF_LIFE,
        "total_capital": round(total, 2),
        "sensitivity": sensitivity,
        "max_rank_move": alt_rank_moves,
        "rows": rows,
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Rank NFL teams by fantasy draft capital (ADP only).")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    report = compute(args.year, args.data_dir)
    print(f"{args.year} ADP share -- weight = 0.5 ^ ((ADP - 1) / {report['half_life']:.0f})\n")
    print(f"{'#':>3}  {'TM':<4} {'share':>7} {'capital':>8} {'t24':>4}{'t50':>4}{'t100':>5}   best player")
    for r in report["rows"]:
        c = r["counts"]
        print(f"{r['rank']:>3}  {r['team']:<4} {r['share']:>6.1%} {r['capital']:>8.2f} "
              f"{c['24']:>4}{c['50']:>4}{c['100']:>5}   {r['best_player']} ({r['best_adp']})")
    print("\nStability of this ordering (Spearman vs the headline):")
    for k, v in report["sensitivity"].items():
        print(f"  {k:<16} {v:+.3f}")
    print("Worst single-team rank move when the half life changes:")
    for k, v in report["max_rank_move"].items():
        print(f"  {k:<16} {v} places")

    if args.out:
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
