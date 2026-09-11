# Audit: recorded claims against the code that produced them

**2026-09-11. No study.** Prompted by four instances of one failure class in a
single day — a description sitting next to the thing it describes, drifting from
it. This checks the recorded claims in `results/` and `BACKLOG.md` against the
modules that produced them.

## Method

Three passes, in order of how mechanical they are:

1. **Constants.** Every module-level `NAME = number` extracted from all 31
   modules; every one of those names searched for in `results/*.md` and
   `BACKLOG.md`; the prose value compared to the code value.
2. **Pinned records.** Numbers in prose that a module pins as a record, compared
   against the pin.
3. **Claims stated in words.** Read the assertion, then read the code that is
   supposed to support it. This is the pass that found everything.

Pass 3 does not scale and cannot be automated. That is the honest limit of this
audit: passes 1 and 2 are complete, pass 3 covered the load-bearing claims in
the eight results files and is not exhaustive.

---

## CLEAN — recorded here so the audit is falsifiable

| checked | result |
|---|---|
| `MAX_OPTION_SPREAD_PCT = 8.0` in 3 documents | matches `risk_params` |
| `MIN_EVENTS 400` in `pead_run1.md` | matches `pead_study` |
| `longshort_split.md` vs `record_recheck.RECORD` | **exact** — 418 / 684 / 1386, +0.012 / −0.012 / −0.048, long 260 @ +0.268, 889 @ +0.085 |
| 43 module-level constants, 4 duplicated across modules | all four agree |
| `atr_stop_run1.md` control row vs the record | exact, verified at run time |

## FINDING 1 — the verifiability screen's Sharpe column is unsourced, and one row claimed a measurement that does not exist

`grep -i sharpe *.py` returns **nothing**. No module in this repo computes a
Sharpe ratio. Yet `verifiability_screen.md` gates every future hypothesis
(BACKLOG 14: *"read before proposing another hypothesis"*) on a table of seven
Sharpes, none cited.

Six are recalled literature estimates. Labelling them as such is enough — the
screen's conclusions are robust to ±0.1 on any of them.

The seventh was not:

> | VRP, as measured here | 1.30 | 5 | **yes, and it was** | no — beta, not premium |

`vrp_check.py` measures a premium in **vol points** and returns no return series,
so it has no Sharpe and 1.30 is not derivable from anything it reports. The
Sharpe the project *did* measure is the tradeable implementation's, and
`spread_backtest_run2.md` puts it at **0.21** — below every other row in the
table rather than at the top of it.

**Severity: high.** Not because 1.30 is wrong, but because the row cited this
project as its source. A future session reading the screen would find the one
row that appears measured, and it is the one row that is not.

Corrected in place: column relabelled, row struck through, both numbers and the
distinction between them recorded.

## FINDING 2 — "beta, not premium" is unresolved, not established

The same table calls the VRP result *"no — beta, not premium"* in bold. That
rested on the opportunity-cost argument in `spread_backtest_run1.md`, which
**run 2 withdrew today**. `hold%` — the direct test of whether beta or the
premium paid you — shows three of four put-spread arms *beating* their own
capital-matched beta benchmark.

Those margins are 5–25× below the design's detection threshold, so run 2 refutes
nothing either. The claim is simply open. Corrected in place.

## FINDING 3 — the 10-year bar was quoted at 0.62 when this project's bar is 0.89

The screen's `z = 2.802` is **α 0.05 and power 0.80**. Deriving the bar afresh
with `z = 1.96` — significance only — gives 0.62 at ten years and 0.44 at twenty,
and a strategy that clears *that* bar still fails to detect its own effect four
times in ten.

This error was mine, made today while analysing VRP structures, and it is the
audit's own failure class: a number re-derived from scratch instead of read from
the document that already fixed the convention. The VRP conclusion strengthens
(0.21 against 0.89, not 0.62), but the bar quoted was the weaker one. The
convention is now stated at the point of use.

## FINDING 4 — a new guard shipped dead, and the suite still said "All 24 passed"

`check_duplicated_constants_agree()` was written, added to the file, and never
registered: the `CHECKS` entry was inserted with 11 spaces against a registry
using 12, the replace silently no-op'd, and `consistency_check.py` reported
**All 24 cross-module consistency checks passed** — the same count as before,
with a dead function sitting above it.

Caught by checking that the new check's own line appeared in the output rather
than trusting the pass count. Recorded because it is the audit committing the
error the audit is about, inside the file meant to prevent it.

## The guard added

`check_duplicated_constants_agree()` — a constant defined in more than one module
must hold the same value. This repo has already paid for this class once: the
option bid-ask ceiling had five copies that disagreed and the option win rate had
two, both found by hand.

Currently four constants are duplicated and all agree:

```
MIN_RHO       adx_retest, pead_study
ALPHA         adx_retest, atr_stop_test, pead_study
RISK_FREE     option_backtest, spread_backtest
TRADING_DAYS  option_backtest, spread_backtest, vrp_check
```

Duplication is not itself the failure — a study module pinning its own
pre-registered alpha is reasonable. The check asserts agreement rather than
forbidding copies. Falsified in both directions: a disagreeing value fails
naming both modules, and a regex that stops matching anything fails rather than
passing vacuously, which is how a check of this shape would otherwise die.

## What this class of bug has cost, and what catches it

Six instances in two days:

| instance | caught by |
|---|---|
| reservation `note` said held-out, `spent` said otherwise | `data_reservation.spend()`, after a run was planned on 32 contaminated tickers |
| BACKLOG item 5's coverage table listed tested modules as untested | manual audit, weeks late |
| ATR same-bar counted `hold <= 1` against a record built on `hold == 0` | restructuring, so the fixture stopped testing itself |
| BACKLOG item 4 said "Not started" about committed work | manual audit |
| the cost claim was backwards for 3 of 4 arms | measuring instead of repeating |
| clause 5's prose described a stronger test than the code ran | the `hold%` column finally existing |

**Only one was caught by a guard.** The rest needed someone to measure the thing
the prose described. That is the argument for the discipline, not for more
checks: a check can compare two numbers, but only a measurement can tell you
whether a sentence is true.
