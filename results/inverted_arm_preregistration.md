# Pre-registration — the short signal, inverted, split by regime

Written and committed **before** `inverted_arm.py` exists.

## What prompted this

`drift_null` run 1 measured the short signal as **worse than random entry**:

| | real | random null | null 95% |
|---|---|---|---|
| SHORTS excess | −9.5pp | −4.6pp | [−7.0, −1.8] |
| SHORTS mean R | −0.341 | −0.210 | [−0.315, −0.059] |

All 200 random draws beat it. A signal reliably worse than random carries
information with the sign inverted: the bars where the short signal fires are
bars from which price rises *more* than random bars do, by roughly 4.9pp of hit
rate beyond what drift alone supplies.

If that holds, **entering LONG at the bars where the short signal fires**
should beat random long entries — and random long entries are the best thing
measured anywhere in this project (+0.149 R, null median).

## The obvious objection, which is the point of the regime split

"Buy the pullback in an uptrend" is close to tautological over 2016–2026,
because there was an uptrend. The short signal fires on RSI under 40, price
under both EMAs, MACD bearish — in a bull market that is a dip, and dips got
bought. In a bear market the same setup is a falling knife.

So the headline number is not the test. **The test is whether the effect
survives when the market is not rising.**

## The three arms

All entered LONG, all on the same frames, same 1.0×ATR stop, same 3.0×ATR
target, same exit engine, same slippage and commission:

1. **REAL LONG** — the bars where the Bullish signal fires. Already known to
   add nothing over random.
2. **INVERTED** — the bars where the **Bearish** signal fires, entered long.
3. **RANDOM** — uniformly random bars, 200 draws, reusing
   `drift_null.random_entries` unchanged.

Metric is the same as drift_null's: hit rate minus each trade's own
1/(1+R:R) driftless baseline, plus mean R. Verdicts reuse
`drift_null.compare`, so the bar is the one already fixed and falsified.

## The regime split

Each trade is labelled by SPY's regime at its **signal bar** —
`backtest.build_regime_series`, price against its own 200-SMA, Bull / Bear /
Neutral. This is the repo's existing definition and SPY is already spent, so
no new data is consumed.

**The null is computed per regime**, not once overall. 2016–2026 is mostly
Bull, so a null drawn across all bars is a mostly-Bull null; comparing a
regime-clustered arm against it would confound regime mix with effect. Within
each regime, the inverted arm is compared against random entries **from that
same regime**.

## The pass bar, decided now

- **REGIME-INDEPENDENT EFFECT** — the inverted arm exceeds the random null's
  97.5th percentile in Bull **and** in Bear, on excess. This is the only result
  that would justify spending tranche C.
- **BULL-ONLY EFFECT** — clears in Bull, fails in Bear. Recorded as a
  description of 2016–2026, not a strategy. Does not go to tranche C.
- **NOTHING** — fails in Bull.
- **UNDERPOWERED IN BEAR** — fewer than 100 inverted trades in the Bear regime,
  or fewer than 20 usable null draws there. Then the Bear cell reports its n
  and refuses a verdict rather than producing one from thirty trades. An
  underpowered cell is **not** evidence of a regime-independent effect and must
  never be read as one.

A disagreement between Bull and Bear is the finding, not a problem to resolve
toward whichever is larger.

## Predictions

- **The inverted arm beats random in the Bull regime.** Near-tautological: it
  is buying dips in a rising market.
- **It does not survive in the Bear regime**, and I expect the Bear cell to be
  either negative or underpowered.
- **Expected verdict: BULL-ONLY EFFECT** — the decade, not an edge.
- I also expect the REAL LONG arm to add nothing in both regimes, matching
  drift_null.

If the Bear cell comes back underpowered, the honest statement is that this
question cannot be answered with twenty mega-cap tickers over one decade, and
no amount of further slicing changes that.

## What a positive result would license

In-sample on twenty spent tickers. A regime-independent effect would be a
hypothesis worth taking to tranche C — the first in this project with a
mechanism behind it — not a finding, and not a reason to trade it.
