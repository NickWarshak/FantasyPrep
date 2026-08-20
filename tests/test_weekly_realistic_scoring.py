"""The `weekly-realistic` backtest scoring mode.

The default path must stay byte-identical -- this is an additive mode, and every
recorded backtest result depends on season-total scoring not having moved.
"""
from __future__ import annotations

import pytest

from fantasyprep.draft_sim.backtest import score_roster
from fantasyprep.historical.sources.ffc import FfcPlayer
from fantasyprep.historical.weekly_stats import realistic_weekly_roster_points
from fantasyprep.league.settings import LeagueSettings, ScoringSettings

SETTINGS = LeagueSettings(
    teams=10, scoring=ScoringSettings(),
    roster_slots={"QB": 1, "RB": 1, "WR": 1}, bench=2,
)


def _player(name: str, position: str) -> FfcPlayer:
    return FfcPlayer(name=name, position=position, team="KC", adp=10.0,
                     stdev=3.0, high=1, low=20)


ROSTER = [_player("Alpha", "WR"), _player("Bravo", "WR"),
          _player("Cee", "QB"), _player("Dee", "RB")]


def test_default_scoring_is_unchanged_when_no_weekly_data_is_passed():
    actual = {"alpha": 200.0, "bravo": 100.0, "cee": 300.0, "dee": 150.0}

    total, detail = score_roster(ROSTER, actual, SETTINGS)

    # One WR slot: the better receiver starts, the other is benched.
    assert total == pytest.approx(200.0 + 300.0 + 150.0)
    assert len(detail) == 4


def test_weekly_mode_penalises_a_late_booming_bust():
    # Alpha out-scores Bravo on the season (106 vs 100) purely by exploding in
    # the last two weeks, so hindsight season scoring starts Alpha all year.
    # A real manager rides Alpha while he is useless, then benches him just as
    # he takes off -- which is exactly the cost the default scorer erases.
    actual = {"alpha": 106.0, "bravo": 100.0, "cee": 40.0, "dee": 40.0}
    weekly = {
        "alpha": {1: 2.0, 2: 2.0, 3: 2.0, 4: 50.0, 5: 50.0},
        "bravo": {1: 20.0, 2: 20.0, 3: 20.0, 4: 20.0, 5: 20.0},
        "cee": {1: 8.0, 2: 8.0, 3: 8.0, 4: 8.0, 5: 8.0},
        "dee": {1: 8.0, 2: 8.0, 3: 8.0, 4: 8.0, 5: 8.0},
    }

    season_total, _ = score_roster(ROSTER, actual, SETTINGS)
    weekly_total, _ = score_roster(ROSTER, actual, SETTINGS, weekly)

    # Season-total scoring credits Alpha's whole 106.
    assert season_total == pytest.approx(106.0 + 40.0 + 40.0)
    # Realistic weekly scoring cannot: Alpha starts weeks 1-3 on draft order
    # (6 points), then Bravo takes the slot once he has out-averaged him.
    assert weekly_total == pytest.approx(6.0 + 20.0 + 20.0 + 40.0 + 40.0)
    assert weekly_total < season_total


def test_detail_stays_season_total_in_weekly_mode():
    # The position-breakdown analysis attributes value per player, and a weekly
    # lineup total cannot be split back onto players unambiguously.
    actual = {"alpha": 60.0, "bravo": 60.0, "cee": 40.0, "dee": 40.0}
    weekly = {n: {1: v / 2, 2: v / 2} for n, v in actual.items()}

    _, detail = score_roster(ROSTER, actual, SETTINGS, weekly)

    assert dict((name, pts) for name, _, pts in detail) == {
        "Alpha": 60.0, "Bravo": 60.0, "Cee": 40.0, "Dee": 40.0
    }


def test_realistic_scorer_cannot_exceed_the_hindsight_scorer():
    roster = [("alpha", "WR"), ("bravo", "WR"), ("cee", "QB"), ("dee", "RB")]
    weekly = {
        "alpha": {1: 30.0, 2: 2.0, 3: 25.0},
        "bravo": {1: 5.0, 2: 28.0, 3: 4.0},
        "cee": {1: 18.0, 2: 20.0, 3: 16.0},
        "dee": {1: 9.0, 2: 12.0, 3: 7.0},
    }

    realistic = realistic_weekly_roster_points(roster, weekly, SETTINGS, hindsight=False)
    hindsight = realistic_weekly_roster_points(roster, weekly, SETTINGS, hindsight=True)

    assert hindsight >= realistic


def test_injured_player_cannot_be_started():
    roster = [("alpha", "WR"), ("bravo", "WR"), ("cee", "QB"), ("dee", "RB")]
    # alpha has no week-2 row: the project's established did-not-play signal.
    weekly = {
        "alpha": {1: 30.0},
        "bravo": {1: 1.0, 2: 9.0},
        "cee": {1: 10.0, 2: 10.0},
        "dee": {1: 5.0, 2: 5.0},
    }

    total = realistic_weekly_roster_points(roster, weekly, SETTINGS, hindsight=True)

    assert total == pytest.approx(30.0 + 9.0 + 10.0 + 10.0 + 5.0 + 5.0)


def test_scoring_mode_choices_include_weekly_realistic():
    from fantasyprep.draft_sim.backtest import parse_args

    args = parse_args(["--scoring-mode", "weekly-realistic"])

    assert args.scoring_mode == "weekly-realistic"
