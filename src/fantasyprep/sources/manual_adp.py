"""Load hand-exported 'sharp' (paid-market) ADP snapshots.

Underdog's Terms of Use prohibit automated scraping, so this data is
maintained manually: periodically copy current ADP numbers from a public
page (e.g. FantasyPros' free Underdog/BB10 ADP page, or Underdog's own
site viewed in a browser) into a CSV under data/raw/. See README.md for
the expected format.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

SNAPSHOT_NAME_RE = re.compile(r"sharp_adp_(\d{4}-\d{2}-\d{2})\.csv$")


@dataclass(frozen=True)
class SharpAdpEntry:
    player_name: str
    team: str
    position: str
    adp: float
    source: str


def load_sharp_adp(csv_path: Path) -> list[SharpAdpEntry]:
    """Load a single sharp-ADP CSV: player_name, team, position, adp, source."""
    entries = []
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"player_name", "team", "position", "adp", "source"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{csv_path} is missing columns: {sorted(missing)}")

        for row in reader:
            entries.append(
                SharpAdpEntry(
                    player_name=row["player_name"].strip(),
                    team=row["team"].strip().upper(),
                    position=row["position"].strip().upper(),
                    adp=float(row["adp"]),
                    source=row["source"].strip(),
                )
            )
    return entries


def find_latest_snapshot(data_dir: Path) -> Path:
    """Pick the most recent data/raw/sharp_adp_YYYY-MM-DD.csv by filename date."""
    candidates = []
    for path in data_dir.glob("sharp_adp_*.csv"):
        m = SNAPSHOT_NAME_RE.search(path.name)
        if m:
            candidates.append((m.group(1), path))

    if not candidates:
        raise FileNotFoundError(
            f"No sharp_adp_YYYY-MM-DD.csv snapshots found in {data_dir}. "
            "See README.md for how to create one."
        )

    candidates.sort(key=lambda c: c[0])
    return candidates[-1][1]
