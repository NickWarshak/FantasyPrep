"""Does a better distribution actually change a draft pick?

THE GATING QUESTION

Every distributional result so far has been statistical: CRPS down 3.3%,
coverage error on early picks nearly halved. None of that matters to a draft
engine unless it changes what the engine *does*. A 3% CRPS gain that never flips
a recommendation is worth exactly nothing at the draft table.

So this runs before any further modeling investment, and it is deliberately
cheap. It does not measure whether the new distributions are BETTER -- the
calibration study already did that on held-out seasons. It measures whether
there is a channel at all: recompute real recommendations at real draft states
under the incumbent distributions and under the recalibrated ones, and count how
often the top recommendation differs.

Three outcomes, each of which redirects the roadmap differently:

  ~0% changed   the channel is closed. No distributional refinement can affect
                decisions, and the whole vNext programme needs rethinking before
                another hour goes into it.
  a few %       plausible, and the changed picks are then the only ones worth
                backtesting -- comparing full-season outcomes when 97% of picks
                are identical would drown a real effect in shared noise.
  many %        the engine is highly sensitive to distribution shape, which is
                itself a warning: small modeling errors would move real
                decisions a lot.

APPLYING A RECALIBRATION TO A BOOTSTRAP DISTRIBUTION

The simulator samples real outcomes from a list, so a quantile correction cannot
be applied directly. Instead the corrected distribution is *resampled*: evaluate
the bucket's empirical quantile function at recalibrated levels across a uniform
grid. The result is a list of the same kind the simulator already consumes,
whose shape carries the correction -- no change to the sampling machinery at all.

Usage:
    python -m fantasyprep.research.decision_impact
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import replace
from pathlib import Path

import numpy as np

from fantasyprep.draft_sim.points_model import HistoricalBootstrapModel
from fantasyprep.draft_sim.simulate import recommend_positions, state_from_picks
from fantasyprep.historical.outcomes import BUCKET_WIDTH, OutcomeDistribution, _load_cached
from fantasyprep.historical.sources import ffc
from fantasyprep.league.settings import LeagueSettings, default_settings

DEFAULT_OUT_PATH = Path("data/historical/decision_impact.json")

# How many points to resample each corrected bucket onto. Comfortably above the
# 40-45 real seasons a bucket holds, so the resampling itself does not coarsen
# the distribution it is meant to preserve.
RESAMPLE_N = 400


def recalibrated_outcomes(outcomes: list[float], mapping: dict[float, float]) -> list[float]:
    """Resample a bucket so its shape carries the recalibration.

    The mapping is defined at a handful of quantile levels; it is interpolated
    across a uniform grid so the resampled distribution comes out smooth rather
    than as a seven-step staircase.
    """
    if len(outcomes) < 2:
        return list(outcomes)

    levels = sorted(mapping)
    corrected = [mapping[q] for q in levels]
    grid = np.linspace(0.0, 1.0, RESAMPLE_N)
    # Anchor both ends so the corrected tails still reach the observed extremes.
    mapped = np.interp(grid, [0.0] + levels + [1.0], [0.0] + corrected + [1.0])
    return [float(v) for v in np.quantile(np.asarray(outcomes, dtype=float), mapped)]


def apply_to_distributions(
    distributions: dict[tuple[str, int], OutcomeDistribution], mapping_for_rank
) -> dict[tuple[str, int], OutcomeDistribution]:
    """Rebuild every bucket under a rank-dependent recalibration."""
    out = {}
    for (position, bucket), dist in distributions.items():
        representative_rank = bucket * BUCKET_WIDTH + 1
        mapping = mapping_for_rank(float(representative_rank))
        out[(position, bucket)] = OutcomeDistribution(
            position=position,
            bucket=bucket,
            outcomes=recalibrated_outcomes(dist.outcomes, mapping),
        )
    return out


def my_pick(teams: int, slot: int, round_index: int) -> int:
    """Snake-draft pick number for my slot in a given round (0-indexed)."""
    if round_index % 2 == 0:
        return round_index * teams + slot
    return round_index * teams + (teams - slot + 1)


def run(
    year: int = 2024,
    slots: list[int] | None = None,
    rounds: int = 6,
    num_sims: int = 300,
    seed: int = 0,
    settings: LeagueSettings | None = None,
    data_dir: Path = Path("data"),
) -> dict:
    from fantasyprep.research.calibration import fit_smooth_recalibration

    settings = settings or default_settings()
    slots = slots or list(range(1, 11))
    raw_dir = data_dir / "raw"

    pool = ffc.fetch_adp(
        year, teams=settings.teams, cache_path=raw_dir / f".ffc_{settings.teams}_{year}.json"
    )
    incumbent = _load_cached(raw_dir / f".outcomes_{settings.teams}.json")

    pit, ranks = training_pit()
    corrected = apply_to_distributions(
        incumbent, lambda rank: fit_smooth_recalibration(pit, ranks, rank)
    )

    incumbent_model = HistoricalBootstrapModel(incumbent)
    corrected_model = HistoricalBootstrapModel(corrected)

    comparisons = []
    for slot in slots:
        for round_index in range(rounds):
            pick = my_pick(settings.teams, slot, round_index)
            base_state = state_from_picks(settings.teams, slot, [])
            state = replace(base_state, current_pick=pick)

            # Common random numbers: the same seed for both arms, so any
            # difference is the distributions rather than simulation noise.
            base = recommend_positions(
                pool, state, settings, incumbent_model, num_sims, random.Random(seed)
            )
            new = recommend_positions(
                pool, state, settings, corrected_model, num_sims, random.Random(seed)
            )
            if not base or not new:
                continue
            comparisons.append({
                "slot": slot,
                "round": round_index + 1,
                "pick": pick,
                "incumbent_top": base[0][0],
                "corrected_top": new[0][0],
                "changed": base[0][0] != new[0][0],
                "incumbent_margin": round(base[0][1] - base[1][1], 2) if len(base) > 1 else None,
                "corrected_margin": round(new[0][1] - new[1][1], 2) if len(new) > 1 else None,
            })
        print(f"  slot {slot} done ({len(comparisons)} decisions so far)")

    return _report(comparisons, year, num_sims, seed)


def _report(comparisons: list[dict], year: int, num_sims: int, seed: int) -> dict:
    changed = [c for c in comparisons if c["changed"]]
    margins = [c["incumbent_margin"] for c in comparisons if c["incumbent_margin"] is not None]
    changed_margins = [c["incumbent_margin"] for c in changed if c["incumbent_margin"] is not None]

    return {
        "year": year,
        "num_sims": num_sims,
        "seed": seed,
        "n_decisions": len(comparisons),
        "n_changed": len(changed),
        "share_changed": round(len(changed) / len(comparisons), 4) if comparisons else 0.0,
        "median_margin_all": round(float(np.median(margins)), 2) if margins else None,
        "median_margin_changed": (
            round(float(np.median(changed_margins)), 2) if changed_margins else None
        ),
        "decisions_by_round": {
            str(r): sum(1 for c in comparisons if c["round"] == r)
            for r in sorted({c["round"] for c in comparisons})
        },
        "changed_by_round": {
            str(r): sum(1 for c in changed if c["round"] == r)
            for r in sorted({c["round"] for c in comparisons})
        },
        "comparisons": comparisons,
    }


def training_pit():
    """PIT values and their ADP ranks, for fitting the smooth correction.

    Each season is scored against strictly-prior seasons only, the same
    discipline the calibration study uses -- a correction fitted on
    distributions that already contain the row would look far too weak.
    """
    import pandas as pd

    from fantasyprep.research.benchmark import build_modeling_frame
    from fantasyprep.research.calibration import pit_values

    frame, _ = build_modeling_frame()
    frame = frame[frame["has_adp"]].copy()

    pit_all, rank_all = [], []
    for season in sorted(frame["season"].unique()):
        prior = frame[frame["season"] < season]
        current = frame[frame["season"] == season]
        if len(prior) < 100 or current.empty:
            continue
        values = pit_values(prior, current)
        if len(values) != len(current):
            continue
        pit_all.append(values)
        rank_all.append(
            pd.to_numeric(current["adp_position_rank"], errors="coerce").fillna(999).to_numpy()
        )
    return np.concatenate(pit_all), np.concatenate(rank_all)


def summarize(results: dict) -> str:
    lines = [
        f"{results['year']}, {results['n_decisions']} real draft decisions "
        f"({results['num_sims']} sims each)",
        "",
        f"  recommendations changed : {results['n_changed']} "
        f"({results['share_changed']:.1%})",
        f"  median decision margin  : {results['median_margin_all']}",
        f"  ...on changed decisions : {results['median_margin_changed']}",
        "",
        "  by round:",
    ]
    for round_number, total in results["decisions_by_round"].items():
        changed = results["changed_by_round"].get(round_number, 0)
        lines.append(f"    round {round_number}: {changed}/{total} changed")
    return "\n".join(lines)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--num-sims", type=int, default=300)
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    args = parser.parse_args(argv)

    results = run(year=args.year, num_sims=args.num_sims, rounds=args.rounds)
    print()
    print(summarize(results))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
