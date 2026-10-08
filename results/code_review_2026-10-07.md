# Code review — 2026-10-07: three weeks of production, and the deeper pass

Second review, against `88a5756` plus 37 automated commits since. The first
review (`code_review_2026-09-16.md`) found that checks existed and nothing read
their results. This one had something the first did not: **three weeks of the
unattended system running on the code that was shipped**, plus a web check of
the methodology the repo rests on.

Baseline: `consistency_check.py` 33/33, all 38 `--selftest`s pass. The forward
log works — 17 rows, chain intact, zero duplicate keys, outcomes correctly
refused. The 09-16 fixes did what they said. What follows is what the harness
cannot see, ordered by consequence.

---

## C1 — The unattended system has been running at 14–23% of its schedule, reporting success

Measured from GitHub's own run list, 09-16 → 10-07, 16 trading days:

| workflow | scheduled | fired | **in session** | since 10-02 |
|---|---:|---:|---:|---|
| Trading Scanner (cron 11:07/13:07/15:07 ET) | 48 | 22 (46%) | **11 (23%)** | **0 of 3 days** |
| Exit Monitor (cron hourly 09–17 ET) | 112 | 35 (31%) | **16 (14%)** | 1/day, 0 on 10-05 |

GitHub's scheduler is delivering runs 2–4 hours late and dropping slots. A run
that lands after 16:00 ET hits the market-hours guard, executes "Skip notice",
and the workflow reports **`success`** — the step list for the 10-07 scan reads
`Check market hours → success`, `Skip notice → success`, `Run scanner →
skipped`. The 11:07 ET slot has fired **zero** times in three weeks.

**What it cost, with the position file as witness:**

- **09-16 (FOMC day):** the monitor's only in-session run was **13:28 ET** — 32
  minutes *before* the 2:00 PM decision. The live NKE put went unmonitored
  through the announcement and the close.
- **AAPL 355C** opened 09-23 15:31. Checks: 09-24 13:44, 09-25 13:46, 09-28
  15:59, 09-29 14:28 → caught at **−82.0%** against a −50% rule. Four checks
  in six days.
- **PFE 29C** opened 09-30 15:23. Checks: 10-01 14:42, 10-02 14:13 → **−81.4%**.
- **NVDA 250C** was already −34.1% at the single check on the day it opened.

The "hourly compromise between responsiveness and not being deprioritised" in
`exit-monitor.yml`'s comments is, in practice, **once a day, mid-afternoon**.

**The outage detector cannot see any of this.** `is_total_outage()` fires when
every ticker fails *inside a run*. A run that never scans is invisible to it.
This is the exact failure the repo names as its worst — silence identical to a
quiet market — and it is happening now.

**External cause, confirmed:** GitHub community discussions
[#207346](https://github.com/orgs/community/discussions/207346) and
[#206019](https://github.com/orgs/community/discussions/206019) report
scheduled runs dropped and delayed by hours **since 2026-08-26**, with manual
dispatches unaffected. The documented mitigation is an external scheduler
calling the `workflow_dispatch` endpoint, with `schedule` kept as backup.

**Remedy, in order:** (1) an external trigger for both workflows — the
`workflow_dispatch` inputs already exist; (2) a positive heartbeat: every run
that *does* scan writes a `scan` row to the forward log (see H4), and the
scanner alerts when no in-session scan has happened for N trading days; (3) a
coverage report (the table above) as a `consistency_check`-style script run
weekly. None of this makes the stop a stop. It makes the silence audible.

## C2 — Two live positions have sat at −82% / −81% for 5 and 8 days, unclosed

`open_positions.json` right now: AAPL 355C `EXIT_SIGNALLED STOP −82.0%` detected
09-29, **expires 10-09 — two sessions from now**; PFE 29C `EXIT_SIGNALLED STOP
−81.4%` detected 10-02. Neither is closed in the journal. Both are
`source: discretionary`.

The monitor did its job: it alerted. The alert was not acted on. This is a
usage finding, not a code finding — but it is the one with money in it, and it
has the same shape as the 09-09 widening of the cron (a position found at
−71.7% against −50%). The app's "close these" list shows them; nothing nags.

## H1 — The live system trades one configuration; the research record describes another

| parameter | **live** (`signal_core.DEFAULTS`, scanner, app) | **record** (`bt.DEFAULTS` → `record_recheck`, all three cuts, `longshort_split`, `drift_null`, `exit_ab`, `atr_stop_test`, `inverted_arm`, `longs_only`, tranche C) | OOS `FROZEN` (591 trades, failed) |
|---|---|---|---|
| `adx_min` | **35** | **25** | 35 |
| `atr_stop_mult` | **1.25** | **1.0** | 1.25 |
| `atr_tgt_mult` | **4.0** | **3.0** | 4.0 |
| `volume_mult` | **1.2** | **1.0** | — |
| SPY regime | **on** | **off** | on |

`record_recheck.py:91` builds every recorded cut as `dict(bt.DEFAULTS,
tickers=…, years=…)`. `longshort_split.md` says so itself ("ADX-25", "1.0 ×
ATR stop | 3.0 × ATR target"). The comments on `bt.DEFAULTS` read `# app.py
default` — they are stale; app.py derives from `signal_core` and
`check_app_defaults_derived` pins that. **Nothing pins `backtest.DEFAULTS` to
`signal_core.DEFAULTS`.** The duplicated-constants check matches `NAME = value`
at module level and cannot see a dict literal.

So: the live scanner runs the configuration that was OOS-tested and failed
(−0.014 R, PF 0.98). The 1,386-trade record and every study built on it
measure a *looser* configuration the scanner does not run. Neither supports
trading, so the decision is unchanged — but the **forward log records the live
config's signals**, and comparing it to the research record without stating the
parameter difference is the basis error this repo keeps finding in other forms.
The forward log's `rr: 3.2` on every row (1.25 → 4.0) is the live geometry; the
record's is 3.0.

One correction to my own BACKLOG 21 falls out of this: I wrote that the stop
"sits at 1.25 × ATR and the target at 4.0 × ATR" when assessing the record's
gap bias. The record's geometry is 1.0 / 3.0. The argument is *stronger* — a
1.0 ATR stop is easier to gap through — but the numbers were the live ones.
Corrected in BACKLOG.

## H2 — The alert tier is gated by two features the research refuted, and it has produced zero alerts

Every forward-log row, with the filter that blocked it:

| blocked by | rows | evidence |
|---|---:|---|
| `ADX < 35` | **13 / 17** | NVDA at 13.7–14.8, AMD 25, CRWD 24.7, PFE 31, TMO 30.7–34.5 |
| `volume < 1.2× avg` (the "Strong" leg) | **4 / 17** | TMO 09-23 → 09-29, **4/4 filters passing**, RSI 71–75 |

`adx_retest.py` (Holm-corrected, dose-response) found ADX adds nothing.
`rvol_retest.py` and `rvol_confirm_run1.md` found RVOL **refuted
out-of-sample**. Those two are the only things standing between a base signal
and an alert, and together they blocked 100% of signals for three weeks.

TMO is the case study: a Bullish setup logged on 8 consecutive bars, ADX
climbing 30.7 → 43.1, RSI 67 → 75, `rr 3.2` throughout — blocked by ADX until
it crossed 35, then blocked by volume, while RSI ran toward the 75 band that
would have failed the *base* condition. A persistent, strengthening trend
produced no alert at any point.

The honest framing: these gates are not wrong *because* they filter on noise —
removing them yields more signals of the same measured non-edge. They are wrong
because **the forward test is the project's only remaining route to evidence,
and gating its intake on refuted features starves it.** 17 base signals and 0
taken in three weeks on 8 tickers is a rate at which the forward log will not
reach the power BACKLOG 13 demands in years.

## H3 — The option engine prices every contract at a constant, guessed IV

`option_backtest.simulate_option_trade()`: `iv = realised_vol(20d) × cfg["iv_mult"]`,
then `bs_price(spot, strike, t_left, iv, right)` on **every bar of the trade
with the same `iv`**. There is no vol-of-vol, no IV crush, no skew, no term
structure. `iv_mult` defaults to **1.15** and was set before any measurement.

`vrp_measurement.md` later measured the premium — **+3.63 vol points on SPX**
(VIX vs realised) — and says in its own text that "VIX is SPX implied vol. It
says nothing about whether MPC's, CRM's or CRWD's implied vol is rich. Single
names carry earnings and idiosyncratic jump risk." `option_backtest.py` runs on
TSLA, NVDA, AAPL, MSFT, AMZN, META, ROKU. The code does not cite the
measurement; the measurement says it does not transfer.

**Everything downstream inherits this:** `OPT_WIN_RATE = 0.249`, the realised
breakeven table, the spread ceiling's loss-cap rationale, the app's EV panel.
And the two live positions that went from a −50% rule to −82% realised are
precisely the premium gap a constant-IV path cannot produce — the model's STOP
fires on a smooth Black-Scholes curve; real premium gaps at the open on a vol
shock.

## H4 — The forward log cannot tell "no signal" from "no scan"

Bars **09-30, 10-02, 10-05, 10-06** have no rows. Not because nothing fired —
because the scan that would have evaluated each bar (the next day's) landed
after the close and skipped. A missing row means either, and the log — built
to be the clean record — cannot say which.

Fix: a `scan` row per executed run (bar evaluated, tickers, n_signals, config).
Absence of signal becomes a positive fact; absence of scan becomes visible.
It also gives C1's heartbeat for free.

## H5 — An 8-name universe that rotated 7 of 8 names in three weeks

| snapshot | added | dropped |
|---|---|---|
| 09-20 | TGT | SCHW |
| 09-27 | **AMD, CRWD, NVDA, PFE** | ABNB, MRK, NOW, TGT |
| 10-04 | ACN, TGT | AMD, CRWD |

AMD, CRWD and NVDA were in the universe for exactly one week. Their forward-log
signals appear 09-28 → 10-01 and then the names leave — a setup cannot be
followed through. TMO's 8-bar run survived only because TMO happened to stay.

`churn_tracker.verdict()` flags only a *mean* interval turnover > 50%; the 09-27
swap was exactly 50%, the three-interval mean is ~29%, so nothing flagged. It
has no cumulative view. And BACKLOG's own power arithmetic says detecting
+0.085 R needs ~50 tickers; the live intake is 8, rotating.

---

## M1 — The sweep that chose the live parameters exceeded the overfitting guidance, and was never deflated

`data_reservation.py` records the Aug 2026 sweep as **~60 configurations** on 5
years × 7 tickers. Bailey & López de Prado's worked example puts the ceiling at
**~45 variations on five years of daily data** before the selected strategy is
expected to be overfit ([DSR paper](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf),
[PBO paper](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)). The
repo applies Holm correction *within* each later study — correctly — but never
computed a Deflated Sharpe or PBO for the selection step itself. The OOS
failure is what PBO predicts, so this is confirmatory, not a new risk. It is
recorded because the selection step is the one undocumented statistical act in
an otherwise pre-registered project.

## M2 — `oos_validate` approximates variance it could compute exactly

`two_point_variance()` rebuilds per-trade variance from win rate, expectancy and
profit factor because, its docstring says, there are "no per-trade logs." But
`bt.run()` returns the trade list (`backtest.py` ~1215, "Return the trades so
callers can judge them rather than re-parse stdout"). The docstring admits the
approximation **understates variance → inflates the t-stat**. The 591-trade CI
[−0.124, +0.096] rests on it. The verdict was FAIL regardless, so no false
pass — but the harness scrapes stdout with regexes tuned to `tabulate`'s layout
(`requirements.txt` says so) when the numbers are a return value away.

## M3 — The live scanner's import closure includes the research engine

> **CORRECTION 2026-10-08 — this finding was overstated.** The
> `import backtest as bt` in `market_context.py` is at line 489, *inside*
> `selftest()`, not at module level. `scanner → market_context` does **not**
> pull `backtest` in; the parity check runs only under `--selftest`. Verified
> by reading the module (`python -c "import scanner, sys; print('backtest' in
> sys.modules)"` prints `False`). Nothing needs moving. The paragraph below is
> left as written so the error is visible, per this repo's convention.

`market_context` imports `backtest` for `build_regime_series` parity. So
`scanner → market_context → backtest` pulls a 2,197-line research module (and
yfinance through it) into the unattended path at import time. The
"Streamlit-free" check does not cover this. A module-level side effect or heavy
import added to `backtest.py` lands in the scanner.

## M4 — Production runs dependency versions CI never tested

`requirements.txt` pins `yfinance==1.6.0`, `pandas==3.0.5`; `tests.yml`
installs from it. `scanner.yml` and `exit-monitor.yml` run `pip install
yfinance pandas ta requests pytz` — **unpinned** — and the 10-02 scan log shows
`yfinance-1.7.0 pandas-3.0.6`. The live money path runs versions the selftests
have not seen. yfinance has a history of behaviour changes between minors
(`auto_adjust` default flip, 429 handling); the repo guards against the first
by pinning the flag, not the version.

## M5 — The price fallback has never been active

`TIINGO_API_KEY:` is **empty** in both workflow logs. The data_source fallback
is documented as optional, and it has been optional in practice: Yahoo is the
single point of failure for the scanner, the monitor's THESIS rule, and the
app.

## M6 — Smaller items

- **Node 20 deprecation** on `actions/checkout@v4` and `actions/setup-python@v5`
  — every run warns; both will break when GitHub stops forcing Node 24.
- **`attach_outcomes` nags forever**: the NKE `no_match` is logged on every scan
  and will be until the journal row is deleted. Needs an age cutoff or a memo.
- **`scanner_state.json` never prunes**: cooldown entries from August persist.
- **Win rate counts dust**: NKE closed +0.04 (+$4) and is a WIN in `journal_stats`;
  the 0.05 R dust floor applies to PF only.
- **Stale comments** on `bt.DEFAULTS` (`# app.py default`) — see H1.
- **`churn_tracker`** has no cumulative-turnover view — see H5.

---

## The FOMC day, for the record

The Fed hiked 25bp to 3.75–4.00%, 12–0, Warsh's first meeting; the S&P **rose**
— the "rally on a hike" outcome several outlets had flagged as the odd one
([CNBC](https://www.cnbc.com/2026/09/16/fed-rate-decision-september-2026.html),
[Advisor Perspectives](https://www.advisorperspectives.com/dshort/updates/2026/09/16/feds-interest-rate-decision-september-16-2026)).
The live NKE put closed 09-22 at **+$4** (+0.04 on premium), "Manual exit." The
monitor's only in-session check that day was 13:28 ET. The pre-decision
observation stands: there is an earnings blackout and no macro-event blackout.

## What held up

Stated because a review that only reports problems is not a measurement.

The 09-16 fixes work in production: one row per bar, chain intact, correct
refusals. The leakage hygiene — unsettled-bar handling, next-open fill, weekly
lag, raw prices pinned, bar-cache fingerprints — is exactly the class of error
that DSR/PBO *cannot* correct ([a leaky oracle at Sharpe 35 passes both](https://arxiv.org/pdf/2605.04004)),
and this repo handles it better than most. The reporting corpus is ~50 files,
every run paired with a pre-registration, voids recorded in place. The decision
the record informs — do not trade the mechanical signal — is not moved by
anything above; several findings reinforce it.

## The pattern, again

The first review's four findings were "the check exists and nothing reads its
result." This review's are **"the system is measuring one thing and running
another"**: a research config that is not the live config, an alert tier gated
on features the research refuted, an option model priced at a premium the
measurement said does not transfer, a schedule that fires a quarter of the
time while every run says success, and a record that cannot distinguish a quiet
market from a silent scanner. Each would have been invisible without three
weeks of production to read against the code.

Sources: [GitHub community #207346](https://github.com/orgs/community/discussions/207346) ·
[#206019](https://github.com/orgs/community/discussions/206019) ·
[cron drift analysis](https://crontap.com/blog/github-actions-cron-drift-problem) ·
[Bailey & López de Prado, DSR](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) ·
[Bailey et al., PBO](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) ·
[CNBC, Fed decision](https://www.cnbc.com/2026/09/16/fed-rate-decision-september-2026.html) ·
[yfinance auto_adjust behaviour](https://softhints.com/understanding-yfinance-auto_adjust-true-what-changed-and-how-to-fix-it/)
