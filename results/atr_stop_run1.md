# ATR stop width — run 1

**Date:** 2026-09-11
**Pre-registration:** `results/atr_stop_preregistration.md` (committed `8dffd81`,
before `atr_stop_test.py` existed)
**Code:** `atr_stop_test.py` at `d36a145`
**Command:**

```bash
python atr_stop_test.py --tickers GOOGL,AVGO,AMD,NFLX,CRM,ADBE,QCOM,MU,ORCL,NOW,PANW,LRCX --years 10
```

## Verdict: NOT ESTABLISHED

Clauses 3 and 4 cleared. Clauses 1, 2 and 5 failed.

## The control reproduces the record exactly

| | trades | avg R | same-bar % |
|---|---:|---:|---:|
| record (`record_recheck.RECORD`, 12t/10y OOS) | 1386 | −0.048 | 17.2 |
| this run, 1.0× arm | 1386 | −0.048 | 17.2 |

Three for three. The engine has not drifted, so everything below is
interpretable.

## Arms

| stop | trades | exp R | win % | same-bar % | payoff | breakeven WR % | win − BE |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.00 | 1386 | −0.048 | 31.3 | 17.2 | 2.04 | 32.9 | −1.6 |
| 1.25 | 1393 | −0.029 | 36.4 | 10.8 | 1.67 | 37.5 | −1.1 |
| 1.50 | 1397 | −0.051 | 39.5 | 7.6 | 1.40 | 41.7 | −2.2 |
| 2.00 | 1368 | −0.012 | 45.3 | 4.8 | 1.18 | 45.9 | −0.6 |

## Paired vs the 1.0× baseline

| stop | pairs | identical | differing | mean ΔR | 95% CI | detectable | p |
|---:|---:|---:|---:|---:|---|---:|---:|
| 1.25 | 1376 | 20 | 1356 | +0.017 | [−0.034, +0.068] | 0.073 | 0.5178 |
| 1.50 | 1370 | 20 | 1350 | −0.000 | [−0.061, +0.060] | 0.087 | 0.9928 |
| 2.00 | 1259 | 19 | 1240 | +0.016 | [−0.062, +0.093] | 0.111 | 0.6929 |

## What actually happened

**The mechanism's predictions came true, and the outcome did not move.** This is
the important sentence. The hypothesis was that a 1× ATR stop sits inside the
instrument's daily noise band, so trades are killed by ordinary range rather than
by the thesis failing. Both of its direct, monotone consequences occurred, which
is why clauses 3 and 4 cleared:

- same-bar death rate **17.2% → 4.8%**, a 72% reduction
- win rate **31.3% → 45.3%**, up 14 percentage points

And expectancy did not improve by a measurable amount at any width. The best
paired effect is **+0.017 R with a CI of [−0.034, +0.068]** against a detectable
threshold of 0.073 R.

BACKLOG item 4 said that group "is the **entire** negative expectancy", and
arithmetically it was: `0.172 × (−0.770) + 0.828 × (+0.101) = −0.048`. That
identity is true and it is not a cause. Removing 72% of those deaths bought
nothing, because payoff fell by exactly as much as win rate rose.

## The invariant that explains it

Win rate minus the payoff's own breakeven, at each width: **−1.6, −1.1, −2.2,
−0.6** points. Converted back to R at each width's payoff, that gap reproduces
each arm's expectancy to the third decimal (−0.049, −0.029, −0.053, −0.013).

The signal lands just under fair odds at every stop width. Widening the stop
slides you along an indifference curve — it changes win rate and payoff in
offsetting proportion and leaves the shortfall where it was. **The stop is not
mis-calibrated. There is nothing for it to be mis-calibrated against.**

The four gaps are within each other's noise, so "constant" is consistent with
this data rather than demonstrated by it. The claim being made is the weaker,
sufficient one: no width closes the gap.

## Why the pairing earned its cost

At 2.0× the naive arm-level comparison reads **+0.036 R**; the paired estimate is
**+0.016 R**. The naive figure overstates by a factor of 2.2, and the reason is
visible in the pair counts:

| stop | baseline signals unmatched | share |
|---:|---:|---:|
| 1.25 | 10 | 0.7% |
| 1.50 | 16 | 1.2% |
| 2.00 | 127 | **9.2%** |

A wider stop changes when each trade exits, and with `cooldown_bars = 3` that
changes which *subsequent* signals the arm is free to take at all. The arms are
not parallel universes over one fixed set of setups; by 2.0× nearly a tenth of
the baseline's signals have no counterpart. Without pairing, half of the apparent
2.0× improvement would have been that reshuffling rather than the stop.

## Where this test is weak, stated plainly

**The pairing did not do what the design assumed.** The pre-registration expected
many trades to hit neither stop and contribute Δ = 0, concentrating variance in
the pairs that differ. In fact only **20 of ~1390 pairs were identical** —
changing the stop changes essentially every trade's outcome. Paired-delta sd came
out ≈ 0.97 R. Pairing roughly halved the standard error versus an unpaired
comparison; it did not deliver the order-of-magnitude gain the design hoped for.

That is exactly why clause 2 fired. **The correct reading is UNMEASURABLE, not
"no effect."** A true improvement of, say, +0.03 R would not have been detected
by this test. What the run rules out is a *large* effect, not a small one.

The 1.5× width — the one the item was named after — is the only arm that did
nothing at all (paired mean −0.000, expectancy −0.051, worse than baseline). At a
detectable threshold of 0.087 that is noise, not a finding.

## A selection wrinkle, checked and not acted on

The pre-registered rule picks the candidate by highest paired mean, which selects
**1.25×** (+0.017). But 2.0× has a near-identical paired mean (+0.016) and much
better expectancy (−0.012 vs −0.029). Had the rule selected on expectancy
instead, clause 5 still fails (−0.012 < 0) and the verdict is unchanged. Recorded
because it was checked, not to re-pick after the fact.

## Standing limitation

Both data tranches are spent. Even a PASS here would have been out-of-sample
against nothing. The null is the cheaper outcome: it closes a question rather
than opening one that can never be confirmed.

## What this closes

The stop width is not the reason the signal is flat. Do **not** sweep more widths
hoping one clears — there is no clean data left to confirm anything a sweep
finds, and the indifference-curve result says the search has no destination.

## Prediction scorecard

Recorded before the run, for calibration:

| prediction | outcome |
|---|---|
| clauses 3 and 4 clear | **right** — both monotone, decisively |
| clause 5 fails | **right** — −0.029 R at the selected width |
| clauses 1 and 2 clear | **wrong** — the effect is undetectable, not merely small |
| expectancy lands −0.01 to −0.02 R | **wrong** at the selected width (−0.029); the 2.0× arm did land at −0.012 |
| ~3,400 trades | **wrong** — 1386. That figure belongs to the RVOL study's different 12-ticker mix, recalled from memory instead of read from `record_recheck.RECORD` |
