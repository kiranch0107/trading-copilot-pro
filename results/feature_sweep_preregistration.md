# Pre-registration: does any entry-time feature predict TP vs SL?

**Written 2026-09-12, BEFORE `feature_sweep.py` exists.** Implemented clause for
clause in that module's `verdict()`.

## The question

For each signal the logic fires, the option is walked to **TP +100%** or
**SL −50%** (or TIME / THESIS). Is there anything observable **at entry** that
separates the trades that reach TP from those that don't?

## The bar this has to clear, computed before any feature was chosen

Measured: `OPT_WIN_RATE = 24.9%` against a breakeven of **33.3%** — an 8.4-point
shortfall, −12.6% of premium per trade. For a filter to close that:

| keep | dropped bucket must win | |
|---|---:|---|
| 80% | −15.5% | impossible |
| 60% | 9.8% | |
| 50% | 14.8% | |

**A usable filter must discard at least 40% of signals, and the discarded bucket
must win at well under half the base rate — roughly 15 win-rate points of
separation.** A filter that shifts win rate 3–4 points cannot work here, whatever
its p-value.

## Power, and the one favourable property of this test

n ≈ 1398, five equal-count buckets of ~279, `pnl_pct` sd ≈ 70%:

| comparison | detectable | in win-rate points |
|---|---:|---:|
| top vs bottom bucket, uncorrected | 16.6% | 11.1 |
| after Holm across 5 features | 20.3% | 13.5 |
| **after Holm across 10 features** | **21.6%** | **14.4** |

Against a requirement of ~15 points, the margin is **1.04×**.

That is thin, and it is also the design's one genuinely good property: **this
sweep can only detect effects big enough to be useful.** Anything it misses would
not have closed the gap anyway. A null here is therefore decisive in the way that
matters, even though it is not decisive about small effects.

**Every feature added costs power.** The ten below were chosen for having a
stated mechanism, not for being available.

## The features, and why each is here

| feature | mechanism |
|---|---|
| `side` | CALL vs PUT. The largest documented asymmetry in the record: share-level short side −0.287 R, CI [−0.432, −0.141] |
| `adx` | trend strength. Refuted at share level; options price *speed*, which share R cannot see |
| `rsi` | extension. A stretched entry may mean the move is done |
| `atr_pct` | ATR / Close. Volatility regime relative to price |
| `rvol` | Volume / VOL_AVG20. Refuted at share level; included for the same speed argument as ADX |
| `ema20_dist` | (Close − EMA20) / ATR. How far the entry is from the mean, in the instrument's own units |
| `ema_spread` | (EMA20 − EMA50) / Close. Trend alignment strength |
| `macd_hist` | (MACD − Signal) / Close. Momentum impulse at entry |
| `rv` | realised vol at entry. Drives the price paid, since `iv = rv × iv_mult` |
| `prem_pct` | prem0 / spot0. How expensive the contract is relative to spot, hence how big a move TP requires |

`side` is binary and gets two groups; the rest get equal-count quintiles.

## The test

For each feature:

- **PRIMARY** — Welch t-test on `pnl_pct`, top bucket vs bottom bucket.
- **DOSE-RESPONSE** — Spearman ρ between bucket index and bucket expectancy.

Holm–Bonferroni is applied to the **primary p-values across features only**.
Dose-response is a **falsifiability clause, not a second shot on goal**: it can
only sink a feature, never rescue one.

## The bar

1. **SAMPLE FLOOR.** Every bucket must hold at least 150 trades. Post-hoc power
   does not stop a thin bucket with a large observed effect — the lesson that
   cost `adx_retest` a retraction.
2. **HOLM.** At least one feature's primary p must survive Holm–Bonferroni at
   α = 0.05 across all features tested.
3. **DOSE-RESPONSE.** That feature's |ρ| must be ≥ 0.6 with a consistent sign.
   A single freak bucket is not a pattern.
4. **THE DECISION BAR — the retained subset must clear ZERO.** After applying
   the filter, expectancy on the kept trades must be **positive**, not merely
   better than the −12.6% blend. A filter that improves a losing system to a
   less-losing system is not a filter worth trading.
5. **DISCARD RATE.** The filter must discard at least 20% of signals. Below
   that, the arithmetic above shows it cannot close an 8.4-point gap regardless
   of significance.

## What a PASS licenses, and it is not trading

**Both data tranches are spent.** Anything this finds is in-sample against
everything and out-of-sample against nothing, and cannot be confirmed — not now,
not later, because there is no clean data left.

A PASS therefore licenses **forward paper-tracking of the filtered subset and
nothing else**. It is a hypothesis with a number attached, not an edge.

This is stated before the run because the temptation afterwards will be to treat
a surviving feature as a discovery. It would not be one.

## What a NULL means, and it is the likelier outcome

The directional edge is **−5.08%**, CI [−11.69, +1.52] — no measurable edge in
either direction. ADX and RVOL were each refuted at share level. The option
backtest prices every contract off `rv` from closes and resolves TP/SL on closes,
so it is largely self-consistent and leaves little room for a feature to carry
information Black-Scholes has not already priced.

**Prediction, recorded before the run: no feature survives Holm, and `side` comes
closest without clearing clause 4.**

If that is the outcome, the correct response is to stop searching this space, not
to add features — the power table shows each additional feature makes the next
one harder to detect.
