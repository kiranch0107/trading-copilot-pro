# THESIS exit — run 1: the rule redistributes and adds nothing

**Run 2026-09-12, `python thesis_test.py --years 10` on the OOS 12. Code at
`472f4c9`.** Pre-registered in `results/thesis_exit_preregistration.md`,
committed before the module existed.

## Verdict: KEEP. `exit_monitor.py` is unchanged.

```
arm                    trades    mean %
thesis ON (live rule)    1397     -9.25
thesis OFF               1397     -9.25

pairs                  1397
unmatched              0.00%   (only ON 0, only OFF 0)
touched by the rule     595
untouched but moved       0     (must be 0)
mean delta             +0.00%
95% CI                 [-2.03, +2.04]%
smallest detectable     2.91%
p                      0.9980
```

Clauses 1 and 2 cleared exactly as the design predicted: **zero unmatched
signals** and **zero untouched trades that moved**. The pairing assumption held
perfectly, which the ATR stop test's 9.2% divergence had made worth testing
rather than trusting.

## The result is not "a small effect". It is zero.

`mean delta +0.00%`, `p = 0.9980`. That is not a near-miss.

**And the arms were genuinely different.** The CI half-width of 2.035% implies a
paired-delta **sd of 38.8%** across all pairs — or **59.5% on the 595 trades the
rule actually touched**. If the two arms had somehow been identical, every delta
would have been exactly zero and the sd would have been zero too. It was not.

So the rule moves individual outcomes by roughly **±60% of premium at one
sigma**, and the average of all that movement is **zero**.

**The EMA20 thesis break is a coin flip with enormous variance.** Closing on it
sometimes saves a trade that was heading to a full stop and sometimes kills one
that would have reached target, and across 595 trades those two cancel.

## What this closes

The decomposition made the rule look like the second-largest drag in the system
(−14.7 of the −9.2% total). That framing was accounting, not causation. The
−14.7 is what THESIS trades *earned*; it is not what the rule *cost*, because the
counterfactual is not zero — those trades would have gone on to lose money
anyway, just differently.

**Removing the rule would not recover −14.7 points. It would recover 0.00.**

## "UNMEASURABLE" is the pre-registered word, and it undersells this one

Clause 3 fires because |0.00| < 2.91, so the module says UNMEASURABLE — correct
by the bar as written. But the CI is tight: **[−2.03, +2.04]%**. Any effect
larger than about two points of premium in either direction is excluded.

For a rule touching 42.6% of trades, that is a real constraint rather than an
absence of information. The honest statement is *"bounded to ±2% and centred on
zero"*, not *"we could not tell"*.

## Prediction scorecard

| prediction | outcome |
|---|---|
| KEEP | **right** |
| clauses 1 and 2 clear (pairing perfect) | **right** — 0.00% unmatched, 0 untouched moved |
| delta **negative** — the rule helps by cutting losers early | **wrong**. It is 0.00%. The −34.4% vs −59.3% gap looked like evidence the rule was saving money; it was selection, not causation — trades that break EMA20 are simply a different population, not a rescued one |

The reasoning behind the wrong call is the useful part: **comparing the average
outcome of trades that took an exit against the average of trades that took a
different exit says nothing about what the exit did.** Only the counterfactual
does, and that needed two arms.

## A bug this run exposed

`option_backtest._result` had **no `entry_date` field**. `thesis_test.arm()`
keyed pairs on `t.get("entry_date", t.get("spot0"))`, so the fallback ran on
every trade and two trades sharing an entry spot collided — one was silently
dropped. That is why this run shows **1397 trades and 595 THESIS exits** against
the decomposition's 1398 and 596.

Fixed: `_result` now carries `entry_date`, `arm()` keys on it and asserts
uniqueness, and the selftest verifies the field exists upstream rather than
trusting a `.get()` default.

**The verdict is unaffected.** One trade out of 1398 can move the mean by at most
0.07% against a detectable threshold of 2.91%, and the delta is 0.00. The fix is
for correctness, not because the answer is in doubt.

*A `.get()` default is a guess unless the key is known to exist.* Fourth instance
this week of a guard checking a proxy rather than the thing.

## What this does NOT mean

The system's directional edge is **−5.08%**, CI [−11.69, +1.52]. No exit rule
repairs an absent edge, and this one was never going to. What the run establishes
is narrower and worth having: **the live rule is not costing money, so there is
nothing to fix in `exit_monitor.py`, and no reason to revisit it.**
