"""Draft-time market expectation (ADP) as a joinable per-player-season column.

The one piece the modeling frame was missing. `preseason_frame()` already
carries everything knowable about a player from his own history; this adds what
the *market* thought of him before the season, so the two can be compared
head-to-head rather than assumed to be complements.

THE JOIN IS BY NAME, AND THAT IS THE WEAK LINK

FFC returns names, not ids -- unlike every other source in this pipeline, which
shares the gsis `player_id`. So this is the one place fuzzy-ish matching is
unavoidable, and a silent join failure here would be especially damaging: it
would make ADP look less informative than it is, and the whole point of the
benchmark this feeds is to decide whether ADP is worth acquiring more of. A
plumbing artefact would produce exactly the wrong strategic conclusion.

So the match rate is measured and returned rather than assumed. Currently
**97.8% across 2010-2024** (2,407 of 2,461 ADP entries), worst season 96.4%.
Most of the residual is drafted players who never recorded a snap that season --
legitimate attrition rather than a broken join, since a player with no stat row
has no outcome to predict either.

Coverage floor is 2010: FFC PPR simply has no earlier data (see
docs/HISTORICAL_ADP_RESEARCH.md). Reads only caches already on disk, so this
stays offline and deterministic.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from fantasyprep.historical.sources import ffc
from fantasyprep.league.settings import LeagueSettings, default_settings
from fantasyprep.players.normalize import normalize_name

# FFC PPR's hard coverage floor -- verified by direct probe, not assumed.
ADP_FIRST_SEASON = 2010

SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

# Market columns are pre-season by definition: an ADP is measured *before* the
# season it describes, which is the entire reason it's a legal model input.
MARKET_FEATURE_COLUMNS = ("adp", "adp_position_rank", "adp_stdev", "has_adp")


def load_adp_seasons(
    seasons: list[int] | None = None,
    settings: LeagueSettings | None = None,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """One row per (season, player name, position) with that season's ADP.

    A season with no cached FFC file is skipped rather than fetched, keeping
    this offline; callers can see which seasons landed via the result's
    `season` column.
    """
    settings = settings or default_settings()
    cache_dir = cache_dir or Path("data/raw")
    seasons = seasons or list(range(ADP_FIRST_SEASON, 2025))

    rows = []
    for season in seasons:
        cache_path = cache_dir / f".ffc_{settings.teams}_{season}.json"
        if not cache_path.exists():
            continue
        players = ffc.fetch_adp(season, teams=settings.teams, cache_path=cache_path)
        ranks = ffc.position_ranks(players)
        for player in players:
            if player.position not in SKILL_POSITIONS:
                continue
            rows.append(
                {
                    "season": season,
                    "join_name": normalize_name(player.name),
                    "fantasy_position": player.position,
                    "adp": player.adp,
                    "adp_position_rank": ranks[player.name],
                    "adp_stdev": player.stdev,
                }
            )
    return pd.DataFrame(rows)


def attach_adp(
    features: pd.DataFrame, adp: pd.DataFrame | None = None, cache_dir: Path | None = None
) -> tuple[pd.DataFrame, dict]:
    """Left-join ADP onto a feature frame, returning (frame, match report).

    Left join: a player with no ADP keeps his row with `has_adp=False`. That
    distinction is itself signal -- "the market did not draft this player" is
    information, not absence of it -- and dropping those rows would quietly
    restrict the whole benchmark to drafted players.
    """
    adp = load_adp_seasons(cache_dir=cache_dir) if adp is None else adp
    adp, ambiguous = _drop_ambiguous_names(adp)

    features = features.copy()
    features["join_name"] = features["player_name"].map(normalize_name)

    # validate="m:1" is a deliberate tripwire, not decoration: it is what caught
    # the colliding-name case below. Never relax it to make a merge succeed.
    merged = features.merge(
        adp, on=["season", "join_name", "fantasy_position"], how="left", validate="m:1"
    )
    merged["has_adp"] = merged["adp"].notna()

    report = _match_report(adp, features, merged)
    report["ambiguous_names_dropped"] = ambiguous
    return merged.drop(columns=["join_name"]), report


def _drop_ambiguous_names(adp: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Remove ADP entries whose (season, name, position) matches another player.

    Real, not hypothetical: two different Mike Williamses (Tampa Bay and
    Seattle) and two different Steve Smiths (Carolina and the Giants) were
    active receivers in both 2010 and 2011. Name-based joining genuinely cannot
    tell them apart, and their ADPs are wildly different -- 42.1 versus 156.0
    for the 2011 Mike Williamses.

    Guessing would be worse than abstaining here. Assigning one player's ADP to
    the other injects a large, confidently-wrong market signal into the exact
    benchmark meant to measure whether the market signal is any good. These rows
    fall through to `has_adp=False` instead, which is honest: we do not know
    what the market thought of *this* player.

    Team is not used as a tiebreaker on purpose -- the features table's
    `recent_team` is the player's *last* team of the season while ADP's is
    preseason, so a midseason trade would silently resolve the tie backwards.

    Costs 8 of 2,461 ADP entries (0.3%).

    Related, and worth fixing separately: `ffc.position_ranks` builds a
    name-keyed dict, so colliding names overwrite one another and BOTH players
    come back with the same positional rank. Visible in the raw data above --
    both 2011 Mike Williamses report rank 62 despite ADPs 114 picks apart.
    """
    key = ["season", "join_name", "fantasy_position"]
    duplicated = adp.duplicated(key, keep=False)
    dropped = adp[duplicated]
    report = {
        "n_entries_dropped": int(duplicated.sum()),
        "names": sorted({str(n) for n in dropped["join_name"]}),
    }
    return adp[~duplicated].copy(), report


def _match_report(adp: pd.DataFrame, features: pd.DataFrame, merged: pd.DataFrame) -> dict:
    """How much of the ADP side actually found a player-season, per season.

    Reported from the *ADP* side deliberately. Measuring from the feature side
    would flatter the join, since most player-seasons legitimately have no ADP
    (deep bench players the market never drafted).
    """
    feature_keys = set(
        zip(features["season"], features["join_name"], features["fantasy_position"])
    )
    adp_keys = list(zip(adp["season"], adp["join_name"], adp["fantasy_position"]))

    by_season: dict[int, dict] = {}
    for season, name, position in adp_keys:
        entry = by_season.setdefault(int(season), {"adp_entries": 0, "matched": 0})
        entry["adp_entries"] += 1
        if (season, name, position) in feature_keys:
            entry["matched"] += 1
    for entry in by_season.values():
        entry["match_rate"] = round(entry["matched"] / entry["adp_entries"], 4)

    total = sum(e["adp_entries"] for e in by_season.values())
    matched = sum(e["matched"] for e in by_season.values())
    return {
        "adp_entries": total,
        "matched": matched,
        "overall_match_rate": round(matched / total, 4) if total else 0.0,
        "by_season": dict(sorted(by_season.items())),
        "player_seasons_with_adp": int(merged["has_adp"].sum()),
        "player_seasons_total": len(merged),
    }
