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

---

# AMENDMENT — after run 1

Run 1 returned `NOTHING` on the pre-registered primary metric (excess), in both
regimes. The pass bar is unchanged and excess remains primary. Two things in
the output were not clean, and both are addressed by measurement rather than by
moving the bar.

## 1. The two R bases disagreed, and only one was reported

| INVERTED — Bull | real | null median | null 95% | p |
|---|---|---|---|---|
| excess | +7.9pp | +4.5pp | [+0.9, +8.2] | 0.035 |
| mean R | **+0.398** | +0.114 | [−0.037, **+0.261**] | **0.005** |

Excess sat a hair inside its band; mean R was well clear. Tripling mean R on a
hit rate only 3.7pp higher is not possible from price action alone when a hit
pays +3R and a stop pays −1R — so the fill is the obvious suspect. The inverted
arm enters where price has been falling (RSI under 40, below both EMAs), and
those next opens often gap further down, landing near the stop, shrinking
`|fill − stop|` and inflating R with no change in what price did.

This is the artifact that voided `setup_cases` run 2, where 37 of 37 winners
had filled toward the stop and re-basing on planned risk collapsed the spread
7.3×.

So every mean R is now reported **twice** — against the fill and against the
stop the signal planned — with the median fill offset beside them. A
disagreement between the two prints as a disagreement. This is a **diagnostic
added after seeing the result**; it does not change the pass bar, and it can
only subtract confidence from a positive, never add it.

## 2. The Bear cell had no power and printed a verdict anyway

330 real trades cleared the floor. The null band was **[−3.7, +12.2]pp** —
sixteen points wide, which nothing could fall outside. The floor checked the
real arm and never looked at the null; Bear regimes are short, so the per-draw
counts there were tiny even though the real arm was large.

A band nothing can escape is not a null result, it is no test at all, and it
printed as `SIGNAL ADDS NOTHING` — indistinguishable from a real null to anyone
reading the summary.

A cell now also requires `MIN_NULL_PER_DRAW = 60` trades in the typical draw.
The rule is extracted into `power_check()` so a test can reach it, and run 1's
actual Bear numbers (330 real, 200 draws, thin per-draw) are a fixture that
must fail.

**This makes the bar stricter and can only turn a verdict into UNDERPOWERED.**
It cannot manufacture a positive.

## Prediction for run 2

- The Bull mean-R advantage **collapses toward the null on the planned basis**.
  If it does, the verdict is a clean `NOTHING` and the entry-signal line is
  finished on spent data.
- The Bear cell reports `UNDERPOWERED` rather than a verdict, which is the
  honest description: twenty mega-cap tickers over one decade do not contain
  enough bear market to settle this.
- If instead the planned basis holds up, that is the first result in this
  project worth taking to tranche C — and it would still be in-sample until it
  goes there.
