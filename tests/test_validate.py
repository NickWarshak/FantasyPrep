import json
from pathlib import Path

from fantasyprep.historical.validate import (
    ActualsCheckRow,
    EspnCheckRow,
    cross_check_espn_projections,
)


def test_actuals_check_row_is_outlier_above_threshold():
    row = ActualsCheckRow(name="A", position="RB", year=2023, our_points=100.0, nflverse_points=95.0, delta=5.0)
    assert row.is_outlier is True


def test_actuals_check_row_not_outlier_within_threshold():
    row = ActualsCheckRow(name="A", position="RB", year=2023, our_points=100.0, nflverse_points=99.0, delta=1.0)
    assert row.is_outlier is False


def test_espn_check_row_is_outlier_above_threshold():
    row = EspnCheckRow(
        name="A", position="WR", full_ppr=300, half_ppr=250, standard=200,
        applied_total=280, best_fit_label="full_ppr", best_fit_delta=20,
    )
    assert row.is_outlier is True


def test_espn_check_row_not_outlier_within_threshold():
    row = EspnCheckRow(
        name="A", position="WR", full_ppr=300, half_ppr=250, standard=200,
        applied_total=298, best_fit_label="full_ppr", best_fit_delta=2,
    )
    assert row.is_outlier is False


def _season_stat_entry(applied_total, applied_average, **stat_ids):
    return {
        "scoringPeriodId": 0, "statSourceId": 1, "statSplitTypeId": 0,
        "appliedTotal": applied_total, "appliedAverage": applied_average,
        "stats": {k: v for k, v in stat_ids.items()},
    }


FIXTURE_RAW = [
    {
        # RB, ~230 full-PPR points, appliedTotal close to full-PPR -> best fit full_ppr, small delta
        "id": 1, "fullName": "Normal Guy", "defaultPositionId": 2, "proTeamId": 1,
        "stats": [_season_stat_entry(232.0, 13.6, **{"24": 1000, "25": 8, "42": 300, "43": 2, "53": 40})],
    },
    {
        # QB with an implausible appliedAverage -- should land in anomalies, not rows
        "id": 2, "fullName": "Anomaly Guy", "defaultPositionId": 1, "proTeamId": 1,
        "stats": [_season_stat_entry(1190.0, 70.0, **{"3": 4000, "4": 30, "20": 10})],
    },
    {
        # WR, heavy target volume -- appliedTotal much closer to half-PPR than full-PPR
        "id": 3, "fullName": "Half PPR Guy", "defaultPositionId": 3, "proTeamId": 1,
        "stats": [_season_stat_entry(220.0, 12.9, **{"42": 1200, "43": 8, "53": 100})],
    },
]


def test_cross_check_espn_projections_separates_anomalies(tmp_path: Path):
    cache_path = tmp_path / "espn_fixture.json"
    cache_path.write_text(json.dumps(FIXTURE_RAW), encoding="utf-8")

    rows, anomalies = cross_check_espn_projections(2026, cache_path=cache_path)

    row_names = {r.name for r in rows}
    anomaly_names = {a.name for a in anomalies}
    assert "Normal Guy" in row_names
    assert "Half PPR Guy" in row_names
    assert "Anomaly Guy" in anomaly_names
    assert "Anomaly Guy" not in row_names


def test_cross_check_espn_projections_anomaly_carries_applied_values(tmp_path: Path):
    cache_path = tmp_path / "espn_fixture.json"
    cache_path.write_text(json.dumps(FIXTURE_RAW), encoding="utf-8")

    _, anomalies = cross_check_espn_projections(2026, cache_path=cache_path)

    anomaly = next(a for a in anomalies if a.name == "Anomaly Guy")
    assert anomaly.applied_average == 70.0
    assert anomaly.applied_total == 1190.0


def test_cross_check_espn_projections_picks_best_fit_variant(tmp_path: Path):
    cache_path = tmp_path / "espn_fixture.json"
    cache_path.write_text(json.dumps(FIXTURE_RAW), encoding="utf-8")

    rows, _ = cross_check_espn_projections(2026, cache_path=cache_path)

    half_ppr_guy = next(r for r in rows if r.name == "Half PPR Guy")
    assert half_ppr_guy.best_fit_label == "half_ppr"

    normal_guy = next(r for r in rows if r.name == "Normal Guy")
    assert normal_guy.best_fit_label == "full_ppr"
    assert abs(normal_guy.best_fit_delta) < 5
