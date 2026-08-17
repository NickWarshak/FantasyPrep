# Player Metadata Research

**Question**: can we get birth date, age, rookie season, draft position, and NFL
experience for the players in the 1999-2024 dataset?

**Answer: yes, completely, at a 100% join rate with no fuzzy matching.** This is the
cleanest result of the three research tasks and the highest-value missing input for
the eventual comparable-player model.

## Source

`nfl_data_py.import_players()` -- 25,040 rows x 39 columns, already a dependency of
this project. No new source, no scraping, no auth.

## Join quality

Joins on `gsis_id`, which **is** the `player_id` in our canonical table. No name
normalization, no position tiebreaking, no fuzzy threshold.

| metric | result |
|---|---|
| Skill-position `player_id`s in our canonical table | 3,462 |
| Matched in `import_players` | **3,462 (100.0%)** |

Row-level `birth_date` coverage is 100% in **every** era -- 1999-2005, 2006-2012,
2013-2018, and 2019-2024 -- so this is not a modern-players-only source. That
matters: it means the 11 extra seasons the new dataset unlocked (1999-2009) are
fully usable for age-conditioned modeling, even though they have no market rank
(see `HISTORICAL_ADP_RESEARCH.md`).

## Field availability, matched skill players

| field | coverage | notes |
|---|---|---|
| `birth_date` | **100%** | exact date, so age at any reference point is derivable |
| `rookie_season` | **100%** | |
| `years_of_experience` | **100%** | |
| `height`, `weight` | 100% | |
| `college_name`, `college_conference` | 100% | |
| `draft_year` / `draft_round` / `draft_pick` | 73.1% of player-seasons | |

**The 27% draft gap is not missing data.** Those players went undrafted, which is
itself a meaningful signal -- an undrafted profile is genuinely different from a
first-round one, and imputing a draft position would destroy exactly the
distinction worth keeping. Encode it as an explicit `undrafted` flag rather than a
null to be filled.

## Derived age sanity check

Computing age as of September 1 of each season gives 100% coverage and entirely
plausible distributions:

| position | n | mean age | min | max |
|---|---|---|---|---|
| QB | 2,003 | 28.6 | 21.2 | 45.1 |
| RB | 4,187 | 26.1 | 20.6 | 38.7 |
| TE | 2,794 | 26.7 | 20.8 | 40.3 |
| WR | 5,061 | 26.3 | 20.8 | 41.9 |

The maxima are real (a 45-year-old quarterback season is Brady/Testaverde
territory, not a parsing error) and the minima sit just above 20, as they should.
Mean age by position matches the known shape -- running backs youngest, quarterbacks
oldest.

**Age is leakage-safe by construction**: date of birth is fixed, so age entering
season Y is knowable before season Y. It belongs in `PRE_SEASON_COLUMNS` when added.

## Bonus finding: `import_ids` is an id crosswalk

`nfl_data_py.import_ids()` -- 12,472 rows x 35 columns -- maps gsis ids to other
platforms. Coverage is narrower (it appears to cover fantasy-relevant players
rather than every player who ever recorded a stat):

| metric | result |
|---|---|
| Our skill `player_id`s present | 2,401 of 3,462 (**69.4%**) |
| `pfr_id` for those matched | 99.4% |
| `mfl_id` | 100% |
| `espn_id` | 99.9% |
| `sleeper_id` | 79.1% |
| `fantasypros_id` | 79.0% |

Two things this unblocks:

1. **`pfr_id` solves the snap-count join** flagged as real integration work in
   `WEEKLY_DATA_RESEARCH.md` -- snap counts key on Pro Football Reference ids, not
   gsis. Coverage would be 69.4% x 99.4% of players, and snaps only exist from 2013
   anyway, so the crosswalk is not the binding constraint there.
2. **`mfl_id` at 100%** is directly relevant to the ADP gap. MyFantasyLeague is the
   most promising route to pre-2010 historical ADP, and a clean id join would remove
   the name-matching step that FFC currently forces.

The 69.4% coverage means the crosswalk is fine for *enrichment* but must not be used
as a filter -- dropping unmatched players would silently discard 31% of the
historical player pool.

## Why this matters most

The eventual model is meant to answer "given this player's market rank, production,
opportunity, age, and environment, what is the distribution of plausible outcomes?"
Of those five inputs, the new dataset already supplies production and opportunity,
FFC supplies market rank back to 2010 -- and **age was the one input with no source
at all**. It now has one, at 100% coverage across all 26 seasons, for free.

Age also enables the comparable-player framing directly: "what happened to 29-year-old
running backs coming off a 250-touch season" is a query this data supports today,
and it's a far better prior than a rank bucket.

## Recommended next step

**Add an age/experience join to the feature pipeline.** It is the cheapest
high-value addition available: one merge on an exact key, four or five new
pre-season columns (`age`, `rookie_season`, `years_of_experience`, `draft_pick`,
`undrafted`), 100% coverage, no new dependency, and no leakage risk.

Do it as a `LivePlayerSeasonSource`-style optional enrichment rather than baking a
network call into the frozen build -- the current build is deterministic and offline,
which is worth preserving. Cache the `import_players` pull to `data/raw/` the same
way FFC fetches are cached.
