import json
from pathlib import Path

from fantasyprep.sources.sleeper import fetch_sleeper_players

RAW_FIXTURE = {
    "9221": {
        "full_name": "Jahmyr Gibbs",
        "position": "RB",
        "team": "DET",
        "search_rank": 1,
    },
    "4034": {
        "full_name": "Backup Nobody Tracks",
        "position": "RB",
        "team": "DET",
        "search_rank": None,  # unranked -- should be dropped
    },
    "5555": {
        "first_name": "Some",
        "last_name": "Kicker",
        "full_name": None,
        "position": "K",
        "team": "kc",
        "search_rank": 250,
    },
    "6666": {
        "full_name": "Some Lineman",
        "position": "OT",  # not a fantasy position -- should be dropped
        "team": "SF",
        "search_rank": 4000,
    },
}


def test_fetch_sleeper_players_normalizes_and_filters(tmp_path: Path):
    cache_path = tmp_path / "sleeper_cache.json"
    cache_path.write_text(json.dumps(RAW_FIXTURE), encoding="utf-8")

    entries = fetch_sleeper_players(cache_path=cache_path)

    names = {e.player_name for e in entries}
    assert "Jahmyr Gibbs" in names
    assert "Backup Nobody Tracks" not in names  # no search_rank
    assert "Some Lineman" not in names  # non-fantasy position
    assert "Some Kicker" in names  # falls back to first/last name

    gibbs = next(e for e in entries if e.player_name == "Jahmyr Gibbs")
    assert gibbs.adp == 1.0
    assert gibbs.source == "sleeper"
    assert gibbs.team == "DET"

    kicker = next(e for e in entries if e.player_name == "Some Kicker")
    assert kicker.team == "KC"  # uppercased


def test_fetch_sleeper_players_sorted_by_rank(tmp_path: Path):
    cache_path = tmp_path / "sleeper_cache.json"
    cache_path.write_text(json.dumps(RAW_FIXTURE), encoding="utf-8")

    entries = fetch_sleeper_players(cache_path=cache_path)

    ranks = [e.adp for e in entries]
    assert ranks == sorted(ranks)
