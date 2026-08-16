"""Data validation + visualization report: assembles cross-check results and
outcome-bucket shapes into a single self-contained HTML artifact.

Usage:
    python -m fantasyprep.historical.report --year 2026 --out data/validation_report.html
"""
from __future__ import annotations

import argparse
import statistics
from pathlib import Path

from fantasyprep.historical.outcomes import DEFAULT_HISTORICAL_YEARS, build_outcome_distributions
from fantasyprep.historical.validate import cross_check_actuals, cross_check_espn_projections
from fantasyprep.league.settings import default_settings

POSITIONS = ("QB", "RB", "WR", "TE")


def gather_report_data(current_year: int, data_dir: Path) -> dict:
    settings = default_settings()
    raw_dir = data_dir / "raw"

    print(f"Cross-checking historical actuals ({DEFAULT_HISTORICAL_YEARS[0]}-{DEFAULT_HISTORICAL_YEARS[-1]})...")
    actuals_rows = cross_check_actuals(DEFAULT_HISTORICAL_YEARS, settings.scoring)
    actuals_deltas = [abs(r.delta) for r in actuals_rows]
    actuals_summary = {
        "n": len(actuals_rows),
        "n_outliers": sum(1 for r in actuals_rows if r.is_outlier),
        "mean_abs_delta": statistics.mean(actuals_deltas),
        "median_abs_delta": statistics.median(actuals_deltas),
        "max_abs_delta": max(actuals_deltas),
    }

    print(f"Cross-checking ESPN {current_year} projections...")
    espn_cache = raw_dir / f".espn_cache_{current_year}.json"
    espn_rows, espn_anomalies = cross_check_espn_projections(current_year, cache_path=espn_cache)
    espn_summary = {
        "n": len(espn_rows),
        "n_outliers": sum(1 for r in espn_rows if r.is_outlier),
        "n_anomalies": len(espn_anomalies),
    }

    print("Loading historical outcome distributions...")
    hist_cache = raw_dir / f".outcomes_{settings.teams}.json"
    distributions = build_outcome_distributions(settings, cache_path=hist_cache, adp_cache_dir=raw_dir)

    bucket_data = {pos: [] for pos in POSITIONS}
    for (pos, bucket), dist in distributions.items():
        if pos not in bucket_data:
            continue
        rank_start = bucket * 3 + 1
        rank_end = rank_start + 2
        outcomes = sorted(dist.outcomes)
        bucket_data[pos].append(
            {
                "bucket": bucket,
                "rank_start": rank_start,
                "rank_end": rank_end,
                "outcomes": outcomes,
                "n": len(outcomes),
                "median": statistics.median(outcomes),
                "p25": outcomes[max(0, int(len(outcomes) * 0.25) - 1)] if len(outcomes) >= 4 else min(outcomes),
                "p75": outcomes[min(len(outcomes) - 1, int(len(outcomes) * 0.75))] if len(outcomes) >= 4 else max(outcomes),
            }
        )
    for pos in bucket_data:
        bucket_data[pos].sort(key=lambda b: b["bucket"])

    return {
        "current_year": current_year,
        "historical_years": DEFAULT_HISTORICAL_YEARS,
        "actuals_rows": actuals_rows,
        "actuals_summary": actuals_summary,
        "espn_rows": espn_rows,
        "espn_anomalies": espn_anomalies,
        "espn_summary": espn_summary,
        "bucket_data": bucket_data,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("data/validation_report.html"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    data = gather_report_data(args.year, args.data_dir)

    from fantasyprep.historical.report_render import render_report  # local import: keeps HTML generation optional

    html = render_report(data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
