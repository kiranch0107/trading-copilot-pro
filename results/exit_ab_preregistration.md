# Pre-registration — A/B of three exit rules

Written and committed **before** `exit_ab.py` exists.

## What prompted this

`outcome_taxonomy` run 1, over 2,360 trades:

| bucket | n | share of losses | med MFE | med MAE |
|---|---|---|---|---|
| DEAD WRONG | 742 | 47.0% | +0.19 | −1.32 |
| NEAR MISS | 356 | **22.5%** | **+1.82** | −1.47 |
| SHALLOW LOSS | 482 | 30.5% | +0.80 | −1.37 |

356 trades travelled a median +1.82 R our way, peaked at bar 2, then reversed
and stopped at −1.04. They were working, and the exit handed it all back.

This tests whether managing the exit recovers any of that — **on the whole
population, not on the bucket that motivated it.**

## Why the naive calculation is forbidden

356 × 1.04 R is the number this pre-registration exists to refuse. Any rule
that rescues a near-miss also acts on the 704 winners, and `SCARE TARGET` is
precisely the exposed bucket: 237 wins with a median MAE of −0.74 that still
reached target. A break-even stop that saves a near-miss converts some of those
into scratches.

The aggregates cannot resolve that, because it depends on whether each winner's
drawdown came before or after it reached +1 R. **The A/B measures both sides at
once. That is why it is the instrument.**

## The three arms, fixed now

Same signals, same entry bars, same entry fills, same initial stop and target,
same costs. **Only the exit management differs.** Paired by signal, so an
identical trade contributes a delta of exactly zero.

1. **BREAK-EVEN** — once price has traded +1.0 R in our favour, move the stop
   to the entry price.
2. **TRAIL** — once price has traded +1.0 R in our favour, trail the stop at
   1.0 ATR below the high-water mark (above, for a short). The stop never
   moves against us.
3. **PARTIAL** — take half the position off at +1.5 R; the remainder runs to
   the original target or stop.

## Conventions, decided now because they change the answer

- **Stop adjustments take effect at the END of the bar that triggers them.**
  Within a single bar we cannot know whether the high or the low came first, so
  moving a stop up on the same bar it was triggered and then testing it against
  that bar's low is lookahead. The trigger is read on bar *j*; the new stop
  applies from bar *j+1*.
- **Stop is still checked before target on an ambiguous bar**, exactly as the
  baseline does. Conservative, and unchanged so the arms stay comparable.
- **The partial fills as a limit** at +1.5 R when the bar's high reaches it —
  the same assumption the existing target already makes — and pays the same
  slippage. If the stop is hit on that bar, the stop wins and there is no
  partial, again matching the baseline's ordering.
- **One implementation.** The exit policy is a parameter on
  `backtest.simulate_trade`, not a second simulator. A duplicated engine is the
  exact pattern this repo has already paid for once, when `scanner.py` and
  `app.py` each had their own copy of the signal and disagreed. A consistency
  check pins that `policy=None` reproduces today's behaviour **bit for bit**,
  because if it does not, every recorded result silently moves.

## The pass bar

Paired delta in **mean R per trade** against the baseline, across all trades.

- Two-sided paired t on the deltas, Holm-corrected across the **three** arms.
- An arm PASSES only if it survives Holm **and** its mean delta is positive.
- A negative arm that survives Holm is reported as **HARMFUL**, not as "no
  effect" — knowing a rule costs money is a result.

Alongside the verdict, and required in the output rather than optional: the
**decomposition**. For each arm, how many trades moved between buckets and what
each movement paid. A rule that converts 300 near-misses and degrades 400
winners must show both numbers, not a net.

## Predictions

- **PARTIAL is net negative.** Near-miss median MFE is +1.82, above the +1.5
  trigger, so most would bank +0.75 R on half and lose 0.5 R on the rest —
  about +0.25 R instead of −1.04. But 704 winners would take roughly 0.75 R
  less each. Back of envelope that is 356 × 1.29 against 704 × 0.75, and the
  second number is larger.
- **BREAK-EVEN is roughly neutral to slightly negative.** It saves near-misses
  but scratches the SCARE TARGET wins whose drawdown arrives after +1 R.
- **TRAIL is the best of the three and still fails Holm.** It keeps some upside
  the other two cap.
- **Most likely outcome: no arm passes**, and the honest conclusion is that a
  third of trades dying on bar one (DEAD WRONG, 742 trades, median hold 1) is
  an entry problem that no exit rule can reach.

Prior worth stating: `atr_stop_test` already found that *widening* the stop to
1.5× ATR was NOT ESTABLISHED. These are dynamic rules rather than a wider
static one, so the question is genuinely different — but the base rate for this
project finding something real is low, and saying so now is cheaper than
saying it afterwards.

## What a pass would license

In-sample on twenty spent tickers. A passing arm is a hypothesis for tranche C,
not a finding, and not a reason to change the live exit.
