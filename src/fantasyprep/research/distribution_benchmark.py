"""Can a player-profile model produce better-CALIBRATED outcome distributions
than FantasyPrep's current ADP-rank buckets?

WHY THIS IS THE RIGHT NEXT EXPERIMENT

The point-estimate benchmark (`research/benchmark.py`) established that the
market already does most of the point-estimation work: prior production adds
+0.0075 r-squared on top of ADP. So chasing a better *median* is chasing a
nearly-exhausted margin.

What ADP does not give you is the shape. Two receivers can carry the same ADP
and radically different downside, and that difference is exactly what should
change a draft decision. So the scoreboard here is calibration, not accuracy:

    If the model says P10 = 128, then ~10% of actual outcomes should land
    below 128. If it says P90 = 247, ~10% should land above.

A model with a worse median but honest tails is more useful to a draft engine
than one with a sharp median and overconfident tails, because the Monte Carlo
consumes the whole distribution.

THREE ARMS

  A  adp_bucket        what FantasyPrep does today -- ADP positional rank ->
                       3-wide bucket -> empirical distribution of real outcomes
  B  adp_prior_bucket  the same, additionally conditioned on prior-season finish
  C  profile_quantile  quantile regression on the full player profile

Arm A is deliberately the incumbent rather than a strawman. If C cannot beat
the system already in production, it does not deserve to go near the simulator
-- that is the whole point of scoring it this way, and it is what stops a more
sophisticated model from shipping on the strength of being more sophisticated.

METRICS

  coverage      share of actuals below each predicted quantile; should equal
                the nominal level. The headline calibration number.
  pinball loss  the proper scoring rule for quantiles -- rewards being both
                calibrated AND sharp, so a model can't win by predicting
                absurdly wide intervals.
  CRPS          approximated by integrating pinball loss across the quantile
                grid; one number for overall distributional quality.
  median error  MAE of P50, so the point-estimate cost of any calibration gain
                stays visible rather than hidden.

Leakage: walk-forward. Predicting season Y uses only seasons strictly before Y
for buckets, model fitting, imputation and scaling alike.

Usage:
    python -m fantasyprep.research.distribution_benchmark
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from fantasyprep.historical.outcomes import BUCKET_WIDTH, bucket_for_rank
from fantasyprep.research.benchmark import (
    DEFAULT_TEST_SEASONS,
    TARGET,
    _design_matrix,
    build_modeling_frame,
)

DEFAULT_OUT_PATH = Path("data/historical/distribution_benchmark.json")

# Spread wide enough to actually probe the tails -- the part of the
# distribution the current bucket system is least likely to get right, and the
# part a draft engine most needs (a bust floor changes a pick; a median rarely
# does).
QUANTILES = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
HEADLINE_QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)

# A bucket needs enough real seasons to have a believable 5th percentile.
# Same rationale and value as historical/outcomes.MIN_BUCKET_SAMPLES.
MIN_BUCKET_SAMPLES = 20

# Prior-finish buckets are deliberately coarser than ADP buckets. Conditioning
# on two rank axes at 3-wide each would shatter the sample into cells of two or
# three seasons, which is the exact failure the tail-pooling fix just repaired.
PRIOR_BUCKET_WIDTH = 6

PROFILE_FEATURES = [
    "adp", "adp_position_rank", "adp_stdev",
    "prev_fantasy_points", "prev_fantasy_points_per_game", "prev_games",
    "prev_position_rank", "prev_position_percentile",
    "prev_targets", "prev_carries", "prev_target_share", "prev_wopr",
    "prev_yards_per_target", "prev_catch_rate",
    "age", "seasons_since_rookie_year", "seasons_of_history", "draft_pick",
]


# --------------------------------------------------------------------------
# empirical bucket arms
# --------------------------------------------------------------------------

def _bucket_keys(row: pd.Series, use_prior: bool) -> list[tuple]:
    """Most specific key first, then progressively coarser fallbacks.

    An explicit fallback chain rather than a single key, because the specific
    cell is often empty and the honest response is to widen the conditioning
    until there is enough real data -- not to emit a distribution built from
    three seasons.
    """
    position = row["fantasy_position"]
    adp_bucket = bucket_for_rank(int(row["adp_position_rank"])) if pd.notna(
        row.get("adp_position_rank")
    ) else None

    keys: list[tuple] = []
    if use_prior and adp_bucket is not None and pd.notna(row.get("prev_position_rank")):
        prior_bucket = (int(row["prev_position_rank"]) - 1) // PRIOR_BUCKET_WIDTH
        keys.append((position, adp_bucket, prior_bucket))
    if adp_bucket is not None:
        keys.append((position, adp_bucket))
    keys.append((position,))
    return keys


def _build_bucket_index(train: pd.DataFrame, use_prior: bool) -> dict[tuple, list[float]]:
    index: dict[tuple, list[float]] = defaultdict(list)
    for row in train.itertuples():
        series = pd.Series(row._asdict())
        outcome = float(series[TARGET])
        for key in _bucket_keys(series, use_prior):
            index[key].append(outcome)
    return index


def predict_empirical_quantiles(
    train: pd.DataFrame, test: pd.DataFrame, use_prior: bool
) -> np.ndarray:
    """Empirical quantiles of real historical outcomes in the player's bucket.

    This is what the production simulator effectively samples from, so arm A is
    the incumbent measured on its own terms rather than a caricature of it.
    """
    index = _build_bucket_index(train, use_prior)

    predictions = np.empty((len(test), len(QUANTILES)))
    for i, row in enumerate(test.itertuples()):
        series = pd.Series(row._asdict())
        outcomes = None
        for key in _bucket_keys(series, use_prior):
            candidate = index.get(key)
            if candidate and len(candidate) >= MIN_BUCKET_SAMPLES:
                outcomes = candidate
                break
        if outcomes is None:
            outcomes = index.get((series["fantasy_position"],), [0.0])
        predictions[i] = np.quantile(outcomes, QUANTILES)
    return predictions


# --------------------------------------------------------------------------
# model arm
# --------------------------------------------------------------------------

def predict_model_quantiles(
    train: pd.DataFrame, test: pd.DataFrame, features: list[str]
) -> np.ndarray:
    """Gradient-boosted quantile regression, one fit per quantile.

    Quantile loss directly, rather than fitting a mean and assuming a noise
    shape around it -- the assumed-shape approach is precisely what makes
    conventional projections overconfident in the tails, since fantasy outcomes
    are strongly right-skewed and nothing like Gaussian.
    """
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.impute import SimpleImputer

    x_train = _design_matrix(train, features)
    x_test = _design_matrix(test, features).reindex(columns=x_train.columns, fill_value=0.0)

    imputer = SimpleImputer(strategy="median").fit(x_train)
    x_train_i, x_test_i = imputer.transform(x_train), imputer.transform(x_test)
    y_train = train[TARGET].to_numpy(dtype=float)

    predictions = np.empty((len(test), len(QUANTILES)))
    for j, q in enumerate(QUANTILES):
        model = GradientBoostingRegressor(
            loss="quantile", alpha=q, n_estimators=200, max_depth=3,
            learning_rate=0.05, random_state=0,
        )
        model.fit(x_train_i, y_train)
        predictions[:, j] = model.predict(x_test_i)

    # Quantiles fitted independently can cross (P75 below P50) since nothing
    # ties the separate fits together. Sorting each row restores monotonicity,
    # the standard remedy, and never makes calibration worse.
    return np.sort(predictions, axis=1)


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def pinball_loss(actual: np.ndarray, predicted: np.ndarray, q: float) -> float:
    delta = actual - predicted
    return float(np.mean(np.maximum(q * delta, (q - 1) * delta)))


def score_distribution(actual: np.ndarray, predicted: np.ndarray) -> dict:
    """Calibration first, then sharpness-aware proper scores."""
    if len(actual) < 10:
        return {"n": int(len(actual))}

    coverage, pinball = {}, {}
    for j, q in enumerate(QUANTILES):
        coverage[f"p{int(q * 100)}"] = round(float(np.mean(actual <= predicted[:, j])), 4)
        pinball[f"p{int(q * 100)}"] = round(pinball_loss(actual, predicted[:, j], q), 4)

    # Coverage error on the headline quantiles: one number for "how honest are
    # the stated percentiles", which is the question the whole exercise asks.
    headline_error = float(
        np.mean([
            abs(coverage[f"p{int(q * 100)}"] - q) for q in HEADLINE_QUANTILES
        ])
    )

    median_index = QUANTILES.index(0.50)
    # CRPS by integrating pinball loss over the quantile grid (Laio & Tamea).
    # Approximate at 7 quantiles, but computed identically for every arm, so
    # the comparison between arms is fair even though the absolute value is not
    # exact.
    crps = 2.0 * float(np.mean([pinball[f"p{int(q * 100)}"] for q in QUANTILES]))

    return {
        "n": int(len(actual)),
        "coverage": coverage,
        "mean_abs_coverage_error": round(headline_error, 4),
        "pinball": pinball,
        "mean_pinball": round(float(np.mean(list(pinball.values()))), 4),
        "crps": round(crps, 4),
        "median_mae": round(float(np.mean(np.abs(actual - predicted[:, median_index]))), 2),
        "mean_interval_width_p10_p90": round(
            float(np.mean(predicted[:, QUANTILES.index(0.90)] - predicted[:, QUANTILES.index(0.10)])), 2
        ),
    }


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

ARMS = ("adp_bucket", "adp_prior_bucket", "profile_quantile")


def run(frame: pd.DataFrame, test_seasons: list[int] | None = None) -> dict:
    test_seasons = test_seasons or DEFAULT_TEST_SEASONS

    records = []
    for season in test_seasons:
        train = frame[(frame["season"] < season) & frame["has_adp"]]
        test = frame[(frame["season"] == season) & frame["has_adp"]]
        if len(train) < 200 or test.empty:
            continue

        predictions = {
            "adp_bucket": predict_empirical_quantiles(train, test, use_prior=False),
            "adp_prior_bucket": predict_empirical_quantiles(train, test, use_prior=True),
            "profile_quantile": predict_model_quantiles(train, test, PROFILE_FEATURES),
        }
        base = test[["player_id", "player_name", "season", "fantasy_position",
                     TARGET, "is_rookie"]].reset_index(drop=True)
        for arm, matrix in predictions.items():
            for j, q in enumerate(QUANTILES):
                base[f"{arm}__p{int(q * 100)}"] = matrix[:, j]
        records.append(base)
        print(f"  scored {season} ({len(test)} players)")

    all_predictions = pd.concat(records, ignore_index=True)
    return {
        "test_seasons": test_seasons,
        "quantiles": list(QUANTILES),
        "n_scored": len(all_predictions),
        "overall": _score_all(all_predictions),
        "veterans": _score_all(all_predictions[~all_predictions["is_rookie"]]),
        "rookies": _score_all(all_predictions[all_predictions["is_rookie"]]),
        "by_position": {
            str(position): _score_all(group)
            for position, group in all_predictions.groupby("fantasy_position")
        },
        "by_season": {
            int(season): _score_all(group)
            for season, group in all_predictions.groupby("season")
        },
    }


def _score_all(subset: pd.DataFrame) -> dict:
    actual = subset[TARGET].to_numpy(dtype=float)
    scores = {}
    for arm in ARMS:
        columns = [f"{arm}__p{int(q * 100)}" for q in QUANTILES]
        if all(c in subset.columns for c in columns):
            scores[arm] = score_distribution(actual, subset[columns].to_numpy(dtype=float))
    return scores


def summarize(results: dict) -> str:
    lines = [f"Scored {results['n_scored']:,} player-seasons "
             f"({results['test_seasons'][0]}-{results['test_seasons'][-1]})"]

    for label, key in [("OVERALL", "overall"), ("Veterans", "veterans"), ("Rookies", "rookies")]:
        block = results.get(key) or {}
        if not block:
            continue
        lines.append("")
        lines.append(label)
        lines.append(f"  {'arm':18s} {'n':>5s} {'covErr':>7s} {'CRPS':>8s} "
                     f"{'pinball':>8s} {'medMAE':>7s} {'P10-P90':>8s}")
        for arm in ARMS:
            s = block.get(arm)
            if not s or "crps" not in s:
                continue
            lines.append(
                f"  {arm:18s} {s['n']:5d} {s['mean_abs_coverage_error']:7.4f} "
                f"{s['crps']:8.3f} {s['mean_pinball']:8.3f} {s['median_mae']:7.2f} "
                f"{s['mean_interval_width_p10_p90']:8.1f}"
            )

    overall = results.get("overall") or {}
    lines.append("")
    lines.append("Coverage detail, overall (nominal -> actual; closer is better)")
    header = "  ".join(f"p{int(q*100):<3d}" for q in HEADLINE_QUANTILES)
    lines.append(f"  {'arm':18s} {header}")
    lines.append(f"  {'nominal':18s} " + "  ".join(f"{q:<4.2f}" for q in HEADLINE_QUANTILES))
    for arm in ARMS:
        s = overall.get(arm)
        if not s or "coverage" not in s:
            continue
        values = "  ".join(f"{s['coverage'][f'p{int(q*100)}']:<4.2f}" for q in HEADLINE_QUANTILES)
        lines.append(f"  {arm:18s} {values}")
    return "\n".join(lines)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument("--test-seasons", type=int, nargs="+", default=DEFAULT_TEST_SEASONS)
    args = parser.parse_args(argv)

    frame, _ = build_modeling_frame()
    print("Running distribution benchmark...")
    results = run(frame, args.test_seasons)
    print()
    print(summarize(results))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
