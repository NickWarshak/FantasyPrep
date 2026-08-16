"""Bucket real historical outcomes by position and draft-time position rank.

For each historical season, joins FFC ADP (position rank at draft time,
e.g. "the 5th WR taken") against actual season fantasy points
(historical/sources/nfl_stats.py), and pools outcomes across seasons into
buckets. The bucket for a given rank is literally the list of real
historical point totals -- for bootstrap resampling in the simulator,
not a fitted curve, so it captures real boom/bust shape.

Bucket width is 3 ranks (e.g. WR1-3, WR4-6, ...) -- with 15 seasons of
data that's ~45 samples per bucket, still thin enough to treat as a
directional approximation rather than a precise distribution, but far
better than the original 7-season depth.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from fantasyprep.historical.sources import ffc, nfl_stats
from fantasyprep.league.settings import LeagueSettings
from fantasyprep.players.normalize import normalize_name

BUCKET_WIDTH = 3
# 2010-2024 inclusive -- verified live (both FFC and nfl_data_py) rather
# than assumed, 2026-08-16. 2025 excluded: nfl_data_py's seasonal parquet
# for it still 404s as of this date, not published upstream yet. 2012 is
# a notably thinner year in FFC's data (93 players vs ~200 in most other
# years) -- included anyway since it's still real signal, just a smaller
# contribution to that year's buckets.
DEFAULT_HISTORICAL_YEARS = list(range(2010, 2025))


@dataclass(frozen=True)
class OutcomeDistribution:
    position: str
    bucket: int  # 0-indexed: rank 1-3 -> bucket 0, rank 4-6 -> bucket 1, etc.
    outcomes: list[float]  # real historical fantasy point totals


def bucket_for_rank(rank: int) -> int:
    return (rank - 1) // BUCKET_WIDTH


def build_outcome_distributions(
    settings: LeagueSettings,
    years: list[int] | None = None,
    cache_path: Path | None = None,
    adp_cache_dir: Path | None = None,
    force_refresh: bool = False,
) -> dict[tuple[str, int], OutcomeDistribution]:
    """Build (position, bucket) -> OutcomeDistribution across historical seasons.

    The join/bucket result itself is cached to `cache_path` (this is the
    expensive part -- 8 seasons x FFC + nfl_data_py pulls) separately from
    the individual raw ADP fetches (cached per-year via `adp_cache_dir`).
    """
    if cache_path and cache_path.exists() and not force_refresh:
        return _load_cached(cache_path)

    years = years or DEFAULT_HISTORICAL_YEARS
    buckets: dict[tuple[str, int], list[float]] = defaultdict(list)

    for year in years:
        adp_cache = adp_cache_dir / f".ffc_{settings.teams}_{year}.json" if adp_cache_dir else None
        adp_players = ffc.fetch_adp(year, teams=settings.teams, cache_path=adp_cache)
        ranks = ffc.position_ranks(adp_players)
        rank_by_key = {(normalize_name(p.name), p.position): ranks[p.name] for p in adp_players}

        season_outcomes = nfl_stats.actual_fantasy_points(year, settings.scoring)

        for outcome in season_outcomes:
            key = (normalize_name(outcome.name), outcome.position)
            rank = rank_by_key.get(key)
            if rank is None:
                continue
            bucket = bucket_for_rank(rank)
            buckets[(outcome.position, bucket)].append(outcome.points)

    distributions = {
        key: OutcomeDistribution(position=key[0], bucket=key[1], outcomes=outcomes)
        for key, outcomes in buckets.items()
    }

    if cache_path:
        _save_cache(cache_path, distributions)

    return distributions


def _load_cached(cache_path: Path) -> dict[tuple[str, int], OutcomeDistribution]:
    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    result = {}
    for k, v in raw.items():
        position, bucket = k.rsplit("_", 1)
        result[(position, int(bucket))] = OutcomeDistribution(
            position=position, bucket=int(bucket), outcomes=v
        )
    return result


def _save_cache(cache_path: Path, distributions: dict[tuple[str, int], OutcomeDistribution]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    raw = {f"{pos}_{bucket}": dist.outcomes for (pos, bucket), dist in distributions.items()}
    cache_path.write_text(json.dumps(raw), encoding="utf-8")


def outcome_for_rank(
    distributions: dict[tuple[str, int], OutcomeDistribution], position: str, rank: int
) -> OutcomeDistribution:
    """Look up the distribution for a rank, falling back to the deepest bucket observed."""
    bucket = bucket_for_rank(rank)
    key = (position, bucket)
    if key in distributions:
        return distributions[key]

    position_buckets = [b for (pos, b) in distributions if pos == position]
    if not position_buckets:
        raise KeyError(f"No historical data for position {position}")
    deepest = max(position_buckets)
    return distributions[(position, deepest)]
