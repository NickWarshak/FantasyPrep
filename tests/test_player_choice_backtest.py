from collections import Counter

import pytest

from fantasyprep.draft_sim.player_choice_backtest import (
    PlayerChoiceReplayResult,
    _compact_summary,
    replay_one,
)
from fantasyprep.draft_sim.points_model import HistoricalBootstrapModel
from fantasyprep.draft_sim.roster import DraftedPlayer, starting_lineup_value
from fantasyprep.historical.outcomes import OutcomeDistribution
from fantasyprep.historical.sources.ffc import FfcPlayer
from fantasyprep.league.settings import LeagueSettings, ScoringSettings
from fantasyprep.players.normalize import normalize_name

SETTINGS = LeagueSettings(
    teams=4,
    scoring=ScoringSettings(),
    roster_slots={"QB": 1, "RB": 1, "WR": 1, "DST": 1},
    bench=1,
)


def _p(name, position, adp, stdev=1.0):
    return FfcPlayer(name=name, position=position, team="XXX", adp=adp, stdev=stdev, high=1, low=100)


def _synthetic_pool():
    # Mirrors test_backtest.py's _synthetic_pool -- 6 players per skill
    # position with interleaved ADPs, DST uniformly latest.
    players = []
    adp = 1.0
    for i in range(6):
        for position in ("RB", "WR", "QB"):
            players.append(_p(f"{position}{i}", position, adp))
            adp += 1.0
    for i in range(6):
        players.append(_p(f"DST{i}", "DST", adp))
        adp += 1.0
    return players


def _distributions():
    dists = {}
    for position, points in (("QB", 50.0), ("RB", 60.0), ("WR", 40.0)):
        dists[(position, 0)] = OutcomeDistribution(position, 0, [points])
        dists[(position, 1)] = OutcomeDistribution(position, 1, [points])
    return dists


# --- PlayerChoiceReplayResult ---------------------------------------------------


def test_delta_is_player_choice_minus_model():
    r = PlayerChoiceReplayResult(
        year=2020, my_slot=1, seed_index=0, model_points=100.0, player_choice_points=130.0,
        model_roster=[], player_choice_roster=[],
    )
    assert r.delta == pytest.approx(30.0)


def test_cluster_key_is_the_year():
    r = PlayerChoiceReplayResult(
        year=2021, my_slot=5, seed_index=2, model_points=1.0, player_choice_points=1.0,
        model_roster=[], player_choice_roster=[],
    )
    assert r.cluster_key == 2021


# --- replay_one ---------------------------------------------------


def test_replay_one_produces_comparable_full_rosters():
    live_pool = _synthetic_pool()
    points_model = HistoricalBootstrapModel(_distributions())
    actual_points = {
        normalize_name(p.name): 10.0 + i
        for i, p in enumerate(pl for pl in live_pool if pl.position != "DST")
    }

    result = replay_one(
        year=2023, my_slot=1, settings=SETTINGS, live_pool=live_pool, points_model=points_model,
        actual_points=actual_points, num_sims=5, seed=7,
    )

    for roster in (result.model_roster, result.player_choice_roster):
        assert len(roster) == 5  # 4 starting slots + 1 bench, matching SETTINGS' total_rounds
        positions = Counter(pos for _name, pos, _pts in roster)
        assert positions["DST"] == 1  # forced-fill worked for both strategies
        assert positions["QB"] + positions["RB"] + positions["WR"] == 4

    def _recompute(roster):
        return starting_lineup_value(
            [DraftedPlayer(name=n, position=p, points=pts) for n, p, pts in roster], SETTINGS
        )

    assert result.model_points == _recompute(result.model_roster)
    assert result.player_choice_points == _recompute(result.player_choice_roster)
    assert result.delta == result.player_choice_points - result.model_points


def test_replay_one_uses_common_random_numbers_for_opponents():
    # Both strategies share the same opponent_rng seed (CRN) -- confirmed
    # indirectly: with a flat/deterministic points model, both rosters must
    # draft the SAME forced DST (opponent behavior is identical either way,
    # since CRN means the two replays only diverge through "my" own picks).
    live_pool = _synthetic_pool()
    points_model = HistoricalBootstrapModel(_distributions())
    actual_points = {normalize_name(p.name): 5.0 for p in live_pool if p.position != "DST"}

    result = replay_one(
        year=2023, my_slot=1, settings=SETTINGS, live_pool=live_pool, points_model=points_model,
        actual_points=actual_points, num_sims=5, seed=11,
    )

    model_dst = next(name for name, pos, _pts in result.model_roster if pos == "DST")
    player_choice_dst = next(name for name, pos, _pts in result.player_choice_roster if pos == "DST")
    assert model_dst == player_choice_dst


def test_replay_one_seed_index_is_preserved():
    live_pool = _synthetic_pool()
    points_model = HistoricalBootstrapModel(_distributions())
    actual_points = {normalize_name(p.name): 5.0 for p in live_pool}

    result = replay_one(
        year=2022, my_slot=2, settings=SETTINGS, live_pool=live_pool, points_model=points_model,
        actual_points=actual_points, num_sims=5, seed=3, seed_index=4,
    )
    assert result.year == 2022
    assert result.my_slot == 2
    assert result.seed_index == 4


# --- _compact_summary ---------------------------------------------------


def test_compact_summary_reports_win_rate_and_identical_roster_rate():
    results = [
        PlayerChoiceReplayResult(
            year=2020, my_slot=1, seed_index=0, model_points=100.0, player_choice_points=150.0,
            model_roster=[("A", "RB", 10.0)], player_choice_roster=[("B", "RB", 15.0)],
        ),
        PlayerChoiceReplayResult(
            year=2021, my_slot=1, seed_index=0, model_points=100.0, player_choice_points=100.0,
            model_roster=[("A", "RB", 10.0)], player_choice_roster=[("A", "RB", 10.0)],
        ),
    ]
    summary = _compact_summary(results)
    assert summary["n"] == 2
    assert summary["win_rate"] == pytest.approx(0.5)  # one win (+50), one tie (0, not > 0)
    assert summary["mean_delta"] == pytest.approx(25.0)  # mean of +50, 0
    assert summary["identical_roster_rate"] == pytest.approx(0.5)  # only the second replay matched
