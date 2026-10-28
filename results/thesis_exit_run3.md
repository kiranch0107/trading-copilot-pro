# Thesis-exit test — run 3 (the calendar-clock engine)

**Run by the owner 2026-10-28, `python thesis_test.py --years 10` on the
OOS 12, frozen bars, branch `claude/trading-code-review-wt48q8` @ `c1de652`
(BACKLOG 36 group B).** File: `thesis_exit_run3.txt`. Pre-registration
unchanged (`thesis_exit_preregistration.md`). Re-run owed because the option
engine that prices both arms moved to a calendar clock (see
`option_iv_sensitivity_run3.md` for what that is).

## Verdict: KEEP. `exit_monitor.py` is unchanged.

| | run 2 (2026-10-28, sessions clock) | run 3 (2026-10-28, calendar clock) |
|---|---|---|
| trades, both arms | 1,398 | 1,389 |
| thesis ON mean | −9.09 % | −9.60 % |
| thesis OFF mean | −8.83 % | −9.95 % |
| touched by the rule | 590 | 298 |
| unmatched / untouched-but-moved | 0 / 0 | 0 / 0 |
| mean delta (OFF − ON) | +0.25 % | −0.34 % |
| 95 % CI | [−1.80, +2.31] % | [−1.71, +1.02] % |
| smallest detectable | 2.94 % | 1.95 % |
| p | 0.810 | 0.622 |

Clauses 1 and 2 hold as before: zero unmatched signals, zero untouched
trades that moved. Clause 3: |delta| 0.34 % is under the detectable 1.95 %,
so the pre-registered default, KEEP, stands.

**What changed in the reading.** The rule now touches 298 trades, not 590:
on the calendar clock the TIME exit (7 calendar days left) fires about 16
sessions in, where it used to fire at 23, so it pre-empts the EMA20 break
on half the trades the break used to close. The point estimate turned from
+0.25 % (removing the rule helped) to −0.34 % (keeping it helped), both
inside an interval that cannot resolve either; the interval narrowed with
the smaller and less dispersed touched set. Nine trades fewer (1,398 →
1,389) is `backtest.compute()`'s fixed warm-up head and indicator-gap rows,
the same nine `record_recheck_groupb_run1.txt` reports on the 12t/10y cut.
No OPEN (sample-end) trade was reported in either arm; `thesis_test` would
have censored one.
