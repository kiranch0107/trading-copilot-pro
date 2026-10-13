# PRE-REGISTRATION — the pullback bucket: a long entry on weakness inside an uptrend, recorded forward (BACKLOG 31, option 1)

**Owner's decision 2026-10-13 ("go with option 1, pre-register the pullback
bucket"), written and committed BEFORE the first pullback row is recorded.**
Everything below is fixed. It may be AMENDED in a dated section at the top
only until the first row is committed. Parent: `logic_review_2026-10-13.md`
§3(b) and §5(2); the lead is `inverted_arm_run2.txt` (the bearish base
setup taken long, Bull regime, +0.398 R on 423 trades, inside its null).

## Arrival note, 2026-10-13 (no rule changes)

Measured the same day on the last 252 sessions across the 83-name pool
(`pullback_arrival.py`, counts only, `data_checks_run1.md`): 823 signal
bars, 203 episodes, **0.81 episodes a session**, so 349 episodes take about
**433 sessions, 20.6 months**, not the twelve to eighteen estimated below.
The estimate assumed half of bearish base setups sit above their 200-day
average; three quarters do not. The rule, the bar and the reading are
unchanged.

## Why forward, and why this rule

Every reserved tranche is spent (A, B, C: `data_reservation.lock.json`), so
the lead cannot be confirmed on clean history. The only clean data is the
data that has not happened. This document adds a THIRD bucket to the
forward record beside `live` and `pool`, recorded by the same scanner on
the same names and bars, graded by the same resolver, alerted on never.

The rule is the lead as measured plus the one mechanism condition the
review named: the short-horizon reversal effect is a property of names
that are still in an uptrend being sold for a few weeks, not of names in
a downtrend. Nothing else is added, so nothing is tuned.

## The rule, fixed

A **pullback signal** fires on a settled daily bar when ALL of:

1. **The base BEARISH setup** of `signal_core.evaluate()`, unchanged:
   close < EMA20 < EMA50, MACD below its signal line, 25 < RSI14 < 70, and
   volume at least 0.70× its 20-bar average (`signal_core.base_trend()`,
   the same function the live signal uses; one implementation).
2. **The name is in an uptrend:** the close is above its **200-session
   simple moving average**, computed on the raw daily frame up to and
   including the signal bar (`pullback_signal.SMA_SESSIONS = 200`). Fewer
   than 200 sessions of history: no signal, counted as `insufficient_history`.
3. **The trade is LONG**, with the live config's long-side levels exactly
   as the base bucket builds them (`signal_core.long_levels()`: stop the
   nearer of 1.25 ATR and the 10-bar low less 0.1 ATR; target 4 ATR, capped
   at 99.5 % of the 20-bar high when that high is at least 1 ATR away),
   and the same risk-sanity and R:R ≥ 0.5 gates. So the geometry, the
   resolver and the costs are the base bucket's; **only the choice of bar
   differs**, which is the one thing under test.

The SPY regime (`Bull` / `Bear` / `Neutral`, `market_context`) is **tagged
on every row and gates nothing**. The lead was a Bull-regime effect; the
record will say whether that holds, rather than assume it.

## What is recorded, and what is not

| item | value |
|---|---|
| bucket | `pullback`, a new value of `forward_log.BUCKETS`; rows carry `trend: Bullish`, `decision: taken`, no `hq` field, `setup.regime` |
| universe | every name the scanner evaluates: the watchlist on every in-session scan and the post-close pass, the pool on its once-a-session scan. No extra fetch |
| dedupe | one row per (ticker, Bullish, bar), the record's existing key. The base bullish setup and the pullback are mutually exclusive on a bar (price above vs below EMA20), so the key never collides |
| alerts | **none, ever**. The bucket is a measurement |
| grading | `forward_log.resolve_signal()`, stop-distance basis, next-open entry, stop first, 20-bar timeout, dust 0.05, gapped levels at the open: identical to the base bucket |
| heartbeat | none of its own; its coverage is the live and pool scan rows of the same bars |

## The question, and the bar, read ONCE

**H0:** the pullback bar is no better than the base bar. **H1:** it is
better by at least +0.15 R per episode.

An **episode** is the record's unit: rows on consecutive bars for one
ticker collapse to the first bar's row. The reading happens when **349
pullback episodes have settled** (`power_check.py --sd 1.0`, +0.15 R, 80 %).
It clears only if, net of the resolver's costs, all three hold:

1. mean R of settled pullback episodes exceeds the mean R of settled
   `live` + `pool` base episodes **over the same calendar span** by ≥ +0.15 R
   (the paired comparison: same names, same period, same geometry);
2. the pullback mean's 95 % interval, standard error over episodes, lies
   above zero;
3. a random-entry null, `drift_null`'s method (200 draws, same names, same
   dates, same exits and costs, built from the bars at the time of the
   reading), is beaten by ≥ +0.15 R.

Descriptive only, no verdict: the split by SPY regime; the comparison to
SPY buy-and-hold over the span; the pool-only and watchlist-only subsets.

## Expected arrival, stated now

Today's first pool scan found 7 bearish base setups among 75 names on one
bar. In a bull market perhaps half of those names sit above their 200-day
average, so about three to four pullback rows a day across the 83 names,
and about one episode a day once consecutive bars collapse. **349 episodes
is roughly twelve to eighteen months**, longer if the market turns and
names fall below the average, shorter in a broad grind higher. A
confirmation window is NOT owed to a clearing result: the forward record
is the confirmation.

## Prediction, so it can be scored

- The pullback bucket beats random entry by a margin **inside costs**, +0.05
  to +0.15 R, and does not clear clause 3.
- It beats the base bucket, by less than +0.15 R: clause 1 fails.
- Its mean R is positive but the interval touches zero at 349 episodes.
- It does not beat SPY buy-and-hold over the span.
- The Bull-regime subset is better than the Bear subset, as the lead was.

If all four fail in the direction predicted, the record will have
measured the reversal lead as a real but unharvestable effect at this
account's costs, which closes the review's §3(b). If clause 1 and 3 both
clear, the owner has a second signal worth a live decision, which is a
separate document.

## Enforcement

`pullback_signal.py --selftest` proves the rule on synthetic frames: fires
on a bearish base setup above the 200-SMA, refuses it below, refuses a
bullish base setup, refuses fewer than 200 sessions, builds the live
long-side levels and applies the R:R gate. `scanner.py --selftest` proves
the row is written under `bucket: pullback` for a watchlist name and a
pool name, with no alert and no cooldown stamp.
`consistency_check.check_pullback_bucket_wired()` pins the constants to
this document, the shared base-condition and level functions, the bucket
value, and the no-alert path.
