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

## Owner hand-off — READ FIRST, every session

Dated notes the owner asked to be read on the next pull. Append; never
rewrite an earlier note. Remove a note only when the thing it waits on has
landed in the repo and the removal says so in the commit.

- *(none open as of 2026-10-26.)* The 2026-10-13 note (AAPL closed outside
  the repo; NVDA and PFE to be closed 10-16; the app's dead PAT) was retired
  on 2026-10-26: the AAPL close, the replacement token and the NVDA and PFE
  closes have all landed in the repo (`results/progress_review_2026-10-26.md`).
  The token expiry dates it carried (app PAT 2027-01-05, dispatch token
  2027-01-06, both rotated in the last week of December 2026) live on in the
  scheduler item's "FORCING FUNCTION — TWO PATs" paragraph.

---

## 1. ~~Market calendar runs out — CI fails 2026-11-10~~ — DONE 2026-09-11

**Closed with 60 days to spare.** 2027 added to all five copies; the calendar
now ends **2027-12-24**, giving 469 days of runway. The next forcing date is
**2027-11-09**, when runway drops under the 45-day minimum.

Two edge cases the derivation caught that copying a typical year would have got
wrong — both were computed, not recalled:

- **Juneteenth 2027 falls on a Saturday** → observed Friday **2027-06-18**
- **Christmas 2027 falls on a Saturday** → observed Friday **2027-12-24**, which
  makes Dec 24 the *holiday itself*, so there is **no** Christmas Eve half-day
  in 2027. A pattern-copied calendar would have listed one and marked the market
  open-but-early on a day it is closed.

2027 is therefore 10 holidays and exactly **one** half-day (2027-11-26, the day
after Thanksgiving). July 3 is a Saturday, so no early close there either.


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

## 2. ~~`source` back-fill~~ — DONE 2026-09-11

**Resolved by owner attestation: every historical trade came from a signal
alert.** All 8 journal rows and all 15 skipped-signal rows set to
`source: "signal"`.

**They are marked as reconstructed, not captured.** Each back-filled row carries
`source_backfilled: true`, a timestamp, and a note saying the value came from
recollection rather than from the trade. That distinction matters: a back-filled
provenance field that looks identical to a captured one would let a future
session treat owner memory as evidence. Anything analysing `source` can now
exclude reconstructed rows.

The live position (opened 2026-09-11) has `source: "signal"` captured at entry,
so the app records it going forward and no further back-fill will be needed.

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

## 3. M-2 — trade state JSONs tracked in a public repo — DEFERRED by owner 2026-09-11

**Owner has judged this low priority for now (2026-09-11).** Recorded rather
than closed: the exposure is unchanged — `trade_journal.json` carries `pnl_usd`
and `contracts`, and `open_positions.json` carries a live position's strike,
expiry and entry premium, all in a public repository. Revisit if the account
grows or the repo audience widens.

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

## 4. ~~Open pre-registered test: the 1.5× ATR stop~~ — RUN 2026-09-11, NOT ESTABLISHED

**Result: `results/atr_stop_run1.md`.** Clauses 3 and 4 cleared; clauses 1, 2 and
5 failed. The control reproduced the record exactly (1386 trades, −0.048 R, 17.2%
same-bar), so the engine had not drifted and the run is interpretable.

The mechanism's own predictions came true and bought nothing. Same-bar deaths
fell 17.2% → 4.8%; win rate rose 31.3% → 45.3%; expectancy moved by +0.017 R with
a CI of [−0.034, +0.068] against a detectable threshold of 0.073 R. Payoff fell by
as much as win rate rose.

Win rate minus breakeven at each width — −1.6, −1.1, −2.2, −0.6 points —
reproduces each arm's expectancy to three decimals. The signal lands just under
fair odds at every stop width, so widening only slides along an indifference
curve. **The stop is not mis-calibrated; there is nothing for it to be
mis-calibrated against.**

Read it as UNMEASURABLE, not "no effect": only 20 of ~1390 pairs were identical,
so the pairing halved the standard error rather than collapsing it, and an effect
of ~+0.03 R would have gone undetected. What is ruled out is a large effect.

Do **not** sweep more widths. Both tranches are spent, so nothing a sweep finds
could be confirmed, and the indifference-curve result says the search has no
destination.

The original item, kept for the record:

The one question in the research record with real power behind it.

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

The rule, as written down: stops swept at 1.0 / 1.25 / 1.5 / 2.0 × ATR with the
target **fixed** at 3.0 ×; trades paired on `(ticker, entry_date)` so a signal
whose outcome is unchanged contributes a delta of exactly zero; and **clause 5
demands the destination, not the direction** — expectancy at the winning width
must clear **zero**, because −0.048 R → −0.020 R satisfies every other clause and
still loses money on every trade.

Note the same-bar figure above is `hold == 0`, matching
`backtest.print_hold_profile()`. `atr_stop_test.py` first counted `hold <= 1`,
which is two bars; that is fixed, and the baseline 1.0 × arm should reproduce
~17.2% and −0.048 R. **If it does not, that is drift, not a result.**

Run (after the test lands on `main`):

```bash
python atr_stop_test.py --tickers GOOGL,AVGO,AMD,NFLX,CRM,ADBE,QCOM,MU,ORCL,NOW,PANW,LRCX --years 10
```

Standing limitation: both data tranches are spent, so a PASS licenses changing
`atr_stop_mult` and nothing more — it is out-of-sample against nothing and stays
a hypothesis permanently.

---

## 5. ~~Three money-affecting modules have no tests~~ — CLOSED 2026-09-11

Found 2026-09-10 while auditing coverage. `tests.yml` carried a comment naming
`scanner.py`, `exit_monitor.py`, `option_chain.py`, `journal_store.py` and
`gh_sync.py` as "the half CI used to miss" — phrased as solved. Only three of
the five were ever given tests.

| module | what it decides | selftest | run by CI |
|---|---|---|---|
| `exit_monitor.py` | when to close a **live** position; runs unattended on a schedule | **yes** | **yes** |
| `option_chain.py` | which contract you actually buy | **yes** | **yes** |
| `option_backtest.py` | `OPT_WIN_RATE`, the input the live spread gate derives from | **yes** | **yes** |
| `liquidity_check.py` | a standalone spread/liquidity report | none | no |
| `universe_backtest.py` | compares universe-selection arms | none | no |
| `rate_limit.py` | throttles provider calls | none | no |

**All three money-affecting modules are now tested and wired into CI**, which
closes the item as written. The three that remain untested are not in the live
money path: `liquidity_check.py` is a standalone report, `universe_backtest.py`
is a research comparison, and `rate_limit.py` throttles provider calls. They are
covered only by `consistency_check.py`'s import check, which proves they parse,
not that they are right — worth doing, but not the hazard this item named.

Verified by measurement on 2026-09-11, not by reading the table: the previous
version of this table still listed `option_chain.py` and `option_backtest.py` as
untested three weeks after they were given tests, which is the same stale-prose
failure as the reservation lock's `note` field.

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

## 7. ~~Option win rate at every TP~~ — RESOLVED 2026-09-10

`option_backtest.py --sweep`, **DATA COVERAGE 7 of 7 confirmed**, 398 trades per
row. Re-run on the fixed code reproduced the first run exactly.

| TP / SL | win% | avg win | avg loss | realised | realised BE | margin | expectancy | PF |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| +50 / −50 | 32.7% | +74.4% | −46.7% | 1.59:1 | 38.6% | −5.9 | −7.11% | 0.77 |
| +75 / −50 | 26.9% | +103.8% | −45.7% | 2.27:1 | 30.6% | −3.7 | −5.54% | 0.83 |
| +100 / −50 | 24.9% | +120.0% | −45.1% | 2.66:1 | 27.3% | −2.4 | −4.07% | 0.88 |
| +150 / −50 | 22.1% | +135.4% | −44.6% | 3.04:1 | 24.8% | −2.7 | −4.80% | 0.86 |
| **+200 / −50** | **21.9%** | **+143.0%** | **−44.6%** | **3.21:1** | **23.8%** | **−1.9** | **−3.58%** | **0.90** |
| +300 / −50 | 21.9% | +144.7% | −44.6% | 3.24:1 | 23.6% | −1.7 | −3.21% | 0.91 |

Recorded in code as `risk_params.OPT_SWEEP_BY_TP`, with `measured_option_edge()`
and `realised_breakeven_wr()`. `app.py` reads them, so a TP+200 position now gets
its measured expectancy instead of a refusal — and an unmeasured structure still
gets no number.

`OPT_WIN_RATE` updated 0.238 → **0.249**, only after coverage was confirmed. It
moves no gate: both are far under every breakeven above.

### THE PAYOFF PLATEAUS — "widen the take-profit" does not work here

Average win by TP: +74.4 → +103.8 → +120.0 → +135.4 → **+143.0 → +144.7**. It
saturates near **3.2:1** because trades exit on the DTE-7 floor and max-hold, not
at the target. Doubling the target from 150 to 300 buys 9 points of average win
and costs 0.2 points of win rate.

Nominal payoff and realised payoff also cross over, which is the trap:

| TP | nominal | realised | |
|---|---:|---:|---|
| +50 | 1.00:1 | 1.59:1 | realised **better** — winners overshoot a near target |
| +100 | 2.00:1 | 2.66:1 | better |
| +150 | 3.00:1 | 3.04:1 | level |
| +200 | 4.00:1 | 3.21:1 | realised **worse** — the target is not reached |
| +300 | 6.00:1 | 3.24:1 | much worse |

So nominal breakeven flatters exactly where the live rules sit. At TP+200 the
nominal breakeven is 20.0% and 21.9% "clears" it; the realised breakeven is 23.8%
and 21.9% is **1.9 points short**. Same trades, opposite conclusion.

`spread_breakeven_wr()` charges the spread but still assumes nominal levels, so
it understates by ~1.7 points. Use `realised_breakeven_wr()` for a decision.

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

## 9. ~~The universe snapshot ignores what time it ran~~ — FIXED 2026-09-10

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

## 10. ~~`option_chain.py` has no test~~ — CLOSED 2026-09-11

The module that decides **which contract you buy** has no selftest. Its scoring
blends liquidity, a volume weight and a quadratic theta penalty, and
`valid.sort_values("score").iloc[0]` breaks ties by row order. None of it is
asserted anywhere; `consistency_check` only proves it imports.

Worth testing specifically: that the spread ceiling actually excludes a wide
contract, that `bid > 0` and `volume > 0` are enforced (a mid can pass with
bid=0), that the theta penalty prefers a longer-dated contract when DTE is
short, and that the tie-break is deterministic.

~~`option_backtest.py` and `universe_backtest.py` are the other two with none.~~
**Stale as of 2026-09-11**: `option_backtest.py` gained a coverage-guard selftest
and is wired into CI. Only `universe_backtest.py` still has none — see item 5.

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

## 12. ~~The VRP is measured but the measurement has not been RUN~~ — PASS 2026-09-10

**RESOLVED. The gate opened.** `results/vrp_measurement.md` is the record.

    mean +3.63 vol points, 95% HAC CI [+2.40, +4.85], 83.7% of days positive,
    positive in all 11 years, 2,491 days (~119 independent windows).

All three pre-registered clauses cleared at the CONSERVATIVE end — the HAC lower
bound of +2.40 clears the 2.0 thickness bar on its own, not just the point
estimate. The bar was not toothless: 2018 (+0.56) and 2022 (+1.68) would each
have failed the thickness clause standing alone.

**Three things the pass does NOT license, carried forward as the next items:**

1. **The tail rests on n ~= 1.** All five worst stretches are within twelve days
   of each other (Feb 2020). Ten years contains roughly one true vol
   catastrophe. The HAC correction fixes overlap in the MEAN; nothing fixes a
   left tail estimated from one event. The bar tested existence, breadth and
   thickness — never survivability. Defined risk is therefore non-negotiable,
   and **position sizing is now the binding constraint**, unmeasured.

2. **The edge is in the SIDE, not the TIMING.** A premium positive on 83.7% of
   *all* days and in every year argues for being systematically short index vol.
   It identifies no better or worse day to sell. This repo is a signal scanner
   whose signal has no demonstrated edge (-0.048 R, 1,386 trades). Continuing to
   gate premium selling behind that scanner captures an unmeasured subset of a
   measured quantity. That is a redirection of the architecture and should be a
   decision, not a drift.

3. **The dollar table in the record is indicative, not measured** — flat
   Black-Scholes with no skew. It shows the edge survives two legs of the 8%
   bid-ask ceiling with room (618/608 nets +0.40, 618/598 nets +0.74), which is
   the question that killed the long side. It is not a sizing input.

**Do not treat this as permission to sell premium on single names.** VIX is SPX
implied vol and says nothing about MPC, CRM or CRWD.

The original instructions, kept for reproduction:

`option_backtest.py` prices every contract at `iv_mult = 1.15` — it assumes
implied vol runs 15% above realised. That constant is the short-premium thesis.
It has never been measured.

`vrp_check.py` measures it: VIX (30 **calendar** days implied) against SPY
realised vol over the next 21 **trading** sessions. The bar is pre-registered in
its docstring and was written before any run:

- 95% **HAC** CI on mean premium clears zero — Newey-West at lag 20, because
  the 21-day forward windows overlap and the iid interval is ~4.6x too narrow
  (the run prints both, so the size of the correction is visible), **and**
- at least 70% of sessions show implied > subsequent realised, **and**
- mean premium >= 2.0 vol points.

All three, or it fails. Thin-but-positive is a fail — a 0.5-point premium does
not survive spreads and slippage.

**To run** (network is blocked in the web sessions, so this is a Codespaces job):

```
python vrp_check.py
```

Exit 0 = pass, 1 = fail, 2 = could not measure (fetch failed / too little data).

Costs no reserved data: `data_reservation.check_clean(['SPY','^VIX'])` reports
SPY already contaminated and `^VIX` unknown.

**What each outcome means:**

- **PASS** → the premium exists on the **index**, and building defined-risk
  short-premium structures on SPY/QQQ is worth the work. It licenses nothing
  about single names — VIX says nothing about whether NKE's implied is rich.
- **FAIL** → stop. Do not build spread machinery (two legs, assignment, margin)
  on a premium that is not there. The honest next move is the opposite side:
  if implied is systematically *cheap*, the measured long-option results in
  item 7 are the thing to re-read, not to work around.

Record the result in `results/` the same way `longshort_split.md` records the
directional test — by-year table, worst five windows, verdict — whichever way it
lands. A fail is a result.

---

## 13. Power check BEFORE the build, not after — now a hard rule

Two studies in this repo were built and run before anyone asked whether they
could detect the thing they were looking for:

- `spread_backtest.py` — 83 cycles, detectable +18%/yr. "No CI cleared zero" was
  read as "no edge" when it meant nothing at all.
- `pead_study.py` — 382 paired events, detectable 2.25% against a 2-3% effect.
  Only the negative grading rescued that verdict from being inconclusive.

Momentum was the first to be checked first, and it was **vetoed without writing
code** (`results/momentum_veto.md`): 42 years of data needed, ~20 available.

The rule, in order:

1. state the mechanism — who is on the other side and why do they lose?
2. `python power_check.py` — can the available data detect the expected effect?
3. only then write the study
4. pre-register the bar, including a dose-response clause where the hypothesis
   predicts one
5. benchmark against the dumbest alternative that takes the same risk

Step 2 has a calendar-span mode for anything measured as a periodic return:

```
python power_check.py --mu-month 0.75 --sd-month 6.0 --span-years 20 --strict
```

And the lesson that mode exists to deliver: **sampling more often buys no
power.** Over a fixed span the t-statistic is identical at monthly, weekly or
daily rebalancing. Only more calendar time, lower volatility, or a bigger effect
moves it.

---

## 14. The verifiability screen — read before proposing another hypothesis

`results/verifiability_screen.md`. Years needed collapses to `(z / Sharpe)^2`,
so **Sharpe is the entire filter**. With ~20 years of reachable data the minimum
verifiable Sharpe is **0.63**; with 10 years, 0.89.

The whole classic factor literature sits below it — value 0.30, quality and
low-vol 0.40, momentum 0.45, trend 0.50. Diversifying five of them clears the
bar in principle (0.79 at rho 0.1), but the retail-implementable version (long-
only factor ETFs) falls back under it: long-only captures about half the premium
and the ETFs run rho 0.5-0.7 with each other *because they all hold the market*.

**This is a stopping rule, not a menu.** Anything from the public factor
literature fails on Sharpe before a line is written. Generating candidate #6, #7
and #8 without a new source of edge is motion, not progress.

What would genuinely reopen it: materially more capital (~$100k+, where shorting
and long-short implementations become reachable), a different cost structure,
data that is not free and universal, or an explicit decision to trade unverified
literature — made openly, not smuggled in via an underpowered backtest.

---

## 15. RVOL refuted, tranche A spent, no clean data remains

`results/rvol_confirm_run1.md`. The ρ = +0.90 dose-response that looked like
this project's first real finding was an artifact of the 12 tickers it was found
on. On 16 clean held-out tickers it is **ρ = −0.10**; on the broader
supplements, −0.90 and −0.70. The primary test cleared its pre-registered
sample floor (1,145 against ~1,060), so this is a genuine negative.

**Two lessons worth carrying, both about the guards rather than the result:**

1. **Dose-response did not prevent this — it was passed by the artifact.** Only
   held-out data caught it. Dose-response killed ADX without spending a tranche;
   only a tranche could kill RVOL. Both guards were necessary, neither
   sufficient.
2. **The lowest-volume bucket came back +0.118 R at p = 0.030** — the only
   nominally significant cell, and the opposite of the hypothesis. Fishing would
   now produce "low relative volume predicts returns." The pre-registered bar is
   the only reason that did not happen.

**No clean data remains.** Tranche A and B are both spent. Any future hypothesis
is judged on contaminated data or not at all. Read alongside item 14: the
verifiability screen already found the public factor literature fails on Sharpe
before a line is written. The programme has exhausted both its candidate ideas
and its capacity to test new ones.

`volume_mult = 1.2` is **not** recommended for removal. It sets the "Strong" tag
(with an RSI extreme), not whether a signal fires, and what was measured is RVOL
alone rather than the conjunction. A null licenses neither keeping nor removing;
removing a gate on a non-significant negative is the ADX-35 error with the sign
reversed.

---

## 16. The record is verified on current code — with the fix proven live

`record_recheck.py`, run 2026-09-11. All three recorded cuts reproduce exactly,
**and the 2026-09-10 gap-fill scoring fix demonstrably fired**: 8 / 17 / 42
setups rejected across the cuts, 67 in total.

The arithmetic closes the question that started this: recorded trades equal
passed-gates minus rejections in every cut (426−8=418, 701−17=684,
1428−42=1386). If the 04:21 re-run on 2026-09-10 had missed the 03:23 fixes, the
record would carry the pre-rejection counts. It does not.

**Two predictions of mine were wrong and are recorded as such:**

1. I expected CODE MOVED on at least one cut, reasoning that a 1,386-trade
   sample must contain rescored fills. All three confirmed.
2. I then framed it as "three correctness bugs, all latent on these samples,
   which suggests the data is narrower than the trade counts imply." **Wrong.**
   The gap fix was not latent at all — it rejected 67 setups. The simpler
   explanation was the right one: the earlier re-run genuinely picked up every
   fix, so there was nothing left to move.

Run `python record_recheck.py` after any change to `backtest.py` scoring,
`signal_core.py`, or the price basis. It costs nothing and it is the only thing
that distinguishes a confirmed record from a stale one.

---

## 17. ~~The forward log records into a void~~ — CLOSED 2026-09-16

It persists, it records signals rather than scans, and outcomes attach.

  - `record_signal()` requires the SIGNAL BAR's date and dedupes on
    `(ticker, trend, bar_date)`. Three scans a day of one settled bar used to
    write three rows. The dedupe is in the module, not the caller.
  - `scanner.yml` stages and commits `forward_log.jsonl`. **Order mattered:**
    committing before the dedupe would have written triplicates into an
    append-only chain that can only be abandoned, never cleaned.
  - **Owner chose route 1, 2026-09-16: the SCANNER attaches outcomes.** It reads
    `trade_journal.json` (which the app writes through the Contents API) and
    appends outcome rows itself, so the app never touches the log and the
    single-writer rule holds. `check_forward_log_single_writer()` pins it,
    including `attach_outcomes` as a write.

**The match refuses far more than it accepts, because a wrong row is
permanent.** An append-only log cannot correct a mis-attached outcome, only
contradict it in a note nobody reads. Refused: reconstructed provenance
(`source_backfilled` is owner recollection — BACKLOG 2 says to exclude it),
discretionary trades, unsettled outcomes, a signal dated *after* the position
opened, a signal outside the 5-day window, skipped signals, and **any ambiguous
match** — two candidates are refused outright rather than resolved by
"nearest", which is exactly when a heuristic picks wrong.

**The basis question is settled by making it explicit.** `record_outcome()` now
requires `basis` with no default. `actual_rr` is a return on PREMIUM; a signal's
setup is on the UNDERLYING. The underlying R is not recoverable — `close_position()`
writes stop and target as 0 for option rows — so premium is the only basis that
exists, and it is labelled rather than silently summed with stop-distance R.

**On the journal as it stands today: 0 attached, 8 refused as reconstructed, 1 as
discretionary.** That is correct — every row on file predates the log. The
mechanism starts producing evidence from the next signal-sourced trade.


**CLOSED: it persists, and it records signals rather than scans.**

  - `record_signal()` now requires the SIGNAL BAR's date and dedupes on
    `(ticker, trend, bar_date)`. The scanner runs three times a day and
    `drop_partial_bar()` means all three read the same settled bar, so one
    signal was writing three rows a day. The dedupe is in the module, not the
    caller, so a second caller cannot reintroduce it.
  - `scanner.yml` now stages and commits `forward_log.jsonl`. **Order mattered:**
    committing before the dedupe would have written those triplicates into an
    append-only chain that cannot be cleaned, only abandoned.
  - `check_forward_log_single_writer()` pins both facts.
  - `_log("taken")` runs before the cooldown and before `send_alert()`, so it
    means "the rules produced a tradeable signal", not "a message went out".
    That is the right semantic for this log; it is now written down where the
    caller sees it, rather than left to be rediscovered from a mismatched rate.

**STILL OPEN: `record_outcome()` has no caller, and wiring it is a DESIGN
DECISION, not a missing line.**

`append()` takes `seq = len(rows)+1` and `prev = rows[-1]["hash"]`, so two
processes appending from the same file produce rows with the SAME seq and prev.
Git merges both and the chain does not verify — **permanently**, because an
append-only log whose hashes cover the seq cannot be renumbered to repair it.
Demonstrated in `forward_log.selftest()`.

The obvious caller is `journal_store.close_position()`, which runs in the app on
**Streamlit Cloud** — a different machine from the scanner's Actions runner. So
it cannot simply append. Three routes, and one must be chosen:

  1. the scanner attaches outcomes, reading closed trades from the journal;
  2. the app keeps its own chain file and the two are reconciled offline;
  3. the log moves somewhere with a single writer and real appends.

**A second question to settle first: WHICH R.** `close_position()` computes a
return on PREMIUM; a signal row's `setup` carries entry/stop/target on the
UNDERLYING. Those are different denominators, and recording one against the
other is the same basis error that put a TP+100 win rate against a TP+200
breakeven in `risk_params.py`. Whatever calls it must carry the basis on the row.


**Found 2026-09-16 by review; see `results/code_review_2026-09-16.md` (H1).**

`forward_log.py` is the answer to "there is no clean data left". Four
independent defects mean it produces nothing.

1. **Never persisted.** `scanner.yml` commits `scanner_state.json` and nothing
   else, so `forward_log.jsonl` dies with the Actions runner. The file is absent
   from the repo. `.gitignore` states the opposite intent verbatim — "Git is the
   external anchor: committing the log after each scan makes a truncation
   visible as a deletion in the diff" — and there is no such step.
2. **Logged per scan, not per bar.** The cron fires three times a day and
   `drop_partial_bar()` means all three read the SAME settled bar, so one signal
   writes three identical `taken` rows daily. The 4h cooldown does not dedupe
   it: `recently_alerted()` is checked in `run()`, AFTER `analyze()` has already
   written.
3. **No signal bar date on the row.** `_setup` carries no date and `ts` is the
   wall-clock write time, so duplicates cannot be collapsed afterwards and no
   row can be joined to its bar.
4. **`record_outcome()` has zero callers.** Nothing attaches an outcome to a
   logged signal. The record can never answer its own question.

**DO NOT FIX (1) FIRST.** Persisting now would commit three duplicate rows per
signal per day into an APPEND-ONLY hash chain, which by construction cannot be
cleaned — the log would have to be abandoned and restarted, spending the one
advantage a forward record has. Order:

1. Add the signal bar date to `_setup` (`df.index[-1]`, already in hand).
2. Dedupe on `(ticker, trend, bar_date)` inside `record_signal()` — in the
   module, not the caller, so a second caller cannot reintroduce it.
3. Wire `record_outcome()` into `journal_store.close_position()`.
4. THEN add the commit step to `scanner.yml`.

Also decide what `taken` means. `_log("taken", "")` runs before the cooldown
check and before `send_alert()`, so it currently means "would have alerted".
That is arguably the right semantic for measuring what the RULES produced, but
it is not what the field name says.

---

## 18. ~~`longs_only.simulate()` books P&L at entry~~ — CLOSED 2026-10-08, RE-RUN DONE

**Re-run by the owner 2026-10-08 on a laptop with Yahoo access:
`results/portfolio_replay_run2.txt`.** On the correct OOS twelve, 10y to
2026-10-08: idealised +47.7% total (+4.0%/yr, 45.6% DD, 883 trades, 35.4% wins),
constrained +39.7% (+3.4%/yr, 19.0% DD, 687 trades), SPY +259.6% (+13.7%/yr,
34.1% DD). The entry-time artifact run 1's banner predicted ("peak capital
deployed 101%") is gone: 100%. Owning the index was better on both axes. One
line of that file's prose quoted the record's +0.085 R instead of this run's
+0.076 R; `longs_only.report()` now measures and prints the run's own long edge
(`long_edge()`), with the record quoted as the record. Run 2 predates the
gapped-stop fix (21); run 3, same day, same cache, is on the fixed engine:
constrained +1.7%/yr at 21.8% drawdown, long side +0.042 R. See 21.

**The accounting is fixed and pinned. The published numbers are not yet replaced.**

P&L is now carried on the position and realised at its EXIT date, so the equity
a trade is sized on contains every result known on its entry date and nothing
else. Anything still open at the sample end settles in exit order rather than
vanishing. The per-year table counts and pays a trade in the year it CLOSED, or
its two columns describe different trades.

Pinned three ways in `longs_only.selftest()` and falsified by restoring the
entry-time booking: the overlapping-trade case (10,400.00, not 10,395.00), the
deployment ratio (never above the 100% the constraint enforced — this is what
"101% of equity" in the old run actually was), and the still-open-at-end case.

**OWED: `python longs_only.py` in a session that can reach Yahoo.**
`results/portfolio_replay_run1.txt` is marked VOID in place and states that the
code has moved underneath it. Every number in it changes — four curves, both
CAGRs, both drawdowns, and the "49% survives" claim about the break-even rule.
Until that run exists, quote nothing from it.


**Found 2026-09-16 by review; see `results/code_review_2026-09-16.md` (H2).**

`simulate()` walks trades in ENTRY-date order and does `equity += pnl` in the
iteration that OPENS the position, while keeping it in `open_pos` until its
`exit_date`. A trade's result funds the account before it has happened.

Reproduced offline, two overlapping trades on a $10,000 account at 1% risk:

```
r=+5.0 entered 01-02 exits 03-01 ;  r=-1.0 entered 01-03 exits 03-02
got      10,395.00     (trade 2 sized on 10,500 — trade 1's unrealised +500)
correct  10,400.00     (P&L booked at exit)
```

Three consequences, all present in `results/portfolio_replay_run1.txt`:

- **Compounding lookahead.** Peak concurrency in that run is 5, so every
  overlapping trade is sized on the unrealised results of its neighbours.
- **The capital constraint is looser than it claims.** `deployed + value >
  equity` tests against an equity inflated by open positions' unrealised gains.
  The idealised-vs-constrained gap is the module's stated finding and it is
  understated.
- **Drawdown is mis-dated and mis-sized.** `curve` is indexed by entry order
  with each trade's full P&L landing at its open. The module calls drawdown
  "the number worth trusting most in this file".

`peak capital deployed 101% of equity` in that run is a symptom of the same
mechanism, not real over-deployment: `peak_deployed` divides by an equity that
has ALREADY absorbed the new trade's loss.

**The fix and the re-run are one change.** Realise P&L on the exit-date event
and build the curve on those events. Every number in
`results/portfolio_replay_run1.txt` moves — four curves, both CAGRs, both
drawdowns, and the "49% survives" claim about the break-even rule. Do not ship
the code without the re-run; a committed result that disagrees with the code
that produced it is worse than either alone.

**Containment, for the record:** R-multiples are untouched, so `exit_ab`,
`drift_null` and the tranche C confirmation are unaffected — the same
containment as the `stop`/`orig_stop` regression in #89.

---

## 19. ~~An EXIT_SIGNALLED position is never monitored again~~ — CLOSED 2026-09-16

**Owner picked the fail-safe design 2026-09-16.** An `EXIT_SIGNALLED` position
stays monitored and re-alerts only on a reason that OUTRANKS the one already
sent, ranked by `check_option_position()`'s own evaluation order
(`EXIT_PRIORITY`: STOP, TARGET, TIME, HOLD, THESIS).

  - STOP after a declined THESIS **alerts** — the case the gap was losing.
  - STOP after TARGET **alerts** — you did not take the profit and it turned.
  - THESIS after STOP, or the same reason twice, stays **silent** — the hourly
    duplicate `exit_alerted` was added to stop.

The earlier verdict is appended to `exit_history` rather than overwritten: a
position that went THESIS then STOP is a different history from one that only
ever stopped. An unrecognised previous reason ranks LOWEST, so a status this
module does not know cannot suppress a stop.

Pinned by tests that drive `run()` end to end, not the comparator — the bug was
never in the ranking, it was in which positions `run()` looked at, and a unit
test on `outranks()` would have passed throughout. Falsified two ways.


**Found 2026-09-16 by review; see `results/code_review_2026-09-16.md` (H3).**

`exit_monitor.run()` selects `status == "OPEN"` and skips everything else. The
first rule to fire flips the position to `EXIT_SIGNALLED` and it leaves
monitoring permanently. `app.py` lists those positions under "close these" with
no dismiss and no re-arm, and nothing in the repo ever sets `status` back to
`OPEN` — the only assignment is in `journal_store.open_option_position()`.

The failure case is ordinary. Rule priority is STOP, TARGET, TIME, HOLD,
THESIS. THESIS fires on one close through EMA20 and HOLD on a session count;
both are judgement calls the owner may reasonably decline. The moment either
fires, **the STOP rule is dead for the life of that position** — on a long
option where the premium is the entire maximum loss.

Same shape as the gap that widened the exit-monitor cron (a position last
checked Friday, exited at −71.7% against a −50% rule), except unbounded.

Two designs, and the choice is the owner's:

- Keep monitoring `EXIT_SIGNALLED` positions for HIGHER-PRIORITY reasons only —
  a STOP after a THESIS re-alerts, a THESIS after a THESIS does not; or
- Add an explicit "keep it open" control that restores `status: OPEN` and clears
  `exit_alerted`, making an override a logged act.

The first fails safe without requiring the owner to be present, which is the
property this monitor exists for.

---

## 20. ~~The earnings blackout is a second duplicated gate implementation~~ — CLOSED 2026-09-16

**Merged into `market_context.py`, the module that already owns the weekly trend
and the SPY regime.** Both callers delegate; the window arithmetic exists in one
place; `check_earnings_gate_shared()` pins all three facts and was falsified
three ways (single-date read, a caller dropping the delegation, a caller
re-growing its own window).

**The window semantics already agreed** — app's two branches combined to exactly
the scanner's single inclusive test — so a constants check would have passed.
The divergence was in PARSING: yfinance returns "Earnings Date" as a range of
two estimates more often than not, and app read only the first element. Reading
every date is the stricter rule and the one kept.


**Found 2026-09-16 by review; see `results/code_review_2026-09-16.md` (M3).**

`app.check_earnings_blackout()` and `scanner.check_earnings_blackout()` are
independent implementations of one rule, and they already differ: **app reads
only the FIRST earnings date** (`get_next_earnings()` returns a single value)
while **scanner iterates every date** in the calendar. On a ticker whose
calendar returns more than one, the two gates disagree — the app-vs-scanner
divergence `signal_core.py` was created to end, in a gate nobody moved.

`consistency_check` pins `is_market_open()` across four copies, `compute()`
across modules, and the unsettled-bar decision across five call sites. Nothing
pins this one.

The durable fix is the one already applied twice here: one implementation,
inputs injected by the caller (each fetches through a different rate limiter),
both callers importing it, and a `check_` function that pins it.

---

## 21. ~~The share backtest fills a gapped stop AT the stop — every loss is floored at −1 R~~ — CLOSED 2026-10-08, FIX AND RE-RUN TOGETHER

**Found 2026-09-16 by review. Fixed 2026-10-08; the re-run is the owner's next
local step.**

- **Code.** `simulate_trade()` fills a stop gapped through at the worse of the
  stop and that bar's open, a target at the better; a touched level with the
  open inside still fills at the level. Tallied as "gapped through a level" in
  `run()`'s summary. Same rule as `forward_log.resolve_signal()`. Selftest:
  −5.00 R on a 90 open against a 98 stop, +5.00 R on a 110 open against a 106
  target, both sides mirrored, ordinary stop-outs still −1.00 R.
- **Baseline.** The owner's 2026-10-08 run on unchanged code
  (`results/record_recheck_baseline_2026-10-08.txt`) could only say DATA
  CHANGED: the windows are relative to today, so the record's fingerprints are
  unreproducible three weeks on. Its three fingerprints are now pinned as
  `record_recheck.BASELINE_2026_10_08`, and `record_recheck` judges a run
  against whichever pin its fingerprint matches. A re-run on the same laptop
  hits `.bar_cache/`, reproduces those fingerprints, and reports CODE MOVED
  against the baseline — the isolated effect of this fix.
- **Re-run done 2026-10-08 by the owner on the same cache**
  (`results/record_recheck_gapfill_run1.txt`): every fingerprint matched the
  baseline, verdict CODE MOVED on all three cuts, so the table below is the
  fix and nothing else.

  | cut | avg R before → after | long R | short R | gapped through |
  |---|---|---|---|---|
  | 7t/5y | −0.004 → −0.004 | +0.260 → +0.297 | −0.466 → −0.532 | 73 of 413 (18%) |
  | 12t/5y | −0.022 → −0.043 | +0.101 → +0.082 | −0.240 → −0.265 | 113 of 675 (17%) |
  | 12t/10y | −0.056 → −0.088 | +0.076 → +0.042 | −0.290 → −0.319 | 216 of 1381 (16%) |

  One trade in six had a level gapped through. The record's 10y long side —
  the number `longs_only_run1.md` called the one worth trusting most — halves,
  from +0.076 R to +0.042 R, CI [−0.093, +0.177]. Longs-only replay on the
  fixed engine (`results/portfolio_replay_run3.txt`): idealised +4.0%/yr →
  +0.3%/yr at 55.7% drawdown; constrained +3.4%/yr → +1.7%/yr at 21.8%; SPY
  +13.7%/yr at 34.1%. The decision not to trade the signal was already made;
  this makes it by a wider margin, and from the left tail specifically.
- **Every committed result built on the share engine carries a STALE FILL RULE
  banner** (25 files) rather than a VOID: their findings are signs and
  dose-responses, which the rule change does not undo, but none of their R is
  on the current engine. Each is re-run on demand before being relied on.

`simulate_trade()` checks `if lo <= stop` and then exits at `stop`. When a bar
GAPS THROUGH the stop, a real stop order fills at the OPEN, not at the trigger.
Reproduced on a long entered at 100 with a stop at 98:

| bar opens at | recorded R | real-fill R |
|---:|---:|---:|
| 95 | **−1.00** | −2.50 |
| 90 | **−1.00** | −5.00 |
| 80 | **−1.00** | −10.00 |
| 40 | **−1.00** | −30.00 |

**No trade in the share engine can be worse than about −1 R, at any gap size.**
The three exit paths are: stop → exactly −1 R; target → +rr; timeout → marked to
the close, which cannot be below the stop because the low would have exited
first. Reality has no such floor.

**Direction of the bias, stated honestly.** The target side has the mirror
defect — a bar gapping past the target fills at the target when a real limit
would fill better — so this is not purely one-directional. But the two are very
unequal in practice: **the record's** stop sits at 1.0 × ATR and its target at
3.0 × ATR (`bt.DEFAULTS`; the live 1.25 / 4.0 is a different configuration — see
item 23), and an overnight gap through 1.0 ATR is ordinary while one through
3 ATR is rare. *(Corrected 2026-10-07: this originally quoted the live geometry.
The argument strengthens — a 1.0 ATR stop is easier to gap through.)*
The net effect flatters the strategy, and it flatters the LEFT TAIL specifically
— which is the part `longs_only.py` calls "the number worth trusting most in
this file".

**This is not the same thing as the entry-bar group already recorded** in item 4
and `results/longshort_split.md`. That is about how OFTEN trades gap into their
stop (17.5%, averaging −0.770 R). This is about what each one is WORTH when it
does. The printed reminder "Stops are not guaranteed (overnight gaps)" is a
caveat about live trading; it does not say the backtest caps the loss.

**`option_backtest.py` does NOT have this defect** and is the model to copy: it
computes `pnl_pct` from the actual mid at the exit bar and books THAT, so a −50%
rule can and does record −72%. Its sweep table's avg loss of −45.1% at SL−50 is
a real distribution, not a threshold.

### Why it was not fixed in the session that found it

Fixing it moves EVERY R-multiple in the repo: the 1,386-trade record, all three
cuts, tranche C, `exit_ab`, `drift_null`, `atr_stop_test`, `inverted_arm`, the
feature sweeps. Per `CLAUDE.md`, the fix and the re-run are ONE change, and that
session could not reach Yahoo to produce a single re-run. Shipping the fix alone
would have left every file in `results/` disagreeing with the code that made it.

### How to fix it, when there is market data

In `simulate_trade()`, on a stop hit take `exit_px = min(stop, Open[j])` for a
long and `max(stop, Open[j])` for a short — the fill is the worse of the trigger
and the open. Mirror it on the target with the better of the two. Then re-run
`record_recheck.py` FIRST: it is the module that says whether the record still
reproduces, and it will show exactly how much of the recorded edge was this.

Expect the expectancy to get worse, not better. That is the point.

---

## 22. ~~The unattended system runs at 14–23% of its schedule~~ — CLOSED 2026-10-08

**The external trigger is live.** Two cron-job.org jobs, verified end to end on
2026-10-08: each test fired a `workflow_dispatch` that GitHub created and started
**in the same second** (scanner 00:20:30Z, exit monitor 00:27:29Z), against the
`schedule` trigger's 2–4 h delays.

| job | fires (America/Los_Angeles) | = ET | target |
|---|---|---|---|
| `trading-scanner` | 8:07, 10:07, 12:07 Mon–Fri | 11:07, 13:07, 15:07 | `scanner.yml` |
| `exit-monitor` | 6:00–13:30 every 30 min Mon–Fri | 9:00–16:30 | `exit-monitor.yml` |

The jobs run in Pacific time because that is the account's timezone; LA and New
York are always exactly three hours apart and both observe DST, so the offset
never drifts. The two monitor slots outside 9:30–16:00 ET are discarded by the
script's own guard. The doubled `schedule` crons in both YAMLs stay as backup.

**FORCING FUNCTION — TWO PATs expire the same week: 2027-01-06 and 2027-01-05.**
(1) The dispatch token: fine-grained, this repo only, Actions: read+write (the
response header `x-accepted-github-permissions: actions=write` confirmed
exactly that scope), expires **2027-01-06**. When it expires every dispatch
returns 401, both jobs go silent, and the only thing standing between that and
a repeat of the three-week outage is the coverage alarm, which pages within two
trading days. (2) The app's token: fine-grained, this repo only, Contents:
read+write, Streamlit secret `GITHUB_TOKEN`, issued 2026-10-13 after its
predecessor died unnoticed (every app write failed from 2026-09-30 to
2026-10-13 behind a green sidebar; see the hand-off note), expires
**2027-01-05**. When it expires the app reads from the container's disk and
every save fails, with the red sidebar box only after the next rerun.
**Rotate BOTH in the last week of December 2026**: new dispatch token into both
cron-job.org jobs' `Authorization` header, new app token into Streamlit
secrets, then one dispatch and one app save to prove each. Nothing in the repo
holds either token.

The in-repo half — the alarm, the heartbeat, the pins — shipped in #96 and is
recorded below as it stood on 10-07.

---

### Code side, as closed 2026-10-07

**Done in the repo (PR #96):**

- `scanner.py --coverage` — the alarm for silence. Reads the committed forward log's
  last `scan` row and the position file's `last_check_epoch`; pages once a day when
  no scan has evaluated a bar for 2 trading days or an open position has gone a
  session unchecked. **Runs on every workflow run via `if: always()`**, including
  the late ones that cannot scan — which is the only place an alarm for a skipped
  run can live. Against the real state on 10-07 it fires.
- Both crons doubled to two slots an hour (`7,37` / `0,30`). More slots are more
  chances; the guard discards the late ones; `concurrency` serialises collisions.
- The Option A runbook is in `scanner.yml`'s comments, next to the existing
  one-time setup. **The dispatch endpoint was proven from an API token on 10-07:
  created and started in the same second**, against scheduled runs 2–4 h late.
- `check_unattended_workflows_pinned_and_alarmed()` pins the alarm step to
  `always()`; falsified.

**Still the owner's, and the part that actually fixes the cadence:** a fine-grained
PAT (this repo only, Actions: read+write) in an HTTP cron service, three jobs for
`scanner.yml` at 11:07/13:07/15:07 America/New_York and hourly 09:30–16:00 for
`exit-monitor.yml`. Until then coverage stays ~1 run/day — but it is now *audible*.

**Open design question, deliberately not decided here:** the guard refuses to
scan after 16:00 ET, yet for a daily-bar signal post-close is the best time —
today's bar is settled and `drop_partial_bar` already keeps it. A 16:00–18:00
*recording* window (alerts still gated to market hours, since option quotes go
stale) would have turned most of the 11 wasted runs into logged bars.

---

### The finding as recorded on 2026-10-07


**Found 2026-10-07 from GitHub's own run list; see `results/code_review_2026-10-07.md` (C1).**

| workflow | scheduled | in session | since 10-02 |
|---|---:|---:|---|
| Trading Scanner | 48 | **11 (23%)** | **0 of 3 trading days** |
| Exit Monitor | 112 | **16 (14%)** | 1/day; 0 on 10-05 |

GitHub's scheduler delivers runs 2–4 h late and drops slots. A run landing after
16:00 ET hits the market-hours guard, runs "Skip notice", and the workflow reports
`success`. The 11:07 ET scanner slot has fired **zero** times in three weeks. The
monitor's only in-session run on FOMC day was 13:28 ET — before the decision.
AAPL 355C was found at −82% on its fourth check in six days; PFE 29C at −81% on
its second.

**`is_total_outage()` cannot see this** — it fires on tickers failing *inside* a
run, and a run that never scans is invisible to it. Silence identical to a quiet
market, which is the failure this repo names as its worst.

External cause confirmed: GitHub community #207346 and #206019 report dropped and
delayed scheduled runs since 2026-08-26, manual dispatch unaffected.

**Remedy, in order:**
1. External trigger (any cron host) calling `workflow_dispatch` on both
   workflows; keep `schedule` as backup. The inputs already exist.
2. A positive heartbeat — item 26 — and an alert when no in-session scan has
   happened for N trading days.
3. The coverage table above as a weekly script, so this is measured, not noticed.

None of this makes the stop a stop. It makes the silence audible.

---

## 23. The live config and the research config are different, and nothing pins them — PINNED 2026-10-08, DECISION OPEN

**Found 2026-10-07; see `results/code_review_2026-10-07.md` (H1).**

**Code half done 2026-10-08.** `backtest.RESEARCH_VS_LIVE` declares every
diverging key with both values and a reason; `backtest.CFG_TO_PARAMS` names the
cfg→`SignalParams` mapping; `check_research_config_divergence_declared()`
asserts the declaration is complete (a diverging key must be listed), exact
(listed values must be the values as they are), not stale (a listed key must
differ), that the mapping matches `build_signal_params()`, and that no comment
in `backtest.py` calls a research value an app.py default. Falsified five ways.
The stale comments are gone. **Still open: which config the next research run
uses.** That is a pre-registration decision (see 24) and belongs to the owner.

| | `adx_min` | stop | target | `volume_mult` | regime |
|---|---|---|---|---|---|
| **live** (`signal_core.DEFAULTS`) | 35 | 1.25 | 4.0 | 1.2 | on |
| **record** (`bt.DEFAULTS`) | 25 | 1.0 | 3.0 | 1.0 | off |

`record_recheck.py:91` builds every recorded cut from `bt.DEFAULTS`. So the
1,386-trade record and everything derived from it — `longshort_split`,
`drift_null`, `exit_ab`, `atr_stop_test`, `inverted_arm`, `longs_only`, tranche C
— measure a configuration the scanner does not run. The scanner runs the OOS
`FROZEN` config, which failed. Neither supports trading; the decision is
unchanged. But the forward log records the *live* config's signals, and the two
cannot be compared without stating this.

`bt.DEFAULTS`'s comments say `# app.py default`. They are stale.
`check_duplicated_constants` matches `NAME = value` at module level and cannot see
a dict literal. **Fix:** a `check_` that asserts either `bt.DEFAULTS` ==
`signal_core.DEFAULTS` on the shared keys, or that the divergence is declared in
one named constant with a reason — then decide which config the *next* research
run should use, and say so in its pre-registration.

---

## 24. ~~The alert tier is gated on refuted features, and the forward test is starving~~ — DECIDED 2026-10-08, RECORD CHANGED

**Found 2026-10-07; see `results/code_review_2026-10-07.md` (H2, H5).**

**Owner decision 2026-10-08: record the base signal, tag the HQ tier.**
Pre-registered in `results/forward_record_preregistration.md` before the first
row under the new rule. A long base signal is now `taken`; every row carries
`hq: true|false` and `setup.hq_fail` (the legs that failed). Alerts still fire
on the HQ tier only; the direction gate is unchanged; the config fingerprint is
unchanged. The 19 existing `skipped: not high-quality` rows are read as
`taken, hq=false` — fixed in the pre-registration, not after. H1 bar: the tier
adds ≥ +0.15 R per episode; 349 settled episodes needed; ~3 years at 8 names,
~6 months at ~50. **The record still has no outcome measurement** — see 28.
The universe-size half of this item (7 of 8 names rotated in three weeks,
`churn_tracker` has no cumulative view) is NOT addressed and stays open below.

17 base signals in three weeks on 8 tickers; **0 taken**. 13 blocked by
`ADX < 35`, 4 by the volume ≥ 1.2× leg of "Strong". `adx_retest.py` found ADX adds
nothing; `rvol_confirm_run1.md` found RVOL refuted out-of-sample. TMO logged a
Bullish setup on 8 consecutive bars (ADX 30.7 → 43.1, RSI 67 → 75) and never
alerted — blocked by ADX until it crossed 35, then by volume.

This is not "the gates are wrong because they filter noise" — removing them yields
more signals of the same measured non-edge. It is that **the forward log is the
only remaining route to evidence, and gating its intake on refuted features
starves it.** At 0 taken per 3 weeks it will not reach BACKLOG 13's power in years.

Compounding it: the universe is 8 names and rotated **7 of 8** in three weeks
(09-27 swapped 4). AMD, CRWD, NVDA were in for exactly one week; their signals
appear and the names leave. `churn_tracker.verdict()` flags only a mean interval
turnover > 50% and has no cumulative view. BACKLOG's own arithmetic says ~50
tickers are needed.

**Decision for the owner, not a fix:** the forward log can record the *base*
signal (every row it already writes) and tag the HQ tier as a field, rather than
gate on it — the record then measures what the rules produce and what the gate
would have selected, without the gate deciding what gets recorded. Pre-register
before changing.

---

## 25. The option engine prices every contract at one constant, guessed IV — PART A DONE, PART B RUNNING (first reading 2027-04-08)

**Found 2026-10-07; see `results/code_review_2026-10-07.md` (H3).**

**Pre-registered in `results/option_iv_preregistration.md` (2026-10-08, before
any run).** Two measurements on two clocks: **Part A**, a sensitivity sweep at
`iv_mult` 1.00 / 1.15 / 1.30 / 1.50 on the laptop, read for ranking and sign
invariance and the range of `OPT_WIN_RATE`; **Part B**, a forward single-name
premium from daily ATM-IV snapshots, first read at six months with its SE.
Historical single-name IV is not available from this data source, which is
why B is forward. A path-dependent IV model is explicitly out of scope.

**Part A run 2026-10-08 by the owner (`results/option_iv_sensitivity_run1.md`).**
Ordering of take-profit levels by win rate: invariant across 1.00–1.50.
Sign of expectancy at TP ≥ +100: **not** invariant — positive at 1.00, negative
at 1.15 and up. So the verdict on the live TP+200 / SL−50 structure is the
constant's, not the data's, and is UNMEASURED until Part B reads. +50 and +75
stay refuted at every multiplier. `OPT_WIN_RATE` at TP+100 / SL−50 spans
27.7% → 20.6% across the bracket. The multiplier now has one home
(`risk_params.OPT_IV_MULT`), the table is `OPT_WIN_RATE_BY_IV_MULT`, and
`check_option_iv_single_source()` pins both to the committed files. The
pre-registration is frozen from this run. **Part B BUILT 2026-10-08:**
`iv_snapshot.py` (stdlib; `record()` with counted refusals, `grade()` after 21
sessions, `report()` with per-name and pooled HAC SE and the 126-session
reading gate), taken by the scanner's post-close pass only
(`fetch_atm_chain()`, nearest 21–45 DTE expiry, ATM = nearest strike, call/put
IV mean, vol points), committed by `scanner.yml`, pinned by
`check_iv_snapshot_single_writer_and_committed()`. **The six-month clock starts
with the first post-close run after merge; first reading no earlier than
2027-04-08.** `python iv_snapshot.py --report` is the offline view.

`simulate_option_trade()`: `iv = realised_vol(20d) × 1.15`, held constant for the
life of the trade. No vol-of-vol, no IV crush, no skew, no term structure.
`vrp_measurement.md` later measured +3.63 vol points — **on SPX** — and states
that it does not transfer to single names. `option_backtest.py` runs on seven
single names and does not cite the measurement.

Downstream: `OPT_WIN_RATE = 0.249`, the realised-breakeven table, the spread
ceiling's rationale, the app's EV panel. The two live positions that went from a
−50% rule to −82% realised are the premium gap a constant-IV path cannot produce.

**Fix requires market data and a design choice:** at minimum, record the IV
assumption on every option-backtest result and derive `iv_mult` from a single-name
measurement rather than SPX. A path-dependent IV (even a simple spot–vol
correlation) changes every option number in the repo; the fix and the re-run are
one change.

---

## 26. ~~The forward log cannot tell "no signal" from "no scan"~~ — CLOSED 2026-10-07

`forward_log.record_scan()` writes one `scan` row per bar evaluated, per run —
bar, tickers, n_signals, config. **Never deduped**: two scans of one bar are two
facts, and each is evidence the scanner ran. Written on dry runs too. `last_scan()`
is what the item-22 alarm reads. `summary()` reports `scans` and `bars_scanned`.
Falsified by removing the call from `run()`.


**Found 2026-10-07; see `results/code_review_2026-10-07.md` (H4).**

Bars 09-30, 10-02, 10-05, 10-06 have no rows — not because nothing fired, because
the scan that would have evaluated each bar landed after the close and skipped. A
missing row means either, and the record built to be the clean record cannot say
which.

**Fix:** a `scan` row per executed run — bar evaluated, tickers, n_signals,
config fingerprint. Absence of signal becomes a positive fact; absence of scan
becomes visible; item 22 gets its heartbeat. Small, offline-testable, and it is
the dedupe's natural companion: `record_signal` already keys on the bar.

---

## 27. Infrastructure and statistical-method debt

**Found 2026-10-07; see `results/code_review_2026-10-07.md` (M1–M6).**

- ~~**Production runs versions CI never tested.**~~ **DONE 2026-10-07.** Both
  workflows now `pip install -c requirements.txt <pkgs>` — a constraints file, so
  only the named packages install, at the pinned versions, and pins have one home.
  `check_unattended_workflows_pinned_and_alarmed()` enforces it; falsified.
- **The price fallback has never been active.** `TIINGO_API_KEY` is empty in both
  workflows. Yahoo is the single point of failure for scanner, monitor and app.
- ~~**`market_context` imports `backtest`**, so the scanner's import closure includes
  a 2,197-line research module.~~ **OVERSTATED — corrected 2026-10-08.** The
  import is inside `market_context.selftest()` only (`market_context.py:489`),
  not at module level. The scanner's import closure does NOT include `backtest`;
  the parity check runs under `--selftest` alone. Nothing to move. The review
  doc carries the same correction in place.
- **No DSR/PBO on the selection step.** The Aug 2026 sweep tried ~60 configs on
  5y × 7 tickers; Bailey & López de Prado's guidance is ≤45 before overfit is
  expected. Holm is applied within later studies, never to the sweep that chose
  the live parameters. Confirmatory — the OOS failed — but it is the one
  undocumented statistical act in a pre-registered project.
- **`oos_validate.two_point_variance()`** rebuilds variance from WR/E/PF and
  admits it inflates the t-stat; `bt.run()` returns per-trade R. Compute it.
- ~~**Node 20 deprecation** on `actions/checkout@v4`, `actions/setup-python@v5`.~~
  **DONE 2026-10-08.** All four workflows now pin the Node 24 majors:
  `checkout@v5`, `setup-python@v6`, `upload-artifact@v5`.
- ~~**`attach_outcomes` nags** the NKE `no_match` every run forever; add an age
  cutoff.~~ **DONE 2026-10-08.** A trade opened before the log's first signal
  bar is counted under `predates_log` and not reported — there is no action
  that resolves it. A trade opened on or after the first bar is matched as
  before. Falsified both ways.
- ~~**`scanner_state.json`** never prunes.~~ **DONE 2026-10-08.**
  `scanner.prune_state()` drops epoch stamps older than 30 days (the longest
  cooldown is 20 h) on every `run()` and `--coverage` pass. Falsified.
- ~~**Win rate counts dust** (NKE +0.04 is a WIN; the 0.05 R floor applies to PF
  only).~~ **DONE 2026-10-08.** One floor, `journal_store.DUST_R = 0.05`, for
  the win rate, the averages and the profit factor: a WIN or LOSS inside ±0.05 R
  is `dust`, stays in the total and in `total_r`, and is neither a win nor a
  loss anywhere a rate is computed — the same treatment BREAKEVEN gets. The app
  says so under the Win Rate metric. Falsified both ways.
- ~~**Stale `# app.py default`** comments on `bt.DEFAULTS`.~~ **DONE 2026-10-08**
  — see 23.
- **Post-close record-only pass, added 2026-10-08** (not a review finding; a
  consequence of 26). During the session every scan evaluates *yesterday's* bar,
  so a signal that forms at the close was first recorded by the next day's
  11:07 scan — or later. `scanner.py --record-only` runs in the two hours after
  the close, when today's bar is settled, and writes its signal rows and `scan`
  heartbeat; no trade alert, no chain fetch, no cooldown stamp. The next
  session's first scan alerts as before (the cooldown does not read the log;
  the log dedupes on bar date). Wired in `scanner.yml` behind the guard's
  `postclose` output, with a backup `schedule` slot; the primary trigger is a
  **fourth cron-job.org job at 16:20 America/New_York, Mon–Fri** (owner to
  create). `check_post_close_record_wired()` pins both halves. Also fixed with
  it: `n_signals` on the scan row now counts bars on which the rules produced a
  signal, taken **or** skipped, matching the rows `record_signal()` writes; it
  used to count only the alert-worthy ones under a name that said otherwise.

**Testing session 2026-10-13, at the owner's request ("thorough testing of
our code/logic").** Run: all 34 selftests, 45 checks, every module
compiled and linted, both chains verified, the coverage alarm dry-run.
Added, as scratch tests not committed as modules: an ENGINE DIFFERENTIAL
(`backtest.simulate_trade` against `forward_log.resolve_signal` on 596
random daily paths with random long and short geometries: zero
disagreements in outcome or R at the grader's recorded precision; both
void the same gapped fills) and RANDOMIZED PROPERTIES of
`signal_core.long_levels` and `pullback_signal.evaluate_pullback` (300
random frames: stop < entry < target, stop within 1.25 ATR and target
within 4 ATR to the cent, every refusal counted, the SMA the raw frame's).
A high-effort code review of the day's diff (`6443daf..3090a56`) returned
eleven findings, all fixed the same day: the pool's once-a-day stamp is now
written only when its alerts were delivered (a Telegram failure leaves the
pool due); the record's headline HQ-versus-base numbers and `by_basis` are
the LIVE bucket's alone; `analyze()` tests the base condition before
fetching the earnings calendar (the pool scan no longer fetches ~75
calendars for bars that cannot signal); `relative_strength()` ranks on
settled, date-aligned closes; the pullback refusals are tallied per run;
`daytrade_paper` reuses `forward_log.append(path=…)` instead of a copy,
refuses to log onto a cut chain, and decides same-day calls before any
fetch; a stale-warning constant derives from the void threshold; the
intraday null-window wording is corrected by dated notes; a discarded
bounds lookup is gone. The single-writer check now keys on a
forward_log.append() call WITHOUT an explicit path.

---

## 28. ~~The forward record has intake and no grading~~ — BOTH PIECES BUILT 2026-10-08

**Opened 2026-10-08 with the decision on 24.** Piece 1 is pre-registered in
`results/forward_grading_preregistration.md` (2026-10-08, before any code) and
**BUILT the same day**: `forward_log.resolve_signal()` (pure, stdlib) and
`grade_open_signals()`, wired into `scanner.run()` after the scan loop.
`record_outcome()` now keys one outcome per (`ref_seq`, `basis`); `summary()`
has no pooled mean. `check_outcome_bases_never_pooled()` pins it. The first
run happens on the next scanner dispatch; its rows are then written up as
`results/forward_grading_run1.txt` from the committed log. The document is
frozen from that first row. **Piece 2 BUILT 2026-10-08:** every Telegram alert
prints `Signal bar: YYYY-MM-DD`; the app's three logging sites pass
`signal_key` (the scan-result quick log derives it from the frame, the manual
form and the contract checker ask for the date when the source is "system
signal"); `open_option_position()` stores it, `close_position()` carries it to
the journal row, and `attach_outcomes()` matches a keyed row EXACTLY — a key
naming a skipped, missing or later-bar signal is refused with the reason, and
an unkeyed row falls back to the five-day heuristic unchanged.
`check_signal_key_round_trip()` pins the chain end to end. Item closed.

**Gap found and closed 2026-10-09, before any outcome row existed.** The first
grading run (10-09 11:07 ET) wrote nothing: `grade_open_signals()` graded only
`decision == "taken"` rows, and the 19 pre-tag rows are `skipped: not
high-quality`. The record pre-registration had fixed that those rows "count as
taken, hq=false for every analysis"; the code did not say so. Now
`forward_log.counts_as_taken()` does — dated (`bar_date < 2026-10-08`) and
literal (that one reason) — and the grader, the journal attachment, the
outcome guard and `summary()` all read through it. A `record_only` dispatch
input on `scanner.yml` runs the post-close pass on demand, so the 19 rows were
graded the same evening rather than on Monday.

**First grading run 2026-10-09 22:20 UTC (`results/forward_grading_run1.txt`).**
24 eligible signals, 12 settled, 12 still inside their windows, 0 void. Every
graded row is hq=false. Rows: mean −0.60 R (3 wins, 9 losses). Episodes, the
pre-registered unit: 5 settled of 12, mean −0.71 R. **Not a result** (5 against
349). Descriptive by-products: no stop exit was gapped through, so the real-fill
rule moved nothing on stops here and improved the three target fills; base
signals carry small planned R:R (0.5–0.9 on the winners), so a target books well
under +1 R against a −1 R stop; TMO's seven consecutive bars are one episode.
No hq=true signal has fired since the log began, so the H0/H1 comparison has no
hq arm yet.

Outcome rows exist only through `attach_outcomes()`, which matches journal
trades the owner actually took. Base signals are not alerted, so they are not
traded, so they never get an outcome that way. Until something grades them the
record measures arrival, not edge.

Two pieces, each needing its own pre-registration before a row is written:

1. **Mechanical resolution** of every `taken` signal against later bars on the
   `stop_distance` basis `record_outcome()` already defines: entry at the next
   bar's open, stop and target from the row's setup, a fixed hold, a gapped
   stop filled at the worse of stop and open (BACKLOG 21's rule, applied here
   from day one rather than discovered later). Runs in the scanner — the one
   writer, on the runner that has market data. One outcome per signal per
   basis: `record_outcome()`'s one-outcome-per-`ref_seq` guard must become
   per-(`ref_seq`, `basis`) so a journal (premium) outcome and a mechanical
   (stop-distance) outcome can both exist and are never summed.
2. **An exact signal key on the journal row.** `open_option_position()` has no
   field for which signal a trade came from, so `attach_outcomes()` matches on
   (ticker, trend, within 5 days) and refuses ambiguity. With base signals
   recorded on consecutive bars ambiguity becomes the common case. Carry
   `signal_key` (ticker|trend|bar_date) from the alert into the position and
   match on it first.

---

## 29. Day trading by chart reading — PHASE 1 COMPLETE ON THREE TIMEFRAMES 2026-10-13: NO RULE CLEARS

**Owner's idea, 2026-10-08; screened 2026-10-09 in `results/day_trading_screen.md`.**

Base rates first: fewer than 1 % of day traders profit persistently (Taiwan,
every trader 1992–2006); 97 % of those who persist lose (Brazil, every trader
2013–2015). RSI, volume and ADX measured d ≈ 0 on 1,457 daily setups here;
Fibonacci has no controlled evidence; patterns and trendlines are testable only
as rules. Power: 349 independent trades for +0.15 R, four to twelve months at
intraday frequency, costs inside the bar, and a $5,000 account under the
pattern-day-trader rule. The screen fixes a three-phase path: codify six rules
and test them on 60 days of 5-minute bars against random entry (weeks); then,
for what cannot be codified, a paper forward test of the owner's own chart
reading read at 349 episodes against the base rate; live only on a cleared
bar. Prediction on file: the indicator, Fibonacci and trendline rules show
nothing; the opening-range rule is the one with a chance, and costs likely eat
it. **Owner decided 2026-10-09: run Phase 1.** Pre-registered in
`results/intraday_rules_preregistration.md` and built the same day as
`intraday_rules.py`: six detectors on one shared feature set, next-open entry,
flat at the session's close, 5 bps a side, drift_null's random-entry benchmark
with twenty fixed seeds, a session-clustered CI, and the three-part bar.
`check_intraday_rules_match_preregistration()` holds the code's constants to
the document's numbers. **Owed: the run, on the laptop** — `python
intraday_rules.py | tee results/intraday_rules_run1.txt` — which fetches 60
days of 5-minute bars for 20 names into `.bar_cache/` on first use.

**Run 2026-10-10 by the owner (`results/intraday_rules_run1.md`, raw
`_run1.txt`).** 20 names, 60 sessions, fingerprint `v2-9b37aca34d3c5b84`.
**No rule clears; five of six have a session-clustered CI entirely below
zero.** Opening-range breakout: 127 setups, zero reached target, RVOL
dose-response absent (−0.04 → −0.12 → −0.11). VWAP reclaim: 72 % wins,
−0.16 R. EMA pullback: −0.39 R, worse than random by 0.17. RSI divergence:
−0.19 R but beats random entry by +0.27 (the one timing effect; it does not
pay for its geometry). Fibonacci: the named zone (−0.20) was WORSE than both
arbitrary control zones (−0.10, −0.16) — the tool is refuted, not just the
signal. Trendline break: −0.27 R, beats random by +0.11, below the margin.
Prediction scored: rules 3 and 5 as predicted, 4 and 6 partly wrong (timing
content exists, does not survive costs), rule 1 wrong. No confirmation window
is owed and none will be run. Phase 2 (paper forward test of the owner's own
reading, 349 episodes, base-rate framing) remains the owner's call.

**Phase 1b, hourly, owner's request 2026-10-10.** Pre-registered in
`results/intraday_rules_hourly_preregistration.md` before any hourly bar is
fetched: the same six rules restated in hourly clock time (1-bar opening
range, ±1-bar pivots, divergence ≥ 3 bars, structure may read the previous
session), two years of bars (~500 session clusters), and the interval
tightened to 97.5 % because this is the second test of the family. Built as
the `1h` profile of `intraday_rules.py` (`--timeframe 1h`); the check pins
both profiles to their documents. Prediction on file: no rule clears, and
the intervals are tight enough that the verdict is a measured loss. **Owed:
`python intraday_rules.py --timeframe 1h | tee results/intraday_rules_hourly_run1.txt`
on the laptop.** **Run 2026-10-10 (`results/intraday_rules_hourly_run1.md`):**
20 names, 495 sessions, fingerprint `v2-eb11301b7b4c55e4`. **No rule clears;
five of six have a 97.5 % interval below zero, the sixth (opening range) is a
measured zero** (820 setups, 13 target hits, 95 % timeouts, no RVOL
dose-response). RSI divergence equals random entry exactly (−0.136 vs −0.135):
the 5-minute timing effect did not survive the timeframe. Fibonacci's named
zone was the worst of three for the second time (−0.163 vs −0.054 / −0.144).
VWAP reclaim was worse than random by 0.28 R. Prediction scored: the verdict
and the timeout and Fibonacci calls correct; "4 and 6 beat random" wrong.
Timeouts dominate every rule (60–95 %). **Phase 1 is complete and negative on
both timeframes; no confirmation window is owed.** Phase 2 remains the
owner's decision.

**Phase 1c, 4-hour, owner's request 2026-10-10 ("lets do 4hr timeframe").**
A session holds two 4-hour bars, so the day-trade framing degenerates to
"enter 13:30, exit 16:00"; the owner was told and the test is therefore
pre-registered as a SWING test of the same six tools in
`results/intraday_rules_4h_preregistration.md`: bars resampled in code from
the frozen hourly cache (Yahoo has no 4h interval; the fingerprint must equal
the hourly run's `v2-eb11301b7b4c55e4`), the rules read the trading week
(weekly opening range, week-anchored VWAP, two-week structure), a position is
held up to ten bars across sessions (five sessions), and the interval is
tightened to 99 % because this is the third test of the family. Power stated
honestly: ~100 week clusters, detection limit +0.15 to +0.30 R, so the "mean
≥ +0.15" clause is decisive but a small loss may read as indistinguishable
from zero. Built as the `4h` profile of `intraday_rules.py` (`--timeframe
4h`, `resample_4h`); the check pins all three profiles. Prediction on file: no
rule clears; timeouts fall well below the hourly run's; the named Fibonacci
zone stays inside noise of its controls. **Run 2026-10-13 by the owner
(`results/intraday_rules_4h_run1.md`, raw `_run1.txt`):** 20 names, 104
weeks, fingerprint `v2-eb11301b7b4c55e4` (equals the hourly run's, as
required). **No rule clears; every mean is negative**, five of six at
percentile 0 of random entry. Fibonacci is a measured loss at 99 %
([−0.44, −0.10]) and its named zone was not the best of three for the third
time (−0.28 vs −0.15 / −0.70). RSI divergence −0.56 R: the stop at the new
low is the level overnight gaps go through, and the gapped fill rule priced
it (155 stops filled at the open across rules). EMA pullback is the only
rule above random (+0.04, still −0.06 R). Timeouts fell to 1–45 % as
predicted; the "means near zero" call was wrong, four of six are more
negative than at hourly. 1,743 setups were skipped by the one-position rule,
about as many as were taken. **Phase 1 is complete and negative on three
timeframes; no confirmation window is owed.**

**Phase 2, owner's decision 2026-10-13 ("go ahead with Phase 2").**
Pre-registered in `results/daytrade_paper_preregistration.md` before the
first call, and built the same day as `daytrade_paper.py`: the owner's
discretionary call is logged BEFORE entry (ticker, side, a setup name from
the Phase 1 list or `pattern`/`other`, the price on the screen, stop,
target) into `daytrade_paper.jsonl`, an append-only hash chain in the
forward log's format with its own single writer, never read by the journal,
the app or the scanner. The Phase 1 engine grades it from 5-minute bars:
next bar's open + 5 bps, stop first, target, else flat at the close, gapped
levels fill at the open, a twenty-draw random-entry null at the same
session, the dust floor. The logged price must sit inside the bar trading at
the call or the call is VOID; a call outside hours or at 15:55 or later is
refused. **Nothing is read before 349 settled episodes**: the report prints
counts and withholds the mean, interval, null and verdict, in code, with no
peek flag. One pooled bar at 95 %: mean ≥ +0.15 R, clustered CI above zero,
≥ +0.15 over the null. Prediction on file: the record does not clear; the
mean sits within ±0.10 R of the random null. `check_daytrade_paper_isolated()`
pins the constants and the one-writer rule. **Owed by the owner: the calls,
committed the same day, and a weekly `grade` inside the provider's 60-day
window.** Four to twelve months to a reading. First call logged 2026-10-13
(a test of the command) and graded mid-session 28 seconds later: VOID by a
grader defect (the frame ended at the bar then trading). Fixed the same day:
a session is graded only after it has closed. Rows 1–2 stay in the chain as
a VOID that does not count; the document carries the note.

---

## 30. A second bucket: scan a wider universe for HIGH-QUALITY signals, price the chain for at most five — LOGGED 2026-10-13, NOT STARTED

**Owner's idea, 2026-10-13, logged before anything else was started.** In
the owner's words: can we expand the universe to identify high-quality
stocks and, if found, check the option chain only for five high-quality
names? This would be another bucket of tickers beside the live one.

**What exists today.** The live universe is `universe.select_universe()`:
the `CANDIDATE_POOL` (about 80 names, reserved tranche A excluded) ranked
by relative strength against SPY, top 8, at most two per sector, refreshed
every 14 days. The scanner runs the signal on those 8 and tags each signal
`hq` with `setup.hq_fail` (BACKLOG 24: the base signal is recorded, the
HQ tier only gates the alert). `fetch_atm_chain()` prices the ATM chain
per name in the post-close record-only pass (BACKLOG 25 Part B). So the
pieces are there: a pool, an HQ tag, a chain fetch. What is new is the
ORDER: scan the pool, select by HQ, and spend the chain calls only on what
cleared.

**What it would take, and what must be decided before any of it.**

1. *The premise is unmeasured.* "High-quality" is the alert tier whose
   features were refuted on daily data (24); the forward record now tags
   HQ precisely so that `summary()` can compare `mean_r_hq` with
   `mean_r_base` once there are enough settled outcomes. Until that reads,
   a bucket selected on HQ is a bucket selected on a label with no measured
   edge over the base signal. The record is the test; this item should not
   front-run it.
2. *Which five.* On a day with more than five HQ signals across 80 names
   the bucket needs a ranking (RS score, R:R, dollar volume, spread) and
   that is a design choice, the owner's, with the usual risk of choosing
   the ranking that looked best last month.
3. *Cost in calls.* The scanner fetches 8 histories; the pool is 10x that,
   and Yahoo from Actions is already the fragile leg (26, 27). Reuse the
   universe refresh's history fetch rather than a second one, or the scan
   doubles the provider's exposure.
4. *Reservation.* The pool already excludes tranche A; the bucket must read
   `data_reservation` the same way, or this becomes the way tranche B was
   lost (universe.py's own note).
5. *Record, not alert, first.* Same rule as 24: record every HQ signal the
   wide scan finds in the forward log under its own `bucket` field, grade
   it with the same resolver, and only then decide whether five of them
   deserve chains. A bucket that alerts before it is graded is a second
   unmeasured strategy running live.

**Explored 2026-10-13, owner's request ("lets explore on backlog 30").**
Facts from the repo, not opinion:

- *The HQ tier almost never fires on the live eight.* The forward record
  holds 27 signal rows and **0** are HQ. Of the 8 rows since 2026-10-08
  that carry `hq_fail`, ADX ≥ 35 failed on all 8, "Strong" on 6, rr < 1 on
  1. Across all 27, strength was Strong on 5. By the rule of three, 0 of 27
  bounds the HQ share of base signals below ~11 % at 95 %.
- *Base signals run about one per ten name-days* (8 in 112 since 10-08).
  On the 83-name pool that is ~6 base signals a day and, from the bound
  above, **fewer than ~0.65 HQ signals a day**, probably far fewer. "At
  most five a day" will never bind; the realistic bucket is 0–3 a week.
- *The expectancy premise has no support yet.* ADX measured d = −0.01 and
  −0.11 (`setup_population_run2.txt`, NOT ESTABLISHED, not informative);
  volume likewise (24). The forward record's HQ-versus-base test (24,
  pre-registered) has 0 HQ rows to read and, at 8 names, will starve.
- *Cost is small if done the universe's way.* `universe.fetch_history()`
  already pulls the whole pool in ONE batched download for the RS ranker;
  the scanner's per-ticker path at 1.2 s a name would take ~2 minutes for
  83, also fine inside the post-close pass. `fetch_atm_chain()` is two
  calls a name; five names is nothing.
- *The ranking decision dissolves.* The RS score the universe snapshot
  already computes for every pool name is a ranking that exists, is
  pre-registered for selection, and was not chosen for this purpose. Use
  it; invent nothing.
- *It does not touch 23.* A wide scan runs the live config, as the forward
  record does. A historical backtest of the HQ tier over the pool would
  need the 23 decision first; the forward version does not.

**What the facts recommend: a record-only pool bucket, no alerts.** Scan the
full candidate pool in the post-close record-only pass; write every signal
to the forward log with a `bucket` field (`live` for the eight, `pool` for
the rest), HQ tagged exactly as today; grade both with the same resolver;
take the ATM chain snapshot (BACKLOG 25's writer) for at most five pool HQ
signals a day, ranked by the snapshot's RS score; alert on nothing new.
This is the only way the pre-registered HQ-versus-base reading (24) gets
rows at all, and it answers the owner's question as a measurement rather
than a live bucket. **Power, honestly:** 349 HQ episodes for +0.15 R at the
bound above is two years or more; a smaller sample reads only a large
effect. If HQ fires more often across the pool than it has on the eight,
the record will show that within weeks, which is itself the first thing to
learn.

**Owner decided 2026-10-13, the same hour: "lets run HQ scan once a day
during market hours."** Built the same day, with alerts (the owner's choice
over the record-only version above), pre-registered as a dated additive
amendment to `results/forward_record_preregistration.md`: the scanner's
first alerting run of each ET date also evaluates the whole candidate pool
beyond the watchlist (`scanner.pool_tickers`, `pool_due`, state key
`POOL:DATE`), alerts on the HQ tier only, **at most five a session ranked by
the universe's relative-strength number** (`scanner.relative_strength`,
63 sessions against SPY), with the suggested contract on each alert, and
records every pool signal and the pool's `scan` row under `bucket: pool`.
`forward_log` rows carry `bucket` (legacy rows read as `live`),
`last_scan()` reads the live bucket so a pool scan is never the coverage
heartbeat, `summary()` reports `by_bucket`. Pool rows are graded on the
pool-scan run and in the post-close pass only. Workflow input `pool`
(`--force-pool`) repeats the scan. `check_pool_bucket_wired()` pins the cap,
the ranking, the bucket split and the workflow input. Prediction on file: HQ
fires rarely on the pool (bound < 0.65 a day) and does not beat base by
+0.15 R. **Still owed:** the 349-episode reading per bucket, years away at
the bound; the first thing to learn is the pool's actual HQ rate, which the
record shows within weeks.

---

## 31. "Most trades fail, others make money": the logic review — ASSESSED 2026-10-13, DECISIONS OPEN

**Owner's question 2026-10-13.** Answered in
`results/logic_review_2026-10-13.md` from the committed record first and
the literature second; no new result. The record already explains both
halves: trades fail because the signal has no edge (`drift_null`: random
entries with the same exits match it) and the live geometry (1.25 ATR stop,
4 ATR target, 20 bars) is hit by noise about seven times in ten under a
driftless walk, so the forward record's 3 wins in 16 is what noise gives;
setups never pass because the HQ gate's ADX ≥ 35 leg almost never occurs on
large caps in a grinding bull (0 of 46 signals, 0 of 19 pool signals) and
the ADX bucket that looked best was 25–28. "Others making money" is beta
with leverage inside a Bull regime plus survivorship of who posts; the base
rates and Bryzgalova et al. (2023, retail options lose on average at 12.6 %
spreads) are the benchmark, and `longs_only_run1` already says the index
beats every version of the signal. **The gap is a category error, not a
bug:** a timing system in a market that pays for holding, with entries on
the wrong side of the one real short-horizon effect (reversal). The one
lead in our own data, the inverted long in Bull (+0.40 R, inside its null),
can only be tested forward: every tranche is spent.

**Owner's decisions:** (a) pre-register a pullback-in-uptrend signal as a
second forward bucket (no alerts, same resolver), (b) measure the RS
top-eight as a holding basket against SPY over ten years (can show it loses
to the index, cannot prove alpha), (c) neither, and read the running
records when they are due.

**Owner chose (a) 2026-10-13 ("go with option 1, pre-register the pullback
bucket").** Pre-registered in `results/pullback_preregistration.md` before
the first row, then built: `signal_core.base_trend()` and
`signal_core.long_levels()` are extracted from `evaluate()` (which now calls
them, bit for bit the same) so the new rule reads the live signal's own
pieces; `pullback_signal.evaluate_pullback()` fires on the base BEARISH setup
when the close is above its 200-session SMA on the raw frame, taken LONG with
the live levels and gates, SPY regime tagged, never gating; the scanner
records it under `bucket: pullback` for every name it evaluates (watchlist
and pool) and never alerts. Read once at 349 settled episodes against the
base buckets over the same span (+0.15 R), zero, and a random-entry null.
Prediction on file: beats random by a margin inside costs, beats base by
less than +0.15, does not beat SPY. `check_pullback_bucket_wired()` pins the
shared functions, the SMA, the bucket and the no-alert path (45 checks).
Twelve to eighteen months to a reading.

**Two laptop checks on real data, built 2026-10-13 (owner: "build both").**
`pullback_arrival.py` counts how often the rule fires over the last trading
year across the pool and the watchlist (signal bars, episodes, by SPY
regime, by month, by name, and the implied time to 349 episodes) and is
built to be unable to do anything else: no engine call, no outcome, no R,
nothing read after a signal bar; `check_pullback_arrival_outcome_free()`
greps its code for exactly that. `engine_differential.py` runs random
geometries on every daily frame frozen in `.bar_cache` through both
`backtest.simulate_trade` and `forward_log.resolve_signal` and reports any
disagreement in outcome or R (synthetic version: 596 paths, zero). **Owed on
the laptop:** `python pullback_arrival.py | tee results/pullback_arrival_run1.txt`
and `python engine_differential.py | tee results/engine_differential_run1.txt`.
**Run 2026-10-13 by the owner (`results/data_checks_run1.md`):** the engines
AGREE on 10,569 random setups over 271 real frames, zero disagreements at
the grader's precision, 212 gapped fills void in both; the pullback rule
fires 0.81 episodes a session across the 83 names (823 bars, 203 episodes,
193 in a Bull regime), so the 349-episode reading is **about 20.6 months**
away, not twelve to eighteen (75 % of bearish base setups sit below their
200-day average); the pre-registration carries a dated arrival note, no
rule changes.

---

## 32. Journal review: the trades traded are not the structure tested — ASSESSED 2026-10-13, DECISIONS OPEN

**Owner's question 2026-10-13** ("review our trade journal including
skipped signals; right vs wrong"), answered in
`results/journal_review_2026-10-13.md`. Right: size (1–2 contracts, 1–5 %
of the account, every live trade), one skip rule applied every time,
honest paper/live and expiry bookkeeping, and the system's seven signal
trades (+$153) beat the owner's three discretionary ones (−$111, two more
open at their stops). Wrong: ten of thirteen trades entered under 21 DTE,
profits taken at +23–131 % never +200 %, losses run to −72/−73/−100 %, one
call held to expiry — the tested structure (21–45 DTE, +200 %/−50 %, out at
7 DTE) was never traded; the 5 % budget rule at $5,000 admits only cheap
short-dated contracts and skipped every liquid name, so the rule and the
tested window are incompatible here; journal dates are log times (holding
time unknowable); `signal_key` empty on all rows so zero journal trades
attach to the record (premium-basis reading has no data after four weeks);
`alert_history.json` is empty; a test row (ZZZ) sits in the journal; two
exit-signalled positions are being held to expiry.

**Owner's decisions:** (1) trade only the measured structure, and only
where affordable, or not at all; (2) take the two stops or note the choice;
(3) stop discretionary option entries or route them to Phase 2's paper
record; (4) going forward log trade time and the signal date, delete the
test row; (5) have the app record the alerts it shows.

**(4) and (5) built 2026-10-13 (owner: "build the journal fields and the
app's alert history").** Journal: the open form takes "Traded at" (date and
time, ET, default now) and both close forms take "Closed at"; the journal row's
`date` is now the trade's time, `closed` the close's, and `logged_at` keeps
the log time; Trade History shows traded, closed, logged and the signal key,
or says in words that an unkeyed row cannot attach to the record. Alerts:
the scanner records every alert ATTEMPT as a forward-log `alert` row
(`forward_log.record_alert`, ticker, trend, bar date, key, bucket,
delivered, header; never on a dry run; the pool cap now bounds attempts, so
a failed day tries the five by RS, not all six) and the app's Alert History
tab reads those rows fresh from the repo (`gh_sync.load_jsonl`, read-only)
and marks the ones whose key appears on a journal row as acted on. The app's
own scan alerts list is unchanged below it. The single-writer check names
`record_alert`. Still the owner's: delete the ZZZ row, take or note the two
stops, and enter the signal bar date when logging.

## 33. ADX 30 instead of 35, on the config the scanner runs — RUN 1 DONE 2026-10-14: NOT ESTABLISHED, DECISION OPEN

**Owner's question 2026-10-14** ("backtest on existing parameters but ADX as
30 instead of 35"). Not already answered: `adx_retest_run1.md` (2026-09-10)
measured the RECORD config (1.0 / 3.0, regime off, both sides) before the
gap-fill fix, and compared nested aggregates. The scanner runs 1.25 / 4.0,
volume 1.2×, regime on, longs only. For this run the owner's question
decides 23's open "which config" for the first time: the live one.

**The unit of the question is the marginal band.** The ADX-30 set is the
ADX-35 set plus exactly the trades whose signal-bar ADX is in [30, 35), so
what that band earns is what the change earns. `adx_retest.py` gained
`--config live` (every shared key from `signal_core.DEFAULTS` via
`backtest.CFG_TO_PARAMS`; the selftest asserts the built params equal the
scanner's), `--longs-only`, and `--band LO,HI` (bands cut from the ungated
run by the ADX carried on each trade's setup; `judge_band` is the
pre-registered bar: n ≥ 150, mean R > 0 at p ≤ 0.05, detectable at 80 %).
The old command is unchanged (flags default off). Pre-registration, bars and
the stated expectation (NOT ESTABLISHED on power, as run 1 and
`setup_population_run2` predict) in `results/adx_live_preregistration.md`.

**Run (owner, laptop with `.bar_cache/`):**
`python adx_retest.py --config live --longs-only --levels 0,20,25,30,35 --band 30,35`,
output to `results/adx_live_run1.txt` with the fingerprint line. Spent data:
the run can refute, not confirm. A null does not make 30 "better"; it makes
30 versus 35 a choice about alert volume from a signal with no measured
edge, which stays the owner's. No `adx_min` change is pre-committed on any
outcome.

**Run 1, 2026-10-14 (owner's laptop, `results/adx_live_run1.txt`,
fingerprint `v2-4ffbb6814f505b97`, six series fetched fresh): NOT
ESTABLISHED on both bars.** The band [30, 35) holds 199 longs at +0.104 R,
CI [−0.159, +0.367], p 0.44, under a 0.376 R detectable effect: the trades
ADX 30 would admit carry nothing the data can see. The ladder is flat
(+0.13 to +0.26 R at every level, ρ = +0.10, no dose-response): ADX carries
no information on the live config either, and the 35 gate keeps 15 % of the
long base signals with no measured benefit. The ungated long side's +0.196 R
on 2,135 trades is NOT a finding: the set holds the seven swept names
(long side +0.297 R on their own), `drift_null_run1` found random long
entries earn the same on this decade, and the cut was chosen after the
fact. Reading in `adx_live_preregistration.md` § Result. **Owner's
decision:** keep 35, or lower to 30 as an INTAKE choice (about double the
HQ alerts, graded forward), recorded as an amendment to
`forward_record_preregistration.md` before the first row under it. Not a
measured improvement either way.

---

## 34. Progress review 2026-10-19: the report counts rows where the pre-registration counts episodes; the 4-ATR target has never been reached — ASSESSED 2026-10-19, DECISIONS OPEN

**Owner's request 2026-10-19** ("re-perform deeper analysis, see what
improved, what can be improved, what idea can be developed"), answered in
`results/progress_review_2026-10-19.md`. Improved since 13 Oct: pool bucket
live (83 rows, ~25 episodes in 5 sessions, ~5 episodes a session against the
watchlist's ~0.9), pullback bucket live (6 rows, first outcome +1.54 R),
both stops taken and logged with trade times, journal fields and alert
history built, ADX 30 answered, 47 green runs. Not done: ZZZ row, empty
`signal_key` on every journal row, no skip logged since 08-31, Phase 2
unused, RS-basket-vs-SPY not started.

**Two findings.** (1) `forward_record_preregistration.md` fixes the episode
as the unit and says only the first bar's row is graded; the grader grades
every row and `report()` prints row counts under an "EPISODES" line. 132
rows are 43 episodes; 49 settled rows are 17 settled episodes; the live
bucket reads −0.18 R by rows and +0.16 R by episodes. Fix: an `episodes()`
reading beside the rows, gate on episodes, no amendment needed. (2) Of 21
settled rows carrying the full 4-ATR target, 0 reached it (19 stopped, 2
timed out); all 16 target hits are resistance-capped targets under 1.5 R.
The live geometry's realised payoff needs a 51 % win rate; the live bucket
has 42 %.

**Decision 2026-10-28 (owner: "go with option 1, pool only"):** amendment
C is declared in `results/forward_record_preregistration.md` (amendment
2026-10-28): the pool is the population for H0/H1 and the 349-episode
gate; the live bucket is the alerting view, recorded and reported in
full, never gating. `forward_log.HEADLINE_BUCKET = "pool"`; the by-basis
reading, the gate count and the report's gate line read the pool;
pinned by selftest and consistency check. The episode reading (finding 1)
landed the same day (item 37). **D decided 2026-10-28 (owner: "go with
D as well, re-pose H1"):** amendment 2026-10-28 (D) in the same file. H1′:
the RANK tier — the top `forward_log.RANK_TIER_K` = 5 taken pool rows a
bar by fewest failed HQ legs, then ADX, then R:R, then ticker; an episode
is in it by its first row — beats the rest of the pool by ≥ +0.15 R per
episode. A read-time tag (`forward_log.rank_tier`), chosen for arrival
rate, not edge; alerts still fire on the HQ threshold. Power stated: at a
tier share of ~0.21 the +0.15 R bar needs ~2,070 settled pool episodes;
the MDE at the 349 gate is ~+0.37 R, so a smaller difference reads "not
established". Known at declaration: tier 9 episodes / 5 settled /
−0.892 R, rest 33 / 21 / −0.199 R; the record points against H1′.
`RANK_TIER_K == scanner.POOL_ALERT_CAP` pinned. A and B remain undecided.

**Proposed, as pre-registrations:** (A) a forward random-entry control row
beside every taken signal, graded by the same resolver, read as signal
minus control; (B) a weekly basket row for the 8 selected names against
SPY; (C) an amendment naming the pool as the population for the
base-signal question; (D) re-pose H1, since a tier that fired 0 times in
132 cannot be compared. Predictions stated in the file. **Owner's
decisions:** which of A to D to pre-register; whether to build the episode
reading now (recommended: yes, it is a reading change).

---

## 35. External review of 2026-10-19: twenty claims verified, none wrong; the record reads the forming bar, IV grading will censor departed names from ~11-07, close durability is P0 — ALL FIVE GROUPS DONE 2026-10-19; OPTION RE-RUNS OWED

The owner had the repository reviewed outside this project (reviewed SHA
6095f74 against the 2026-10-08 audit). Verification and response in
`results/external_review_2026-10-19_response.md`. CONFIRMED and biting
the record now: the daily grader reads today's forming bar (`frames[tk]`
is raw; a timeout was marked at an 11:10 ET price); the SPY regime is read
from the forming bar; `_scan_cfg` omits volume threshold, HQ min R:R and
regime policy; the exit monitor returns success when every check failed.
CONFIRMED with a date: IV snapshots for names that left the watchlist are
never graded, and the grader anchors on history start rather than the
snapshot date; first premium rows are due ~2026-11-07. CONFIRMED, latent:
keyed journal attachment skips ticker/direction; the paper grader treats a
truncated session as complete; the intraday CI mixes two estimators (no
verdict can flip: all 18 rules failed the mean-R clause); option entry
premium uses the entry bar's close; app batch download uses adjusted
prices; write retry re-puts without re-merging; close proceeds after a
failed journal save (the 10-13 AAPL incident). NOT VERIFIED: contract
selection divergence, quote/risk NaN acceptance, DTE/weekend timing,
portfolio chronology. POLICY: dry-run scan rows, ADX rounding at the gate.
**Owner's decisions:** the order of work in the response file §"Order of
work"; group 1 and group 2 are small and recommended now.

**Group 1 done 2026-10-19 (owner: "go with group 1").** Grading reads
settled bars only (`bars_for_grading` drops the forming bar until the
close; outcomes land on the next run after the bar settles); the SPY regime
is read from the settled bar in `market_context.get_spy_regime`, which
covers the scanner and the app; `_scan_cfg` names `hq_min_rr`,
`volume_mult`, `volume_soft_mult` and `spy_regime_on`, so the fingerprint
moves once from `e7d75b855a51` to `c357a0d01ff5` with the same rules,
declared in `forward_log.KNOWN_CONFIGS` and in the forward pre-registration
(amendment 2026-10-19) before the first row under it; the exit monitor
exits 1 when every check raised or the state write failed, 0 on a partial
with a warning; the outage cooldown is stamped on delivery only;
(correction 2026-10-20: the signal path held a second config literal, so
the 10-20 11:07 scan wrote its signal rows under the old hash and its scan
rows under the new one; both are declared, and from the next scan every
row uses `_scan_cfg()`, asserted by the selftest);
`backtest.stats` counts the drawdown from initial equity, and the five
committed files that print the old figure say so at their top. Every guard
falsified.

**Group 2 done 2026-10-19 (owner: "fix group 2").** `iv_snapshot.grade`
computes a premium only when the snapshot date itself is in the history and
the 21 DISTINCT sessions after it are present, each exactly one exchange
session apart (the scanner injects `trading_days_between`; the stdlib
fallback is a 4-calendar-day gap rule), with finite positive closes
(`window_after`); a window that fails is held and counted (anchor missing,
gap, bad close), never shifted, compressed or voided; conflicting duplicate
dates reject the history; the premium row carries anchor, window start and
end and a hash of the dated closes; `summary` reports unresolved snapshots
and the oldest. The post-close pass fetches each departed name once for IV
grading, up to `IV_GRADE_FETCH_CAP` = 20 a pass, and logs
fetched/deferred/held counts. Grading-validity note in
`option_iv_preregistration.md` (Part B), before the first premium row.
Guards falsified.

**Group 3 done 2026-10-19 (owner: "go with G3-5 in your own order").**
`forward_log.attach_outcomes` refuses a signal key whose ticker or
direction is not the journal row's (N03). `daytrade_paper.resolve_call`
grades only from a whole session (`session_coverage`: ordered unique
timestamps five minutes apart, 09:30 to 15:55 ET, 12:55 on a half day);
a truncated, holed or duplicated session is HELD and voided with its
reason only after the fifty-day fetch horizon; the call's bar must contain
the call, an earlier bar never stands in (N01). The evidence hash is
version 2: symbol, timeframe and every bar's timestamp, OHLC and volume,
versioned on the outcome row (N07). Grading-validity note in
`daytrade_paper_preregistration.md`. Guards falsified.

**Group 5 done 2026-10-19.** `intraday_rules.clustered` reports the
cluster-robust SE of the trade-weighted mean it prints (N02; with equal
cluster sizes it equals the old session-mean SE, which is why the fixtures
never caught it); every committed intraday verdict failed the mean-R
clause, so none can change, and the six result files say their CI bounds
predate the fix. `option_backtest.simulate_option_trade` prices the entry
from closes through the signal bar only (one bar of look-ahead removed);
the seven committed files that print modelled premiums carry a caveat and
a re-run is owed. `app.batch_get_data` passes `auto_adjust=False`, and a
47th consistency check reads every `yf.download()` in the live paths to
its closing paren for the flag. `gh_sync.load_jsonl` parses each source
into its own list and returns it only whole (N08). Guards falsified.
**Still open:** the option re-runs.

**Group 4 done 2026-10-19.** `journal_store.close_position` removes the
position only after the journal row's durable write succeeded and returns
`{ok, error}`; the app shows a failed close and keeps the position (the
10-13 incident cannot recur silently). `gh_sync.save_result` returns the
write error beside the data; `save()` wraps it. `_gh_put` re-merges
against the remote as it then is on every sha-conflict retry (`remerge`
callback) instead of re-putting the stale payload, and `save_result`
returns and caches what was actually written. `merge_positions` reads the
journal's ids as tombstones (`closed_ids`): a remote-only id in the
journal was closed here, any other was added by another app session and
survives; on equal rank the monitor-owned fields come from the record with
the newer `last_check_epoch`, app-owned fields stay the app's. Position
and skip ids carry a random suffix so two entries in one second cannot
collide. Guards falsified. **Still open from the review:** the option
re-runs on frozen bars (owner's laptop); contract-selection divergence,
NaN quote handling, DTE/weekend timing and portfolio chronology were not
verified here and are not addressed.

---

## 36. Second and third external reviews (2026-10-20): persistence completed in two passes, a regression of mine fixed, numeric validation and rule-level gate precision open — VERIFIED AND PARTLY FIXED 2026-10-20

The owner had the 2026-10-19 fixes re-audited (reviewed SHA 340a221).
Verification and response in `results/external_review_2026-10-20_response.md`;
every claim checked held. **Fixed 2026-10-20:** the first PUT now merges
against the same GET that supplies its sha (a remote change between two
reads was overwritten with no conflict); app-owned files are written only
at the version this session loaded, a changed or unseen remote refuses the
overwrite (`gh_sync._LOADED_SHA`); a close whose journal row landed but
whose position removal failed is reported as pending and completes on
retry; `save_journal` moves the session copy on success only; a stale
local record of a closed id is dropped in the merge; with GitHub off a
failed local write is reported; and the regression PR #142 introduced
(`return save_journal(...)` made the share-close alert bookkeeping
unreachable) is fixed with both paths tested. Guards falsified.

**Third pass, same day (reviewed 612de5c):** five gaps in the above, all
confirmed and fixed: the loaded-version ledger is per session
(`gh_sync._sha_store()` in `st.session_state`), not per process; a failed
position removal leaves the session's list as persisted so the close is
retryable; the local fallback copy is written on a successful durable
write only; closed ids for the merge are the session journal's UNION the
repo journal's read at save time; a file loaded as absent must be created,
and refuses if another writer created it since (`SHA_ABSENT`). Guards
falsified. Response file § "Third pass".

**Group A DONE 2026-10-28 (owner: "fix these"; see item 37).** ~~Open, group A (small, mechanical):~~ one validator for finite OHLC,
positive prices, sorted unique sessions; finite R before `record_outcome`;
finite spot/strike before an IV row; finite OHLC in the paper grader's
`session_coverage`; `0 < bid <= ask` and integer contracts in
`risk_params.check_option_cost`; drop bars dated after today whatever the
clock; UUID-sized id suffix. **Open, group B (research engines, re-runs
owed):** backtest warm-up discontinuity, execution bar dropped on missing
volume, option clock by calendar dates and end-of-sample trades marked
open, spread maturity, portfolio chronology. **Open, group C (app
paths):** partial batch retry, contract selection divergence, regime
minimum history shared with the backtest, macro status wording.
**Owner's decision:** unrounded gate comparisons (ADX, R:R) are a rule
change that moves what the record records and need a dated declaration
before their first row. The three policy items (dry-run rows, next-open
R:R, opening-target precedence) stay policy.

---

---

## 37. Progress review 2026-10-26: the live bucket cannot reach its gate (~2.3 years) and the pool can (~4.5 months); the 4-ATR target is now reached 8 of 57 times; a dead name is never settled — ASSESSED 2026-10-26, DECISIONS OPEN

**Owner's request 2026-10-26** ("review again thoroughly, improvements and
pending items"), answered in `results/progress_review_2026-10-26.md`.
Improved in the week: PRs #138–#145 merged (all three external reviews),
24 scanner runs and every exit-monitor run green, hand-off resolved (NVDA
−1.0 R expired, PFE +0.09 R manual close, no open positions), record at
579 rows / 121 outcomes. Readings (stop distance, NOT results): live 17
episodes, 12 settled, +0.007 R by episodes (−0.115 by rows); pool 37 / 22,
−0.272; pullback 7 / 4, −0.517. The 10-19 "4-ATR target never reached"
finding is out of date: 8 of 57 settled full-target rows reached it (CME,
KO, PG, CVX), booked at 1.64–2.80 R under the declared risk-as-filled
denominator. Still 0 HQ rows and 0 alerts ever.

**Findings.** (F1) Arrival rates: live 0.61 episodes a bar → 349 in ~2.3
years; pool 3.7 a bar → ~94 bars ≈ 4.5 months. The pool is the only
population whose gate arrives; and the live population rotates 3–4 of 8
names a week under the dynamic snapshot, which the pre-registration never
names. BACKLOG 34 amendment C is the decision that matters most. (F2)
`report()` still counts rows and prints `+nan` for the empty HQ tier. (F3)
`resolve_signal` never settles a name that stops trading inside its
20-bar window with some bars after the signal: `no_bars` applies only when
there are none; the row stays open and is re-fetched for ever. Not biting
now. (F4) Journal: 15 rows, 15 without `signal_key`, 14 with stop 0; the
premium basis has zero rows (BACKLOG 32). (F5) First IV premium rows due
2026-11-09; five departed names will exercise the group-2 fetch path for
the first time. (F6) Both PATs expire 2027-01-05/06 with no consistency
check enforcing the rotation, unlike the calendar. (F7) `universe-snapshot.yml`
comment says Monday, cron is Sunday.

**Fixed 2026-10-28 (owner: "fix these").** F2: `forward_log.episodes()`
reads the record by the pre-registered unit (consecutive bars on one
ticker and direction, gap ≤ 4 calendar days, first row graded);
`summary()["by_episode"]` and `report()` print it under each bucket's row
line, the gate line counts the live bucket's settled episodes, and an
empty HQ tier reads "no HQ rows" instead of NaN. Reading change only; no
amendment. F3: `resolve_signal` voids `no_bars` when the window has not
closed and the last fetched bar is older than `GRADE_NO_BARS_DAYS` (a
delisted, acquired or halted name), with the bar count and last bar in
the detail; the declared reason applied to the case its text describes.
F4: `journal_store.open_option_position` refuses `source="signal"` without
a `signal_key`, and both app forms show the refusal; the 15 existing rows
are untouched. F6: `consistency_check.check_token_runway` fails from
2026-12-16 until both expiry dates in `TOKEN_EXPIRY` are moved by the
rotation commit. F7: the snapshot workflow comment says Sunday. BACKLOG 36
group A: `signal_core.validate_bars` (finite, positive, low ≤ high, dated,
unique, ordered) runs in `scanner.get_data` (ticker refused with the
reason, counted failed) and `bars_for_grading` (grade refused, counted as
a failed fetch), enforced by a 49th check; `drop_unsettled` drops a bar
dated after today whatever the clock or policy; `record_outcome` refuses
a non-finite R; `iv_snapshot.record` refuses a non-finite spot or strike;
the paper grader holds a session with a non-finite or non-positive bar
(`bad_bar`); `check_option_cost` refuses fractional contracts; the chain
treats ask < bid as unquoted in both the diagnostic and the selection;
position, skip and alert ids carry a 16-hex-character suffix. Every guard
falsified. Readings after the change (not results): live 18 episodes, 15
settled, −0.196 R; pool 42 / 26, −0.332; pullback 9 / 4, −0.517.

**Amendment C declared 2026-10-28** (owner: "option 1, pool only"; item
34): the pool is the population, the live bucket the alerting view.

**Amendment D declared 2026-10-28** (owner: "go with D as well"; item 34):
H1 re-posed as the rank tier, read at `report()`.

**Still pending, in blocking order:** BACKLOG 34 A/B; the unrounded-gate
declaration — the owner's, since each moves what the record records;
group B and the option re-runs (owner's laptop); group C app paths;
journal hygiene beyond F4 (BACKLOG 32); BACKLOG 33 and 23 decisions; read
the 11-09/11-10 post-close logs (F5).

---

## Working conventions

- `signal_core.py` is canonical. `consistency_check.py` enforces 49 cross-module
  invariants (49 `check_` functions); run it before pushing.
- **Every new guard gets falsified** — deliberately broken to confirm it fails
  with the right message. That pass found six dead fixtures in the #20–#25 run;
  tests that cannot fail are the default outcome, not the exception. Do not skip
  it.
- `app.py` and `scanner.py` are **CRLF**; `signal_core.py`, `backtest.py` and
  `consistency_check.py` are LF. Edit CRLF files with `newline=''`.
- Costs are single-sourced in `risk_params.py`. This bullet used to say
  `MAX_OPTION_SPREAD_PCT = 8.0` is "**derived** from `OPT_WIN_RATE = 0.238` —
  at an 8% round trip the breakeven win rate is 23.3% against a measured
  23.8%". **That derivation was refuted in the module on 2026-09-10 and this
  copy survived it.** It compared a win rate measured at TP+100 against a
  breakeven computed at TP+200. At the measured basis an 8% round trip needs
  **38.9%** against a measured **24.9%**, and NO spread clears it. The ceiling
  is a **loss-minimising cost cap**, not the point where the arithmetic turns —
  tightening a cost cannot overfit, because it does not touch the signal. Read
  `risk_params.py` for the full account. `check_cost_gates_shared()` enforces
  the single source.
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
