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
