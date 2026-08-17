# Weekly Data Research

**Question**: can we get `player x season x week` data to eventually model floor,
ceiling, volatility, consistency, and injury impact?

**Answer: yes, and it is already working in this repo.** This document is mostly an
inventory of something that exists rather than a proposal to build it. The genuine
gaps are snap counts (2013+) and route participation (absent entirely).

## What already exists

`historical/weekly_stats.py` has used `nfl_data_py.import_weekly_data` in
production since the waiver-adjusted scoring work. It is not research -- it backs
the backtest's `--scoring-mode` waiver option and shipped with tests
(`tests/test_weekly_stats.py`).

Two design decisions in that module worth carrying forward into any future weekly
modeling, because both were arrived at from real failures:

1. **Scoring reuses `nfl_stats.compute_points` unchanged.** Weekly rows carry the
   same stat columns as seasonal ones, so there is no second scoring formula to
   drift out of sync with the validated one. Worth preserving -- and note it means
   the 4-point-passing-TD finding in `HISTORICAL_DATA_AUDIT.md` applies identically
   to weekly data, since `fantasy_points_ppr` appears there too with the same
   meaning.
2. **Missing weeks are the injury signal.** nflverse simply has no row for a player
   in a week they didn't play. The module uses that rather than official injury
   designations, because those were checked live and came back **completely empty**
   for Kirk Cousins's well-documented season-ending 2023 Achilles tear. "Did they
   produce a row" is unambiguous and always present; the designation field is not.

## Coverage

| property | value |
|---|---|
| **Seasons** | 1999-2024, same range as the seasonal dataset (1999 verified: 5,031 rows) |
| **Columns** | 53 |
| **Granularity** | one row per player x season x week, REG and POST |
| **Weeks** | 1-22 in 2024 (REG through 18); 1-21 in 1999 |
| **2024 volume** | 5,597 rows; 559 unique skill-position players in REG |
| **Join key** | `player_id`, the same gsis id as the seasonal table -- **no fuzzy matching needed** |
| **Extra vs seasonal** | `week`, `opponent_team` |

Fields are otherwise the same family as the seasonal table: passing/rushing/
receiving counting stats, EPA, `target_share`, `air_yards_share`, `wopr`,
`fantasy_points`/`fantasy_points_ppr`.

**Median skill player has 10 rows in a season** (mean 9.4, IQR 4-15, max 17) --
the spread is mostly real roster churn and injury, not data loss, and it's exactly
the signal a consistency/availability model wants.

## Limitations

1. **The pre-2006 air-yards cliff applies here identically.** `air_yards_share`,
   `wopr`, `racr`, and the air-yards counting columns are unavailable before 2006
   at the weekly level too. Any weekly feature pipeline needs the same
   `mask_uncollected_eras` treatment `loader.py` applies -- the seasonal masking
   does not protect a separate weekly pull.
2. **~20% null on share metrics** (`target_share`, `air_yards_share`, `wopr`,
   `receiving_epa`) in 2024 skill rows -- mostly quarterbacks and players with no
   team-target denominator that week. Counting stats are 0% null.
3. **A missing row is ambiguous between causes.** Injury, healthy scratch, bye,
   suspension, and not-on-a-roster all look identical. Distinguishing them needs a
   roster/transaction source, not this one.
4. **No opponent-adjusted context** beyond `opponent_team`. Strength of schedule
   would have to be derived.

## Snap counts

`nfl_data_py.import_snap_counts` exists but is much shallower than the stat data:

| season | result |
|---|---|
| 2010, 2011 | raises `Data not available before 2012.` |
| 2012 | returns **0 rows** (accepted but empty) |
| 2013 | 23,799 rows |
| 2024 | 26,615 rows, 16 columns |

**Effective floor is 2013**, not 2012 as the error message implies. Columns include
`offense_snaps`, `offense_pct`, `defense_snaps`, plus game and player identifiers.

**Important join caveat**: snap counts key on **`pfr_player_id`** (Pro Football
Reference) and a `player` name string -- **not** the gsis `player_id` everything
else in this project uses. Joining snaps to the canonical table therefore requires
an id crosswalk (`nfl.import_ids` carries both) or falls back to fuzzy name
matching. That's real integration work, not a column rename, and it should be
costed before committing to snap-based features.

## What weekly data would unlock

Not built, deliberately -- listed so the eventual design has a target:

- **Floor / ceiling** as within-season percentiles rather than season totals, which
  is much closer to what actually wins a weekly-lineup league.
- **Volatility and consistency**: week-to-week variance, and "startable week" rates
  against a positional threshold.
- **Injury impact**: games missed, and production before versus after a missed
  stretch.
- **Genuine weekly simulation** in place of season-total bootstrapping -- the Phase
  4 rearchitecture ROADMAP.md already anticipates.

## Recommended next step

**Don't integrate weekly data into the simulator yet.** The ordering that makes
sense: get player-level *season* distributions working first (the current goal),
then move to weekly, because weekly modeling multiplies the model's complexity
while the season-level player model is still unbuilt.

The cheap, high-value thing available now is a **weekly-derived volatility feature
attached to the season table** -- per player-season standard deviation of weekly
points, and a startable-week rate. That's a handful of columns, needs no snap
counts and no id crosswalk, is leakage-safe when lagged (`prev_weekly_volatility`),
and would give the eventual player model a real risk input rather than an
inferred one.
