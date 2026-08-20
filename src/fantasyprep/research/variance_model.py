"""Is a player's WEEKLY volatility predictable before the season?

WHY THIS TARGET, AND WHY ONLY NOW

The residual analysis asked whether season-level dispersion was predictable and
found a real but modest signal (+13.0 stdev points across the top 24, CI
excluding zero). Acting on it was deliberately deferred, because
`research/lineup_hindsight.py` then showed the engine could not have used it
honestly: under hindsight scoring volatility looks like free upside (+76.6 at a
2x spread), while under realistic weekly management it is a genuine cost
(-21.8). The sign flips.

That flip also redefines the right target. What costs a real manager is not
uncertainty about a player's season *level* -- that mostly resolves into
"he was good" or "he wasn't" -- it is week-to-week volatility, because you must
commit a lineup every Sunday without knowing which weeks boom. A steady 14
points a week is worth more than an alternating 30/0, and the realistic scorer
now prices that difference correctly.

So this asks the question that is now actionable: **before the season, can we
tell who will be week-to-week volatile?**

Target: within-season standard deviation of weekly fantasy points, and its
scale-free sibling, the coefficient of variation. Coefficient of variation
matters because raw stdev is mechanically larger for high scorers -- a 300-point
receiver will out-vary a 100-point one almost regardless of consistency, and a
model predicting raw stdev would mostly be rediscovering ADP.

Leakage-safe walk-forward throughout: predicting season Y uses only seasons
strictly before Y.

Usage:
    python -m fantasyprep.research.variance_model
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import numpy as np
import pandas as pd

from fantasyprep.historical import weekly_stats
from fantasyprep.league.settings import default_settings
from fantasyprep.players.normalize import normalize_name
from fantasyprep.research.benchmark import DEFAULT_TEST_SEASONS, _design_matrix, evaluate

DEFAULT_OUT_PATH = Path("data/historical/variance_model.json")
WEEKLY_CACHE = Path("data/historical/weekly_volatility.parquet")

# A player needs enough real weeks for a volatility estimate to mean anything.
# Below this the sample standard deviation is mostly noise about itself.
MIN_WEEKS = 8

FEATURES = [
    "adp", "adp_position_rank", "adp_stdev",
    "age", "seasons_since_rookie_year", "draft_pick",
    "prev_fantasy_points_per_game", "prev_games", "prev_targets", "prev_carries",
    "prev_target_share", "prev_wopr", "prev_yards_per_target",
    "prev_weekly_stdev", "prev_weekly_cv",
]

TARGETS = ("weekly_stdev", "weekly_cv")


def build_weekly_volatility(
    seasons: list[int], cache_path: Path = WEEKLY_CACHE, force_refresh: bool = False
) -> pd.DataFrame:
    """Per player-season weekly volatility, cached because the pulls are slow."""
    if cache_path.exists() and not force_refresh:
        return pd.read_parquet(cache_path)

    scoring = default_settings().scoring
    rows = []
    for season in seasons:
        print(f"  pulling weekly {season}...")
        weekly = weekly_stats.weekly_points_by_player(season, scoring)
        for name, points in weekly.items():
            values = list(points.values())
            if len(values) < MIN_WEEKS:
                continue
            mean = statistics.mean(values)
            stdev = statistics.stdev(values)
            rows.append({
                "join_name": name,
                "season": season,
                "weeks_played": len(values),
                "weekly_mean": round(mean, 3),
                "weekly_stdev": round(stdev, 3),
                # Scale-free, so it is not just a proxy for how good the player is.
                "weekly_cv": round(stdev / mean, 4) if mean > 0 else np.nan,
            })

    frame = pd.DataFrame(rows)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(cache_path, index=False)
    return frame


def attach_volatility(modeling_frame: pd.DataFrame, volatility: pd.DataFrame) -> pd.DataFrame:
    """Join current-season volatility as the target, and prior-season volatility
    as a feature.

    The prior-season join is the interesting one: if week-to-week volatility is
    a stable player trait at all, last year's is the obvious predictor of this
    year's, and if it is not, that is itself the answer.
    """
    frame = modeling_frame.copy()
    frame["join_name"] = frame["player_name"].map(normalize_name)

    current = volatility.rename(columns={"weeks_played": "weeks_played_current"})
    frame = frame.merge(current, on=["join_name", "season"], how="left", validate="m:1")

    prior = volatility.copy()
    prior["season"] = prior["season"] + 1
    prior = prior[["join_name", "season", "weekly_stdev", "weekly_cv"]].rename(
        columns={"weekly_stdev": "prev_weekly_stdev", "weekly_cv": "prev_weekly_cv"}
    )
    return frame.merge(prior, on=["join_name", "season"], how="left", validate="m:1")


def run(frame: pd.DataFrame, test_seasons: list[int] | None = None) -> dict:
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.impute import SimpleImputer

    test_seasons = test_seasons or DEFAULT_TEST_SEASONS
    results: dict = {}

    for target in TARGETS:
        predictions = []
        for season in test_seasons:
            train = frame[(frame["season"] < season) & frame[target].notna() & frame["has_adp"]]
            test = frame[(frame["season"] == season) & frame[target].notna() & frame["has_adp"]]
            if len(train) < 150 or len(test) < 20:
                continue

            x_train = _design_matrix(train, FEATURES)
            x_test = _design_matrix(test, FEATURES).reindex(columns=x_train.columns, fill_value=0.0)
            imputer = SimpleImputer(strategy="median").fit(x_train)

            model = GradientBoostingRegressor(
                n_estimators=200, max_depth=3, learning_rate=0.05, random_state=0
            )
            model.fit(imputer.transform(x_train), train[target])

            block = test[["player_id", "season", "fantasy_position", target]].copy()
            block["predicted"] = model.predict(imputer.transform(x_test))
            # The obvious naive rival: last year's own volatility, unmodelled.
            block["naive_prev"] = test[f"prev_{target}"].to_numpy()
            predictions.append(block)

        if not predictions:
            continue
        combined = pd.concat(predictions, ignore_index=True)
        results[target] = _score_target(combined, target)

    return {"test_seasons": test_seasons, "targets": results}


def _score_target(combined: pd.DataFrame, target: str) -> dict:
    actual = combined[target].to_numpy(dtype=float)
    model_scores = evaluate(combined[target], combined["predicted"].to_numpy(dtype=float))

    naive = combined.dropna(subset=["naive_prev"])
    naive_scores = (
        evaluate(naive[target], naive["naive_prev"].to_numpy(dtype=float))
        if len(naive) > 30 else {}
    )

    return {
        "n": len(combined),
        "actual_mean": round(float(np.mean(actual)), 3),
        "actual_stdev": round(float(np.std(actual)), 3),
        "model": model_scores,
        "naive_last_season": naive_scores,
        "by_position": {
            str(position): evaluate(group[target], group["predicted"].to_numpy(dtype=float))
            for position, group in combined.groupby("fantasy_position")
            if len(group) > 30
        },
    }


def summarize(results: dict) -> str:
    lines = []
    for target, block in results["targets"].items():
        lines.append(f"TARGET: {target}   (n={block['n']}, actual mean "
                     f"{block['actual_mean']}, sd {block['actual_stdev']})")
        lines.append(f"  {'predictor':22s} {'spearman':>9s} {'r2':>8s} {'mae':>8s}")
        for label, key in [("model (profile)", "model"), ("naive: last season", "naive_last_season")]:
            s = block.get(key) or {}
            if "spearman" not in s:
                continue
            lines.append(f"  {label:22s} {s['spearman']:9.4f} {s['r2']:8.4f} {s['mae']:8.3f}")
        if block.get("by_position"):
            lines.append("  by position (model):")
            for position, s in block["by_position"].items():
                if "spearman" in s:
                    lines.append(f"    {position:4s} n={s['n']:4d} spearman={s['spearman']:7.4f}")
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args(argv)

    from fantasyprep.research.benchmark import build_modeling_frame

    modeling_frame, _ = build_modeling_frame()
    seasons = sorted(int(s) for s in modeling_frame["season"].unique() if s >= 2009)
    print("Building weekly volatility table...")
    volatility = build_weekly_volatility(seasons, force_refresh=args.force_refresh)
    print(f"  {len(volatility):,} player-seasons with >= {MIN_WEEKS} weeks")

    frame = attach_volatility(modeling_frame, volatility)
    print("Running variance model...")
    results = run(frame)
    print()
    print(summarize(results))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
