"""Can the draft engine's objective consume player-specific variance sensibly?

WHY THIS HAS TO RUN BEFORE ANY VARIANCE MODEL IS BUILT

The obvious next step after the residual analysis is a player-specific variance
model feeding the Monte Carlo: two receivers at the same ADP get different
spreads, the simulator sees the difference, better picks follow. That plan has a
latent flaw, and it is cheaper to find here than after building the model.

`roster.starting_lineup_value` sorts a roster by points and takes the top N per
slot. That is a **selection operator**, and it is convex. By Jensen's
inequality, increasing a player's outcome variance while holding his mean fixed
*increases* the expected starting-lineup value: the lineup captures his upside
and the bench truncates his downside.

So the objective is not variance-neutral, and it is not variance-averse. It is
structurally **variance-seeking** -- and not because anyone decided it should
be. Today that bias is invisible, because every player in a rank bucket shares
one distribution, so the effect is roughly uniform and cancels out of the
comparison between positions. Introduce genuine player-specific variance and it
stops cancelling.

The deeper cause is that the simulator draws one SEASON TOTAL per player and
then picks the lineup with perfect hindsight. A real manager sets lineups weekly
without knowing season totals and cannot retroactively bench a bust. Season
totals plus hindsight-optimal lineup selection is what makes variance look like
free upside.

WHAT THIS MEASURES

A mean-preserving spread: multiply every outcome's deviation from its bucket
mean by a factor, leaving the mean *exactly* unchanged. Any movement in expected
starting-lineup value is therefore attributable to variance alone, with the
point estimate held fixed by construction.

The number that matters is the size of the artifact relative to the real signal.
The residual analysis found a genuine dispersion difference of roughly 13 points
of standard deviation between high- and low-risk players at the same ADP. If a
comparable spread moves the objective by substantially more than the model's
whole measured edge, then feeding real variance into this objective would swamp
the signal with the artifact -- and the resulting backtest would look like
"variance doesn't help" when the truth is "the objective cannot use it".

Usage:
    python -m fantasyprep.research.variance_sensitivity
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path

from fantasyprep.draft_sim.points_model import HistoricalBootstrapModel
from fantasyprep.draft_sim.simulate import recommend_positions, state_from_picks
from fantasyprep.historical.outcomes import OutcomeDistribution, _load_cached
from fantasyprep.historical.sources import ffc
from fantasyprep.league.settings import LeagueSettings, default_settings

DEFAULT_OUT_PATH = Path("data/historical/variance_sensitivity.json")
SPREAD_FACTORS = (1.0, 1.25, 1.5, 2.0)


def mean_preserving_spread(
    distributions: dict[tuple[str, int], OutcomeDistribution], position: str, factor: float
) -> dict[tuple[str, int], OutcomeDistribution]:
    """Scale deviations from the bucket mean, leaving the mean untouched.

    The mean is preserved exactly rather than approximately, which is what lets
    any movement in the objective be attributed to variance alone.
    """
    out: dict[tuple[str, int], OutcomeDistribution] = {}
    for key, dist in distributions.items():
        if key[0] != position:
            out[key] = dist
            continue
        mean = statistics.mean(dist.outcomes)
        out[key] = OutcomeDistribution(
            position=dist.position,
            bucket=dist.bucket,
            outcomes=[mean + (o - mean) * factor for o in dist.outcomes],
        )
    return out


def run(
    year: int = 2024,
    slot: int = 5,
    position: str = "WR",
    num_sims: int = 400,
    seed: int = 0,
    settings: LeagueSettings | None = None,
    data_dir: Path = Path("data"),
) -> dict:
    settings = settings or default_settings()
    raw_dir = data_dir / "raw"
    pool = ffc.fetch_adp(year, teams=settings.teams, cache_path=raw_dir / f".ffc_{settings.teams}_{year}.json")
    base = _load_cached(raw_dir / f".outcomes_{settings.teams}.json")
    state = state_from_picks(settings.teams, slot, [])

    rows = []
    for factor in SPREAD_FACTORS:
        distributions = (
            base if factor == 1.0 else mean_preserving_spread(base, position, factor)
        )
        # Same seed every time: the only thing varying across conditions is the
        # spread factor, so any movement is the effect under test rather than
        # simulation noise.
        recommendation = recommend_positions(
            pool, state, settings, HistoricalBootstrapModel(distributions),
            num_sims=num_sims, rng=random.Random(seed),
        )
        by_position = {pos: mean for pos, mean, _, _ in recommendation}
        rows.append({
            "spread_factor": factor,
            "top_recommendation": recommendation[0][0],
            "expected_value_by_position": {k: round(v, 1) for k, v in by_position.items()},
            "spread_position_value": round(by_position[position], 1),
        })

    baseline = rows[0]
    return {
        "year": year, "slot": slot, "spread_position": position,
        "num_sims": num_sims, "seed": seed,
        "baseline_top_recommendation": baseline["top_recommendation"],
        "conditions": rows,
        "verdict": _verdict(rows, position),
    }


def _verdict(rows: list[dict], position: str) -> dict:
    baseline = rows[0]
    flipped = [r for r in rows[1:] if r["top_recommendation"] != baseline["top_recommendation"]]
    gains = {
        r["spread_factor"]: round(r["spread_position_value"] - baseline["spread_position_value"], 1)
        for r in rows[1:]
    }
    return {
        "objective_is_variance_seeking": bool(gains and max(gains.values()) > 0),
        "value_gain_from_pure_variance": gains,
        "recommendation_flipped_at_factors": [r["spread_factor"] for r in flipped],
        "note": (
            f"Mean held exactly fixed, so every point of gain is variance alone. "
            f"A flipped recommendation means the objective preferred {position} "
            f"purely for being more volatile."
        ),
    }


def summarize(results: dict) -> str:
    lines = [
        f"{results['year']} pool, {results['slot']}th of "
        f"{len(results['conditions'])} conditions, mean-preserving spread on "
        f"{results['spread_position']} only",
        "",
        f"  {'spread':>7s}  {'top':5s}  expected starting-lineup value by position",
    ]
    for row in results["conditions"]:
        values = "  ".join(
            f"{pos}={val:7.1f}" for pos, val in row["expected_value_by_position"].items()
        )
        lines.append(f"  {row['spread_factor']:7.2f}  {row['top_recommendation']:5s}  {values}")

    verdict = results["verdict"]
    lines.append("")
    lines.append(f"  variance-seeking: {verdict['objective_is_variance_seeking']}")
    lines.append(f"  value gained from pure variance: {verdict['value_gain_from_pure_variance']}")
    lines.append(
        f"  recommendation flipped at factors: "
        f"{verdict['recommendation_flipped_at_factors'] or 'none'}"
    )
    return "\n".join(lines)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--slot", type=int, default=5)
    parser.add_argument("--position", type=str, default="WR")
    parser.add_argument("--num-sims", type=int, default=400)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    args = parser.parse_args(argv)

    results = run(year=args.year, slot=args.slot, position=args.position, num_sims=args.num_sims)
    print(summarize(results))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
