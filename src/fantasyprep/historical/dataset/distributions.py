"""Is the 3-rank outcome bucket actually the right width?

`historical/outcomes.py` pools real season outcomes into 3-rank buckets
(WR1-3, WR4-6, ...) and the simulator bootstraps from them. Three was a
reasonable choice at the time -- with 15 seasons it yields ~45 samples per
bucket -- but it was chosen, not measured. This module measures it.

The tension is the ordinary bias/variance one. Narrow buckets track the real
rank-to-points curve closely but each holds few samples, so its percentiles are
noise. Wide buckets are statistically stable but flatten genuine structure --
if WR1 and WR10 really do have different outcome distributions, a 10-wide
bucket asserts they don't.

TWO STUDIES, BECAUSE THE OBVIOUS ONE CAN'T USE MOST OF THE DATA:

(a) **ADP-rank buckets, 2010-2024.** Directly comparable to what the simulator
    does today, since it buckets by draft-time market rank. Limited to 2010+
    because that's how far back FFC ADP goes -- the 1999-2009 seasons in the new
    dataset have no market rank at all, which is precisely the gap
    docs/HISTORICAL_ADP_RESEARCH.md exists to address.

(b) **Prior-season finish-rank buckets, 1999-2024.** Uses all 26 seasons by
    conditioning on last year's positional finish instead of this year's ADP.
    It answers a slightly different question, and it is leakage-safe by
    construction (`prev_position_rank` is strictly prior-season). Worth having
    on its own merits: it's a candidate conditioning variable for the eventual
    player-level model, and it's the only one available for the full history.

This module REPORTS. It does not change `BUCKET_WIDTH` -- that's a live
simulator parameter and changing it needs its own A/B backtest, not a summary
statistic.
"""
from __future__ import annotations

import statistics
from pathlib import Path

import pandas as pd

from fantasyprep.historical.sources import ffc
from fantasyprep.league.settings import LeagueSettings, default_settings
from fantasyprep.players.normalize import normalize_name

BUCKET_WIDTHS = (1, 3, 5, 10)
POSITIONS = ("QB", "RB", "WR", "TE")

# How deep to study. Past this, ranks are populated by players nobody drafts and
# the buckets stop meaning anything for draft decisions.
MAX_RANK = 60

# FFC ADP coverage floor -- see the module docstring and ROADMAP.md.
ADP_STUDY_START_SEASON = 2010


def summarize(values: list[float]) -> dict:
    """The distribution shape of one bucket.

    Percentiles are the point of the exercise: the simulator bootstraps from
    these outcomes, so what matters is the spread and the tails, not the mean.
    Standard deviation needs two points; a single-sample bucket reports None
    rather than 0.0, which would read as "no variance" instead of "unknown".
    """
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "mean": round(statistics.mean(ordered), 2),
        "median": round(statistics.median(ordered), 2),
        "stdev": round(statistics.stdev(ordered), 2) if len(ordered) > 1 else None,
        "p10": round(_percentile(ordered, 0.10), 2),
        "p25": round(_percentile(ordered, 0.25), 2),
        "p50": round(_percentile(ordered, 0.50), 2),
        "p75": round(_percentile(ordered, 0.75), 2),
        "p90": round(_percentile(ordered, 0.90), 2),
        "min": round(ordered[0], 2),
        "max": round(ordered[-1], 2),
    }


def _percentile(ordered: list[float], q: float) -> float:
    """Linear-interpolated percentile on an already-sorted list."""
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def bucket_study(
    rank_to_points: dict[str, list[tuple[int, float]]], widths=BUCKET_WIDTHS
) -> dict:
    """Bucket (rank, points) pairs at several widths, per position."""
    study: dict = {}
    for position, pairs in sorted(rank_to_points.items()):
        study[position] = {}
        for width in widths:
            buckets: dict[int, list[float]] = {}
            for rank, points in pairs:
                if rank > MAX_RANK:
                    continue
                buckets.setdefault((rank - 1) // width, []).append(points)
            study[position][f"width_{width}"] = {
                _bucket_label(index, width): summarize(values)
                for index, values in sorted(buckets.items())
            }
    return study


def _bucket_label(index: int, width: int) -> str:
    low = index * width + 1
    high = low + width - 1
    return str(low) if width == 1 else f"{low}-{high}"


def adp_rank_pairs(
    canonical: pd.DataFrame,
    settings: LeagueSettings | None = None,
    adp_cache_dir: Path | None = None,
    start_season: int = ADP_STUDY_START_SEASON,
) -> dict[str, list[tuple[int, float]]]:
    """(draft-time positional ADP rank, actual points) pairs, per position.

    Joins the same way `historical/outcomes.py` does -- normalized name plus
    position -- so this study measures the buckets the simulator actually uses,
    not a cleaner idealized version of them. Reads only the FFC caches already
    on disk; a season with no cache is skipped and reported rather than fetched,
    keeping this offline and deterministic.
    """
    settings = settings or default_settings()
    adp_cache_dir = adp_cache_dir or Path("data/raw")

    pairs: dict[str, list[tuple[int, float]]] = {p: [] for p in POSITIONS}
    seasons_used, seasons_skipped = [], []

    for season in sorted(canonical["season"].unique()):
        if season < start_season:
            continue
        cache_path = adp_cache_dir / f".ffc_{settings.teams}_{season}.json"
        if not cache_path.exists():
            seasons_skipped.append(int(season))
            continue

        adp_players = ffc.fetch_adp(int(season), teams=settings.teams, cache_path=cache_path)
        ranks = ffc.position_ranks(adp_players)
        rank_by_key = {
            (normalize_name(p.name), p.position): ranks[p.name] for p in adp_players
        }

        season_rows = canonical[canonical["season"] == season]
        for row in season_rows.itertuples():
            rank = rank_by_key.get((normalize_name(row.player_name), row.fantasy_position))
            if rank is None or row.fantasy_position not in pairs:
                continue
            pairs[row.fantasy_position].append((int(rank), float(row.fantasy_points)))
        seasons_used.append(int(season))

    pairs["_meta"] = {"seasons_used": seasons_used, "seasons_skipped": seasons_skipped}  # type: ignore[assignment]
    return pairs


def prior_rank_pairs(features: pd.DataFrame) -> dict[str, list[tuple[int, float]]]:
    """(prior-season positional finish rank, actual points) pairs, per position.

    Leakage-safe by construction: `prev_position_rank` comes from
    `features._add_lags`, which only ever carries a strictly-adjacent prior
    season forward.
    """
    pairs: dict[str, list[tuple[int, float]]] = {p: [] for p in POSITIONS}
    usable = features.dropna(subset=["prev_position_rank", "fantasy_points"])
    for row in usable.itertuples():
        if row.fantasy_position not in pairs:
            continue
        pairs[row.fantasy_position].append(
            (int(row.prev_position_rank), float(row.fantasy_points))
        )
    return pairs


def width_comparison(study: dict) -> dict:
    """A compact read on what widening the bucket costs and buys.

    Two numbers per (position, width): median samples per bucket (the stability
    the width buys) and the spread of bucket medians across the top 30 ranks
    (the real rank-to-points structure it retains). If widening barely moves the
    second number, the structure wasn't there to lose.
    """
    comparison: dict = {}
    for position, by_width in study.items():
        comparison[position] = {}
        for width_key, buckets in by_width.items():
            populated = [b for b in buckets.values() if b.get("n", 0) > 0]
            if not populated:
                continue
            medians = [b["median"] for b in populated]
            comparison[position][width_key] = {
                "n_buckets": len(populated),
                "median_samples_per_bucket": round(
                    statistics.median([b["n"] for b in populated]), 1
                ),
                "min_samples_in_any_bucket": min(b["n"] for b in populated),
                "spread_of_bucket_medians": round(max(medians) - min(medians), 2),
            }
    return comparison
