"""Persisted log of backtest runs worth remembering -- every deliberate
experiment (a bucket-width change, a bigger seed count, a recency-weighting
test) gets an entry with its parameters and headline results, so "I think
version 17 was better?" has an actual answer instead of a guess.

Not every backtest invocation is logged -- only ones explicitly named via
`--experiment-name` on `draft_sim.backtest`'s CLI, so ad-hoc smoke tests
don't clutter the log. Append-only JSONL, one line per experiment.

Usage (reading it back):
    python -m fantasyprep.draft_sim.experiment_registry
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def log_experiment(
    data_dir: Path, name: str, notes: str, params: dict, summary: dict, reproducible: bool = True
) -> Path:
    """`reproducible`: whether this run's PYTHONHASHSEED was pinned before it ran
    (see backtest.py's `_ensure_fixed_hash_seed`). Runs logged before that fix
    existed (2026-08-16, discovered identical --seed CLI args could still produce
    different results due to Python's per-process string-hash randomization) are
    NOT reproducible and shouldn't be compared against later ones as if they were
    the same kind of measurement -- flagged explicitly so that mistake can't
    happen silently later. Defaults to True since every current call site
    (backtest.py's `run()`) only reaches this point after that guard passes."""
    registry_path = data_dir / "experiments.jsonl"
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "name": name,
        "notes": notes,
        "params": params,
        "summary": summary,
        "reproducible": reproducible,
    }
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    with registry_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return registry_path


def load_experiments(data_dir: Path) -> list[dict]:
    registry_path = data_dir / "experiments.jsonl"
    if not registry_path.exists():
        return []
    entries = []
    with registry_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def _print_registry(entries: list[dict]) -> None:
    if not entries:
        print("No experiments logged yet -- use --experiment-name on draft_sim.backtest to log one.")
        return
    for e in entries:
        s = e.get("summary", {})
        repro_flag = "" if e.get("reproducible", False) else "  [NOT REPRODUCIBLE -- pre hash-seed fix]"
        print(f"\n{e['timestamp']}  {e['name']}{repro_flag}")
        if e.get("notes"):
            print(f"  notes: {e['notes']}")
        print(f"  params: {e['params']}")
        print(
            f"  vs ADP+need:   win_rate={s.get('win_rate', '?')}  "
            f"mean_delta={s.get('mean_delta', '?')}  median_delta={s.get('median_delta', '?')}"
        )
        if "win_rate_vs_pure_adp" in s:
            print(
                f"  vs pure ADP:   win_rate={s.get('win_rate_vs_pure_adp', '?')}  "
                f"mean_delta={s.get('mean_delta_vs_pure_adp', '?')}"
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    entries = load_experiments(args.data_dir)
    _print_registry(entries)


if __name__ == "__main__":
    main()
