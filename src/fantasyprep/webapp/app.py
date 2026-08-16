"""Local web UI for the Monte Carlo draft simulator: a Sleeper-style grid.

Usage:
    python -m fantasyprep.webapp.app --year 2026 --draft-state data/draft_state_live.json

Single-user, local-only tool -- no auth, in-memory session, autosaves to
the same draft-state JSON format the CLI (draft_sim.simulate) reads, so
either tool can pick up where the other left off. Any grid cell can be
assigned a player: the current pick for live drafting, or any future cell
for a keeper (pre-assigned before the live draft reaches it) -- same
mechanism either way.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from fantasyprep.draft_sim.auto_pick import espn_pool_for_auto_pick
from fantasyprep.draft_sim.opponent import sample_pick
from fantasyprep.draft_sim.points_model import EspnProjectionModel, HistoricalBootstrapModel, PointsModel
from fantasyprep.draft_sim.simulate import (
    current_pick_number,
    pick_owner,
    recommend_positions,
    state_from_picks,
)
from fantasyprep.historical.outcomes import build_outcome_distributions
from fantasyprep.historical.sources import ffc
from fantasyprep.sources import espn
from fantasyprep.league.settings import LeagueSettings, default_settings
from fantasyprep.players.normalize import normalize_name

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


class DraftSession:
    """Mutable draft-in-progress state, autosaved to a JSON file after every change."""

    def __init__(self, path: Path, settings: LeagueSettings, live_pool: list[ffc.FfcPlayer]):
        self.path = path
        self.settings = settings
        self.by_name = {normalize_name(p.name): p for p in live_pool}
        self.my_draft_slot: int | None = None
        self.picks: list[dict] = []  # [{"pick": N, "player": name}, ...], any order, gaps allowed
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.my_draft_slot = raw.get("my_draft_slot")
        # tolerate legacy files that had a "mine" field -- ignored, it's derived now
        self.picks = [{"pick": p["pick"], "player": p["player"]} for p in raw.get("picks", [])]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"teams": self.settings.teams, "my_draft_slot": self.my_draft_slot, "picks": self.picks},
                indent=2,
            ),
            encoding="utf-8",
        )

    def state(self):
        if self.my_draft_slot is None:
            raise ValueError("Draft slot not set yet -- call /api/setup first")
        return state_from_picks(self.settings.teams, self.my_draft_slot, self.picks)

    def total_rounds(self) -> int:
        return sum(self.settings.roster_slots.values()) + self.settings.bench

    def drafted_names(self) -> set[str]:
        return {normalize_name(p["player"]) for p in self.picks}

    def set_pick(self, pick_number: int, player_name: str) -> str | None:
        """Assign a player to a specific pick slot. Returns an error message, or None on success."""
        total_picks = self.settings.teams * self.total_rounds()
        if not 1 <= pick_number <= total_picks:
            return f"pick_number must be 1-{total_picks}"
        if any(p["pick"] == pick_number for p in self.picks):
            return f"pick {pick_number} is already assigned"
        if normalize_name(player_name) in self.drafted_names():
            return f"{player_name} is already drafted"

        self.picks.append({"pick": pick_number, "player": player_name})
        self.save()
        return None

    def clear_pick(self, pick_number: int) -> None:
        self.picks = [p for p in self.picks if p["pick"] != pick_number]
        self.save()

    def to_dict(self) -> dict:
        total_rounds = self.total_rounds()
        total_picks = self.settings.teams * total_rounds
        current = current_pick_number(self.picks)

        by_pick = {}
        for p in self.picks:
            owner = pick_owner(self.settings.teams, p["pick"])
            player = self.by_name.get(normalize_name(p["player"]))
            by_pick[p["pick"]] = {
                "player": p["player"],
                "position": player.position if player else None,
                "team": player.team if player else None,
                "owner": owner,
                "mine": owner == self.my_draft_slot,
            }

        return {
            "teams": self.settings.teams,
            "my_draft_slot": self.my_draft_slot,
            "current_pick": current,
            "total_rounds": total_rounds,
            "total_picks": total_picks,
            "picks": by_pick,
            "next_pick_is_mine": (
                pick_owner(self.settings.teams, current) == self.my_draft_slot
                if self.my_draft_slot is not None
                else None
            ),
        }


def _ffc_scoring_slug(settings: LeagueSettings) -> str:
    if settings.scoring.reception == 1.0:
        return "ppr"
    if settings.scoring.reception == 0.5:
        return "half-ppr"
    return "standard"


def create_app(
    year: int,
    draft_state_path: Path,
    data_dir: Path,
    num_sims: int,
    settings: LeagueSettings | None = None,
    live_pool: list[ffc.FfcPlayer] | None = None,
    distributions=None,
    espn_points: dict[str, float] | None = None,
    espn_players: list[espn.EspnPlayer] | None = None,
) -> Flask:
    """Build the Flask app. `live_pool`/`distributions`/`espn_points`/
    `espn_players` can be injected (e.g. small fixtures in tests) to skip
    the live-network/historical-build cost at startup."""
    app = Flask(__name__, template_folder=str(TEMPLATE_DIR), static_folder=str(STATIC_DIR))
    settings = settings or default_settings()
    raw_dir = data_dir / "raw"

    if live_pool is None:
        print(f"Loading live {year} ADP ({settings.teams}-team, {_ffc_scoring_slug(settings)})...")
        live_cache = raw_dir / f".ffc_{settings.teams}_{year}.json"
        live_pool = ffc.fetch_adp(year, teams=settings.teams, scoring=_ffc_scoring_slug(settings), cache_path=live_cache)
        print(f"  {len(live_pool)} players loaded")

    if distributions is None:
        print("Loading historical outcome distributions (2018-2024)...")
        hist_cache = raw_dir / f".outcomes_{settings.teams}.json"
        distributions = build_outcome_distributions(settings, cache_path=hist_cache, adp_cache_dir=raw_dir)
        print(f"  {len(distributions)} (position, bucket) distributions loaded")

    historical_model = HistoricalBootstrapModel(distributions)
    espn_model_cache: dict[str, PointsModel] = {}

    def get_points_model(points_source: str) -> PointsModel:
        if points_source == "historical":
            return historical_model
        if "espn" not in espn_model_cache:
            if espn_points is not None:
                points = espn_points
            else:
                espn_cache = raw_dir / f".espn_cache_{year}.json"
                points = espn.fetch_espn_projected_points(year, settings.scoring, cache_path=espn_cache)
            espn_model_cache["espn"] = EspnProjectionModel(points, fallback=historical_model)
        return espn_model_cache["espn"]

    espn_players_cache: dict[str, list[espn.EspnPlayer]] = {}

    def get_espn_players() -> list[espn.EspnPlayer]:
        if "players" not in espn_players_cache:
            if espn_players is not None:
                players = espn_players
            else:
                espn_cache = raw_dir / f".espn_cache_{year}.json"
                players = espn.fetch_espn_players(year, cache_path=espn_cache)
            espn_players_cache["players"] = players
        return espn_players_cache["players"]

    session = DraftSession(draft_state_path, settings, live_pool)

    def undrafted_pool() -> list[ffc.FfcPlayer]:
        drafted = session.drafted_names()
        return [p for p in live_pool if normalize_name(p.name) not in drafted]

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/state")
    def get_state():
        return jsonify(session.to_dict())

    @app.post("/api/setup")
    def setup():
        body = request.get_json(force=True)
        slot = int(body["my_draft_slot"])
        if not 1 <= slot <= settings.teams:
            return jsonify({"error": f"my_draft_slot must be 1-{settings.teams}"}), 400
        session.my_draft_slot = slot
        session.save()
        return jsonify(session.to_dict())

    @app.get("/api/players")
    def search_players():
        query = request.args.get("q", "").strip().lower()
        if not query:
            return jsonify([])
        matches = [p for p in undrafted_pool() if query in p.name.lower()]
        matches.sort(key=lambda p: p.adp)
        return jsonify(
            [{"name": p.name, "position": p.position, "team": p.team, "adp": p.adp} for p in matches[:15]]
        )

    @app.put("/api/picks/<int:pick_number>")
    def assign_pick(pick_number: int):
        if session.my_draft_slot is None:
            return jsonify({"error": "Call /api/setup first"}), 400
        body = request.get_json(force=True)
        error = session.set_pick(pick_number, body["player_name"])
        if error:
            return jsonify({"error": error}), 400
        return jsonify(session.to_dict())

    @app.delete("/api/picks/<int:pick_number>")
    def clear_pick(pick_number: int):
        session.clear_pick(pick_number)
        return jsonify(session.to_dict())

    @app.post("/api/reset")
    def reset():
        session.picks = []
        session.save()
        return jsonify(session.to_dict())

    @app.get("/api/recommend")
    def recommend():
        if session.my_draft_slot is None:
            return jsonify({"error": "Call /api/setup first"}), 400
        state = session.state()
        seed = request.args.get("seed", type=int)
        sims = request.args.get("num_sims", default=num_sims, type=int)
        points_source = request.args.get("points_source", default="historical")
        if points_source not in ("historical", "espn"):
            return jsonify({"error": "points_source must be 'historical' or 'espn'"}), 400
        rng = random.Random(seed)

        points_model = get_points_model(points_source)
        rows = recommend_positions(live_pool, state, settings, points_model, sims, rng)
        return jsonify(
            [{"position": pos, "expected": mean, "p25": p25, "p75": p75} for pos, mean, p25, p75 in rows]
        )

    @app.post("/api/simulate/step")
    def simulate_step():
        """Auto-pick exactly one opponent pick (ESPN ADP + tunable randomness),
        one call per pick so the frontend can pace/animate and stop cleanly.
        Does nothing (just returns current state) if the current pick is mine
        or the draft is already complete -- that's the frontend's stop signal.
        """
        if session.my_draft_slot is None:
            return jsonify({"error": "Call /api/setup first"}), 400
        body = request.get_json(silent=True) or {}
        try:
            randomness = float(body.get("randomness", 1.0))
        except (TypeError, ValueError):
            return jsonify({"error": "randomness must be a number"}), 400
        # Real live drafting wants genuine randomness each click (unseeded,
        # the default) -- but that makes outcomes non-reproducible for
        # tests. An optional seed lets tests pin it down without changing
        # real usage at all.
        seed = body.get("seed")

        state = session.state()
        if state.current_pick > session.settings.teams * session.total_rounds():
            return jsonify(session.to_dict())  # draft complete, nothing to do
        if pick_owner(session.settings.teams, state.current_pick) == session.my_draft_slot:
            return jsonify(session.to_dict())  # my turn -- stop, don't auto-pick for myself

        pool = espn_pool_for_auto_pick(get_espn_players(), randomness)
        undrafted = [p for p in pool if normalize_name(p.name) not in state.drafted_names]
        if undrafted:
            chosen = sample_pick(undrafted, state.current_pick, random.Random(seed))
            session.set_pick(state.current_pick, chosen.name)
        return jsonify(session.to_dict())

    return app


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--draft-state", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--num-sims", type=int, default=300)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    app = create_app(args.year, args.draft_state, args.data_dir, args.num_sims)
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
