"""
Trading Scanner — scheduled watchlist scan with Telegram alerts
================================================================
Runs on GitHub Actions during market hours and alerts on qualifying setups.

FIXES IN THIS VERSION (all five were verified in the previous script):

1. SILENTLY LONG-ONLY.  strength was `rsi > 60 and macd > signal` — a bullish
   test applied to BOTH directions, and anything not "Strong" was discarded.
   A bearish setup needs price < EMA20 < EMA50, where RSI is low by
   definition, so no short signal could ever fire. Strength is now
   direction-aware.

2. MIXED REFERENCE POINTS.  entry came from a past bar's high while stop and
   target came from the live price, and abs() hid the damage: with the 5-bar
   high 5% above price, entry (105) sat ABOVE target (104) yet still reported
   a positive R:R. Worst of all it penalised true breakouts, where price IS
   the 5-bar high. All three levels are now anchored to the same reference.

3. TOO LITTLE HISTORY.  period="3mo" is ~63 bars. EMA50 needs ~150, MACD ~78,
   RSI/ATR ~100. Measured across 400 simulated series, the trend verdict
   differed from full-history 6.5% of the time. Now fetches 1y and discards
   the unconverged head.

4. ALERT SPAM.  No dedup meant a setup that stayed valid re-fired on every
   run — 13 identical messages a day at 30-minute cadence. Now state-backed
   with a per-ticker-per-direction cooldown.

5. ONE BAD TICKER KILLED THE SCAN.  No try/except in the loop, so a yfinance
   failure on ticker 3 of 14 meant the other 11 were never checked. And
   rr = .../abs(entry - stop) raised ZeroDivisionError when entry == stop.
   Each ticker is now isolated and the risk gate is relative to price.

Run
---
    pip install yfinance pandas ta requests pytz
    export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
    python scanner.py

    python scanner.py --dry-run --force    # print, send nothing, ignore hours
    python scanner.py --record-only        # post-close: forward-log rows +
                                           # heartbeat for today's bar, no alerts
"""
from __future__ import annotations

import argparse
import json
import os
import time
import logging
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
import pytz
import ta
import yfinance as yf

import data_source
import market_context

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("scanner")

ET = pytz.timezone("America/New_York")

# ── Watchlist ──
# STATIC_WATCHLIST is the frozen baseline — the config selected by the Aug 2026
# sweep. Kept as the fallback because it is the best-DEFINED hypothesis tested,
# not because it is profitable: the 591-trade out-of-sample validation returned
# -0.014 R with PF 0.98.
#
# When USE_DYNAMIC_UNIVERSE is set, the list comes from the newest snapshot in
# universe_history/, written by the weekly scheduled job. Same env var app.py
# reads, so the two can never scan different universes — alerting on tickers
# the app will not show you is worse than either choice on its own.
STATIC_WATCHLIST = ["NVDA", "META", "MSFT"]


def resolve_watchlist() -> list[str]:
    """
    Resolve the scan list at startup and log which source won.

    allow_live=False on purpose: ranking the universe pulls ~67 symbols
    through yf.download, and this process still has a whole watchlist to fetch
    through the same Yahoo rate budget. If no snapshot exists, fall back to the
    static list rather than spending the budget on ranking.
    """
    try:
        import universe as _u
    except Exception as e:
        logger.warning("universe.py unavailable (%s) — using static watchlist", e)
        return STATIC_WATCHLIST

    if not _u.dynamic_enabled():
        logger.info("Watchlist: static baseline (%s). Set %s=1 for the "
                    "RS-ranked universe.", ", ".join(STATIC_WATCHLIST),
                    _u.ENV_TOGGLE)
        return STATIC_WATCHLIST

    tickers, status, age, source = _u.load_universe(
        STATIC_WATCHLIST, allow_live=False)
    if source == "snapshot":
        logger.info("Watchlist: dynamic — %s (%dd old): %s",
                    status, age, ", ".join(tickers))
    elif source == "snapshot-stale":
        logger.warning("Watchlist: %s. Scanning it anyway: %s",
                       status, ", ".join(tickers))
    else:
        logger.warning("Watchlist: %s — falling back to %s",
                       status, ", ".join(tickers))
    return tickers


WATCHLIST = resolve_watchlist()

import signal_core as sc
import forward_log
import risk_params
import notify

# ── Tunables ──
# These now come from signal_core.DEFAULTS so app.py and scanner.py cannot
# drift again. Overriding any of them here would recreate the exact bug this
# refactor removed, so don't — change signal_core.SignalParams instead.
PARAMS             = sc.DEFAULTS

# FIX: these had drifted from app.py, which meant Telegram alerts were firing
# on a looser configuration than the app scanned with — ADX 25 vs 35, target
# 3.0x vs 4.0x, R:R 2.0 vs 0.5. Two systems disagreeing about what counts as a
# signal is worse than either being wrong, because you cannot tell which one
# produced a given trade. Now matched to app.py's frozen baseline.
# NOTE: ADX_MIN / ATR_STOP_MULT / ATR_TGT_MULT / MIN_RR used to live here as
# module constants. They were removed once analyze() started reading PARAMS
# directly: leaving them would be actively misleading, since editing them
# would look like it changed the signal and would in fact do nothing. Change
# signal_core.SignalParams instead.
EARNINGS_BLACKOUT_DAYS = 3   # matches app.py sidebar default
POST_EARNINGS_DAYS     = 1   # matches app.py sidebar default
ALERT_COOLDOWN_HRS = 4      # per ticker AND direction
STATE_FILE         = Path("scanner_state.json")
STATE_MAX_AGE_DAYS = 30     # cooldown stamps older than this are pruned
POST_CLOSE_WINDOW_HRS = 2   # --record-only runs this long after the close

# ── Option suggestion ──
# DTE window is centred on 30 deliberately: option_backtest.py measured this
# strategy with a 30-DTE entry, so suggesting 7-DTE contracts would be
# recommending something never tested. 21-45 keeps live trades comparable to
# the backtest they are supposed to be validating.
SUGGEST_OPTIONS  = True
OPT_MIN_DTE      = 21
OPT_MAX_DTE      = 45
OPT_MAX_EXPIRIES = 3        # each expiry is one chain fetch — keep it lean
# Single-sourced from risk_params.py, not chosen here.
#
# This said "at the old 15% the strategy needed a 26.5% win rate against a
# measured 23.8%". That 26.5% was a TP+200 breakeven while OPT_WIN_RATE was
# measured at TP+100 — the basis mismatch corrected on 2026-09-10. At the
# measured basis 15% needs 44.1% and the 8% ceiling needs 38.9%, so no spread
# clears 23.8%: this is a loss cap, not a profitability threshold.
OPT_MAX_SPREAD   = risk_params.MAX_OPTION_SPREAD_PCT

# Shown in the alert for context. NOT used to filter during the test phase —
# you asked to see every suggestion and judge affordability yourself.
# Sizing constants come from risk_params.py so app.py and this file cannot
# drift. NOTE the rename: this percentage is the MAX PREMIUM PER CONTRACT,
# because on a long option the premium is the maximum loss. app.py's RISK_PCT
# is a different quantity — the fraction of the account risked between entry
# and stop. They were both called RISK_PCT and a review read the 1% vs 5% gap
# as a 5x position-sizing bug; it was not, but one name meaning two things is
# exactly how the earlier app/scanner divergences started.
ACCOUNT_SIZE      = risk_params.DEFAULT_ACCOUNT_SIZE
OPTION_BUDGET_PCT = risk_params.DEFAULT_OPTION_BUDGET_PCT

# Indicator warm-up — same reasoning as the Streamlit app
FETCH_PERIOD          = "1y"
INDICATOR_WARMUP_BARS = 100
MIN_BARS_AFTER_WARMUP = 40

# Politeness gap between Yahoo calls. 14 tickers hammered back-to-back is a
# meaningful share of the same rate limit the options tooling needs.
FETCH_GAP_SEC = 1.2     # was 0.6. The shared-signal refactor added the weekly,
                        # earnings and regime fetches the scanner previously
                        # skipped, taking a scan from ~1 call per ticker to ~3
                        # plus one SPY call — roughly 3x the Yahoo traffic.
                        # At 8 tickers that is ~25 calls; 1.2s spacing spreads
                        # them over ~30s, which is nothing against the job's
                        # timeout and cheap insurance against a limiter trip.
                        # Rate limiting costs a whole scan cycle; 15 extra
                        # seconds costs nothing.

# Retry/backoff for Yahoo calls. Every OTHER Yahoo-calling function across this
# project (the app's get_data, its options engine, exit_monitor.py) already
# retries with escalating backoff. get_data() and suggest_option() here did
# not — a single 429 failed the ticker outright with no second attempt. That
# gap, combined with GitHub Actions running from a shared datacenter IP range
# (heavily used by other jobs hitting Yahoo the same hour), is what produced
# "always rate limited": no retry meant no chance to wait out a busy moment.
YF_RETRY_ATTEMPTS = 4
YF_RETRY_DELAY    = 3.0    # seconds; doubles each attempt (3 -> 6 -> 12)


def _is_rate_limit_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "too many requests" in msg or "rate limit" in msg or "429" in msg


MARKET_HOLIDAYS = {
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27",
    "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}
MARKET_HALF_DAYS = {"2025-07-03", "2025-11-28", "2025-12-24",
                    "2026-11-27", "2026-12-24",
    "2027-11-26",}


def is_market_open(now: datetime | None = None) -> bool:
    """Weekday + hours + HOLIDAYS. The old version ignored holidays entirely."""
    now = now or datetime.now(ET)
    if now.weekday() >= 5:
        return False
    day = now.strftime("%Y-%m-%d")
    if day in MARKET_HOLIDAYS:
        return False
    ch, cm = (13, 0) if day in MARKET_HALF_DAYS else (16, 0)
    return (now.replace(hour=9, minute=30, second=0, microsecond=0)
            <= now <= now.replace(hour=ch, minute=cm, second=0, microsecond=0))


def is_post_close_window(now: datetime | None = None) -> bool:
    """
    The POST_CLOSE_WINDOW_HRS after the close on a trading day — when today's
    bar is settled and the market is shut. This is when --record-only runs.

    Same calendar as is_market_open(): weekday, not a holiday, and a half day
    closes at 13:00. Strictly AFTER the close: at 16:00:00 exactly the session
    is still "open" to is_market_open(), and the two must never both be true.
    """
    now = now or datetime.now(ET)
    if now.weekday() >= 5:
        return False
    day = now.strftime("%Y-%m-%d")
    if day in MARKET_HOLIDAYS:
        return False
    ch = 13 if day in MARKET_HALF_DAYS else 16
    close = now.replace(hour=ch, minute=0, second=0, microsecond=0)
    return close < now <= close + timedelta(hours=POST_CLOSE_WINDOW_HRS)


# ══════════════════════════════════════════════════════════════════
# ALERT STATE (dedup)
# ══════════════════════════════════════════════════════════════════
def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except Exception as e:
        logger.warning("Could not read %s (%s) — starting fresh", STATE_FILE, e)
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))
    except Exception as e:
        logger.error("Could not write %s: %s — dedup will not persist, so "
                     "expect repeat alerts next run.", STATE_FILE, e)


def prune_state(state: dict, now: float | None = None,
                max_age_days: int = STATE_MAX_AGE_DAYS) -> int:
    """
    Drop cooldown stamps older than `max_age_days`. Returns how many went.

    Every value in scanner_state.json is an epoch: "TICKER:Trend" for the
    alert cooldown, "SCANNER:OUTAGE" and "SCANNER:COVERAGE" for the
    operational ones. The longest cooldown is 20 hours, so a stamp a month
    old gates nothing — it is just a line the file carries forever, and the
    file is committed on every run. Entries from August were still there in
    October. A value that is not a number is left alone rather than guessed
    at.
    """
    now = time.time() if now is None else now
    cutoff = now - max_age_days * 86400
    gone = [k for k, v in state.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
            and v < cutoff]
    for k in gone:
        del state[k]
    return len(gone)


def is_total_outage(failed: list, watchlist: list) -> bool:
    """
    Every ticker failed to return usable data — the scan did not happen.

    Kept separate from the alerting so it can be tested. An empty watchlist is
    NOT an outage: nothing was asked for, so nothing failing is consistent.
    Without that guard a misconfigured watchlist would page you every run.
    """
    return bool(watchlist) and len(failed) == len(watchlist)


def recently_alerted(state: dict, ticker: str, trend: str) -> bool:
    """
    Cooldown is keyed on ticker AND direction, so a genuine flip from Bullish
    to Bearish still alerts immediately rather than being suppressed.
    """
    last = state.get(f"{ticker}:{trend}")
    if not last:
        return False
    return (time.time() - float(last)) < ALERT_COOLDOWN_HRS * 3600


# ══════════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════════
def send_alert(message: str, dry_run: bool = False) -> bool:
    """
    Delegates to notify.py, which retries transient failures.

    This used to be a bare requests.post with no retry: a single blip between
    the Actions runner and Telegram dropped the alert silently, and "it did
    not send" looked exactly like "there was nothing to send".
    """
    return notify.send(message, dry_run=dry_run)


# ══════════════════════════════════════════════════════════════════
# DATA + INDICATORS
# ══════════════════════════════════════════════════════════════════
def _yf_download_with_retry(ticker: str, period: str, interval: str) -> pd.DataFrame | None:
    """
    BUG FIX: previously called yf.download() ONCE with no retry, unlike every
    other Yahoo-calling function in this project. A single 429 failed the
    ticker outright. Now retries with escalating backoff (3s -> 6s -> 12s)
    before giving up, matching get_data() in app.py and exit_monitor.py.

    This is the Yahoo LEG only — wrapped so data_source.fetch_daily() can try
    a second provider when Yahoo keeps coming back empty after every retry,
    instead of the whole scan failing on that ticker.
    """
    delay = YF_RETRY_DELAY
    last_err = None
    for attempt in range(YF_RETRY_ATTEMPTS):
        try:
            return yf.download(ticker, period=period, interval=interval,
                               progress=False, auto_adjust=False)
        except Exception as e:
            last_err = e
            if _is_rate_limit_error(e) and attempt < YF_RETRY_ATTEMPTS - 1:
                logger.warning("Rate limited get_data(%s); backoff %ss "
                               "(attempt %d/%d)", ticker, delay, attempt + 1,
                               YF_RETRY_ATTEMPTS)
                time.sleep(delay)
                delay *= 2
                continue
            logger.warning("get_data(%s) Yahoo leg failed: %s", ticker, e)
            return None
    logger.warning("get_data(%s) exhausted Yahoo retries: %s", ticker, last_err)
    return None


def get_data(ticker: str) -> pd.DataFrame | None:
    """
    Yahoo first, then data_source's fallback (Tiingo, if TIINGO_API_KEY is
    set) when Yahoo comes back empty after every retry above. Before this,
    the scanner had no fallback at all — only app.py did — so a Yahoo outage
    or throttle took the scanner down even on days the app kept working via
    Stooq/Tiingo. See data_source.py's docstring for what the fallback does
    and does not cover.
    """
    df, source = data_source.fetch_daily(
        ticker, period=FETCH_PERIOD, interval="1d",
        yahoo_fetch=lambda t, p, i: _yf_download_with_retry(t, p, i))
    if df is None or df.empty:
        return None
    if source != "yahoo":
        logger.info("%s: Yahoo unavailable, used %s fallback", ticker, source)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])


def compute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["EMA20"]  = ta.trend.ema_indicator(df["Close"], 20)
    df["EMA50"]  = ta.trend.ema_indicator(df["Close"], 50)
    macd         = ta.trend.MACD(df["Close"])
    df["MACD"]   = macd.macd()
    df["Signal"] = macd.macd_signal()
    df["RSI"]    = ta.momentum.rsi(df["Close"], 14)
    df["ATR"]    = ta.volatility.average_true_range(df["High"], df["Low"],
                                                    df["Close"], 14)
    df["ADX"]    = ta.trend.adx(df["High"], df["Low"], df["Close"], 14)
    df["VOL_AVG20"] = df["Volume"].rolling(20).mean()
    df = df.dropna(subset=["EMA20", "EMA50", "MACD", "Signal", "RSI", "ATR", "ADX", "VOL_AVG20"])
    # Discard the unconverged head so EMA50/MACD/ADX are trustworthy
    if len(df) > INDICATOR_WARMUP_BARS + MIN_BARS_AFTER_WARMUP:
        df = df.iloc[INDICATOR_WARMUP_BARS:]
    return df


# ══════════════════════════════════════════════════════════════════
# OPTION SUGGESTION
#
# Picks a liquid, near-the-money contract for the signal so the alert tells you
# WHAT to buy, not just which way to lean. Ported from the app's selection
# logic, with two differences: the DTE window is pinned to the range the
# backtest used, and cost is reported against your account so you can see
# affordability at a glance.
#
# Called ONLY after the cooldown check passes, so suppressed alerts never spend
# option-chain API calls. That ordering matters: chains are the single most
# rate-limit-expensive thing this script can do.
# ══════════════════════════════════════════════════════════════════
def suggest_option(ticker: str, price: float, trend: str,
                   atr: float) -> dict | None:
    try:
        stock = yf.Ticker(ticker)
        expiries = None
        delay = YF_RETRY_DELAY
        for attempt in range(YF_RETRY_ATTEMPTS):
            try:
                expiries = list(stock.options or [])
                break
            except Exception as e:
                if _is_rate_limit_error(e) and attempt < YF_RETRY_ATTEMPTS - 1:
                    logger.warning("Rate limited options(%s); backoff %ss", ticker, delay)
                    time.sleep(delay)
                    delay *= 2
                    continue
                logger.warning("%s expiries fetch failed: %s", ticker, e)
                return None
        if not expiries:
            return None

        today = pd.Timestamp.today().normalize()
        right = "CALL" if trend == "Bullish" else "PUT"

        # Strike window sized to +-2 ATR, clamped to 3-12%. A flat 5% band means
        # something very different on a 1%-ATR index than a 6%-ATR small cap.
        band = min(max((atr * 2.0) / price, 0.03), 0.12) if price > 0 else 0.05
        lo, hi = price * (1 - band), price * (1 + band)

        best, best_score, checked = None, 0.0, 0
        for exp in expiries:
            if checked >= OPT_MAX_EXPIRIES:
                break
            try:
                dte = (pd.Timestamp(exp) - today).days
            except Exception:
                continue
            if not (OPT_MIN_DTE <= dte <= OPT_MAX_DTE):
                continue
            checked += 1
            chain = None
            c_delay = YF_RETRY_DELAY
            for c_attempt in range(YF_RETRY_ATTEMPTS):
                try:
                    time.sleep(0.5)
                    chain = stock.option_chain(exp)
                    break
                except Exception as e:
                    if _is_rate_limit_error(e) and c_attempt < YF_RETRY_ATTEMPTS - 1:
                        logger.warning("Rate limited chain %s %s; backoff %ss",
                                       ticker, exp, c_delay)
                        time.sleep(c_delay)
                        c_delay *= 2
                        continue
                    logger.warning("%s chain %s failed: %s", ticker, exp, e)
                    chain = None
                    break
            if chain is None:
                continue

            df = chain.calls if right == "CALL" else chain.puts
            if df is None or df.empty:
                continue
            df = df[(df["strike"] >= lo) & (df["strike"] <= hi)].copy()
            if df.empty:
                continue

            df["mid"]    = (df["bid"] + df["ask"]) / 2
            df["spread"] = df["ask"] - df["bid"]
            # bid>0 and volume>0 matter: a mid can look fine on a contract with
            # no bid at all, and a zero-volume contract is untradeable however
            # much open interest it carries.
            df = df[(df["mid"] > 0) & (df["bid"] > 0) &
                    (df["volume"].fillna(0) > 0) &
                    (df["openInterest"].fillna(0) > 0)]
            if df.empty:
                continue
            df["spread_pct"] = df["spread"] / df["mid"] * 100
            df = df[df["spread_pct"] <= OPT_MAX_SPREAD]
            if df.empty:
                continue

            df["score"] = ((df["volume"].fillna(0) + df["openInterest"].fillna(0))
                           / (1 + df["spread_pct"] / 10))
            top = df.sort_values("score", ascending=False).iloc[0]
            if float(top["score"]) > best_score:
                best_score = float(top["score"])
                best = (top, exp, dte)

        if best is None:
            return None
        row, exp, dte = best
        mid  = float(row["mid"])
        cost = mid * 100
        budget = risk_params.option_budget(ACCOUNT_SIZE, OPTION_BUDGET_PCT)
        return {
            "right": right, "strike": float(row["strike"]), "expiry": exp,
            "dte": dte, "mid": round(mid, 2), "bid": float(row["bid"]),
            "ask": float(row["ask"]),
            "spread_pct": round(float(row["spread_pct"]), 1),
            "volume": int(row["volume"] or 0), "oi": int(row["openInterest"] or 0),
            "cost": round(cost, 2),
            "pct_account": round(cost / ACCOUNT_SIZE * 100, 1),
            "within_budget": cost <= budget,
            "budget": round(budget, 2),
        }
    except Exception as e:
        logger.warning("suggest_option(%s) failed: %s", ticker, e)
        return None


# ══════════════════════════════════════════════════════════════════
# ANALYSIS
# ══════════════════════════════════════════════════════════════════
def get_weekly_trend(ticker: str) -> str | None:
    """
    Weekly-timeframe direction, for the multi-timeframe filter.

    Now delegates to market_context — the SAME rule app.py uses. app.py used to
    run an EMA10w-vs-EMA20w crossover here while this file used price-vs-EMA20w:
    different questions, opposite verdicts on ordinary pullbacks, on a BLOCKING
    filter. Returns None on any failure — signal_core treats None as blocking
    rather than guessing.
    """
    def _fetch(t, period, interval):
        time.sleep(FETCH_GAP_SEC)
        df, source = data_source.fetch_daily(
            t, period=period, interval=interval,
            yahoo_fetch=lambda tt, pp, ii: yf.download(
                tt, period=pp, interval=ii, progress=False, auto_adjust=False))
        if df is not None and source != "yahoo":
            logger.info("%s weekly: Yahoo unavailable, used %s fallback",
                        t, source)
        return df
    return market_context.get_weekly_trend(ticker, _fetch)


def _earnings_calendar(ticker: str):
    """The raw yfinance calendar payload, behind this module's fetch gap."""
    time.sleep(FETCH_GAP_SEC)
    return yf.Ticker(ticker).calendar


def check_earnings_blackout(ticker: str) -> tuple[bool, str]:
    """
    ROUTED THROUGH market_context — one implementation, as with the weekly
    trend and the SPY regime.

    This copy and app.py's parsed the yfinance calendar differently: app read
    only the FIRST earnings date, this one iterated all of them. yfinance
    routinely returns a RANGE of two estimates for one event, so the two could
    reach opposite verdicts on the same ticker at the same moment. Reading
    every date — the behaviour this copy already had — is what the shared rule
    keeps, because a calendar entry inside the window should block whichever
    position it holds in the list.

    Still ET, still fails open. `today` is passed explicitly because
    market_context refuses to default it: a host-clock default is the bug this
    repo has already fixed twice, and the runners here are UTC.
    """
    return market_context.get_earnings_blackout(
        ticker, _earnings_calendar, datetime.now(ET).date(),
        blackout_days=EARNINGS_BLACKOUT_DAYS, post_days=POST_EARNINGS_DAYS)


def get_spy_regime() -> dict | None:
    """
    SPY vs its 200-SMA — the macro regime gate, via market_context so app.py
    and this file cannot diverge. Two-state and ungated, matching
    backtest.build_regime_series(), which is the version the 591-trade
    out-of-sample test actually ran (oos_validate FROZEN use_regime=True).

    Returns None when the regime is Unknown, preserving this function's
    previous contract with run(): signal_core skips the filter on a falsy
    spy_regime, which is the right response to "no data".
    """
    def _fetch(t, period, interval):
        time.sleep(FETCH_GAP_SEC)
        spy, source = data_source.fetch_daily(
            t, period=period, interval=interval,
            yahoo_fetch=lambda tt, pp, ii: yf.download(
                tt, period=pp, interval=ii, progress=False, auto_adjust=False))
        if spy is not None and source != "yahoo":
            logger.info("SPY regime: Yahoo unavailable, used %s fallback", source)
        return spy
    r = market_context.get_spy_regime(_fetch)
    if r.get("regime") in (None, "Unknown"):
        logger.warning("SPY regime unavailable (%s)", r.get("reasoning"))
        return None
    return r


def analyze(df: pd.DataFrame, ticker: str,
            spy_regime: dict | None = None,
            tally: dict | None = None) -> dict | None:
    """
    Thin adapter over signal_core.evaluate().

    THIS USED TO BE A SECOND IMPLEMENTATION. It applied four gates — trend
    stack, MACD, ADX, R:R — while app.py applied those plus volume, weekly
    alignment, earnings blackout and macro regime. The scanner was structurally
    looser, so on 2026-08-25 it alerted on TGT, ABT and TMO while the app
    rejected all three. Syncing the CONSTANTS did not help, because the LOGIC
    was duplicated.

    Now there is one implementation and this function only translates its
    output into the shape send_alert() expects. Returns None when no tradeable
    signal fires, matching the previous contract.

    `tally`, when given, has its "signals" count incremented once for every
    bar on which THE RULES PRODUCED A SIGNAL — taken or skipped — so run() can
    write that count on the bar's `scan` row. The return value cannot carry
    it: a skipped signal returns None, exactly like a blocked bar.
    """
    if len(df) < MIN_BARS_AFTER_WARMUP:
        logger.info("%s — only %d usable bars after warm-up; skipping.",
                    ticker, len(df))
        return None

    r = sc.evaluate(
        df, ticker, PARAMS,
        weekly_trend=get_weekly_trend(ticker) if PARAMS.weekly_confirm else None,
        earnings=check_earnings_blackout(ticker),
        spy_regime=spy_regime,
    )
    if r["blocked"]:
        logger.debug("%s — no signal (%s)", ticker, r.get("block_reason"))
        return None

    if tally is not None:
        tally["signals"] = tally.get("signals", 0) + 1

    # FROM HERE A SIGNAL EXISTS. Every one is recorded, whatever happens to it
    # next — a log of only the alerts that went out describes what the filters
    # let through, not what the rules produced, and the gap between those two
    # is the thing worth measuring. Blocked bars are NOT logged: that is most
    # bars of most tickers and would bury the signals in noise.
    _setup = {k: r.get(k) for k in
              ("price", "entry", "stop", "target", "rr", "rsi", "adx", "atr",
               "strength", "high_quality", "filters_pass", "filters_total")}
    _cfg = {"adx_min": PARAMS.adx_min, "min_rr": PARAMS.min_rr,
            "atr_stop_mult": PARAMS.atr_stop_mult,
            "atr_tgt_mult": PARAMS.atr_tgt_mult,
            "weekly_confirm": PARAMS.weekly_confirm,
            "longs_only": risk_params.LONGS_ONLY}

    def _log(decision: str, reason: str) -> None:
        # "taken" MEANS "THE RULES PRODUCED A TRADEABLE SIGNAL", not "a Telegram
        # message went out". This runs before run()'s alert cooldown and before
        # send_alert(), so a signal suppressed as a 4-hour duplicate, or lost to
        # a Telegram outage, is still recorded as taken.
        #
        # That is the right semantic for a record of what the RULES produced --
        # which is what this log is for -- but it is not what the word implies,
        # so it is written down here rather than left to be rediscovered from a
        # rate that does not match the alerts received.
        #
        # Never let a logging failure lose an alert. The record matters; it
        # does not matter more than the trade.
        try:
            # THE SIGNAL BAR'S DATE, not the time of the scan. This module runs
            # three times a trading day and drop_partial_bar() removes today's
            # in-progress bar, so all three runs evaluate THE SAME SETTLED BAR.
            # Without this the log recorded one row per SCAN — three per signal
            # per day — in a chain that is append-only and cannot be corrected.
            # record_signal() dedupes on it and returns None for a repeat.
            _bar = sc._bar_dates(df, None).max().date()
            if forward_log.record_signal(ticker, r["trend"], _setup, decision,
                                         reason, _cfg, bar_date=_bar) is None:
                logger.debug("%s — %s bar %s already in the forward log",
                             ticker, r["trend"], _bar)
        except Exception as exc:                       # noqa: BLE001
            logger.warning("%s — forward log write failed: %s", ticker, exc)

    # DIRECTION GATE. Shorts measured worse than random entry, so the live
    # system does not send them. See risk_params.LONGS_ONLY for the numbers.
    # Logged at INFO, not debug: a dropped alert should be visible in the
    # scanner's own output, or "the scanner went quiet" and "the scanner is
    # suppressing half its signals" look identical from outside.
    _dir = risk_params.direction_blocked(r["trend"])
    if _dir:
        logger.info("%s — %s setup suppressed: %s", ticker, r["trend"], _dir)
        _log("skipped", "direction gate: shorts are switched off")
        return None

    # Alerts fire on the high-quality tier only, exactly as app.py defines it.
    if not r["high_quality"]:
        logger.debug("%s — signal but not high-quality (rr %.2f, %s, "
                     "filters %d/%d)", ticker, r["rr"], r["strength"],
                     r["filters_pass"], r["filters_total"])
        _log("skipped", "not high-quality")
        return None

    _log("taken", "")

    return {
        "ticker": ticker, "trend": r["trend"], "strength": r["strength"],
        "price": r["price"], "entry": r["entry"], "stop": r["stop"],
        "target": r["target"], "rr": r["rr"], "rsi": r["rsi"],
        "adx": r["adx"], "atr": r["atr"],
        "filters_pass": r["filters_pass"], "filters_total": r["filters_total"],
    }

def selftest() -> int:
    """
    Run the checks with the forward log pointed at a throwaway file.

    THE BUG THIS FIXES. analyze() writes to forward_log.LOG, and the
    direction-gate check below drives analyze() directly — so every selftest
    run appended two fabricated "ZZ" rows to the REAL forward_log.jsonl. Twenty
    two of them reached a commit before anyone noticed, and CI would have kept
    adding more on every push.

    A research log that quietly accumulates test fixtures is worse than no log:
    it looks like a record. The redirect has to wrap the WHOLE function, not
    the one block that happens to mention the log, because any check that
    drives analyze() writes as a side effect.
    """
    import tempfile as _tf, pathlib as _pl
    real = forward_log.LOG
    try:
        forward_log.LOG = _pl.Path(_tf.mkdtemp()) / "selftest.jsonl"
        return _selftest_body()
    finally:
        forward_log.LOG = real


def run(args) -> int:
    # RECORD-ONLY is the post-close pass. During the session every scan
    # evaluates YESTERDAY's bar (drop_partial_bar), so a signal that forms at
    # today's close is first recorded by tomorrow's 11:07 scan — or the day
    # after, if tomorrow's dispatches miss. In the two hours after the close
    # today's bar is settled; this pass writes its signal rows and `scan`
    # heartbeat and nothing else. No trade alert (the market is shut; the
    # next session's first scan alerts, because the alert cooldown does not
    # read the log) and no chain fetch (post-close quotes are stale).
    record_only = bool(getattr(args, "record_only", False))
    if record_only:
        if not args.force and not is_post_close_window():
            logger.info("Not in the post-close window — record-only run skipped.")
            return 0
    elif not args.force and not is_market_open():
        logger.info("Market closed — skipping.")
        return 0

    state = load_state()
    _pruned = prune_state(state)
    if _pruned:
        logger.info("state: pruned %d stamp(s) older than %d days",
                    _pruned, STATE_MAX_AGE_DAYS)
    hits, skipped, failed = 0, 0, []

    # ── ATTACH OUTCOMES TO SETTLED SIGNALS, BEFORE SCANNING ──
    #
    # THE SCANNER DOES THIS, by the owner's decision (BACKLOG 17). The app
    # writes trade_journal.json through the Contents API but must never append
    # to the forward log: two writers on an append-only hash chain collide on
    # seq AND prev, and the merged chain does not verify — permanently, because
    # the hashes cover the seq so it cannot be renumbered. Reading the journal
    # here keeps exactly one writer.
    #
    # Runs first so an outcome lands even on a scan that finds no setups, and
    # before the loop that can raise per ticker.
    #
    # Non-fatal, like the signal write: the record matters, it does not matter
    # more than the trade.
    try:
        _j = Path("trade_journal.json")
        if _j.exists():
            _rep = forward_log.attach_outcomes(json.loads(_j.read_text()))
            if _rep["attached"]:
                logger.info("forward log: attached %d outcome(s)", _rep["attached"])
            # REFUSALS ARE REPORTED, NOT SWALLOWED. An ambiguous match is
            # refused rather than guessed (a wrong row here is permanent), and
            # a silent refusal would look exactly like "no trades closed".
            for _k in ("ambiguous", "no_match"):
                if _rep[_k]:
                    logger.warning("forward log: %d trade(s) %s — %s", _rep[_k],
                                   _k, "; ".join(_rep["detail"][:5]))
    except Exception as _exc:                          # noqa: BLE001
        logger.warning("forward log: attaching outcomes failed: %s", _exc)

    # Fetched ONCE for the whole scan, not per ticker.
    spy_regime = get_spy_regime() if PARAMS.spy_regime_on else None
    if PARAMS.spy_regime_on:
        logger.info("SPY regime: %s",
                    (spy_regime or {}).get("regime", "unavailable"))

    # THE BAR EACH TICKER WAS EVALUATED ON, so the run can record a `scan`
    # heartbeat per bar at the end. Normally one bar per run; tracked as a dict
    # because a frame that lags a day (a stale snapshot, a late Yahoo bar)
    # would otherwise be recorded under the wrong date.
    bars_scanned: dict = {}

    for tk in WATCHLIST:
        try:
            time.sleep(FETCH_GAP_SEC)
            df = get_data(tk)
            if df is None:
                failed.append(f"{tk} (no data)")
                continue
            # BUG FIX: the scanner used to analyse df.iloc[-1] directly, so a
            # mid-session run read TODAY'S PARTIAL BAR — a Close that is just
            # the live price and a Volume only partly accumulated. That is why
            # the 12:30 run alerted on names the app rejected an hour later.
            cdf = compute(df)
            cdf, dropped = sc.drop_partial_bar(cdf, now=datetime.now(ET))
            if dropped:
                logger.debug("%s — dropped today's partial bar", tk)
            try:
                _bar = str(sc._bar_dates(cdf, None).max().date())
                bars_scanned.setdefault(_bar, {"tickers": [], "signals": 0})
                bars_scanned[_bar]["tickers"].append(tk)
            except Exception:                              # noqa: BLE001
                _bar = None
            # The bar's tally rides along so n_signals on the scan row counts
            # what the RULES produced (taken or skipped), not what reached
            # Telegram. A None return cannot distinguish "no signal" from
            # "signal, skipped", and the first version of this counted only
            # the alert-worthy ones under a name that said otherwise.
            r = analyze(cdf, tk, spy_regime=spy_regime,
                        tally=bars_scanned.get(_bar) if _bar else None)
            if not r:
                continue
            if record_only:
                logger.info("%s %s — recorded; the next session's scan alerts.",
                            tk, r["trend"])
                continue

            if recently_alerted(state, tk, r["trend"]):
                logger.info("%s %s qualifies but alerted within %dh — suppressed.",
                            tk, r["trend"], ALERT_COOLDOWN_HRS)
                skipped += 1
                continue

            lines = [
                "🚨 TRADE ALERT",
                f"{r['ticker']} → {r['trend']} ({r['strength']})",
                f"Price: {r['price']} | RR: {r['rr']} | ADX: {r['adx']} | RSI: {r['rsi']}",
                f"Underlying: entry {r['entry']} | stop {r['stop']} | target {r['target']}",
            ]

            # Chain fetch happens here — AFTER the cooldown check — so
            # suppressed alerts cost no API calls.
            opt = suggest_option(tk, r["price"], r["trend"], r["atr"]) \
                if SUGGEST_OPTIONS else None

            if opt:
                lines += [
                    "",
                    f"📄 CONTRACT: {tk} {opt['expiry']} ${opt['strike']:g} {opt['right']}",
                    f"Mid ${opt['mid']:.2f}  (bid {opt['bid']:.2f} / ask {opt['ask']:.2f}, "
                    f"spread {opt['spread_pct']:.0f}%)",
                    f"Cost ${opt['cost']:,.0f} per contract = {opt['pct_account']:.1f}% of account",
                    f"{opt['dte']} DTE · vol {opt['volume']:,} · OI {opt['oi']:,}",
                ]
                if not opt["within_budget"]:
                    lines.append(f"⚠️ Above your ${opt['budget']:,.0f} risk budget "
                                 f"({OPTION_BUDGET_PCT:g}% of ${ACCOUNT_SIZE:,}) — on a long "
                                 f"option the premium IS the max loss.")
                if opt["spread_pct"] > OPT_MAX_SPREAD * 0.75:
                    lines.append(f"⚠️ Spread {opt['spread_pct']:.0f}% of mid — a wide "
                                 f"round trip can erase the edge on its own.")
                lines += ["", "Test rules: TP +200% · SL −50% · exit at 7 DTE · thesis OFF"]
            elif SUGGEST_OPTIONS:
                lines += ["", "📄 No liquid contract found in the 21-45 DTE window "
                              "(spread/volume/OI gates). Check the chain yourself."]

            msg = "\n".join(lines)
            logger.info("ALERT %s %s rr=%s", tk, r["trend"], r["rr"])
            if send_alert(msg, dry_run=args.dry_run):
                hits += 1
                if not args.dry_run:
                    state[f"{tk}:{r['trend']}"] = time.time()

        except Exception as e:
            # FIX 5: isolate each ticker. Previously one failure aborted the
            # entire scan and the remaining tickers were never examined.
            logger.exception("%s failed: %s", tk, e)
            failed.append(f"{tk} ({type(e).__name__})")

    # A scan where EVERY ticker failed is an outage, not a quiet market — and
    # this runs unattended on a schedule, so the only place it would otherwise
    # appear is a workflow log nobody opens. The app showed the same failure
    # as "0 setups" on 2026-09-02; the scanner's version of that mistake is
    # worse, because silence is its normal output.
    #
    # Cooldown-keyed like any other alert so a day-long Yahoo outage sends one
    # message, not one per scheduled run.
    if is_total_outage(failed, WATCHLIST):
        if not recently_alerted(state, "SCANNER", "OUTAGE"):
            send_alert(
                "⚠️ SCANNER OUTAGE\n"
                f"All {len(WATCHLIST)} watchlist tickers failed to return "
                f"usable data, so nothing was scanned this run. This is NOT "
                f"'no setups' — it means the scan did not happen.\n\n"
                f"{', '.join(failed)}",
                dry_run=args.dry_run)
            if not args.dry_run:
                state["SCANNER:OUTAGE"] = time.time()
        else:
            logger.warning("Total scan outage, but an outage alert was sent "
                           "within %dh — suppressed.", ALERT_COOLDOWN_HRS)

    # ── RECORD THAT THIS SCAN HAPPENED ──
    #
    # One `scan` row per bar evaluated. Without it a bar with no signal row
    # meant EITHER "quiet market" OR "no scan ran" — and for three weeks the
    # second was true on 1 in 4 trading days while every workflow run reported
    # success. This is the heartbeat coverage_check() reads.
    #
    # Written on dry runs too: a dry run still evaluated the bar. Skipped only
    # when nothing was scanned at all — "scanned zero tickers" is an outage
    # (handled above), not a heartbeat. n_signals is the count of bars on
    # which the rules produced a signal, taken OR skipped — the same rows
    # record_signal() wrote (or deduped) for this bar.
    for _bar, _info in sorted(bars_scanned.items()):
        try:
            forward_log.record_scan(_bar, _info["tickers"], _info["signals"],
                                    {"adx_min": PARAMS.adx_min,
                                     "min_rr": PARAMS.min_rr,
                                     "atr_stop_mult": PARAMS.atr_stop_mult,
                                     "atr_tgt_mult": PARAMS.atr_tgt_mult,
                                     "weekly_confirm": PARAMS.weekly_confirm,
                                     "longs_only": risk_params.LONGS_ONLY})
        except Exception as _exc:                          # noqa: BLE001
            logger.warning("forward log: scan row for %s failed: %s", _bar, _exc)

    if not args.dry_run:
        save_state(state)

    logger.info("Done. %d alert(s) sent, %d suppressed by cooldown, "
                "%d ticker(s) failed.", hits, skipped, len(failed))
    if failed:
        logger.warning("Failed tickers: %s", ", ".join(failed))
    return 0


# ══════════════════════════════════════════════════════════════════
# COVERAGE — the alarm for silence
# ══════════════════════════════════════════════════════════════════
# is_total_outage() fires when every ticker fails INSIDE a run. It cannot see a
# run that never scans — and from 09-16 to 10-07 GitHub's scheduler delivered
# the scanner in session on 11 of 48 scheduled slots and the exit monitor on 16
# of 112, each late run skipping at the market-hours guard and reporting
# success. Three straight trading days passed with no scan and nothing said so.
# This runs on EVERY workflow run, including the ones that cannot scan, and
# reads the committed state the last real run left behind.

NO_SCAN_ALERT_SESSIONS = 2     # alert once this many trading days pass unscanned
STALE_POSITION_SESSIONS = 1    # an open position unchecked for this many sessions
COVERAGE_COOLDOWN_HRS = 20     # one nag a day, not one per late run


def trading_days_between(start: date, end: date) -> int:
    """Completed trading sessions after `start` up to and including `end`."""
    if end <= start:
        return 0
    n, cur = 0, start
    while cur < end:
        cur += timedelta(days=1)
        if cur.weekday() >= 5 or cur.strftime("%Y-%m-%d") in MARKET_HOLIDAYS:
            continue
        n += 1
    return n


def coverage_report(now: datetime | None = None,
                    positions_path: Path | None = None) -> dict:
    """
    What the committed state says about whether the unattended system has been
    running. Pure read; the alerting decision is coverage_check()'s.

      last_scan_bar       bar date of the last `scan` row, or None
      sessions_unscanned  trading days since that bar whose bar no scan has
                          evaluated (the scanner reads the last SETTLED bar, so
                          on a run day the newest possible bar is yesterday's)
      stale_positions     OPEN / EXIT_SIGNALLED positions whose last_check_epoch
                          is STALE_POSITION_SESSIONS or more sessions old
    """
    now = now or datetime.now(ET)
    today = now.date()
    last = forward_log.last_scan()
    if last and last.get("bar_date"):
        last_bar = date.fromisoformat(last["bar_date"])
        unscanned = max(0, trading_days_between(last_bar, today) - 1)
    else:
        last_bar, unscanned = None, None

    stale = []
    p = positions_path or Path("open_positions.json")
    try:
        for pos in (json.loads(p.read_text()) if p.exists() else []):
            if pos.get("status") not in ("OPEN", "EXIT_SIGNALLED"):
                continue
            ep = pos.get("last_check_epoch")
            if not ep:
                stale.append((pos.get("ticker"), None))
                continue
            checked = datetime.fromtimestamp(float(ep), ET).date()
            gap = trading_days_between(checked, today)
            if gap >= STALE_POSITION_SESSIONS:
                stale.append((pos.get("ticker"), gap))
    except Exception as e:                                 # noqa: BLE001
        logger.warning("coverage: could not read positions: %s", e)

    return {"last_scan_bar": str(last_bar) if last_bar else None,
            "sessions_unscanned": unscanned, "stale_positions": stale,
            "today": str(today)}


def coverage_check(state: dict, now: datetime | None = None,
                   dry_run: bool = False) -> bool:
    """Alert if the system has gone silent. Returns True if an alert was sent."""
    rep = coverage_report(now)
    lines = []
    if rep["sessions_unscanned"] is None:
        lines.append("The forward log has NO scan row at all — the scanner "
                     "has never recorded a completed scan.")
    elif rep["sessions_unscanned"] >= NO_SCAN_ALERT_SESSIONS:
        lines.append(f"No scan has run for {rep['sessions_unscanned']} trading "
                     f"day(s) — last bar evaluated was {rep['last_scan_bar']}.")
    if rep["stale_positions"]:
        bits = ", ".join(f"{t} ({'never' if g is None else f'{g} session(s)'})"
                         for t, g in rep["stale_positions"])
        lines.append(f"Open position(s) not checked by the exit monitor: {bits}.")
    if not lines:
        logger.info("coverage: OK — last scan bar %s, %s session(s) unscanned, "
                    "no stale positions", rep["last_scan_bar"],
                    rep["sessions_unscanned"])
        return False
    last_alert = float(state.get("SCANNER:COVERAGE", 0) or 0)
    if (time.time() - last_alert) < COVERAGE_COOLDOWN_HRS * 3600:
        logger.warning("coverage: PROBLEM, alert suppressed by cooldown — %s",
                       " ".join(lines))
        return False
    msg = ("⚠️ SCANNER COVERAGE\n" + "\n".join(lines) +
           "\n\nA workflow run can report success while skipping the scan — a "
           "late run hits the market-hours guard. This is NOT 'no setups'; it "
           "means the system is not looking. See BACKLOG 22.")
    logger.warning("coverage: %s", " ".join(lines))
    if send_alert(msg, dry_run=dry_run):
        if not dry_run:
            state["SCANNER:COVERAGE"] = time.time()
        return True
    return False


def _selftest_body() -> int:
    """
    Offline checks for the unattended paths. This module sends real alerts on
    a schedule and had NO test coverage at all, which is how it could have
    fetched nothing for days while looking exactly like a quiet market.
    """
    # ── THE DIRECTION GATE MUST BIND IN analyze(), not just in risk_params ──
    # (forward_log.LOG is redirected for the whole of this function by
    # selftest() below — see the note there.)
    # Asserting that direction_blocked() returns a string proves the helper
    # works, not that the scanner calls it. This project has shipped that
    # mistake repeatedly — a guard aimed at the producer while the break sat in
    # the consumer. Drive the real function and read what comes back.
    import pandas as _pd
    # A REAL DATE INDEX. This was a bare RangeIndex, so _bar_dates() read the
    # row numbers as epoch nanoseconds and every bar came back 1970-01-01 — fine
    # while nothing looked at the date, useless the moment the forward log
    # started keying on it. Production frames always carry a DatetimeIndex
    # (compute() runs on yfinance bars); the fixture now does too.
    _frame = _pd.DataFrame(
        {"Close": [100.0] * (MIN_BARS_AFTER_WARMUP + 5)},
        index=_pd.bdate_range("2026-01-05", periods=MIN_BARS_AFTER_WARMUP + 5))

    def _canned(trend):
        return {"blocked": False, "ticker": "ZZ", "trend": trend,
                "strength": "Strong", "price": 100.0, "entry": 100.0,
                "stop": 98.0, "target": 106.0, "rr": 3.0, "rsi": 65.0,
                "adx": 30.0, "atr": 2.0, "high_quality": True,
                "filters_pass": 4, "filters_total": 4}

    _real_eval = sc.evaluate
    _real_wk, _real_earn = get_weekly_trend, check_earnings_blackout
    try:
        globals()["get_weekly_trend"] = lambda t: "Bullish"
        globals()["check_earnings_blackout"] = lambda t: (True, "n/a")
        sc.evaluate = lambda *a, **k: _canned("Bearish")
        assert analyze(_frame, "ZZ") is None, (
            "a high-quality BEARISH signal reached the alert path. Shorts "
            "measured worse than random entry (drift_null: all 200 random "
            "draws beat the real signal, -0.131 R per trade) and the scanner "
            "must not send them")
        sc.evaluate = lambda *a, **k: _canned("Bullish")
        got = analyze(_frame, "ZZ")
        assert got is not None and got["trend"] == "Bullish", (
            f"the gate swallowed a LONG. It must block one direction, not "
            f"stop the scanner: {got}")
        # THE TALLY counts what the rules PRODUCED: the skipped bearish signal
        # above counts, the taken bullish one counts, a blocked bar does not.
        _tally: dict = {}
        sc.evaluate = lambda *a, **k: _canned("Bearish")
        assert analyze(_frame, "ZT", tally=_tally) is None
        sc.evaluate = lambda *a, **k: _canned("Bullish")
        analyze(_frame, "ZU", tally=_tally)
        sc.evaluate = lambda *a, **k: dict(_canned("Bullish"), blocked=True,
                                           block_reason="fixture")
        assert analyze(_frame, "ZV", tally=_tally) is None
        assert _tally.get("signals") == 2, (
            f"tally {_tally}: analyze() must count a skipped signal AND a "
            f"taken one, and not a blocked bar — n_signals on the scan row "
            f"is 'what the rules produced', not 'what was alert-worthy'")
    finally:
        sc.evaluate = _real_eval
        globals()["get_weekly_trend"] = _real_wk
        globals()["check_earnings_blackout"] = _real_earn
    print("direction gate          : bearish dropped, bullish still alerts")

    # ── EVERY SIGNAL MUST REACH THE FORWARD LOG, INCLUDING THE SKIPS ──
    # A log of only the alerts that went out describes what the filters let
    # through, not what the rules produced. Checking that analyze() MENTIONS
    # forward_log would test a string; drive all three paths and read the file.
    import tempfile as _tf, pathlib as _pl
    _tmp = _pl.Path(_tf.mkdtemp()) / "fl.jsonl"
    _real_log = forward_log.LOG
    _real_eval = sc.evaluate
    _real_wk, _real_earn = get_weekly_trend, check_earnings_blackout
    try:
        forward_log.LOG = _tmp
        globals()["get_weekly_trend"] = lambda t: "Bullish"
        globals()["check_earnings_blackout"] = lambda t: (True, "n/a")
        # DISTINCT TICKERS, because a signal's identity is
        # (ticker, trend, bar date) and these are three different signals.
        # They used to share "ZZ" and one frame, which the dedupe now — quite
        # correctly — collapses into one row.
        for _tk, _trend, _hq in (("ZA", "Bullish", True),
                                 ("ZB", "Bearish", True),
                                 ("ZC", "Bullish", False)):
            _c = dict(_canned(_trend), high_quality=_hq)
            sc.evaluate = lambda *a, **k: _c
            analyze(_frame, _tk)
        _rows = forward_log.read_all(_tmp)
        assert len(_rows) == 3, (
            f"three signals fired and {len(_rows)} were logged. The ones that "
            f"never became alerts are exactly the ones that make the record "
            f"worth having")

        # ── ONE ROW PER SIGNAL BAR, NOT ONE PER SCAN ──
        #
        # THE BUG THIS PINS. This module runs three times a trading day and
        # drop_partial_bar() removes today's in-progress bar, so all three runs
        # evaluate THE SAME SETTLED BAR. run()'s 4-hour cooldown does not help:
        # it is checked AFTER analyze() has already written. So one signal
        # produced three identical rows a day, in a chain that is append-only
        # and cannot be corrected afterwards.
        _c = dict(_canned("Bullish"), high_quality=True)
        sc.evaluate = lambda *a, **k: _c
        for _ in range(3):                      # three scans, one bar
            analyze(_frame, "ZD")
        _zd = [x for x in forward_log.read_all(_tmp) if x["ticker"] == "ZD"]
        assert len(_zd) == 1, (
            f"three scans of one settled bar wrote {len(_zd)} rows. The log "
            f"would count scans, not signals, and inflate any rate taken from "
            f"it roughly threefold")
        assert _zd[0]["bar_date"] == str(sc._bar_dates(_frame, None).max().date()), (
            f"the row must carry the SIGNAL BAR's date, not the scan time: "
            f"{_zd[0].get('bar_date')}")
        # A genuinely NEW bar is a new signal and must still be recorded.
        _next = _frame.copy()
        _next.index = _next.index + _pd.Timedelta(days=1)
        analyze(_next, "ZD")
        assert len([x for x in forward_log.read_all(_tmp)
                    if x["ticker"] == "ZD"]) == 2, (
            "the next bar is a new signal; deduping it away would make the log "
            "record one row per setup forever")
        print("forward log, dedupe     : 3 scans of one bar -> 1 row; "
              "the next bar -> a new row")
        _by = {(x["trend"], x["decision"]) for x in _rows}
        assert ("Bullish", "taken") in _by, _by
        assert ("Bearish", "skipped") in _by, "the direction skip was not logged"
        assert sum(1 for x in _rows if x["decision"] == "skipped") == 2, _by
        assert all(x["reason"] for x in _rows if x["decision"] == "skipped"), (
            "a skip was logged with no reason")
        assert not forward_log.verify(_tmp), forward_log.verify(_tmp)
        assert len({x["config"] for x in _rows}) == 1, (
            "one scan produced two config fingerprints")
    finally:
        forward_log.LOG = _real_log
        sc.evaluate = _real_eval
        globals()["get_weekly_trend"] = _real_wk
        globals()["check_earnings_blackout"] = _real_earn
    print("forward log             : 3 signals, 1 taken 2 skipped with "
          "reasons, chain intact")

    # ── A BROKEN LOG MUST NOT COST AN ALERT ──
    # The record matters; it does not matter more than the trade. Nothing
    # exercised the failure path, so raising instead of warning left the suite
    # green — and a full disk would then have silenced the scanner.
    _real_rec = forward_log.record_signal
    _real_eval = sc.evaluate
    _real_wk, _real_earn = get_weekly_trend, check_earnings_blackout
    try:
        def _boom(*a, **k):
            raise OSError("disk full")
        forward_log.record_signal = _boom
        globals()["get_weekly_trend"] = lambda t: "Bullish"
        globals()["check_earnings_blackout"] = lambda t: (True, "n/a")
        sc.evaluate = lambda *a, **k: _canned("Bullish")
        _got = analyze(_frame, "ZZ")
        assert _got is not None and _got["trend"] == "Bullish", (
            "a failed log write killed the alert. The record matters; it does "
            "not matter more than the trade, and a full disk must not silence "
            "the scanner")
    finally:
        forward_log.record_signal = _real_rec
        sc.evaluate = _real_eval
        globals()["get_weekly_trend"] = _real_wk
        globals()["check_earnings_blackout"] = _real_earn
    print("log failure             : alert still goes out, warning logged")

    wl = ["AAA", "BBB", "CCC"]

    assert is_total_outage(["AAA (no data)", "BBB (x)", "CCC (y)"], wl) is True
    print("total outage            : all tickers failed -> True")

    assert is_total_outage(["AAA (no data)"], wl) is False, \
        "a partial failure is not an outage — the scan still looked at the rest"
    assert is_total_outage([], wl) is False
    print("partial / no failure    : not an outage")

    # An empty watchlist must not page you forever. len([]) == len([]) is the
    # trap: a misconfigured universe would alert on every scheduled run.
    assert is_total_outage([], []) is False, \
        "an empty watchlist is a config problem, not a data outage — and " \
        "alerting on it every run would train you to ignore the alert"
    print("empty watchlist         : not an outage (no false page)")

    # The cooldown that stops a day-long outage becoming one alert per run.
    st_ = {}
    assert recently_alerted(st_, "SCANNER", "OUTAGE") is False
    st_["SCANNER:OUTAGE"] = time.time()
    assert recently_alerted(st_, "SCANNER", "OUTAGE") is True
    st_["SCANNER:OUTAGE"] = time.time() - (ALERT_COOLDOWN_HRS * 3600) - 1
    assert recently_alerted(st_, "SCANNER", "OUTAGE") is False, \
        "the outage alert must fire again once the cooldown has elapsed"
    print(f"outage alert cooldown   : one per {ALERT_COOLDOWN_HRS}h, then re-arms")

    # ══════════════════════════════════════════════════════════════
    # COVERAGE — the alarm for silence (BACKLOG 22 / 26)
    # ══════════════════════════════════════════════════════════════
    import tempfile as _tf2, pathlib as _pl2, json as _json2
    from datetime import datetime as _dt2, date as _d2

    # trading_days_between: weekends and holidays are not sessions
    assert trading_days_between(_d2(2026, 10, 2), _d2(2026, 10, 5)) == 1, \
        "Fri -> Mon is ONE session, not three days"
    assert trading_days_between(_d2(2026, 9, 4), _d2(2026, 9, 8)) == 1, \
        "Fri -> Tue across Labor Day (09-07) is ONE session"
    assert trading_days_between(_d2(2026, 10, 7), _d2(2026, 10, 7)) == 0
    print("sessions between        : weekends and holidays excluded")

    _tmpl = _pl2.Path(_tf2.mkdtemp()) / "cov.jsonl"
    _real_log2 = forward_log.LOG
    _real_send2 = globals()["send_alert"]
    _sent: list = []
    try:
        forward_log.LOG = _tmpl
        globals()["send_alert"] = lambda m, dry_run=False: (_sent.append(m) or True)
        _now = _dt2(2026, 10, 7, 12, 0, tzinfo=ET)
        _pos = _pl2.Path(_tf2.mkdtemp()) / "pos.json"

        # No scan row at all -> the report says so, the check alerts.
        _pos.write_text("[]")
        _rep = coverage_report(now=_now, positions_path=_pos)
        assert _rep["sessions_unscanned"] is None and _rep["last_scan_bar"] is None
        # A run that scanned bar 10-06 on 10-07: 0 unscanned (yesterday is the
        # newest bar a scan can evaluate).
        forward_log.record_scan("2026-10-06", ["TMO"], 0, {"k": 1})
        _rep = coverage_report(now=_now, positions_path=_pos)
        assert _rep["sessions_unscanned"] == 0, _rep
        # THE THREE-WEEK FAILURE, reproduced: last scan evaluated 10-01, today is
        # 10-07 -> bars 10-02, 10-05, 10-06 unscanned = 3 sessions.
        _tmpl.write_text("")
        forward_log.record_scan("2026-10-01", ["TMO"], 2, {"k": 1})
        _rep = coverage_report(now=_now, positions_path=_pos)
        assert _rep["sessions_unscanned"] == 3, _rep
        print("coverage report         : none / current / 3 sessions silent")

        # Stale position: checked 10-02, today 10-07 -> 3 sessions.
        _ep = _dt2(2026, 10, 2, 14, 0, tzinfo=ET).timestamp()
        _pos.write_text(_json2.dumps([
            {"ticker": "PFE", "status": "EXIT_SIGNALLED", "last_check_epoch": _ep},
            {"ticker": "NVDA", "status": "OPEN", "last_check_epoch": _now.timestamp()},
            {"ticker": "OLD", "status": "CLOSED", "last_check_epoch": _ep},
            {"ticker": "NEW", "status": "OPEN"}]))
        _rep = coverage_report(now=_now, positions_path=_pos)
        assert ("PFE", 3) in _rep["stale_positions"], _rep
        assert ("NEW", None) in _rep["stale_positions"], "never-checked is stale"
        assert all(t != "NVDA" for t, _ in _rep["stale_positions"]), \
            "a position checked today is not stale"
        assert all(t != "OLD" for t, _ in _rep["stale_positions"]), \
            "a CLOSED position is nobody's to check"
        print("stale positions         : 3 sessions and never-checked flagged; "
              "today and CLOSED not")

        # coverage_check: alerts, then cooldown, then dry-run leaves no state.
        # Point it at the temp positions file by monkeypatching the default.
        import scanner as _self
        _orig_cr = _self.coverage_report
        _self.coverage_report = lambda now=None: _orig_cr(now=now, positions_path=_pos)
        globals()["coverage_report"] = _self.coverage_report
        try:
            _st: dict = {}
            assert coverage_check(_st, now=_now) is True and len(_sent) == 1
            assert "No scan has run for 3 trading day" in _sent[0], _sent[0]
            assert "PFE (3 session" in _sent[0], _sent[0]
            assert "SCANNER:COVERAGE" in _st, "the alert must be recorded for cooldown"
            assert coverage_check(_st, now=_now) is False and len(_sent) == 1, \
                "a second check inside the cooldown must not re-page"
            _st2: dict = {}
            assert coverage_check(_st2, now=_now, dry_run=True) is True
            assert "SCANNER:COVERAGE" not in _st2, "dry run must not touch state"
            # And a healthy state is silent.
            _tmpl.write_text("")
            forward_log.record_scan("2026-10-06", ["TMO"], 0, {"k": 1})
            _pos.write_text("[]")
            _n = len(_sent)
            assert coverage_check({}, now=_now) is False and len(_sent) == _n
            print("coverage check          : pages once, cools down 20h, dry-run "
                  "stateless, silent when healthy")
        finally:
            _self.coverage_report = _orig_cr
            globals()["coverage_report"] = _orig_cr

        # ── run() WRITES A SCAN ROW — the heartbeat is produced, not just read ──
        _tmpl.write_text("")
        import pandas as _pd2
        # ENDS IN THE PAST, so drop_partial_bar() never trims it. A range that
        # ran past today lost its last bars during the session and kept them
        # after the close, and this test passed or failed on the clock.
        _bars = _pd2.bdate_range(end="2026-06-30", periods=MIN_BARS_AFTER_WARMUP + 20)
        _fr = _pd2.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0,
                              "Close": 100.0, "Volume": 1e6}, index=_bars)
        _g = globals()
        _saved = {k: _g[k] for k in ("get_data", "compute", "analyze",
                                     "get_spy_regime", "save_state", "load_state",
                                     "WATCHLIST", "FETCH_GAP_SEC")}
        try:
            _g["get_data"] = lambda tk: _fr
            _g["compute"] = lambda df: df
            _g["analyze"] = lambda df, tk, spy_regime=None, tally=None: None
            _g["get_spy_regime"] = lambda: None
            _g["save_state"] = lambda st: None
            _g["load_state"] = lambda: {}
            _g["WATCHLIST"] = ["AAA", "BBB"]
            _g["FETCH_GAP_SEC"] = 0
            import argparse as _ap2
            run(_ap2.Namespace(force=True, dry_run=True))
        finally:
            _g.update(_saved)
        _rows = forward_log.read_all(_tmpl)
        _scans = [r for r in _rows if r["kind"] == "scan"]
        assert len(_scans) == 1, f"one bar scanned -> one scan row, got {len(_scans)}"
        assert _scans[0]["tickers"] == ["AAA", "BBB"] and _scans[0]["n_signals"] == 0
        assert _scans[0]["bar_date"] == str(_bars[-1].date()), _scans[0]
        print(f"run() heartbeat         : one scan row, bar {_scans[0]['bar_date']}, "
              f"2 tickers, 0 signals (written on a dry run too)")
    finally:
        forward_log.LOG = _real_log2
        globals()["send_alert"] = _real_send2

    # ── THE POST-CLOSE WINDOW ──
    _wed = lambda h, m: _dt2(2026, 10, 7, h, m, tzinfo=ET)       # a Wednesday
    assert is_post_close_window(_wed(16, 20)) is True
    assert is_post_close_window(_wed(18, 0)) is True, "inclusive at the end"
    assert is_post_close_window(_wed(16, 0)) is False, \
        "16:00:00 is still the session to is_market_open(); never both"
    assert is_post_close_window(_wed(15, 59)) is False
    assert is_post_close_window(_wed(18, 1)) is False
    assert is_post_close_window(_dt2(2026, 10, 10, 16, 20, tzinfo=ET)) is False, "Saturday"
    assert is_post_close_window(_dt2(2026, 11, 26, 16, 20, tzinfo=ET)) is False, "Thanksgiving"
    assert is_post_close_window(_dt2(2026, 11, 27, 13, 30, tzinfo=ET)) is True, \
        "half day closes at 13:00"
    assert is_post_close_window(_dt2(2026, 11, 27, 16, 20, tzinfo=ET)) is False, \
        "half day: 16:20 is three hours after the close"
    for _t in (_wed(16, 20), _wed(18, 0), _dt2(2026, 11, 27, 13, 30, tzinfo=ET)):
        assert not is_market_open(_t), "the two windows must never overlap"
    print("post-close window       : 16:00 < t <= 18:00 ET on trading days, "
          "13:00-15:00 on a half day, disjoint from the session")

    # ── STATE PRUNING ──
    _now_ep = 1_800_000_000.0
    _st3 = {"AAPL:Bullish": _now_ep - 31 * 86400,
            "NVDA:Bullish": _now_ep - 3600,
            "SCANNER:COVERAGE": _now_ep - 30 * 86400 - 1,
            "SCANNER:OUTAGE": _now_ep - 29 * 86400,
            "odd": "not a number", "flag": True}
    assert prune_state(_st3, now=_now_ep) == 2, _st3
    assert set(_st3) == {"NVDA:Bullish", "SCANNER:OUTAGE", "odd", "flag"}, _st3
    assert prune_state(_st3, now=_now_ep) == 0, "idempotent"
    print("state pruning           : stamps older than 30 days dropped, "
          "fresh / non-numeric kept")

    # ── RECORD-ONLY: rows and heartbeat, no alert; n_signals counts skipped ──
    _tmpl3 = _pl2.Path(_tf2.mkdtemp()) / "ro.jsonl"
    _real_log3 = forward_log.LOG
    _real_send3 = globals()["send_alert"]
    _sent3: list = []
    try:
        forward_log.LOG = _tmpl3
        globals()["send_alert"] = lambda m, dry_run=False: (_sent3.append(m) or True)
        import pandas as _pd3
        _bars3 = _pd3.bdate_range(end="2026-06-30", periods=MIN_BARS_AFTER_WARMUP + 20)
        _fr3 = _pd3.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0,
                               "Close": 100.0, "Volume": 1e6}, index=_bars3)

        def _fake_analyze(df, tk, spy_regime=None, tally=None):
            # AAA: a tradeable signal. BBB: a signal the rules produced but
            # skipped (returns None, like the direction gate). CCC: blocked.
            if tk in ("AAA", "BBB") and tally is not None:
                tally["signals"] = tally.get("signals", 0) + 1
            if tk == "AAA":
                return {"ticker": tk, "trend": "Bullish", "strength": "Strong",
                        "price": 100.0, "entry": 100.0, "stop": 98.0,
                        "target": 106.0, "rr": 3.0, "rsi": 55.0, "adx": 40.0,
                        "atr": 1.6, "filters_pass": 4, "filters_total": 4}
            return None

        _g = globals()
        _saved = {k: _g[k] for k in ("get_data", "compute", "analyze",
                                     "get_spy_regime", "save_state", "load_state",
                                     "WATCHLIST", "FETCH_GAP_SEC", "suggest_option",
                                     "is_post_close_window")}
        _saved_state: dict = {}
        try:
            _g["get_data"] = lambda tk: _fr3
            _g["compute"] = lambda df: df
            _g["analyze"] = _fake_analyze
            _g["get_spy_regime"] = lambda: None
            _g["save_state"] = lambda st: _saved_state.update(st)
            _g["load_state"] = lambda: {}
            _g["WATCHLIST"] = ["AAA", "BBB", "CCC"]
            _g["FETCH_GAP_SEC"] = 0
            # RECORDS the call rather than raising: run() isolates each
            # ticker in try/except, so a stub that raised was swallowed
            # there and the alert path it was meant to expose never ran.
            _chain_calls: list = []
            _g["suggest_option"] = lambda *a, **k: (_chain_calls.append(a) or None)
            import argparse as _ap3
            # Outside the window, not forced: nothing happens at all.
            _g["is_post_close_window"] = lambda now=None: False
            run(_ap3.Namespace(force=False, dry_run=False, record_only=True))
            assert not forward_log.read_all(_tmpl3) and not _sent3, \
                "a record-only run outside its window must not scan"
            # Inside the window: rows + heartbeat, no alert, no state.
            _g["is_post_close_window"] = lambda now=None: True
            run(_ap3.Namespace(force=False, dry_run=False, record_only=True))
        finally:
            _g.update(_saved)
        assert not _sent3, f"record-only sent an alert: {_sent3}"
        assert not _chain_calls, "record-only fetched an option chain"
        _rows3 = forward_log.read_all(_tmpl3)
        _scans3 = [r for r in _rows3 if r["kind"] == "scan"]
        assert len(_scans3) == 1 and _scans3[0]["tickers"] == ["AAA", "BBB", "CCC"], _scans3
        assert _scans3[0]["n_signals"] == 2, (
            _scans3[0], "n_signals must count what the rules PRODUCED (AAA "
            "taken + BBB skipped), not only the alert-worthy one")
        assert "AAA:Bullish" not in _saved_state, \
            "record-only must not stamp the alert cooldown — the next " \
            "session's scan has to be free to alert"
        print("record-only run         : heartbeat n_signals=2 (1 taken + 1 "
              "skipped), no alert, no chain, no cooldown stamp; skipped "
              "outside its window")
    finally:
        forward_log.LOG = _real_log3
        globals()["send_alert"] = _real_send3

    print("\nAll self-tests passed.")
    return 0


def parse_args():
    p = argparse.ArgumentParser(description="Watchlist scanner")
    p.add_argument("--dry-run", action="store_true",
                   help="Print alerts instead of sending; don't touch state")
    p.add_argument("--force", action="store_true",
                   help="Run even when the market is closed")
    p.add_argument("--selftest", action="store_true",
                   help="Run offline checks and exit (no network, no alerts)")
    p.add_argument("--record-only", action="store_true",
                   help="Post-close pass: evaluate today's settled bar and "
                        "write its forward-log rows and scan heartbeat. Sends "
                        "no trade alert and fetches no chain. Runs only inside "
                        "the 2h after the close unless --force.")
    p.add_argument("--coverage", action="store_true",
                   help="Check whether the unattended system has been running "
                        "(last scan row, unchecked positions) and alert if not. "
                        "No market data; runs on every workflow run, including "
                        "the ones that cannot scan.")
    return p.parse_args()


if __name__ == "__main__":
    _args = parse_args()
    if _args.selftest:
        raise SystemExit(selftest())
    if _args.coverage:
        _st = load_state()
        prune_state(_st)
        coverage_check(_st, dry_run=_args.dry_run)
        if not _args.dry_run:
            save_state(_st)
        raise SystemExit(0)
    raise SystemExit(run(_args))
