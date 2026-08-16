import json
from pathlib import Path

import pytest

from fantasyprep.historical.sources.ffc import FfcPlayer
from fantasyprep.market import snapshot
from fantasyprep.sources.espn import EspnPlayer


def _espn_player(name="Some Player"):
    return EspnPlayer(espn_id=1, name=name, position="RB", team="ATL", espn_adp=5.0, espn_expert_rank=4)


def _ffc_player(name="Some Player"):
    return FfcPlayer(name=name, position="RB", team="ATL", adp=5.0, stdev=1.2, high=1, low=10)


def test_run_snapshot_writes_dated_files_for_both_sources(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(snapshot.espn, "fetch_espn_players", lambda year: [_espn_player()])
    monkeypatch.setattr(snapshot.ffc, "fetch_adp", lambda year, teams, scoring: [_ffc_player()])

    status = snapshot.run_snapshot(year=2026, data_dir=tmp_path, snapshot_date="2026-08-15")

    espn_path = tmp_path / "snapshots" / "espn_2026-08-15.json"
    ffc_path = tmp_path / "snapshots" / "ffc_2026-08-15.json"
    assert espn_path.exists()
    assert ffc_path.exists()
    assert "ok: 1 players" in status["espn"]
    assert "ok: 1 players" in status["ffc"]

    espn_data = json.loads(espn_path.read_text(encoding="utf-8"))
    assert espn_data[0]["name"] == "Some Player"


def test_run_snapshot_one_source_failing_does_not_block_the_other(tmp_path: Path, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("endpoint changed")

    monkeypatch.setattr(snapshot.espn, "fetch_espn_players", _boom)
    monkeypatch.setattr(snapshot.ffc, "fetch_adp", lambda year, teams, scoring: [_ffc_player()])

    status = snapshot.run_snapshot(year=2026, data_dir=tmp_path, snapshot_date="2026-08-15")

    assert "FAILED" in status["espn"]
    assert "endpoint changed" in status["espn"]
    assert "ok" in status["ffc"]
    assert (tmp_path / "snapshots" / "ffc_2026-08-15.json").exists()
    assert not (tmp_path / "snapshots" / "espn_2026-08-15.json").exists()


def test_run_snapshot_defaults_date_to_today(tmp_path: Path, monkeypatch):
    from datetime import date

    monkeypatch.setattr(snapshot.espn, "fetch_espn_players", lambda year: [])
    monkeypatch.setattr(snapshot.ffc, "fetch_adp", lambda year, teams, scoring: [])

    snapshot.run_snapshot(year=2026, data_dir=tmp_path)

    today = date.today().isoformat()
    assert (tmp_path / "snapshots" / f"espn_{today}.json").exists()


# --- CLI ---------------------------------------------------


def test_parse_args_requires_year():
    with pytest.raises(SystemExit):
        snapshot.parse_args([])


def test_parse_args_defaults():
    args = snapshot.parse_args(["--year", "2026"])
    assert args.year == 2026
    assert args.data_dir == Path("data")
    assert args.date is None


def test_parse_args_date_override():
    args = snapshot.parse_args(["--year", "2026", "--date", "2026-01-01"])
    assert args.date == "2026-01-01"
