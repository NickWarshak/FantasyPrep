"""ADP share: which NFL teams carry the most fantasy draft capital.

The question is "rank every NFL team by how many highly-drafted players it
has". Counting them is the obvious first answer and it is genuinely useful, so
it is reported -- but a raw count cannot tell the difference between a team
with Jahmyr Gibbs at ADP 1.8 and a team with a WR at ADP 95. Both are "a
top-100 player". Any threshold you pick is arbitrary, and the answer changes
when you move it.

So the headline metric weights each player by what he is actually worth, using
the same real historical outcome data the draft engine already runs on:

    ADP -> position rank -> historical outcome bucket -> mean real points
    value = mean real points - replacement level at that position
    team capital = sum of value over that team's players
    ADP share    = team capital / league total capital

The replacement subtraction is what makes positions comparable. A QB who
projects 260 points is not more valuable than a 200-point RB if any streamed
QB gives you 221 -- and in a 10-team league the 19th QB really does
(`derive_rank_cutoff` measures that from real draft depth rather than
guessing). Without it this table would just rank teams by whether they have a
starting quarterback.

Deliberately excluded: K and DST. DST has no real-points source anywhere in
this codebase (`nfl_stats.POSITION_MAP` omits it, so it scores 0), and kickers
are draft-capital noise. Including them would add rows that are mostly an
artifact of where those positions happen to get drafted.
"""
from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from fantasyprep.historical.outcomes import build_outcome_distributions, outcome_for_rank
from fantasyprep.historical.sources import ffc
from fantasyprep.league.settings import LeagueSettings, default_settings

# The positions that represent real draft capital.
CAPITAL_POSITIONS = ("QB", "RB", "WR", "TE")

# Count thresholds reported alongside the weighted metric. Three of them, not
# one, precisely because any single threshold is arbitrary -- if a team's
# standing swings wildly across the three, the count view is not telling you
# anything stable and the weighted column is the one to trust.
COUNT_THRESHOLDS = (24, 50, 100)


@dataclass
class PlayerCapital:
    name: str
    position: str
    team: str
    adp: float
    position_rank: int
    expected_points: float
    replacement_points: float
    beyond_replacement_rank: bool = False

    @property
    def value(self) -> float:
        """Points above replacement, floored at zero.

        Floored because a late-round flier is worth 0 draft capital, never
        negative -- nobody is made worse off by holding a player they can cut.
        Letting these go negative would let a team of deep bench players score
        below a team with nobody at all.

        Players drafted deeper than their position's replacement rank are worth
        exactly 0 by construction. That is what replacement level *means*: if
        the 19th quarterback is freely available, the 24th cannot be an asset.
        Stating it as a rule matters here because the outcome buckets do not
        enforce it on their own -- `outcome_for_rank` falls back to the deepest
        bucket for every rank past the grid, and those deep buckets are thin
        enough to be non-monotonic. Measured on this very pool: the deepest TE
        bucket is a single 151.7-point season, so Greg Dulcich (TE24) scored
        *above* Sam LaPorta (TE7), and Malik Willis (QB24) scored 35.8 points
        above a QB19 replacement. Both are artifacts, and both would otherwise
        show up as real draft capital for their teams.
        """
        if self.beyond_replacement_rank:
            return 0.0
        return max(0.0, self.expected_points - self.replacement_points)


@dataclass
class TeamCapital:
    team: str
    players: list[PlayerCapital] = field(default_factory=list)

    @property
    def capital(self) -> float:
        return sum(p.value for p in self.players)

    def count_within(self, threshold: int) -> int:
        return sum(1 for p in self.players if p.adp <= threshold)

    @property
    def best(self) -> PlayerCapital | None:
        return min(self.players, key=lambda p: p.adp) if self.players else None


def replacement_levels(
    distributions, rank_cutoff: dict[str, int]
) -> dict[str, float]:
    """Mean real points at each position's replacement rank."""
    out = {}
    for position in CAPITAL_POSITIONS:
        cutoff = rank_cutoff.get(position)
        if cutoff is None:
            continue
        out[position] = statistics.mean(
            outcome_for_rank(distributions, position, cutoff).outcomes
        )
    return out


def build_player_capital(
    pool: list[ffc.FfcPlayer], distributions, rank_cutoff: dict[str, int]
) -> list[PlayerCapital]:
    pos_ranks = ffc.position_ranks(pool)
    replacements = replacement_levels(distributions, rank_cutoff)

    raw = []
    for player in pool:
        if player.position not in CAPITAL_POSITIONS:
            continue
        rank = pos_ranks.get(player.name, 999)
        try:
            expected = statistics.mean(
                outcome_for_rank(distributions, player.position, rank).outcomes
            )
        except (KeyError, AttributeError):
            continue
        raw.append((player, rank, expected))

    # Second guard, for inversions *inside* the rank grid rather than past its
    # end: force expected points to be non-increasing in position rank. A WR12
    # can never be credited with more than a WR9, whatever the sampling noise
    # in a particular bucket says. Applied as a running minimum down the rank
    # order, so it only ever revises a value downward and never invents one.
    ceiling: dict[str, float] = {}
    monotone: dict[tuple[str, int], float] = {}
    for player, rank, expected in sorted(raw, key=lambda r: (r[0].position, r[1])):
        key = (player.position, rank)
        if key in monotone:
            continue
        cap = ceiling.get(player.position)
        value = expected if cap is None else min(expected, cap)
        ceiling[player.position] = value
        monotone[key] = value

    rows = []
    for player, rank, _expected in raw:
        cutoff = rank_cutoff.get(player.position)
        rows.append(
            PlayerCapital(
                name=player.name,
                position=player.position,
                team=player.team,
                adp=player.adp,
                position_rank=rank,
                expected_points=monotone[(player.position, rank)],
                replacement_points=replacements.get(player.position, 0.0),
                beyond_replacement_rank=cutoff is not None and rank > cutoff,
            )
        )
    return rows


def by_team(rows: list[PlayerCapital]) -> list[TeamCapital]:
    teams: dict[str, TeamCapital] = {}
    for row in rows:
        teams.setdefault(row.team, TeamCapital(row.team)).players.append(row)
    for team in teams.values():
        team.players.sort(key=lambda p: p.adp)
    return sorted(teams.values(), key=lambda t: t.capital, reverse=True)


def spearman(a: list[float], b: list[float]) -> float:
    """Rank correlation, used to check the weighted ranking against the naive
    count ranking. No scipy -- this module is not worth an optional dep."""

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
    mean_a, mean_b = sum(ra) / n, sum(rb) / n
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(ra, rb))
    den = (sum((x - mean_a) ** 2 for x in ra) * sum((y - mean_b) ** 2 for y in rb)) ** 0.5
    return num / den if den else 0.0


def compute(year: int = 2026, data_dir: Path = Path("data"), settings: LeagueSettings | None = None) -> dict:
    settings = settings or default_settings()
    raw_dir = data_dir / "raw"
    pool = ffc.fetch_adp(
        year, teams=settings.teams, cache_path=raw_dir / f".ffc_{settings.teams}_{year}.json"
    )
    distributions = build_outcome_distributions(
        settings, cache_path=raw_dir / f".outcomes_{settings.teams}.json", adp_cache_dir=raw_dir
    )
    rank_cutoff = ffc.derive_rank_cutoff(pool, settings)
    rows = build_player_capital(pool, distributions, rank_cutoff)
    teams = by_team(rows)
    total = sum(t.capital for t in teams) or 1.0

    ranked = []
    for i, t in enumerate(teams, start=1):
        best = t.best
        ranked.append({
            "rank": i,
            "team": t.team,
            "capital": round(t.capital, 1),
            "share": t.capital / total,
            "counts": {str(k): t.count_within(k) for k in COUNT_THRESHOLDS},
            "n_players": len(t.players),
            "best_player": best.name if best else None,
            "best_adp": best.adp if best else None,
            "players": [
                {
                    "name": p.name, "position": p.position, "adp": p.adp,
                    "position_rank": p.position_rank,
                    "expected": round(p.expected_points, 1),
                    "replacement": round(p.replacement_points, 1),
                    "value": round(p.value, 1),
                }
                for p in t.players
            ],
        })

    caps = [t.capital for t in teams]
    counts100 = [float(t.count_within(100)) for t in teams]
    return {
        "year": year,
        "source": "Fantasy Football Calculator consensus ADP",
        "teams_in_league_settings": settings.teams,
        "scoring": "PPR" if settings.scoring.reception == 1.0 else "non-PPR",
        "rank_cutoff": rank_cutoff,
        "replacement_levels": {
            k: round(v, 1) for k, v in replacement_levels(distributions, rank_cutoff).items()
        },
        "total_capital": round(total, 1),
        "spearman_weighted_vs_top100_count": round(spearman(caps, counts100), 3),
        "rows": ranked,
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    report = compute(args.year, args.data_dir)
    print(f"{args.year} ADP share -- {report['source']}\n")
    print(f"{'#':>3}  {'TM':<4} {'share':>7} {'capital':>8} "
          f"{'t24':>4}{'t50':>4}{'t100':>5}   best player")
    for r in report["rows"]:
        c = r["counts"]
        print(f"{r['rank']:>3}  {r['team']:<4} {r['share']:>6.1%} {r['capital']:>8.1f} "
              f"{c['24']:>4}{c['50']:>4}{c['100']:>5}   {r['best_player']} ({r['best_adp']})")
    print(f"\nSpearman(weighted, top-100 count) = "
          f"{report['spearman_weighted_vs_top100_count']}")

    if args.out:
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
