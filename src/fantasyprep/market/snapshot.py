"""Daily timestamped market snapshots -- captured now, independent of
whether anything downstream uses them yet.

Motivation, confirmed concretely while building the backtest: FFC's own
"historical" ADP for a past season isn't a frozen archive -- it drifts
slightly over time as their data keeps accumulating (rebuilding the
backtest's caches on two different days gave two different bucket
distributions). A live snapshot taken today is the only thing that will
ever be a true, reproducible record of what the market looked like today.
History not captured now can't be reconstructed later -- this is
deliberately built before anything consumes it (ADP momentum tracking,
ranking-gravity analysis, etc. are all future work; this just starts the
clock).

Sources captured: ESPN (rankings + ADP), FFC (ADP + real stdev), and ESPN
award futures (OPOY/MVP/ROY market prices). All always fetched live (no cache
reuse) since the whole point is a real point-in-time reading, not yesterday's
cached one.

The futures capture matters more than the others, because it is the only one
whose history genuinely cannot be recovered: ESPN preserves no historical
futures prices at all (verified -- 2020-2024 return zero priced runners), so the
OPOY signal is unvalidatable today and stays out of the model until an archive
exists. This is that archive, starting now.

FantasyPros ECR is intentionally NOT auto-fetched here -- there's no clean
free programmatic endpoint for it, so it stays on the existing manual
process (`sources/manual_adp.py`'s `sharp_adp_YYYY-MM-DD.csv` convention,
see README.md). This module doesn't duplicate that, just follows the same
dated-filename pattern for the sources that ARE automatable.

Usage:
    python -m fantasyprep.market.snapshot --year 2026

Meant to be run once a day (e.g. via an OS-level scheduled task) --
running it more than once on the same date overwrites that date's file
with a fresher pull, which is fine; it does not append historical rows.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from fantasyprep.historical.sources import ffc
from fantasyprep.league.settings import default_settings
from fantasyprep.sources import espn, espn_futures


def _write_json(records: list, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps([asdict(r) for r in records], indent=2), encoding="utf-8")


def snapshot_espn(year: int, out_dir: Path, snapshot_date: str) -> tuple[Path, int]:
    players = espn.fetch_espn_players(year)
    out_path = out_dir / f"espn_{snapshot_date}.json"
    _write_json(players, out_path)
    return out_path, len(players)


def snapshot_ffc(year: int, teams: int, scoring: str, out_dir: Path, snapshot_date: str) -> tuple[Path, int]:
    players = ffc.fetch_adp(year, teams=teams, scoring=scoring)
    out_path = out_dir / f"ffc_{snapshot_date}.json"
    _write_json(players, out_path)
    return out_path, len(players)


def snapshot_futures(year: int, out_dir: Path, snapshot_date: str) -> tuple[Path, int]:
    """Capture the RAW award-futures payload, not just the parsed OPOY field.

    Raw because this module's own principle applies with unusual force here:
    history not captured now cannot be reconstructed later. ESPN was probed
    directly and does NOT preserve historical futures -- 2020 through 2024 all
    return zero priced OPOY runners, and only the current season carries a full
    field (see docs/VEGAS_DATA_RESEARCH.md). That makes the OPOY signal
    currently impossible to validate against outcomes, which is why it is shown
    to the drafter but deliberately kept out of the model.

    Snapshotting is the only thing that ever changes that. One season of
    preseason-stamped archive makes the validation possible next year; capturing
    nothing makes it permanently impossible. Keeping the raw payload also
    preserves the other 22 award markets (MVP, both Rookie of the Year awards,
    ...) at no extra cost, rather than pre-committing to the one market that
    happens to be interesting today.
    """
    raw = espn_futures.fetch_raw_futures(year)
    out_path = out_dir / f"futures_{snapshot_date}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    priced = espn_futures.fetch_award_futures(year, raw=raw)
    return out_path, len(priced)


def run_snapshot(year: int, data_dir: Path, snapshot_date: str | None = None) -> dict[str, str]:
    """Best-effort per source -- one source failing (a network blip, an
    endpoint change) shouldn't prevent capturing the other. Returns a
    source -> status string for each attempted source."""
    settings = default_settings()
    snapshot_date = snapshot_date or date.today().isoformat()
    out_dir = data_dir / "snapshots"

    status = {}

    try:
        path, n = snapshot_espn(year, out_dir, snapshot_date)
        status["espn"] = f"ok: {n} players -> {path}"
    except Exception as e:
        status["espn"] = f"FAILED: {type(e).__name__}: {e}"

    try:
        path, n = snapshot_ffc(year, settings.teams, "ppr", out_dir, snapshot_date)
        status["ffc"] = f"ok: {n} players -> {path}"
    except Exception as e:
        status["ffc"] = f"FAILED: {type(e).__name__}: {e}"

    try:
        path, n = snapshot_futures(year, out_dir, snapshot_date)
        status["futures"] = f"ok: {n} priced OPOY runners -> {path}"
    except Exception as e:
        status["futures"] = f"FAILED: {type(e).__name__}: {e}"

    return status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--date", type=str, default=None, help="override snapshot date (YYYY-MM-DD), default today")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    status = run_snapshot(args.year, args.data_dir, args.date)
    for source, result in status.items():
        print(f"{source}: {result}")


if __name__ == "__main__":
    main()
