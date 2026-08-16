"""CLI: compare ESPN ADP against a manually-exported sharp-market ADP snapshot.

Usage:
    python -m fantasyprep.adp_gap.report --year 2026
    python -m fantasyprep.adp_gap.report --year 2026 --sharp data/raw/sharp_adp_2026-08-15.csv
"""
from __future__ import annotations

import argparse
import csv as csv_module
from pathlib import Path

from fantasyprep.adp_gap.compute import AdpGap, compute_gaps
from fantasyprep.players.normalize import load_aliases, match_players
from fantasyprep.sources.espn import fetch_espn_players
from fantasyprep.sources.manual_adp import find_latest_snapshot, load_sharp_adp
from fantasyprep.sources.sleeper import fetch_sleeper_players


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True, help="NFL season year")
    parser.add_argument(
        "--sharp-source",
        choices=["csv", "sleeper"],
        default="csv",
        help="Where to pull the comparison ranks from: a manual CSV snapshot (default, e.g. Underdog) or Sleeper's live search_rank",
    )
    parser.add_argument("--sharp", type=Path, default=None, help="Sharp ADP CSV, only used with --sharp-source csv (default: latest snapshot in data/raw)")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Base data directory")
    parser.add_argument("--out-prefix", type=Path, default=Path("adp_gap_report"), help="Output file prefix")
    parser.add_argument("--top", type=int, default=25, help="Rows per direction in the markdown summary")
    parser.add_argument("--refresh-espn", action="store_true", help="Bypass the ESPN response cache")
    parser.add_argument("--refresh-sleeper", action="store_true", help="Bypass the Sleeper response cache")
    parser.add_argument(
        "--exclude-positions",
        type=lambda s: {p.strip().upper() for p in s.split(",") if p.strip()},
        default=set(),
        help="Comma-separated positions to drop before matching, e.g. QB (Sleeper's search_rank runs Superflex-hot on QB, "
        "so single-QB leagues should pass --exclude-positions QB when using --sharp-source sleeper)",
    )
    parser.add_argument(
        "--max-adp",
        type=float,
        default=220,
        help="Drop players ranked beyond this ADP on either source before matching (default 220). Past typical draft "
        "depth, ADP/rank stop reflecting real draft behavior -- sources disagree wildly on barely-drafted players, "
        "producing huge fake 'gaps' that are just noise, not signal. Pass 0 to disable.",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> None:
    raw_dir = args.data_dir / "raw"
    aliases_path = args.data_dir / "aliases.csv"
    cache_path = raw_dir / f".espn_cache_{args.year}.json"

    print(f"Loading ESPN players for {args.year} (cache: {cache_path})...")
    espn_players = fetch_espn_players(args.year, cache_path=cache_path, force_refresh=args.refresh_espn)
    print(f"  {len(espn_players)} ESPN players with an ADP")

    if args.sharp_source == "sleeper":
        sleeper_cache = raw_dir / ".sleeper_cache.json"
        print(f"Loading Sleeper players (cache: {sleeper_cache})...")
        sharp_entries = fetch_sleeper_players(cache_path=sleeper_cache, force_refresh=args.refresh_sleeper)
        sharp_path = Path("sleeper (live)")
    else:
        sharp_path = args.sharp or find_latest_snapshot(raw_dir)
        print(f"Loading sharp ADP snapshot: {sharp_path}")
        sharp_entries = load_sharp_adp(sharp_path)
    print(f"  {len(sharp_entries)} sharp ADP entries")

    if args.exclude_positions:
        espn_players = [p for p in espn_players if p.position not in args.exclude_positions]
        sharp_entries = [e for e in sharp_entries if e.position not in args.exclude_positions]
        print(f"  excluding positions: {sorted(args.exclude_positions)}")

    if args.max_adp:
        espn_players = [p for p in espn_players if p.espn_adp <= args.max_adp]
        sharp_entries = [e for e in sharp_entries if e.adp <= args.max_adp]
        print(f"  capping to ADP <= {args.max_adp}")

    aliases = load_aliases(aliases_path)
    matched, unmatched_sharp, unmatched_espn = match_players(espn_players, sharp_entries, aliases)
    print(f"  {len(matched)} matched, {len(unmatched_sharp)} unmatched sharp, {len(unmatched_espn)} unmatched ESPN (not in sharp snapshot)")

    gaps = compute_gaps(matched)

    md_path = args.out_prefix.with_suffix(".md")
    csv_path = args.out_prefix.with_suffix(".csv")
    _write_csv(csv_path, gaps)
    _write_markdown(md_path, gaps, unmatched_sharp, args.top, sharp_path)
    print(f"Wrote {csv_path} and {md_path}")


def _write_csv(path: Path, gaps: list[AdpGap]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv_module.writer(f)
        writer.writerow(
            ["player_name", "position", "team", "espn_adp", "sharp_adp", "sharp_source", "raw_gap", "adjusted_score", "match_confidence"]
        )
        for g in gaps:
            writer.writerow(
                [g.player_name, g.position, g.team, g.espn_adp, g.sharp_adp, g.sharp_source, round(g.raw_gap, 2), round(g.adjusted_score, 2), g.match_confidence]
            )


def _write_markdown(path: Path, gaps: list[AdpGap], unmatched_sharp: list, top: int, sharp_path: Path) -> None:
    lines = [f"# ADP Gap Report (sharp source: {sharp_path.name})", ""]

    sharp_earlier = [g for g in gaps if g.raw_gap > 0][:top]
    espn_earlier = sorted((g for g in gaps if g.raw_gap < 0), key=lambda g: g.adjusted_score)[:top]

    lines.append("## Sharp market drafts earlier than ESPN (potential ESPN sleepers)")
    lines.append("")
    lines.extend(_table(sharp_earlier))
    lines.append("")

    lines.append("## ESPN drafts earlier than sharp market (potential ESPN traps)")
    lines.append("")
    lines.extend(_table(espn_earlier))
    lines.append("")

    if unmatched_sharp:
        lines.append(f"## Unmatched sharp-ADP entries ({len(unmatched_sharp)})")
        lines.append("")
        lines.append("| Player | Position | Team | Sharp ADP |")
        lines.append("|---|---|---|---|")
        for e in unmatched_sharp:
            lines.append(f"| {e.player_name} | {e.position} | {e.team} | {e.adp} |")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _table(gaps: list[AdpGap]) -> list[str]:
    header = ["| Player | Pos | Team | ESPN ADP | Sharp ADP | Raw Gap | Adjusted Score | Match |", "|---|---|---|---|---|---|---|---|"]
    rows = [
        f"| {g.player_name} | {g.position} | {g.team} | {g.espn_adp:.1f} | {g.sharp_adp:.1f} | {g.raw_gap:+.1f} | {g.adjusted_score:+.2f} | {g.match_confidence} |"
        for g in gaps
    ]
    return header + rows


def main(argv: list[str] | None = None) -> None:
    run(parse_args(argv))


if __name__ == "__main__":
    main()
