import json
from pathlib import Path

import pytest

from fantasyprep.historical.outcomes import OutcomeDistribution
from fantasyprep.historical.sources.ffc import FfcPlayer
from fantasyprep.league.settings import default_settings
from fantasyprep.sources.espn import EspnPlayer
from fantasyprep.webapp.app import create_app

SETTINGS = default_settings()  # 10-team PPR


def _p(name, position, adp, stdev=1.0):
    return FfcPlayer(name=name, position=position, team="XXX", adp=adp, stdev=stdev, high=1, low=100)


FIXTURE_POOL = [
    _p("RB One", "RB", 1.0),
    _p("RB Two", "RB", 5.0),
    _p("RB Three", "RB", 9.0),
    _p("WR One", "WR", 2.0),
    _p("WR Two", "WR", 6.0),
    _p("WR Three", "WR", 10.0),
    _p("QB One", "QB", 20.0),
    _p("QB Two", "QB", 25.0),
    _p("QB Three", "QB", 30.0),
    _p("TE One", "TE", 15.0),
    _p("TE Two", "TE", 22.0),
    _p("TE Three", "TE", 28.0),
]

FIXTURE_DISTRIBUTIONS = {
    ("RB", 0): OutcomeDistribution(position="RB", bucket=0, outcomes=[280.0, 260.0, 300.0]),
    ("WR", 0): OutcomeDistribution(position="WR", bucket=0, outcomes=[270.0, 250.0, 290.0]),
    ("QB", 0): OutcomeDistribution(position="QB", bucket=0, outcomes=[350.0, 320.0, 300.0]),
    ("TE", 0): OutcomeDistribution(position="TE", bucket=0, outcomes=[180.0, 160.0, 200.0]),
}

FIXTURE_ESPN_POINTS = {"RB One": 999.0}  # deliberately way outside the historical range, to detect if it's used

FIXTURE_ESPN_PLAYERS = [
    EspnPlayer(espn_id=1, name="Star RB", position="RB", team="XXX", espn_adp=1.0, espn_expert_rank=1),
    EspnPlayer(espn_id=2, name="Star WR", position="WR", team="XXX", espn_adp=2.0, espn_expert_rank=2),
    EspnPlayer(espn_id=3, name="Bench Guy", position="RB", team="XXX", espn_adp=150.0, espn_expert_rank=150),
]


def _make_app(tmp_path: Path, state_name: str = "draft_state.json", espn_points=None, espn_players=None):
    return create_app(
        year=2026,
        draft_state_path=tmp_path / state_name,
        data_dir=tmp_path,
        num_sims=5,  # small for fast tests
        settings=SETTINGS,
        live_pool=list(FIXTURE_POOL),
        distributions=FIXTURE_DISTRIBUTIONS,
        espn_points=espn_points,
        espn_players=espn_players,
    )


@pytest.fixture
def app(tmp_path: Path):
    return _make_app(tmp_path)


@pytest.fixture
def client(app):
    return app.test_client()


def test_state_before_setup(client):
    resp = client.get("/api/state")
    data = resp.get_json()
    assert data["my_draft_slot"] is None
    assert data["picks"] == {}
    assert data["current_pick"] == 1


def test_setup_sets_draft_slot(client):
    resp = client.post("/api/setup", json={"my_draft_slot": 3})
    data = resp.get_json()
    assert data["my_draft_slot"] == 3
    assert data["current_pick"] == 1


def test_setup_rejects_out_of_range_slot(client):
    resp = client.post("/api/setup", json={"my_draft_slot": 99})
    assert resp.status_code == 400


def test_assign_pick_before_setup_rejected(client):
    resp = client.put("/api/picks/1", json={"player_name": "RB One"})
    assert resp.status_code == 400


def test_assign_pick_response(client):
    client.post("/api/setup", json={"my_draft_slot": 1})
    resp = client.put("/api/picks/1", json={"player_name": "RB One"})
    data = resp.get_json()
    assert data["picks"]["1"]["player"] == "RB One"
    assert data["picks"]["1"]["position"] == "RB"
    assert data["picks"]["1"]["mine"] is True
    assert data["current_pick"] == 2


def test_assign_pick_autosaves_to_disk(tmp_path: Path):
    app = _make_app(tmp_path)
    client = app.test_client()
    client.post("/api/setup", json={"my_draft_slot": 2})
    client.put("/api/picks/1", json={"player_name": "WR One"})

    saved = json.loads((tmp_path / "draft_state.json").read_text(encoding="utf-8"))
    assert saved["my_draft_slot"] == 2
    assert saved["picks"] == [{"pick": 1, "player": "WR One"}]


def test_assign_duplicate_player_rejected(client):
    client.post("/api/setup", json={"my_draft_slot": 1})
    client.put("/api/picks/1", json={"player_name": "RB One"})
    resp = client.put("/api/picks/2", json={"player_name": "RB One"})
    assert resp.status_code == 400


def test_assign_already_filled_slot_rejected(client):
    client.post("/api/setup", json={"my_draft_slot": 1})
    client.put("/api/picks/1", json={"player_name": "RB One"})
    resp = client.put("/api/picks/1", json={"player_name": "WR One"})
    assert resp.status_code == 400


def test_assign_future_pick_is_a_keeper_and_does_not_move_current_pick(client):
    # 10-team, slot 1 -> picks 1, 20, 21, ... Assigning pick 20 (a future,
    # mine slot) ahead of time is a keeper -- current_pick stays at 1.
    client.post("/api/setup", json={"my_draft_slot": 1})
    resp = client.put("/api/picks/20", json={"player_name": "RB One"})
    data = resp.get_json()
    assert data["current_pick"] == 1
    assert data["picks"]["20"]["mine"] is True


def test_clear_pick(client):
    client.post("/api/setup", json={"my_draft_slot": 1})
    client.put("/api/picks/1", json={"player_name": "RB One"})
    resp = client.delete("/api/picks/1")
    data = resp.get_json()
    assert data["picks"] == {}
    assert data["current_pick"] == 1


def test_clear_unassigned_pick_is_a_noop(client):
    client.post("/api/setup", json={"my_draft_slot": 1})
    resp = client.delete("/api/picks/1")
    assert resp.get_json()["picks"] == {}


def test_reset_clears_picks_keeps_slot(client):
    client.post("/api/setup", json={"my_draft_slot": 4})
    client.put("/api/picks/1", json={"player_name": "RB One"})
    resp = client.post("/api/reset")
    data = resp.get_json()
    assert data["picks"] == {}
    assert data["my_draft_slot"] == 4


def test_player_search_excludes_drafted(client):
    client.post("/api/setup", json={"my_draft_slot": 1})
    client.put("/api/picks/1", json={"player_name": "RB One"})

    resp = client.get("/api/players?q=RB")
    names = [p["name"] for p in resp.get_json()]
    assert "RB One" not in names
    assert "RB Two" in names


def test_player_search_empty_query_returns_empty(client):
    client.post("/api/setup", json={"my_draft_slot": 1})
    resp = client.get("/api/players?q=")
    assert resp.get_json() == []


def test_recommend_before_setup_rejected(client):
    resp = client.get("/api/recommend")
    assert resp.status_code == 400


def test_recommend_returns_all_positions_sorted(client):
    client.post("/api/setup", json={"my_draft_slot": 1})
    resp = client.get("/api/recommend?seed=1")
    rows = resp.get_json()

    positions = {row["position"] for row in rows}
    assert positions == {"QB", "RB", "WR", "TE"}
    expecteds = [row["expected"] for row in rows]
    assert expecteds == sorted(expecteds, reverse=True)


def test_recommend_rejects_invalid_points_source(client):
    client.post("/api/setup", json={"my_draft_slot": 1})
    resp = client.get("/api/recommend?points_source=made_up")
    assert resp.status_code == 400


def test_recommend_espn_points_source_uses_injected_projection(tmp_path: Path):
    app = _make_app(tmp_path, espn_points=FIXTURE_ESPN_POINTS)
    client = app.test_client()
    client.post("/api/setup", json={"my_draft_slot": 1})
    # Draft RB One immediately so its (deliberately huge) 999-point ESPN
    # projection is guaranteed to land in the roster if the espn model is used.
    client.put("/api/picks/1", json={"player_name": "RB One"})

    resp_historical = client.get("/api/recommend?seed=1&points_source=historical")
    resp_espn = client.get("/api/recommend?seed=1&points_source=espn")

    hist_best = max(row["expected"] for row in resp_historical.get_json())
    espn_best = max(row["expected"] for row in resp_espn.get_json())
    # ESPN source must reflect RB One's 999-point fixture projection somewhere
    # in the roster value -- historical (max real fixture outcome ~350) can't.
    assert espn_best > hist_best
    assert espn_best >= 999.0


def test_next_pick_is_mine_hint(client):
    # 10-team, slot 1 -> picks 1, 20, 21, ...
    client.post("/api/setup", json={"my_draft_slot": 1})
    data = client.get("/api/state").get_json()
    assert data["next_pick_is_mine"] is True

    client.put("/api/picks/1", json={"player_name": "RB One"})
    data = client.get("/api/state").get_json()
    assert data["next_pick_is_mine"] is False


# --- POST /api/simulate/step: auto-pick opponent picks based on ESPN ADP ---


def test_simulate_step_before_setup_rejected(client):
    resp = client.post("/api/simulate/step", json={"randomness": 1.0})
    assert resp.status_code == 400


def test_simulate_step_makes_one_opponent_pick(tmp_path: Path):
    app = _make_app(tmp_path, espn_players=FIXTURE_ESPN_PLAYERS)
    client = app.test_client()
    # slot 2 -> pick 1 belongs to team 1, not mine -- should get auto-picked
    client.post("/api/setup", json={"my_draft_slot": 2})

    resp = client.post("/api/simulate/step", json={"randomness": 1.0})
    data = resp.get_json()

    assert data["current_pick"] == 2
    assert "1" in data["picks"]
    assert data["picks"]["1"]["mine"] is False


def test_simulate_step_stops_on_my_turn(tmp_path: Path):
    app = _make_app(tmp_path, espn_players=FIXTURE_ESPN_PLAYERS)
    client = app.test_client()
    # slot 1 -> pick 1 is mine -- simulate/step must not auto-pick for me
    client.post("/api/setup", json={"my_draft_slot": 1})

    resp = client.post("/api/simulate/step", json={"randomness": 1.0})
    data = resp.get_json()

    assert data["current_pick"] == 1
    assert data["picks"] == {}


def test_simulate_step_chalk_picks_lowest_adp(tmp_path: Path):
    # Real live usage wants unseeded randomness each click (see app.py) --
    # but that made this test genuinely flaky (confirmed live: intermittent
    # failures across full-suite runs, not a fluke). `seed` is exposed
    # exactly so tests can pin it down without changing production
    # behavior at all (defaults to None => unseeded, same as before).
    app = _make_app(tmp_path, espn_players=FIXTURE_ESPN_PLAYERS)
    client = app.test_client()
    client.post("/api/setup", json={"my_draft_slot": 2})

    resp = client.post("/api/simulate/step", json={"randomness": 0, "seed": 1})
    data = resp.get_json()

    # randomness=0 -> effectively chalk -- pick 1 should go to the lowest-ADP player
    assert data["picks"]["1"]["player"] == "Star RB"


def test_simulate_step_rejects_bad_randomness(client):
    client.post("/api/setup", json={"my_draft_slot": 2})
    resp = client.post("/api/simulate/step", json={"randomness": "not-a-number"})
    assert resp.status_code == 400
