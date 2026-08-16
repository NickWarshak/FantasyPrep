import json
from pathlib import Path

from fantasyprep.league.settings import ScoringSettings
from fantasyprep.sources.espn import (
    _compute_points_from_espn_stats,
    _season_projection_raw_stats,
    fetch_espn_players,
    fetch_espn_projected_points,
)

RAW_PLAYERS_FIXTURE = [
    {
        "id": 1, "fullName": "Elite RB", "defaultPositionId": 2, "proTeamId": 1,
        "ownership": {"averageDraftPosition": 1.2},
        "draftRanksByRankType": {"STANDARD": {"rank": 1}},
    },
    {
        "id": 2, "fullName": "Second WR", "defaultPositionId": 3, "proTeamId": 2,
        "ownership": {"averageDraftPosition": 8.0},
        "draftRanksByRankType": {"STANDARD": {"rank": 5}},
    },
    {
        # Never actually drafted (adp 0) -- should be filtered out, no signal.
        "id": 3, "fullName": "Undrafted Guy", "defaultPositionId": 2, "proTeamId": 3,
        "ownership": {"averageDraftPosition": 0.0},
    },
    {
        # Offensive lineman -- not a fantasy position, filtered out.
        "id": 4, "fullName": "Some Lineman", "defaultPositionId": 99, "proTeamId": 4,
        "ownership": {"averageDraftPosition": 50.0},
    },
]


def test_fetch_espn_players_filters_undrafted_and_non_fantasy_positions(tmp_path: Path):
    cache_path = tmp_path / "espn_cache.json"
    cache_path.write_text(json.dumps(RAW_PLAYERS_FIXTURE), encoding="utf-8")

    players = fetch_espn_players(year=2026, cache_path=cache_path)

    names = {p.name for p in players}
    assert names == {"Elite RB", "Second WR"}


def test_fetch_espn_players_maps_position_team_and_rank(tmp_path: Path):
    cache_path = tmp_path / "espn_cache.json"
    cache_path.write_text(json.dumps(RAW_PLAYERS_FIXTURE), encoding="utf-8")

    players = fetch_espn_players(year=2026, cache_path=cache_path)
    rb = next(p for p in players if p.name == "Elite RB")

    assert rb.position == "RB"
    assert rb.team == "ATL"  # proTeamId 1
    assert rb.espn_adp == 1.2
    assert rb.espn_expert_rank == 1


def test_fetch_espn_players_sorted_by_adp(tmp_path: Path):
    cache_path = tmp_path / "espn_cache.json"
    cache_path.write_text(json.dumps(RAW_PLAYERS_FIXTURE), encoding="utf-8")

    players = fetch_espn_players(year=2026, cache_path=cache_path)
    adps = [p.espn_adp for p in players]
    assert adps == sorted(adps)


def test_fetch_espn_players_unmapped_team_falls_back_to_fa(tmp_path: Path):
    raw = [{
        "id": 9, "fullName": "Mystery Team Guy", "defaultPositionId": 1, "proTeamId": 9999,
        "ownership": {"averageDraftPosition": 12.0},
    }]
    cache_path = tmp_path / "espn_cache.json"
    cache_path.write_text(json.dumps(raw), encoding="utf-8")

    players = fetch_espn_players(year=2026, cache_path=cache_path)
    assert players[0].team == "FA"


# --- _season_projection_raw_stats: picking the right entry out of several ---


def test_season_projection_raw_stats_finds_the_season_aggregate_projected_entry():
    player = {
        "stats": [
            {"scoringPeriodId": 1, "statSourceId": 0, "statSplitTypeId": 1, "stats": {"3": 1.0}},  # weekly actual
            {"scoringPeriodId": 0, "statSourceId": 0, "statSplitTypeId": 0, "stats": {"3": 2.0}},  # season actual
            {"scoringPeriodId": 0, "statSourceId": 1, "statSplitTypeId": 0, "stats": {"3": 4000.0}},  # the one we want
        ]
    }
    stats = _season_projection_raw_stats(player)
    assert stats == {"3": 4000.0}


def test_season_projection_raw_stats_returns_none_when_absent():
    player = {"stats": [{"scoringPeriodId": 1, "statSourceId": 0, "statSplitTypeId": 1, "stats": {"3": 1.0}}]}
    assert _season_projection_raw_stats(player) is None


def test_season_projection_raw_stats_handles_missing_stats_key():
    assert _season_projection_raw_stats({}) is None


# --- _compute_points_from_espn_stats: the reception 41/53 fallback quirk ---


def test_compute_points_reception_uses_id_41_when_populated():
    stats = {"41": 80.0, "3": 4000.0}
    points = _compute_points_from_espn_stats(stats, ScoringSettings())
    # 80 receptions * 1.0 (full PPR) + 4000 pass yards * 0.04 = 80 + 160 = 240
    assert points == 240.0


def test_compute_points_reception_falls_back_to_id_53_when_41_missing():
    # The real-world quirk: id 41 present but None, id 53 has the actual value.
    stats = {"41": None, "53": 80.0}
    points = _compute_points_from_espn_stats(stats, ScoringSettings())
    assert points == 80.0  # 80 receptions * 1.0 PPR


def test_compute_points_missing_stat_ids_default_to_zero():
    points = _compute_points_from_espn_stats({}, ScoringSettings())
    assert points == 0.0


def test_compute_points_full_scoring_formula():
    stats = {
        "3": 300.0,   # pass yards
        "4": 2.0,     # pass td
        "20": 1.0,    # interception
        "24": 100.0,  # rush yards
        "25": 1.0,    # rush td
        "53": 5.0,    # receptions (via fallback id)
        "42": 50.0,   # rec yards
        "43": 1.0,    # rec td
        "72": 1.0,    # fumble lost
    }
    scoring = ScoringSettings()
    expected = (
        300 * scoring.pass_yard + 2 * scoring.pass_td + 1 * scoring.interception
        + 100 * scoring.rush_yard + 1 * scoring.rush_td
        + 5 * scoring.reception + 50 * scoring.rec_yard + 1 * scoring.rec_td
        + 1 * scoring.fumble_lost
    )
    assert _compute_points_from_espn_stats(stats, scoring) == expected


# --- fetch_espn_projected_points: end-to-end, only skill positions ---


def test_fetch_espn_projected_points_only_covers_skill_positions(tmp_path: Path):
    raw = [
        {
            "id": 1, "fullName": "Some QB", "defaultPositionId": 1,
            "stats": [{"scoringPeriodId": 0, "statSourceId": 1, "statSplitTypeId": 0, "stats": {"3": 4000.0}}],
        },
        {
            "id": 2, "fullName": "Some Kicker", "defaultPositionId": 5,  # K -- not modeled, should be excluded
            "stats": [{"scoringPeriodId": 0, "statSourceId": 1, "statSplitTypeId": 0, "stats": {"3": 1.0}}],
        },
    ]
    cache_path = tmp_path / "espn_cache.json"
    cache_path.write_text(json.dumps(raw), encoding="utf-8")

    points = fetch_espn_projected_points(year=2026, scoring=ScoringSettings(), cache_path=cache_path)

    assert "Some QB" in points
    assert "Some Kicker" not in points
    assert points["Some QB"] == 160.0  # 4000 * 0.04


def test_fetch_espn_projected_points_skips_players_without_a_projection_entry(tmp_path: Path):
    raw = [{"id": 1, "fullName": "No Projection Guy", "defaultPositionId": 1, "stats": []}]
    cache_path = tmp_path / "espn_cache.json"
    cache_path.write_text(json.dumps(raw), encoding="utf-8")

    points = fetch_espn_projected_points(year=2026, scoring=ScoringSettings(), cache_path=cache_path)
    assert points == {}
