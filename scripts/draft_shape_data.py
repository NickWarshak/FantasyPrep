"""One-off: emit draft_shape_by_round / first_round_by_position /
picks_per_position for model/baseline/vor as a single JSON blob, for
hand-authoring the "Draft Shape" artifact report. Not a permanent CLI --
backtest_analysis.py's own --shape flag is the supported entry point for
this data; this script just packages all three conditions into one file
for the report's embedded JS.

Usage:
    python scripts/draft_shape_data.py data/backtest_shape_run.json data/draft_shape.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fantasyprep.draft_sim.backtest_analysis import (
    draft_shape_by_round,
    first_round_by_position,
    picks_per_position,
)

in_path, out_path = sys.argv[1], sys.argv[2]
results = json.loads(Path(in_path).read_text(encoding="utf-8"))

conditions = {"model": "model_roster", "baseline": "baseline_roster", "vor": "vor_roster"}
out = {"n": len(results), "conditions": {}}

for label, key in conditions.items():
    out["conditions"][label] = {
        "shape": draft_shape_by_round(results, key),
        "first_round": first_round_by_position(results, key),
        "picks": picks_per_position(results, key),
    }

Path(out_path).write_text(json.dumps(out, indent=2), encoding="utf-8")
print(f"Wrote {out_path}")
