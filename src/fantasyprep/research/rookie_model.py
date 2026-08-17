"""Should rookies get their own model, or share the veterans' one?

Both prior benchmarks pointed the same way: whatever edge exists over the
market is concentrated in rookies. The point benchmark had the market arm at
0.517 spearman against history's 0.283 for rookies; the distribution benchmark
had the profile model beating ADP buckets by 8.8% CRPS on rookies against 3.3%
overall, with coverage error roughly halved.

The obvious next step is a dedicated rookie model, and the obvious argument for
it is that

    prev_fantasy_points = NaN

is not missing data. It is a different information state. A veteran with no
prior targets recorded genuinely produced nothing; a rookie never had the
chance. Feeding both to one model through a shared median-imputer tells it they
are the same thing, which they are not.

But "obvious" is not "true", and specialising has a real cost that is easy to
overlook: a rookie-only model trains on rookie rows alone -- roughly 20 per
season here -- so it trades a better-matched feature space for a far smaller
sample. Whether that trade pays is an empirical question, which is what this
answers rather than assumes.

THREE ARMS, scored on rookies only:

  shared_profile    the general model from distribution_benchmark, applied to
                    rookies (veterans and rookies trained together)
  rookie_specialist trained on rookie rows only, using only features a rookie
                    actually has
  adp_bucket        the incumbent, for reference

Leakage: walk-forward throughout, same discipline as every other benchmark here.

Usage:
    python -m fantasyprep.research.rookie_model
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from fantasyprep.research.benchmark import DEFAULT_TEST_SEASONS, TARGET, build_modeling_frame
from fantasyprep.research.distribution_benchmark import (
    PROFILE_FEATURES,
    QUANTILES,
    predict_empirical_quantiles,
    predict_model_quantiles,
    score_distribution,
)

DEFAULT_OUT_PATH = Path("data/historical/rookie_model.json")

# Everything a rookie actually has before his first snap. No prior-production
# columns at all -- they are structurally absent, not merely unobserved, and
# including them would just feed the model a column of imputed medians.
ROOKIE_FEATURES = [
    "adp",
    "adp_position_rank",
    "adp_stdev",
    "age",
    "draft_pick",
]

ARMS = ("adp_bucket", "shared_profile", "rookie_specialist")


def run(frame: pd.DataFrame, test_seasons: list[int] | None = None) -> dict:
    test_seasons = test_seasons or DEFAULT_TEST_SEASONS

    blocks = []
    for season in test_seasons:
        train = frame[(frame["season"] < season) & frame["has_adp"]]
        test = frame[(frame["season"] == season) & frame["has_adp"] & frame["is_rookie"]]
        rookie_train = train[train["is_rookie"]]
        if len(train) < 200 or test.empty or len(rookie_train) < 60:
            continue

        block = test[
            ["player_id", "player_name", "season", "fantasy_position", TARGET]
        ].reset_index(drop=True).copy()

        predictions = {
            "adp_bucket": predict_empirical_quantiles(train, test, use_prior=False),
            "shared_profile": predict_model_quantiles(train, test, PROFILE_FEATURES),
            "rookie_specialist": predict_model_quantiles(rookie_train, test, ROOKIE_FEATURES),
        }
        for arm, matrix in predictions.items():
            for j, q in enumerate(QUANTILES):
                block[f"{arm}__p{int(q * 100)}"] = matrix[:, j]
        blocks.append(block)
        print(f"  {season}: {len(test)} rookies, {len(rookie_train)} rookie training rows")

    combined = pd.concat(blocks, ignore_index=True)
    return {
        "test_seasons": test_seasons,
        "n_rookies_scored": len(combined),
        "overall": _score(combined),
        "by_position": {
            str(position): _score(group)
            for position, group in combined.groupby("fantasy_position")
            if len(group) >= 30
        },
    }


def _score(subset: pd.DataFrame) -> dict:
    actual = subset[TARGET].to_numpy(dtype=float)
    scores = {}
    for arm in ARMS:
        columns = [f"{arm}__p{int(q * 100)}" for q in QUANTILES]
        if all(c in subset.columns for c in columns):
            scores[arm] = score_distribution(actual, subset[columns].to_numpy(dtype=float))
    return scores


def summarize(results: dict) -> str:
    lines = [f"Rookies scored: {results['n_rookies_scored']:,}"]
    for label, block in [("OVERALL", results["overall"])] + [
        (f"Position {p}", b) for p, b in results["by_position"].items()
    ]:
        if not block:
            continue
        lines.append("")
        lines.append(label)
        lines.append(f"  {'arm':18s} {'n':>5s} {'covErr':>7s} {'CRPS':>8s} "
                     f"{'pinball':>8s} {'medMAE':>7s}")
        for arm in ARMS:
            s = block.get(arm)
            if not s or "crps" not in s:
                continue
            lines.append(
                f"  {arm:18s} {s['n']:5d} {s['mean_abs_coverage_error']:7.4f} "
                f"{s['crps']:8.3f} {s['mean_pinball']:8.3f} {s['median_mae']:7.2f}"
            )
    return "\n".join(lines)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    args = parser.parse_args(argv)

    frame, _ = build_modeling_frame()
    print("Running rookie model comparison...")
    results = run(frame)
    print()
    print(summarize(results))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
