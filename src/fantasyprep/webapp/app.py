"""Local web UI for the Monte Carlo draft simulator: a Sleeper-style grid.

Usage:
    python -m fantasyprep.webapp.app --year 2026 --draft-state data/draft_state_live.json

Single-user, local-only tool -- no auth, in-memory session, autosaves to
the same draft-state JSON format the CLI (draft_sim.simulate) reads, so
either tool can pick up where the other left off. Any grid cell can be
assigned a player: the current pick for live drafting, or any future cell
for a keeper (pre-assigned before the live draft reaches it) -- same
mechanism either way.

Recommendations and simulated opponent picks both use
`opponent.pick_weight_with_tail_floor` (2026-08-16), not the plain Gaussian
`pick_weight` -- fixes the "stuck player" bug where a player fallen many
standard deviations past ADP incorrectly became near-impossible to draft
instead of near-certain. This is the one piece of tonight's backtest work
that's an actual model-behavior fix rather than an evaluation-only change
(VOR, the derived replacement cutoffs, waiver-adjusted scoring, and the CI
clustering fix all live in the backtest harness and don't affect what this
tool recommends -- VOR in particular is a baseline the model is compared
against, not a component of the model itself).

Live player pool defaults to FantasyPros' overall consensus rankings CSV
(`--rankings-source fantasypros`, the default), not FFC ADP -- real expert
rank order and much deeper coverage (500+ players vs. FFC's ~260), but
stdev/high/low are approximated from tier width rather than real
draft-position variance (see `historical/sources/fantasypros_rankings.py`
for the exact tradeoff). Pass `--rankings-source ffc` to use FFC's live ADP
instead, which has real variance data but shallower coverage.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from fantasyprep.draft_sim.auto_pick import espn_pool_for_auto_pick
from fantasyprep.draft_sim.draft_now_vs_wait import compare_now_vs_wait
from fantasyprep.draft_sim.opponent import pick_weight_with_tail_floor, sample_pick
from fantasyprep.historical.sources.fantasypros_rankings import load_fantasypros_rankings
from fantasyprep.draft_sim.points_model import EspnProjectionModel, HistoricalBootstrapModel, PointsModel
from fantasyprep.draft_sim.simulate import (
    best_value_within_position,
    current_pick_number,
    pick_owner,
    position_confidence,
    recommend_positions,
    resolve_pick,
    state_from_picks,
)
from fantasyprep.historical.outcomes import build_outcome_distributions
from fantasyprep.historical.sources import ffc
from fantasyprep.sources import espn, espn_futures
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
        # Keepers persist across reset_draft() -- picks are wiped back down to
        # just these instead of to nothing, so a keeper only needs entering
        # once, not after every reset. A pick becomes a keeper automatically
        # when it's assigned somewhere other than the actual current pick
        # (see set_pick) -- same "ahead of the current pick" concept the UI
        # already used for keepers, just now remembered instead of forgotten.
        self.keepers: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.my_draft_slot = raw.get("my_draft_slot")
        # tolerate legacy files that had a "mine" field -- ignored, it's derived now
        self.picks = [{"pick": p["pick"], "player": p["player"]} for p in raw.get("picks", [])]
        self.keepers = [{"pick": p["pick"], "player": p["player"]} for p in raw.get("keepers", [])]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "teams": self.settings.teams, "my_draft_slot": self.my_draft_slot,
                    "picks": self.picks, "keepers": self.keepers,
                },
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
        """Assign a player to a specific pick slot. Returns an error message, or None on success.

        Assigning anywhere other than the actual current pick means this is a
        keeper (pre-assigned ahead of where the live/simulated draft has
        reached) -- automatically remembered in `self.keepers` too, so it
        survives `reset_draft()` instead of needing to be re-entered."""
        total_picks = self.settings.teams * self.total_rounds()
        if not 1 <= pick_number <= total_picks:
            return f"pick_number must be 1-{total_picks}"
        if any(p["pick"] == pick_number for p in self.picks):
            return f"pick {pick_number} is already assigned"
        if normalize_name(player_name) in self.drafted_names():
            return f"{player_name} is already drafted"

        current = current_pick_number(self.picks)
        entry = {"pick": pick_number, "player": player_name}
        self.picks.append(entry)
        if pick_number != current:
            self.keepers = [k for k in self.keepers if k["pick"] != pick_number]
            self.keepers.append(entry)
        self.save()
        return None

    def clear_pick(self, pick_number: int) -> None:
        self.picks = [p for p in self.picks if p["pick"] != pick_number]
        self.keepers = [p for p in self.keepers if p["pick"] != pick_number]
        self.save()

    def reset_draft(self) -> None:
        """Wipe picks back down to just the saved keepers, not to nothing."""
        self.picks = list(self.keepers)
        self.save()

    def to_dict(self) -> dict:
        total_rounds = self.total_rounds()
        total_picks = self.settings.teams * total_rounds
        current = current_pick_number(self.picks)
        keeper_picks = {k["pick"] for k in self.keepers}

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
                "is_keeper": p["pick"] in keeper_picks,
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
    upside_scores: dict[str, float] | None = None,
) -> Flask:
    """Build the Flask app. `live_pool`/`distributions`/`espn_points`/
    `espn_players`/`upside_scores` can be injected (e.g. small fixtures in
    tests) to skip
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

    upside_cache: dict[str, dict[str, float]] = {}

    def get_upside() -> dict[str, float]:
        """Market-implied ceiling per player, from OPOY futures.

        A deliberately different signal from ADP rather than another projection.
        The research established that the market already prices the MEDIAN well
        (prior production adds +0.0075 r2 on top of ADP), while the system's
        worst measured calibration defect is understated UPSIDE for exactly the
        early picks that decide a draft. Award futures speak to the second.

        Degrades to an empty mapping rather than failing the recommendation: a
        live draft must never break because an odds endpoint is unreachable.
        """
        if "scores" not in upside_cache:
            if upside_scores is not None:
                upside_cache["scores"] = upside_scores
                return upside_cache["scores"]
            try:
                futures = espn_futures.fetch_award_futures(
                    year, cache_path=raw_dir / f".espn_futures_{year}.json"
                )
                name_by_id = {
                    str(p.espn_id): normalize_name(p.name) for p in get_espn_players()
                }
                upside_cache["scores"] = espn_futures.upside_by_name(futures, name_by_id)
            except Exception:
                upside_cache["scores"] = {}
        return upside_cache["scores"]

    def _rank_by_upside(pool, upside: dict[str, float]) -> dict[str, int]:
        """Rank among still-available players by market-implied ceiling.

        Rank rather than raw probability, everywhere it is shown: a 3.9% OPOY
        chance means nothing to a drafter in isolation, while "2nd highest
        ceiling left on the board" is immediately actionable. Computed over the
        whole undrafted pool so the picker and the recommendation cards cannot
        disagree about what "3rd" means.
        """
        priced = sorted(
            ((normalize_name(p.name), upside[normalize_name(p.name)])
             for p in pool if normalize_name(p.name) in upside),
            key=lambda kv: -kv[1],
        )
        return {name: i for i, (name, _) in enumerate(priced, start=1)}

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
        pool = undrafted_pool()
        # No query -- default to best-available-by-ADP instead of nothing, so
        # opening the picker shows useful options immediately, before typing.
        matches = [p for p in pool if query in p.name.lower()] if query else list(pool)
        matches.sort(key=lambda p: p.adp)

        # Market-implied ceiling on every searched player, not just on the ones
        # the model happens to recommend. The picker is where a human actually
        # weighs two names against each other, so it is where a signal the model
        # deliberately does NOT consume is most useful -- ADP already tells you
        # the ordering, and this tells you something ADP does not.
        upside = get_upside()
        ranked = _rank_by_upside(pool, upside)
        return jsonify([
            {
                "name": p.name, "position": p.position, "team": p.team, "adp": p.adp,
                "upside_probability": upside.get(normalize_name(p.name)),
                "upside_rank": ranked.get(normalize_name(p.name)),
            }
            for p in matches[:15]
        ])

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
        session.reset_draft()  # back to just the saved keepers, not to nothing
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

        # ESPN's real per-player projections -- the one source of genuine
        # player-specific signal in this codebase (the historical bucket
        # model can't differentiate two players at the same rank). Always
        # fetched (cached to disk after the first call) since the
        # within-position value-pick comparison below needs it regardless
        # of which points_source the main recommendation uses; only
        # actually steers the simulation's own player *selection* when
        # points_source=espn was explicitly requested (opt-in -- see
        # simulate.resolve_pick's docstring for why historical stays
        # untouched by this).
        espn_points = get_points_model("espn").espn_points
        player_points = espn_points if points_source == "espn" else None

        points_model = get_points_model(points_source)
        rows = recommend_positions(
            live_pool, state, settings, points_model, sims, rng,
            opponent_weight_fn=pick_weight_with_tail_floor, player_points=player_points,
        )
        # How lopsided the top position's edge is over the runner-up -- the
        # same logistic blend backtest.py has used since the season-total
        # calibration check, surfaced live for the first time here instead
        # of only ever existing as an offline diagnostic. None when there's
        # no second position to compare against.
        confidence = position_confidence(rows)

        # Resolve each recommended position down to the specific player the
        # tool would actually draft there -- resolve_pick with the SAME
        # player_points the simulation itself used above, so the displayed
        # player always matches what was actually simulated (best-ADP
        # unless points_source=espn asked for value-aware selection).
        undrafted = [p for p in live_pool if normalize_name(p.name) not in state.drafted_names]
        results = []
        for pos, mean, p25, p75 in rows:
            candidates = [p for p in undrafted if p.position == pos]
            if not candidates:
                continue
            player = resolve_pick(candidates, player_points)
            # Rank among players STILL AVAILABLE at the position, not the
            # preseason rank. Mid-draft those diverge sharply and the live one
            # is what the decision turns on: "the 2nd-best remaining RB" tells
            # you what you are actually choosing between, while "preseason RB14"
            # does not. `remaining` alongside it gives the scarcity that makes
            # the rank mean something -- 2nd of 4 is a different situation from
            # 2nd of 30.
            by_adp = sorted(candidates, key=lambda c: c.adp)
            rank_among_remaining = next(
                (i for i, c in enumerate(by_adp, start=1) if c.name == player.name), None
            )
            # Where this player's market-implied ceiling sits among everyone
            # still available -- rank, not the raw probability, because a 3.9%
            # OPOY chance means nothing to a drafter in isolation but "2nd
            # highest ceiling left on the board" is immediately actionable.
            upside = get_upside()
            player_key = normalize_name(player.name)
            upside_score = upside.get(player_key)
            upside_rank = _rank_by_upside(undrafted, upside).get(player_key)

            results.append({
                "position": pos, "expected": mean, "p25": p25, "p75": p75,
                "player": player.name, "team": player.team, "adp": player.adp,
                "rank_among_remaining": rank_among_remaining,
                "remaining_at_position": len(candidates),
                "upside_probability": upside_score,
                "upside_rank": upside_rank,
            })

        # Within-position comparison for the #1 recommended position: is the
        # best-ADP player actually the best real value -- shown regardless
        # of points_source (even under historical scoring, it's worth
        # knowing a later-ADP player projects higher in reality).
        value_pick = None
        if rows:
            value_pick = best_value_within_position(rows[0][0], undrafted, espn_points)

        return jsonify({"rows": results, "confidence": confidence, "value_pick": value_pick})

    @app.get("/api/now-vs-wait")
    def now_vs_wait():
        """Draft-Now-vs-Wait for a specific position pair -- the caller
        (frontend) passes the top-2 positions from a /api/recommend response
        it already has, rather than this endpoint recomputing
        recommend_positions from scratch (that's the expensive part, no
        reason to pay for it twice for the same decision point)."""
        if session.my_draft_slot is None:
            return jsonify({"error": "Call /api/setup first"}), 400
        target = request.args.get("target", "")
        alternative = request.args.get("alternative", "")
        if not target or not alternative:
            return jsonify({"error": "target and alternative query params are required"}), 400

        state = session.state()
        seed = request.args.get("seed", type=int)
        sims = request.args.get("num_sims", default=num_sims, type=int)
        points_source = request.args.get("points_source", default="historical")
        if points_source not in ("historical", "espn"):
            return jsonify({"error": "points_source must be 'historical' or 'espn'"}), 400

        points_model = get_points_model(points_source)
        result = compare_now_vs_wait(
            target, alternative, live_pool, state, settings, points_model, sims, random.Random(seed),
            opponent_weight_fn=pick_weight_with_tail_floor,
        )
        if result is None:
            return jsonify(None)
        return jsonify({
            "position": result.position,
            "now_mean": result.now_mean, "now_p25": result.now_p25, "now_p75": result.now_p75,
            "wait_alternative_position": result.wait_alternative_position,
            "wait_mean": result.wait_mean, "wait_p25": result.wait_p25, "wait_p75": result.wait_p75,
            "survival_probability": result.survival_probability,
            "cost_of_waiting": result.cost_of_waiting,
        })

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
            chosen = sample_pick(undrafted, state.current_pick, random.Random(seed), weight_fn=pick_weight_with_tail_floor)
            session.set_pick(state.current_pick, chosen.name)
        return jsonify(session.to_dict())

    return app


DEFAULT_RANKINGS_PATH = Path("data/raw/fantasypros_2026_rankings.csv")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--draft-state", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--num-sims", type=int, default=300)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--rankings-source", choices=["fantasypros", "ffc"], default="fantasypros",
                         help="'fantasypros' (default) uses the FantasyPros overall consensus rankings CSV "
                         "as the live draft pool -- real expert rank order, but stdev/high/low are "
                         "approximated from tier width, not real draft-position variance. 'ffc' uses "
                         "FantasyFootballCalculator's live ADP instead (real draft-position variance, but "
                         "shallower player coverage -- ~260 players vs FantasyPros' 500+).")
    parser.add_argument("--rankings-path", type=Path, default=DEFAULT_RANKINGS_PATH,
                         help="CSV path when --rankings-source=fantasypros.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    live_pool = None
    if args.rankings_source == "fantasypros":
        print(f"Loading FantasyPros rankings from {args.rankings_path}...")
        live_pool = load_fantasypros_rankings(args.rankings_path)
        print(f"  {len(live_pool)} players loaded")
    app = create_app(args.year, args.draft_state, args.data_dir, args.num_sims, live_pool=live_pool)
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
