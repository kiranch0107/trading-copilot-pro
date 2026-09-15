# Stage 1 run 2 is VOID — the cases were selected on the fill, not the setup

Run 2 (commit `aee480b`) had real readings for the first time. It still cannot
be read as evidence, for a different reason: the rule that chose which 80
trades to show was selecting on something the setup cannot contain.

## What the selection was doing

`pick()` took the top 2 and bottom 2 trades **by R** for each ticker. R is
measured against the actual fill:

    risk = |fill - stop|,   fill = the OPEN of the bar after the signal

Nothing at the signal bar knows that open. Of the 37 trades in run 2 that
exited at their target:

- **37 filled toward the stop. Zero filled at or better than plan.**
- The median winner kept 56% of its planned risk distance.
- The largest kept 21%.

| case | planned R:R | realised R | risk surviving |
|---|---|---|---|
| AVGO | 3.00 | +18.12 | 21% |
| ORCL | 3.00 | +15.83 | 24% |
| MU | 3.00 | +12.52 | 30% |
| QCOM | 3.00 | +11.52 | 32% |

A shrinking denominator is what makes an 18 R trade, not a better setup.
Measured against the stop the signal actually planned, those same winners ran
**+1.67 to +3.79 R** — sd 3.35 becomes sd 0.46, a 7.3x collapse. Nearly every
winner did the same thing: hit its 3 R target.

## Why it is asymmetric, not merely noisy

A loser exits **at** the stop, so it books −1.00 R however it filled. Fill luck
cannot show up on the losing side at all. It can only ever inflate winners. So
"top 2 by R" is close to a pure ranking of fill accidents, and the losers it
was compared against carry no such ranking.

This is not a bug in `backtest.py`. The accounting is right — a smaller risk
per share does buy more shares. It was a bug in what stage 1 chose to look at.

## Three readings that could not have said anything

- **`gates passed`: 4.00 vs 4.00.** `evaluate_signal()` returns nothing unless
  every gate passes, so every case that reaches this output is 4/4 by
  construction. Three of the four gates are disabled anyway — every card reads
  "Weekly confirmation disabled", "Earnings check not supplied", "Regime filter
  disabled". Only ADX is live. A constant is not a null result.
- **`hold bars`: 7.33 vs 0.12**, perfect separation, worthless. It is an
  outcome: a stop-out is fast by construction and a runner is slow.
- **`stop %` alongside `ATR %`.** 54 of 56 longs had them equal to within 1%,
  because the stop IS 1.0 x ATR. One reading printed twice. Two rows moving
  together is not two pieces of evidence.

## One reading that was two opposite things averaged

Pooled RSI showed a +2.27 gap. Bullish cases run RSI 57.7–75.0; bearish run
25.2–47.4. The pooled mean averages across the split and lands in the middle
regardless of what either side does. `price-EMA20 ATR` is the same story with
the sign flipped: longs +1.0 to +3.3, shorts −4.1 to −0.0.

A pooled gap on a direction-dependent reading measures the long/short mix of
the sample, not the setup. This is the `side` problem the feature sweep already
found, arriving by a different route.

## What changed

- **`pick()` selects by OUTCOME** — reached its target vs hit its stop — not by
  R magnitude. Timeouts are neither and are excluded rather than assigned to a
  side (3 of 80 in run 2).
- **Cases are spread evenly across the period** rather than taken from one end,
  so a regime that only existed in one year cannot supply every case.
- **`fill_stats()`** reports R against the planned stop alongside R against the
  fill, plus the fill offset itself, so the part of R that was decided after
  the signal bar is visible on every case instead of hidden inside it.
- **Every comparison is split by side** before any mean is taken.
- **Constant readings are reported as CONSTANT**, not as a +0.00 gap. "No
  difference" and "no possible difference" are different claims.
- **Collinearity between `stop %` and `ATR %` is measured and printed.**

## Falsified

Ten breaks, each confirmed to turn the selftest red:

| break | caught |
|---|:---:|
| `pick` ranks by R again (the original defect) | yes |
| timeouts counted as wins | yes |
| `_spread` takes the first n (era-biased) | yes |
| `_spread` drops the newest end | yes |
| `fill_offset` sign flipped | yes |
| `r_planned` inverted | yes |
| `r_planned` fabricates a zero for missing levels | yes |
| gaps pooled instead of split by side | yes |
| a constant reading printed as a +0.00 gap | yes |
| the matrix's self-caveat dropped | yes |

The side-split guard was dead on the first pass: it asserted the headings
"LONGS" and "SHORTS" both appeared, and pooling every case under LONGS while
passing an empty list to SHORTS prints both. It now reads the numbers inside
each section.

## What run 2 suggested, pending a clean re-run

Longs only, 29 reached target / 27 stopped, win rate rather than mean R
because mean R carries the fill contamination:

| ATR% bucket | range | reached target |
|---|---|---|
| low | 0.93–1.96 | 7/18 |
| mid | 2.00–2.66 | 9/18 |
| high | 2.69–7.35 | 13/20 |

Monotone. It is the only candidate worth carrying into stage 2, and it is not
carried yet — it was measured under the broken selection and has to survive the
clean one first.

**The R:R row is a trap and is recorded here so it is not rediscovered.** At the
3.0 cap 24/36 won; below the cap 5/20. That is circular: a trade whose target
sits 1.2 R away can pay at most 1.2 R, so it can essentially never be a top-2
winner by R, while it can still book −1 R and land in the bottom 2. The
selection created the finding.

## Status

Stage 1 must be re-run a third time. Nothing from run 1 or run 2 has been
carried forward as a finding.
