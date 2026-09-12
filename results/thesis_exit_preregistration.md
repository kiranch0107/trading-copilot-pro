# Pre-registration: does the THESIS exit help or hurt?

**Written 2026-09-11, BEFORE `thesis_test.py` exists.** The rule below is fixed
here and implemented clause for clause in that module's `verdict()`.

## Why this one matters more than the others

`exit_monitor.py` runs this rule on **live positions**, unattended, on a
schedule. It closes real contracts. Every other study in this repo asked whether
some hypothesis was tradeable; this one asks whether a rule already spending
money is earning its place.

`results/option_decompose_run1.md` found it fires on **42.6% of all trades** —
596 of 1398, the largest bucket in the system — and contributes **−14.7 points**
of the −9.2% total. Nobody had ever looked at it.

## The ambiguity this resolves

THESIS exits average **−34.4%**. A full stop averages **−59.3%**. So a
thesis-break lands **24.9 points above** a full stop:

- if those trades would otherwise have run to the stop, **the rule saves money**
- if they would have recovered to target, **the rule destroys it**

The decomposition cannot tell these apart. Two arms can.

## Design

Two arms, identical in every respect except `use_thesis`:

| | arm A | arm B |
|---|---|---|
| `use_thesis` | **True** (production) | **False** |
| everything else | identical | identical |

Universe: the OOS 12 over 10 years, the same cut the decomposition ran on.
Metric: `pnl_pct` per trade, percent of premium.
Delta is defined **OFF minus ON**, so a positive delta means *removing* the rule
helped.

### Why the pairing is expected to be complete

`run_ticker` advances `i += cooldown_bars + 1` from the **signal** bar, not from
the exit. Entry dates therefore do not depend on how a trade ended, and every
signal should appear in both arms. This is a design *assumption*, so clause 1
tests it rather than trusting it — the ATR stop test lost 9.2% of its pairs to
exactly this kind of divergence and only found out by counting.

### The live rule and the backtested rule are the same rule

Verified before writing this: `exit_monitor.py` uses
`close < ema20` for calls and `close > ema20` for puts, computed as
`ewm(span=20, adjust=False)`. `option_backtest.py` uses the identical comparison
against `bt.compute()`'s EMA20 from `ta.trend.ema_indicator`. The two EMA
implementations agree to **0.0** after warmup, measured. Had they differed, this
test would not transfer to the live rule and would not be worth running.

## Power, computed before the build

Only the 596 THESIS-exiting trades can move; the other 802 pair at delta exactly
zero. At a plausible subset sd of 40–70%, the aggregate sd is 26–46% and the
smallest detectable effect is **1.96% to 3.42%** of premium. Against a bucket
contributing −14.7 points, that is comfortable. **This is one of the few tests
in this repo that is well powered before it runs.**

## The bar

1. **PAIRING COMPLETE.** Every signal must appear in both arms. If more than 1%
   of signals fail to match, the design assumption is void and the run reports
   **VOID**, not a result.
2. **CONFINEMENT.** Every trade that did NOT exit on THESIS in arm A must have a
   delta of exactly zero. If untouched trades moved, something other than the
   rule changed and the comparison is not clean.
3. **MEASURABLE.** `|paired mean delta|` must exceed the smallest detectable
   effect. Otherwise the result reads **UNMEASURABLE**, not "no difference".
4. **CI CLEARS ZERO.** The 95% CI on the paired delta must exclude zero.
5. **THE DECISION, declared now so it cannot be chosen later:**
   - delta **negative**, CI clears → the rule **helps**. Keep it. No live change.
   - delta **positive**, CI clears → removing it helps. This, and only this,
     licenses changing `exit_monitor.py`.
   - **UNMEASURABLE or CI spans zero → KEEP THE RULE.** The default action is
     the status quo. Changing an unattended live exit rule on a difference the
     data cannot resolve is churn dressed as research.

## AMENDED 2026-09-11, BEFORE THE RUN: clause 4 is redundant

Implementing the bar showed **clause 4 cannot fire**. `mde` uses z = 2.802
(alpha 0.05 **and** power 0.80); the 95% CI half-width uses 1.96. Since
2.802 > 1.96, any delta that passes clause 3 has already cleared zero, so
clause 4 is implied by clause 3 and is unreachable as written.

It is **kept, not removed** — it guards against a future edit that weakens
clause 3 — and the selftest asserts the inequality that makes it redundant
rather than pretending it fires, plus proves the branch still works when handed
a state that reaches it.

Recorded here rather than quietly dropped, and dated: this was found while
writing the code, **before any data was seen**, so it is a clarification of the
bar and not a change to it. The bar is unchanged in effect — clauses 1, 2, 3 and
5 decide everything.

## What a PASS does NOT mean

The system's directional edge is **−5.08%** with a CI of [−11.69, +1.52] — no
measurable edge in either direction. No exit rule repairs that. Even if removing
THESIS improves expectancy by every point available, the system stays
unprofitable. **This test can only make a losing system lose less, and must not
be read as a route to a profitable one.**

## What is NOT being tested

Whether some *different* thesis rule would beat both arms — a wider band, a
confirmation delay, EMA50 instead. That is a parameter search over a live rule
with no clean data left to confirm anything it finds. Out of scope, deliberately.
