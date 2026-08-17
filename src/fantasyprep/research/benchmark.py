"""Does draft-time ADP tell us anything a player's own history doesn't?

THE STRATEGIC QUESTION

FFC PPR ADP floors at 2010, so 9 of the dataset's 26 seasons have no market
rank at all (docs/HISTORICAL_ADP_RESEARCH.md). The obvious response is to go
acquire pre-2010 ADP from somewhere else, which is a large project. This
benchmark exists to find out whether that project is worth starting, *before*
starting it.

Three arms, each predicting the same held-out season's fantasy points:

  A  history  -- prior production, opportunity, age, draft capital. No market.
  B  market   -- ADP and positional ADP rank. No player history.
  C  both     -- the union.

If C barely beats A, ADP is largely redundant with what we already know for
free across all 26 seasons, and the acquisition project is not worth starting.
If C clearly beats A, it is.

TWO METHODOLOGICAL TRAPS THIS DELIBERATELY AVOIDS

1. **The arms do not cover the same players.** The market arm can only score
   players who have an ADP (~170/season); the history arm can only score
   players with a prior season (~500/season, excluding *every rookie by
   construction*). Scoring each arm on its own population and comparing the
   numbers would be meaningless -- the easier population would win. So the
   headline comparison runs on the **intersection**, and each arm's own
   coverage is reported separately rather than silently averaged in.

2. **Rookies are split out, not averaged away.** A rookie has an ADP and no
   history at all, so he is exactly where ADP *must* carry unique information.
   Averaging rookies into one overall number would smear ADP's real
   contribution across a population where most players have four years of
   history. Reported separately, the answer becomes actionable: if ADP's edge
   lives entirely in rookies, "do we need more ADP?" becomes "only if we care
   about rookies."

LEAKAGE

Walk-forward: predicting season Y trains only on seasons strictly before Y --
the same discipline `backtest.leakage_safe_distributions` uses. Features come
exclusively from `PRE_SEASON_COLUMNS`, which fails closed, and the imputer and
scaler are fit on training seasons only, never on the test season.

Models are deliberately interpretable -- ridge regression and a mean-of-bucket
baseline, not gradient boosting. The question is whether the *information* is
there, not how much a flexible learner can squeeze out of it, and a weaker model
that answers the real question beats a stronger one that obscures it.

Usage:
    python -m fantasyprep.research.benchmark
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_FEATURES_PATH = Path("data/historical/player_season_features.parquet")
DEFAULT_OUT_PATH = Path("data/historical/adp_vs_history_benchmark.json")

# Matches DEFAULT_BACKTEST_YEARS. Every one has >=5 strictly-prior seasons of
# ADP (2010+) to train the market arm on, so no arm is handicapped by a thin
# training set in the early test years.
DEFAULT_TEST_SEASONS = list(range(2015, 2025))

TARGET = "fantasy_points"

HISTORY_FEATURES = [
    "prev_fantasy_points",
    "prev_fantasy_points_per_game",
    "prev_games",
    "prev_position_rank",
    "prev_position_percentile",
    "prev_targets",
    "prev_carries",
    "prev_target_share",
    "prev_wopr",
    "prev_yards_per_target",
    "prev_catch_rate",
    "prev_fantasy_points_per_opportunity",
    "age",
    "seasons_since_rookie_year",
    "seasons_of_history",
    "draft_pick",
]

MARKET_FEATURES = ["adp", "adp_position_rank", "adp_stdev"]

ARMS = {
    "history": HISTORY_FEATURES,
    "market": MARKET_FEATURES,
    "both": HISTORY_FEATURES + MARKET_FEATURES,
}


def _position_dummies(df: pd.DataFrame) -> pd.DataFrame:
    """One-hot position, included in every arm. Without it a model can't know
    that 300 points means something different at QB than at TE, and the arms
    would differ by more than the information under test."""
    return pd.get_dummies(df["fantasy_position"], prefix="pos").astype(float)


def _design_matrix(df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    present = [c for c in features if c in df.columns]
    numeric = df[present].apply(pd.to_numeric, errors="coerce")
    # A missingness indicator alongside the imputed value: "we don't know this
    # player's prior target share" is itself informative (he may not have
    # played), and imputing silently would throw that away.
    missing = numeric.isna().astype(float).add_suffix("__missing")
    missing = missing.loc[:, missing.std() > 0]
    return pd.concat([numeric, missing, _position_dummies(df)], axis=1)


def fit_predict_ridge(
    train: pd.DataFrame, test: pd.DataFrame, features: list[str], alpha: float = 10.0
) -> np.ndarray:
    """Ridge on standardized features, imputer and scaler fit on train only."""
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    x_train = _design_matrix(train, features)
    x_test = _design_matrix(test, features).reindex(columns=x_train.columns, fill_value=0.0)

    model = make_pipeline(
        SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=alpha)
    )
    model.fit(x_train, train[TARGET])
    return model.predict(x_test)


def evaluate(actual: pd.Series, predicted: np.ndarray) -> dict:
    """Rank correlation first, because drafting is an ordering problem.

    A model that is systematically 20 points low but orders every player
    correctly is perfectly useful for a draft; one with great RMSE that
    scrambles the order is not. Absolute-error metrics are reported too, since
    they matter once the distributions feed the Monte Carlo.
    """
    from scipy import stats

    actual_values = np.asarray(actual, dtype=float)
    mask = ~np.isnan(actual_values) & ~np.isnan(predicted)
    actual_values, predicted = actual_values[mask], predicted[mask]
    if len(actual_values) < 3:
        return {"n": int(len(actual_values))}

    residual = actual_values - predicted
    total = actual_values - actual_values.mean()
    return {
        "n": int(len(actual_values)),
        "spearman": round(float(stats.spearmanr(actual_values, predicted).statistic), 4),
        "pearson": round(float(stats.pearsonr(actual_values, predicted).statistic), 4),
        "mae": round(float(np.abs(residual).mean()), 2),
        "rmse": round(float(np.sqrt((residual**2).mean())), 2),
        "r2": round(float(1 - (residual**2).sum() / (total**2).sum()), 4),
    }


def build_modeling_frame(
    features_path: Path = DEFAULT_FEATURES_PATH, cache_dir: Path | None = None
) -> tuple[pd.DataFrame, dict]:
    """Feature table plus ADP, restricted to rows with a real outcome."""
    from fantasyprep.historical.dataset.market import attach_adp

    features = pd.read_parquet(features_path)
    frame, match_report = attach_adp(features, cache_dir=cache_dir)

    frame = frame[frame[TARGET].notna()].copy()
    frame["is_rookie"] = frame["prev_fantasy_points"].isna()
    return frame, match_report


def run_benchmark(
    frame: pd.DataFrame, test_seasons: list[int] | None = None, alpha: float = 10.0
) -> dict:
    """Walk-forward: each test season is predicted from strictly prior seasons."""
    test_seasons = test_seasons or DEFAULT_TEST_SEASONS

    predictions = []
    for season in test_seasons:
        train = frame[frame["season"] < season]
        test = frame[frame["season"] == season]
        if train.empty or test.empty:
            continue

        # ALL arms train on the drafted-player population, not just the market
        # arm. This matters more than it looks, and getting it wrong overstates
        # ADP's advantage badly.
        #
        # The market arm is *forced* onto this population -- a row with no ADP
        # has no market features to learn from. If the history arm were left to
        # train on every row, the two would differ by training distribution as
        # well as by information, and the market arm would get a free advantage
        # from a training set that better matches the drafted-player test set.
        #
        # Measured, not assumed: training history on all prior rows scores
        # r2 0.2812, and on the matched population 0.3661. Two thirds of the
        # apparent gap to the market arm was that artefact rather than
        # information. The matched comparison below is the honest one; the
        # unmatched variant is kept as a reported robustness line.
        matched_train = train[train["has_adp"]]
        if len(matched_train) < 50:
            continue

        season_predictions = test[
            ["player_id", "player_name", "season", "fantasy_position", TARGET,
             "has_adp", "is_rookie"]
        ].copy()
        for arm, arm_features in ARMS.items():
            season_predictions[f"pred_{arm}"] = fit_predict_ridge(
                matched_train, test, arm_features, alpha=alpha
            )
        # Robustness line: history's best-available model, using every prior row
        # rather than only drafted ones. Not part of the headline comparison.
        season_predictions["pred_history_all_rows"] = fit_predict_ridge(
            train, test, HISTORY_FEATURES, alpha=alpha
        )
        predictions.append(season_predictions)

    if not predictions:
        raise ValueError("No test seasons produced predictions -- check the input frame.")

    all_predictions = pd.concat(predictions, ignore_index=True)
    common = all_predictions[all_predictions["has_adp"] & ~all_predictions["is_rookie"]]

    # Each subset reports only the arms that are actually *applicable* to it.
    # Scoring the market arm on players who have no ADP measures nothing about
    # ADP -- every market feature is imputed to the median, so the model
    # confidently predicts a drafted-player outcome for an undrafted player and
    # posts a wildly negative R-squared. That number is an artefact of applying
    # a model outside its domain, not a finding, so it isn't reported.
    return {
        "test_seasons": test_seasons,
        "populations": _population_counts(all_predictions),
        "headline_common_population": _score_subset(common),
        "incremental_value": _incremental_value(common),
        "rookies_with_adp": _score_subset(
            all_predictions[all_predictions["is_rookie"] & all_predictions["has_adp"]]
        ),
        "rookies_without_adp": _score_subset(
            all_predictions[all_predictions["is_rookie"] & ~all_predictions["has_adp"]],
            arms=("history",),
        ),
        "veterans_without_adp": _score_subset(
            all_predictions[~all_predictions["has_adp"] & ~all_predictions["is_rookie"]],
            arms=("history",),
        ),
        "robustness_history_trained_on_all_rows": evaluate(
            common[TARGET], common["pred_history_all_rows"].to_numpy(dtype=float)
        ),
        "each_arm_own_population": _own_population_scores(all_predictions),
        "by_position": _by_position(common),
        "by_season": _by_season(common),
    }


def _incremental_value(common: pd.DataFrame) -> dict:
    """What each information source adds *on top of* the other.

    The strategically decisive numbers. "Does ADP beat history" is interesting;
    "does ADP add anything history doesn't already have" is the question that
    decides whether the pre-2010 ADP acquisition project is worth starting.
    """
    scores = _score_subset(common)
    if not all(arm in scores and "spearman" in scores[arm] for arm in ARMS):
        return {}
    return {
        "adp_adds_over_history": {
            "spearman": round(scores["both"]["spearman"] - scores["history"]["spearman"], 4),
            "r2": round(scores["both"]["r2"] - scores["history"]["r2"], 4),
            "mae": round(scores["both"]["mae"] - scores["history"]["mae"], 2),
        },
        "history_adds_over_adp": {
            "spearman": round(scores["both"]["spearman"] - scores["market"]["spearman"], 4),
            "r2": round(scores["both"]["r2"] - scores["market"]["r2"], 4),
            "mae": round(scores["both"]["mae"] - scores["market"]["mae"], 2),
        },
    }


def _population_counts(predictions: pd.DataFrame) -> dict:
    return {
        "total_player_seasons": len(predictions),
        "with_adp": int(predictions["has_adp"].sum()),
        "rookies": int(predictions["is_rookie"].sum()),
        "rookies_with_adp": int((predictions["is_rookie"] & predictions["has_adp"]).sum()),
        "common_population_adp_and_history": int(
            (predictions["has_adp"] & ~predictions["is_rookie"]).sum()
        ),
    }


def _score_subset(subset: pd.DataFrame, arms: tuple[str, ...] | None = None) -> dict:
    scores = {}
    for arm in arms or tuple(ARMS):
        column = f"pred_{arm}"
        if column in subset.columns:
            scores[arm] = evaluate(subset[TARGET], subset[column].to_numpy(dtype=float))
    return scores


def _own_population_scores(predictions: pd.DataFrame) -> dict:
    """Each arm on every row it can actually score -- reported alongside the
    headline so the coverage difference is visible rather than hidden."""
    history_rows = predictions[~predictions["is_rookie"]]
    market_rows = predictions[predictions["has_adp"]]
    return {
        "history_on_all_returning_players": evaluate(
            history_rows[TARGET], history_rows["pred_history_all_rows"].to_numpy(dtype=float)
        ),
        "market_on_all_players_with_adp": evaluate(
            market_rows[TARGET], market_rows["pred_market"].to_numpy(dtype=float)
        ),
    }


def _by_position(subset: pd.DataFrame) -> dict:
    return {
        str(position): _score_subset(group)
        for position, group in subset.groupby("fantasy_position")
    }


def _by_season(subset: pd.DataFrame) -> dict:
    return {int(season): _score_subset(group) for season, group in subset.groupby("season")}


def summarize(results: dict) -> str:
    lines = []
    pop = results["populations"]
    lines.append("Populations")
    lines.append(f"  total player-seasons scored : {pop['total_player_seasons']:,}")
    lines.append(f"  with ADP                    : {pop['with_adp']:,}")
    lines.append(f"  rookies                     : {pop['rookies']:,}")
    lines.append(f"  common (ADP + prior season) : {pop['common_population_adp_and_history']:,}")

    for label, key in [
        ("HEADLINE -- common population (has ADP and a prior season)", "headline_common_population"),
        ("Rookies WITH ADP (market applies, prior production does not)", "rookies_with_adp"),
        ("Rookies without ADP (history arm = age + draft capital only)", "rookies_without_adp"),
        ("Veterans with no ADP (market never drafted them)", "veterans_without_adp"),
    ]:
        block = results[key]
        if not block:
            continue
        lines.append("")
        lines.append(label)
        lines.append(f"  {'arm':10s} {'n':>6s} {'spearman':>9s} {'r2':>8s} {'mae':>8s} {'rmse':>8s}")
        for arm in ARMS:
            s = block.get(arm)
            if not s or "spearman" not in s:
                continue
            lines.append(
                f"  {arm:10s} {s['n']:6d} {s['spearman']:9.4f} {s['r2']:8.4f} "
                f"{s['mae']:8.2f} {s['rmse']:8.2f}"
            )

    rob = results.get("robustness_history_trained_on_all_rows")
    if rob and "spearman" in rob:
        lines.append("")
        lines.append("Robustness: history trained on ALL prior rows instead of the matched")
        lines.append("population (its best-available model, but an unmatched comparison)")
        lines.append(
            f"  {'history*':10s} {rob['n']:6d} {rob['spearman']:9.4f} {rob['r2']:8.4f} "
            f"{rob['mae']:8.2f} {rob['rmse']:8.2f}"
        )

    inc = results.get("incremental_value")
    if inc:
        lines.append("")
        lines.append("INCREMENTAL VALUE on the common population")
        a, h = inc["adp_adds_over_history"], inc["history_adds_over_adp"]
        lines.append(
            f"  ADP added on top of history : spearman {a['spearman']:+.4f}  "
            f"r2 {a['r2']:+.4f}  mae {a['mae']:+.2f}"
        )
        lines.append(
            f"  History added on top of ADP : spearman {h['spearman']:+.4f}  "
            f"r2 {h['r2']:+.4f}  mae {h['mae']:+.2f}"
        )
    return "\n".join(lines)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=DEFAULT_FEATURES_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument("--alpha", type=float, default=10.0)
    parser.add_argument("--test-seasons", type=int, nargs="+", default=DEFAULT_TEST_SEASONS)
    args = parser.parse_args(argv)

    frame, match_report = build_modeling_frame(args.features)
    print(f"ADP join: {match_report['overall_match_rate']:.1%} of "
          f"{match_report['adp_entries']:,} ADP entries matched a player-season\n")

    results = run_benchmark(frame, args.test_seasons, alpha=args.alpha)
    results["adp_match_report"] = match_report
    print(summarize(results))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
