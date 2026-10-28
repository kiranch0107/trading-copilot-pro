!! SUPERSEDED BY GROUP B (BACKLOG 36, built 2026-10-28). The option engine in this
!! file counted DTE in SESSIONS (priced dte/252, held dte rows), skipped the entry
!! session's close, and settled a trade cut off by the sample's end as if it had
!! expired. The engine now runs on the calendar (expiry = entry + dte days, time
!! value over 365), marks the entry session, and leaves sample-end trades OPEN.
!! RE-RUN OWED on the frozen bars: results/thesis_exit_run3.md (thesis_exit_run3.txt). Until it lands, no constant may
!! be read from this file (consistency_check refuses a SUPERSEDED source).
# Thesis-exit test — run 2 (the no-look-ahead code)

**Run by the owner 2026-10-28, `python thesis_test.py --years 10` on the
OOS 12, frozen bars.** File: `thesis_exit_run2.txt`. Pre-registration
unchanged (`thesis_exit_preregistration.md`). Re-run owed since 2026-10-19
(BACKLOG 35): the option engine that prices both arms carried one bar of
look-ahead in its entry premium.

## Verdict: KEEP. `exit_monitor.py` is unchanged.

| | run 1 (2026-09-12) | run 2 (2026-10-28) |
|---|---|---|
| trades, both arms | 1,397 | 1,398 |
| thesis ON mean | −9.25 % | −9.09 % |
| thesis OFF mean | −9.25 % | −8.83 % |
| touched by the rule | 595 | 590 |
| unmatched / untouched-but-moved | 0 / 0 | 0 / 0 |
| mean delta (OFF − ON) | +0.00 % | +0.25 % |
| 95 % CI | [−2.03, +2.04] % | [−1.80, +2.31] % |
| smallest detectable | 2.91 % | 2.94 % |
| p | 0.998 | 0.810 |

Clauses 1 and 2 hold exactly as in run 1: zero unmatched signals, zero
untouched trades that moved. Clause 3: |delta| 0.25 % is under the
detectable 2.94 %, so the pre-registered default, KEEP, stands.

**What changed in the reading.** Run 1's write-up says the effect "is not
a small effect, it is zero" (+0.00 %, p 0.998). On the fixed code the
point estimate is +0.25 % in favour of removing the rule, inside a ±2 %
interval: UNMEASURABLE, which is what the pre-registration's clause 3
names, not "no difference". The conclusion that the rule neither earns
nor costs its place at a size this test can see is unchanged; the word
"zero" in run 1 was stronger than the test ever licensed.

Trade count 1,398 against 1,397: run 1 recorded one pair silently dropped
against the decomposition's 1,398 (a missing `entry_date`, fixed then);
run 2 has all 1,398.
