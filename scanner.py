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
"""
from __future__ import annotations

import argparse
import json
import os
import time
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytz
import requests
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

# ── Option suggestion ──
# DTE window is centred on 30 deliberately: option_backtest.py measured this
# strategy with a 30-DTE entry, so suggesting 7-DTE contracts would be
# recommending something never tested. 21-45 keeps live trades comparable to
# the backtest they are supposed to be validating.
SUGGEST_OPTIONS  = True
OPT_MIN_DTE      = 21
OPT_MAX_DTE      = 45
OPT_MAX_EXPIRIES = 3        # each expiry is one chain fetch — keep it lean
OPT_MAX_SPREAD   = 15.0     # % of mid; above this the round trip eats the edge

# Shown in the alert for context. NOT used to filter during the test phase —
# you asked to see every suggestion and judge affordability yourself.
ACCOUNT_SIZE = 1500
RISK_PCT     = 5.0

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
}
MARKET_HALF_DAYS = {"2025-07-03", "2025-11-28", "2025-12-24",
                    "2026-11-27", "2026-12-24"}


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
    if dry_run:
        print("\n--- TELEGRAM (dry-run) ---\n" + message + "\n--- end ---\n")
        return True
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat  = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — no alert sent.")
        return False
    try:
        # The old version had no timeout (could hang the job) and swallowed
        # every error with a bare `except: pass`, so failures were invisible.
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": message}, timeout=10)
        if r.status_code != 200:
            logger.error("Telegram API %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        logger.error("Telegram send failed: %s", e)
        return False


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
        budget = ACCOUNT_SIZE * RISK_PCT / 100
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


def check_earnings_blackout(ticker: str) -> tuple[bool, str]:
    """
    (ok, detail). False when earnings fall inside the blackout window either
    side of today. Fails OPEN — an unavailable calendar must not silence every
    signal, so an error returns ok=True with the reason stated.
    """
    try:
        time.sleep(FETCH_GAP_SEC)
        cal = yf.Ticker(ticker).calendar
        dates = None
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date")
        elif cal is not None and hasattr(cal, "empty") and not cal.empty:
            if "Earnings Date" in cal.index:
                dates = cal.loc["Earnings Date"].tolist()
        if not dates:
            return True, "No earnings date available"
        if not isinstance(dates, (list, tuple)):
            dates = [dates]
        today = datetime.now().date()
        for d in dates:
            try:
                ed = pd.Timestamp(d).date()
            except Exception:
                continue
            delta = (ed - today).days
            if -POST_EARNINGS_DAYS <= delta <= EARNINGS_BLACKOUT_DAYS:
                return False, f"Earnings {ed} ({delta:+d}d) inside blackout"
        return True, "Outside earnings blackout"
    except Exception as e:
        logger.debug("Earnings check failed for %s (%s)", ticker, e)
        return True, f"Earnings check unavailable ({e})"


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
            spy_regime: dict | None = None) -> dict | None:
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

    # Alerts fire on the high-quality tier only, exactly as app.py defines it.
    if not r["high_quality"]:
        logger.debug("%s — signal but not high-quality (rr %.2f, %s, "
                     "filters %d/%d)", ticker, r["rr"], r["strength"],
                     r["filters_pass"], r["filters_total"])
        return None

    return {
        "ticker": ticker, "trend": r["trend"], "strength": r["strength"],
        "price": r["price"], "entry": r["entry"], "stop": r["stop"],
        "target": r["target"], "rr": r["rr"], "rsi": r["rsi"],
        "adx": r["adx"], "atr": r["atr"],
        "filters_pass": r["filters_pass"], "filters_total": r["filters_total"],
    }

def run(args) -> int:
    if not args.force and not is_market_open():
        logger.info("Market closed — skipping.")
        return 0

    state = load_state()
    hits, skipped, failed = 0, 0, []

    # Fetched ONCE for the whole scan, not per ticker.
    spy_regime = get_spy_regime() if PARAMS.spy_regime_on else None
    if PARAMS.spy_regime_on:
        logger.info("SPY regime: %s",
                    (spy_regime or {}).get("regime", "unavailable"))

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
            r = analyze(cdf, tk, spy_regime=spy_regime)
            if not r:
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
                                 f"({RISK_PCT:g}% of ${ACCOUNT_SIZE:,}) — on a long "
                                 f"option the premium IS the max loss.")
                if opt["spread_pct"] > 10:
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

    if not args.dry_run:
        save_state(state)

    logger.info("Done. %d alert(s) sent, %d suppressed by cooldown, "
                "%d ticker(s) failed.", hits, skipped, len(failed))
    if failed:
        logger.warning("Failed tickers: %s", ", ".join(failed))
    return 0


def selftest() -> int:
    """
    Offline checks for the unattended paths. This module sends real alerts on
    a schedule and had NO test coverage at all, which is how it could have
    fetched nothing for days while looking exactly like a quiet market.
    """
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
    return p.parse_args()


if __name__ == "__main__":
    _args = parse_args()
    raise SystemExit(selftest() if _args.selftest else run(_args))
