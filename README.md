# FantasyPrep

Draft decision engine tools. See [ROADMAP.md](ROADMAP.md) for the full plan.

**Working across multiple machines (or starting a new session)?** Read
[STATUS.md](STATUS.md) first — it's the living current-state snapshot (headline
results, what's running, what's next), kept up to date specifically so a session on
a different machine doesn't have to re-derive context from scratch.

## Setup

```
python -m pip install -e ".[dev]"
```

## Tool: ADP Gap Report

Compares ESPN's community ADP against a manually-exported "sharp"
comparison snapshot, flags the biggest gaps in both directions.

The current default source is **FantasyPros' Expert Consensus Rankings
(ECR)**, not a market ADP. That's a real distinction, not just a naming
detail: ADP (Underdog, NFFC, etc.) reflects where real money actually
drafts players; ECR is a poll of fantasy analysts' opinions, no money or
draft pressure involved. Landed here after ADP sources kept hitting
problems — Underdog is real money but wrong format (best ball, not
redraft) and its Terms of Use prohibit scraping; NFFC is real money and
correct format but currently thin-sampled (5-10 drafts) enough to produce
outliers (Lamar Jackson showed up ~25 picks off broad consensus, likely
sample noise); Sleeper's `search_rank` is free and live but confirmed
wrong for QB. ECR has one thin piece of supporting evidence for being a
better predictor historically: a 2015-season study (fantasyfootballanalytics.net)
found ECR modestly out-predicted ADP on R² across every position, though
that's a single decade-old season, not a strong verdict.

### 1. Create a sharp-comparison snapshot

FantasyPros' rankings/ADP tables are JS-rendered, so grabbing them is
manual: go to their
[Consensus Cheatsheet](https://www.fantasypros.com/nfl/rankings/consensus-cheatsheets.php)
(or export a CSV if the page offers one), set the scoring format to match
your league, and save/convert it to:

`data/raw/sharp_adp_YYYY-MM-DD.csv`

```csv
player_name,team,position,adp,source
Ja'Marr Chase,CIN,WR,1,fantasypros_ecr
Jahmyr Gibbs,DET,RB,2,fantasypros_ecr
```

Columns: `player_name, team, position, adp, source`. `position` should be
one of `QB, RB, WR, TE, K, DST`. For ECR, `adp` is just rank order (1, 2,
3...) — the tool only cares about relative ordering, not the literal
value, so this works the same as a real ADP pick number. The same CSV
format also still works for real ADP data (Underdog, NFFC, etc.) if you
want to go back to a market-based source instead.

Known gap: FantasyPros' team-defense naming doesn't match ESPN's closely
enough for the fuzzy matcher, so DST rows currently go unmatched. Low
priority since DST barely matters for draft strategy, but worth knowing
before wondering where your defenses went.

### 2. (Optional) Add name aliases

If a player's name differs between the sharp source and ESPN in a way the
matcher can't resolve on its own (rare — fuzzy matching handles most
cases), add an override to `data/aliases.csv`:

```csv
sharp_name,position,espn_name
Gabe Davis,WR,Gabriel Davis
```

### 3. Run the report

```
python -m fantasyprep.adp_gap.report --year 2026
```

This fetches ESPN's public player pool (cached to
`data/raw/.espn_cache_<year>.json` after the first run — pass
`--refresh-espn` to bypass the cache), matches it against the latest
sharp-ADP snapshot in `data/raw/`, and writes `adp_gap_report.md` +
`adp_gap_report.csv`.

Use `--sharp <path>` to target a specific snapshot instead of the latest.

### Alternative: live Sleeper source

Instead of a manual CSV, you can compare ESPN against Sleeper's live
`search_rank` field (from their official, documented, no-auth
`/v1/players/nfl` endpoint):

```
python -m fantasyprep.adp_gap.report --year 2026 --sharp-source sleeper --exclude-positions QB
```

`search_rank` isn't literally ADP — it holds up well for skill positions
(spot-checked against a real Sleeper mock draft) but looks like a
player-value/production rank rather than market draft cost, and QB is
where those diverge hardest in single-QB leagues: `search_rank` has Josh
Allen at 4th overall, real single-QB Sleeper ADP has him around 20. If
you're in single-QB leagues, always pass `--exclude-positions QB` with
this source. It's cached to `data/raw/.sleeper_cache.json` (pass
`--refresh-sleeper` to bypass). This hasn't been verified beyond WR/QB, so
treat it as directionally useful rather than precise — the manual-CSV
route is more trustworthy if you want real ADP numbers.

### Reading the output

- **Raw gap**: `espn_adp - sharp_adp`. Positive means the sharp market
  drafts the player earlier than ESPN's average — a potential ESPN
  sleeper. Negative means ESPN drafts him earlier — a potential trap in
  ESPN-scored leagues.
- **Adjusted score**: raw gap divided by a round-based tolerance (tighter
  early, wider late), since a 10-pick gap at pick 5 means far more than at
  pick 150. This is what the report is sorted by. It's a heuristic, not a
  true ADP standard deviation — see `adp_gap/compute.py` for the buckets.
- **Match confidence**: 100 for exact/alias matches, else the fuzzy-match
  score (only matches ≥85 are kept).

By default, players ranked beyond ADP 220 on either source are dropped
before matching (`--max-adp`, pass `0` to disable). Past typical draft
depth, sources stop reflecting real draft behavior and disagree wildly on
barely-drafted players — without this cap the report gets dominated by
fake "gaps" of 100+ picks that are just noise between two different ways
of ordering players nobody actually drafts.

## Tool: Monte Carlo Draft Simulator

The centerpiece tool: given where you're picking and what's already off
the board, which **position** (QB/RB/WR/TE) maximizes your expected
starting-lineup value if you take it right now?

### How it works

- **Live opponent/pick modeling**: uses
  [FantasyFootballCalculator's free ADP API](https://fantasyfootballcalculator.com/api/v1/adp/)
  — real drafts (6,466 for 10-team PPR as of 2026-08-15), exactly
  format-matched (`teams`/scoring params), with real per-player ADP
  standard deviation rather than a guessed tolerance. Both "opponent"
  picks and "your" future picks (beyond the one being decided right now)
  sample from this — best-player-available weighted by proximity to a
  player's real ADP, no positional-need-awareness yet (that's a later
  roadmap phase, see `ROADMAP.md`).
- **Outcome model**: rather than a synthetic points projection, uses real
  historical data — 2018-2024 actual season fantasy points
  ([nfl_data_py](https://github.com/nflverse/nfl_data_py), computed under
  your league's exact scoring, not someone else's PPR assumption) joined
  against each player's historical draft-time position rank (also FFC).
  Outcomes are pooled into buckets of 3 consecutive ranks (e.g. "WR4-6"),
  and each simulated player's season score is a **bootstrap sample from
  real historical outcomes** at that rank, not a fitted curve — this
  captures real bust/boom shape (e.g. an actual $0-point QB bust shows up
  in the QB1-3 bucket) for free.
- Bench points don't count — only the optimal starting lineup
  (`draft_sim/roster.py`) contributes to a simulated roster's value.

### Usage

Describe the draft so far in a small JSON file (see
`data/draft_state_example.json`):

```json
{
  "teams": 10,
  "my_draft_slot": 3,
  "picks": [
    {"pick": 1, "player": "Bijan Robinson"},
    {"pick": 2, "player": "Jahmyr Gibbs"}
  ]
}
```

`picks` is everyone off the board so far — each entry carries its own
pick number, so order in the list doesn't matter and gaps are fine. A
pick number ahead of the earliest open one is a **keeper**, pre-assigned
before the live draft reaches that slot. "Mine" isn't stored — it's
derived from which team column a pick number belongs to, via snake-draft
math (`my_draft_slot` + `teams`), so you can't get it out of sync by
forgetting a flag. The current decision point is the smallest pick number
with nothing assigned yet, keeper gaps included.

```
python -m fantasyprep.draft_sim.simulate --draft-state data/draft_state_example.json --year 2026
```

Prints expected starting-lineup points per candidate position (mean +
P25/P75 spread, not a false-precision single number):

```
Position    Expected       P25       P75
WR            1567.6    1452.7    1702.3
RB            1560.5    1421.2    1689.5
QB            1521.2    1438.6    1613.0
TE            1511.7    1393.1    1634.7
```

`--num-sims` defaults to 300 (~12s runtime); historical outcome
distributions are cached to `data/raw/.outcomes_<teams>.json` after the
first run (`--refresh-historical` to rebuild), live ADP to
`data/raw/.ffc_<teams>_<year>.json` (`--refresh-live-adp`).

**Points source**: `--points-source historical` (default) bootstraps a
real historical outcome by draft-rank tier, as described above.
`--points-source espn` instead uses **ESPN's own named-player season
projection** (`sources/espn.py`, reusing the same `kona_player_info`
payload the ADP gap tool already pulls, scored under the league's own
settings, not ESPN's default display), falling back to the historical
model for anyone ESPN doesn't project. Named projections are more
specific per player, but a single number, not a distribution — expect
tighter P25/P75 spreads than the historical source, since simulated
variance then only comes from *which* players end up on the roster, not
from each player's own outcome uncertainty. (Extracting this required
reverse-engineering which of ESPN's duplicate stat-ID fields is actually
populated in the season-total block — receptions sit under id `53`
there, not the `41` used elsewhere; see the comment in `sources/espn.py`
if that ever needs revisiting.) Same flag on the web UI's
`/api/recommend?points_source=espn`.

**v1 scope, explicitly bounded** (see `ROADMAP.md` for what's deferred):
position-level recommendations only, not full player rankings; no
positional-need-awareness in opponent or "your future picks" modeling
(pure ADP-best-player-available); no correlated outcomes/stacking; league
settings currently hardcoded (`league/settings.py`) rather than
configurable via CLI yet -- confirmed as 10-team full PPR, 1 QB / 2 RB /
2 WR / 1 TE / 2 FLEX / 1 DST / 6 bench, no kicker.

### Live draft companion: local web UI

The JSON-file-and-rerun-the-CLI workflow above doesn't work during an
actual draft where picks happen every 30-90 seconds. The web UI is the
same engine (`recommend_positions`/`state_from_picks`, shared with the
CLI — not a reimplementation) behind a page you keep open during your
draft:

```
python -m fantasyprep.webapp.app --year 2026 --draft-state data/draft_state_live.json
```

Then open `http://127.0.0.1:5000`. It's a Sleeper-style grid — teams as
columns, rounds as rows, your column highlighted, the current pick
outlined. Enter your draft slot once; the grid fills in automatically.

**Every cell works the same way, whether it's a live pick or a keeper**:
click an empty cell, search the player, select — no "mine" checkbox,
since which column you clicked already answers that. Click the
highlighted current-pick cell during a live draft; click any cell further
out to pre-assign a keeper (the picker labels it as such so you don't
confuse the two). Filled cells are color-coded by position and clickable
to clear (for correcting mis-clicks). "Reset draft" clears everything.

Hit "Get Recommendation" whenever you're actually on the clock — it's the
slow action (~5-12s at the default sim count), so it's a manual button,
not automatic after every pick.

**Keyboard-driven entry, for logging picks as fast as they happen**: press
`Enter` anywhere on the page (with no input focused) to open the picker
for the current pick — no clicking the cell required. Type a name;
suggestions appear with the top match pre-highlighted. `↑`/`↓` to move
between matches, `Enter` to assign the highlighted one, `Escape` to
cancel without assigning. Assigning the actual current pick this way
immediately reopens the picker for the *new* current pick, so a whole
run of picks — yours and everyone else's — can be logged as
type → Enter → type → Enter without ever touching the mouse. This
chaining only happens for live (current-pick) entry; assigning a keeper
(a future cell, clicked directly) closes normally instead, since that's
a deliberate one-off action, not part of the live sequence.

State autosaves to the `--draft-state` file after every change, in the
same format the CLI reads — the two are interchangeable; you can drop
into the CLI mid-draft against the same file, or hand-edit the JSON and
refresh the page. This is a local single-user tool (no auth, in-memory
session) — don't expose the port beyond your own machine.

### Auto-simulate opponent picks

Manually logging every opponent pick is tedious for a mock draft, or for
skipping ahead in a real one. **"Simulate opponent picks"** auto-fills
picks for every team except yours, one pick at a time
(`POST /api/simulate/step`), stopping automatically the moment it reaches
your turn — and auto-opens the picker for you right then, so you can hand
off straight from watching to deciding.

- **Speed** (Instant/Fast/Normal/Slow) is purely the pacing between
  picks appearing, for watching it happen rather than a wall of picks at
  once.
- **Randomness** (0-3, default 1.0) controls how much opponent picks
  deviate from strict ESPN ADP order. `0` is effectively chalk (always
  the closest-to-ADP player); `1.0` uses the same round-based tolerance
  heuristic the ADP gap tool uses (`adp_gap/compute.py`'s
  `tolerance_for_adp`, tighter early rounds, wider late) as the spread
  around each player's ESPN ADP; higher values wander further. ESPN
  doesn't publish a real per-player standard deviation the way FFC does
  (used internally by the Monte Carlo simulator's own opponent model),
  so this is a synthetic, tunable stand-in
  (`draft_sim/auto_pick.py`) — reuses the exact same ADP-weighted
  sampling function (`draft_sim/opponent.py`), just fed ESPN data with a
  scaled tolerance instead of FFC's real stdev.
- **"Stop"** halts before the next in-flight pick; because the loop is
  one small request per pick with a client-side delay between them, this
  is a genuine stop, not just a paused animation — nothing further gets
  written to the draft state after you click it (there can be a brief
  visible delay up to one "speed" cycle before the button itself flips
  back, but no further pick is made once you click it).
- Skips cleanly over any keepers already sitting in the way (their picks
  aren't "current" anymore, so the loop never touches them).

## Tool: Data Integrity Review

Before touching the outcome model, the historical-year range, or recency
weighting: does the underlying data actually hold up? This is a
standalone report, not part of the live draft tools, generated on demand
rather than kept running.

```
python -m fantasyprep.historical.report --year 2026 --out data/validation_report.html
```

Two independent cross-checks, not just "the code ran without errors":

- **Historical actuals** (`historical/validate.py`,
  `cross_check_actuals`): our computed points for every player-season
  2018-2024 against nflverse's own `fantasy_points_ppr` column — a
  near-exact match is expected under matching full-PPR settings. This is
  exactly what caught a real bug: `special_teams_tds` (return TDs) and
  2-point conversions weren't credited (`historical/sources/nfl_stats.py`),
  confirmed via a live player (Gunner Olszewski, 2023) and fixed —
  max delta across 4,056 real player-seasons went from 6.00 to 0.00.
- **ESPN projections** (`cross_check_espn_projections`): our extracted
  current-year projections against ESPN's own `appliedTotal` field, under
  three scoring variants (ESPN's default doesn't match any specific
  league's settings, so the best-fitting variant is compared, not an
  exact match). Also surfaced a real ESPN-side data quality issue: a
  handful of players (mostly QBs) have an internally-impossible
  `appliedAverage` from ESPN itself (60-100+ pts/game) — not our bug,
  set aside as a documented anomaly rather than compared.

Plus the actual historical outcome distributions the Monte Carlo
simulator draws from, visualized rather than just described: per
position, draft-rank tier vs. real point outcomes (median, P25-P75 band,
every individual sample dot) and sample depth per tier, so thin data
reads as thin data rather than a confident-looking average.

Regenerate whenever the underlying data changes (scoring rules,
historical year range, bucket design) — it's a snapshot, not a live
dashboard.

## Tool: Draft Backtest

Does the Monte Carlo simulator's position recommendation actually beat a
realistic drafter, on real historical outcomes? Replays full 150-pick
drafts three times per (season, draft slot, opponent-room seed) — a pure
ADP-chalk baseline, a positional-need-aware ADP baseline, and the actual
`recommend_positions` engine — then scores all three resulting rosters on
what really happened that season.

```
python -m fantasyprep.draft_sim.backtest --num-seeds 10 --out data/backtest_results.json --experiment-name "my-experiment"
python -m fantasyprep.draft_sim.backtest_report --in data/backtest_results.json --out data/backtest_report.html
```

- `--years` (default 2015-2024 — each has enough strictly-prior seasons,
  now back to 2010, to build leakage-safe outcome buckets from),
  `--slots` (default all 10), `--num-sims` (recommendation quality per
  decision, default 100 — bumped from 50 after a live convergence check,
  see `convergence.py`), `--num-seeds` (opponent-room draws per (year,
  slot) cell, default 1 — more seeds buys statistical power without
  needing more historical seasons).
- `--experiment-name`/`--experiment-notes`: logs the run's params and
  headline results to `data/experiments.jsonl` (see
  `experiment_registry.py`) — omit for ad-hoc smoke tests you don't want
  cluttering the log.
- `--scoring-mode {season-total, waiver-adjusted}` (default
  `season-total`): the default implicitly scores an empty/injured roster
  spot as zero for the rest of the season. `waiver-adjusted` instead
  credits real replacement-level production for weeks a player's data
  shows they didn't play (`historical/weekly_stats.py`) — a real manager
  streams a replacement rather than eating a zero. Not the full
  week-to-week simulation rebuild (still future work), just a more
  realistic scoring lens on the existing backtest.
- Opponents in all three conditions share an RNG seed (common random
  numbers) so the comparison isolates "my" strategy, not also opponent
  luck.
- A test year's outcome buckets are built only from strictly-prior years
  (leakage-safe) — a separate cache per test year
  (`data/raw/.outcomes_{teams}_pre{year}.json`).
- Headline stats include 95% confidence intervals: a Wilson interval for
  win rate, and a bootstrap that resamples whole (year, slot) cells rather
  than individual replays for mean/median — opponent-room seeds sharing a
  cell aren't independent observations of each other.
- Also reports a confidence-weighted calibration check: at every genuine
  model decision, blends the real point value of the top-2 candidate
  positions' best-available player, weighted by how close the model's own
  estimate was between them — does the model's own uncertainty tracking
  predict what actually happened, not just "did it win."
- The report shows every (season, slot) cell's result, not just the
  average, plus concrete "what changed" case studies naming the actual
  players each strategy drafted differently in the biggest swings.

## Tool: Convergence Check

Does a recommendation actually stabilize as `--num-sims` increases, or is
it still noisy? Repeats `recommend_positions` at a fixed, realistic
mid-draft decision across several `num_sims` levels and measures how
often the top-ranked position agrees across repeats.

```
python -m fantasyprep.draft_sim.convergence --year 2026 --pick 45
```

Real finding from this session: agreement can resolve cleanly with more
sims (pick 15: 47% at 50 sims → 87% at 200) or stay noisy even at 400
sims (pick 45) — the latter isn't a convergence failure, it's more likely
a genuinely close decision where two positions' true values are near-tied.

## Tool: Draft Now vs. Wait

A standalone diagnostic (not wired into live recommendations yet):
what's my expected completed-roster value if I take this position now,
versus if I take my best alternative now and deliberately target this
position again at my very next pick? Reframed away from a hand-tuned
formula to a counterfactual the simulator answers directly — see
`draft_sim/draft_now_vs_wait.py`'s module docstring for why.

```
python -m fantasyprep.draft_sim.draft_now_vs_wait --year 2026 --pick 25
python -m fantasyprep.draft_sim.draft_now_vs_wait --validate
```

- The first form evaluates one real decision point and prints EV/P25/P75
  for both "take now" and "wait," plus the target position's survival
  probability to your next pick.
- `--validate` runs `validate_against_real_outcome` across several real
  historical decision points — actually plays out both strategies as
  complete draft replays scored on real points, and checks whether the
  pre-decision predicted direction matches which one really scored
  better. Real 8-sample result this session: 6/8 agreement (75%),
  against a 50% coin-flip baseline.
- A real discovery made while validating: every sample showed exactly
  100% survival probability. Root cause (confirmed directly, not a bug
  in this tool): the shared Gaussian opponent model
  (`opponent.pick_weight`) has no correction for a player who's fallen
  far past their real ADP — once the gap exceeds ~4-5 standard
  deviations their selection weight numerically collapses to zero and
  they're permanently "stuck" undrafted for the rest of that simulated
  continuation. A real draft room corrects hard for this; this model
  doesn't yet (see ROADMAP.md Phase 5).

## Tool: VOR Baseline

A second, stronger baseline for the backtest ladder (beyond ADP+need):
value-over-replacement, computed entirely from data already on hand — no
historical projections needed, just the same outcome-bucket machinery
the model itself draws from. A player's VOR = their bucket's real
historical mean minus a real replacement-level baseline for that
position (`draft_sim/vor_baseline.py`).

```
python -m fantasyprep.draft_sim.vor_baseline --years 2023 2024 --slots 1 2 3 4 5
```

Real 10-replay spot check: VOR beat the ADP+need baseline 6/10 (60%),
mean +49.0, median +96.3 — a promising early signal, not yet folded into
the main 3-way backtest (`backtest.py`) as an official 4th condition.

## Tool: Market Snapshot Logger

Daily timestamped ADP/rankings snapshot, captured now independent of
whether anything downstream uses it yet — history not collected today
can't be reconstructed later. (Confirmed concretely while building the
backtest: FFC's own "historical" ADP for a past season isn't a frozen
archive, it drifts slightly over time.)

```
python -m fantasyprep.market.snapshot --year 2026
```

Writes `data/snapshots/espn_YYYY-MM-DD.json` and `ffc_YYYY-MM-DD.json`,
always fetched live (no cache reuse — the point is a real point-in-time
reading). One source failing doesn't block the other. FantasyPros ECR is
intentionally not auto-fetched here — no clean free programmatic endpoint
for it, so it stays on the existing manual `sharp_adp_YYYY-MM-DD.csv`
process (`sources/manual_adp.py`).

Runs automatically once a day via a Windows Task Scheduler entry
(`FantasyPrep Market Snapshot`, 8:00 AM daily,
`scripts/run_market_snapshot.bat`) — persists independent of any
particular session; check/manage it via Task Scheduler or
`schtasks /query /tn "FantasyPrep Market Snapshot"`. The `--year` is
hardcoded to 2026 in the batch script; bump it once next season's draft
prep begins.

## Tests

```
python -m pytest
```
