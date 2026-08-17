"""Do two players at the same ADP have predictably different RISK?

THE QUESTION THAT MATTERS FOR A DRAFT

The two benchmarks before this one converged on an uncomfortable result: the
market prices the median well (history adds +0.0075 r-squared on top of ADP),
and the incumbent bucket system is already well calibrated (1.5pp coverage
error). A better central estimate is a nearly-exhausted margin.

But calibration measured *on average* can hide the thing a draft engine most
needs. A system can be perfectly calibrated across all receivers while being
systematically overconfident about 30-year-olds and underconfident about
second-year breakouts -- the errors cancel in aggregate and mislead on every
individual pick.

So this asks two separate questions about the residual (actual minus
ADP-implied expectation):

  1. LEVEL     -- who systematically beats or misses their ADP? If this is
                  predictable, the market has an exploitable bias.
  2. DISPERSION -- whose outcome is systematically harder to predict? If |residual|
                  is predictable from a preseason profile, then two players at
                  identical ADP carry genuinely different risk, and the engine
                  should say so.

Question 2 is the more valuable one, and the less obvious. A finding of "no
exploitable level bias, but strongly predictable dispersion" would be the ideal
result for this project: it means the market is efficient about *where* to draft
a player and silent about *how risky* he is, which is exactly the gap a Monte
Carlo draft engine is built to exploit.

Leakage: the ADP-implied expectation for season Y is built only from seasons
strictly before Y, and the dispersion model is fit the same way.

Usage:
    python -m fantasyprep.research.residual_analysis
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from fantasyprep.research.benchmark import (
    DEFAULT_TEST_SEASONS,
    TARGET,
    _design_matrix,
    build_modeling_frame,
)
from fantasyprep.research.distribution_benchmark import (
    QUANTILES,
    predict_empirical_quantiles,
)

DEFAULT_OUT_PATH = Path("data/historical/residual_analysis.json")

# Preseason characteristics tested as explanations for beating/missing ADP and
# for being hard to predict at all.
PROFILE_FEATURES = [
    "age", "seasons_since_rookie_year", "seasons_of_history", "draft_pick",
    "adp", "adp_position_rank", "adp_stdev",
    "prev_fantasy_points", "prev_fantasy_points_per_game", "prev_games",
    "prev_position_rank", "prev_targets", "prev_carries",
    "prev_target_share", "prev_wopr", "prev_yards_per_target", "prev_catch_rate",
]


def build_residuals(frame: pd.DataFrame, test_seasons: list[int] | None = None) -> pd.DataFrame:
    """Actual minus leakage-safe ADP-implied expectation, per player-season.

    The expectation is the median of the same empirical ADP bucket the
    production simulator samples from, so the residual is literally "how wrong
    was FantasyPrep's current view of this player".
    """
    test_seasons = test_seasons or DEFAULT_TEST_SEASONS
    median_index = QUANTILES.index(0.50)

    records = []
    for season in test_seasons:
        train = frame[(frame["season"] < season) & frame["has_adp"]]
        test = frame[(frame["season"] == season) & frame["has_adp"]]
        if len(train) < 200 or test.empty:
            continue

        predicted = predict_empirical_quantiles(train, test, use_prior=False)
        block = test.reset_index(drop=True).copy()
        block["adp_expectation"] = predicted[:, median_index]
        block["residual"] = block[TARGET] - block["adp_expectation"]
        block["abs_residual"] = block["residual"].abs()
        records.append(block)

    return pd.concat(records, ignore_index=True)


def feature_associations(residuals: pd.DataFrame) -> dict:
    """Rank correlation of each preseason feature with residual level and
    with residual magnitude.

    Spearman rather than Pearson: these relationships are monotone-but-curved
    (aging isn't linear) and fantasy outcomes are heavily right-skewed, so a
    Pearson coefficient would understate real structure and be dragged by
    outliers.
    """
    from scipy import stats

    associations = {}
    for feature in PROFILE_FEATURES:
        if feature not in residuals.columns:
            continue
        column = pd.to_numeric(residuals[feature], errors="coerce")
        mask = column.notna()
        if mask.sum() < 100:
            continue
        associations[feature] = {
            "n": int(mask.sum()),
            "vs_residual_level": round(
                float(stats.spearmanr(column[mask], residuals["residual"][mask]).statistic), 4
            ),
            "vs_residual_magnitude": round(
                float(stats.spearmanr(column[mask], residuals["abs_residual"][mask]).statistic), 4
            ),
        }
    return associations


def dispersion_experiment(
    frame: pd.DataFrame, test_seasons: list[int] | None = None
) -> dict:
    """Can we predict, before the season, WHICH players will be hard to predict?

    Fits |residual| on the preseason profile using only strictly-prior seasons,
    then splits the held-out season into predicted-risky and predicted-safe
    halves and reports what actually happened to each.

    The decisive number is the actual spread of each half. If the market alone
    determined risk, both halves would show the same spread -- the split would
    be noise. A real gap means risk is a separable, predictable property.
    """
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.impute import SimpleImputer

    test_seasons = test_seasons or DEFAULT_TEST_SEASONS
    median_index = QUANTILES.index(0.50)

    blocks = []
    for season in test_seasons:
        train_raw = frame[(frame["season"] < season) & frame["has_adp"]]
        test = frame[(frame["season"] == season) & frame["has_adp"]]
        if len(train_raw) < 300 or test.empty:
            continue

        # Training targets must themselves be leakage-safe: each training row's
        # |residual| is computed against an expectation built only from seasons
        # before *that* row's season, never from the whole training window.
        train = _training_residuals(frame, train_raw, median_index)
        if len(train) < 200:
            continue

        x_train = _design_matrix(train, PROFILE_FEATURES)
        x_test = _design_matrix(test, PROFILE_FEATURES).reindex(
            columns=x_train.columns, fill_value=0.0
        )
        imputer = SimpleImputer(strategy="median").fit(x_train)

        model = GradientBoostingRegressor(
            n_estimators=200, max_depth=3, learning_rate=0.05, random_state=0
        )
        model.fit(imputer.transform(x_train), train["abs_residual"])

        predicted = predict_empirical_quantiles(train_raw, test, use_prior=False)
        block = test.reset_index(drop=True).copy()
        block["adp_expectation"] = predicted[:, median_index]
        block["residual"] = block[TARGET] - block["adp_expectation"]
        block["abs_residual"] = block["residual"].abs()
        block["predicted_risk"] = model.predict(imputer.transform(x_test))
        blocks.append(block)

    combined = pd.concat(blocks, ignore_index=True)
    return _dispersion_report(combined)


def _training_residuals(
    frame: pd.DataFrame, train_raw: pd.DataFrame, median_index: int
) -> pd.DataFrame:
    """|residual| labels for training rows, each computed leakage-safely."""
    rows = []
    for season in sorted(train_raw["season"].unique()):
        prior = frame[(frame["season"] < season) & frame["has_adp"]]
        current = train_raw[train_raw["season"] == season]
        if len(prior) < 100 or current.empty:
            continue
        predicted = predict_empirical_quantiles(prior, current, use_prior=False)
        block = current.reset_index(drop=True).copy()
        block["abs_residual"] = (block[TARGET] - predicted[:, median_index]).abs()
        rows.append(block)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _dispersion_report(combined: pd.DataFrame) -> dict:
    from scipy import stats

    predicted_risk = combined["predicted_risk"]
    actual = combined["abs_residual"]

    median_split = predicted_risk.median()
    risky = combined[predicted_risk >= median_split]
    safe = combined[predicted_risk < median_split]

    # Within-ADP-tier comparison: the strong form of the claim. Comparing risky
    # against safe across all players would be trivially confounded, since deep
    # sleepers obviously vary more than first-round picks. Splitting *inside*
    # each ADP tier holds market expectation roughly fixed, so any remaining
    # gap is information the market rank alone did not carry.
    combined = combined.copy()
    combined["adp_tier"] = pd.cut(
        combined["adp_position_rank"], bins=[0, 6, 12, 24, 48, 999],
        labels=["1-6", "7-12", "13-24", "25-48", "49+"],
    )
    within_tier = {}
    for tier, group in combined.groupby("adp_tier", observed=True):
        if len(group) < 60:
            continue
        split = group["predicted_risk"].median()
        high, low = group[group["predicted_risk"] >= split], group[group["predicted_risk"] < split]
        within_tier[str(tier)] = {
            "n_high_risk": len(high), "n_low_risk": len(low),
            "actual_stdev_high_risk": round(float(high["residual"].std()), 1),
            "actual_stdev_low_risk": round(float(low["residual"].std()), 1),
            "actual_mean_abs_residual_high": round(float(high["abs_residual"].mean()), 1),
            "actual_mean_abs_residual_low": round(float(low["abs_residual"].mean()), 1),
        }

    return {
        "n": len(combined),
        "spearman_predicted_vs_actual_magnitude": round(
            float(stats.spearmanr(predicted_risk, actual).statistic), 4
        ),
        "pooled_early_round_bootstrap": _bootstrap_stdev_gap(combined),
        "median_split": {
            "predicted_risky_half": {
                "n": len(risky),
                "actual_mean_abs_residual": round(float(risky["abs_residual"].mean()), 1),
                "actual_residual_stdev": round(float(risky["residual"].std()), 1),
            },
            "predicted_safe_half": {
                "n": len(safe),
                "actual_mean_abs_residual": round(float(safe["abs_residual"].mean()), 1),
                "actual_residual_stdev": round(float(safe["residual"].std()), 1),
            },
        },
        "within_adp_tier": within_tier,
    }


EARLY_ROUND_RANK_CUTOFF = 24
BOOTSTRAP_RESAMPLES = 5000


def _bootstrap_stdev_gap(combined: pd.DataFrame, seed: int = 0) -> dict:
    """Is the risky/safe spread gap real, or resampling noise?

    Pooled across the early-round tiers, splitting *within* each tier so market
    rank stays held down. Pooling is necessary rather than cosmetic: no single
    tier carries enough player-seasons for its own interval to exclude zero,
    and reporting a per-tier point estimate without saying so would overstate
    the finding considerably.

    Deep ranks are excluded because the signal measurably is not there -- past
    rank 24 the gap goes slightly negative with intervals spanning zero, which
    is a real boundary on the finding and not a reason to keep hunting.
    """
    early = combined[combined["adp_position_rank"] <= EARLY_ROUND_RANK_CUTOFF].copy()
    if len(early) < 100:
        return {}

    tiers = pd.cut(early["adp_position_rank"], bins=[0, 6, 12, 24])
    split = early.groupby(tiers, observed=True)["predicted_risk"].transform("median")
    high = early[early["predicted_risk"] >= split]["residual"].to_numpy(dtype=float)
    low = early[early["predicted_risk"] < split]["residual"].to_numpy(dtype=float)
    if len(high) < 30 or len(low) < 30:
        return {}

    rng = np.random.default_rng(seed)
    observed = float(high.std(ddof=1) - low.std(ddof=1))
    draws = np.array([
        rng.choice(high, len(high), replace=True).std(ddof=1)
        - rng.choice(low, len(low), replace=True).std(ddof=1)
        for _ in range(BOOTSTRAP_RESAMPLES)
    ])
    ci_low, ci_high = np.percentile(draws, [2.5, 97.5])
    return {
        "rank_cutoff": EARLY_ROUND_RANK_CUTOFF,
        "n_high_risk": len(high),
        "n_low_risk": len(low),
        "observed_stdev_gap": round(observed, 1),
        "ci_low": round(float(ci_low), 1),
        "ci_high": round(float(ci_high), 1),
        "excludes_zero": bool(ci_low > 0 or ci_high < 0),
    }


def summarize(associations: dict, dispersion: dict, residuals: pd.DataFrame) -> str:
    lines = ["Residual = actual - leakage-safe ADP-bucket expectation",
             f"  n = {len(residuals):,}   mean = {residuals['residual'].mean():+.1f}   "
             f"stdev = {residuals['residual'].std():.1f}"]

    ranked = sorted(
        associations.items(), key=lambda kv: -abs(kv[1]["vs_residual_magnitude"])
    )
    lines.append("")
    lines.append("Feature association with residual (Spearman)")
    lines.append(f"  {'feature':32s} {'level':>8s} {'magnitude':>10s}")
    for feature, stats_ in ranked[:12]:
        lines.append(
            f"  {feature:32s} {stats_['vs_residual_level']:+8.4f} "
            f"{stats_['vs_residual_magnitude']:+10.4f}"
        )

    lines.append("")
    lines.append("Can we predict WHO is hard to predict?")
    lines.append(f"  spearman(predicted risk, actual |residual|) = "
                 f"{dispersion['spearman_predicted_vs_actual_magnitude']:+.4f}")
    split = dispersion["median_split"]
    lines.append(f"  predicted-risky half : mean |resid| "
                 f"{split['predicted_risky_half']['actual_mean_abs_residual']:6.1f}   "
                 f"stdev {split['predicted_risky_half']['actual_residual_stdev']:6.1f}"
                 f"   (n={split['predicted_risky_half']['n']})")
    lines.append(f"  predicted-safe  half : mean |resid| "
                 f"{split['predicted_safe_half']['actual_mean_abs_residual']:6.1f}   "
                 f"stdev {split['predicted_safe_half']['actual_residual_stdev']:6.1f}"
                 f"   (n={split['predicted_safe_half']['n']})")

    boot = dispersion.get("pooled_early_round_bootstrap")
    if boot:
        verdict = "REAL (CI excludes 0)" if boot["excludes_zero"] else "not distinguishable from 0"
        lines.append("")
        lines.append(f"  Pooled top-{boot['rank_cutoff']}, split within tier: stdev gap "
                     f"{boot['observed_stdev_gap']:+.1f} "
                     f"(95% CI {boot['ci_low']:+.1f} to {boot['ci_high']:+.1f}) -- {verdict}")
        lines.append("  No detectable signal beyond that rank -- see the tier table below.")

    if dispersion["within_adp_tier"]:
        lines.append("")
        lines.append("Within ADP tier -- market rank held roughly fixed")
        lines.append(f"  {'tier':8s} {'n hi/lo':>10s} {'stdev hi':>9s} {'stdev lo':>9s} "
                     f"{'|res| hi':>9s} {'|res| lo':>9s}")
        for tier, s in dispersion["within_adp_tier"].items():
            counts = f"{s['n_high_risk']}/{s['n_low_risk']}"
            lines.append(
                f"  {tier:8s} {counts:>10s} "
                f"{s['actual_stdev_high_risk']:9.1f} {s['actual_stdev_low_risk']:9.1f} "
                f"{s['actual_mean_abs_residual_high']:9.1f} {s['actual_mean_abs_residual_low']:9.1f}"
            )
    return "\n".join(lines)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    args = parser.parse_args(argv)

    frame, _ = build_modeling_frame()
    print("Building leakage-safe residuals...")
    residuals = build_residuals(frame)
    associations = feature_associations(residuals)

    print("Running dispersion experiment...")
    dispersion = dispersion_experiment(frame)

    print()
    print(summarize(associations, dispersion, residuals))

    payload = {
        "n_residuals": len(residuals),
        "residual_mean": round(float(residuals["residual"].mean()), 2),
        "residual_stdev": round(float(residuals["residual"].std()), 2),
        "feature_associations": associations,
        "dispersion": dispersion,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
