"""Match players across sources that don't share IDs (ESPN vs. manual ADP export).

Matching is name + position, with team as a tiebreaker for collisions,
a manual alias table for persistent mismatches, and fuzzy matching as a
last resort. Anything below the fuzzy-match confidence threshold is left
unmatched rather than guessed.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz, process

from fantasyprep.sources.espn import EspnPlayer
from fantasyprep.sources.manual_adp import SharpAdpEntry

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
FUZZY_MATCH_THRESHOLD = 85


def normalize_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[.'’]", "", name)
    name = re.sub(r"[-]", " ", name)
    name = re.sub(r"\s+", " ", name)
    parts = [p for p in name.split(" ") if p not in SUFFIXES]
    return " ".join(parts)


def load_aliases(path: Path) -> dict[tuple[str, str], str]:
    """Load manual overrides: (normalized_sharp_name, position) -> normalized_espn_name."""
    aliases: dict[tuple[str, str], str] = {}
    if not path.exists():
        return aliases

    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sharp_key = (normalize_name(row["sharp_name"]), row["position"].strip().upper())
            aliases[sharp_key] = normalize_name(row["espn_name"])
    return aliases


@dataclass(frozen=True)
class MatchedPlayer:
    espn: EspnPlayer
    sharp: SharpAdpEntry
    match_confidence: int  # 100 for exact/alias match, else fuzzy score


def match_players(
    espn_players: list[EspnPlayer],
    sharp_entries: list[SharpAdpEntry],
    aliases: dict[tuple[str, str], str] | None = None,
) -> tuple[list[MatchedPlayer], list[SharpAdpEntry], list[EspnPlayer]]:
    aliases = aliases or {}

    espn_by_key: dict[tuple[str, str], list[EspnPlayer]] = {}
    for ep in espn_players:
        espn_by_key.setdefault((normalize_name(ep.name), ep.position), []).append(ep)

    espn_names_by_position: dict[str, list[str]] = {}
    espn_name_lookup: dict[tuple[str, str], EspnPlayer] = {}
    for ep in espn_players:
        norm = normalize_name(ep.name)
        espn_names_by_position.setdefault(ep.position, []).append(norm)
        espn_name_lookup[(norm, ep.position)] = ep

    matched: list[MatchedPlayer] = []
    unmatched_sharp: list[SharpAdpEntry] = []
    matched_espn_ids: set[int] = set()

    for entry in sharp_entries:
        norm_name = normalize_name(entry.player_name)
        key = (norm_name, entry.position)

        alias_target = aliases.get(key)
        candidates = espn_by_key.get((alias_target, entry.position)) if alias_target else None
        if not candidates:
            candidates = espn_by_key.get(key)

        if candidates:
            chosen = _resolve_collision(candidates, entry.team)
            matched.append(MatchedPlayer(espn=chosen, sharp=entry, match_confidence=100))
            matched_espn_ids.add(chosen.espn_id)
            continue

        fuzzy = _fuzzy_match(norm_name, entry.position, espn_names_by_position)
        if fuzzy:
            fuzzy_name, score = fuzzy
            chosen = espn_name_lookup[(fuzzy_name, entry.position)]
            matched.append(MatchedPlayer(espn=chosen, sharp=entry, match_confidence=score))
            matched_espn_ids.add(chosen.espn_id)
            continue

        unmatched_sharp.append(entry)

    unmatched_espn = [ep for ep in espn_players if ep.espn_id not in matched_espn_ids]
    return matched, unmatched_sharp, unmatched_espn


def _resolve_collision(candidates: list[EspnPlayer], team: str) -> EspnPlayer:
    if len(candidates) == 1:
        return candidates[0]
    for c in candidates:
        if c.team == team:
            return c
    return candidates[0]


def _fuzzy_match(
    norm_name: str, position: str, espn_names_by_position: dict[str, list[str]]
) -> tuple[str, int] | None:
    pool = espn_names_by_position.get(position)
    if not pool:
        return None
    result = process.extractOne(norm_name, pool, scorer=fuzz.token_sort_ratio)
    if result is None:
        return None
    name, score, _ = result
    if score < FUZZY_MATCH_THRESHOLD:
        return None
    return name, int(score)
