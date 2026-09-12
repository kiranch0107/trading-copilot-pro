# Pre-registration: is there anything beyond `side`?

**Written 2026-09-12, BEFORE `conditional_test.py` exists.** Successor to
`results/feature_sweep_run1.md`, which passed its bar and disclosed two things
the bar had not asked for.

## What run 1 left unresolved

Six features cleared Holm. Five are proxies for *"this setup is bullish"* —
`side`, `rsi`, `ema20_dist`, `macd_hist`, `ema_spread` — and `side` itself reads
**CALL −3.1% vs PUT −20.4%**. Holm corrects for multiplicity, not correlation,
and run 1 tested every feature marginally and none conditionally, so **by
construction it could not tell six findings from one finding seen six ways.**

`atr_pct` was the exception: low volatility at entry, ρ −0.90, a different axis,
with `rv` and `prem_pct` agreeing without clearing Holm.

Two questions remain, and exactly two:

1. Does `rsi` — the best of the bullish cluster — separate **within CALLs**, or
   was it `side` wearing a different hat?
2. Does `atr_pct` survive conditioning, making it a genuine second axis?

## The design was chosen on power, before any data was seen

Within the CALL stratum, n = 897.

| design | detectable |
|---|---:|
| re-sweep all 9 remaining features, quintiles of 179, Holm across 9 | **28.6%** |
| two pre-specified claims, top tercile vs rest, Holm across 2 | **16.3%** |

**The first design is rejected.** The largest effect run 1 found was ~23%, so
re-sweeping nine features inside a smaller sample could not see what the full
sample barely saw. A test that cannot detect the effect it exists to check is
theatre.

**This is therefore not a sweep.** `rsi` and `atr_pct` are named in advance —
they are the two claims run 1 raised — and Holm is applied across exactly those
two. Nothing else is tested, and no feature may be substituted after seeing the
result.

Terciles, not quintiles, to keep the groups large: ~299 vs ~598.

## The bar

1. **STRATUM SIZE.** The CALL stratum must hold at least 500 trades. Below that
   the comparison is void, not weak.
2. **SEPARATION.** At least one of `rsi`, `atr_pct` must separate its top tercile
   from the rest, surviving Holm–Bonferroni across the two at α = 0.05.
3. **DOSE-RESPONSE.** That feature must be strictly monotone across the three
   terciles. With three buckets Spearman ρ can only take ±1.0 or ±0.5, so
   requiring |ρ| ≥ 0.6 means **ρ = ±1.0 exactly** — stated plainly because a
   threshold that quietly admits only one value should not look like a range.
4. **THE CI CLAUSE — the fix for run 1's hole.** The filtered subset's
   expectancy must clear zero **with its 95% confidence interval**, not as a
   point estimate. Run 1's clause 4 asked only for a positive number and passed
   a subset whose interval was [−6.07, +11.39].
5. **DECISION.** Only a feature clearing 2, 3 **and** 4 licenses anything, and
   what it licenses is forward paper-tracking. Nothing here can be confirmed.

## Clause 4 is a high bar and is expected to fail

To clear zero with its interval, a subset needs:

| subset size | expectancy required |
|---:|---:|
| 179 | +10.96% |
| 299 | **+8.48%** |
| 449 | +6.92% |

**Run 1's best subset reached +2.66%.** Clause 4 is roughly 3× above anything
observed so far, and it is set there because that is what "positive" actually
requires at this sample size. A clause that a real result cannot reach is a
problem; a clause that *this* result cannot reach is the point.

## Prediction, recorded before the run

**Neither feature separates within CALLs at Holm-corrected significance.** `rsi`
will shrink sharply once `side` is held fixed, because they are close to the same
variable. `atr_pct` is the live possibility — it is on a different axis and three
independent measures agreed — but at a detectable of 16.3% I expect it to land
somewhere real and unprovable.

**I was wrong predicting run 1's outcome**, so this prediction is recorded for
calibration rather than offered as an expectation to plan around.

## What a NULL settles

If neither separates: **run 1 found `side` six times and nothing else**, the
long/short asymmetry already in `longshort_split.md` and already diagnosed there
as a decaying discovery artifact. That closes the entry-feature line, and the
correct response is to stop rather than to condition on something else — each
additional stratum costs the sample that makes the next test possible.

## Also reported, and deliberately NOT tested

The PUT stratum (n ≈ 501) is printed descriptively for symmetry. It is **not**
part of the Holm family and no clause reads it. Adding it would be two more tests
bought with the power this design does not have.
