"""Recalibrate the empirical outcome distributions the simulator samples from.

THE DEFECT

The distribution benchmark found the incumbent buckets well calibrated on
average (1.5pp coverage error) but under-covering the upside. Diagnosing it by
segment shows the problem is not uniform, and the segment that matters most is
the worst:

    ADP tier   p50    p75    p90    p95      (nominal 0.50/0.75/0.90/0.95)
    1-6        0.46   0.64   0.80   0.85
    7-12       0.47   0.72   0.85   0.90
    13-24      0.46   0.72   0.87   0.93
    25-48      0.51   0.74   0.89   0.93
    49+        0.57   0.81   0.91   0.95

An elite player beats his bucket's stated P90 about **20%** of the time instead
of 10%. Deep players are fine, occasionally over-covered. So the system
systematically understates the upside of exactly the picks that decide a draft.

Globally both tails are compressed (p5 covers 0.07, p95 covers 0.92), which is
the expected finite-sample behaviour: with ~45 real seasons in a bucket, the
empirical extreme quantiles simply cannot reach the true tails. The tier pattern
sits on top of that, because early-round outcomes are far more right-skewed --
a first-round pick has a genuine ceiling that a bucket median cannot express.

THE FIX

Quantile recalibration via the probability integral transform, which needs no
distributional assumption and directly targets the measured defect.

For each training player, compute the PIT value: the fraction of his bucket's
historical outcomes at or below what he actually scored. If the distributions
were calibrated, PIT values would be Uniform(0,1). They are not, and their
empirical CDF `G` is exactly the miscalibration.

Coverage of the raw quantile at level q' is `G(q')`. So to emit a quantile whose
true coverage is q, read the raw distribution at level `G^-1(q)` instead. Fit
`G` on strictly-prior seasons, apply it out of sample.

Deliberately kept as a thin layer over the incumbent rather than a replacement.
The empirical buckets are simple, interpretable, already integrated and
historically validated; the measured problem is a squeeze in the tails, and the
proportionate fix is to unsqueeze them, not to swap the engine.

Usage:
    python -m fantasyprep.research.calibration
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from fantasyprep.research.benchmark import DEFAULT_TEST_SEASONS, TARGET, build_modeling_frame
from fantasyprep.research.distribution_benchmark import (
    MIN_BUCKET_SAMPLES,
    QUANTILES,
    _bucket_keys,
    _build_bucket_index,
    score_distribution,
)

DEFAULT_OUT_PATH = Path("data/historical/calibration.json")

# The grid `G` is estimated on. Finer than QUANTILES so the inverse mapping is
# smooth; the tails are what need fixing, so the grid reaches further out.
PIT_GRID = np.linspace(0.01, 0.99, 99)

# Tiers for the conditional variant. Boundaries follow the diagnosis above:
# the damage is concentrated inside the top 24, and deep players are already
# fine, so a split that lumped 1-6 with 49+ would average the fix away.
TIER_EDGES = [0, 6, 12, 24, 48, 10_000]
TIER_LABELS = ["1-6", "7-12", "13-24", "25-48", "49+"]


def assign_tier(adp_position_rank: pd.Series) -> pd.Series:
    return pd.cut(adp_position_rank, bins=TIER_EDGES, labels=TIER_LABELS)


def _bucket_outcomes(index: dict, row: pd.Series) -> list[float] | None:
    for key in _bucket_keys(row, use_prior=False):
        candidate = index.get(key)
        if candidate and len(candidate) >= MIN_BUCKET_SAMPLES:
            return candidate
    return index.get((row["fantasy_position"],))


def pit_values(train: pd.DataFrame, target: pd.DataFrame) -> np.ndarray:
    """Fraction of each player's bucket at or below what he actually scored.

    Uniform(0,1) if the distributions are honest; any departure from uniform is
    the miscalibration this module corrects.
    """
    index = _build_bucket_index(train, use_prior=False)
    values = []
    for row in target.itertuples():
        series = pd.Series(row._asdict())
        outcomes = _bucket_outcomes(index, series)
        if not outcomes:
            continue
        values.append(float(np.mean(np.asarray(outcomes) <= series[TARGET])))
    return np.asarray(values)


def fit_recalibration(pit: np.ndarray) -> dict[float, float]:
    """Map desired coverage q -> raw quantile level to read instead.

    `G` is the empirical CDF of the PIT values; the correction is its inverse.
    Falls back to the identity when there is too little data to estimate it,
    so a thin segment degrades to the incumbent rather than to noise.
    """
    if len(pit) < 100:
        return {q: q for q in QUANTILES}

    coverage_of_level = np.array([float(np.mean(pit <= level)) for level in PIT_GRID])
    mapping = {}
    for q in QUANTILES:
        # Smallest raw level whose observed coverage reaches q.
        reached = np.nonzero(coverage_of_level >= q)[0]
        mapping[q] = float(PIT_GRID[reached[0]]) if len(reached) else 0.99
    return mapping


def anchor_median(mapping: dict[float, float]) -> dict[float, float]:
    """Keep the median where it is; only widen the tails around it.

    The plain recalibration above corrects marginal coverage, but because the
    PIT distribution is skewed rather than merely compressed, it does so partly
    by SHIFTING the whole distribution -- measured: it fixes p90 (0.87 -> 0.89)
    while pushing the median off (0.50 -> 0.53). Moving the median is exactly
    what should not happen here, since the point estimate is the part the
    market already gets right.

    This variant re-centres the mapping so q=0.50 stays at 0.50 and the
    correction is applied only to the distance from the median outward. Tails
    unsqueeze, centre stays put.
    """
    median_level = mapping.get(0.50, 0.50)
    anchored = {}
    for q, level in mapping.items():
        if q == 0.50:
            anchored[q] = 0.50
        elif q > 0.50:
            # Rescale the upper half so it still spans to 1.0 from the anchor.
            span = (1.0 - median_level) or 1e-9
            anchored[q] = 0.50 + (level - median_level) / span * 0.50
        else:
            span = median_level or 1e-9
            anchored[q] = 0.50 - (median_level - level) / span * 0.50
    return {q: float(min(max(v, 0.001), 0.999)) for q, v in anchored.items()}


# Bandwidth in ADP-rank units for the smooth correction. Wide enough that every
# estimate draws on well over a hundred effective observations, narrow enough
# that rank 3 and rank 40 get visibly different corrections.
RANK_BANDWIDTH = 12.0


def fit_smooth_recalibration(
    pit: np.ndarray, ranks: np.ndarray, target_rank: float, bandwidth: float = RANK_BANDWIDTH
) -> dict[float, float]:
    """Recalibration estimated locally in ADP rank, with no binning.

    This is the synthesis of the whole research arc. Conditioning the correction
    on rank clearly helps -- the defect is four times worse at tier 1-6 than at
    25-48 -- but every attempt so far to condition by *splitting* the sample has
    lost to pooling, three times running (2D buckets, rookie specialisation,
    per-tier recalibration).

    Kernel weighting resolves the tension. Every training observation
    contributes to every estimate, weighted by how close its ADP rank is to the
    player being predicted. Nothing is discarded and no cell can go empty, yet
    the correction still varies continuously with rank. It is conditioning
    without fragmenting.
    """
    if len(pit) < 100:
        return {q: q for q in QUANTILES}

    weights = np.exp(-(((ranks - target_rank) / bandwidth) ** 2))
    total = weights.sum()
    if total <= 0:
        return {q: q for q in QUANTILES}

    # Weighted empirical CDF of PIT at each grid level.
    coverage_of_level = np.array(
        [float((weights * (pit <= level)).sum() / total) for level in PIT_GRID]
    )
    mapping = {}
    for q in QUANTILES:
        reached = np.nonzero(coverage_of_level >= q)[0]
        mapping[q] = float(PIT_GRID[reached[0]]) if len(reached) else 0.99
    return anchor_median(mapping)


def apply_smooth_recalibration(
    train: pd.DataFrame, test: pd.DataFrame, pit: np.ndarray, pit_ranks: np.ndarray
) -> np.ndarray:
    index = _build_bucket_index(train, use_prior=False)
    predictions = np.empty((len(test), len(QUANTILES)))
    cache: dict[int, dict[float, float]] = {}

    for i, row in enumerate(test.itertuples()):
        series = pd.Series(row._asdict())
        outcomes = _bucket_outcomes(index, series) or [0.0]
        rank = int(series["adp_position_rank"]) if pd.notna(series.get("adp_position_rank")) else 999
        if rank not in cache:
            cache[rank] = fit_smooth_recalibration(pit, pit_ranks, float(rank))
        levels = [cache[rank][q] for q in QUANTILES]
        predictions[i] = np.quantile(outcomes, levels)
    return np.sort(predictions, axis=1)


def apply_recalibration(
    train: pd.DataFrame, test: pd.DataFrame, mapping_for: dict | None
) -> np.ndarray:
    """Empirical quantiles read at recalibrated levels.

    `mapping_for` is either one global mapping or a dict of per-tier mappings;
    passing None reproduces the incumbent exactly, which is what makes the
    comparison below a like-for-like measurement of the layer's effect.
    """
    index = _build_bucket_index(train, use_prior=False)
    predictions = np.empty((len(test), len(QUANTILES)))

    for i, row in enumerate(test.itertuples()):
        series = pd.Series(row._asdict())
        outcomes = _bucket_outcomes(index, series) or [0.0]

        if mapping_for is None:
            levels = list(QUANTILES)
        elif isinstance(next(iter(mapping_for.values())), dict):
            tier = series.get("tier")
            mapping = mapping_for.get(tier) or {q: q for q in QUANTILES}
            levels = [mapping[q] for q in QUANTILES]
        else:
            levels = [mapping_for[q] for q in QUANTILES]

        predictions[i] = np.quantile(outcomes, levels)
    # Recalibrated levels are monotone in q, but a coarse PIT grid can tie two
    # adjacent levels; sorting keeps the emitted quantiles non-crossing.
    return np.sort(predictions, axis=1)


def run(frame: pd.DataFrame, test_seasons: list[int] | None = None) -> dict:
    test_seasons = test_seasons or DEFAULT_TEST_SEASONS
    frame = frame.copy()
    frame["tier"] = assign_tier(frame["adp_position_rank"])

    blocks = []
    for season in test_seasons:
        train = frame[(frame["season"] < season) & frame["has_adp"]]
        test = frame[(frame["season"] == season) & frame["has_adp"]]
        if len(train) < 200 or test.empty:
            continue

        # G is fitted on training seasons only, scored against distributions
        # that themselves only use seasons before each training row's season.
        pit_train = _walk_forward_pit(frame, train)
        global_mapping = fit_recalibration(pit_train["pit"].to_numpy())
        tier_mapping = {
            str(tier): fit_recalibration(group["pit"].to_numpy())
            for tier, group in pit_train.groupby("tier", observed=True)
        }

        block = test[["player_id", "season", "fantasy_position", TARGET, "tier",
                      "is_rookie", "adp_position_rank"]].reset_index(drop=True).copy()
        for label, mapping in [
            ("incumbent", None),
            ("global", global_mapping),
            ("per_tier", tier_mapping),
            ("tail_only", anchor_median(global_mapping)),
        ]:
            matrix = apply_recalibration(train, test, mapping)
            for j, q in enumerate(QUANTILES):
                block[f"{label}__p{int(q * 100)}"] = matrix[:, j]

        smooth = apply_smooth_recalibration(
            train, test,
            pit_train["pit"].to_numpy(dtype=float),
            pd.to_numeric(pit_train["adp_position_rank"], errors="coerce").fillna(999).to_numpy(dtype=float),
        )
        for j, q in enumerate(QUANTILES):
            block[f"smooth__p{int(q * 100)}"] = smooth[:, j]
        blocks.append(block)
        print(f"  scored {season} ({len(test)} players)")

    combined = pd.concat(blocks, ignore_index=True)
    return {
        "test_seasons": test_seasons,
        "n_scored": len(combined),
        "overall": _score(combined),
        "by_tier": {
            str(tier): _score(group)
            for tier, group in combined.groupby("tier", observed=True)
        },
        "coverage_by_tier": _coverage_table(combined),
    }


def _walk_forward_pit(frame: pd.DataFrame, train: pd.DataFrame) -> pd.DataFrame:
    """PIT values for training rows, each scored against strictly-prior seasons.

    Scoring a training row against a distribution that already contains it would
    make the PIT values look far more uniform than they are, and the fitted
    correction would then be far too weak.
    """
    rows = []
    for season in sorted(train["season"].unique()):
        prior = frame[(frame["season"] < season) & frame["has_adp"]]
        current = train[train["season"] == season]
        if len(prior) < 100 or current.empty:
            continue
        values = pit_values(prior, current)
        if len(values) != len(current):
            continue
        block = current.reset_index(drop=True)[["tier", "adp_position_rank"]].copy()
        block["pit"] = values
        rows.append(block)
    return (pd.concat(rows, ignore_index=True) if rows
            else pd.DataFrame(columns=["tier", "adp_position_rank", "pit"]))


ARMS = ("incumbent", "global", "per_tier", "tail_only", "smooth")


def _score(subset: pd.DataFrame) -> dict:
    actual = subset[TARGET].to_numpy(dtype=float)
    scores = {}
    for arm in ARMS:
        columns = [f"{arm}__p{int(q * 100)}" for q in QUANTILES]
        if all(c in subset.columns for c in columns):
            scores[arm] = score_distribution(actual, subset[columns].to_numpy(dtype=float))
    return scores


def _coverage_table(combined: pd.DataFrame) -> dict:
    table = {}
    for tier, group in combined.groupby("tier", observed=True):
        table[str(tier)] = {
            arm: {
                f"p{int(q * 100)}": round(
                    float((group[TARGET] <= group[f"{arm}__p{int(q * 100)}"]).mean()), 3
                )
                for q in QUANTILES
            }
            for arm in ARMS
        }
    return table


def summarize(results: dict) -> str:
    lines = [f"Scored {results['n_scored']:,} player-seasons"]
    for label, block in [("OVERALL", results["overall"])] + [
        (f"tier {t}", b) for t, b in results["by_tier"].items()
    ]:
        if not block:
            continue
        lines.append("")
        lines.append(label)
        lines.append(f"  {'arm':12s} {'n':>5s} {'covErr':>8s} {'CRPS':>8s} {'pinball':>8s}")
        for arm in ARMS:
            s = block.get(arm)
            if not s or "crps" not in s:
                continue
            lines.append(f"  {arm:12s} {s['n']:5d} {s['mean_abs_coverage_error']:8.4f} "
                         f"{s['crps']:8.3f} {s['mean_pinball']:8.3f}")

    lines.append("")
    lines.append("Upper-tail coverage by tier (nominal p75=0.75, p90=0.90)")
    lines.append(f"  {'tier':8s} {'arm':12s} {'p75':>6s} {'p90':>6s} {'p95':>6s}")
    for tier, arms in results["coverage_by_tier"].items():
        for arm in ARMS:
            c = arms[arm]
            lines.append(f"  {tier:8s} {arm:12s} {c['p75']:6.2f} {c['p90']:6.2f} {c['p95']:6.2f}")
    return "\n".join(lines)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    args = parser.parse_args(argv)

    frame, _ = build_modeling_frame()
    print("Running calibration comparison...")
    results = run(frame)
    print()
    print(summarize(results))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
