# Project Status

**Read this first, before ROADMAP.md, before anything else.** This is a living
snapshot, not a log — whoever (whichever machine/session) changes something
material should edit the relevant section in place, not append. If a section looks
stale (references a job that's obviously long finished, a number that doesn't match
the latest report), fix it rather than trusting it.

`ROADMAP.md` has the full narrative history and technical detail behind every
decision below — this file is the fast-read current state, not a replacement for it.

## Machine roles

- **Mac mini**: large/slow backtests and anything CPU-heavy. Better sustained
  multi-core throughput than the Windows machine (which is an Intel i7-8650U, a
  4-core/8-thread ULV laptop chip — real contention was observed running 3
  concurrent backtest jobs on it).
- **Windows (Dell)**: interactive dev, feature work, smaller/faster tests — things
  that shouldn't have to wait behind a multi-hour background run.
- Neither machine should sit idle waiting on the other. Kick off long runs on the
  Mac and keep developing on whichever machine you're at.

## Cross-machine workflow

1. `git pull` before starting anything.
2. Do the work.
3. If you changed something that affects "current state" below (a fix landed, a
   big test finished, the headline numbers moved), **update this file**, not just
   ROADMAP.md — ROADMAP is the append-only history, this file is what the next
   session reads first.
4. `git add -A && git commit -m "..." && git push`.

Claude Code's own memory doesn't transfer between machines (it's keyed by local
filesystem path) — this file is the deliberate substitute. Read it fully at the
start of any session before doing new work.

## Current headline result

100 replays, all 10 real seasons (2015-2024) × all 10 draft slots, 1 opponent-room
seed, `num_sims=100`, season-total scoring, season-level-clustered 95% CIs (the
current best-practice methodology — see ROADMAP.md for why clustering matters here):

| vs. | Win rate | Mean Δ | Median Δ |
|---|---|---|---|
| Pure ADP-chalk | 75% (66-82%) | +152.2 | +128.7 |
| ADP + need | 63% (50-76%) | +30.2 (+2.4 to +56.5) | +36.2 (+1.0 to +57.0) |
| VOR | 57% (46-67%) | +27.8 (+4.1 to +54.3) | +27.3 (-2.9 to +44.9) |

Source: `data/backtest_shape_run.json`. Regenerate the report with
`python -m fantasyprep.draft_sim.backtest_report --in data/backtest_shape_run.json --out <path>.html`.

## The #1 open question right now

Almost the entire vs-VOR edge is concentrated at TE: model minus VOR by position is
TE +50.8, WR +12.2, QB +7.7, RB -42.9 (nets to +27.8). The model rosters a 2nd TE in
**88%** of replays vs. VOR's 6%. Without the TE effect the model would currently be
*losing* to VOR on the other three positions combined (-23.0 net).

This matters because the season-total scorer credits whichever of two drafted
same-position players had the better *actual* season — a real manager can't know
that in August. The 2nd-TE effect might be genuine value, or it might be inflated by
this hindsight quirk.

**Direct test in progress / check first**: `data/backtest_big_hourtest_waiver.json`
— identical draft states/decisions to the headline result above, but scored with
`--scoring-mode waiver-adjusted` (credits real replacement-level production for
weeks a player's data shows they didn't play, instead of scoring an empty/injured
slot as zero). If the TE-driven edge survives under this scorer, that's real
evidence. If it collapses, the +27.8 was substantially a hindsight artifact.

**Status as of 2026-08-16 ~1PM EDT**: `data/backtest_big_hourtest.json` (200-replay,
2-seed version of the headline result) and `data/backtest_big_hourtest_waiver.json`
(the waiver-adjusted comparison above) were both still running on the Windows
machine, ~95%+ and ~83% done respectively by CPU-time. **Check whether these files
now exist before doing anything else** — if they do, analyze them (see ROADMAP.md's
most recent entries for the exact commands) and update this section with the
answer, then delete this "status as of" paragraph once it's stale.

## What's next (priority order)

1. Answer the TE-hindsight question above — highest priority, may already be
   answerable if the waiver-adjusted run has landed.
2. Report redesign (verdict-first, VOR ordered first, position-edge breakdown near
   the top, a season×slot heatmap instead of the 100-row replay list, a draft-capital
   table) — **deliberately deferred** until #1 is answered, since the verdict itself
   may change.
3. Close the opponent tail-floor fix's remaining gap: `simulate.py`'s internal
   Monte Carlo lookahead still uses the old Gaussian opponent model even when
   `--opponent-model gaussian-tail-floor` is passed to the outer backtest.
4. Draft Now vs. Wait — built as a diagnostic, not yet wired into live
   recommendations. Next actual feature after the above.
5. Player-level Monte Carlo optimization (the model currently only recommends a
   *position*, then takes best-ADP at that position) — larger architectural jump,
   later.

**Explicitly not being worked on right now**: bucket widths, recency decay, Vegas
data, projection-source changes. The model produces enough signal now that the
priority is understanding *why* it exists, not adding more knobs.

## Quick health check for a new session

```
pytest                          # should be fully green (215+ tests)
git log --oneline -5            # recent history
cat data/experiments.jsonl | tail -5   # most recent logged experiments
```
