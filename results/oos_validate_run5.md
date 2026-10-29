# Out-of-sample validation — run 5 (2026-10-29), the reproduction owed by the research engine floor

**Run by the owner 2026-10-29 on the laptop, `python oos_validate.py`,
branch `claude/trading-code-review-wt48q8` @ `1c7a76f`, frozen config hash
`a3c94a897f4d6f11` (unchanged since the 2026-09-02 re-lock), the same 12
held-out names, 15 years, regime ON, raw prices.** Files:
`oos_validate.ledger.json` (entry 6; the tool counts every ledger entry,
this is the fifth committed reading), `oos_validate_run5.txt`. Owed because
`backtest.simulate_trade()` now refuses a fill whose |fill − stop| is inside
`risk_params.min_risk(fill)` = max($0.05, 0.3 % of the fill) (BACKLOG 40,
owner: "go with the research engine floor as well").

## Verdict: FAIL on all three criteria, as in runs 3 and 4; one AVGO row gone

| run | date | bars | trades | expectancy | PF | t | p | 95 % CI | verdict |
|---|---|---|---|---|---|---|---|---|---|
| 4 | 2026-10-28 | v2-38fc8d83a7275823, 13 cached / 0 fetched | 579 | −0.112 R | 0.85 | −1.76 | 0.079 | [−0.237, +0.013] | FAIL |
| 5 | 2026-10-29 | v2-38fc8d83a7275823, 13 cached / 0 fetched | 579 | −0.087 R | 0.88 | −1.44 | 0.149 | [−0.205, +0.031] | FAIL |

Same fingerprint, same 61 stops and 31 targets filled at the open, the same
three failure reasons (expectancy below +0.20 R, profit factor below 1.30,
the CI's lower bound below zero). Eleven of the twelve per-ticker rows are
identical to run 4. **AVGO moved alone: 45 trades in both runs, average R
−0.455 → −0.131, total −20.5 R → −5.9 R.** The trade count is the same
because the engine holds one position per name: the refused fill freed
AVGO for the next signal inside what would have been its hold. The
+14.6 R difference on one name with one substitution is the size of the
row the floor removed: a fill a few cents from its stop that then gapped,
booked at roughly −15 R on a risk unit of cents. That is the hazard L2
named, now measured on the held-out set.

## What this does and does not change

Nothing. The verdict is FAIL in every number, and the stopping decision
that rests on it stands. The expectancy moved by +0.025 R because one row
of 579 was removed; the CI's upper bound crossed to +0.031 R for the same
reason and is not a reading. This is run 5 against the same held-out set;
it spent its independence to establish that the floor removes the one
degenerate row and nothing else here.
