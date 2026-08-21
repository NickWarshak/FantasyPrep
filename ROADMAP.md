# FantasyPrep — Draft Decision Engine Roadmap

## North star

Not another rankings site. The system should answer, live, on the clock:

> Given my roster, my draft position, the players remaining, the behavior of
> this specific draft room, and what the market knows, what decision gives me
> the best range of possible completed rosters?

Everything below is scaffolding toward that single answer.

## Architecture: 5 engines

Every idea from brainstorming is a *view* on top of one of these. Build the
engines; the "flashy tools" (Can He Make It Back?, Draft Alpha, Run
Probability, etc.) are thin UI/query layers once the engines exist.

| Engine | Owns | Feeds |
|---|---|---|
| **1. Projection Engine** | Statistical projections, Vegas-derived projections, projection uncertainty (P10–P90 distributions, not point estimates), injury-adjusted value, correlated outcomes (QB↔WR stacking, RB1↔RB2) | Everything downstream |
| **2. Market Engine** | ESPN / Sleeper / Underdog / NFFC ADP + history, ADP momentum, sharp-vs-casual gap, ranking "gravity" (platform-driven overdrafting), custom-scoring re-ranker | Draft Simulator, Decision Engine |
| **3. Draft Simulator** | Opponent pick models (ADP distribution + positional need + observed room tendencies), rest-of-draft Monte Carlo, availability %, run probability, positional supply/demand | Decision Engine |
| **4. Roster Optimizer** | Starter-vs-bench value, replacement cliff, positional scarcity, roster construction sims (Hero RB vs Zero RB, etc.), bye/schedule fit, best-ball vs redraft valuation | Draft Simulator, Decision Engine |
| **5. Decision Engine** | Combines 1–4 into Draft/Wait/Avoid + "why," Draft Alpha composite score, backtesting, post-draft report card, counterfactual analyzer | You, on the clock |

Dependency order is basically 1 & 2 in parallel → 4 → 3 → 5. The simulator
needs projections+market data to model opponents, and needs the roster
optimizer to *score* each simulated outcome — you can't rank "draft RB now"
vs "draft WR now" without a way to value a completed roster.

## Principles

Two rules adopted 2026-08-15, after a second GPT-assisted critique of the
now-working backtest (agreed with, not just logged) — apply going forward:

1. **Model the signal, don't score it.** Don't hand-weight a composite
   score ("30% Vegas + 25% projections + 20% ADP..."). If a new data
   source matters, it should change an actual distribution the simulator
   already reasons over (Vegas → outcome distribution, ADP momentum →
   availability distribution, sharp/casual gap → acquisition price) —
   not bolt on another arbitrary weighted number next to Draft Alpha.
2. **Every feature earns its place by changing one of three things**: the
   estimated distribution of a player's outcomes, the estimated
   distribution of who's still available later, or how a completed roster
   gets valued. If a proposed feature doesn't move one of those three, it
   doesn't belong in the core model — it's a UI/reporting concern at most.

## Phased roadmap

**Phase 0 — Foundations (data layer, no modeling yet)**
- Player ID mapping across sources (ESPN/Sleeper/Underdog/odds feeds don't
  share IDs — this is the unglamorous but load-bearing piece)
- Storage for time-series ADP and lines (need history for momentum/velocity
  metrics later, so start logging from day one even before you use it)
- Custom scoring engine: ingest your actual league's scoring rules and
  rerank any projection source under them. Prerequisite for everything else
  being *your* numbers instead of generic PPR.
- **Market snapshot logger** — daily timestamped snapshot of ESPN
  rankings/ADP, FFC ADP, and FantasyPros ECR, independent of whether
  anything downstream uses it yet. History not captured today can't be
  reconstructed later; this is what Phase 8's ADP-momentum work will need.

**Phase 1 — Three flagship tools, standalone**
1. **Sharp vs Casual ADP Gap** ✅ **built** (`fantasyprep.adp_gap.report`)
   — original idea #3. Sharp source pivoted from Underdog/NFFC/Sleeper to
   **FantasyPros ECR** after each ADP source hit a real problem (Underdog:
   ToS-prohibited scraping + wrong format/best-ball; NFFC: correct format
   but thin-sampled enough to produce outliers; Sleeper `search_rank`:
   free/live but wrong for QB). See README.md for the full data-source
   saga and `--max-adp` caveat.
2. **Vegas Projection System** — not started. Capped to team-level implied
   totals (no paid odds API, per the decision below).
3. **Monte Carlo Draft Simulator v1** ✅ **built**
   (`fantasyprep.draft_sim.simulate`) — position-level recommendation
   ("RB or WR right now"), matching the original ask exactly. Opponent
   *and* "your future picks" modeling both use live ADP + real per-player
   stdev from FantasyFootballCalculator's free API (not a heuristic
   tolerance). Outcome model uses **real historical data** instead of a
   projection curve: 2018-2024 actual fantasy points (nfl_data_py, scored
   under the league's own settings) bucketed by historical draft-time
   position rank, bootstrap-resampled per simulation — captures real
   boom/bust shape for free. League settings confirmed: 10-team, full
   PPR, 1 QB / 2 RB / 2 WR / 1 TE / 2 FLEX / 1 DST / 6 bench, no kicker
   (`league/settings.py`). Deferred to later phases: player-level (not
   just position) recommendations, positional-need-aware opponent
   modeling, room-tendency inference (#9), correlated outcomes.
4. **Local web UI** ✅ **built** (`fantasyprep.webapp.app`, Flask) — a
   Sleeper-style draft grid (teams as columns, rounds as rows) on top of
   the same simulator engine (shared `recommend_positions`/
   `state_from_picks`, not a reimplementation). Clicking any cell — the
   current pick or a future one — opens the same search-and-assign flow;
   a future cell is how **keepers** work, pre-assigned before the live
   draft reaches that slot. "Mine" is derived from snake-draft math (team
   column), not a stored flag. State autosaves to the same JSON the CLI
   reads/writes so either tool can pick up mid-draft. See README.md for
   usage.
   - **Bug found and fixed while building this**: `simulate_position_choice`
     computed "players already on my team" by searching the pool *after*
     it had already been filtered to exclude drafted players — so
     already-drafted players (including keepers) silently contributed
     zero points to every recommendation. Fixed by passing the full live
     pool through instead of a pre-filtered one; covered by a regression
     test (`test_already_mine_player_contributes_to_roster_value`).
   - **Keyboard-driven entry** ✅ **built** — `Enter` opens the current
     pick's picker from anywhere on the page (no click needed), `↑`/`↓`
     to move between search suggestions (top match pre-highlighted so a
     bare `Enter` after typing usually just works), `Escape` to cancel.
     Assigning the actual current pick auto-reopens the picker for the
     next one, so a run of picks can be logged as type → Enter → type →
     Enter without touching the mouse. Keeper entry (a future cell,
     clicked directly) doesn't auto-chain, since it's a deliberate
     one-off action.
   - **Points source is now pluggable** ✅ **built**
     (`draft_sim/points_model.py`) — `HistoricalBootstrapModel` (the
     original v1 approach, unchanged) and a new `EspnProjectionModel`
     using ESPN's own named-player season projection (`sources/espn.py`'s
     `fetch_espn_projected_points`, reusing the same `kona_player_info`
     payload the ADP gap tool already pulls, scored under the league's
     real settings rather than ESPN's own display), falling back to the
     historical model for anyone ESPN doesn't project. `--points-source
     {historical,espn}` on the CLI, `?points_source=` on
     `/api/recommend`. Named-player projections mean tighter P25/P75
     spreads than the historical source, since simulated variance then
     only comes from which players land on the roster, not from each
     player's own outcome uncertainty — a real interpretation difference,
     not a bug. One real data quirk hit and worked around: ESPN's
     season-total stat block leaves receptions (stat id `41`) empty for
     some players and only populates the documented-duplicate id `53`
     instead; the code tries both.
   - **Auto-simulate opponent picks** ✅ **built**
     (`draft_sim/auto_pick.py`, `POST /api/simulate/step`) — a "Simulate
     opponent picks" button that auto-fills every non-mine pick one at a
     time (ESPN ADP + a tunable synthetic variance, since ESPN doesn't
     publish real per-player stdev like FFC does — the ADP gap tool's
     `tolerance_for_adp` heuristic scaled by a user-set "randomness"
     multiplier), stopping the instant it's my turn and auto-opening the
     picker right then. "Speed" is pure client-side pacing between picks;
     "Stop" genuinely halts (one small request per pick, not a batch), and
     it correctly skips over any keepers already sitting in the way.
   - **Data Integrity Review** ✅ **built**
     (`historical/validate.py`, `historical/report.py`,
     `python -m fantasyprep.historical.report`) — a standalone, on-demand
     report (not a live tool) cross-checking computed points against
     independent sources and visualizing the real outcome distributions
     the simulator draws from. Found and fixed a real bug (missing
     special-teams-TD/2pt-conversion credit, max delta 6.00 → 0.00 across
     4,056 player-seasons) and a real ESPN-side data anomaly (implausible
     `appliedAverage` for a handful of players, mostly QBs — not our bug).
     See README.md for details. This was step 1 of the priority order
     agreed on after a GPT-assisted audit of the simulator (**validation
     → backtest against baselines → model experiments (bucket design,
     recency weighting, historical-year range) → simulation-count
     convergence testing → speed optimization** — in that order,
     deliberately not starting with speed or more years).
   - **Backtest harness** ✅ **built** (`draft_sim/backtest.py`, `python -m
     fantasyprep.draft_sim.backtest`) — step 2. Replays full historical
     10-team drafts three times per (season, draft slot) — pure ADP-chalk,
     ADP+need, and the model (added below) — sharing an opponent
     RNG seed between the two runs (common-random-numbers, isolates the
     comparison to "my" strategy): a **baseline** condition (best-ADP
     player among positions of need — `positions_of_need`, accounting for
     FLEX overflow and bench-open-any) versus the **model** condition
     (the actual unmodified `recommend_positions`, not an idealized
     need-aware version of it). Both score on *real* historical points for
     that season, not sampled outcomes. Leakage-safe: a test year's
     outcome buckets are built only from strictly-prior years (separate
     cache per test year, `.outcomes_{teams}_pre{year}.json`), so the
     model isn't partly graded on data it was tuned from. ESPN has no
     usable historical ADP for past seasons (verified live this session —
     queried 2019/2022/2024 directly; ownership/ADP is a rolling
     current-season snapshot, not preserved history), so the baseline uses
     FFC ADP as the public-consensus-ADP proxy, same source the live
     opponent model already uses.
     - **Real bug found and fixed while building this**:
       `HistoricalBootstrapModel.sample()` (`draft_sim/points_model.py`)
       raised `KeyError` whenever a DST got sampled into a hypothetical
       future pick during any Monte Carlo simulation — DST has zero
       historical distributions since `nfl_stats.py`'s `POSITION_MAP`
       never computes DST scoring. Live-reachable today with the default
       `points_source=historical` webapp path, just probabilistic (didn't
       happen to trigger during this session's UI test). Now scores 0
       instead of crashing; regression test added
       (`test_historical_bootstrap_model_scores_zero_for_position_with_no_data`).
     - **DST symmetry fix**: `recommend_positions` never proposes DST
       (only QB/RB/WR/TE), so left alone the model condition would never
       voluntarily draft one — an unearned extra skill-position pick versus
       the baseline, which does fill its required DST slot. Fixed by
       force-filling any position that's the *sole* remaining need and
       isn't one `recommend_positions` ever proposes, identically for both
       conditions. DST also has no real-points source anywhere in this
       codebase, so it scores 0 for both conditions either way —
       symmetric, doesn't bias the comparison, but does mean total roster
       values slightly underestimate true value.
     - **First real result** (original scope: 2022–2024 × 10 slots = 30
       replays, `num_sims=50`): model beat baseline in 17/30 (57%), mean
       +57.4 pts, median +14.5 pts, full range -173.4 to +319.5 — a
       genuine, moderate edge, not a blowout. Edge wasn't uniform by year
       (2022 mean +131.3, 2023 +18.8, 2024 +22.1). Full results:
       `data/backtest_results.json`. Superseded by everything below —
       kept here as the original milestone, not the current number.
     - **Default scope since widened** (2026-08-16, alongside the year
       extension below): `DEFAULT_BACKTEST_YEARS` is now 2015-2024 (10
       seasons, up from 3) × all 10 slots = 100 (year, slot) cells, each
       with `--num-seeds` opponent-room draws. A 30-cell (1 seed each)
       check at the new `num_sims=100` + the pure-ADP baseline (below):
       model beat ADP+need in 21/30 (70%, 95% CI 52%-83%), mean +55.0
       (CI crosses zero — a handful of outliers, including one -460.8
       loss, pull the mean around more than the median +46.6 does, CI
       +11.1 to +130.3, doesn't cross zero); beat pure ADP-chalk 22/30
       (73%), mean +133.4 (CI +63.6 to +200.8, solidly positive) — beating
       a need-aware drafter is a harder bar than beating pure chalk, as
       expected. The full hardened run (many more seeds per cell) is
       queued, not yet launched as of this writing — deliberately held
       until the rest of this batch (below) is built and verified first,
       rather than running it prematurely on a partial feature set.
     - **The -460.8 loss, investigated concretely**: 2023 slot 4. Baseline
       drafted Josh Allen (394.6 real pts that season). The model instead
       ended up with two mediocre QBs — Deshaun Watson (86.8) and Kirk
       Cousins (149.7, before tearing his Achilles that season) — and
       since only 1 QB slot starts, Watson's bench and Cousins' 149.7 is
       ~245 pts short of Allen alone. Root cause: the model decided "take
       a QB" twice without ever comparing which *specific* QB was the
       better bet — concrete, empirical evidence (not just theory) that
       player-level recommendations (Phase 3) is the right next
       investment, found by tracing one real replay's actual rosters
       rather than guessing from the summary stats.
     - **Historical years extended 2018-2024 → 2010-2024** (verified live
       against both FFC and nfl_data_py, not assumed — 2012 is a notably
       thinner year in FFC's data, 93 players vs ~200 typical, included
       anyway as real signal). `DEFAULT_HISTORICAL_YEARS` in
       `historical/outcomes.py`. This is what unlocked widening
       `DEFAULT_BACKTEST_YEARS` back to 2015 (every test year now has
       ≥5 strictly-prior years instead of the original ≥4-year floor).
     - **Pure-ADP-chalk baseline added** as a third condition alongside
       ADP+need and the model (`pure_adp_pick`) — best-ADP-available, need
       ignored entirely. Nearly free to add: the two baseline conditions
       are deterministic single-pass drafts, all the real compute cost is
       in the model condition's simulation, unchanged either way. Every
       `ReplayResult` now carries `pure_adp_points` /
       `delta_vs_pure_adp` alongside the original baseline fields.
     - **Confidence-weighted calibration check added**
       (`confidence_weighted_pick_value`) — at every genuine model
       decision, blends the *real* point value of the top-2 candidate
       positions' best-available player, weighted by a logistic function
       of the margin between the model's own expected values for them,
       scaled by their pooled P25-P75 spread (self-calibrating: the same
       raw point margin counts as more decisive when the underlying
       spread is tighter). A near-tied decision blends close to 50/50; a
       lopsided one close to 100/0 — the "showcase legitimate uncertainty"
       idea, applied at position-granularity since player-level isn't
       built yet. Reported as a separate calibration diagnostic (blended
       estimate vs. what actually happened), not part of the primary
       win/loss comparison — `ReplayResult.confidence_weighted_points` /
       `confidence_weighted_actual_points` / `confidence_weighted_gap`.
     - **A second real bug found and fixed**, this one much bigger in
       blast radius, discovered while manually tracing a replay to explain
       a specific draft decision: `historical/sources/ffc.py` filtered
       players by a hardcoded `{"QB","RB","WR","TE","K","DST"}` set, but
       FFC's own API returns defense as `"DEF"` and kicker as `"PK"` — so
       every DST/K was silently dropped from *every* FFC fetch this whole
       project, not just in the backtest. Opponents never drafted a DST/K
       in any simulation, live or backtest, and the webapp's search picker
       couldn't find one either. Fixed with an explicit `FFC_POSITION_MAP`;
       regression tests added. QB/RB/WR/TE outcome-bucket ranks are
       unaffected (ranks are computed per-position-group, so an absent
       DST/K group doesn't touch them) — confirmed by rebuilding the
       production cache and diffing, not assumed. Also surfaced, while
       diffing that rebuild: FFC's "historical" ADP for a past season
       **isn't a frozen archive** — it drifts slightly over time as their
       own data keeps accumulating. Benign (two fresh builds from the same
       cache are deterministic — confirmed), but worth knowing: exact
       backtest numbers aren't bit-for-bit reproducible across different
       calendar days, only internally consistent within one run's own
       cache files.
   - **Backtest report** ✅ **built** (`draft_sim/backtest_report.py`,
     `python -m fantasyprep.draft_sim.backtest_report`) — renders
     `backtest.py`'s JSON results into a self-contained HTML report:
     headline stat tiles (now with 95% CIs — see Phase 2), per-year
     breakdown, every (season, slot) cell shown individually as a
     mean-with-range chart rather than 300 individual bars once
     `--num-seeds` > 1 (not just the overall mean — same
     don't-wash-out-outliers principle as the outcome-distribution charts
     in the Data Integrity Review), and case-study cards naming the actual
     players each strategy drafted differently in the biggest swings.
     Same visual system as `historical/report_render.py` (tokens, stat
     tiles, position colors) for a consistent look across the project's
     reports — published as an Artifact per user preference. Regenerate
     after any backtest rerun.

**Phase 2 — Evaluation Hardening** (re-sequenced 2026-08-15 after a second
GPT-assisted critique of the working backtest — "make the edge trustworthy
before adding features," agreed with)
- ✅ Multiple opponent-room seeds per (season, draft slot) — same
  common-random-numbers pairing, more paired draws per cell instead of
  more seasons (which we don't have more of). See Phase 1 backtest entry
  for the real numbers.
- ✅ Confidence intervals on every headline metric (Wilson for win rate,
  cluster bootstrap grouped by season×slot for mean/median — not a naive
  per-replay bootstrap, since seeds within a slot aren't independent of
  each other the way different slots are).
- ✅ Baseline ladder, rung 1: pure ADP-chalk added alongside ADP+need
  (`pure_adp_pick`) — every replay now runs all three conditions
  (pure-ADP, ADP+need, model) sharing one opponent seed, so the report
  shows "beats pure chalk by X" and "beats a need-aware drafter by Y"
  side by side. Nearly free to add — the two baseline conditions are
  deterministic single-pass drafts, all the actual compute cost is in
  the model condition's simulation, unchanged either way. ECR-and-need
  and projected-points-and-need still wait on Phase 0-style data
  collection — no historical FantasyPros ECR or historical pre-season
  projections are archived anywhere yet.
- ✅ Baseline ladder, rung 2: **VOR (value-over-replacement)**
  (2026-08-16, `draft_sim/vor_baseline.py`, `python -m
  fantasyprep.draft_sim.vor_baseline`) — initially assumed blocked on the
  same missing-projections problem as the other unbuilt rungs; turned out
  not to be, since VOR needs an *expected value*, not a *projection*, and
  the outcome-bucket machinery the model itself already draws from
  (`historical/outcomes.py`) already provides real, leakage-safe expected
  value per (position, draft-rank) bucket. A player's VOR = their
  bucket's real historical mean minus a real historical replacement-level
  baseline for that position (mean outcome at a "replacement rank"
  bucket, same `DEFAULT_RANK_CUTOFF` concept as the weekly-adjustment
  work, applied to season-total buckets here). A materially stronger
  baseline than ADP+need: it reasons explicitly about positional
  scarcity from real data rather than trusting the market's implicit
  pricing of it. **Real result, 10-replay spot check (2023-2024,
  slots 1-5)**: VOR beat the ADP+need baseline in 6/10 (60%), mean +49.0,
  median +96.3 — a promising early signal that reasoning explicitly about
  replacement value may itself be a materially stronger drafting
  heuristic than ADP+need. **Promoted to an official 4th backtest
  condition** (2026-08-16) — `vor_pick`/`replacement_level_points`/
  `DEFAULT_RANK_CUTOFF` moved into `backtest.py` itself (alongside
  `baseline_pick`/`pure_adp_pick`), `ReplayResult` gained `vor_points`/
  `vor_roster`/`delta_vs_vor`, and every replay in `run_backtest` now
  drafts all 4 conditions (ADP+need, pure-ADP-chalk, VOR, model) sharing
  one opponent seed via common random numbers. `vor_baseline.py` re-exports
  the moved names (no circular import — it still imports `run_full_draft`/
  `score_roster`/`baseline_pick` from `backtest.py`, `backtest.py` never
  imports from it) and keeps its standalone 2-way (`run_vor_comparison`,
  `python -m fantasyprep.draft_sim.vor_baseline`) runner for quick
  spot-checks that don't need a full 4-way backtest. `backtest_report.py`
  renders a "Vs. value-over-replacement" section (mirrors the pure-ADP-chalk
  section) whenever `vor_points` is present in the results JSON — verified
  end-to-end against real 2024 data (2 replays, slots 1/3) before wiring
  into the report. Older result JSON files without `vor_points` still
  render fine (section just doesn't appear).
- ✅ **Real, derived replacement-level rank cutoffs** (2026-08-16,
  `ffc.derive_rank_cutoff`) — `DEFAULT_RANK_CUTOFF = {QB:15, RB:30, WR:30,
  TE:15}` (used by both the VOR baseline and waiver-adjusted weekly
  scoring) was a hand-guessed split assuming a fixed FLEX/bench-stash
  allocation per position. Checked against real FFC ADP for a 10-team
  league (2020/2022/2023/2024): real draft depth for RB/WR runs 50-61,
  not 30 — the guess was off by roughly 2x, because it didn't account
  for real bench-stashing (RB handcuffs, high-upside WR stashes are
  heavily drafted in practice) on top of FLEX competition, and because
  the FLEX/bench split between RB and WR isn't fixed the way the formula
  assumed — it shifts with relative depth each year. TE's guess (15)
  turned out basically correct (real: 13-17) since TE rarely absorbs
  FLEX or gets bench-stashed the way RB/WR do — confirms real behavior
  varies materially by position in ways a single formula can't capture.
  Fix: `derive_rank_cutoff(players, settings)` counts real ADP-drafted
  players per position within the league's actual total pick count
  (`(sum(roster_slots) + bench) * teams`) and returns count+1 per
  position — "one past the last player who really gets drafted, i.e.
  the first true free agent" — using real human draft behavior instead
  of a guessed split, so it automatically reflects whatever FLEX
  competition and bench-stash rates actually happened that year, no
  formula to keep re-tuning. Wired into both `replay_one`'s VOR
  condition (recomputed per test-year from that year's real live ADP
  pool) and `run_backtest`'s waiver-adjusted scoring path. Old
  `DEFAULT_RANK_CUTOFF` kept only as a fallback for callers with no ADP
  data on hand. Verified: real 2024 derived cutoff is `{RB:51, WR:60,
  TE:17, QB:22, DST:5}` vs. the old flat `{QB:15, RB:30, WR:30, TE:15}`;
  full test suite green (204 tests) and both scoring modes smoke-tested
  end-to-end against real data with the new cutoffs flowing through.
- ✅ **Reproducibility bug found and fixed** (2026-08-16) — three
  back-to-back backtest runs with identical CLI args including `--seed`
  produced three different vs-VOR win rates (8/15, 10/15, 10/15). Root
  cause: Python randomizes string hashing per-process by default
  (`PYTHONHASHSEED` unset), silently reordering some `set`/`dict`
  iteration between runs even though every explicit `random.Random` in
  this codebase is correctly seeded — a language-level effect layered
  underneath, not a seeding bug in the code itself. Confirmed via
  `PYTHONHASHSEED=0` twice → bit-identical results both times. Fixed:
  `backtest.py`'s `main()` now re-execs itself via `subprocess.run` with
  `PYTHONHASHSEED=0` if not already set, before anything else runs (used
  `subprocess.run` over `os.execv` after the latter mangled a multi-word
  `--experiment-notes` argument's quoting on Windows). **Every result
  logged before this fix should be treated as non-reproducible** —
  `experiment_registry.log_experiment` now records a `reproducible: bool`
  field (existing log entries migrated retroactively), and the CLI reader
  prints a visible `[NOT REPRODUCIBLE]` tag on any pre-fix entry so one
  can't get silently compared against a post-fix number later.
- ✅ **CI clustering fixed to the season level, not (season, slot)**
  (2026-08-16, caught via external/GPT review) — confidence intervals
  across this project were cluster-bootstrapping at `(year, my_slot)`,
  treating different slots within the same season as independent draws.
  They aren't: one real player's monster season (e.g. a 2023 CMC) can
  move the result at several different slots simultaneously if different
  strategies end up drafting him depending on when he falls. The true
  independent unit of real-world evidence is the *season* (10 of them),
  not the (season, slot) cell (100) or replay count (hundreds). Fix:
  `ReplayResult.cluster_key` now returns the season alone. Also caught
  the same gap in the win-rate CI specifically — it used a plain Wilson
  interval (assumes iid Bernoulli trials, wrong here for the same
  reason), now uses the same season-level cluster bootstrap as
  mean/median (`_win_rate_stat`). Pure post-hoc statistics change, no
  resimulation needed — verified it visibly widens CIs on existing data
  (a 15-replay/3-season subset: win-rate CI (60%-100%), mean CI (+19.5 to
  +117.3) — correctly coarser, reflecting only 3 independent seasons
  backing that subset).
- ✅ **Opponent "stuck player" tail-floor fix, built but not yet the
  default** (2026-08-16, `opponent.pick_weight_with_tail_floor`, GPT
  review) — the pure-Gaussian opponent model's pick-weight numerically
  collapses toward 0 once a player falls several stdevs past their real
  ADP (verified: real 2023 Christian McCaffrey, ADP 2.4/stdev 1.0, weight
  0.84 at pick 3, exactly 0.0 by pick 8+), making them "stuck" undrafted
  for the rest of a simulated continuation — the opposite of real
  drafter behavior (a top player anomalously still on the board should
  become *more* likely to go, not less). Not just a symmetric quirk: it
  can interact with strategy, since who gets "stranded" by this bug
  differs depending on what each condition already drafted, changing
  which players remain available differently per condition. Fix: past a
  3-stdev threshold, replaces the Gaussian's continued decay with a
  rising hazard that climbs toward near-certainty instead (continuous at
  the boundary, unchanged below it) — verified against the same CMC case:
  weight rises from 0.73 (pick 8) to 0.90 (pick 10) to 0.999 (pick 20)
  instead of staying at 0.0. Opt-in via `sample_pick`'s `weight_fn` /
  `run_full_draft`'s `opponent_weight_fn` / `backtest.py`'s
  `--opponent-model {gaussian, gaussian-tail-floor}` — kept opt-in rather
  than swapped as the new default so it doesn't silently change what an
  in-flight comparison is measuring, matching the `vor_rank_cutoff_mode`
  pattern. **Known gap, documented not fixed**: only changes the *outer*
  real-draft opponents in `backtest.py` — the model's own internal
  Monte Carlo lookahead (`recommend_positions` → `simulate.py`'s
  `simulate_position_choice`) still hardcodes the plain Gaussian
  internally, so running `gaussian-tail-floor` makes outer opponents more
  realistic while the model's own assumption about the rest of the draft
  stays stale. Fully fixing this means threading a weight_fn through
  `simulate.py` too, which also touches the live webapp's recommendation
  engine — bigger, separate change, not done here. Next: a controlled A/B
  (same pattern as the VOR-cutoff one) once there's a moment to spend
  the compute, ideally after also closing the simulate.py gap.
- ✅ **Closed the tail-floor "known gap" above** (2026-08-16, Mac mini) —
  threaded `opponent_weight_fn` through `simulate.py`'s
  `simulate_position_choice`/`recommend_positions` (both default to the
  original `pick_weight`, matching `opponent.sample_pick`'s own default,
  so this is additive, not a behavior change for existing callers) and
  wired `backtest.py`'s `replay_one`/`run_backtest` to pass its
  `opponent_weight_fn` into the model condition's internal
  `recommend_positions` call, not just the four `run_full_draft` calls.
  A `gaussian-tail-floor` backtest run now has the model's own lookahead
  agree with the outer opponents about how the rest of the draft
  behaves, instead of the lookahead silently staying on the plain
  Gaussian. Two spy-based regression tests added (`test_simulate.py`,
  `test_backtest.py`) asserting the actual `weight_fn` object reaches
  `sample_pick`, not just that the call doesn't crash. The live webapp's
  `recommend_positions` call (`webapp/app.py`) still doesn't expose an
  opponent-model choice and keeps the plain-Gaussian default — untouched,
  since it's a separate call site with no `--opponent-model` flag to
  thread from. The controlled A/B this unblocks (tail-floor vs. plain
  Gaussian, now apples-to-apples end to end) hasn't been run yet.
- Untouched final holdout season — 2025 doesn't work yet (`nfl_data_py`
  still 404s on it as of 2026-08-16, not published upstream; recheck
  periodically). Once available: freeze model design before ever looking
  at it, use 2015-2024 as dev seasons only.
- Decision-level backtesting: record every point where the model and a
  baseline would have disagreed, and grade that single decision's actual
  outcome, not just the whole draft. Bigger lift than the above — do
  after the cheap wins land, since it wants a wider replay set to have a
  meaningful sample of disagreements to grade.
- ✅ **Position-level breakdown** (`draft_sim/backtest_analysis.py`,
  `python -m fantasyprep.draft_sim.backtest_analysis --in <results.json>`)
  — built ahead of the big run finishing, so "where does the edge
  actually concentrate" (GPT's suggested next step) can run the instant
  it completes instead of being designed fresh at that point. Round-level
  isn't possible from the current schema (roster tuples don't record
  which pick number a player came from), position-level is. New
  `roster.starting_lineup_value_by_position` (a breakdown of the exact
  same greedy assignment `starting_lineup_value` already uses — sums to
  the same total, a breakdown not a different computation; a FLEX
  starter is attributed to their real position, not a separate FLEX
  bucket) makes this possible without touching the validated original
  function at all. **Real result on the existing 30-replay dataset**: the
  edge concentrates in TE (+47.4, 67% win rate) and QB (+29.5, 53%) — but
  the model actually *loses* value at WR (-57.5, only 30% win rate) and
  slightly at RB (-4.7, 30%). A real, concrete, actionable finding, not
  yet re-run on the big backtest (queued to run there once it finishes).
- Model ablation testing (drop one feature at a time — positional need,
  ADP stdev, bootstrap vs. deterministic mean, future-draft lookahead —
  and see what the edge actually comes from) — not started; deliberately
  deferred rather than rushed, since it means editing the core simulation
  logic and that's not something to do carelessly right before a big
  overnight validation run.
- ✅ Experiment registry (`draft_sim/experiment_registry.py`,
  `--experiment-name`/`--experiment-notes` on the backtest CLI) — every
  named run appends a JSONL entry (params + headline win-rate/delta for
  both baselines) to `data/experiments.jsonl`, so "I think version 17 was
  better?" has an actual answer. Ad-hoc smoke tests stay unlogged (name
  is opt-in).
- ✅ Convergence check (`draft_sim/convergence.py`,
  `python -m fantasyprep.draft_sim.convergence --year Y --pick N`) — does
  `recommend_positions`' top pick actually stabilize as `num_sims`
  increases? Repeats the recommendation at a fixed, realistic mid-draft
  decision across several `num_sims` levels and measures how often the
  top-ranked position agrees across repeats (the number that actually
  matters — the estimates can look "close" while the winner still flips).
  **Real finding, checked live at two different picks**: at pick 15,
  agreement rose from 47% (`num_sims=50`, the previous default) to 87%
  at `num_sims=200` — a real convergence issue, fixable with more
  simulation. At pick 45, agreement stayed 40-60% even at `num_sims=400`
  — not a convergence failure, more likely a genuinely close decision
  where two positions' true values are near-tied, so no amount of extra
  simulation produces a stable "winner." Conclusion: `num_sims=50` is
  measurably under-converged in cases with real signal to find. Bumped
  to `num_sims=100` as the new default for backtest runs going forward
  (a deliberate, measured choice, not a guess) — full convergence
  behavior and whether decision-level calibration (see above) should
  surface "how close was this call" to the user is follow-up work.
- ✅ **The TE-hindsight question, resolved** (2026-08-17, Mac mini): the
  #1 open question as of the previous session (does the model's TE-driven
  edge over VOR survive `--scoring-mode waiver-adjusted`, or is it
  substantially a same-position-hindsight artifact of season-total
  scoring?) — see the position-level breakdown entry above for the
  original finding (TE +50.8, RB -42.9, WR +12.2, QB +7.7 at n=100).
  Answer: **the edge is real, not primarily hindsight.** Ran season-total
  and waiver-adjusted back-to-back at identical, much larger scope (full
  10yr×10slot grid, 20 seeds/cell — 10x the original headline —
  `num_sims=300`, `n=2000` each) for a true apples-to-apples comparison at
  matched statistical power, not just against the old n=100/1-seed
  headline. vs-VOR win rate is **literally identical (58%)** across
  scoring modes; the TE-specific delta only shrinks ~8% (+52.8 →
  +48.6) under the scorer that removes the hindsight quirk, and the
  2nd-TE rostering rate (91% model vs. 4% VOR) is identical by
  construction (scoring mode doesn't change draft decisions, only how
  the resulting roster is scored). Mean/median deltas across all four
  baselines drop a more real ~25-30% under waiver-adjusted scoring
  (e.g. vs-VOR mean +32.7 → +23.6), consistent with *some* hindsight
  inflation, but nowhere near enough to explain the edge away. Results:
  `data/backtest_macmini_seasontotal_20seed_300sims.json` /
  `data/backtest_macmini_waiver_20seed_300sims.json` (both committed),
  logged experiments `mac-mini-seasontotal-20seed-300sims` /
  `mac-mini-waiver-adjusted-20seed-300sims`. Two Windows-side attempts at
  this same test were killed earlier due to real CPU contention on that
  machine's 4-core ULV chip before producing output — this Mac-side run
  (post-vectorization, `be8d0fa`/`cfaf38e`) is the first one to actually
  finish. Unblocks the report redesign (STATUS.md's former #2 priority,
  deferred pending exactly this verdict) — not done yet.
- 🔴 **Opponent-model choice (Gaussian vs. tail-floor) reverses 2 of the
  3 backtest baselines** (2026-08-17, Windows overnight) — the headline
  table and the TE-hindsight run above were both measured under the
  plain-Gaussian opponent model, which the live tool stopped using on
  2026-08-16 (see the tail-floor fix above). Re-ran the same 200-replay
  scope (10yr×10slot×2 seeds, same seed) with only `--opponent-model`
  differing: vs. ADP+need drops from 59% win/+30.2 (Gaussian) to **42%
  win/-34.5, a losing record** (tail-floor); vs. pure ADP-chalk drops
  from 73%/+155.0 to a coin-flip 51%/+52.9; vs. VOR moves the *other*
  direction, from a CI-includes-zero 60%/+32.6 to a solid 63%/+77.2.
  CIs for ADP+need don't overlap between the two conditions — a real,
  reproducible effect (this run's Gaussian arm reproduces the old
  published mean delta to one decimal, +30.2 both times, ruling out a
  cross-machine methodology drift). A supplementary position-level
  breakdown computed from the raw roster data (not the tool's built-in
  output, so a plausible hypothesis rather than a confirmed mechanism):
  QB's contribution to the model's edge shrinks roughly 40% under
  tail-floor, RB's negative contribution widens, TE and WR move less.
  Results: `data/overnight_backtest_gaussian.json` /
  `data/overnight_backtest_tailfloor.json` (both committed), logged
  experiments `overnight_ab_gaussian_2026-08-16` /
  `overnight_ab_tailfloor_2026-08-16`. Headline table itself not yet
  rerun under tail-floor at full scope — see the TE-hindsight tail-floor
  confirmation immediately below, which does independently cross-validate
  this finding at 10x this entry's n (n=2000 vs. n=200): ADP+need also
  flips to a losing record (47% win, mean -17.0 to -21.0, entirely
  negative CI) in that larger run.
- ✅ **TE-hindsight verdict confirmed under tail-floor too** (2026-08-17,
  Mac mini) — reran both scoring modes above a second time, swapping only
  `--opponent-model gaussian` → `gaussian-tail-floor`, identical
  20-seed/`num_sims=300`/n=2000 scope for direct comparability, completing
  the 2×2 (scoring-mode × opponent-model) grid. **The edge doesn't just
  survive — it's substantially stronger** under the realistic opponent
  model: vs-VOR TE-specific delta more than doubles (+52.8/+48.6 under
  Gaussian → **+127.2/+137.1** under tail-floor, season-total/
  waiver-adjusted respectively), RB's deficit widens similarly
  (-40.5/-44.1 → -92.8/-104.8), and the 2nd-TE rostering split widens
  further (91%/4% model/VOR under Gaussian → **96%/2%** under tail-floor).
  Critically for the hindsight question specifically: waiver-adjusted's TE
  delta (+137.1) is not smaller than season-total's (+127.2) here — if
  anything larger — the strongest evidence yet against the hindsight-
  artifact hypothesis, now confirmed under both opponent models. Overall
  vs-VOR win rate: 61% (season-total) / 60% (waiver-adjusted) tail-floor
  vs. 58%/58% Gaussian — also stronger, consistent with Windows's finding
  that VOR gets stronger under tail-floor generally. Results:
  `data/backtest_macmini_seasontotal_20seed_300sims_tailfloor.json` /
  `data/backtest_macmini_waiver_20seed_300sims_tailfloor.json` (both
  committed), logged experiments
  `mac-mini-seasontotal-20seed-300sims-tailfloor` /
  `mac-mini-waiver-adjusted-20seed-300sims-tailfloor`. **This closes out
  the opponent-model-reversal open item as far as the TE-hindsight
  question goes** — the report redesign (STATUS.md #2) can now proceed
  without an asterisk on the verdict, though it should present tail-floor
  numbers as primary (the live tool's actual model) rather than the
  Gaussian ones above.

**Phase 3 — Player-Level Decision Engine**
- ✅ **Built as a standalone diagnostic** (2026-08-16/17, Mac mini,
  `draft_sim/player_choice.py` + `draft_sim/player_choice_backtest.py`) —
  same posture as Phase 5's Draft Now vs. Wait: not wired into live
  recommendations yet, validate first. `simulate_player_choice`/
  `recommend_players` extend the existing Monte Carlo lookahead to
  simulate the top `top_n` (default 3, matching this phase's original
  "top 3-5" plan) undrafted players at a recommended position
  *individually* as "my pick now," ranking them by simulated
  completed-roster EV — instead of `simulate_position_choice`'s
  always-best-ADP default. A close variant, not a modification of the
  validated original (same pattern every other diagnostic in this
  project uses).

  One caveat baked into the points model, documented in both new
  modules' docstrings: `HistoricalBootstrapModel` pools outcomes into
  3-rank buckets (`outcomes.py`'s `BUCKET_WIDTH`) — two players in the
  same bucket are statistically identical to it, so this can only find a
  real difference between players in *different* buckets. This is a real
  ceiling on how often the feature could possibly matter under the
  default points source, not a bug.

  `player_choice_backtest.py` runs the actual test, same rigor as the
  headline backtest: two full real-draft replays per (year, slot, seed)
  under common random numbers — the current model's naive-best-ADP
  strategy vs. the new player-choice strategy — scored on real historical
  points, same season-clustered CIs
  (`cluster_bootstrap_ci`/`wilson_interval` reused directly from
  `backtest.py` via duck typing, not reimplemented).

  Discovered while building this: Windows was concurrently building a
  related-but-different answer to the same underlying gap (commits
  `670ce61`/`a39d573`, `resolve_pick`/`best_value_within_position` — a
  much cheaper direct ESPN-projection-window comparison, already wired
  into the live webapp). Complementary, not duplicate/conflicting code —
  no file overlap. Worth a future comparison: does the heavier
  full-roster-simulation approach here ever beat the cheaper live-wired
  one, or is the live one already sufficient?

  A 2-replay smoke test (`num_sims=30`) showed the player-choice branch
  losing badly (0/2, mean -200) — not concerning at n=2 given the
  bucket-pooling caveat above, but flagged rather than assumed away. The
  full overnight 20-seed/`num_sims=100` grid (`data/backtest_player_choice_20seed.json`,
  experiment `player-choice-vs-current-model-20seed`, n=2000) **confirmed
  the smoke test was not noise: this makes real performance measurably
  worse.** Player-choice beat the current model in only 39% of replays
  (95% CI 37%-43%, doesn't cross 50%), mean delta **-57.5** (95% CI -70.8
  to -42.4, entirely negative). And **0/2000 replays matched the naive
  best-ADP roster** — every single one diverged somewhere, far more often
  than the bucket-pooling caveat alone predicts.

  Root cause confirmed by hand, not just inferred (`player_choice.py
  --year 2022 --pick 10`): at a real decision, Derrick Henry (ADP 4.3)
  edged Jonathan Taylor (ADP 1.3, the real best-ADP pick) by simulated EV
  1765.5 vs. 1763.0 — a 2.5-point margin on a ~1765-point roster value,
  well inside Monte Carlo noise at `num_sims=100`. That's the mechanism
  end to end: `HistoricalBootstrapModel` gives same-bucket candidates
  statistically identical outcome distributions, so `recommend_players`'
  "winner" among close candidates is essentially a coin flip driven by
  simulation noise, not a real per-player signal — and real ADP already
  encodes genuine market information (injury risk, situation, aging
  curves) this points model has no other access to. Departing from it on
  a noise-level coin flip throws that real signal away, which is exactly
  what a systematic real-world loss looks like.

  **Verdict**: full Monte Carlo re-simulation per candidate player is the
  wrong tool for player-vs-player under the historical bootstrap points
  model specifically — it needs genuine per-player signal to differentiate
  candidates on anything but noise, which this points model doesn't have.
  This directly validates Windows's simpler, already-live `resolve_pick`/
  `best_value_within_position` approach (commits `670ce61`/`a39d573`): using
  ESPN's real per-player projections directly, instead of re-simulating an
  points model blind to per-player identity, was the right call. Not
  wired into live recommendations (per this phase's stated posture); kept
  as a validated-negative diagnostic and a real methodological lesson
  (worth revisiting *if* paired with real per-player signal, e.g. ESPN
  projections as `player_choice.py`'s `points_model`, rather than
  abandoned outright) rather than deleted.
- DST/K: deliberately *not* a target for modeling investment here (one
  low-value late slot, high year-to-year noise, streaming-friendly) —
  now that `historical/sources/ffc.py`'s DEF/PK position-code bug is
  fixed, real ADP-ranked DST/K already flow through the existing
  need-based baseline logic for free. No further DST-specific work
  planned unless something breaks again.

**Phase 4 — Roster Value v2**
- Starter/bench/replacement contribution properly (a bench WR5 scoring
  170 raw points isn't worth 170 to a roster that rarely starts him),
  positional cliffs, FLEX competition. The Draft Simulator has outpaced
  this engine — an imprecise objective function means a better Monte
  Carlo just optimizes the wrong target more precisely.
- ✅ **Waiver-wire replacement value** — a tractable first slice of the
  "week-to-week" idea below, built 2026-08-16, *not* the full rebuild
  (`historical/weekly_stats.py`, `backtest.py --scoring-mode
  waiver-adjusted`). Season-total scoring implicitly assumes an
  empty/injured roster spot scores zero for the rest of the year — real
  managers stream a replacement instead. Real weekly data (verified
  live, `nfl_data_py.import_weekly_data`) already tells you when a
  player didn't play: a missing row for that week, no injury-report
  parsing needed (checked live — that data source is spotty, came back
  empty for a real, well-documented season-ending injury). Missed weeks
  get credited at real replacement level (the ~Nth-best actual
  performance at that position that week, `DEFAULT_RANK_CUTOFF`, a
  documented approximation of the streaming tier in a 10-team league) instead
  of zero. **Verified concretely**: the 2023-slot-4 replay that lost
  -460.8 under season-total scoring (the Cousins/Watson QB case) only
  loses -77.2 under waiver-adjusted scoring — most of that loss was a
  season-total-scoring artifact, not a genuinely bad decision, though the
  model still loses that replay either way (drafting two mediocre QBs
  instead of one great one was still a real mistake, just a smaller one
  than it first appeared). Applied symmetrically to all three backtest
  conditions, doesn't bias the comparison.
- Eventual, larger-scope, still future work — **the full rebuild**:
  weekly (not season-total) *simulation* — each player's season becomes
  17 weekly draws, an optimal legal lineup gets selected fresh each week,
  bench depth/byes/volatility become emergent instead of hand-built
  bonuses. The waiver-wire adjustment above is a scoring-methodology
  layer on top of the existing season-total backtest; this would be a
  genuine outcome-model rearchitecture — a real, separate project, not
  something to fold into the same session.
  - **A real data point on how much this would actually matter**,
    checked live (2026-08-16, real 2023 weekly data): compared each
    top-24 player's rank by raw season total against their rank by
    "top-12-of-17 weeks" (a crude proxy for bench/streaming-optimized
    value — sum only a player's best 12 weeks, not all 17). Average rank
    movement was small: RB 2.1 spots, WR 1.4, TE 1.3, QB 1.5 — the
    biggest individual mover was 6 spots (Jaylen Warren, Devon Achane,
    Justin Herbert). Tentative, honest read: season-total scoring may be
    a reasonable approximation of *who's more valuable* more often than
    intuition suggests — the full rebuild's real payoff is probably more
    about roster-construction strategy (bench depth, FLEX allocation,
    streaming decisions) than reshuffling player rankings. Caveats:
    single season, a crude proxy (not a real optimal-lineup weekly sim
    with correlated bench/FLEX decisions), and rank movement isn't the
    same as points-value impact. Worth rechecking once more years of
    weekly data get pulled through the same analysis, not treated as
    settled from one season.

**Phase 5 — Draft Now vs. Wait**
- ✅ **Built as a standalone diagnostic** (2026-08-16,
  `draft_sim/draft_now_vs_wait.py`, `python -m
  fantasyprep.draft_sim.draft_now_vs_wait --year Y --pick N`) —
  deliberately *not* wired into live recommendations yet, per a second
  GPT-assisted design pass agreed with: validate the signal first.
  Reframed away from a hand-engineered formula
  (`urgency = value * (1 - survival) + cliff`, which looks quantitative
  but hides unstated assumptions about how much a survival probability
  is "worth") to a counterfactual the simulator answers directly: expected
  completed-roster value if I take this position now, vs. if I take my
  best alternative now and deliberately target this position again at my
  very next pick. Two pieces, both reusing existing infrastructure:
  `simulate_wait_and_target` (a close variant of `simulate_position_choice`,
  built entirely separately so nothing here can affect the validated live
  tool/backtest) for the completed-roster EV; `survival_probability` for
  whether the tier is actually at risk — which turns out not to need its
  own expensive roster simulation at all, just repeatedly sampling
  intervening opponent picks with the same ADP+stdev model already used
  everywhere else (`opponent.sample_pick`).
  - **Real live result, both directions confirmed**: at one real pick-25
    scenario, `recommend_positions` ranked TE highest, but Draft Now vs.
    Wait showed the TE tier survives to the next pick 99% of the time —
    cost of waiting **-43.6** (taking RB now and still getting TE next
    pick beats grabbing TE immediately). At a more contested pick-6
    scenario, cost of waiting was **+38.7** (draft now) despite reasonably
    high tier survival — confirms the tool isn't just defaulting to one
    answer, and that it surfaces real information `recommend_positions`
    alone doesn't (it has no concept of survival at all).
  - ✅ **Validated against real historical decisions**
    (`validate_against_real_outcome` — actually plays out both the "now"
    and "wait" strategies as complete real draft replays, reverting to
    normal `recommend_positions`-driven picking after the decision (and
    its follow-up), scored on real historical points, not hypothetical
    simulation). **8-sample real result: 6/8 agreement (75%)** between
    the pre-decision predicted direction and what actually scored better
    — a real, promising signal against a 50% coin-flip baseline, though
    n=8 is small and this should be rerun at real scale before trusting
    it fully.
  - **A genuine, separate discovery made while investigating this**: every
    one of the 8 samples showed exactly 100% survival probability, which
    was suspicious enough to dig into rather than accept at face value.
    Root cause confirmed directly (not a bug in this new diagnostic): the
    shared Gaussian opponent model (`opponent.pick_weight`, used
    everywhere in this project, not new here) has no correction mechanism
    for a player who's fallen unrealistically far past their real ADP —
    once the gap exceeds roughly 4-5 standard deviations, their selection
    weight numerically collapses to zero and they become permanently
    "stuck" undrafted for the rest of that simulated continuation.
    Verified directly: Christian McCaffrey (2023 ADP 2.4, stdev 1.0) has
    weight 0.84 at pick 3, 0.03 at pick 5, and exactly 0.0 by pick 8 and
    every pick after. A real draft room corrects hard for this ("wait,
    he's still here?!") — this model doesn't. Worth a real fix
    eventually (a fatter-tailed distribution, or an explicit
    anomaly-correction rule), not attempted tonight since it's a change
    to shared, already-validated infrastructure that deserves careful,
    unhurried design — flagged here rather than rushed.
  - ✅ **Wired into the live tool** (2026-08-16, commit `85356cd`):
    `/api/now-vs-wait` fires automatically after the main recommendation
    loads. Threading `pick_weight_with_tail_floor` through at the same
    time is what surfaced the next finding.
  - 🔴 **The 6/8 (75%) validation number above was measuring the wrong
    opponent model, and the corrected number is exactly coin-flip**
    (2026-08-16, same session as the QB scoring change below). Root
    cause: `validate_against_real_outcome` (and `_cli_validate`/`_cli_run`)
    always sampled opponent picks with plain `pick_weight`, even after
    the live tool switched to `pick_weight_with_tail_floor` — so the one
    number meant to answer "does this feature's advice track reality"
    was silently validating a *different* model than the one giving live
    advice. Fixed (`opponent_weight_fn` now threads through the whole
    validation path; new `--opponent-model` CLI flag, default stays
    `gaussian` for reproducibility with every earlier run — commit
    `95818d3`). Rerunning the same 8 samples under the live-matching
    model (`gaussian-tail-floor`), immediately after also changing QB
    scoring to 6-point passing TDs (see Scoring section below — both
    changes landed in the same session): **4/8 (50%)**, exactly the
    coin-flip baseline. Both disagreements involve QB decisions with
    large real deltas (pick 25 2023, +277.8; pick 70 2024, +191.2 —
    both predicted "wait," both should have been "draft now") — a
    plausible pattern (elite-QB scarcity under-weighted, or an effect of
    the same-session scoring change) but n=2 QB samples isn't evidence of
    a mechanism. Full writeup, table, and a separately-confirmed noise
    finding (the verdict's sign flips across RNG seeds at practical sim
    counts near a true toss-up) published as an Artifact during this
    session. **Status downgraded**: this feature's structural mechanics
    work correctly and consistently (verified via live spot-checks and
    unit tests), but its real-world predictive accuracy is currently
    unvalidated at best, not the "promising, above coin-flip" signal
    previously recorded. Needs a real-scale validation sweep (the
    hardcoded 8-sample `DEFAULT_VALIDATION_SAMPLES` list is not that)
    before trusting or distrusting the live advice it gives.

**Phase 6 — Historical Model Tournament**
- Parameter sweep, not one-off tweaks: bucket width/rolling/distance-
  weighted neighborhoods × historical year window × recency decay,
  evaluated automatically — but selected using dev-season results only,
  per the Phase 2 holdout discipline, not the final test season.
- Distance-weighted smoothing (observations near a rank weighted by
  distance instead of a hard bucket boundary) is probably the first
  concrete experiment, given the validation report already showed
  buckets are thin (~16-24 samples).

**Phase 7 — Performance**
- Only after 2-6 give a stable target worth being fast at: convergence
  testing (what's the minimum num_sims that doesn't change the
  recommendation?), profiling, vectorization, caching, parallel sims.

**Phase 8 — Market + Projection Intelligence, then Copilot**
- Vegas-derived projections, injury/missed-game distributions (before
  correlated outcomes — probably matters more), ADP momentum, sharp/
  casual disagreement — all feeding the Projection/Market engines'
  distributions directly (per the Principles section above), not a
  hand-weighted Draft Alpha score.
- Market snapshot logging starts now, independent of when it gets used —
  see Phase 0 addition below. History not collected today can't be
  recreated later.
- Decision Engine copilot layer last — by this point it's mostly wiring:
  reasoning text, live on-the-clock recommendation, calibration-backed
  confidence ("recommendations of this magnitude have won 71% of the
  time historically") instead of an arbitrary confidence label.

Phases re-sequenced from the original Phase 2/3/4 split after concluding
the Draft Simulator had raced ahead of the engines that should be scoring
and gating its output. Individually, later phases don't block each other
— pull opportunistically once the engine each depends on exists.

## Decisions (locked in 2026-08-15)

1. **Tech stack: Python.** pandas/numpy for simulation work, SQLite/DuckDB
   for time-series ADP/lines storage.
2. **First build: Sharp vs Casual ADP Gap tool.** Fast, low-risk win before
   tackling the simulator. Also a good forcing function for Phase 0's
   player-ID-mapping problem, since it needs multiple sources reconciled.
3. **Vegas data: free/scraped sources only for now**, no paid odds API.
   This caps the Vegas Projection System to whatever spread/total data is
   freely available — full player-prop lines (passing/rushing/receiving
   yard props) generally sit behind paid feeds, so that tool is likely
   limited to team-level implied totals rather than player-level stat
   lines until/unless this is revisited.
4. **Underdog ADP access is still open** — likely scraping their board,
   ToS needs checking before building on it. Not blocking for the first
   tool; revisit when Underdog data is actually needed. (Superseded by
   how Phase 1 actually shipped: Sleeper's `search_rank` turned out wrong
   for QB and got dropped, ESPN doesn't have usable historical ADP either
   — the sharp source that actually shipped is FantasyPros ECR. See
   README.md for the full saga.)

## Historical data foundation (2026-08-17)

Groundwork for the eventual player-level outcome model. Deliberately scoped as
**data engineering only** — the simulator, decision engine, backtest
methodology, scoring, and opponent model are all unchanged. New package
`historical/dataset/` lands alongside `historical/outcomes.py` rather than
replacing it.

The motivating gap: today every WR drafted 4th–6th at his position shares one
bootstrap distribution. The goal is a distribution conditioned on that player's
market rank, prior production, opportunity, age, and environment. That needs
trustworthy data first, so this pass built and audited the data and stopped
short of modelling.

### Three traps found auditing the source

A 1999–2024 nflverse seasonal CSV was supplied. It turned out to be the *same
upstream source* the project already pulls via `nfl_data_py.import_seasonal_data`
— its real value-add is the extra 1999–2009 seasons and being frozen, hence
reproducible. Auditing it before use found three things that would each have
silently corrupted anything built on top:

1. **`season_type` holds three views of every player-season** — REG, POST, and
   REG+POST — and REG+POST reports *different* fantasy points for 3,105 of the
   15,102 player-seasons. A naive `read_csv` triple-counts every player and
   inflates totals with playoff production. We keep REG, matching
   `nfl_stats.py`'s existing `s_type="REG"`.

2. **nflverse's `fantasy_points_ppr` uses 4-point passing TDs; our league uses
   6.** Established by reproduction rather than assumption: `compute_points` at
   `pass_td=4.0` matches that column to **0.00 max delta across all 13,415**
   regular-season skill rows. That is simultaneously a strong independent
   validation of `compute_points` itself — the same function the live pipeline
   and backtest score with — and proof the column can't be the outcome variable.
   Peyton Manning 2013 is 519.98 ours vs 409.98 theirs, exactly 2x his 55
   passing TDs. Kept as `fantasy_points_nflverse_ppr`, a cross-check only.

3. **The air-yards column family is fabricated *zeros* before 2006, not nulls.**
   nflfastR's charting starts in 2006; before that these columns pass a null
   check while carrying no information, so a model spanning the full history
   would learn that nobody had air yards in 2003. `receiving_yards_after_catch`
   is the nastiest case — *fragmentary* rather than uniformly zero (85 players
   in 1999, ~1% in 2000–2005, season max 185 vs 670 in 2006), so it looks alive.
   All masked to NaN by `loader.mask_uncollected_eras`.

Also found: one 1999 orphan row with no name *and* no position. Dropped, but
reported in the audit and bounded by a 0.1% threshold that still fails the build
outright if name resolution ever breaks systemically.

### Leakage prevention is enforced in code

A leak doesn't crash anything — it just makes a future backtest report an edge
that isn't real — so it isn't left to reviewer discipline. `features.py` splits
every column into `PRE_SEASON_COLUMNS` and its **complement**, so an
unclassified new column is treated as an outcome and fails closed.
`preseason_frame()` is the sanctioned way to build model inputs.

Two classifications that look over-cautious and are deliberate: `recent_team` is
an *outcome* (it's the player's last team, so a midseason trade encodes
post-draft information), and `yoy_fantasy_change` is an *outcome* despite
reading like a feature (season Y's change contains season Y). Prior-season lags
only carry a strictly adjacent season forward — a player who missed a year gets
NaN, not a two-year-old season relabelled "last year".

### Bucket-width study: width 3 holds for RB/WR, is thin for QB/TE

`BUCKET_WIDTH = 3` was a reasonable choice but never measured. Measured now,
two ways — by draft-time ADP rank (2010–2024, the comparable-to-today view) and
by prior-season finish rank (2000–2024, the only view available for the full
history, and leakage-safe by construction).

Reading the ADP study as the bias/variance trade it is: at width 3, RB and WR
carry ~43 samples per bucket with minimums of 30 and 36 — defensible. But the
**deepest 3-wide buckets hold 2 samples at QB and 1 at TE**, which is not a
distribution. Separately, WR loses almost nothing going 3 to 5 (spread of bucket
medians 178.8 to 175.1, a 2% loss) while median samples per bucket rise 43 to 71
— so if any width changes, WR is the strongest candidate. The prior-finish-rank
study carries ~60% more samples per bucket than the ADP one, since it spans 25
seasons instead of 15.

**`BUCKET_WIDTH` deliberately not changed.** It's a live simulator parameter and
deserves its own A/B backtest like everything else here, not a summary statistic.

### FFC's `teams` parameter does nothing — affects the existing backtest

Probing FFC directly while researching historical ADP: every past season returns
`meta.teams=12` regardless of what's requested, and for the current season the
echo is cosmetic — 2026 ADP values are **byte-identical across teams=8/10/12/14**
(0 of 259 players differ, same `total_drafts`). So every
`data/raw/.ffc_10_*.json` is 12-team-labelled pooled ADP despite the `_10_` in
the filename, and has been all along.

**This does not invalidate any existing backtest result** — both baseline and
model conditions draft from the same ADP, so the A/B comparison stays internally
consistent. What's wrong is the *claim* that the market input is 10-team
specific. `ffc.derive_rank_cutoff` computes draft depth from 10-team roster math
and applies it to an ordering that isn't 10-team specific, making the derived
replacement ranks approximate in a way its docstring doesn't acknowledge. Fix is
either a genuinely team-count-specific source or dropping the parameter and
documenting the input as pooled consensus ADP. Not done here — out of scope for
a data-only pass.

Related: the earlier note that FFC historical ADP "drifts" **did not reproduce**.
A fresh 2015 fetch versus the 2026-08-16 cache differs for 0 of 201 players,
with `total_drafts` identical. Most likely the drift was on a still-accumulating
recent season, not a closed one — meaning closed-season backtests should be
reproducible across calendar days after all.

### Coverage answers

- **Historical ADP**: PPR floors at **2010** — exactly where
  `DEFAULT_HISTORICAL_YEARS` already sat, so that was right. Standard scoring
  reaches 2008; half-PPR has nothing. **1999–2007 has no market rank at all**,
  which is the binding constraint on using the dataset's extra history for
  anything ADP-conditioned. Not recommended to mix standard into PPR buckets:
  a reception is worth 1 point in our league and 0 in that data, which
  systematically distorts exactly the pass-catchers we're trying to learn about.
- **Weekly data**: already working in `weekly_stats.py`, 1999–2024, joins on
  gsis `player_id`. Snap counts are shallower than advertised (effective floor
  **2013**, not 2012) and key on `pfr_player_id`, needing an id crosswalk.
- **Player metadata**: the best result. `import_players` joins at **100%** on
  gsis_id, with `birth_date`, `rookie_season`, and `years_of_experience` at 100%
  coverage **in every era including 1999–2005**. Age was the one intended model
  input with no source at all; it now has one, for free, no new dependency. The
  27% draft-position gap is undrafted players — signal, not missing data.

### Next

Cheapest high-value step is the **age/experience join** (one exact-key merge,
~5 new pre-season columns, zero leakage risk). Before spending anything on
pre-2010 ADP acquisition, first check whether prior-finish-rank conditioning —
available across all 26 seasons today — is sufficient on its own; that's a cheap
analysis against data already in hand and it determines whether the ADP gap
matters at all.

Artifacts: `docs/HISTORICAL_DATA_AUDIT.md` (generated, re-runnable),
`docs/HISTORICAL_ADP_RESEARCH.md`, `docs/WEEKLY_DATA_RESEARCH.md`,
`docs/PLAYER_METADATA_RESEARCH.md`, `data/historical/*.parquet`. 46 new tests,
full suite 349 passing, no regressions.

## Modeling research: four experiments (2026-08-17, Windows)

Full detail in `docs/MODELING_RESEARCH.md`; raw output in
`data/historical/{adp_vs_history_benchmark,distribution_benchmark,residual_analysis,rookie_model}.json`.
New `fantasyprep.research` package, with sklearn/scipy declared as an optional
`research` extra so the simulator, backtest and dataset build never depend on
them. **The draft engine was not modified by any of this.**

The four experiments were run in sequence, each prompted by the last one's
result, and together they tell one story that is not the story the project set
out to confirm.

### 1. The market already prices the median

Three arms predicting held-out season points, walk-forward, ridge. Common
population (has ADP *and* a prior season, n=1,458):

| arm | Spearman | R² |
|---|---|---|
| history | 0.5715 | 0.3661 |
| market (ADP) | 0.6074 | 0.4049 |
| both | 0.6149 | 0.4124 |

ADP adds **+0.046 R²** on top of history. History adds **+0.0075** on top of
ADP — essentially nothing.

**A methodological trap nearly reversed this.** The first run had history at
R² 0.2812. But only the market arm trained on drafted players (it has no choice
— a row with no ADP has no market features), while history trained on every
row. Matching the training populations lifted history to 0.3661: **two thirds
of the apparent gap was a training-population artifact, not information.** All
arms now train matched, with the unmatched variant kept as a robustness line.

ADP's real edge is on **rookies** (0.517 vs 0.283 Spearman) — where the player
has no history at all. Worth remembering why: ADP contains offseason
information (injuries, depth charts, holdouts) that lagged stats structurally
cannot. This is "recent vs stale information", not "crowd beats stats".

### 2. The incumbent bucket system is already well calibrated

Given #1, a better median is an exhausted margin, so the scoreboard moved to
calibration. Scored on 1,656 held-out player-seasons:

| arm | coverage err | CRPS | median MAE |
|---|---|---|---|
| adp_bucket (incumbent) | 0.0149 | 35.716 | 62.36 |
| adp_prior_bucket | 0.0152 | 35.728 | 62.35 |
| profile_quantile | 0.0154 | **34.551** | **60.47** |

**Mean absolute coverage error of 1.5 percentage points for the system already
in production** — being empirical, it is calibrated close to by construction.
That is a real validation of the current design and a hard bar, not a strawman.
The profile model wins **modestly**: CRPS −3.3%, median MAE −3.0%, at equal
calibration and slightly tighter intervals.

**Two-axis empirical bucketing is infeasible at this data volume**, which is why
arm B matches arm A to three decimals. Measured: the ADP × prior-finish cell
clears 20 samples only **4.0%** of the time; 65.8% falls back to ADP-only and
30.2% all the way to position-level. Conditioning beyond ADP rank requires a
*model*, not finer buckets.

**A concrete defect worth fixing**: both bucket arms under-cover the upside.
P90 coverage is 0.87 against nominal 0.90, P75 is 0.73 against 0.75 — real
outcomes beat the stated ceiling ~13% of the time instead of 10%.

### 3. Risk is separable from rank, but weakly and only early

Residual = actual − leakage-safe ADP-bucket expectation (n=1,656, mean −0.9,
stdev 79.0). Average calibration can hide what a draft engine needs: a system
can be calibrated overall while being overconfident about 30-year-olds and
underconfident about second-year breakouts, with the errors cancelling.

**Level: no exploitable bias.** Every feature association is weak (largest
|Spearman| 0.15). The market is efficient about *where* to draft a player.

**Dispersion: real but modest.** Fitting |residual| on the preseason profile and
splitting each ADP tier at its own median predicted risk gives stdev gaps of
+13.3 / +9.3 / +14.2 in tiers 1-6 / 7-12 / 13-24, and *reversed* (−3.9, −1.5) in
25-48 and 49+. Bootstrapped, because a 15% gap on ~120 players per cell is
exactly the kind of number that evaporates under resampling: pooled across the
top 24, split within tier, **+13.0 stdev, 95% CI [+3.6, +22.4] — excludes
zero.** No single tier clears zero alone, which is stated rather than papered
over.

Past rank 24 there is no signal. That is a real boundary, not a reason to keep
hunting — deep picks are lottery tickets where everyone is equally uncertain.
It also happens to be the useful place for it, since the early rounds decide a
season.

### 4. Rookies do NOT deserve their own model

The natural conclusion from #1 and #2, argued explicitly in review:
`prev_fantasy_points = NaN` is a different information state, not missing data.
Tested rather than assumed, on 198 held-out rookies:

| arm | coverage err | CRPS | median MAE |
|---|---|---|---|
| adp_bucket | 0.0267 | 36.063 | 64.08 |
| **shared_profile** | **0.0152** | **32.902** | **57.59** |
| rookie_specialist | 0.0640 | 35.361 | 63.09 |

**The specialist is worse on every metric**, with ~4× the coverage error, and
barely better than the incumbent it was meant to displace. Same ordering at RB
and WR separately. Cause is sample size, not feature design: a rookie-only model
trains on 62–236 rows while the shared model transfers an ADP-to-outcome
relationship learned from thousands.

### The through-line, and what it implies

Two independent experiments hit the same wall from different directions — 2D
bucketing failed because its cells were empty 96% of the time, rookie
specialisation failed because 150 rows cannot support a model. **The binding
constraint on this project's modeling is data volume, not model
sophistication.** Anything that fragments the sample loses; pooling wins.

That reorders priorities:

1. **Expand the sample before elaborating the model.** The 1999-2009 seasons can
   train the history component *today*, with no ADP at all — already sitting in
   `player_season_features.parquet`.
2. **Carry a variance term, not a better median.** The median is the market's
   job. The early-round dispersion signal is what is worth feeding the Monte
   Carlo.
3. **Fix the upside under-coverage**, a measured defect in the distributions the
   simulator samples from today.
4. **Pre-2010 ADP stays demoted** — its value is concentrated in rookie
   modeling, and the shared model (needing no extra ADP) is the better rookie
   model anyway.

**Size the next phase against gains that are real but incremental**: 3% CRPS
overall, 9% on rookies, a modest early-round variance signal. Not the step
change the "player-level vNext" framing assumed.

### Engineering fixed along the way

- **FFC name collision.** `position_ranks` built a name-keyed dict, so two
  players sharing a name overwrote each other and *both* returned the same
  positional rank — the 2011 Mike Williams pair (Tampa Bay and Seattle) both
  reported rank 62 despite ADPs 114 picks apart, and which one won was an
  accident of sort order. Two Steve Smiths had the same problem. `ranked_players()`
  is now the correct primitive (ranks attach to objects, not strings);
  `position_ranks` omits ambiguous names rather than resolving them arbitrarily,
  costing 8 of 2,461 entries (0.3%). Regression tests added.
- **ADP columns were classified as outcomes**, caught by the fail-closed leakage
  split — `preseason_frame()` was silently stripping them, so no model could
  have used ADP at all.
- **Age/experience/draft-capital join** landed at 100% coverage on `gsis_id`.
  The source's `years_of_experience` is deliberately dropped because it leaks
  (career-to-date, reflects today rather than the row's season);
  `seasons_since_rookie_year` replaces it. Undrafted players carry an explicit
  flag rather than an imputed pick number.

## What the engine can actually consume (2026-08-17 → 2026-08-20, Windows)

Part I of the modeling research asked what predicts outcomes. This asked the
follow-up that actually governs the roadmap: **can the draft engine consume
anything better, and what is wrong with the distributions it samples from
today?** The answers reordered the plan twice, and the second reordering is
where the payoff came from. Full detail in `docs/MODELING_RESEARCH.md`.

### The objective is structurally variance-seeking

`roster.starting_lineup_value` sorts a roster and takes the top N per slot —
a **convex selection operator**. By Jensen, raising a player's variance while
holding his mean fixed *raises* expected starting-lineup value. Nobody chose
this; it falls out of the roster math, and it is invisible today only because
every player in a bucket shares one distribution so the effect cancels.

Measured with a mean-preserving spread on WR (bucket mean held at 264.49 →
264.49 **exactly**, stdev 83.3 → 166.5): a **1.25× spread flips the top
recommendation** from RB to WR and buys +29.7 points; 2× buys +126.1. The
residual analysis had found a *real* dispersion signal of ~13 stdev points —
**the artifact is several times larger than the signal.**

### …and under realistic weekly lineups, the sign flips

The cause is the scorer, not the objective. Three scorers, 200 random rosters,
real 2023 weekly data, mean-preserving spread on WR *weekly* outcomes leaving
each season total exactly unchanged:

| scorer | ×1.25 | ×1.5 | ×2.0 | mean roster |
|---|---|---|---|---|
| season_hindsight *(what the engine does)* | +0.0 | +0.0 | +0.0 | 1478.0 |
| weekly_hindsight | +15.6 | +34.0 | +76.6 | 1690.1 |
| weekly_realistic | −3.9 | −9.0 | **−21.8** | 1529.5 |

Hindsight scoring **rewards** volatility; realistic weekly management
**penalises** it. Boom/bust players are systematically overvalued by any
hindsight-based scorer, and the engine is hindsight-based. Also: season-total
scoring understates roster value (−51.4 vs realistic), because it ignores that a
manager rotates players across a season.

Shipped as `--scoring-mode weekly-realistic`, additive — the season-total path
is byte-identical so no recorded result moves.

### The upside defect, and a fix that finally conditions without fragmenting

The buckets under-cover the upside, badly and non-uniformly: tier 1-6 P90
coverage is **0.80** against nominal 0.90 (elite players beat their stated
ceiling ~20% of the time), while tier 49+ is fine.

Four corrections tried. `per_tier` was the **third consecutive** loss to sample
fragmentation, after 2D bucketing and rookie specialisation. The winner,
`smooth`, kernel-weights the recalibration by distance in ADP rank so every
observation contributes to every estimate and no cell can empty — *conditioning
without fragmenting*. On tier 1-6: coverage error 0.0583 → **0.0311**, P90
0.80 → **0.87**, CRPS better in essentially every tier, median held at 0.50.

**Not shipped into the live distributions** — that needs its own A/B.

### Decision gate: 58% of picks change

Before spending more on modeling, the only question that matters for a draft
engine: does any of this change a pick? 60 real 2024 draft states, incumbent vs
recalibrated distributions, common random numbers — **35 of 60 (58.3%) changed**,
evenly across all six rounds.

The channel is wide open. But the median decision margin is **21.52 points on a
~1700-point roster (1.3%)**, so most recommendations are near-ties. The engine's
decisions are considerably more fragile than its headline win rates suggest.

### The payoff: weekly volatility is predictable

The scorer work changed what the right variance *target* was. Season-level
dispersion was barely predictable (+0.09); week-to-week volatility is what
actually costs a manager, because a lineup must be set every Sunday.

Walk-forward, 1,502 held-out player-seasons:

| target | model | naive: last season's own |
|---|---|---|
| weekly **CV** | **0.5126** spearman, R² 0.212 | 0.3791, R² **−0.239** |
| weekly stdev | 0.4765, R² 0.215 | 0.3475, R² −0.288 |

**Five times the season-level signal**, and it decisively beats the obvious
naive rival — last season's own volatility posts a *negative* R², worse than
predicting the average. So volatility is predictable but **not** simply a stable
player trait, which is what a naive implementation would have assumed. CV is the
headline because raw stdev is mechanically larger for high scorers and would
partly rediscover ADP. Concentrated where roster decisions are: RB 0.52, WR
0.36, TE 0.25, QB ~nothing.

**The most useful conclusion from the whole arc**: FantasyPrep's edge is not
going to come from predicting *how much* a player scores — the market has that.
It may come from predicting *how reliably* he scores it, which the market prices
far less efficiently and which only an honest weekly scorer can value correctly.

### Dashboard: scarcity and market-implied ceiling

Two live-draft additions. Each recommendation now shows its player's rank among
players **still available** at that position plus the count left ("RB2 left · 14
avail") — mid-draft that diverges sharply from preseason rank and is what the
decision turns on.

And a **market-implied ceiling** chip from OPOY award futures. ROADMAP's standing
no-paid-odds decision caps Vegas work to team totals because player props are
paywalled — but award futures turned out to be a free exception: ESPN's public
core API, DraftKings-sourced, no key, and **all 108 athlete IDs match the ESPN
cache already kept here** (a 100% join, unlike every other cross-source join in
this codebase). De-vigging is essential at that field size: measured overround
**1.6846**.

Chosen because the research pointed there. ADP already prices the median, so a
Vegas *projection* competes where nothing is left to win; the measured defect is
understated upside. Futures speak to the second.

**Honest limit**: ESPN preserves no historical futures (2020-2024 return zero
priced runners), so the signal has **no held-out test set** and is deliberately
kept out of the model — displayed only. Daily archiving now started, and the
`.gitignore` corrected, since an irreplaceable archive was being written to a
single machine's ignored directory.

### Engineering fixed

- **FFC name collision**: `position_ranks` was name-keyed, so the two 2011 Mike
  Williamses both returned rank 62 despite ADPs 114 picks apart. `ranked_players()`
  is now the correct primitive; the name lookup abstains on collisions.
- **ADP was classified as an outcome**, caught by the fail-closed leakage split —
  `preseason_frame()` was silently stripping it.
- **A leaking metadata column**: `years_of_experience` is career-to-date and
  reflects today, so a 2017 row carried an 8-year figure.

Suite went 303 → 460 across this work, no regressions.

## Tail-pooling A/B: a principled fix that did not survive measurement (2026-08-20)

The deep outcome buckets are genuinely degenerate. In the production cache the
deepest TE bucket held **one** sample (151.7), QB's held two, RB's five, WR's
four, against a typical 40-45 — and because `outcome_for_rank` falls back to the
deepest bucket for every rank past the end of the grid, that one sample served
every deeper rank, returned deterministically by `rng.choice` on a one-element
list. Worse, 151.7 sits *above* the median of TE4-6, so the model believed the
23rd tight end outscores the 5th, with certainty. With 24 TEs in the 2026 pool
this is live, not hypothetical.

`pool_thin_tail` merged each position's starved tail until it held ≥20 samples.
It was shipped **behind a flag with the default flipped to `pooled`**, on the
explicit undertaking that the default would flip back if the A/B lost.

**It lost.** 200 paired replays, 10 seasons × 10 slots × 2 seeds, common seed,
tail-floor opponent model, all 200 cells matched:

```
pooled won 96, lost 104
mean -15.8   median -8.7
season-clustered 95% CI [-40.2, +9.7]  -- not distinguishable from zero
```

Against the baselines, legacy is marginally better too — vs VOR 61% / +63.3
against pooled's 55.5% / +21.8; vs ADP+need 41% / −44.8 against 38% / −60.7.

**Checked whether the test was underpowered rather than negative**, since the
headline TE defect is reachable in only one backtest season (2021 is the only
2015-2024 pool with 22+ TEs). It was not underpowered: pooling alters the
sampled distribution for **19.8% of TEs, 12.2% of QBs, 6.1% of RBs and 2.9% of
WRs** across those pools. There was plenty for it to move.

**Likeliest explanation**: pooling trades one bias for another. Merging the
starved tail lifts the deepest distribution a long way — the deepest WR bucket's
median goes from **41.6 to 93.5** — so it overvalues genuinely deep players even
while fixing the degeneracy above them.

**Default reverted to `legacy`.** `pooled` stays behind `--tail-pooling` for
reproducibility and as something a better fix can be measured against.

**The defect is NOT resolved**, and that is stated deliberately rather than
quietly dropped. The recommended next attempt is narrower: pool only genuinely
degenerate buckets (n below ~5) while preserving rank monotonicity, instead of
this blanket tail merge.

Worth recording as a process point too. The fix was correct on first principles
— a one-sample zero-variance "distribution" that inverts rank order is
indefensible — and it still failed to help. That is exactly the case the A/B
discipline exists for, and the reason the default was put behind a flag before
being trusted.


## 2026-08-21 (Windows) — The two draft panels contradicted each other on screen

A live screenshot showed the tool disagreeing with itself. The recommendation
panel had **RB Jahmyr Gibbs 2029 / QB Justin Herbert 1969**. The now-vs-wait
panel directly beneath it put the same two branches at **1853 / 1852** and
concluded *"safe to wait"* — while also reporting **"100% chance a top RB is
still there next round"**, directly above a recommendation to draft the one RB
who was about to be taken.

Neither was a display bug. Both were the machinery underneath being wrong.

### The ~190-point disagreement

`compare_now_vs_wait` never received the marginal-value lookahead that
`recommend_positions` had grown (`need_aware_future`), so its simulated future
picks for ME were still plain ADP draws while the panel above used marginal
lineup value. Two panels, same question, different machinery.

The immediate cause was one missing keyword argument. The real cause was
structural: `simulate_wait_and_target` was a hand-maintained **near-copy** of
`simulate_position_choice`. It was deliberately kept as a copy — the original
docstring said so explicitly — on the reasoning that the live tool and backtest
depend on `simulate_position_choice`, so a separate implementation could not
perturb them.

That caution cost more than it bought. The copy could not perturb its twin, but
it also silently failed to *receive* anything its twin gained, and there was no
mechanism that would ever notice. Patching the missing argument by hand would
have restored agreement while leaving two implementations to be kept in step by
discipline alone — the same setup that produced the bug.

So the copy was deleted. `simulate_position_choice` now takes
`target_position_at_next_pick`, which expresses the entire content of what the
wait branch meant: take this position now, deliberately come back for that one at
my very next pick. That pick outranks the marginal-value lookahead, since
targeting it is the branch's whole premise. `simulate_wait_and_target` survives
as a thin wrapper, so existing callers and tests are untouched.

### The 100%

`survival_probability` answered "does **at least one of the top three** undrafted
players at this position survive to my next pick". On the live board that tier
survived **100.0% of 4,000 sims** — with 41 RBs left, of course it did.

But that is not the question the drafter is asking. He is not waiting for "a top
RB"; he is waiting for the player named on the card. Asked about Gibbs
specifically, the answer is **3.0%**. The function now takes `specific_player`,
and `compare_now_vs_wait` passes the actual target candidate.

The tier form is kept for callers that genuinely want it, so this is a widening
rather than a replacement.

### Measured, live board (slot 9, pick 89), before → after

| | before | after |
|---|---|---|
| survival reported | 100% | **3.0%** |
| "take RB now", recommend panel | 2029 | 2025 |
| "take RB now", now-vs-wait panel | 1853 | **2025** |
| verdict | safe_to_wait | **draft_now** |

The shared quantity is now computed *identically* by both panels rather than 190
points apart. A 29-point difference remains between "QB now, then RB next" (1944)
and the QB row (1973) — that one is **real and should stay**: the wait branch
forces an RB at the next pick, which genuinely costs 29 against free choice. Two
numbers differing because they measure different things is not the defect that
was being fixed.

### The copy was also the slow one

`simulate_position_choice` had been vectorized; its copy never was. Deleting the
copy therefore bought most of what "1000 sims without the wait" required:

| at 1000 sims | before | after |
|---|---|---|
| `simulate_wait_and_target` | 18.43s | **4.83s** |
| `/api/now-vs-wait` | 62.8s | **11.7s** |
| `/api/recommend` | 26–36s | **17.5s** |

`num_sims` default and the running server are both 1000 now; they had drifted to
1000 vs 400. Suite **508 passing**, no regressions.

### Open, and stated rather than quietly dropped

Three live-tool changes are now shipped **ahead of their backtests**:
`need_aware_future`, `pick_weight_with_value_urgency`, and this panel merge. All
three default **off** in the backtest, so no recorded result in this file moves —
but the live tool and the measured configuration have genuinely diverged, and
that gap is the next thing to close. The A/B discipline that caught tail-pooling
has not yet been applied to any of the three.
