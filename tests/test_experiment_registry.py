import json
from pathlib import Path

from fantasyprep.draft_sim.experiment_registry import load_experiments, log_experiment


def test_log_experiment_writes_a_jsonl_entry(tmp_path: Path):
    path = log_experiment(
        tmp_path, name="baseline-10-seeds", notes="first hardened run",
        params={"num_seeds": 10}, summary={"win_rate": 0.6, "mean_delta": 42.0},
    )
    assert path == tmp_path / "experiments.jsonl"

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["name"] == "baseline-10-seeds"
    assert entry["notes"] == "first hardened run"
    assert entry["params"] == {"num_seeds": 10}
    assert entry["summary"] == {"win_rate": 0.6, "mean_delta": 42.0}
    assert "timestamp" in entry


def test_log_experiment_appends_not_overwrites(tmp_path: Path):
    log_experiment(tmp_path, "exp-1", "", {}, {})
    log_experiment(tmp_path, "exp-2", "", {}, {})

    entries = load_experiments(tmp_path)
    assert [e["name"] for e in entries] == ["exp-1", "exp-2"]


def test_load_experiments_empty_when_no_registry_exists(tmp_path: Path):
    assert load_experiments(tmp_path) == []


def test_load_experiments_skips_blank_lines(tmp_path: Path):
    registry = tmp_path / "experiments.jsonl"
    registry.write_text('{"name": "a", "timestamp": "x", "params": {}, "summary": {}, "notes": ""}\n\n', encoding="utf-8")
    entries = load_experiments(tmp_path)
    assert len(entries) == 1
    assert entries[0]["name"] == "a"
