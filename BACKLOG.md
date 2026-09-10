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
