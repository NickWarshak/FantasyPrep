from pathlib import Path

import pytest

from fantasyprep.sources.manual_adp import find_latest_snapshot, load_sharp_adp

CSV_HEADER = "player_name,team,position,adp,source\n"


def _write_csv(path: Path, rows: str) -> Path:
    path.write_text(CSV_HEADER + rows, encoding="utf-8")
    return path


# --- load_sharp_adp ---------------------------------------------------


def test_load_sharp_adp_parses_rows(tmp_path: Path):
    csv_path = _write_csv(
        tmp_path / "sharp_adp_2026-08-01.csv",
        "Bijan Robinson,atl,rb,1.5,underdog\n",
    )
    entries = load_sharp_adp(csv_path)

    assert len(entries) == 1
    e = entries[0]
    assert e.player_name == "Bijan Robinson"
    assert e.team == "ATL"  # uppercased
    assert e.position == "RB"  # uppercased
    assert e.adp == 1.5
    assert e.source == "underdog"


def test_load_sharp_adp_strips_whitespace(tmp_path: Path):
    csv_path = _write_csv(
        tmp_path / "sharp_adp_2026-08-01.csv",
        " Bijan Robinson , atl , rb ,1.5, underdog \n",
    )
    entries = load_sharp_adp(csv_path)
    assert entries[0].player_name == "Bijan Robinson"
    assert entries[0].source == "underdog"


def test_load_sharp_adp_multiple_rows(tmp_path: Path):
    csv_path = _write_csv(
        tmp_path / "sharp_adp_2026-08-01.csv",
        "Player A,ATL,RB,1.0,underdog\nPlayer B,BUF,WR,2.0,underdog\n",
    )
    entries = load_sharp_adp(csv_path)
    assert len(entries) == 2
    assert [e.player_name for e in entries] == ["Player A", "Player B"]


def test_load_sharp_adp_raises_on_missing_columns(tmp_path: Path):
    csv_path = tmp_path / "sharp_adp_2026-08-01.csv"
    csv_path.write_text("player_name,team,adp\nGuy,ATL,5.0\n", encoding="utf-8")  # missing position, source

    with pytest.raises(ValueError, match="missing columns"):
        load_sharp_adp(csv_path)


def test_load_sharp_adp_handles_bom(tmp_path: Path):
    # utf-8-sig -- a BOM (common when exported from Excel) shouldn't break the header check.
    csv_path = tmp_path / "sharp_adp_2026-08-01.csv"
    csv_path.write_bytes(("﻿" + CSV_HEADER + "Guy,ATL,RB,5.0,underdog\n").encode("utf-8"))

    entries = load_sharp_adp(csv_path)
    assert entries[0].player_name == "Guy"


# --- find_latest_snapshot ---------------------------------------------------


def test_find_latest_snapshot_picks_most_recent_date(tmp_path: Path):
    _write_csv(tmp_path / "sharp_adp_2026-08-01.csv", "A,ATL,RB,1.0,src\n")
    _write_csv(tmp_path / "sharp_adp_2026-08-15.csv", "B,BUF,WR,2.0,src\n")
    _write_csv(tmp_path / "sharp_adp_2026-07-20.csv", "C,DAL,QB,3.0,src\n")

    latest = find_latest_snapshot(tmp_path)
    assert latest.name == "sharp_adp_2026-08-15.csv"


def test_find_latest_snapshot_ignores_non_matching_files(tmp_path: Path):
    _write_csv(tmp_path / "sharp_adp_2026-08-01.csv", "A,ATL,RB,1.0,src\n")
    (tmp_path / "sharp_adp_not_a_date.csv").write_text("junk", encoding="utf-8")
    (tmp_path / "other_file.csv").write_text("junk", encoding="utf-8")

    latest = find_latest_snapshot(tmp_path)
    assert latest.name == "sharp_adp_2026-08-01.csv"


def test_find_latest_snapshot_raises_when_none_found(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="No sharp_adp"):
        find_latest_snapshot(tmp_path)
