# Code review — 2026-09-16

Full-repo review of the live money path and the research engine, run against
`c5894b7`. Baseline established first: `consistency_check.py` passes all 31
checks and all 38 module `--selftest`s pass, before and after the changes
below. Nothing here was found by the existing harness, which is the point —
every finding is a gap that harness does not cover.

Findings are ordered by consequence, not by how hard they were to find.

---

## H1 — The forward log records into a void, and could not be settled if it did

`forward_log.py` is the project's stated answer to "there is no clean data
left": all three reserved tranches are spent, so the only remaining route to
new evidence is a record that starts before the outcomes do. Four independent
defects mean it produces nothing.

**1. It is never persisted.** `.github/workflows/scanner.yml` commits
`scanner_state.json` and nothing else. `forward_log.jsonl` is written on the
Actions runner and destroyed when the runner is reclaimed. The file is absent
from the repo at HEAD (deleted in `a5b54ae` and never recreated).

This contradicts the design in writing. `.gitignore` says:

> `forward_log.jsonl` is DELIBERATELY TRACKED, not ignored. […] Git is the
> external anchor: committing the log after each scan makes a truncation
> visible as a deletion in the diff.

There is no such commit step. The hash chain's one protection against tail
truncation — an external anchor — does not exist, and neither does the data.

**2. The same signal is logged once per scan, not once per bar.** The cron is
`7 15,17,19 * * 1-5` — three runs a day. `drop_partial_bar()` removes today's
in-progress bar, so all three runs evaluate the *same settled bar* and
`analyze()` calls `_log()` on each. The 4-hour cooldown does not help: it lives
in `run()` and is checked **after** `analyze()` has already written. So one
signal produces three identical `taken` rows per day, and more on every
subsequent day the setup persists.

**3. Rows carry no signal bar date.** `_setup` is `price, entry, stop, target,
rr, rsi, adx, atr, strength, high_quality, filters_pass, filters_total`.
`append()` stamps `ts` — wall-clock write time, not the bar. So duplicates
cannot be collapsed after the fact and no row can be joined to the bar it
fired on.

**4. `record_outcome()` has zero callers.** Nothing in the repo attaches an
outcome to a logged signal; `journal_store.close_position()` does not touch the
forward log. A signal recorded here can never be settled, so the record can
never answer the question it was built to answer.

Also worth naming: `_log("taken", "")` runs before both the cooldown check and
`send_alert()`, so `taken` means "would have alerted", not "alerted". That is
arguably the right semantic for measuring what the *rules* produced, but it is
not what the field name says and it is not documented.

**Fix ordering matters, and it is the reason nothing was changed here.**
Persisting the log first — the obvious one-step fix — would commit three
duplicate rows per signal per day into an **append-only hash chain**. The
chain's whole value is that it cannot be edited, so a poisoned log cannot be
cleaned; it would have to be abandoned and restarted, spending the one
advantage a forward record has (that it started early). The correct order is:

1. Add the signal bar date to `_setup` (`df.index[-1]`, already in hand).
2. Dedupe on `(ticker, trend, bar_date)` inside `record_signal()` — in the
   module, not the caller, so a second caller cannot reintroduce it.
3. Wire `record_outcome()` into `journal_store.close_position()`.
4. *Then* add the commit step to `scanner.yml`.

---

## H2 — `longs_only.simulate()` books P&L at entry, not at exit

`simulate()` walks trades in **entry-date** order and does `equity += pnl` in
the same iteration that opens the position, while keeping it in `open_pos`
until its `exit_date`. A trade's result therefore funds the account before it
has happened.

Reproduced offline:

```python
trades = [
    {"entry_date": "2020-01-02", "exit_date": "2020-03-01",
     "entry": 100.0, "stop": 98.0, "r": +5.0, "trend": "Bullish"},
    {"entry_date": "2020-01-03", "exit_date": "2020-03-02",
     "entry": 100.0, "stop": 98.0, "r": -1.0, "trend": "Bullish"},
]
simulate(trades, account=10_000, risk_pct=1.0, constrained=False)
# equity 10_395.00   curve [10_000, 10_500, 10_395]
# correct (P&L at exit): 10_000 + 500 - 100 = 10_400.00
```

The second trade is sized on $10,500 — including the first trade's unrealised
$500, two months before it closes — so it risks $105 instead of $100.

Three consequences, all in the published run:

- **Compounding lookahead.** Peak concurrency in `portfolio_replay_run1` is 5,
  so this is not a corner case; every overlapping trade is sized on the
  unrealised results of the trades still open beside it.
- **The capital constraint is looser than it claims.** `deployed + value >
  equity` is tested against an equity already inflated by open positions'
  unrealised gains. The idealised-vs-constrained gap is the module's stated
  finding ("The GAP BETWEEN THEM is the finding"), and it is understated.
- **Drawdown is mis-dated and mis-sized.** `curve` is indexed by entry order
  with each trade's full P&L landing at its open, so `max_dd_pct` is not the
  path the account took. The module says drawdown "is what the path did" and
  calls it "the number worth trusting most in this file".

`peak capital deployed 101% of equity` in the published run is a symptom of the
same mechanism, not a real over-deployment: `peak_deployed` divides by an
equity that has *already* absorbed the new trade's loss, so a losing entry
pushes the ratio above 100% even though the constraint that is supposed to cap
it at 100% was satisfied when it was checked.

**Not fixed here, deliberately.** The fix is a restructure (realise P&L on the
exit-date event rather than the entry-date event, and build the curve on those
events), and it changes every number in `results/portfolio_replay_run1.txt` —
all four curves, both CAGR figures, both drawdowns, and the "49% survives"
claim about the break-even rule. This session has no market-data access
(Yahoo is blocked at the proxy), so the corrected run cannot be produced, and
shipping code that silently disagrees with a committed result is worse than
either. It needs a fix and a re-run in the same change.

Note the affected results are *the account-level replay only*. R-multiples are
untouched, so `exit_ab`, `drift_null` and the tranche C confirmation are not
affected — the same containment as the `stop`/`orig_stop` regression in `58b62e6`.

---

## H3 — A position that is EXIT_SIGNALLED is never monitored again, with no way back

`exit_monitor.run()` selects `status == "OPEN"` and `continue`s on everything
else. Once any rule fires, the position is flipped to `EXIT_SIGNALLED` and
drops out of monitoring permanently. `app.py` renders those positions in a
"🔔 Exit signalled — close these" list; there is no dismiss, no re-arm, and
nothing anywhere in the repo sets `status` back to `OPEN` (the only
assignment is in `journal_store.open_option_position()`).

The failure case is ordinary, not exotic. The rule priority is STOP, TARGET,
TIME, HOLD, THESIS. THESIS fires on a single close through EMA20 and HOLD on a
session count — both are judgement calls the owner may reasonably decline. The
moment either fires, **the STOP rule is dead for the life of that position**,
on a long option where the premium is the entire maximum loss.

This is the same shape as the gap that motivated widening the exit-monitor
cron — a position last checked Friday, exiting at −71.7% against a −50% rule —
except unbounded rather than 92 hours.

Two designs would close it, and the choice is the owner's:

- Keep monitoring `EXIT_SIGNALLED` positions for *higher-priority* reasons
  only (a STOP after a THESIS re-alerts; a THESIS after a THESIS does not), or
- Add an explicit "keep it open" control that sets `status` back to `OPEN` and
  clears `exit_alerted`, making the decision to override a logged act.

The first fails safe without requiring the owner to be present, which is the
property this monitor exists for.

---

## M1 — The take-profit tooltip sold the arithmetic `risk_params.py` refutes *(fixed)*

The `help=` text on the "Take profit +%" input read:

> this entry signal wins ~40% of the time […] TP+100/SL-50 is 2:1, breakeven
> at 33%, so the same signal turns positive.

40% is the **share** backtest's win rate. `risk_params.py` names that exact
substitution as the error that "made losing configurations look profitable",
and the measured option-level rate at TP+100/SL-50 is 24.9% against a
*realised* breakeven of 27.3%. The EV panel rendered a few lines below the
same widget already says every measured level is negative — so the app
contradicted itself on one screen, with the optimistic copy attached to the
control that sets the rule.

Replaced with text computed from `risk_params.OPT_SWEEP_BY_TP`,
`realised_breakeven_wr()` and `measured_option_edge()`, so it cannot drift from
the panel again.

---

## M2 — `capture_entry_features()` compared a settled ticker bar against an unsettled SPY bar *(fixed)*

`df` goes through `drop_partial_bar()`; `spy = get_data("SPY")` did not
(`get_data()` does no dropping — `app.drop_partial_bar()` is applied separately
by each caller). Mid-session, `rs_20d` therefore measured the ticker's return
to **yesterday's settled close** against SPY's return to the **live price**,
over 20-bar windows offset from each other by one bar.

The one live position on file carries `rs_20d: -0.0706` captured at 11:56 ET
and is affected. `entry_features` is collect-only today, and it is also the
field a later study would slice on — and a captured feature cannot be
recomputed after the fact. This is the seventh site of the unsettled-bar
defect `signal_core.drop_unsettled()` exists to end; it reached this path
because the snapshot fetches a *second* series and only guarded the first.

---

## M3 — The earnings blackout is a second duplicated gate implementation

`app.check_earnings_blackout()` and `scanner.check_earnings_blackout()` are
independent implementations of the same rule, and they already differ in
behaviour: **app reads only the first earnings date** returned
(`get_next_earnings()` returns a single value), while **scanner iterates every
date** in the calendar. On a ticker whose calendar returns more than one date,
the two gates can disagree — which is precisely the app-vs-scanner divergence
`signal_core.py` was created to end.

`consistency_check` pins `is_market_open()` across four copies, `compute()`
across modules, and the unsettled-bar decision across five call sites. Nothing
pins this one. The durable fix is the one this repo has already applied twice:
one implementation, injected inputs, both callers importing it.

Not changed here — merging the two is a behaviour change to a live gate on
both the app and the unattended scanner, and it deserves its own change with
its own test rather than riding a review.

---

## M4 — `scanner.check_earnings_blackout()` read the host clock *(fixed)*

`today = datetime.now().date()` against an ET earnings calendar, in a module
that runs on UTC GitHub Actions runners. This is the trap
`exit_monitor.days_to_expiry()` documents and fixed there ("`date.today()`
reads the HOST clock, and GitHub Actions runners are UTC"); this was the copy
that still had it.

No live impact at the current cron — 15/17/19 UTC never crosses midnight UTC —
so this was a latent trap, not an active bug. It is worth closing because
`exit-monitor.yml` has already been widened to 21 UTC once for good reasons,
and the same widening here would have shifted every earnings delta by a day
with nothing raising.

---

## Low

- `exit_monitor.trading_sessions_between()` docstring said the calendar "only
  covers 2025-2026"; it covers 2027 since BACKLOG item 1 closed. *(fixed)*
- `.github/workflows/exit-monitor.yml` passed `--interval 30` while its cron is
  hourly. The flag is informational, so the value was simply wrong. *(fixed)*
- `app.py`'s EXIT_SIGNALLED icon map omitted `HOLD`, one of the five reasons
  `exit_monitor.format_alert()` emits, so a max-hold exit rendered as a generic
  ⚠️. *(fixed)*
- `journal_stats()` orders the equity curve by `closed` (write time) while
  `close_position()` accepts a back-dated `closed_at` that nothing reads. A
  back-dated close is plotted at today.
- The expectancy column in `risk_params.py`'s sweep table reads `-4.07%` at
  TP+100 where `measured_option_edge(100)` computes `-3.99%` from the same row
  (and `-3.21%` vs `-3.14%` at TP+300). Rounding in the recorded table, not in
  the code; the code is the one callers read.
- `option_chain.get_option_data()` returns `is_budget = mid <= budget_max`,
  where `budget_max` is app's per-share sidebar value (default $2.00). The
  units are consistent, but the UI prints "under $2.00" next to "$X/contract"
  when a contract costs 100× that, and it is an entirely separate budget from
  `risk_params.option_budget()` ($250). `size_gate()` is the real gate, so
  nothing is mis-gated — only mis-read.

---

## What this review did not find

Worth stating, because a review that reports only problems is not a
measurement either.

The things most likely to be wrong in a system like this are right here. Entry
is next-bar open with slippage both ways. Stop wins over target on the same bar.
Gaps past either level before the fill are refused and counted rather than
scored with an inverted sign. Policy-moved stops take effect on the *next* bar,
so a trigger is never tested against the bar that produced it. Right-censored
trades are flagged rather than silently counted as timeouts. The direction gate
is live-only so ten years of recorded results do not change meaning. Costs are
charged on both legs. RVOL is read at the signal bar, not the entry bar.

The option-level arithmetic is honest in the direction that costs the author
something: breakeven is computed from *realised* average win and loss rather
than nominal TP/SL, an unmeasured payoff structure gets refused a number
instead of an extrapolation, and the app says plainly that no setting makes the
signal profitable.

The four findings above are all of the same family, and it is a family worth
naming: **the check exists, and the thing it checks is not wired to anything.**
A tamper-evident log that is never committed. A capital constraint tested
against inflated capital. A monitor that stops monitoring. A tooltip carrying
the number the module beneath it refutes. Each was invisible for the same
reason — nothing downstream ever read the result and complained.
