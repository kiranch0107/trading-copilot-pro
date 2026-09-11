# PRE-REGISTRATION — RVOL confirmation on tranches A + B

**Written 2026-09-11, and committed BEFORE any tranche data is fetched.**
Everything below is fixed. Nothing here may be changed after the result is seen;
that is the entire function of this document existing in git with a timestamp
that precedes the run.

## What is being spent, and why both

`results/rvol_run1.md` found the project's first passing dose-response
(ρ = +0.90) on 3,406 contaminated trades, failing every clause on **power
rather than sign**. Clause 4 — the live `volume_mult = 1.2` gate — missed its
detection threshold by 0.013 R.

| spend | tickers | est. trades at RVOL ≥ 1.2 | margin |
|---|---|---|---|
| A alone | 16 | ~1,196 | +13% |
| **A + B** | **48** | **~3,588** | **+238%** |

A alone carries a 13% margin **on an estimate**. If these names fire fewer
signals than the mega-cap set did, the confirmation lands underpowered and the
tranche is burned for an inconclusive answer. Both are spent so that one run
settles it either way.

**A sequential spend was explicitly rejected.** "Spend A, and if inconclusive
spend B" chooses the sample size after seeing the result — optional stopping,
the same class of error as picking the ADX threshold after the sweep.

## The universe, fixed

```
Tranche A (16): TXN INTC AMAT KLAC SNPS CDNS INTU IBM CSCO DIS HD LOW NKE
                SBUX MCD COST
Tranche B (32): TGT WMT PG KO PEP JPM BAC GS MS V MA AXP BLK CAT DE HON GE BA
                UNP UPS UNH JNJ LLY ABBV MRK PFE TMO ABT XOM CVX COP SLB
```

**Zero overlap with the 12 tickers the effect was measured on** (TSLA NVDA AAPL
MSFT AMZN META SPY GOOGL AMD NFLX ORCL CRM). This is a genuinely out-of-sample
test across a different sector mix — semis, software, consumer, retail,
financials, industrials, healthcare and energy — not more of the same names.

## The test, fixed

Identical code and identical bar to `rvol_retest.py`. **No new module, no new
parameters, no re-tuning.** Only the universe changes.

- **years**: 10
- **adx_min**: 0 (unfiltered baseline, as in the original run)
- **buckets**: 5, equal-count
- **gate**: 1.2 — the value already in `signal_core.DEFAULTS.volume_mult`, not
  re-chosen
- **command**: `python rvol_retest.py --tickers <A+B> --years 10`

## The bar, fixed

Unchanged from `rvol_retest.py`:

1. at least one bucket significant after **Holm** correction across 5 buckets
2. that bucket clears its own power threshold **and** the 150-trade floor
3. **dose-response**: Spearman ρ ≥ 0.6
4. **the live gate**: RVOL ≥ 1.2 out-earns RVOL < 1.2 by more than the pair can
   detect, with the 95% CI clearing zero

## What each outcome means — decided now

- **PASS (all four).** The RVOL effect replicates out-of-sample on a different
  sector mix. `volume_mult = 1.2` is validated, and the case for raising the
  gate — or for using RVOL as a first-class signal input — becomes testable
  work rather than speculation.
- **FAIL on sign or dose-response.** The original ρ = +0.90 was a property of
  mega-cap tech over 2016–2026, not of the mechanism. Treated as refuted.
- **FAIL on power despite correct sign and grading.** The effect is real but
  smaller than this project can resolve with all the data it has. Recorded as
  such, and the search ends there: **there is no third tranche.**

## Binding conditions

- **This is the last held-out data.** After this run, no clean tranche exists.
  Any future hypothesis must be judged on contaminated data or not at all.
- **The result is final for RVOL.** Whatever comes back is written to
  `results/rvol_confirm_run1.md` and the question is closed — no re-cutting by
  sector, no dropping names, no second threshold. If tranche A's consumer names
  behave differently from the semis, that is a finding to record, not a reason
  to split the sample and re-test.
- **A positive result still does not license trading.** It licenses further
  work. `vrp_check` was measured, reproduced, and still did not survive contact
  with an implementation.
