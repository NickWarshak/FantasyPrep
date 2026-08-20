"""Does the model's edge survive HONEST scoring?

Two arms identical except for scoring mode: season-total (a lineup chosen with
perfect hindsight, what every headline number in this project was measured
under) versus weekly-realistic (lineups a manager could actually set).

The research raised a specific worry. Hindsight scoring REWARDS volatility
(+76.6 at a 2x weekly spread) while realistic management PENALISES it (-21.8),
so an edge measured under hindsight could have been the model quietly picking
boom/bust players and being credited for booms it could never have started.

The paired delta-of-deltas answers it directly: for each (year, slot, seed)
cell, how much did the model's margin over a baseline change when the scorer
became honest?
"""
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

ARMS = {
    "season-total (hindsight)": "data/ab_tailpool_legacy.json",
    "weekly-realistic (honest)": "data/backtest_weekly_realistic_2seed.json",
}
COMPARISONS = [
    ("delta", "vs ADP+need"),
    ("delta_vs_pure_adp", "vs pure ADP"),
    ("delta_vs_vor", "vs VOR"),
]


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def season_bootstrap(by_season, draws=5000, seed=0):
    rng = random.Random(seed)
    seasons = list(by_season)
    means = []
    for _ in range(draws):
        sampled = [v for s in (rng.choice(seasons) for _ in seasons) for v in by_season[s]]
        means.append(statistics.mean(sampled))
    means.sort()
    return means[int(0.025 * draws)], means[int(0.975 * draws)]


def main():
    arms = {label: load(path) for label, path in ARMS.items()}
    for label, rows in arms.items():
        print(f"{label}: {len(rows)} replays")
    print()

    print(f"{'comparison':14s} {'scorer':26s} {'win%':>7s} {'mean':>9s} {'median':>9s}")
    for field, label in COMPARISONS:
        for arm_label, rows in arms.items():
            values = [r[field] for r in rows if r.get(field) is not None]
            wins = sum(1 for v in values if v > 0)
            print(f"{label:14s} {arm_label:26s} {wins/len(values):6.1%} "
                  f"{statistics.mean(values):+9.1f} {statistics.median(values):+9.1f}")
        print()

    key = lambda r: (r["year"], r["my_slot"], r.get("seed_index", 0))
    hind = {key(r): r for r in arms["season-total (hindsight)"]}
    honest = {key(r): r for r in arms["weekly-realistic (honest)"]}
    shared = sorted(set(hind) & set(honest))
    print(f"PAIRED delta-of-deltas, {len(shared)} matched cells")
    print("  positive = the model's margin GREW once scoring became honest\n")

    for field, label in COMPARISONS:
        diffs, by_season = [], defaultdict(list)
        for k in shared:
            a, b = honest[k].get(field), hind[k].get(field)
            if a is None or b is None:
                continue
            diffs.append(a - b)
            by_season[k[0]].append(a - b)
        if not diffs:
            continue
        low, high = season_bootstrap(by_season)
        verdict = "REAL" if (low > 0 or high < 0) else "not distinguishable from 0"
        print(f"  {label:14s} mean {statistics.mean(diffs):+7.1f}   "
              f"95% CI [{low:+7.1f}, {high:+7.1f}]   {verdict}")


if __name__ == "__main__":
    main()
