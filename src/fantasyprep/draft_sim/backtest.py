"""Backtest: does the simulator's position recommendation actually beat a
realistic positional-need-aware ADP drafter, scored against real outcomes?

For each (season, draft slot), replays a full 150-pick draft twice, sharing
an opponent RNG seed between the two runs (common-random-numbers -- isolates
the comparison to "my" strategy instead of also varying opponent luck).
Opponents in both conditions draft via the existing ADP+stdev sampler
(`draft_sim.opponent.sample_pick`) -- that's "how a typical room drafts,"
held constant. Only my team's strategy differs:

- baseline: at each of my picks, take the best-ADP player among positions
  my roster currently needs (`positions_of_need`).
- model: at each of my picks, call the real `recommend_positions` --
  deliberately unmodified, the actual shipped tool, not an idealized
  need-aware version of it -- and take the best-ADP player at its
  top-ranked position.

Both conditions are scored on the *real* historical points their resulting
roster actually earned that season (`historical/sources/nfl_stats.py`), not
sampled/model outcomes.

Two things worth knowing before trusting the numbers:

1. Leakage: the model's historical outcome buckets for a test year Y are
   built only from years strictly before Y (see `leakage_safe_distributions`)
   -- otherwise the model would be partly evaluated on the same data it was
   tuned from.
2. DST: `recommend_positions` only ever evaluates QB/RB/WR/TE. Left alone,
   the model condition would never voluntarily draft a DST, freeing up an
   extra skill-position pick versus the baseline (which does fill its
   required DST slot) -- a structural advantage with nothing to do with
   drafting skill. Fixed by force-filling any position that is the *sole*
   remaining need and isn't one `recommend_positions` ever proposes,
   identically for both conditions (see `forced_fill_positions`). DST also
   has no real-points source anywhere in this codebase
   (`nfl_stats.POSITION_MAP` doesn't include it), so it always scores 0 for
   both conditions -- symmetric, so it doesn't bias the comparison, but it
   does mean total roster values are a slight underestimate of true value.

Usage:
    python -m fantasyprep.draft_sim.backtest --years 2022 2023 2024
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from fantasyprep.draft_sim.opponent import pick_weight, pick_weight_with_tail_floor, sample_pick

OPPONENT_WEIGHT_FN = {"gaussian": pick_weight, "gaussian-tail-floor": pick_weight_with_tail_floor}
from fantasyprep.draft_sim.points_model import HistoricalBootstrapModel, PointsModel
from fantasyprep.draft_sim.roster import DraftedPlayer, starting_lineup_value
from fantasyprep.draft_sim.simulate import CANDIDATE_POSITIONS, pick_owner, recommend_positions, state_from_picks
from fantasyprep.historical import weekly_stats
from fantasyprep.historical.outcomes import (
    DEFAULT_HISTORICAL_YEARS,
    OutcomeDistribution,
    build_outcome_distributions,
    outcome_for_rank,
)
from fantasyprep.historical.sources import ffc, nfl_stats
from fantasyprep.historical.sources.ffc import FfcPlayer
from fantasyprep.league.settings import LeagueSettings, default_settings
from fantasyprep.players.normalize import normalize_name

# 2015-2024: now that DEFAULT_HISTORICAL_YEARS goes back to 2010, every
# one of these has >=5 strictly-prior years to build leakage-safe outcome
# buckets from (2015 -> 2010-2014). Widened from [2022,2023,2024] once the
# year extension landed -- more distinct seasons tested, not just more
# repeated seeds of the same 3.
DEFAULT_BACKTEST_YEARS = list(range(2015, 2025))
MyPickStrategy = Callable[[list[FfcPlayer], list[str], list[dict]], FfcPlayer]


def positions_of_need(drafted_positions: list[str], settings: LeagueSettings) -> set[str]:
    """Which positions a roster still needs, given what's drafted so far.

    Fixed slots first, then FLEX-eligible overflow (RB/WR/TE beyond their
    own fixed count still count against FLEX), then -- once every starting
    slot including FLEX is filled -- "any skill position" for open bench
    spots. Empty once the whole roster (starting + bench) is full.
    """
    counts = Counter(drafted_positions)
    needed: set[str] = set()

    for position, required in settings.roster_slots.items():
        if position == "FLEX":
            continue
        if counts[position] < required:
            needed.add(position)

    flex_required = settings.roster_slots.get("FLEX", 0)
    if flex_required:
        flex_surplus = sum(
            max(0, counts[pos] - settings.roster_slots.get(pos, 0)) for pos in LeagueSettings.FLEX_ELIGIBLE
        )
        if flex_surplus < flex_required:
            needed.update(LeagueSettings.FLEX_ELIGIBLE)

    if not needed:
        total_roster = sum(settings.roster_slots.values()) + settings.bench
        if len(drafted_positions) < total_roster:
            needed.update(CANDIDATE_POSITIONS)

    return needed


def baseline_pick(pool: list[FfcPlayer], drafted_positions: list[str], settings: LeagueSettings) -> FfcPlayer:
    """Deterministic 'best ADP player at a position of need' -- the
    realistic-drafter baseline this backtest measures the model against."""
    needed = positions_of_need(drafted_positions, settings)
    candidates = [p for p in pool if p.position in needed] if needed else []
    if not candidates:
        candidates = list(pool)
    return min(candidates, key=lambda p: p.adp)


def pure_adp_pick(pool: list[FfcPlayer]) -> FfcPlayer:
    """The simplest possible baseline: best-ADP-available, full stop, no
    positional need considered at all. Cheaper baseline than ADP+need in
    every sense (no historical data source of its own required, and
    computationally free relative to the model condition's simulation
    cost) -- included so the report can say 'beats pure chalk by X' and
    'beats a need-aware drafter by Y' side by side, not just one number."""
    return min(pool, key=lambda p: p.adp)


# A guessed fallback only -- real callers should use ffc.derive_rank_cutoff()
# instead, which counts actual real-ADP draft depth per position for a given
# league size/season rather than assuming a fixed split. This flat guess
# (a fixed FLEX/bench-stash allocation) was checked against real FFC ADP for
# a 10-team league and found to badly understate real RB/WR draft depth
# (real depth runs 50-60+, not 30) -- see derive_rank_cutoff's docstring.
# Kept only for callers with no ADP data on hand (e.g. quick standalone use).
DEFAULT_RANK_CUTOFF = {"QB": 15, "RB": 30, "WR": 30, "TE": 15}


def replacement_level_points(
    distributions: dict[tuple[str, int], OutcomeDistribution],
    position: str,
    rank_cutoff: dict[str, int] | None = None,
) -> float:
    """Real historical mean points at the replacement-rank bucket for a
    position -- the bar a bench/waiver-tier player at that position
    actually clears, from real outcome data, not a guess."""
    rank_cutoff = rank_cutoff or DEFAULT_RANK_CUTOFF
    cutoff = rank_cutoff.get(position, 30)
    dist = outcome_for_rank(distributions, position, cutoff)
    return statistics.mean(dist.outcomes)


def vor_pick(
    pool: list[FfcPlayer],
    drafted_positions: list[str],
    settings: LeagueSettings,
    distributions: dict[tuple[str, int], OutcomeDistribution],
    pos_ranks: dict[str, int],
    rank_cutoff: dict[str, int] | None = None,
) -> FfcPlayer:
    """Value-over-replacement baseline (rung 2 of the baseline ladder) --
    best VOR player among positions of need. VOR = a candidate's real
    historical mean outcome for their draft-rank bucket minus a real
    replacement-level baseline for that position -- reasons explicitly
    about positional scarcity from real data, rather than trusting the
    market's (ADP's) implicit pricing of it, the way baseline_pick does.
    No historical projections needed -- the outcome-bucket machinery the
    model itself already draws from already provides real expected value.
    Falls back to best-ADP-overall if the needed set has no candidates
    (mirrors baseline_pick's fallback) or if a candidate has no
    historical bucket data at all (falls back to ADP order for those,
    rather than crashing or silently mis-ranking them as worthless)."""
    needed = positions_of_need(drafted_positions, settings)
    candidates = [p for p in pool if p.position in needed] if needed else []
    if not candidates:
        candidates = list(pool)

    replacement_cache: dict[str, float] = {}

    def vor(player: FfcPlayer) -> float | None:
        rank = pos_ranks.get(player.name, 999)
        try:
            dist = outcome_for_rank(distributions, player.position, rank)
        except KeyError:
            return None  # no historical data for this position at all (e.g. DST)
        expected = statistics.mean(dist.outcomes)
        if player.position not in replacement_cache:
            try:
                replacement_cache[player.position] = replacement_level_points(
                    distributions, player.position, rank_cutoff
                )
            except KeyError:
                replacement_cache[player.position] = 0.0
        return expected - replacement_cache[player.position]

    scored = [(p, vor(p)) for p in candidates]
    with_vor = [(p, v) for p, v in scored if v is not None]
    if not with_vor:
        return min(candidates, key=lambda p: p.adp)  # no VOR data at all -- fall back to ADP order
    return max(with_vor, key=lambda pv: pv[1])[0]


def forced_fill_positions(settings: LeagueSettings) -> set[str]:
    """Required-roster positions `recommend_positions` never proposes (DST
    in this league) -- must be force-filled identically by both conditions
    once it's the only thing left needed, or the model condition would never
    draft one at all. See module docstring point 2."""
    return set(settings.roster_slots) - set(CANDIDATE_POSITIONS) - {"FLEX"}


@dataclass(frozen=True)
class DraftResult:
    my_players: list[FfcPlayer]


def run_full_draft(
    live_pool: list[FfcPlayer],
    teams: int,
    my_slot: int,
    total_rounds: int,
    settings: LeagueSettings,
    my_pick_strategy: MyPickStrategy,
    opponent_rng: random.Random,
    opponent_weight_fn=pick_weight,
) -> DraftResult:
    """`opponent_weight_fn` defaults to the original pure-Gaussian
    `pick_weight` -- every existing/currently-running backtest uses this.
    Pass `pick_weight_with_tail_floor` (see opponent.py) to use the fixed
    opponent model instead, where a player who's fallen anomalously far
    past their ADP becomes more likely to be taken rather than getting
    numerically "stuck" undrafted. Opt-in rather than a swapped default so
    switching it doesn't silently change what an in-flight comparison is
    measuring -- same pattern as `vor_rank_cutoff_mode`."""
    total_picks = teams * total_rounds
    picks: list[dict] = []
    drafted_names: set[str] = set()
    my_players: list[FfcPlayer] = []
    my_positions: list[str] = []
    forced = forced_fill_positions(settings)

    for pick_num in range(1, total_picks + 1):
        undrafted = [p for p in live_pool if normalize_name(p.name) not in drafted_names]
        if not undrafted:
            break

        if pick_owner(teams, pick_num) == my_slot:
            needed = positions_of_need(my_positions, settings)
            if needed and needed <= forced:
                chosen = baseline_pick(undrafted, my_positions, settings)
            else:
                chosen = my_pick_strategy(undrafted, my_positions, picks)
            my_players.append(chosen)
            my_positions.append(chosen.position)
        else:
            chosen = sample_pick(undrafted, pick_num, opponent_rng, weight_fn=opponent_weight_fn)

        picks.append({"pick": pick_num, "player": chosen.name})
        drafted_names.add(normalize_name(chosen.name))

    return DraftResult(my_players=my_players)


def score_roster(
    players: list[FfcPlayer], actual_points: dict[str, float], settings: LeagueSettings
) -> tuple[float, list[tuple[str, str, float]]]:
    drafted = []
    detail = []
    for p in players:
        pts = actual_points.get(normalize_name(p.name), 0.0)
        drafted.append(DraftedPlayer(name=p.name, position=p.position, points=pts))
        detail.append((p.name, p.position, pts))
    return starting_lineup_value(drafted, settings), detail


def confidence_weighted_pick_value(
    rows: list[tuple[str, float, float, float]],
    undrafted: list[FfcPlayer],
    actual_points: dict[str, float],
) -> tuple[float, float, float]:
    """Blend the *real* point value of the top-2 candidate positions'
    best-available player, weighted by how close the model's own
    expected-value estimate is between them -- captures genuine
    uncertainty in what the decision was worth, rather than only scoring
    the one literal pick that happened to win the argmax.

    Weight is a logistic function of the margin between the top two
    candidates' means, scaled by their pooled P25-P75 spread (a
    self-calibrating notion of "how much uncertainty is actually here" --
    a wide spread needs a bigger raw-point margin before a pick counts as
    genuinely confident, matching e.g. "50/50" or "75/25" language rather
    than an arbitrary fixed cutoff). Only the top two are blended,
    matching that same framing -- not a general softmax over every
    candidate position.

    Returns (blended_value, actual_realized_value, weight_on_top_pick).
    `actual_realized_value` is the real points of whichever player the
    model's normal (unweighted) logic would actually have drafted --
    returned alongside so the blended estimate and what really happened
    can be compared directly, as a calibration check, not a replacement
    for the primary win/loss comparison.
    """
    top_position, top_mean, top_p25, top_p75 = rows[0]
    top_candidates = [p for p in undrafted if p.position == top_position]
    top_player = min(top_candidates, key=lambda p: p.adp) if top_candidates else None
    top_value = actual_points.get(normalize_name(top_player.name), 0.0) if top_player else 0.0

    if len(rows) < 2 or top_player is None:
        return top_value, top_value, 1.0

    second_position, second_mean, second_p25, second_p75 = rows[1]
    second_candidates = [p for p in undrafted if p.position == second_position]
    second_player = min(second_candidates, key=lambda p: p.adp) if second_candidates else None
    if second_player is None:
        return top_value, top_value, 1.0
    second_value = actual_points.get(normalize_name(second_player.name), 0.0)

    margin = top_mean - second_mean
    spread = ((top_p75 - top_p25) + (second_p75 - second_p25)) / 2 or 1.0  # avoid div-by-zero
    weight_top = 1.0 / (1.0 + math.exp(-margin / spread))

    blended = weight_top * top_value + (1 - weight_top) * second_value
    return blended, top_value, weight_top


@dataclass(frozen=True)
class ReplayResult:
    year: int
    my_slot: int
    seed_index: int  # which opponent-room draw this is within (year, my_slot) -- see run_backtest
    baseline_points: float  # ADP + positional need
    model_points: float
    pure_adp_points: float  # ADP only, need ignored -- the simplest baseline
    baseline_roster: list[tuple[str, str, float]]
    model_roster: list[tuple[str, str, float]]
    pure_adp_roster: list[tuple[str, str, float]]
    # Confidence-weighted diagnostic (see confidence_weighted_pick_value) --
    # NOT part of the primary win/loss comparison, a calibration check:
    # does the model's own uncertainty-weighted estimate track what
    # actually happened? Raw sums over genuine (non-forced-fill) model
    # decisions only, not slotted into starting-lineup logic like
    # model_points is, so not directly comparable to it 1:1.
    confidence_weighted_points: float = 0.0
    confidence_weighted_actual_points: float = 0.0
    # VOR: rung 2 of the baseline ladder (see vor_pick) -- reasons
    # explicitly about replacement value from real historical data,
    # rather than trusting ADP's implicit pricing of scarcity.
    vor_points: float = 0.0
    vor_roster: list[tuple[str, str, float]] = field(default_factory=list)

    @property
    def delta(self) -> float:
        """Model vs. the ADP+need baseline -- the primary, harder-to-beat comparison."""
        return self.model_points - self.baseline_points

    @property
    def delta_vs_pure_adp(self) -> float:
        """Model vs. the simplest possible baseline -- expected to be a bigger edge
        than `delta`, since ADP+need is itself already a step up from pure chalk."""
        return self.model_points - self.pure_adp_points

    @property
    def delta_vs_vor(self) -> float:
        """Model vs. a drafter reasoning explicitly about replacement value --
        a different question than beating the market (ADP) signal."""
        return self.model_points - self.vor_points

    @property
    def confidence_weighted_gap(self) -> float:
        """Blended estimate minus what actually happened, for the same
        decisions -- near zero means the model's uncertainty-weighting is
        well-calibrated; a large gap either direction means it isn't."""
        return self.confidence_weighted_points - self.confidence_weighted_actual_points

    @property
    def cluster_key(self) -> int:
        """The independent unit of real-world evidence is the *season*,
        not the (season, slot) cell -- a season's real outcomes (e.g. one
        player's monster year) can move the result at several different
        draft slots within that season simultaneously, so slots within a
        season aren't independent of each other the way different seasons
        are. Originally clustered at (year, my_slot) -- caught this was
        still too fine-grained (GPT review, 2026-08-16): that granularity
        only accounts for opponent-room-seed correlation within a cell,
        not the larger within-season correlation across cells. Used to
        cluster-bootstrap confidence intervals rather than treat every
        replay as an iid observation."""
        return self.year


def replay_one(
    year: int,
    my_slot: int,
    settings: LeagueSettings,
    live_pool: list[FfcPlayer],
    points_model: PointsModel,
    distributions: dict[tuple[str, int], OutcomeDistribution],
    actual_points: dict[str, float],
    num_sims: int,
    seed: int,
    seed_index: int = 0,
    vor_rank_cutoff: dict[str, int] | None = None,
    opponent_weight_fn=pick_weight,
) -> ReplayResult:
    total_rounds = sum(settings.roster_slots.values()) + settings.bench
    teams = settings.teams
    pos_ranks = ffc.position_ranks(live_pool)
    # vor_rank_cutoff lets a caller pin VOR to a specific replacement-rank
    # split (e.g. the old hardcoded guess) for a controlled A/B against the
    # default real-ADP-derived cutoff -- see run_backtest's vor_rank_cutoff_mode.
    rank_cutoff = vor_rank_cutoff if vor_rank_cutoff is not None else ffc.derive_rank_cutoff(live_pool, settings)

    baseline_result = run_full_draft(
        live_pool, teams, my_slot, total_rounds, settings,
        my_pick_strategy=lambda undrafted, my_positions, picks: baseline_pick(undrafted, my_positions, settings),
        opponent_rng=random.Random(seed), opponent_weight_fn=opponent_weight_fn,
    )

    pure_adp_result = run_full_draft(
        live_pool, teams, my_slot, total_rounds, settings,
        my_pick_strategy=lambda undrafted, my_positions, picks: pure_adp_pick(undrafted),
        opponent_rng=random.Random(seed),  # same seed -> CRN across all four conditions
        opponent_weight_fn=opponent_weight_fn,
    )

    vor_result = run_full_draft(
        live_pool, teams, my_slot, total_rounds, settings,
        my_pick_strategy=lambda undrafted, my_positions, picks: vor_pick(
            undrafted, my_positions, settings, distributions, pos_ranks, rank_cutoff
        ),
        opponent_rng=random.Random(seed), opponent_weight_fn=opponent_weight_fn,  # same seed -> CRN
    )

    recommend_rng = random.Random(seed + 1)
    weighted_blended: list[float] = []
    weighted_actual: list[float] = []

    def model_strategy(undrafted: list[FfcPlayer], my_positions: list[str], picks: list[dict]) -> FfcPlayer:
        state = state_from_picks(teams, my_slot, picks)
        rows = recommend_positions(
            live_pool, state, settings, points_model, num_sims, recommend_rng,
            opponent_weight_fn=opponent_weight_fn,
        )
        if not rows:
            return baseline_pick(undrafted, my_positions, settings)
        blended, actual, _weight = confidence_weighted_pick_value(rows, undrafted, actual_points)
        weighted_blended.append(blended)
        weighted_actual.append(actual)
        top_position = rows[0][0]
        candidates = [p for p in undrafted if p.position == top_position]
        if not candidates:
            return baseline_pick(undrafted, my_positions, settings)
        return min(candidates, key=lambda p: p.adp)

    model_result = run_full_draft(
        live_pool, teams, my_slot, total_rounds, settings,
        my_pick_strategy=model_strategy,
        opponent_rng=random.Random(seed), opponent_weight_fn=opponent_weight_fn,  # same seed as baseline -> CRN
    )

    baseline_points, baseline_detail = score_roster(baseline_result.my_players, actual_points, settings)
    model_points, model_detail = score_roster(model_result.my_players, actual_points, settings)
    pure_adp_points, pure_adp_detail = score_roster(pure_adp_result.my_players, actual_points, settings)
    vor_points, vor_detail = score_roster(vor_result.my_players, actual_points, settings)

    return ReplayResult(
        year=year, my_slot=my_slot, seed_index=seed_index,
        baseline_points=baseline_points, model_points=model_points, pure_adp_points=pure_adp_points,
        baseline_roster=baseline_detail, model_roster=model_detail, pure_adp_roster=pure_adp_detail,
        confidence_weighted_points=sum(weighted_blended),
        confidence_weighted_actual_points=sum(weighted_actual),
        vor_points=vor_points, vor_roster=vor_detail,
    )


def leakage_safe_distributions(
    settings: LeagueSettings, test_year: int, raw_dir: Path, force_refresh: bool = False
):
    """Outcome buckets for evaluating `test_year`, built only from seasons
    strictly before it -- see module docstring point 1."""
    prior_years = [y for y in DEFAULT_HISTORICAL_YEARS if y < test_year]
    cache_path = raw_dir / f".outcomes_{settings.teams}_pre{test_year}.json"
    return build_outcome_distributions(
        settings, years=prior_years, cache_path=cache_path, adp_cache_dir=raw_dir, force_refresh=force_refresh
    )


def run_backtest(
    years: list[int],
    slots: list[int],
    settings: LeagueSettings,
    data_dir: Path,
    num_sims: int,
    seed: int,
    num_seeds: int = 1,
    scoring_mode: str = "season-total",
    vor_rank_cutoff_mode: str = "derived",
    opponent_model: str = "gaussian",
) -> list[ReplayResult]:
    """Replay every (year, slot), each with `num_seeds` independent
    opponent-room draws (still paired between baseline/model within a
    draw via common random numbers -- see replay_one). More seeds per
    cell buys statistical power without needing more historical seasons,
    which don't exist. Results from the same (year, slot) are NOT
    independent of each other -- see ReplayResult.cluster_key and
    _bootstrap_ci, which account for this when reporting confidence
    intervals.

    `scoring_mode`: 'season-total' (default) scores rosters on raw actual
    season points -- implicitly assumes an empty/injured roster spot
    scores zero for the rest of the year. 'waiver-adjusted' credits
    real replacement-level production instead for weeks a player's data
    shows they didn't play (see historical/weekly_stats.py) -- a more
    realistic approximation of actual in-season roster management,
    without the full weekly-simulation rearchitecture that's still
    future work (ROADMAP.md Phase 4).

    `vor_rank_cutoff_mode`: 'derived' (default) uses ffc.derive_rank_cutoff
    (real ADP-drafted depth per position, per test year). 'legacy' pins
    VOR to the old hardcoded DEFAULT_RANK_CUTOFF instead -- for a
    controlled A/B isolating the cutoff fix's effect on VOR specifically
    (ADP+need/pure-ADP/model are all unaffected by this either way, since
    none of them take a rank_cutoff).

    `opponent_model`: 'gaussian' (default) is the original pure-Gaussian
    ADP+stdev sampler used by every existing/currently-running backtest.
    'gaussian-tail-floor' fixes a real bug (found 2026-08-16): a player who
    falls many standard deviations past their ADP has their pick-weight
    numerically collapse toward 0 under the plain Gaussian, making them
    "stuck" undrafted for the rest of a simulated draft, when a real
    drafter's reaction to a top player anomalously still being available is
    the opposite (near-certain to grab them) -- see opponent.py's
    `pick_weight_with_tail_floor`. Affects opponent behavior identically
    across all 4 conditions within a replay (still CRN-paired), so this
    changes realism, not the fairness of the within-replay comparison.

    This now also drives the model condition's own internal Monte Carlo
    lookahead (`recommend_positions` -> `simulate.py`'s
    `simulate_position_choice`, which simulates hypothetical future picks
    to score each candidate position) via the same `opponent_weight_fn`
    (fixed 2026-08-16 -- previously that internal lookahead hardcoded the
    plain Gaussian regardless of what was passed here, so a
    'gaussian-tail-floor' run made the outer opponents more realistic while
    the model's own assumption about the rest of the draft stayed stale).
    The live webapp's `recommend_positions` call (`webapp/app.py`) still
    doesn't expose an opponent-model choice and keeps the plain-Gaussian
    default -- unaffected by this backtest flag either way, since it's a
    separate call site."""
    raw_dir = data_dir / "raw"
    results = []
    opponent_weight_fn = OPPONENT_WEIGHT_FN[opponent_model]

    for year in years:
        live_cache = raw_dir / f".ffc_{settings.teams}_{year}.json"
        live_pool = ffc.fetch_adp(year, teams=settings.teams, cache_path=live_cache)

        distributions = leakage_safe_distributions(settings, year, raw_dir)
        points_model = HistoricalBootstrapModel(distributions)

        vor_rank_cutoff = DEFAULT_RANK_CUTOFF if vor_rank_cutoff_mode == "legacy" else None

        if scoring_mode == "waiver-adjusted":
            weekly_rank_cutoff = DEFAULT_RANK_CUTOFF if vor_rank_cutoff_mode == "legacy" else ffc.derive_rank_cutoff(live_pool, settings)
            actual_points = weekly_stats.waiver_adjusted_actual_points(year, settings.scoring, weekly_rank_cutoff)
        else:
            season_outcomes = nfl_stats.actual_fantasy_points(year, settings.scoring)
            actual_points = {normalize_name(o.name): o.points for o in season_outcomes}

        for slot in slots:
            for seed_index in range(num_seeds):
                result = replay_one(
                    year, slot, settings, live_pool, points_model, distributions, actual_points,
                    num_sims, seed=seed + year * 10_000 + slot * 100 + seed_index,
                    seed_index=seed_index, vor_rank_cutoff=vor_rank_cutoff,
                    opponent_weight_fn=opponent_weight_fn,
                )
                results.append(result)

    return results


def _percentile(sorted_values: list[float], pct: float) -> float:
    idx = min(len(sorted_values) - 1, max(0, int(pct * len(sorted_values))))
    return sorted_values[idx]


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a win rate -- much less overconfident
    than a naive normal-approximation interval at small n, which is
    exactly the regime a few dozen-to-hundreds of replays sits in.

    Assumes iid Bernoulli trials -- NOT valid for the backtest's own win
    rate, where replays from the same season are correlated (a season's
    real outcomes can flip several slots' win/loss together). Kept as a
    correctly-implemented general-purpose utility (own tests below check
    it against that assumption), but the headline win-rate CI uses
    `cluster_bootstrap_ci` with a win-rate statistic instead -- see
    `_summarize_comparison`."""
    if n == 0:
        return (0.0, 0.0)
    p_hat = wins / n
    denom = 1 + z * z / n
    center = (p_hat + z * z / (2 * n)) / denom
    margin = z * ((p_hat * (1 - p_hat) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def cluster_bootstrap_ci(
    results: list[ReplayResult],
    statistic,
    num_iterations: int = 2000,
    seed: int = 0,
    value_fn=lambda r: r.delta,
) -> tuple[float, float]:
    """Bootstrap CI that resamples whole season clusters, not individual
    replays. Replays sharing a cluster (any slot, any opponent-room seed,
    same season) are correlated with each other in a way replays from a
    different season aren't -- resampling individual replays, or even
    resampling (season, slot) cells as their own clusters, would
    understate the true uncertainty by treating same-season cells as if
    they were independent when they're not (a season's real outcomes,
    e.g. one player's monster year, can move several slots' results
    together). `value_fn` picks which per-replay value to bootstrap
    (default: model vs. ADP+need baseline; pass
    `lambda r: r.delta_vs_pure_adp` for the pure-ADP comparison instead).
    Also used for the win-rate CI (see `_summarize_comparison`) by passing
    a win-rate `statistic` instead of mean/median -- same clustering
    applies there too; a naive Wilson interval on raw win/loss counts
    would make the same independence mistake."""
    rng = random.Random(seed)
    clusters: dict[int, list[float]] = {}
    for r in results:
        clusters.setdefault(r.cluster_key, []).append(value_fn(r))
    cluster_deltas = list(clusters.values())
    if not cluster_deltas:
        return (0.0, 0.0)

    stats = []
    for _ in range(num_iterations):
        resampled = rng.choices(cluster_deltas, k=len(cluster_deltas))
        pooled = [d for cluster in resampled for d in cluster]
        stats.append(statistic(pooled))
    stats.sort()
    return (_percentile(stats, 0.025), _percentile(stats, 0.975))


def _win_rate_stat(pooled_deltas: list[float]) -> float:
    return sum(1 for d in pooled_deltas if d > 0) / len(pooled_deltas)


def _summarize_comparison(results: list[ReplayResult], label: str, value_fn) -> None:
    deltas = sorted(value_fn(r) for r in results)
    n = len(deltas)
    wins = sum(1 for d in deltas if d > 0)
    losses = sum(1 for d in deltas if d < 0)
    ties = n - wins - losses
    win_lo, win_hi = cluster_bootstrap_ci(results, _win_rate_stat, seed=0, value_fn=value_fn)
    mean_lo, mean_hi = cluster_bootstrap_ci(results, statistics.mean, seed=1, value_fn=value_fn)
    median_lo, median_hi = cluster_bootstrap_ci(results, statistics.median, seed=2, value_fn=value_fn)

    print(f"\n--- vs {label} ---")
    print(f"model beat {label} in {wins} ({wins / n:.0%}, 95% CI {win_lo:.0%}-{win_hi:.0%}), "
          f"lost {losses}, tied {ties}")
    print(f"mean delta:   {statistics.mean(deltas):+.1f}  (95% CI {mean_lo:+.1f} to {mean_hi:+.1f})")
    print(f"median delta: {statistics.median(deltas):+.1f}  (95% CI {median_lo:+.1f} to {median_hi:+.1f})")
    print(f"min / P10 / P90 / max: "
          f"{deltas[0]:+.1f} / {_percentile(deltas, 0.1):+.1f} / {_percentile(deltas, 0.9):+.1f} / {deltas[-1]:+.1f}")


def _summarize(results: list[ReplayResult]) -> None:
    n_seasons = len({r.cluster_key for r in results})
    n_cells = len({(r.year, r.my_slot) for r in results})
    print(f"\n{len(results)} replays across {n_cells} (year, slot) cells in {n_seasons} seasons")
    print("Note: CIs (win rate, mean, median) all cluster-bootstrap at the SEASON level, not "
          "(year, slot) -- different slots within the same season share that season's real "
          "outcomes and aren't independent of each other, only different seasons are treated as "
          "independent draws. With only a handful of seasons, don't over-read a tight-looking CI.")

    _summarize_comparison(results, "ADP+need baseline", lambda r: r.delta)
    _summarize_comparison(results, "pure ADP-chalk", lambda r: r.delta_vs_pure_adp)
    _summarize_comparison(results, "VOR baseline", lambda r: r.delta_vs_vor)

    print("\nBiggest swings (vs ADP+need baseline):")
    for r in sorted(results, key=lambda r: abs(r.delta), reverse=True)[:5]:
        print(f"  {r.year} slot {r.my_slot} seed {r.seed_index}: baseline {r.baseline_points:.1f} vs "
              f"model {r.model_points:.1f} (delta {r.delta:+.1f})")

    weighted_total = sum(r.confidence_weighted_points for r in results)
    actual_total = sum(r.confidence_weighted_actual_points for r in results)
    print(f"\n--- confidence-weighted calibration check ---")
    print(f"blended (uncertainty-weighted) estimate: {weighted_total:.1f}")
    print(f"actual realized (same decisions):        {actual_total:.1f}")
    print(f"gap: {weighted_total - actual_total:+.1f} "
          f"({'model overestimated' if weighted_total > actual_total else 'model underestimated'} "
          f"on average across genuine decisions -- near zero is good calibration)")


def _result_to_dict(r: ReplayResult) -> dict:
    return {
        "year": r.year,
        "my_slot": r.my_slot,
        "seed_index": r.seed_index,
        "baseline_points": r.baseline_points,
        "model_points": r.model_points,
        "pure_adp_points": r.pure_adp_points,
        "vor_points": r.vor_points,
        "delta": r.delta,
        "delta_vs_pure_adp": r.delta_vs_pure_adp,
        "delta_vs_vor": r.delta_vs_vor,
        "baseline_roster": r.baseline_roster,
        "model_roster": r.model_roster,
        "pure_adp_roster": r.pure_adp_roster,
        "vor_roster": r.vor_roster,
        "confidence_weighted_points": r.confidence_weighted_points,
        "confidence_weighted_actual_points": r.confidence_weighted_actual_points,
    }


def _compact_summary(results: list[ReplayResult]) -> dict:
    """Small dict of headline numbers for the experiment registry -- not
    the full CI-bearing summary printed to the console, just enough to
    compare experiments against each other at a glance."""
    n = len(results)
    deltas = [r.delta for r in results]
    pure_deltas = [r.delta_vs_pure_adp for r in results]
    vor_deltas = [r.delta_vs_vor for r in results]
    return {
        "n": n,
        "win_rate": sum(1 for d in deltas if d > 0) / n,
        "mean_delta": statistics.mean(deltas),
        "median_delta": statistics.median(deltas),
        "win_rate_vs_pure_adp": sum(1 for d in pure_deltas if d > 0) / n,
        "mean_delta_vs_pure_adp": statistics.mean(pure_deltas),
        "win_rate_vs_vor": sum(1 for d in vor_deltas if d > 0) / n,
        "mean_delta_vs_vor": statistics.mean(vor_deltas),
        "confidence_weighted_gap": sum(r.confidence_weighted_gap for r in results),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", default=DEFAULT_BACKTEST_YEARS)
    parser.add_argument("--slots", type=int, nargs="+", default=list(range(1, 11)))
    parser.add_argument("--num-sims", type=int, default=100,
                         help="per-decision Monte Carlo depth -- bumped from 50 after a live convergence "
                         "check showed 50 measurably under-converged in cases with real signal to find "
                         "(draft_sim/convergence.py)")
    parser.add_argument("--num-seeds", type=int, default=1,
                         help="opponent-room draws per (year, slot) cell, paired via common random numbers")
    parser.add_argument("--scoring-mode", choices=["season-total", "waiver-adjusted"], default="season-total",
                         help="'season-total' (default) scores rosters on raw actual points -- an empty/"
                         "injured slot implicitly scores 0 the rest of the season. 'waiver-adjusted' credits "
                         "real replacement-level production for weeks a player's data shows they didn't play "
                         "(historical/weekly_stats.py) -- more realistic, not the full weekly-simulation "
                         "rearchitecture (ROADMAP.md Phase 4), just a scoring-methodology adjustment.")
    parser.add_argument("--vor-rank-cutoff-mode", choices=["derived", "legacy"], default="derived",
                         help="'derived' (default) uses real ADP-drafted depth per position for VOR's "
                         "replacement level. 'legacy' pins it to the old hardcoded {QB:15,RB:30,WR:30,TE:15} "
                         "guess -- for a controlled A/B against 'derived' (ADP+need/pure-ADP/model results "
                         "are identical either way, since only VOR/waiver-adjusted scoring use this).")
    parser.add_argument("--opponent-model", choices=list(OPPONENT_WEIGHT_FN), default="gaussian",
                         help="'gaussian' (default) is the original opponent pick-weight model, used by every "
                         "existing/currently-running backtest. 'gaussian-tail-floor' fixes the 'stuck player' "
                         "bug where a player fallen many stdevs past ADP incorrectly becomes near-impossible "
                         "to draft instead of near-certain -- for a controlled A/B against 'gaussian'.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--experiment-name", type=str, default=None,
                         help="if set, logs this run to data/experiments.jsonl (see experiment_registry.py). "
                         "Omit for ad-hoc smoke tests you don't want cluttering the log.")
    parser.add_argument("--experiment-notes", type=str, default="")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> list[ReplayResult]:
    settings = default_settings()
    print(f"Backtesting years={args.years} slots={args.slots} num_sims={args.num_sims} "
          f"num_seeds={args.num_seeds} scoring_mode={args.scoring_mode}")
    results = run_backtest(
        args.years, args.slots, settings, args.data_dir, args.num_sims, args.seed, args.num_seeds,
        scoring_mode=args.scoring_mode, vor_rank_cutoff_mode=args.vor_rank_cutoff_mode,
        opponent_model=args.opponent_model,
    )
    _summarize(results)

    if args.out:
        args.out.write_text(json.dumps([_result_to_dict(r) for r in results], indent=2), encoding="utf-8")
        print(f"\nWrote {len(results)} replay results to {args.out}")

    if args.experiment_name:
        from fantasyprep.draft_sim.experiment_registry import log_experiment

        params = {
            "years": args.years, "slots": args.slots, "num_sims": args.num_sims,
            "num_seeds": args.num_seeds, "seed": args.seed, "scoring_mode": args.scoring_mode,
            "vor_rank_cutoff_mode": args.vor_rank_cutoff_mode, "opponent_model": args.opponent_model,
        }
        path = log_experiment(
            args.data_dir, args.experiment_name, args.experiment_notes, params, _compact_summary(results),
            reproducible=(os.environ.get("PYTHONHASHSEED") == "0"),
        )
        print(f"Logged experiment '{args.experiment_name}' to {path}")

    return results


def main(argv: list[str] | None = None) -> None:
    _ensure_fixed_hash_seed(argv)
    run(parse_args(argv))


def _ensure_fixed_hash_seed(argv: list[str] | None) -> None:
    """`--seed` alone does NOT make a run reproducible -- discovered
    2026-08-16 the hard way: three back-to-back runs with identical CLI
    args (same --seed) gave three different vs-VOR win rates (8/15, 10/15,
    10/15). Root cause: Python randomizes str hashing per-process by
    default (PYTHONHASHSEED unset -> random), which changes set/dict
    iteration order between runs even though every explicit random.Random
    in this codebase is properly seeded. Confirmed via
    PYTHONHASHSEED=0 python -m ... twice -> bit-identical results both
    times. Rather than hunt down which specific set iteration (in this
    module, in a dependency, doesn't matter) leaks into the random
    sequence, re-exec with a fixed hash seed unconditionally -- the
    standard fix for this class of bug, and it makes '--seed' actually
    mean what its help text claims."""
    if os.environ.get("PYTHONHASHSEED") != "0":
        # subprocess.run, not os.execv -- execv's argv reconstruction mangles
        # quoting for arguments containing spaces on Windows (confirmed live:
        # a multi-word --experiment-notes value got silently re-split into
        # separate argv entries and errored as unrecognized arguments).
        # subprocess handles Windows command-line quoting correctly.
        real_args = sys.argv[1:] if argv is None else argv
        env = {**os.environ, "PYTHONHASHSEED": "0"}
        result = subprocess.run(
            [sys.executable, "-m", "fantasyprep.draft_sim.backtest", *real_args], env=env
        )
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
