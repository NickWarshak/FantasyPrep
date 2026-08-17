# Historical ADP Research

**Question**: can we get `season x player x market expectation` for the seasons the
new 1999-2024 player-season dataset covers?

**Short answer**: no, and the ceiling is lower than expected. FFC PPR ADP starts in
**2010**; their older standard-scoring format reaches back to **2008**. That leaves
**1999-2007 with no market rank from this source at all** -- 9 of the 26 seasons in
the dataset. Two findings below are more consequential than the coverage answer.

## What ROADMAP.md already settled -- not re-researched

- **ESPN has no usable historical ADP.** Verified live in an earlier session by
  querying 2019/2022/2024 directly: ownership/ADP is a rolling current-season
  snapshot, not preserved history.
- **Sleeper's `search_rank` was wrong for QB** and was dropped as a sharp source.
- **FantasyPros ECR is the sharp source that actually shipped**, current-year.
- FFC is free for personal and commercial use, no auth, no scraping involved --
  it's a public JSON API. Nothing in this document required scraping any site.

## Finding 1: FFC's `teams` parameter does nothing

This is the significant one, and it affects the **existing** backtest, not just
future work.

Every FFC fetch in this project passes `teams=settings.teams` (10). Probing the API
directly:

| year requested | `teams=8` | `teams=10` | `teams=12` | `teams=14` |
|---|---|---|---|---|
| 2015 | returns 12 | returns 12 | returns 12 | returns 12 |
| 2020 | returns 12 | returns 12 | returns 12 | returns 12 |
| 2024 | returns 12 | returns 12 | returns 12 | returns 12 |
| 2025 | returns 12 | returns 12 | returns 12 | returns 12 |
| 2026 | returns 8 | returns 10 | returns 12 | returns 14 |

For every past season the response's `meta.teams` comes back as **12 regardless of
what was asked for**. Only the current season echoes the request.

And the echo is only an echo. For 2026, comparing the returned ADP values across
team counts: **0 of 259 players differ in ADP** between `teams=8`, `10`, `12`, and
`14`. The player set, the ADP values, and `total_drafts` (6,665) are identical.
FFC serves one pooled ADP dataset and labels it with whatever you asked for.

**What this means:**

- Every cached historical file in `data/raw/.ffc_10_*.json` for 2010-2025 is
  **12-team-labelled pooled ADP**, despite the `_10_` in the filename.
- The backtest's **A/B validity is not affected**. Both the baseline and model
  conditions draft from the same ADP, so the comparison remains internally
  consistent -- this does not invalidate any existing result.
- What *is* affected is the claim that the market input is 10-team-specific. It
  isn't, and never was. `ffc.derive_rank_cutoff` computes draft depth from
  `total_picks = rosters x teams` using our 10-team roster math, but applies it to
  an ADP ordering that isn't 10-team-specific. Positional depth genuinely differs
  between 10- and 12-team leagues, so the derived replacement ranks are
  approximate in a way the docstring doesn't currently acknowledge.

**Recommended follow-up** (deliberately not done here -- this task doesn't touch the
engine): either source genuinely team-count-specific ADP, or drop the `teams`
parameter from the FFC calls and document the market input as "pooled consensus
ADP", so the code stops implying a precision the source doesn't provide.

## Finding 2: historical ADP is stable, contrary to the earlier drift note

ROADMAP.md records that FFC "historical" ADP "isn't a frozen archive -- it drifts
slightly over time as their own data keeps accumulating."

Re-tested by diffing a fresh 2015 PPR fetch against the copy cached on 2026-08-16:

- 201 players cached, 201 fresh
- 0 players present in one and not the other
- **0 of 201 shared players changed ADP**
- `total_drafts` identical at 844

A settled historical season looks **frozen**, at least across this interval. The
most likely reconciliation is that the drift observed earlier was on a *recent*
season still accumulating drafts (2025 shows 8,470 drafts and is plainly still
live), not on a closed one. This is a refinement of the ROADMAP note, not a
contradiction -- and the practical upshot is better than recorded there: backtests
over closed seasons should be reproducible across calendar days.

Worth re-checking on a longer interval before relying on it.

## Finding 3: coverage floor by scoring format

Probed each format directly. Two distinct failure modes: HTTP 400 (the API rejects
the year outright) and HTTP 200 with `{"status": "Error", "errors": "No ADP data
found."}` (year accepted, no data).

| format | earliest usable season | behaviour before that |
|---|---|---|
| **PPR** | **2010** (214 players) | 2007-2009 "No ADP data"; 2000-2006 HTTP 400 |
| **standard** | **2008** (180 players) | 2007 returns success with 0 players; 2005 HTTP 400 |
| **half-PPR** | none found | errors for every year tested, 2007-2012 |

So PPR -- the format our league actually uses -- has a hard floor at 2010, which is
exactly where `DEFAULT_HISTORICAL_YEARS` already sits. That floor was correct.

### The 2008-2009 standard-scoring option

Standard scoring unlocks two seasons PPR can't reach. Whether that's worth taking:

- **Against**: standard scoring materially reorders the board relative to PPR. A
  reception is worth a full point in our league and zero in that data, which
  systematically depresses pass-catching backs and high-volume possession
  receivers -- exactly the players whose rank-to-outcome mapping we're trying to
  learn. Two extra seasons of a *differently-scored market* is not the same input
  as two more seasons of ours.
- **For**: it's real market consensus, and the outcome side (our own scoring,
  applied to real stats) stays correct either way.

**Recommendation: don't mix formats into the primary buckets.** If 2008-2009 are
wanted, carry them as a clearly-labelled separate series with a format flag, so any
result can be re-run without them.

### A related use: standard ADP could patch the thin 2012 PPR year

ROADMAP.md notes 2012 is anomalously thin in FFC's PPR data -- 93 players versus
~200 typical. Standard scoring for 2012 returns **145 players**. Same format
caveat applies, but it's a substantially better-populated year and worth knowing
about if 2012's thinness ever becomes a problem.

## Coverage summary against the new dataset

| seasons | player-season data | FFC PPR ADP | FFC standard ADP |
|---|---|---|---|
| 1999-2007 | yes (26-season dataset) | **no** | **no** |
| 2008-2009 | yes | no | yes |
| 2010-2024 | yes | yes | yes |

**The binding constraint on the new dataset's extra history is market rank.** The
11 extra seasons (1999-2009) are fully usable for anything conditioned on *prior
production* -- which is why `distributions.py` runs a prior-finish-rank study
across all 26 seasons -- but 9 of them cannot be conditioned on market
expectation at all.

## Target schema, when a source is found

```
season, player_id, player_name, position,
overall_adp, positional_rank, adp_stdev, adp_high, adp_low,
source, scoring_format, teams, draft_window_start, draft_window_end
```

`player_id` is aspirational: FFC returns names only, so joins currently go through
`players.normalize.normalize_name` plus position. Any new source that carries a
gsis_id would remove that fuzzy step entirely.

## Sources not pursued, and why

- **Underdog**: ToS needs checking before building on it; still open from ROADMAP.
- **FantasyPros historical ECR**: plausible but not verified here. ECR is a
  *ranking*, not an ADP -- a different quantity (expert opinion vs revealed draft
  behaviour), so it would need its own validation before being treated as market
  expectation.
- **MyFantasyLeague / NFFC ADP archives**: known to publish historical ADP and the
  most likely route to pre-2010 coverage. Not probed here because the task brief
  says not to build scrapers against unvetted sources. **This is the single most
  promising next avenue** if pre-2010 market data is genuinely wanted.

## Recommended next step

Decide whether pre-2010 market rank is actually needed. The prior-finish-rank
conditioning already available across all 26 seasons may be sufficient for the
player-level model, in which case the 2010 PPR floor costs nothing and no new
source is required. Establish that first -- it's a cheap analysis against data
already in hand, and it determines whether any further ADP acquisition work is
worth doing at all.
