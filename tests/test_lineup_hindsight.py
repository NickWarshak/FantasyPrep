"""Lineup scorers: hindsight vs what a manager can actually do."""
from __future__ import annotations

import pytest

from fantasyprep.league.settings import LeagueSettings, ScoringSettings
from fantasyprep.research.lineup_hindsight import (
    score_season_hindsight,
    score_weekly,
    spread_weekly,
)

SETTINGS = LeagueSettings(
    teams=10, scoring=ScoringSettings(),
    roster_slots={"QB": 1, "RB": 1, "WR": 1}, bench=2,
)


def test_spread_preserves_each_players_weekly_mean_and_season_total():
    weekly = {"a": {1: 0.0, 2: 20.0, 3: 10.0}}

    spread = spread_weekly(weekly, {"a"}, 2.0)

    assert sum(spread["a"].values()) == pytest.approx(sum(weekly["a"].values()))
    assert max(spread["a"].values()) > max(weekly["a"].values())


def test_spread_leaves_unlisted_players_untouched():
    weekly = {"a": {1: 5.0, 2: 15.0}, "b": {1: 5.0, 2: 15.0}}

    spread = spread_weekly(weekly, {"a"}, 2.0)

    assert spread["b"] == weekly["b"]


def test_season_scorer_is_blind_to_weekly_volatility():
    # A season-total scorer cannot see how the points were distributed across
    # weeks, which is exactly the limitation this module exists to expose.
    roster = [("a", "WR"), ("b", "WR"), ("qb", "QB"), ("rb", "RB")]
    weekly = {"a": {1: 10.0, 2: 10.0}, "b": {1: 5.0, 2: 5.0},
              "qb": {1: 20.0, 2: 20.0}, "rb": {1: 8.0, 2: 8.0}}

    steady = score_season_hindsight(roster, weekly, SETTINGS)
    volatile = score_season_hindsight(roster, spread_weekly(weekly, {"a"}, 3.0), SETTINGS)

    assert steady == pytest.approx(volatile)


def test_weekly_hindsight_starts_whoever_actually_scored_most():
    roster = [("a", "WR"), ("b", "WR"), ("qb", "QB"), ("rb", "RB")]
    # a and b alternate booms; hindsight captures the boom every week.
    weekly = {"a": {1: 30.0, 2: 0.0}, "b": {1: 0.0, 2: 30.0},
              "qb": {1: 10.0, 2: 10.0}, "rb": {1: 5.0, 2: 5.0}}

    total = score_weekly(roster, weekly, SETTINGS, realistic=False)

    # One WR slot, and the right WR is started both weeks: 30 + 30.
    assert total == pytest.approx(30.0 + 30.0 + 10.0 + 10.0 + 5.0 + 5.0)


def test_realistic_scorer_cannot_capture_alternating_booms():
    roster = [("a", "WR"), ("b", "WR"), ("qb", "QB"), ("rb", "RB")]
    weekly = {"a": {1: 30.0, 2: 0.0}, "b": {1: 0.0, 2: 30.0},
              "qb": {1: 10.0, 2: 10.0}, "rb": {1: 5.0, 2: 5.0}}

    realistic = score_weekly(roster, weekly, SETTINGS, realistic=True)
    hindsight = score_weekly(roster, weekly, SETTINGS, realistic=False)

    # No manager can know which receiver booms; hindsight always can.
    assert realistic < hindsight


def test_a_player_who_did_not_play_cannot_be_started():
    roster = [("a", "WR"), ("b", "WR"), ("qb", "QB"), ("rb", "RB")]
    # 'a' has no row in week 2 -- injured/inactive, the project's established
    # signal for "didn't play".
    weekly = {"a": {1: 30.0}, "b": {1: 1.0, 2: 7.0},
              "qb": {1: 10.0, 2: 10.0}, "rb": {1: 5.0, 2: 5.0}}

    total = score_weekly(roster, weekly, SETTINGS, realistic=False)

    # Week 2's WR slot must be filled by b (7.0), not by a's absent row.
    assert total == pytest.approx(30.0 + 7.0 + 10.0 + 10.0 + 5.0 + 5.0)


def test_hindsight_scorer_never_scores_below_the_realistic_one():
    roster = [("a", "WR"), ("b", "WR"), ("qb", "QB"), ("rb", "RB")]
    weekly = {"a": {1: 12.0, 2: 3.0, 3: 18.0}, "b": {1: 4.0, 2: 16.0, 3: 6.0},
              "qb": {1: 20.0, 2: 14.0, 3: 22.0}, "rb": {1: 9.0, 2: 11.0, 3: 7.0}}

    hindsight = score_weekly(roster, weekly, SETTINGS, realistic=False)
    realistic = score_weekly(roster, weekly, SETTINGS, realistic=True)

    # Perfect foresight is an upper bound by construction.
    assert hindsight >= realistic
