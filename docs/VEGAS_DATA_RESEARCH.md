# Vegas Data Research

**Question**: can betting-market data improve FantasyPrep's draft recommendations,
and which parts of it are actually free?

**Short answer**: the *ceiling* half is free, valuable, and now built. The
*projection* half is both paywalled and — more importantly — aimed at the one
thing the market already does well.

---

## What ROADMAP.md already decided

> **Vegas data: free/scraped sources only for now**, no paid odds API. This caps
> the Vegas Projection System to whatever spread/total data is freely available —
> full player-prop lines generally sit behind paid feeds, so that tool is likely
> limited to team-level implied totals rather than player-level stat lines.

That assessment holds. Confirmed against The Odds API's current pricing: the free
tier covers NBA/MLB moneylines only, and **futures and player props both require
the Business plan ($99/mo)**.

But "no paid API" turned out not to mean "no futures", because ESPN publishes
them.

---

## Availability, probed live

| data | source | cost | status |
|---|---|---|---|
| **Award futures (OPOY, MVP, …)** | ESPN core API | **free, no key** | ✅ **built** |
| **Team implied totals** | ESPN scoreboard API | **free, no key** | available, not built |
| Player prop lines (yards, TDs, receptions) | paid feeds | $99/mo+ | not pursued |

### Award futures — free, and a clean join

`sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/{year}/futures`
returns 23 award markets including Offensive Player of the Year, Regular Season
MVP, and both Rookie of the Year awards, DraftKings-sourced, no key, no scraping.

- **108 players** carry 2026 OPOY odds.
- **All 108 athlete IDs match the ESPN player cache this project already keeps** —
  a 100% join with no fuzzy name matching, unlike every other cross-source join
  in this codebase.

**De-vigging is not optional at this field size.** The measured overround is
**1.6846**, so raw implied probabilities overstate every player's chance by
roughly two-thirds. Normalising to sum to 1 removes the book margin under the
standard proportional assumption.

De-vigged top of the 2026 field: Gibbs 9.1%, Bijan Robinson 5.9%, Ja'Marr Chase
5.4%, Puka Nacua 4.2%, Justin Jefferson 4.0%.

**Caveat worth carrying**: OPOY is *voted*, not computed. It rewards narrative
and team success alongside production. Treat it as the market's ranking of
ceiling, not as a literal P(monster season).

### Team implied totals — free, available, not yet built

`site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard` carries a
DraftKings block per game with `spread` and `overUnder`, from which each team's
implied total is the standard arithmetic. Free and reliable.

This is in-season, game-by-game data. It is genuinely useful for *weekly* lineup
decisions, and largely irrelevant to a *draft*, which happens before any of these
lines exist. Filed as available rather than built.

---

## Why the "Vegas player projection" idea is the weaker half

The instinct is to turn Vegas data into a better point projection. Two findings
argue against spending there:

1. **The market already prices the median.** Prior production adds only
   **+0.0075 R²** on top of ADP (`docs/MODELING_RESEARCH.md`, experiment 1). ADP
   is itself a market aggregation, so a second market signal aimed at the same
   quantity is competing where there is almost nothing left to win.
2. **Player props are the paywalled part**, so the expensive data is precisely
   the data aimed at the exhausted margin.

Meanwhile the *measured defect* is elsewhere: the outcome distributions
**understate upside for early picks** — elite players beat their bucket's stated
P90 about 20% of the time instead of 10% (`research/calibration.py`).

So the ordering is the opposite of the intuitive one. Skip the projection; take
the ceiling.

---

## What was built

`sources/espn_futures.py` fetches, parses and de-vigs award futures; the draft
dashboard shows each recommended player's **rank among still-available players by
market-implied ceiling** rather than the raw probability — a 3.9% OPOY chance
means nothing to a drafter in isolation, while "2nd highest ceiling left on the
board" is immediately actionable.

Two deliberate failure behaviours:

- A player the market did not price gets **null, never zero**. Unpriced is not
  the same claim as no ceiling.
- The whole lookup **degrades to empty on any error**, because a live draft must
  never break because an odds endpoint is unreachable.

---

## Recommended next steps

1. **Test whether OPOY rank predicts residual upside.** The signal is now
   available and displayed, but it has *not* been validated against outcomes.
   The honest next experiment: do players with high market-implied ceiling
   actually beat their ADP-bucket P90 more often? Award futures exist back
   several seasons, so this is testable, and it is the difference between an
   interesting chip and a real input.
2. **If it validates, feed it into the distributions rather than the median** —
   as an upside-shaping term, consistent with everything the research found.
3. **Leave team implied totals for the weekly-lineup work**, where they belong.

## Sources

- [The Odds API — NFL odds](https://the-odds-api.com/sports-odds-data/nfl-odds.html)
- [The Odds API — pricing/docs](https://the-odds-api.com/liveapi/guides/v4/)
- ESPN core API futures endpoint (probed live, 2026-08-20)
- ESPN scoreboard API (probed live, 2026-08-20)
