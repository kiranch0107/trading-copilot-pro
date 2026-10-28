# Out-of-sample validation — run 4 (2026-10-28), the reproduction owed by group B

**Run by the owner 2026-10-28 on the laptop, `python oos_validate.py`,
branch `claude/trading-code-review-wt48q8` @ `c1de652`, frozen config hash
`a3c94a897f4d6f11` (unchanged since the 2026-09-02 re-lock), the same 12
held-out names, 15 years, regime ON, raw prices.** Files:
`oos_validate.ledger.json` (entry 4), `oos_validate_run4.txt`. Owed because
BACKLOG 36 group B changed `backtest.compute()` (fixed warm-up head; every
session kept on the execution timeline; no signal read from an indicator-gap
row).

## Verdict: FAIL, and IDENTICAL to run 3 in every number

| run | date | bars | trades | expectancy | PF | t | p | 95 % CI | verdict |
|---|---|---|---|---|---|---|---|---|---|
| 3 | 2026-10-28 | v2-38fc8d83a7275823, 13 cached / 0 fetched | 579 | −0.112 R | 0.85 | −1.76 | 0.079 | [−0.237, +0.013] | FAIL |
| 4 | 2026-10-28 | v2-38fc8d83a7275823, 13 cached / 0 fetched | 579 | −0.112 R | 0.85 | −1.76 | 0.079 | [−0.237, +0.013] | FAIL |

Same fingerprint, same 61 stops and 31 targets filled at the open, same
per-ticker table, the same three failure reasons.

## Why nothing moved, and why that is the expected reading

On a frame long enough to trade, the old warm-up (drop every row with a NaN
indicator, then slice 100 more if more than 140 remain) and the new one
(discard the first complete-indicator row plus 100) remove the same 149
head rows. The two differ only on short frames (the 189-vs-190 cliff) and
on interior indicator gaps, where the old engine deleted twenty sessions
and the new one keeps them without a signal. Fifteen years of the twelve
held-out names at ADX ≥ 35 with the regime on produced no trade on a gap
row and no stop crossed inside one. The record's own cuts, run at ADX 25
without the regime on the same names and bars, lost three to nine trades
each (`record_recheck_groupb_run1.txt`): looser gates read the handful of
gap rows the OOS gates never did.

This is run 4 against the same held-out set. Each repeat spends some of
its independence; this one spent it to establish that a code change
touched nothing, which is the one reason to spend it.
