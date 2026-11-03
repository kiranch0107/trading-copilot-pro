# Expert review — 2026-11-03 (evening): the alert, the contract, the exit and the record, read as a trader

Owner's request: "be an expert in trading stocks/options … assess and review
the code, logic, assumptions, decisions … strict review, testing as needed …
identify any gaps and improve the overall logic, experience for accuracy and
better trade alerts/triggers". Read against `main` at f59bda5. Five passes: the
signal-to-alert path and the grader (this session), and four parallel reviews
(contract selection and option pricing; the exit monitor and the app's logging
forms; the research engine's look-ahead and fill realism; the market-context
filters, data sources and universe). Every finding below was re-checked in the
code before it was accepted, most with a reproduction. Nothing was fetched from
Yahoo (the proxy blocks it). **No rule, threshold, level or grading changed, and
no committed number in `results/` moves.** Owner decisions are listed, not
guessed at.

The research verdict stands and is not re-argued here: the base signal has no
demonstrated edge, and the measured option structure is negative at the assumed
IV (`risk_params.OPT_SWEEP_BY_TP`, +200 %: 25.1 % wins × +134 % against 74.9 % ×
−51 %, about −4.6 % a trade). The review is about whether the alerts, the
contract, the exits and the record do what they say.

## The headline

**The largest money item is earnings inside the position's life.** The blackout
looks three days ahead of the alert; the contract is 21–45 DTE and held to 7
DTE. In the forward record, 17 graded stops that gapped past −1.3 R cost
**−41.1 R against a +10.5 R total** (250 graded signal rows: mean +0.04 R with
them, +0.22 R without). They cluster on a handful of events (NOW's 10-28
release: three rows, −14.5 R; PM about 10-21: six rows), so this is evidence of
a risk, not a measured effect. The 10-30 straddle rule (J2) could not have
caught NOW (N8 below). The alert now says where the next release falls against
the contract (N9); gating on it is decision D1.

## Fixed in this change (plain bugs; each pinned by a test and falsified)

| | where | the defect | evidence |
|---|---|---|---|
| **N1** | `scanner.gapped_at_fill` | The fill gate (J1) restated the grader's gap rule and missed two of its legs: the 2 bps slippage and the risk floor (amendment L2, landed a day before J1). An open within max($0.05, 0.3 %) above the stop, or within the slippage of the target, was **sent** and then **voided** by the grader. The gate now asks `forward_log.resolve_signal` itself on a one-bar probe. | 25,600-point grid: 760 disagreements → 0. |
| **N2** | `scanner.at_send`, `compose_alert`, alert rows | Every level in an alert is the signal close's; the alert goes out at 11:07 the next session and the record grades from that open. The alert now prints the move already made (in R) and the R:R from the latest price, warns under the tier's 1.0, and the alert row carries the numbers (`at_send`) so the gap is measured. | Of 7 graded HQ alerts, the open alone left WMT at R:R 0.24 and V at 0.65. |
| **N3** | `option_chain.et_today`, scanner, app | DTE was counted from the host clock; the app's UTC server read a 46-DTE contract as 45 after 20:00 ET, offered it, and the journal (ET) refused it. | Selftest greps the three files. |
| **N4** | `scanner.compose_alert` | The strike window ("the symmetric ATM window") was centred on the signal bar's close, not the price at send. The measured structure buys at the money **at the fill** (`option_backtest`: strike = round(entry open)). On 10-29 the window around 149.85 named NOW 150 calls with the stock near 110. | Centre is the latest print, then the open, then the close. |
| **N5** | `app.py` quick-buy form | The success message and `st.rerun()` sat **inside** `except ValueError`. A refusal flashed "now monitoring"; a success showed nothing and left Confirm live, so a second click logged a duplicate position monitored (and alerted) twice. | AST check over every logging `try`. |
| **N6** | `app.py` close forms | "Your actual fill" was pre-filled with the monitor's detection mid (days old; NOW 0.11 with the contract near 0.01) or the entry premium; a click-through logged a fill that never happened. Empty until typed now; 0 is a real fill; the close refuses an empty fill. | Streamlit AppTest on the pattern; check falsified. |
| **N7** | `exit_monitor` | (a) A position the monitor could not price reached the Actions log only, behind a green run; `last_check_epoch` kept the coverage alarm quiet; the "every check failed" guard counted only raised exceptions, which an outage cannot produce. Now one Telegram per ET day per position, and nonzero when nothing that needed a price was priced (a STOP already sent, or a position past expiry, is exempt). (b) An undelivered exit alert ended green; now nonzero. (c) A crossed quote (ask < bid) read as a reliable mid. (d) A one-sided quote that **proves** a level was skipped as unreliable in exactly the deepest-loss state: no bid with the ask at or under the stop level is now a STOP (the mid lies between ask/2 and the ask); no ask with the bid over the target, a TARGET. (e) The workflow's persist step ran only on success, so a nonzero exit dropped the state of alerts that did go out; now `always()`. | Six mutations falsified. The live NOW position (STOP sent) is exempt: no nag, no red run. |
| **N8** | `market_context.remember_release_dates`, scanner | **J2 could not fire on the case it was built for.** Yahoo's calendar drops a release once it happens: at 10-29 11:07 NOW's 10-28 date was already gone (with `post_days` 1 it would otherwise have blocked), and on Tue–Fri scans the straddle adds nothing the post window does not. The selftest's NOW assertion passed without the straddle. Release dates seen inside the window are now remembered for 10 days in `scanner_state.json`. Also: the SPY regime fetch ran once (yfinance 1.x returns an empty frame when throttled, so one throttled call turned the regime leg off for the run); it now retries an empty frame. Each row and alert says which tier legs passed **unchecked** (`setup.unchecked`). | Eight mutations falsified; a case only the straddle catches (Friday after-close release, Monday fill) added. **Partial:** only names whose calendar was fetched on an earlier run are remembered. |
| **N9** | `scanner.earnings_line` | The alert now says whether the next release falls before the contract's 7-DTE exit (held through: event IV paid, crush after), before expiry, after expiry, or is unknown. | Display only. |

Three false comments corrected: `option_backtest` said the live selector
targets ATM (it ranks by liquidity alone, D2); `risk_params` said the backtest
measured 21–45 DTE (it measured a 30-DTE entry; 21–45 is the acceptance
window); `exit_monitor` said the 20:00/21:00 UTC runs see the session that just
ended (`run()` skips after 16:00 ET, D6).

## Decisions for the owner

**D1 — earnings inside the contract's life (recommended: gate it).** Block the
HQ alert when the next release falls before the named contract's 7-DTE exit
(or pick the nearest expiry that exits before it), as an intake amendment like
F and G. Built so far: the warning line (N9). Of alerts that clear the current
blackout, a release falls before the 7-DTE exit on roughly 13 % at 21 DTE, 24 %
at 30 DTE and 41 % at 45 DTE (modelled: quarterly releases, a calendar that
keeps the date). `iv_snapshots.jsonl` shows the ATM IV drop across a release
on the same expiry: NOW 68.5 → 50.9 (10-28 → 10-29), TMO 43.7 → 34.8 and CME
35.2 → 26.3 (10-20 → 10-23): a flat stock costs a call bought before it roughly −17 % to −31 %,
against −2 % to −9 % in the constant-IV engine (dates inferred from the drops).

**D2 — the strike the alert names (recommended: nearest the money).** The
selector's score is liquidity over spread with no moneyness term; inside a
±2 ATR window the most liquid strike wins. On the scanner's own fixture
(price 100) it names the 103 call although the 100 strike is in the window;
with a resistance-capped target of 102.6 the named strike sits **above the
alert's own target**. In 8 of 24 delivered alerts the window reached above the
target (ACN, NOW, V, WMT, CME, PG, CVX, ABBV); which strike was picked there
depends on where the open interest sat. Recommended: among strikes passing the liquidity gates, the one nearest
the price at send, never above the target, and a moneyness leg in
`MEASURED_OPTION_STRUCTURE` and `option_structure_violation`. This follows from
BACKLOG 32 decision 1 (trade the measured structure, which is ATM), but it
changes which contract is named, so it is the owner's.

**D3 — the +200 % take-profit against the alert's target (recommended:
measure an arm first).** With the stock **at** a resistance-capped target
(1.3–2.0 ATR) the ATM call is +24 % to +73 %; at a full 4-ATR target about
+185 % (the repo's `bs_price`, IV = RV × 1.15, 7–14 days in). The option's
take-profit needs the stock to **overshoot** the target the alert names, so the
share signal can win while the option exits on time or thesis. Options:
require a 4-ATR target for an option alert, or exit the option when the
underlying reaches its target. Recommended: an `option_backtest` arm "exit
when the underlying touches the signal's target", run on the laptop, before
choosing.

**D4 — withhold when the R:R from the price at send is under 1.0
(recommended: yes).** The tier's R:R leg is asked at a price no one can fill
at. Built: the warning (N2). Withholding is a rule change.

**D5 — exits on the bid, and an expiry reminder.** STOP and TARGET read the
mid; a long sells at the bid, and the backtest marked exits half a modelled
spread below the mid. At bid 0.30 / ask 0.80 on a 1.00 entry the mid says
−45 % (no alert) and the bid −70 %; at 1.80 / 4.40 TARGET fires at "+210 %"
with the bid at +80 %. Separately, once STOP or TARGET has alerted nothing
else ever alerts, including TIME and expiry: the NOW position gets no TIME
alert on 11-13 and nothing on expiry day, and an unsold ITM call would ride
to auto-exercise silently. Recommended: trigger on the bid and print bid/ask;
an always-sent "expires within 2 sessions, still open" reminder.

**D6 — the thesis exit is one session late.** A settled close exists only
after 16:00 and `run()` skips every run after 16:00, so THESIS alerts at the
next 09:30, carrying the overnight gap; `option_backtest` exits at the
breaking close (65 of 395 measured exits). The last half hour is never
checked either (the 16:00 dispatch always lands after 16:00:00). Recommended: a
~15:50 ET dispatch that reads THESIS on the live price, or re-measure with the
thesis exit filled at the next open.

**D7 — the forward test's power (recommended: amend the stated MDE).** The
pre-registration sized the 349-episode gate on per-trade sd ≈ 1.0 R, inferred
from a 0.075 R detectable effect that was a **paired** figure. The record's own
graded rows have sd **1.78 R** (episodes about 1.4). At 349 episodes the test
detects about 0.21–0.27 R, not +0.15 R; +0.15 R needs roughly 680–1,130
episodes. H1′ and H2 scale the same way. A dated amendment stating the MDE at
349, or a larger gate; the stopping rule is the owner's.

**D8 — the pool's top five.** RS is never written to the record, so the cut
cannot be audited; with no SPY frame every RS is None and the five are chosen
alphabetically; there is no sector cap (10-30: V, MA and CME, three
Financials); a name with no contract inside the 8 % ceiling (UNP) still takes a
slot. Recommended: record `rs` and "cut by cap" on the row (plain), and decide
the sector cap and the untradeable skip.

## Decided 2026-11-03 (owner: "go with D1, D2, D4 and D5 as well") — built

- **D1** built as an alert-path gate: the latest expiry whose 7-DTE exit
  precedes the next release is `release − today + 6` DTE; under 21 the
  alert is withheld (one `NOT SENT` row), otherwise the contract is chosen
  only among expiries at or under it. An unknown release sends, with the
  warning.
- **D2** built: nearest the money among liquid strikes, inside 5 % of the
  price at send, never beyond the target; the journal refuses a signal
  trade outside 5 % of the underlying. The 5 % is a judgement (two strike
  increments on a liquid large cap), not a measurement: the measurement is
  ATM.
- **D4** built: withheld when the R:R from the price at send is under 1.0.
- **D5** built: STOP and TARGET read the bid on a two-sided quote; a
  once-only expiry reminder (within 2 sessions, and after expiry) whatever
  the rules say; `--diagnose` reports the monitor's own verdict.

The forward record is untouched by all four (the intake note of
2026-11-03 in `forward_record_preregistration.md` says so).

## Decided 2026-11-03, later (owner: "go with D6 and D8 as well") — built

- **D6** built: from 35 minutes before the close (13:00 on a half day) the
  thesis exit reads the live price against an EMA20 that includes it, a
  stand-in for the close the measured exit leaves at. The existing 15:30
  run is inside the window; earlier runs read settled closes only. A
  15:45 ET backup schedule is added; a 15:45 slot on the external
  dispatcher is the reliable version (the owner's setting).
- **D8** built: at most two pool alerts a sector a session (the
  universe's own cap); a name with no tradeable contract is withheld and
  takes no slot; with no SPY benchmark the five rank by ADX then R:R (the
  record's declared rank), not the alphabet; RS and the cut are recorded.

## Decided 2026-11-03, last (owner: "go with D3 and D7 as well")

- **D7** built: a dated amendment corrects the forward record's stated
  power (sd 1.0 R was a paired figure; the record's episodes run ~1.3-1.5 R).
  At the unchanged 349 gate the main question detects about +0.20 R and the
  rank tier about +0.49 R; the report now computes these from the record.
- **D3** built as a pre-registered arm in `option_backtest` (off by
  default), with its power checked through the engine; the run is owed on
  the laptop's frozen bars (`results/option_target_exit_preregistration.md`).
  Nothing in the live system changes until it reads ADOPT.

Every decision of this review is now taken.

## Recorded, not fixed

- **FIXED 2026-11-03 (owner: "fix the Tiingo split adjustment as well"):
  `data_source.fetch_tiingo()` now builds split-only bars from Tiingo's
  `splitFactor`, and a split after the fill voids the row as on Yahoo's bars
  (`check_tiingo_split_adjusted`). The end-of-day gap is FIXED too: on a
  fallback day the fill is read from Tiingo's IEX quote, or the alert is
  withheld.** As found: **Do not set `TIINGO_API_KEY`
  yet (P0 if set).** Tiingo's close and volume
  are not split-adjusted, Yahoo's are; the grader's split guard relies on the
  provider rewriting the signal bar. On a constructed frame, a 2:1 split
  after the fill grades as **LOSS −19.02 R** on Tiingo-style bars where Yahoo-style bars void it. Both
  workflows already pass the secret, so setting it turns this on with no code
  change. Fix: split-only-adjusted bars from Tiingo's split factor, or have the
  grader refuse non-Yahoo frames.
- **The post-close slot was decided on a counter with a blind spot** (item 39):
  `record_revision` only runs when both reads signal, so a bar that signals at
  16:20 and not on the settled morning read (or the reverse) writes nothing
  (PFE 10-29/10-30); and the earnings window, anchored on the scan day, can
  flip the HQ tag with no data change. Instrumentation, not the slot.
- **Research engine (each needs a re-run on the laptop's frozen bars, so fix
  and re-run ship together):** one NaN High/Low/Close blanks ADX for the rest
  of the series and the ticker signals no more (the tally is never printed);
  the first 19 tradeable bars of each ticker read a truncated 10/20-bar
  look-back (on synthetic frames, 603 of 2,881 early-bar signals got different
  levels); a bar that opens
  beyond the target and then trades through the stop is booked −1 R in both
  engines (rare); MaxDD is computed in ticker order, not time order;
  same-ticker trades overlap (40 % on trending fixtures) while CIs assume
  independence; dividends are not credited on raw prices.
- **Option engine:** close-only marking against ~13 live polls a day (a
  Monte Carlo on the engine's own assumptions puts the live win rate 2–3 points
  under the measured one); the
  DTE adequacy check is inert (12 sessions needed against a 21-DTE floor);
  `iv_snapshot` accepts any IV > 0 (Yahoo's 1e-5 placeholder would pass).
- **Exit monitor:** the push retry loop is dead after a rebase conflict
  (needs `git rebase --abort` and a real merge); `--diagnose` disagrees with
  `run()` on stale quotes; rounding the mid to cents can flip a stop sitting
  exactly on −50 %; the app's strike window still centres on the settled close
  (its frame has no live price).
- **Hygiene:** `universe-snapshot.yml` installs unpinned; `churn_tracker`
  re-implements the ranking the universe fixed; `liquidity_check.py` defaults
  to 9 DTE and is not wired in; `live_cfg()`'s docstring claims more parity
  with the scanner than it has (R:R 0.5, no blackout).
- **Not a finding, an observation (BACKLOG 34 recorded the pattern):** in the
  record, signals whose target was capped near resistance (median 1.5 ATR)
  graded +0.54 R (58 rows) while full 4-ATR targets graded −0.25 R (115 rows;
  31 target hits, 84 stops). Rows overlap and the window is two months.

## What held

- **No look-ahead** in the signal, the levels, the regime or the weekly
  filter: every `ta` indicator recomputed on prefixes matches the full series
  exactly. `ta`'s ADX equals a textbook Wilder ADX to three decimals and is
  stable on the last row (the post-close pass and the morning scan compute the
  same value). Live's one year of history against ten differs by at most 1e-5
  of price (EMA50) and 5e-6 points (ADX) on the evaluated bar.
- `backtest.compute()` and `scanner.compute()` agree exactly; the grader and
  the research engine fill, walk and exit identically (516 setups, 0
  mismatches).
- Black-Scholes matches Hull and an independent integral to 1e-9; costs are
  one round trip; liquidity gates fail NaN and zero quotes; after-hours zeros
  are diagnosed, never alerted.
- Stops: 362 of 381 recorded stops sit at the 1.25-ATR level; the
  "tighter of structure and ATR" rule rarely binds.
- Calendar, DST and half days; every yfinance calendar shape parses and every
  date is checked; the SPY regime reads the settled bar and matches the
  backtest's rule; relative strength is settled and date-aligned.
