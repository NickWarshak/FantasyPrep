# Modeling Research: what actually predicts fantasy outcomes

Four leakage-safe experiments run on the 1999–2024 historical foundation, in the
order they were run, each one prompted by the previous one's result. All are
reproducible from `fantasyprep.research.*` and write their full output to
`data/historical/*.json`.

**None of them changed the draft engine.** They exist to decide what is worth
changing.

---

## The through-line

Read in order, the four experiments tell one story, and it is not the story the
project set out to confirm:

1. The market already prices the median. Prior production adds **+0.0075 R²** on
   top of ADP.
2. The incumbent bucket system is already **well calibrated** — 1.5 percentage
   points of coverage error.
3. Risk *is* separable from rank, but weakly, and only in the early rounds.
4. Every attempt to condition more finely **lost to a simpler pooled model**.

The binding constraint on this project is **data volume, not model
sophistication.** Two independent experiments hit the same wall from different
directions, and that should govern what gets built next.

---

## 1. Does ADP add information beyond a player's own history?

`python -m fantasyprep.research.benchmark`

Three arms predicting held-out season fantasy points, walk-forward (season Y
trains only on seasons < Y), ridge regression. Common population = players with
both an ADP and a prior season, n = 1,458.

| arm | Spearman | R² | MAE |
|---|---|---|---|
| history | 0.5715 | 0.3661 | 62.03 |
| market (ADP) | 0.6074 | 0.4049 | 59.62 |
| both | 0.6149 | 0.4124 | 59.24 |

**Incremental value is lopsided:**

- ADP added on top of history: **+0.0434 Spearman, +0.0463 R²**
- History added on top of ADP: **+0.0075 Spearman, +0.0075 R²**

### The methodological trap that nearly reversed the conclusion

The first run had history at R² 0.2812 and was nearly reported. But only the
market arm was training on drafted players — it has no choice, since a row with
no ADP has no market features — while history trained on every row. Matching the
training populations:

```
history trained on ALL prior rows      r2 = 0.2812
history trained on DRAFTED rows only   r2 = 0.3661   <- matched, fair
market                                 r2 = 0.4049
```

**Two-thirds of the apparent gap was a training-population artifact, not
information.** All arms now train on the matched population; the unmatched
variant is retained as a reported robustness line.

### Rookies are where ADP earns its keep

On rookies with an ADP (n = 198): market **0.5170** Spearman / **0.3004** R²
against history's 0.2833 / 0.1139. ADP's unique contribution concentrates
where the player has no history to model at all.

**Caveat that matters for interpretation:** ADP incorporates offseason
information — injuries, depth charts, holdouts, scheme changes — that lagged
stats structurally cannot contain. This is "recent versus stale information",
not "crowd wisdom beats statistics". It is also exactly why the rookie gap is so
large.

---

## 2. Is a profile model better *calibrated* than ADP buckets?

`python -m fantasyprep.research.distribution_benchmark`

Given experiment 1, chasing a better median is chasing an exhausted margin. What
ADP does not give you is the **shape**. Scored on 1,656 held-out player-seasons.

| arm | coverage err | CRPS | pinball | median MAE | P10–P90 width |
|---|---|---|---|---|---|
| adp_bucket *(incumbent)* | 0.0149 | 35.716 | 17.858 | 62.36 | 189.4 |
| adp_prior_bucket | 0.0152 | 35.728 | 17.864 | 62.35 | 189.3 |
| profile_quantile | 0.0154 | **34.551** | **17.275** | **60.47** | 187.0 |

### Finding: the incumbent is already well calibrated

Mean absolute coverage error of **1.5 percentage points**. Being an empirical
distribution, it is calibrated more or less by construction. That is a genuine
validation of the current design, and it sets a hard bar rather than a strawman.

The profile model wins **modestly**: CRPS −3.3%, median MAE −3.0%, at equal
calibration and slightly *tighter* intervals. Sharper at the same honesty — the
right kind of win, but not the step change the "vNext" framing assumed.

On rookies it wins decisively: CRPS −8.8%, median MAE −10.1%, coverage error
roughly halved.

### Finding: two-axis empirical bucketing is infeasible here

Arm B is identical to arm A to three decimals. Measured, not inferred — how
often each bucket level actually has ≥20 samples:

| bucket resolved | share |
|---|---|
| ADP × prior-finish (the 2D cell) | **4.0%** |
| ADP only | 65.8% |
| position only | 30.2% |

96% of the time arm B falls back to exactly what arm A does. **Conditioning on
anything beyond ADP rank requires a model, not finer buckets.**

### Actionable calibration defect

Both bucket arms **under-cover the upside**: P90 coverage is 0.87 against a
nominal 0.90, P75 is 0.73 against 0.75. Real outcomes beat the stated ceiling
about 13% of the time instead of 10%. The current system understates upside.

---

## 3. Is *risk* predictable when market rank is held fixed?

`python -m fantasyprep.research.residual_analysis`

Residual = actual − leakage-safe ADP-bucket expectation. n = 1,656, mean −0.9,
stdev 79.0.

Average calibration can hide the thing a draft engine needs. A system can be
perfectly calibrated across all receivers while being systematically
overconfident about 30-year-olds and underconfident about second-year
breakouts — the errors cancel in aggregate and mislead on every individual pick.

### Level: no exploitable bias

Every feature association is weak (largest |Spearman| = 0.15, `adp_stdev`). The
market is efficient about *where* to draft a player. Worth establishing before
anyone tries to beat it on central estimates.

### Dispersion: real, but modest and early only

Fitting |residual| on the preseason profile, then splitting each ADP tier at its
own median predicted risk:

| ADP tier | stdev high-risk | stdev low-risk |
|---|---|---|
| 1–6 | 100.0 | 86.7 |
| 7–12 | 86.5 | 77.2 |
| 13–24 | 87.4 | 73.2 |
| 25–48 | 68.1 | 72.0 ← reversed |
| 49+ | 69.4 | 70.9 ← flat |

A 15% gap on ~120 players per cell is exactly the kind of number that evaporates
under resampling, so it was bootstrapped. Pooled across the top 24 with the
split taken *within* each tier:

**+13.0 points of standard deviation, 95% CI [+3.6, +22.4] — excludes zero.**

No single tier clears zero on its own. That is stated plainly rather than
papered over by quoting a per-tier point estimate.

Past rank 24 the signal is gone and slightly negative. That is a real boundary,
not a reason to keep hunting: deep picks are lottery tickets where everyone is
equally uncertain, while early picks have rich histories to condition on. It
also happens to be the useful place for it, since the first few rounds decide a
season.

**Honest summary:** risk is separable from rank, but weakly (overall Spearman
+0.09) and only early. Enough to justify carrying a per-player variance term
into the Monte Carlo; not enough to claim a large edge.

---

## 4. Do rookies deserve their own model?

`python -m fantasyprep.research.rookie_model`

The natural conclusion from experiments 1 and 2, and argued explicitly in
review: `prev_fantasy_points = NaN` is a different *information state*, not
missing data. Tested rather than assumed. Scored on 198 held-out rookies.

| arm | coverage err | CRPS | pinball | median MAE |
|---|---|---|---|---|
| adp_bucket | 0.0267 | 36.063 | 18.031 | 64.08 |
| **shared_profile** | **0.0152** | **32.902** | **16.451** | **57.59** |
| rookie_specialist | 0.0640 | 35.361 | 17.681 | 63.09 |

**The specialist is worse than the shared model on every metric**, with roughly
4× the coverage error, and barely better than the incumbent it was meant to
displace. Same ordering at RB and WR separately.

The cause is sample size, not feature design. A rookie-only model trains on
62–236 rows depending on season; the shared model learns the ADP-to-outcome
relationship from thousands of player-seasons and transfers it. Specialising
buys a better-matched feature space and pays an order of magnitude in data.

---

## What this means for the roadmap

**Two independent experiments say the same thing.** 2D bucketing failed because
its cells were empty 96% of the time; rookie specialisation failed because 150
rows cannot support a model. Anything that fragments this sample loses.

That reorders the priorities:

1. **Expand the sample before elaborating the model.** The 1999–2009 seasons can
   train the history component *today* with no ADP at all. That is worth more
   than a better architecture, and it is already sitting in
   `player_season_features.parquet`.
2. **Carry a variance term, not a better median.** The median is the market's
   job and it does it well. The early-round dispersion signal is the part worth
   feeding into the Monte Carlo.
3. **Fix the upside under-coverage** — a concrete, measured defect in the
   distributions the simulator samples from today.
4. **Pre-2010 ADP acquisition stays demoted.** Its value is concentrated in
   rookie modeling for those years, and the rookie experiment just showed that
   the shared model — which needs no extra ADP — is the better rookie model
   anyway.

**The gains available here are real but incremental** — 3% CRPS overall, 9% on
rookies, a modest variance signal in the first two rounds. Anyone planning the
next phase should size the effort against that, not against the hoped-for step
change.

---

# Part II: from "what predicts outcomes" to "what the engine can use"

Part I established that the market prices the median and the incumbent buckets
are already calibrated. Part II asks the follow-up question that actually
governs the roadmap: **can the draft engine consume anything better, and what is
wrong with the distributions it samples from today?**

The answers reordered the plan.

---

## 5. The draft objective is structurally variance-seeking

`python -m fantasyprep.research.variance_sensitivity`

`roster.starting_lineup_value` sorts a roster and takes the top N per slot. That
is a **selection operator, and it is convex** — so by Jensen's inequality,
raising a player's variance while holding his mean fixed *raises* expected
starting-lineup value. The lineup captures upside; the bench truncates downside.
Nobody chose this. It falls out of the roster math.

Today it is invisible, because every player in a rank bucket shares one
distribution, so the effect is uniform and cancels between positions. Introduce
genuine player-specific variance and it stops cancelling.

Measured with a mean-preserving spread on WR only (bucket mean held at
**264.49 → 264.49 exactly**; stdev 83.3 → 166.5), 2024 pool, slot 5, common seed:

| spread | top rec | WR | RB |
|---|---|---|---|
| 1.00 | **RB** | 1708.2 | 1708.3 |
| 1.25 | **WR** | 1737.9 | 1729.9 |
| 1.50 | **WR** | 1769.2 | 1752.8 |
| 2.00 | **WR** | 1834.3 | 1801.2 |

**A 1.25× spread — mean unchanged to the cent — flips the top recommendation and
buys +29.7 points.** At 2× it is +126.1.

The residual analysis found a *real* dispersion difference of ~13 stdev points.
**The artifact is several times larger than the signal.** Feeding real variance
into this objective would swamp the signal, and the resulting backtest would read
as "variance doesn't help" when the truth is "the objective cannot use it."

---

## 6. Realistic weekly lineups are variance-*averse* — the sign flips

`python -m fantasyprep.research.lineup_hindsight`

The suspected root cause of #5 is not the objective but the **scoring**: one
season total per player, then a lineup chosen with perfect hindsight. Three
scorers on the same 200 random rosters and the same real 2023 weekly data, with
a mean-preserving spread applied to WR *weekly* outcomes that leaves each
player's season total exactly unchanged:

| scorer | ×1.25 | ×1.5 | ×2.0 | mean roster score |
|---|---|---|---|---|
| season_hindsight *(what the engine does)* | +0.0 | +0.0 | +0.0 | 1478.0 |
| weekly_hindsight | +15.6 | +34.0 | **+76.6** | 1690.1 |
| weekly_realistic | −3.9 | −9.0 | **−21.8** | 1529.5 |

`season_hindsight` reading exactly +0.0 is the sanity check that the spread is
genuinely total-preserving.

**The finding is the sign flip.** Hindsight weekly lineups reward volatility
heavily — you can start a player precisely on his boom weeks. Realistic weekly
lineups (rank by what a player had averaged over weeks *already played*, then
score what those starters actually did) **penalise** it, because nobody can
predict which weeks boom, so a volatile player gets started on his busts and
benched on his booms.

**Boom/bust players are systematically overvalued by any hindsight-based scorer,
and the current engine is hindsight-based.**

Second finding: the current scorer is wrong in two directions at once. Its mean
roster score (1478.0) is *below* realistic weekly management (1529.5) — a premium
of **−51.4** — because season totals ignore that a manager rotates players across
a season and extracts value from more than N of them. It simultaneously
understates roster value and is blind to weekly consistency.

---

## 7. Fixing the upside under-coverage

`python -m fantasyprep.research.calibration`

Experiment 2 found the buckets under-cover the upside. Diagnosed by segment, the
defect is badly non-uniform and worst where it matters most:

| ADP tier | p50 | p75 | p90 | p95 |
|---|---|---|---|---|
| 1–6 | 0.46 | **0.64** | **0.80** | **0.85** |
| 49+ | 0.57 | 0.81 | 0.91 | 0.95 |

An elite player beats his bucket's stated P90 about **20%** of the time instead
of 10%.

Four corrections tried, all via PIT recalibration (fit `G`, the empirical CDF of
the probability integral transform, on strictly-prior seasons; read the raw
distribution at `G⁻¹(q)`):

| arm | coverage err | CRPS | note |
|---|---|---|---|
| incumbent | 0.0149 | 35.716 | |
| global | 0.0172 | 35.879 | fixes the tail but **shifts the median** (.50→.53) |
| per_tier | 0.0349 | 36.089 | **fragments the sample** — worst of all |
| tail_only | **0.0097** | 35.746 | median anchored; best marginal calibration |
| smooth | 0.0206 | **35.359** | best CRPS, and best where it matters |

`per_tier` failing is the **third consecutive instance** of the fragmentation
lesson, after 2D bucketing and rookie specialisation.

**`smooth` is the synthesis.** Kernel-weight the recalibration by distance in ADP
rank, so every training observation contributes to every estimate and no cell can
go empty, while the correction still varies continuously with rank —
*conditioning without fragmenting*. On the target segment:

| tier 1–6 | p75 | p90 | p95 | coverage err | CRPS |
|---|---|---|---|---|---|
| incumbent | 0.64 | 0.80 | 0.85 | 0.0583 | 43.751 |
| **smooth** | **0.71** | **0.87** | **0.93** | **0.0311** | **42.567** |

Coverage error nearly halved on the segment that decides drafts, CRPS better in
essentially every tier, median held at 0.50 exactly.

**Not yet wired into the engine.** Changing the distributions the simulator
samples from deserves its own A/B like every other engine change here.

---

## Revised roadmap

The Part II results reorder what Part I suggested:

1. **Fix the scorer before the variance model.** Realistic weekly lineup scoring
   is now a *prerequisite*, not a later enhancement — building a variance model
   on top of a hindsight scorer would push recommendations toward volatile
   players when reality penalises them.
2. **Ship the `smooth` recalibration**, behind its own A/B.
3. **Then** the variance model, which finally has a channel that can carry it.
4. **Decision-value testing throughout**, not at the end. A 3% CRPS gain means
   nothing until it changes a pick.

**The fragmentation lesson now has four instances** (2D buckets, rookie
specialisation, per-tier recalibration — and the kernel-weighted fix that finally
works). It should be treated as a standing constraint on this project's
modeling, not a series of coincidences.
