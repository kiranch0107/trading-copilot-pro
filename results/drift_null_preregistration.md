# Pre-registration — is the entry signal's edge just market drift?

Written and committed **before** `drift_null.py` exists.

## The observation this tests

`setup_population` run 2 measured every trade's outcome against the
**driftless-walk baseline** — for a random walk with no drift, the chance of
touching +k risk before −1 risk is 1/(1+k), so a trade's target distance alone
predicts its hit rate.

| | observed | driftless baseline | excess | z | p |
|---|---|---|---|---|---|
| LONGS | 35.7% | 31.5% | **+4.2pp** | +3.32 | 9e-04 |
| SHORTS | 22.2% | 31.7% | **−9.5pp** | −6.57 | 5e-11 |

20 of 21 long buckets positive; **21 of 21 short buckets negative**. Both are
far outside noise.

## The two explanations, and why they are hard to tell apart

**Signal.** The entry logic picks bars from which price is more likely to run
up than down, and the excess is what that skill is worth.

**Drift.** 1/(1+k) assumes zero drift. These are twenty mega-cap US equities
over a decade-long bull market, where price drifts up. A long entered at *any*
bar should beat a driftless baseline and a short should fall short of it. One
parameter explains both rows, including their opposite signs.

Nothing measured so far distinguishes them. The flatness is suggestive — the
excess sits between +0.2 and +6.9pp across every long bucket of every reading,
with no reading concentrating it, which is what a constant term looks like and
not what a signal looks like — but suggestive is not measured.

## The test

Enter at **random bars** on the same tickers over the same period, with the
same stop, target, exit rules and costs, and measure the same excess.

If random entries show the same excess, the entry logic contributes nothing and
the +4.2pp is the market. If random entries show materially less, the
difference is what the signal is worth, in percentage points.

## What is held identical, and what is not

**Identical**: the price frames (real and null are computed from the same
`compute()` output per ticker), the exit engine (`backtest.simulate_trade` —
stop-first on ambiguous bars, next-open fill, gapped-fill rejection, max_hold
mark-to-market), slippage and commission, `max_hold`, the minimum spacing
between trades (`cooldown_bars`), the number of trades per ticker per side, and
the side itself — longs are nulled against random longs, shorts against random
shorts.

**Not identical, and stated plainly**: the null builds levels as
entry = close, stop = 1.0 × ATR, target = 3.0 × ATR, so its R:R is always 3.00.
The real signal's structural-stop validation sometimes widens the stop, giving
a spread of R:R. The excess metric subtracts each trade's own 1/(1+R:R), which
is exactly the correction for that difference — but a correction is not the
same as an identical sample, so the run **also reports the comparison
restricted to real trades with R:R within 0.05 of 3.00**, where the geometry
matches exactly and no correction is doing any work.

Random bars are drawn uniformly over the eligible range, without replacement
and respecting the same spacing. Uniform is the point: it is the null of "the
timing carries no information".

## The pass bar, decided now

Per side, with 200 independent random draws:

- **SIGNAL ADDS NOTHING** — the real excess falls inside the null
  distribution's central 95% (2.5th to 97.5th percentile). The entry logic is
  not distinguishable from entering at random.
- **SIGNAL CONTRIBUTES** — the real excess exceeds the null's 97.5th
  percentile. The contribution is the gap, reported in percentage points, with
  the empirical p-value (the fraction of null draws at or above the real value).
- **SIGNAL IS NEGATIVE** — the real excess falls below the null's 2.5th
  percentile. The entry logic is worse than random and should be switched off,
  not tuned.

The same three verdicts are computed on mean R alongside the excess, because a
signal could shift which trades it takes without shifting the hit rate.

The R:R-matched subset is reported but is **not** a separate shot at
significance — it is a robustness check on the main result, and a disagreement
between the two is reported as a disagreement rather than resolved in favour of
whichever is more flattering.

## Predictions

- **LONGS: SIGNAL ADDS NOTHING.** I expect the null's long excess to land near
  +4pp, covering the real +4.2pp comfortably. The flat excess across every
  bucket of every reading is the fingerprint of a constant, and the entry
  readings were a complete null at d ≈ 0.02–0.07 against a detectable d of
  0.153.
- **SHORTS: SIGNAL ADDS NOTHING**, with the null's short excess near −9pp.
- If both hold, the honest summary is that ten years and 2,360 trades produced
  no measurable entry edge, and the strategy's whole result is "be long in a
  bull market".

I have been wrong on the direction of nearly every prediction recorded in this
project. The most recent, "zero of seven survive on longs", was wrong because a
tautology survived. That is why these are written down first.

## What a positive result would and would not license

If the signal does contribute, the contribution is measured **in-sample on
twenty spent tickers**. It would be a hypothesis worth taking to tranche C, not
a finding. Tranche C stays unspent either way until there is something with a
mechanism behind it worth spending on.
