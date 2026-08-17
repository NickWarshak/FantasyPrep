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

# The deepest bucket at each position is not a distribution -- it's a handful of
# leftovers, because ADP pools run out of players before the bucket grid does.
# Measured in the production cache (2010-2024): TE's deepest bucket held ONE
# sample, QB's held two, RB's five, WR's four, against a typical bucket of 40-45.
#
# That is worse than thin, for two reasons:
#
# 1. `outcome_for_rank` falls back to the deepest observed bucket for any rank
#    past the end of the grid. So a one-sample bucket doesn't just serve its own
#    three ranks -- it serves every deeper rank too, and `rng.choice` on a
#    one-element list returns the same number every time. The model gets a
#    point estimate with zero variance where it should be least certain.
# 2. The values are erratic in the way tiny samples always are, and the errors
#    are not conservative. TE's single deepest sample is 151.7, which is *higher*
#    than the median of TE4-6 (145.3) -- so the model believed the 23rd tight end
#    outscores the 5th, with certainty. 2026's pool has 24 TEs and 29 QBs, so
#    this was live, not hypothetical.
#
# Fix: pool the tail. Walk up from the deepest bucket accumulating outcomes until
# the pool is a real distribution, and let every rank at or beyond that boundary
# share it. Pooling loses rank resolution exactly where the data never supported
# any, and the buckets it merges have near-identical medians anyway (TE16-18 at
# 116.7 vs TE19-21 at 114.4).
#
# 20 is deliberately well below the typical 40-45, so only genuinely starved
# tails are touched and normal buckets are left alone.
MIN_BUCKET_SAMPLES = 20

TAIL_POOLING_MODES = ("pooled", "legacy")
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
    tail_pooling: str = "pooled",
) -> dict[tuple[str, int], OutcomeDistribution]:
    """Build (position, bucket) -> OutcomeDistribution across historical seasons.

    The join/bucket result itself is cached to `cache_path` (this is the
    expensive part -- 8 seasons x FFC + nfl_data_py pulls) separately from
    the individual raw ADP fetches (cached per-year via `adp_cache_dir`).

    `tail_pooling`: 'pooled' (default) merges starved deepest buckets into one
    real distribution -- see MIN_BUCKET_SAMPLES. 'legacy' is the original
    unpooled behaviour, kept so the two can be A/B'd against real outcomes
    rather than the change being assumed to be an improvement.

    Pooling is applied *after* loading, never baked into the cache, so both
    modes read the same cache files and no existing cache is invalidated by
    this change.
    """
    if cache_path and cache_path.exists() and not force_refresh:
        return _apply_tail_pooling(_load_cached(cache_path), tail_pooling)

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

    return _apply_tail_pooling(distributions, tail_pooling)


def _apply_tail_pooling(
    distributions: dict[tuple[str, int], OutcomeDistribution], mode: str
) -> dict[tuple[str, int], OutcomeDistribution]:
    if mode not in TAIL_POOLING_MODES:
        raise ValueError(f"tail_pooling must be one of {TAIL_POOLING_MODES}, got {mode!r}")
    if mode == "legacy":
        return distributions
    return pool_thin_tail(distributions)


def pool_thin_tail(
    distributions: dict[tuple[str, int], OutcomeDistribution],
    min_samples: int = MIN_BUCKET_SAMPLES,
) -> dict[tuple[str, int], OutcomeDistribution]:
    """Merge each position's starved deepest buckets into one pooled tail.

    Walks upward from the deepest bucket accumulating outcomes until the pool
    holds at least `min_samples`, then replaces every bucket from that boundary
    down-rank with a single pooled distribution. Because the pooled bucket
    becomes the deepest one, `outcome_for_rank`'s existing past-the-end fallback
    now lands on a real distribution instead of a handful of leftovers -- no
    change needed there.

    A position whose deepest bucket is already healthy is returned untouched,
    which is the common case: this only fires where the ADP pool genuinely ran
    out of players.
    """
    by_position: dict[str, list[int]] = defaultdict(list)
    for position, bucket in distributions:
        by_position[position].append(bucket)

    pooled: dict[tuple[str, int], OutcomeDistribution] = {}
    for position, buckets in by_position.items():
        buckets.sort()
        accumulated: list[float] = []
        boundary = buckets[-1]
        for bucket in reversed(buckets):
            accumulated.extend(distributions[(position, bucket)].outcomes)
            boundary = bucket
            if len(accumulated) >= min_samples:
                break

        for bucket in buckets:
            if bucket < boundary:
                pooled[(position, bucket)] = distributions[(position, bucket)]
        pooled[(position, boundary)] = OutcomeDistribution(
            position=position, bucket=boundary, outcomes=accumulated
        )

    return pooled


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
