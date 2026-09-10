# Backlog

Carried out of the session that produced `results/longshort_split.md`, where it
lived only in conversation context. Written down so the next session picks it up
cold.

Read `results/longshort_split.md` first. It is the research record: the
mechanical signal has no demonstrated edge (10 years, 1,386 trades, −0.048 R,
PF 0.93), the long side decayed +0.268 → +0.106 → +0.085 across three growing
samples, and the short side is reliably negative. Tranche A (16 tickers) is
unspent and cannot settle the question — detecting +0.085 R needs ~50 tickers at
10 years.

Ordered by forcing function, not by interest.

---

## 1. Market calendar runs out — CI fails 2026-11-10

**The only item here with a deadline.**

`MARKET_HOLIDAYS` ends at `2026-12-25`. `consistency_check.py` sets
`CALENDAR_MIN_RUNWAY_DAYS = 45`, so `check_calendar_runway()` starts failing
**61 days from 2026-09-10**, which is the point of it — the check is a forcing
function, not a style rule.

What breaks if it is ignored past 2026-12-25: `is_market_open()` reports the
market OPEN on a holiday, `drop_partial_bar()` then discards the last COMPLETED
bar as though it were still forming, and every module silently analyses
day-stale data. Nothing raises.

The calendar is duplicated **5×** and all copies must stay byte-identical or
`check_calendars_identical()` fails. `CALENDAR_SOURCES` in
`consistency_check.py` is the list:

| file | holidays anchor | half-days anchor |
|---|---|---|
| `signal_core.py` | `MARKET_HOLIDAYS = frozenset({` | `MARKET_HALF_DAYS = frozenset({` |
| `app.py` | `MARKET_HOLIDAYS = {` | `MARKET_HALF_DAYS = {` |
| `scanner.py` | `MARKET_HOLIDAYS = {` | `MARKET_HALF_DAYS = {` |
| `exit_monitor.py` | `MARKET_HOLIDAYS = {` | `MARKET_HALF_DAYS = {` |
| `.github/workflows/scanner.yml` | `HOLIDAYS = {` | `HALF_DAYS = {` |

Add the 2027 NYSE holidays and 1:00pm early closes to all five. `app.py` and
`scanner.py` are CRLF — edit with `newline=''`. Then run
`python consistency_check.py`.

---

## 2. `source` back-fill — needs the user, and will not answer the question alone

The mechanical signal is settled. Whether **operator discretion** adds anything
is not, and `skipped_signals.json` is the control group for the setups actually
taken. PR #19 put the signal-vs-discretionary selector on all three logging
paths, so new rows will carry it.

Existing rows do not. As of 2026-09-10:

- `trade_journal.json` — **6 rows, all `source: "unknown"`** (5 live, 1 paper)
- `open_positions.json` — the open NKE put is `source: "unknown"`
- `skipped_signals.json` — 15 rows

Only the user can tag the historical rows; the information is not in the repo.

**Do not oversell what the back-fill buys.** 6 taken against 15 skipped cannot
settle anything, and the power arithmetic that killed the mechanical signal
applies harder here: no pre-registration, selection is not controlled, and the
operator is the instrument being measured. The back-fill is worth doing because
it is cheap and because the data is only collectable now — not because the study
is ready to run. Pre-register the comparison before the 30-trade test completes,
or it will be another discovery-set result.

---

## 3. M-2 — trade state JSONs tracked in a public repo

`kiranch0107/trading-copilot-pro` is **public** (verified 2026-09-10). These are
committed to it:

- `trade_journal.json` — entries, exits, P&L in dollars
- `open_positions.json` — live and paper positions: ticker, right, strike,
  expiry, contracts, entry premium
- `skipped_signals.json` — setups passed on, with reasons

So current open positions and the full trade history are publicly readable, and
the exit-monitor workflow keeps pushing updates to them.

**Explicitly accepted until after the 30-trade test**, on the grounds that the
account is small and the positions are not market-moving. Recorded here so the
acceptance is a decision on the record rather than an oversight. Revisit when the
test completes or if sizing changes.

---

## 4. Open pre-registered test: the 1.5× ATR stop

Not started. The one question in the research record with real power behind it.

~17.5% of trades die on the bar they opened on, 84% of those lose, averaging
−0.770 R — and `0.172 × (−0.770) + 0.828 × (+0.101) = −0.048` means that group
is the **entire** negative expectancy. The figure reproduces across three cuts
spanning different tickers and different decades, so it is structural to a 1×
ATR stop, not a quirk of one sample.

They cannot be excluded after the fact — you do not know in advance which trades
gap into their stop, and removing them afterwards is selecting on the outcome.
The only lever is a wider stop, and it moves both sides of the ledger:

| stop | target | payoff | breakeven WR |
|---|---|---:|---:|
| 1.0 × ATR | 3.0 × ATR | 3:1 | 25.0% |
| 1.5 × ATR | 3.0 × ATR | 2:1 | 33.3% |

Observed win rate is 31.3%, so widening puts breakeven **above** it — while also
raising the win rate by an unknown amount. Which effect wins is a
pre-registered test, not an adjustment. Write the rule down before running it.

---

## 5. Three money-affecting modules have no tests

Found 2026-09-10 while auditing coverage. `tests.yml` carried a comment naming
`scanner.py`, `exit_monitor.py`, `option_chain.py`, `journal_store.py` and
`gh_sync.py` as "the half CI used to miss" — phrased as solved. Only three of
the five were ever given tests.

| module | what it decides | selftest | run by CI |
|---|---|---|---|
| `exit_monitor.py` | when to close a **live** position; runs unattended on a schedule | **added 2026-09-10** | **yes** |
| `option_chain.py` | which contract to actually buy | none | no |
| `option_backtest.py` | `OPT_WIN_RATE`, the input the live spread gate is derived from | none | no |
| `liquidity_check.py` | — | none | no |
| `universe_backtest.py` | — | none | no |
| `rate_limit.py` | — | none | no |

All are covered only by `consistency_check.py`'s import check, which proves they
parse, not that they are right.

Deliberately **not** stubbed: a shallow test on live-money code that passes
regardless is worse than a visible gap, and the falsification pass has already
found seven fixtures in this repo that did exactly that. `exit_monitor.py` is the
one to do first — its date math (`trading_sessions_between`, the DTE countdown)
is pure and testable offline, and it is the module that can close a real
position.

## 6. ~~Re-run the three backtest cuts on the fixed alignment~~ — DONE 2026-09-10

Run in Codespaces on the fixed code. **All three fingerprints reproduced and
every figure matched exactly**, so the `Open`/`Date` alignment bug was latent in
these three series, not live:

| run | fingerprint | expectancy |
|---|---|---:|
| 7 tickers, 5y | `v2-24b0f52e1203ecd5` ✓ | +0.012 R |
| 12 tickers, 5y | `v2-6b5c07fe41849b1c` ✓ | −0.012 R |
| 12 tickers, 10y | `v2-e09e31d6eaf30233` ✓ | −0.048 R |

The runs reported `cached / 0 fetched`, so they replayed the original bars —
inputs identical by construction, which isolates the code change cleanly.

`results/longshort_split.md` is no longer provisional. The bug was still real;
it would have fired on any series with an interior NaN.

## 7. Option win rate at every TP — MEASURED 2026-09-10, pending one re-run

`python option_backtest.py --sweep`, 7 tickers, 5y, DTE 30, IV 1.15x, 5% spread,
398 trades per row:

| TP / SL | win% | avg win | avg loss | realised payoff | realised breakeven | expectancy | PF |
|---|---:|---:|---:|---:|---:|---:|---:|
| +50 / −50 | 32.7% | +74.4% | −46.7% | 1.59:1 | 38.6% | −7.11% | 0.77 |
| +75 / −50 | 26.9% | +103.8% | −45.7% | 2.27:1 | 30.6% | −5.54% | 0.83 |
| +100 / −50 | 24.9% | +120.0% | −45.1% | 2.66:1 | 27.3% | −4.07% | 0.88 |
| +150 / −50 | 22.1% | +135.4% | −44.6% | 3.04:1 | 24.8% | −4.80% | 0.86 |
| **+200 / −50** | **21.9%** | **+143.0%** | **−44.6%** | **3.21:1** | **23.8%** | **−3.58%** | **0.90** |
| +300 / −50 | 21.9% | +144.7% | −44.6% | 3.24:1 | 23.6% | −3.21% | 0.91 |

**No structure clears.** PF 0.77–0.91, never 1.0. Every row falls short of its own
realised breakeven by 1.7–5.9 points. The gap narrows as TP widens then flattens:
+200 and +300 share the same 21.9% win rate, so there is nothing further out to
reach for. TP+200 is the payoff both open positions use.

### The finding that changes how breakeven must be computed

Checked against NOMINAL breakeven, TP+200 looks fine: 50/(200+50) = 20.0%, and
21.9% clears it. It does not, because **trades do not reach their nominal
levels** — at TP+200 the realised average win is +143.0%, not +200%, and the
average loss −44.6%, not −50%, because the DTE-7 floor and max-hold exit them
early. Realised payoff 3.21:1, realised breakeven 23.8%.

`risk_params.spread_breakeven_wr()` models the SPREAD but not the early exits, so
it understates breakeven by roughly 1.7 points across the sweep. It happens to
reach the right verdict at the 8% ceiling, but by margin, not by modelling. Use
the realised avg win / avg loss from this table, not nominal TP/SL.

(Coincidence worth naming: the realised breakeven at TP+200 is 23.8%, the same
number as the recorded `OPT_WIN_RATE`. Unrelated.)

### TWO THINGS TO RESOLVE BEFORE `OPT_WIN_RATE` IS TOUCHED

1. **23.8% did not reproduce.** Today's measurement at that exact basis
   (TP+100/SL−50, 5y, 7 tickers) is **24.9%**. Two candidate causes, not
   distinguished: the 5-year window is relative to today and has moved; and the
   default universe includes **ROKU**, which is not in the 12-ticker set that
   reproduced exactly — if ROKU carries an interior NaN, the alignment fix of
   #27 would have moved this file's result. The share backtest reproducing
   proves nothing about this one.

2. **Coverage was not confirmed.** That run predated the #32 coverage guard, so
   it printed no `DATA COVERAGE` line. 398 trades across all six rows is a good
   sign (the comparable share run had 418) but is not confirmation.

**NEXT ACTION — one command, in Codespaces:**

```bash
git pull && python option_backtest.py --sweep
```

Check the `DATA COVERAGE` line first. If it says `7 of 7`, the table above stands
and `OPT_WIN_RATE` can be re-derived against it, recording the realised payoff
alongside. If it says fewer, the run is over a partial universe and the numbers
change. `OPT_WIN_RATE` is deliberately NOT updated until this is settled — it
feeds the live spread gate.

## 8. Survivorship in the candidate pool — recorded, not fixable here

`universe_backtest.py` avoids the severe form of survivorship bias (it calls
`select_universe(as_of=d)` so membership on a past date uses only what was
knowable then). The **cross-sectional** axis is still biased:
`universe.CANDIDATE_POOL` is 90 names all listed *today*, so a company that was
a liquid large cap in 2016 and has since been acquired, delisted or shrunk out
cannot be selected on any `as_of` date.

- It flatters the **absolute** numbers of all three arms.
- The **comparison** is only partly protected: `dynamic` picks from the whole
  survivor pool each rebalance, so it harvests pool-level survivorship more
  thoroughly than a fixed 3-name list can. A dynamic-beats-static edge of a few
  basis points sits inside that gap.

Fixing it needs point-in-time index membership including delisted names. yfinance
will not provide it and this project has no such source, so it is recorded rather
than carried silently. Treat any dynamic-beats-static result as a hypothesis
needing survivorship-free data, not a measurement.

## 9. The universe snapshot now ignores what time it ran

Fixed 2026-09-10, noted because it changes snapshots. `universe.fetch_history()`
kept today's bar, whose Close is the live price and whose Volume is partial — and
every gate reads both (`MIN_PRICE`, the 20-day dollar-volume mean, the RS return,
the 200-SMA). A mid-session run therefore ranked partly on the clock.

It had been guarded only by scheduling: `universe-snapshot.yml` runs 12:30 UTC
pre-market. But that workflow also exposes `workflow_dispatch`, and the module's
own docstring invites `python universe.py` directly. `drop_unsettled()` now drops
a today-dated bar unconditionally, matching `backtest._drop_todays_bar()`. Cost:
one settled session after the close. Benefit: the snapshot is the same whenever
it is produced.

If you compare a new snapshot against the three already in `universe_history/`,
expect small membership differences for this reason — the old ones may have been
ranked partly on unsettled bars.

---

## 10. `option_chain.py` still has no test — and it picks the contract

The module that decides **which contract you buy** has no selftest. Its scoring
blends liquidity, a volume weight and a quadratic theta penalty, and
`valid.sort_values("score").iloc[0]` breaks ties by row order. None of it is
asserted anywhere; `consistency_check` only proves it imports.

Worth testing specifically: that the spread ceiling actually excludes a wide
contract, that `bid > 0` and `volume > 0` are enforced (a mid can pass with
bid=0), that the theta penalty prefers a longer-dated contract when DTE is
short, and that the tie-break is deterministic.

`option_backtest.py` and `universe_backtest.py` are the other two with none.
`option_backtest.py` matters most of the three because it produces
`OPT_WIN_RATE` (see item 7).

## 11. Live weekly trend and backtested weekly trend are different rules

`market_context.weekly_trend_from_bars()` reads `close.iloc[-1]` — the CURRENT
weekly bar, which does not close until Friday. `backtest.build_weekly_trend_map()`
deliberately lags each week's verdict so it is only usable from the following
week, and its selftest asserts that lag at a week where the verdict flips.

So anything the backtest concluded about the weekly filter does not transfer to
the live rule. It is harmless **today** only because
`signal_core.DEFAULTS.weekly_confirm` is `False` and pinned False by an
assertion (it rejected 5 bars in 13,748).

`consistency_check.check_weekly_rule_parity()` now fails CI if the filter is ever
turned on while the divergence stands. To actually close it: either lag the live
rule to match the backtest, or re-measure the filter with the live (unlagged)
definition and record the result. Do not enable it on the strength of the
existing measurement.

---

## Working conventions

- `signal_core.py` is canonical. `consistency_check.py` enforces 17 cross-module
  invariants (17 `check_` functions); run it before pushing.
- **Every new guard gets falsified** — deliberately broken to confirm it fails
  with the right message. That pass found six dead fixtures in the #20–#25 run;
  tests that cannot fail are the default outcome, not the exception. Do not skip
  it.
- `app.py` and `scanner.py` are **CRLF**; `signal_core.py`, `backtest.py` and
  `consistency_check.py` are LF. Edit CRLF files with `newline=''`.
- Costs are single-sourced in `risk_params.py`. `MAX_OPTION_SPREAD_PCT = 8.0` is
  **derived** from `OPT_WIN_RATE = 0.238`, not fitted — at an 8% round trip the
  breakeven win rate is 23.3% against a measured 23.8%. If `OPT_WIN_RATE` is
  ever re-measured, re-derive the ceiling rather than keeping 8.
  `check_cost_gates_shared()` enforces the single source.
- Backtest windows are **relative to today** — `backtest.py` passes
  `period=f"{years}y"` to yfinance. A "10-year" run means today minus 10 years,
  so which regimes a half contains shifts as time passes. State the absolute
  dates when writing up a result; getting this wrong is what put two errors into
  `results/longshort_split.md` (see its 2026-09-10 correction block).
- Data fingerprints (`bar_cache`, v2) hash the DATA, not the code. A moved number
  under an unchanged fingerprint is a code change; a moved fingerprint is a data
  change. Check it before comparing anything else.
- No `gh` CLI in the web sessions — GitHub MCP tools only.
- Web sessions install their own dependencies via
  `.claude/hooks/session-start.sh` (SessionStart hook). It upgrades setuptools
  from PyPI **before** `pip install -r requirements.txt`, because `ta==0.11.0`
  is sdist-only and the container's Debian-patched setuptools 68.1.2 fails its
  build with `AttributeError: install_layout` — which aborts the whole install
  and takes streamlit, pytz and altair down with it. CI is unaffected
  (`actions/setup-python` ships an unpatched setuptools), which is why the
  workaround lives in the hook and not in `requirements.txt`.
