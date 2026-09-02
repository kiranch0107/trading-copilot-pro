# trading_copilot_elite.py
# Run: streamlit run trading_copilot_elite.py

import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import os
import json
import logging
import requests
import math
import time
from datetime import datetime, date as _date, timedelta as _timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import pytz
import signal_core
import data_source
import market_context
import gh_sync
from journal_store import (
    load_alerts, save_alerts, load_journal, save_journal,
    load_positions, save_positions, load_skipped, save_skipped,
    log_skipped_signal, open_option_position, close_position,
    log_alert, add_journal_trade, journal_stats, calc_position_size,
    MIN_JOURNAL_TRADES_FOR_SIGNAL,
)
from rate_limit import (
    RATE_LIMITER as _rl, RATE_LIMITER_SLOW as _rl_slow,
    is_rate_limit_error as _is_rate_limit_error,
)
from option_chain import get_option_data, _fetch_chain_with_retry, _OPT_MAX_EXPIRIES


def _build_stamp() -> str:
    """
    Last-modified time of this file. On Streamlit Cloud that is when the repo
    was cloned, i.e. deploy time — so it answers "is my fix actually live?"
    without needing to be bumped by hand. A manual version constant is only
    correct until the first time you forget to bump it, and the moments it
    matters most are exactly the moments you are moving fast.
    """
    try:
        import os as _os, datetime as _dt
        return _dt.datetime.fromtimestamp(
            _os.path.getmtime(__file__)).strftime("%b %d %H:%M")
    except Exception:
        return "unknown"


APP_BUILD = _build_stamp()

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("trading_copilot")

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(page_title="Trading Copilot ELITE", layout="wide",
                   initial_sidebar_state="expanded")

st.markdown("""
<style>
  .block-container{padding-top:1.5rem}
  .stAlert{border-radius:8px}
  div[data-testid="metric-container"]{background:#1e1e2e;border:1px solid #333;
    border-radius:8px;padding:12px}
  .filter-pass{background:#0d2b1a;border-left:3px solid #22c55e;
    padding:6px 10px;border-radius:5px;margin:3px 0;font-size:.85em}
  .filter-fail{background:#2b0d0d;border-left:3px solid #ef4444;
    padding:6px 10px;border-radius:5px;margin:3px 0;font-size:.85em}
</style>
""", unsafe_allow_html=True)

st.title("🤖 Trading Copilot ELITE")
st.caption("Swing · Options · Alerts · Journal · ADX · Multi-TF · Earnings Guard · Regime Filter")


# ═════════════════════════════════════════════════════════════════════
# GITHUB-BACKED STORAGE — see gh_sync.py
#
# Extracted out of this file into its own module (same reasoning that put
# the signal logic in signal_core.py): it has no Streamlit UI of its own,
# so it was the safest first piece to pull out of a 3,000+ line file.
# gh_sync.py's docstring has the full "why" (data loss on Streamlit Cloud's
# stateless containers; the app and exit_monitor.py writing to two disks
# that never synced) and the one-time GITHUB_TOKEN setup steps.
# ═════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────
# SIDEBAR — CONFIG & TUNABLES
# ─────────────────────────────────────────────
st.sidebar.header("⚙️ Scan Settings")

# ── Watchlist source ────────────────────────────────────────────────
# STATIC is the frozen baseline: the config selected by the Aug 2026 sweep.
# It is kept as the default because it is the best-DEFINED hypothesis tested,
# not because it is profitable — the 591-trade out-of-sample validation says
# it is not (-0.014 R, PF 0.98).
#
# DYNAMIC ranks a broad candidate pool by relative strength as of today. That
# removes survivorship bias from the universe (NVDA/META/MSFT are on the
# static list BECAUSE they already went up), but it changes only WHICH stocks
# get scanned — the entry logic is untouched and still has to earn its keep.
STATIC_WATCHLIST = ["NVDA","META","MSFT"]


@st.cache_data(ttl=3600, show_spinner=False)
def _resolve_universe(_cache_key: str) -> tuple[list, str, int, str]:
    """
    Thin cached wrapper over universe.load_universe — the SAME function
    scanner.py calls, so the two can never drift apart on freshness rules.

    allow_live=False, matching scanner.py exactly. An earlier version let the
    app rank live when no snapshot existed — but the scanner cannot do that
    (it must not spend ~67 Yahoo calls before its own scan), so the two would
    resolve to DIFFERENT universes precisely when the snapshot was missing.
    Both now fall back to the same static baseline and say so loudly.
    """
    try:
        import universe as _u
    except Exception as e:
        return STATIC_WATCHLIST, f"universe.py unavailable ({e})", -1, "fallback"
    return _u.load_universe(STATIC_WATCHLIST, allow_live=False)


def _dynamic_default() -> bool:
    """Env var seeds the toggle so app and scanner start in the same mode."""
    try:
        import universe as _u
        return _u.dynamic_enabled()
    except Exception:
        return False


USE_DYNAMIC = st.sidebar.checkbox(
    "Dynamic universe (RS-ranked)", value=_dynamic_default(),
    help="OFF: scan the fixed NVDA/META/MSFT baseline. ON: scan the top names "
         "by 63-session relative strength vs SPY, sector-capped. Reads the "
         "newest file in universe_history/ written by the weekly scheduled "
         "job — no Yahoo calls, and the scanned list matches the churn "
         "history exactly. Falls back to a live ranking only if no snapshot "
         "exists. Changing this mid-test makes the sample harder to "
         "interpret — finish the current trade run before switching.")

if USE_DYNAMIC:
    _today = datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d")
    WATCHLIST, _uni_status, _uni_age, _uni_src = _resolve_universe(_today)

    if _uni_src == "snapshot":
        st.sidebar.caption(f"Dynamic universe — {_uni_status} ({_uni_age}d old)")
    elif _uni_src == "snapshot-stale":
        st.sidebar.warning(f"{_uni_status}. Check the Actions tab. "
                           f"Scanning it anyway.")
    else:
        st.sidebar.error(
            f"No usable universe snapshot ({_uni_status}) — scanning the "
            f"static baseline. scanner.py is doing the same, so the two stay "
            f"in sync. Run the weekly job to restore the dynamic universe.")
else:
    WATCHLIST = STATIC_WATCHLIST

# FIX #11: FAST_MODE exposed as sidebar toggle
# Defaults OFF whenever the dynamic universe is active: it slices WATCHLIST[:5],
# and scanner.py has no equivalent, so leaving it on would have the app scan 5
# names while the scanner alerts on all 8 — a silent desync in the other
# direction from the one above.
FAST_MODE  = st.sidebar.checkbox(
    "Fast Mode (top 5 only)", value=not USE_DYNAMIC,
    help="Scans only the first 5 tickers. Leave OFF with the dynamic universe "
         "on, or the app and the scanner cover different lists.")
SCAN_LIST  = WATCHLIST[:5] if FAST_MODE else WATCHLIST
st.sidebar.caption(f"Scanning: {', '.join(SCAN_LIST)}")
if USE_DYNAMIC and FAST_MODE and len(WATCHLIST) > 5:
    st.sidebar.warning(
        f"Fast Mode is trimming {len(WATCHLIST) - 5} name(s) off the ranking. "
        f"scanner.py scans all {len(WATCHLIST)}, so alerts may arrive for "
        f"tickers this scan skipped.")
st.sidebar.caption(f"build {APP_BUILD}")

_fallback_src = st.session_state.get("_fallback_source")
if _fallback_src:
    st.sidebar.info(f"Some price data came from {_fallback_src.title()} — Yahoo "
                    f"was unavailable. Fallback bars are not split-adjusted; "
                    f"option data is unaffected (it is always Yahoo).")
st.sidebar.divider()

# ── Sidebar defaults come FROM signal_core.DEFAULTS ──────────────────
# Not hardcoded. Every one of these used to be a literal, and three of them
# had silently drifted away from the values scanner.py runs on:
#     atr_stop_mult  app 1.0  vs core 1.25   <- changes the STOP, the R:R,
#                                               and therefore which setups
#                                               clear min_rr / hq_min_rr
#     hq_min_rr      app 1.5  vs core 1.0    <- scanner Telegrams "high
#                                               quality" at 1.0-1.5 that the
#                                               app would not badge as such
#     volume_mult    app 1.0  vs core 1.2    <- different "Strong" tag, which
#                                               feeds high_quality
# That is the same class of bug signal_core.py was created to end (see its
# docstring): one signal implementation is not enough if the two callers feed
# it different parameters. oos_validate.lock.json's live_divergence_note even
# recorded "Live now matches frozen.atr_stop=1.25" — true of scanner.py, but
# not of this file, which is the surface you actually read before trading.
# Sourcing them here means a default cannot drift again without editing
# signal_core.SignalParams, where both callers see it.
_D = signal_core.DEFAULTS

ADX_MIN       = st.sidebar.number_input("ADX minimum",              value=int(_D.adx_min), min_value=1, max_value=100)
EARNINGS_DAYS      = int(st.sidebar.number_input("Earnings blackout days",      value=3,   min_value=0, max_value=30))
POST_EARNINGS_DAYS = int(st.sidebar.number_input("Post-earnings cooling (days)", value=1,   min_value=0, max_value=7,
    help="Also block signals N days AFTER earnings (avoids IV crush residual)"))
BUDGET_MAX    = st.sidebar.number_input("Budget max (option mid)",   value=2.00, min_value=0.01, step=0.10)
MIN_DTE       = int(st.sidebar.number_input("Min DTE for options",   value=12,   min_value=1,
    help="Minimum days-to-expiry to consider. Your swing target (2.5× ATR) usually needs "
         "~8 sessions to play out — a 1-2 DTE contract will lose to theta even if the "
         "trade thesis is correct. 7+ is a sane floor for swing trades."))
MIN_RR        = st.sidebar.number_input("Min Reward/Risk",           value=float(_D.min_rr),  min_value=0.1,  step=0.1)
HQ_MIN_RR     = st.sidebar.number_input("High-Quality R:R threshold", value=float(_D.hq_min_rr), min_value=0.2, step=0.1,
    help="R:R needed to qualify as a 🔥 HIGH QUALITY setup (these trigger Telegram alerts). "
         "Must also be 'Strong' strength with all 4 filters passing. The default matches "
         "signal_core.DEFAULTS.hq_min_rr, which is the threshold scanner.py actually "
         "alerts on — raise it here and the app will stop badging setups the scanner "
         "still Telegrams you.")
MIN_ROWS      = int(st.sidebar.number_input("Min history bars",      value=50,   min_value=10))
VOLUME_MULT   = st.sidebar.number_input("Volume multiplier",         value=float(_D.volume_mult), min_value=0.1, step=0.1)
ATR_STOP_MULT = st.sidebar.number_input("ATR stop multiplier",       value=float(_D.atr_stop_mult), min_value=0.5, max_value=4.0, step=0.25,
    help="Stop distance = this × ATR. The default matches signal_core.DEFAULTS and the "
         "frozen out-of-sample config (oos_validate.py FROZEN.atr_stop = 1.25) — i.e. the "
         "only value that has actually been validated end to end, and the one scanner.py "
         "runs on. Note the synthetic sweep scored 1.0 higher on raw expectancy (+0.252 R "
         "vs +0.162 R at 1.5) because tighter stops cut losers faster; it also whipsaws "
         "more. Changing this here makes the app disagree with the scanner and with the "
         "OOS test, so change it knowing that.")
ATR_TGT_MULT  = st.sidebar.number_input("ATR target multiplier",     value=float(_D.atr_tgt_mult), min_value=1.0, max_value=6.0, step=0.25,
    help="Target distance = this × ATR. Backtested across 300 simulated market series, "
         "3.0 lifted per-trade expectancy ~29% over 2.5 (+0.196 → +0.252 R) with the same "
         "stop and same trade count — the edge in trend-following comes from letting "
         "winners run. Win rate drops slightly (you reach a farther target less often) but "
         "the larger average win more than compensates.")
MAX_HOLD      = int(st.sidebar.number_input("Max hold (sessions)",   value=30,   min_value=0, max_value=250, step=5,
    help="Bar-count time stop, mirroring backtest.py --max-hold. Counted in "
         "TRADING SESSIONS, not calendar days, so weekends and holidays do not "
         "burn the clock. 0 disables it. Applied to new positions only — "
         "contracts already being monitored keep the rules they were opened with."))
st.sidebar.divider()

# MIN_DTE and ATR_TGT_MULT are coupled: the theta check needs roughly
# ATR_TGT_MULT × 3 sessions for the target to play out. If Min DTE is below
# that, EVERY contract the search returns gets flagged as theta-inadequate —
# which looks like a bug but is just the two settings disagreeing.
_days_needed_preview = max(5, int(ATR_TGT_MULT * 3))
if MIN_DTE < _days_needed_preview:
    st.sidebar.warning(
        f"⚠️ Min DTE ({MIN_DTE}) is below the ~{_days_needed_preview} sessions a "
        f"{ATR_TGT_MULT}× ATR target usually needs. Every contract found will be "
        f"flagged as theta-inadequate. Raise Min DTE to {_days_needed_preview}+ "
        f"or lower the ATR target multiplier."
    )

WEEKLY_CONFIRM = st.sidebar.checkbox("Require weekly TF alignment",  value=bool(_D.weekly_confirm))
SPY_REGIME     = st.sidebar.checkbox("Apply SPY regime filter",      value=bool(_D.spy_regime_on))
st.sidebar.divider()
st.sidebar.header("📍 Exit Monitoring")

# ── Storage status — make silent data loss impossible to miss ──
if gh_sync.gh_enabled():
    _gh_err = st.session_state.get("_gh_last_error")
    if _gh_err:
        st.sidebar.error(f"🔴 GitHub sync FAILING\n\n{_gh_err[:120]}")
    else:
        st.sidebar.success(f"🟢 Synced to `{gh_sync.GITHUB_REPO}`")
        st.sidebar.caption("Positions persist across sessions and are visible to "
                           "the exit monitor.")
else:
    st.sidebar.error(
        "🔴 **No GITHUB_TOKEN — data will be lost**\n\n"
        "Positions are only in this browser session. Closing the tab loses "
        "them, and `exit_monitor.py` cannot see them, so **no exit alert can "
        "ever fire**. Add GITHUB_TOKEN to your Streamlit secrets.")

EXIT_CHECK_MINUTES = int(st.sidebar.number_input(
    "Check interval (minutes)", value=30, min_value=5, max_value=240, step=5,
    help="How often exit_monitor.py should check your open positions. Set your "
         "scheduler (cron / GitHub Actions) to the SAME interval — the monitor "
         "scans the intraday range since its last check, so matching them means "
         "no window is missed."))
st.sidebar.caption("Alerts come from `exit_monitor.py` on a scheduler, not from "
                   "this app — an app only runs while a tab is open.")
st.sidebar.divider()
st.sidebar.header("💰 Position Sizing")
ACCOUNT_SIZE = int(st.sidebar.number_input("Account size ($)",   value=1500, min_value=100, step=500))
RISK_PCT     = st.sidebar.number_input("Risk per trade (%)",     value=1.0,   min_value=0.1, max_value=10.0, step=0.1)
# ─────────────────────────────────────────────
# PERSISTENCE, ALERTS, JOURNAL, POSITIONS, POSITION SIZING — see journal_store.py
#
# Extracted out of this file (same reasoning as gh_sync.py before it): the
# alert cooldown constant (COOLDOWN), the four JSON stores' load/save
# functions, log_skipped_signal, open_option_position, close_position,
# log_alert, add_journal_trade, journal_stats and calc_position_size all
# moved there. Imported by name above so every call site below is unchanged.
#
# capture_entry_features() below did NOT move: it calls get_data() / compute()
# / drop_partial_bar() / get_spy_regime(), which are defined further down in
# THIS file (the market-data layer, not yet split out). Callers now compute
# the snapshot themselves and pass it into journal_store.open_option_position()
# as `entry_features` — see that function's docstring for why.
# ─────────────────────────────────────────────
def capture_entry_features(ticker: str) -> dict:
    """
    Snapshot indicator state at the moment a position is opened.

    COLLECT ONLY. These fields exist so that, once there are enough trades to
    support it, you can ask which conditions the winners shared. They are NOT
    read by any filter and do not affect whether a signal fires.

    Deliberately not analysed yet: slicing 30 trades by ADX bucket will always
    produce a bucket that looks good, because that is what random data does.
    Wait for a few hundred, and write down the question before looking.

    Returns {} on any failure — a missing snapshot must never block a trade.
    """
    try:
        df = get_data(ticker)
        if df is None or df.empty:
            return {}
        df = compute(df)
        df, _ = drop_partial_bar(df)
        if df.empty:
            return {}
        last = df.iloc[-1]

        def _num(v):
            try:
                v = float(v)
                return round(v, 4) if math.isfinite(v) else None
            except (TypeError, ValueError):
                return None

        feats = {
            "adx":        _num(last.get("ADX")),
            "rsi":        _num(last.get("RSI")),
            "atr":        _num(last.get("ATR")),
            "close":      _num(last.get("Close")),
            "ema20":      _num(last.get("EMA20")),
            "ema50":      _num(last.get("EMA50")),
            "volume":     _num(last.get("Volume")),
            "vol_avg20":  _num(last.get("VOL_AVG20")),
        }

        # Relative volume — today's volume against its own 20-day average.
        if feats["volume"] and feats["vol_avg20"]:
            feats["rvol"] = round(feats["volume"] / feats["vol_avg20"], 3)
        else:
            feats["rvol"] = None

        # Price extension above EMA20, expressed in ATR — how stretched the
        # entry was. A large value means chasing.
        if feats["close"] and feats["ema20"] and feats["atr"]:
            feats["ext_atr"] = round((feats["close"] - feats["ema20"]) / feats["atr"], 3)
        else:
            feats["ext_atr"] = None

        # Relative strength vs SPY over 20 sessions. Recorded, not filtered on.
        try:
            spy = get_data("SPY")
            if spy is not None and len(spy) > 21 and len(df) > 21:
                t_ret = float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-21]) - 1
                s_ret = float(spy["Close"].iloc[-1]) / float(spy["Close"].iloc[-21]) - 1
                feats["rs_20d"] = round(t_ret - s_ret, 4)
            else:
                feats["rs_20d"] = None
        except Exception:
            feats["rs_20d"] = None

        feats["regime"] = (get_spy_regime() or {}).get("regime")
        feats["captured"] = datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d %H:%M ET")
        return feats
    except Exception as e:
        logger.warning("Feature capture failed for %s (%s)", ticker, e)
        return {}


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def short_ts(ts: str) -> str:
    """FIX #6: compact timestamp — 'Jul 1 14:32' instead of '2025-07-01 14:32 ET'"""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M ET")
        # %-d is a glibc extension (raises on Windows) — build portably:
        return f"{dt.strftime('%b')} {dt.day} {dt.strftime('%H:%M')}"
    except Exception:
        return ts


# ─────────────────────────────────────────────
# MARKET HOURS
# ─────────────────────────────────────────────
# NYSE full-day closures and 1:00pm ET half-days.
#
# BUG FIX: is_market_open() previously checked only weekday + clock time, so on
# Thanksgiving, Christmas, Good Friday etc. it reported the market OPEN. That
# matters because drop_partial_bar() trusts it: believing the market is open,
# it DISCARDS the last completed daily bar as if it were a partial in-progress
# bar. Every indicator, level and ATR was then computed on data a full session
# stale — silently, with no warning to the user.
#
# Maintenance note: these are fixed dates published by the NYSE each year. Add
# the next year's list when it's released; an unknown future year simply falls
# back to weekday+time behaviour (the old, slightly-wrong-on-holidays logic).
MARKET_HOLIDAYS = {
    # 2025
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27",
    "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}
# Early closes — market shuts at 1:00pm ET
MARKET_HALF_DAYS = {
    "2025-07-03", "2025-11-28", "2025-12-24",
    "2026-11-27", "2026-12-24",
}


def is_market_open() -> bool:
    try:
        tz  = pytz.timezone("America/New_York")
        now = datetime.now(tz)
        if now.weekday() >= 5:
            return False
        day = now.strftime("%Y-%m-%d")
        if day in MARKET_HOLIDAYS:
            return False
        close_hour, close_min = (13, 0) if day in MARKET_HALF_DAYS else (16, 0)
        return (now.replace(hour=9, minute=30, second=0, microsecond=0)
                <= now <=
                now.replace(hour=close_hour, minute=close_min,
                            second=0, microsecond=0))
    except Exception:
        return False


# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
def send_telegram_alert(ticker: str, message: str) -> None:
    TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
    CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": message}, timeout=5)
    except Exception:
        logger.exception("Failed to send Telegram alert for %s", ticker)


# ─────────────────────────────────────────────
# RATE LIMITER — see rate_limit.py
#
# RateLimiter, _rl, _rl_slow and _is_rate_limit_error moved there so
# option_chain.py can share the SAME limiter instances instead of getting
# its own independent budget. Imported by name above; every call site below
# (_rl.wait(), _rl_slow.wait(), _is_rate_limit_error(e)) is unchanged.
# ─────────────────────────────────────────────
# SPY_ADX_THRESHOLD used to live here: the regime was only Bull/Bear when SPY's
# ADX cleared 20, and "Neutral" below it — which signal_core reads as "no view"
# and does not filter on. backtest.build_regime_series() has no such gate, and
# the 591-trade OOS test ran with use_regime=True, so the gate was never part of
# anything that was validated. It is gone, not moved: see market_context.py,
# which reports SPY's ADX but does not let it decide the regime.

_YF_RETRY_TRIES = 3
_YF_RETRY_DELAY = 2.0


def _yf_download_with_retry(ticker: str, period: str, interval: str) -> pd.DataFrame | None:
    delay = _YF_RETRY_DELAY
    last_err = None
    out = None
    for attempt in range(_YF_RETRY_TRIES):
        _rl.wait()
        try:
            out = yf.download(ticker, period=period, interval=interval, progress=False)
            # BUG FIX: yf.download returns an EMPTY DataFrame when throttled
            # rather than raising, so `return yf.download(...)` handed back the
            # empty frame on attempt 1 and the retry loop never ran. The caller
            # then reported "No usable data for 'TGT' — check the symbol",
            # which sends you to check a ticker that was never the problem.
            # Treat empty as retryable, exactly like an explicit rate-limit
            # exception.
            if out is not None and not out.empty:
                return out
            if attempt < _YF_RETRY_TRIES - 1:
                logger.warning("Empty frame for %s (likely throttled); "
                               "backing off %ss", ticker, delay)
                time.sleep(delay); delay *= 2; continue
        except Exception as e:
            last_err = e
            if _is_rate_limit_error(e) and attempt < _YF_RETRY_TRIES - 1:
                logger.warning("Rate limited yf.download(%s). Backing off %ss", ticker, delay)
                time.sleep(delay); delay *= 2; continue
            raise
    if last_err: raise last_err
    return out


def _normalise_df(df: pd.DataFrame, min_rows: int) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Open","High","Low","Close","Volume"])
    return df if len(df) >= min_rows else None


@st.cache_data(ttl=600, show_spinner=False)
def get_data(ticker: str, period: str = "1y", interval: str = "1d",
             min_rows: int = 50) -> pd.DataFrame | None:
    # min_rows is a cache-key param (was the MIN_ROWS global — stale on change)
    try:
        return _normalise_df(_yf_download_with_retry(ticker, period, interval), min_rows)
    except Exception as e:
        logger.info("get_data(%s) failed: %s", ticker, e)
        return None


def get_data_with_error(ticker: str, period: str = "1y",
                        interval: str = "1d") -> tuple[pd.DataFrame | None, str | None]:
    """
    Yahoo first, then data_source's fallback (currently Tiingo, if
    TIINGO_API_KEY is set; Stooq is a dead stub — see data_source.py) if
    Yahoo comes back empty or throws.

    Retries alone could not fix the throttle problem — they only make a
    throttled app slower. A second, independent source can. The fallback
    covers daily and weekly OHLCV, which is what every tab depends on;
    options remain Yahoo-only because neither fallback has chains.
    """
    df, source = data_source.fetch_daily(
        ticker, period, interval,
        yahoo_fetch=_yf_download_with_retry)

    if df is None:
        return None, (f"No configured price source returned data for "
                      f"'{ticker}'. If the symbol is right, Yahoo (and the "
                      f"fallback, if configured) are unavailable — wait a "
                      f"minute and retry.")
    if source != "yahoo":
        # Surfaced so a silent source switch never goes unnoticed: fallback
        # bars are not split-adjusted the way Yahoo's are.
        logger.info("Using %s data for %s (Yahoo unavailable)", source, ticker)
        st.session_state["_fallback_source"] = source
    # Past this point a frame exists; the only remaining failure is too few
    # bars. The "nothing came back" case is handled above, where we know which
    # source was tried.
    rows = len(df)
    df = _normalise_df(df, MIN_ROWS)
    if df is None:
        return None, (f"Only {rows} usable bars for '{ticker}' from "
                      f"{source} (need {MIN_ROWS}). Try a longer period.")
    return df, None


@st.cache_data(ttl=600, show_spinner=False)
def batch_get_data(tickers: tuple, period: str = "1y",
                   interval: str = "1d",
                   min_rows: int = 50) -> dict[str, pd.DataFrame]:
    if not tickers:
        return {}
    _rl.wait()
    try:
        raw = yf.download(list(tickers), period=period, interval=interval,
                          progress=False, group_by="ticker")
    except Exception as e:
        logger.exception("Batch fetch failed, falling back: %s", e)
        raw = None

    result: dict[str, pd.DataFrame] = {}
    if raw is not None and not raw.empty and isinstance(raw.columns, pd.MultiIndex):
        for t in tickers:
            try:
                df = _normalise_df(raw[t].copy(), min_rows)
                if df is not None:
                    result[t] = df
            except Exception:
                pass
        if result:
            return result

    for t in tickers:
        df = get_data(t, period, interval, min_rows)
        if df is not None:
            result[t] = df
    return result


# ─────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────
# WARM-UP REQUIREMENTS (TA correctness — this is not cosmetic):
#   EMA50  — ta seeds the EMA with an SMA of the first 50 bars, so the EMA
#            has only "evolved" for (bars - 50) periods. Needs ~150 bars (3×
#            the span) before its value is materially correct.
#   MACD   — built on a 26-period EMA, so needs ~78 bars (3 × 26).
#   ADX    — DOUBLE-smoothed Wilder (DI smoothing, then ADX smoothing).
#            Needs ~100+ bars to converge; it is the slowest of the set.
#   RSI/ATR— Wilder smoothing, ~100 bars to fully settle.
#
# Measured on 200 simulated tickers, fetching only 3 months (63 bars) versus
# 1 year produced a DIFFERENT trend direction 5.5% of the time and flipped
# the ADX≥25 filter 10.3% of the time — i.e. materially wrong trades, purely
# from insufficient warm-up. Fetch period is now "1y" and we additionally
# discard the unconverged head of the series below.
INDICATOR_WARMUP_BARS = 100   # bars discarded so every indicator has converged
MIN_BARS_AFTER_WARMUP = 40    # need at least this many usable bars to trade


def compute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["EMA20"]     = ta.trend.ema_indicator(df["Close"], window=20)
    df["EMA50"]     = ta.trend.ema_indicator(df["Close"], window=50)
    macd            = ta.trend.MACD(df["Close"])
    df["MACD"]      = macd.macd()
    df["Signal"]    = macd.macd_signal()
    df["RSI"]       = ta.momentum.rsi(df["Close"], window=14)
    df["ATR"]       = ta.volatility.average_true_range(df["High"],df["Low"],df["Close"],window=14)
    df["VOL_AVG20"] = df["Volume"].rolling(20).mean()
    df["ADX"]       = ta.trend.adx(df["High"],df["Low"],df["Close"],window=14)

    df = df.dropna(subset=["EMA20","EMA50","MACD","Signal","RSI","ATR","ADX","VOL_AVG20"])

    # Discard the unconverged head. If we have plenty of history, drop the
    # first INDICATOR_WARMUP_BARS outright. If history is thin, keep what we
    # have but the caller's MIN_BARS_AFTER_WARMUP check will reject it.
    if len(df) > INDICATOR_WARMUP_BARS + MIN_BARS_AFTER_WARMUP:
        df = df.iloc[INDICATOR_WARMUP_BARS:]

    return df


def has_sufficient_history(df: pd.DataFrame, ticker: str = "") -> bool:
    """Reject tickers whose indicators cannot be trusted."""
    if df is None or df.empty:
        return False
    if len(df) < MIN_BARS_AFTER_WARMUP:
        logger.warning(
            "%s: only %d usable bars after indicator warm-up (need %d) — "
            "indicators would be unconverged; skipping.",
            ticker or "ticker", len(df), MIN_BARS_AFTER_WARMUP
        )
        return False
    return True


def drop_partial_bar(df: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """
    CRITICAL TA FIX (bug #7): when the market is OPEN, the final daily bar is
    INCOMPLETE — its Volume is only the volume accumulated so far today, and
    its Close is the current price, not the settled close.

    The volume filter compares that partial volume against a 20-day average of
    FULL-day volumes. US equity volume is U-shaped: a stock has only ~15% of
    its daily volume by 10:00 ET and doesn't cross 70% until roughly 14:40 ET.

    Consequence of using the partial bar: a perfectly normal stock FAILS the
    0.70× volume floor all morning and PASSES every afternoon. The filter was
    measuring the clock, not the market — signals appeared and vanished purely
    as a function of when the scan was run.

    Fix: while the market is open, analyse the last COMPLETED bar (yesterday's
    settled daily bar). Every indicator, level and volume comparison is then
    computed on complete data. Returns (df, dropped_flag).
    """
    if df is None or len(df) < 2:
        return df, False
    if not is_market_open():
        return df, False          # market closed → final bar is complete
    return df.iloc[:-1], True     # market open → drop the in-progress bar


# ─────────────────────────────────────────────
# FILTER HELPERS
# ─────────────────────────────────────────────
def check_adx(df: pd.DataFrame) -> tuple[bool, float]:
    adx_val = float(df["ADX"].iloc[-1])
    return adx_val >= ADX_MIN, round(adx_val, 1)


@st.cache_data(ttl=900, show_spinner=False)
def get_weekly_trend(ticker: str) -> str | None:
    """
    Weekly trend via market_context — the SAME rule scanner.py now uses.

    This used to be an EMA10w-vs-EMA20w crossover here while scanner.py used
    price-vs-EMA20w. Different questions, opposite verdicts on ordinary
    pullbacks, on a BLOCKING filter. See market_context.py's docstring for the
    evidence (there is none either way — the backtest runs weekly_confirm=False,
    so this rule is chosen for consistency with every other trend test in the
    system, and the filter itself remains unvalidated).
    """
    def _fetch(t, period, interval):
        _rl_slow.wait()
        return yf.download(t, period=period, interval=interval,
                           progress=False, auto_adjust=False)
    return market_context.get_weekly_trend(ticker, _fetch)


@st.cache_data(ttl=3600, show_spinner=False)
def get_next_earnings(ticker: str) -> str | None:
    try:
        _rl_slow.wait()
        t   = yf.Ticker(ticker)
        cal = t.calendar
        if cal is None:
            return None
        if isinstance(cal, dict):
            date_val = cal.get("Earnings Date")
            if isinstance(date_val, (list, tuple)):
                date_val = date_val[0]
            ts = pd.to_datetime(date_val, errors="coerce")
        elif isinstance(cal, pd.DataFrame):
            if "Earnings Date" in cal.columns:
                ts = pd.to_datetime(cal["Earnings Date"].iloc[0], errors="coerce")
            else:
                first = cal.iloc[0].dropna().iloc[0] if not cal.empty else None
                ts    = pd.to_datetime(first, errors="coerce")
        else:
            ts = pd.NaT
        return None if pd.isna(ts) else str(ts.date())
    except Exception as e:
        logger.exception("get_next_earnings(%s): %s", ticker, e)
        return None


def check_earnings_blackout(ticker: str) -> tuple[bool, str]:
    ds = get_next_earnings(ticker)
    if ds is None:
        return True, "Earnings date unknown — proceed with caution"
    try:
        edt   = datetime.strptime(ds, "%Y-%m-%d").date()
        today = datetime.now(pytz.timezone("America/New_York")).date()
        days  = (edt - today).days
        if 0 <= days <= EARNINGS_DAYS:
            return False, f"⚠️ Earnings in {days}d ({ds}) — signal blocked"
        elif days < 0:
            # Fix 5: post-earnings cooling window — very recent earnings can
            # still cause IV crush / gap residual the next 1-2 days
            if abs(days) <= POST_EARNINGS_DAYS:
                return False, f"⚠️ Earnings was {abs(days)}d ago ({ds}) — post-earnings cooling ({POST_EARNINGS_DAYS}d)"
            return True, f"Last earnings: {ds} ({abs(days)}d ago)"
        return True, f"Next earnings: {ds} ({days}d away)"
    except Exception as e:
        logger.exception("check_earnings_blackout(%s): %s", ticker, e)
        return True, "Earnings check failed — proceed with caution"


@st.cache_data(ttl=1800, show_spinner=False)
def get_spy_regime() -> dict:
    """
    Macro regime via market_context — the SAME rule scanner.py and
    backtest.build_regime_series() use: price vs its own 200-SMA, two-state.

    This used to gate the verdict on ADX >= 20 and return "Neutral" below it,
    which signal_core reads as "no view" — so in choppy tape the app applied NO
    regime filter at all while the scanner still blocked counter-regime setups.
    The backtest never had that gate, and the 591-trade OOS test ran with
    use_regime=True, so the ungated rule is the validated one. ADX is still
    fetched and displayed; it just no longer decides anything.
    """
    def _fetch(t, period, interval):
        _rl_slow.wait()   # SPY fetched once per 30 min — slow limiter so it
                          # does not crowd the per-ticker data calls
        return yf.download(t, period=period, interval=interval,
                           progress=False, auto_adjust=False)
    return market_context.get_spy_regime(_fetch)


# ─────────────────────────────────────────────
# OPTIONS ENGINE — see option_chain.py
#
# _fetch_chain_with_retry, get_full_chain_data, get_option_data and the
# _OPT_* tuning constants moved there. check_manual_contract() below did NOT
# move — see option_chain.py's module docstring for why (it calls this
# file's whole signal-evaluation pipeline, a much larger dependency set than
# "fetch and rank a chain"). Imported by name above; call sites unchanged.
# ─────────────────────────────────────────────
def check_manual_contract(ticker: str, right: str, strike: float,
                          expiry: str, entry_premium: float) -> dict:
    """
    Evaluate a contract YOU picked against the same rules the scanner applies.

    WHAT THIS ANSWERS: "does this contract meet my stated criteria?"
    WHAT IT DOES NOT ANSWER: "is this a good trade?"

    Those are different questions and only the first one is checkable. The
    591-trade out-of-sample validation found no measurable edge in this entry
    logic (-0.014 R, PF 0.98), so a PASS means "this matches the pattern that
    was tested and found nothing in" — not "this will work". Treat it as a
    checklist, never as a forecast.

    Every rule is read from the SAME sidebar values and the SAME analyze()
    output the scanner uses. Nothing is reimplemented here — a second copy of
    "does this pass" would drift within a month, exactly as scanner.py drifted
    to ADX 25 while the app ran 35.
    """
    out = {"ticker": ticker.upper(), "right": right.upper(),
           "strike": float(strike), "expiry": expiry,
           "entry_premium": float(entry_premium),
           "checks": [], "signal": None, "contract": None, "error": None}

    def add(name, passed, detail, blocking=True):
        out["checks"].append({"name": name, "pass": bool(passed),
                              "detail": detail, "blocking": blocking})

    # ── A. Underlying signal — reuse analyze() verbatim ──
    try:
        df = get_data(ticker)
        if df is None or df.empty:
            out["error"] = f"No price data for {ticker}"
            return out
        df = compute(df)
        df, _ = drop_partial_bar(df)
        spy_regime = get_spy_regime()
        # fetch_options=False: analyze() would otherwise pick its OWN best
        # contract, which is wasted work and irrelevant — we are evaluating
        # the specific strike the user chose, not looking for a better one.
        a = analyze(df, ticker.upper(), f"{ticker.upper()}_{df.index[-1]}",
                    get_settings_key(), spy_regime=spy_regime,
                    fetch_options=False)
    except Exception as e:
        out["error"] = f"Analysis failed: {e}"
        return out

    out["signal"] = a
    sig_trend = (a.get("trend") or "").strip()
    want = "Bullish" if out["right"] == "CALL" else "Bearish"

    if a.get("signal"):
        add("Signal fires", True,
            f"{sig_trend} {a.get('strength','')} — ADX {a.get('adx',0):.1f}")
        add("Direction matches contract", sig_trend == want,
            f"signal is {sig_trend}, you are buying a {out['right']}")
    else:
        why = a.get("reason") or "conditions not met"
        add("Signal fires", False, f"no signal on {ticker.upper()} — {why}")
        add("Direction matches contract", False,
            "cannot match direction without a signal")

    # ── B. The contract itself ──
    try:
        dte = (pd.Timestamp(expiry) - pd.Timestamp.today().normalize()).days
    except Exception:
        out["error"] = f"Could not parse expiry {expiry!r} (use YYYY-MM-DD)"
        return out

    # BUG FIX: this referenced a MAX_DTE that does not exist in this app. The
    # scanner enforces a LOWER bound only (MIN_DTE) and then looks at the
    # nearest _OPT_MAX_EXPIRIES expiries past it — there is no upper DTE rule
    # to mirror. Inventing one here would make the checker stricter than the
    # thing it is supposed to be checking against, which defeats the purpose.
    add("DTE at or above minimum", dte >= MIN_DTE,
        f"{dte} DTE (minimum {MIN_DTE})")

    # Informational: the scanner would never surface a contract this far out,
    # since it only looks at the nearest few expiries. Not a rule violation —
    # just a note that this is outside what the scanner explores.
    if dte > 120:
        add("Within the scanner's usual horizon", False,
            f"{dte} DTE — the scanner only examines the nearest "
            f"{_OPT_MAX_EXPIRIES} expiries past {MIN_DTE} DTE, so it would "
            f"never propose this contract itself", blocking=False)

    atr = a.get("atr")
    days_needed = max(5, int(ATR_TGT_MULT * 3)) if (atr and atr > 0) else 10
    add("Theta adequate for target", dte >= days_needed,
        f"a {ATR_TGT_MULT:g}x ATR target needs ~{days_needed} sessions; "
        f"this has {dte}")

    # Live quote for THIS contract — liquidity is a property of the strike,
    # not of the underlying, so it has to be fetched specifically.
    row = None
    try:
        # BUG FIX: this used get_full_chain_data(), which caps at
        # _OPT_MAX_EXPIRIES (3) — the three NEAREST expiries. A contract 4th in
        # line (e.g. the monthly, 24 DTE, when three weeklies sit in front of
        # it) was never fetched, and the checker reported "no CALL at $X" when
        # the contract existed perfectly well. The scan needs a truncated list
        # because it is looking for a contract; here the user has already named
        # one, so fetch exactly that expiry — more accurate AND one call
        # instead of three.
        # One call: fetch the chain for exactly the expiry given. No pre-flight
        # validation against the listed-expiry list — that was a second Yahoo
        # round trip to check something the user already knows, and it was the
        # tab's main source of latency and of throttle-induced false failures.
        stock = yf.Ticker(ticker)
        chain = _fetch_chain_with_retry(stock, expiry)
        if chain is None:
            add("Contract found on chain", False,
                f"no chain came back for {ticker.upper()} {expiry} — either "
                f"that is not a listed expiry, or Yahoo is rate limiting. "
                f"If the date is right, try again in a minute.")
        else:
            side = (chain.calls if out["right"] == "CALL"
                    else chain.puts).fillna(0)
            hit = side[abs(side["strike"] - out["strike"]) < 0.01]
            if hit.empty:
                strikes = sorted(float(x) for x in side["strike"])
                near = [x for x in strikes
                        if abs(x - out["strike"]) <= max(5.0, out["strike"] * 0.05)]
                hint = (", ".join(f"${x:g}" for x in near[:8])
                        if near else "none within 5%")
                add("Contract found on chain", False,
                    f"no {out['right']} at ${out['strike']:g} on {expiry}. "
                    f"Nearby strikes: {hint}")
            else:
                row = hit.iloc[0]
    except Exception as e:
        add("Contract found on chain", False, f"chain fetch failed ({e})")

    if row is not None:
        bid, ask = float(row.get("bid", 0)), float(row.get("ask", 0))
        mid = (bid + ask) / 2
        spread = ask - bid
        vol, oi = int(row.get("volume", 0) or 0), int(row.get("openInterest", 0) or 0)
        spread_pct = (spread / mid * 100) if mid > 0 else 999.0
        out["contract"] = {"bid": bid, "ask": ask, "mid": round(mid, 2),
                           "spread": round(spread, 2),
                           "spread_pct": round(spread_pct, 1),
                           "volume": vol, "oi": oi, "dte": dte,
                           "last": float(row.get("lastPrice", 0) or 0)}
        add("Contract found on chain", True,
            f"bid ${bid:.2f} / ask ${ask:.2f}, mid ${mid:.2f}")
        add("Bid is live", bid > 0, f"bid ${bid:.2f}")
        add("Volume > 0", vol > 0, f"{vol} contracts today")
        add("Open interest > 0", oi > 0, f"{oi} open")
        add("Spread <= 15% of mid", spread_pct <= 15.0,
            f"${spread:.2f} = {spread_pct:.1f}% of mid")

        # Non-blocking, and DIRECTIONAL. An earlier version used abs(slip),
        # which flagged a fill BELOW mid as a failure — but paying under mid
        # when buying is a good fill, not a bad one. Only paying OVER mid
        # costs you anything.
        #
        # This comparison is also only meaningful for a contract you are about
        # to buy. If the position was opened days ago the "gap" is mostly the
        # contract having moved since, not slippage, so it is reported as
        # context rather than judged.
        if mid > 0 and out["entry_premium"] > 0:
            slip = (out["entry_premium"] - mid) / mid * 100
            if slip > 10:
                add("Entry not far above mid", False,
                    f"paid ${out['entry_premium']:.2f} vs mid ${mid:.2f} "
                    f"(+{slip:.1f}%) — that premium is a cost you carry from "
                    f"the first tick", blocking=False)
            elif slip < -10:
                add("Entry not far above mid", True,
                    f"paid ${out['entry_premium']:.2f} vs mid ${mid:.2f} "
                    f"({slip:+.1f}%) — below mid. Good fill if this was just "
                    f"bought; if the position is older, the contract has "
                    f"simply moved since entry", blocking=False)
            else:
                add("Entry not far above mid", True,
                    f"paid ${out['entry_premium']:.2f} vs mid ${mid:.2f} "
                    f"({slip:+.1f}%)", blocking=False)

    # ── C. Verdict ──
    blocking = [c for c in out["checks"] if c["blocking"]]
    out["n_pass"] = sum(1 for c in blocking if c["pass"])
    out["n_total"] = len(blocking)
    out["passed"] = all(c["pass"] for c in blocking)
    out["failures"] = [c["name"] for c in blocking if not c["pass"]]
    return out


# ─────────────────────────────────────────────
# TRADE ANALYSIS
#
# Returns a dict on success, OR a diagnostic dict
# with "blocked": True so the UI can always show
# exactly WHY — base conditions / filters / RR.
# Never returns bare None anymore.
# ─────────────────────────────────────────────
def _signal_params() -> "signal_core.SignalParams":
    """
    Build the shared parameter object from the sidebar.

    scanner.py uses signal_core.DEFAULTS. The app lets you move these live, but both
    read the SAME dataclass, so a field cannot exist in one and not the other.
    """
    return signal_core.SignalParams(
        adx_min=float(ADX_MIN),
        min_rr=float(MIN_RR),
        hq_min_rr=float(HQ_MIN_RR),
        volume_mult=float(VOLUME_MULT),
        atr_stop_mult=float(ATR_STOP_MULT),
        atr_tgt_mult=float(ATR_TGT_MULT),
        weekly_confirm=bool(WEEKLY_CONFIRM),
        spy_regime_on=bool(SPY_REGIME),
    )


def _analyze_uncached(df: pd.DataFrame, ticker: str,
                      spy_regime: dict | None = None,
                      fetch_options: bool = True) -> dict:
    """
    Adapter over signal_core.evaluate().

    The signal logic used to live here, and scanner.py had its own separate
    copy. They disagreed — the scanner missed the volume, weekly, earnings and
    regime gates entirely — which produced alerts the app then rejected. Both
    now call one implementation; this function only supplies the fetches
    (weekly trend, earnings, ADX) and bolts the option chain onto the result.

    Kept identical in shape to the old return dict so every caller and every
    UI renderer downstream continues to work unchanged.
    """
    params = _signal_params()
    adx_ok, adx_val = check_adx(df)

    r = signal_core.evaluate(
        df, ticker, params,
        adx_value=adx_val,
        weekly_trend=get_weekly_trend(ticker) if WEEKLY_CONFIRM else None,
        earnings=check_earnings_blackout(ticker),
        spy_regime=spy_regime,
    )
    if r["blocked"]:
        return r

    # Option chain stays here: it is app-specific, rate-limit sensitive, and
    # irrelevant to whether the SIGNAL fired.
    r["option"] = (get_option_data(ticker, r["price"], r["trend"],
                                   r["strength"], MIN_DTE, ATR_TGT_MULT,
                                   BUDGET_MAX, is_market_open, atr=r["atr"])
                   if fetch_options else
                   {"error": "Not fetched during scan — open the Stock "
                             "Analysis tab for options."})
    r["signal"] = True
    return r

def analyze(_df: pd.DataFrame, ticker: str, latest_bar_key: str,
            settings_key: str, spy_regime: dict | None = None,
            fetch_options: bool = True) -> dict:
    """
    BUG FIX #1: settings_key is a fingerprint of every sidebar tunable that
    _analyze_uncached() reads as a global (ADX_MIN, MIN_RR, VOLUME_MULT,
    EARNINGS_DAYS, POST_EARNINGS_DAYS, WEEKLY_CONFIRM, SPY_REGIME).

    Previously those were captured as CLOSURES, not cache-key params — so
    changing ADX_MIN from 25→40 in the sidebar did NOT invalidate this cache.
    Users saw stale results computed with the OLD threshold for up to 5 minutes
    with no indication anything was wrong.

    Including the fingerprint in the signature forces Streamlit to treat a
    settings change as a cache miss.
    """
    return _analyze_uncached(_df, ticker, spy_regime=spy_regime,
                             fetch_options=fetch_options)


def get_settings_key() -> str:
    """Fingerprint of all sidebar tunables that affect signal logic."""
    return (
        f"adx{ADX_MIN}_rr{MIN_RR}_hqrr{HQ_MIN_RR}_vol{VOLUME_MULT}"
        f"_astop{ATR_STOP_MULT}_atgt{ATR_TGT_MULT}"
        f"_earn{EARNINGS_DAYS}_post{POST_EARNINGS_DAYS}"
        f"_wk{int(WEEKLY_CONFIRM)}_spy{int(SPY_REGIME)}"
        f"_dte{MIN_DTE}_bud{BUDGET_MAX}_rows{MIN_ROWS}"
    )


# ─────────────────────────────────────────────
# SCALP ENGINE
# ─────────────────────────────────────────────
def scalp(df: pd.DataFrame) -> dict:
    latest = df.iloc[-1]
    price  = float(latest["Close"])
    atr    = float(latest["ATR"]) if "ATR" in df.columns else 0
    # S1 FIX: widened from 6 to 12 bars — 6 bars = only 30 min of 5-min data,
    # too sensitive; 12 bars = 1 hour gives a more stable intraday range.
    prior_high = float(df["High"].iloc[-13:-1].max())
    prior_low  = float(df["Low"].iloc[-13:-1].min())
    if (prior_high - prior_low)/price < 0.005:
        return {"signal":"Low volatility — avoid scalping","direction":None}
    rsi  = float(latest["RSI"])    if "RSI"    in df.columns else 50
    macd = float(latest["MACD"])   if "MACD"   in df.columns else 0
    sig  = float(latest["Signal"]) if "Signal" in df.columns else 0
    if price>prior_high and macd>sig and rsi<75:
        return {"signal":f"Breakout scalp ↑ {round(price,2)}","direction":"Long",
                "stop":round(prior_high-atr*0.5,2),"target":round(price+atr,2)}
    elif price<prior_low and macd<sig and rsi>25:
        return {"signal":f"Breakdown scalp ↓ {round(price,2)}","direction":"Short",
                "stop":round(prior_low+atr*0.5,2),"target":round(price-atr,2)}
    return {"signal":"No clear intraday setup","direction":None}


# ─────────────────────────────────────────────
# WATCHLIST SCAN
# FIX: ThreadPoolExecutor moved OUT of
# @st.cache_data. Streamlit's cache serialises
# the return value — running threads inside the
# cached function causes OOM on Streamlit Cloud.
# Pattern: uncached _run_scan() does the work;
# cached run_watchlist_scan() stores the result.
# ─────────────────────────────────────────────
_SCAN_MAX_WORKERS = 2   # reduced from 3 → 2 for Streamlit Cloud memory headroom


def _scan_one_ticker(ticker: str, data_map: dict, spy_regime: dict,
                     settings_key: str) -> dict | None:
    df = data_map.get(ticker)
    if df is None: return None
    df = compute(df)
    df, _ = drop_partial_bar(df)      # bug #7: never analyse an in-progress bar
    if not has_sufficient_history(df, ticker): return None
    # fetch_options=False: the scan must not pull option chains (see the LAZY
    # note in _analyze_uncached). This is the single biggest reduction in
    # Yahoo call volume — a 5-ticker scan drops from ~16-32 calls to ~12.
    r = analyze(df, ticker, f"{ticker}_{df.index[-1]}", settings_key,
                spy_regime=spy_regime, fetch_options=False)
    return r if r and not r.get("blocked") else None


def _run_scan_uncached(scan_list: tuple, spy_regime: dict,
                       settings_key: str) -> list[dict]:
    """Does the actual parallel work — not cached so threads don't OOM cache."""
    data_map = batch_get_data(scan_list, min_rows=MIN_ROWS)
    results  = []
    with ThreadPoolExecutor(max_workers=_SCAN_MAX_WORKERS) as executor:
        futures = {
            executor.submit(_scan_one_ticker, t, data_map, spy_regime, settings_key): t
            for t in scan_list
        }
        for future in as_completed(futures):
            try:
                r = future.result()
                if r: results.append(r)
            except Exception as e:
                logger.exception("Scan ticker failed: %s", e)
    return sorted(results, key=lambda x: x["rr"], reverse=True)


@st.cache_data(ttl=300, show_spinner=False)
def run_watchlist_scan(scan_list: tuple, spy_regime_key: str,
                       settings_key: str) -> list[dict]:
    """
    Cached wrapper. Both spy_regime_key AND settings_key are part of the cache
    signature so the scan re-runs when either the macro regime OR any sidebar
    tunable changes (BUG FIX #1).
    """
    spy_regime = get_spy_regime()   # cached at ttl=1800, cheap
    return _run_scan_uncached(scan_list, spy_regime, settings_key)


# ─────────────────────────────────────────────
# UI HELPERS
# ─────────────────────────────────────────────
def render_filter_scorecard(filters: dict, n_pass: int, n_total: int):
    st.markdown(f"**Signal Filters: {n_pass}/{n_total} passed**")
    icons = {True:"✅",False:"❌"}
    for name, f in filters.items():
        css = "filter-pass" if f["pass"] else "filter-fail"
        st.markdown(
            f'<div class="{css}">{icons[f["pass"]]} <b>{name}</b> — {f["detail"]}</div>',
            unsafe_allow_html=True)


def render_no_signal_diagnostic(df, latest_price, latest_rsi, vol_now, vol_avg,
                                diag: dict | None = None):
    """
    Shows exactly WHY no tradeable signal was produced.
    Now consumes the rich diagnostic dict from _analyze_uncached so when
    base conditions ALL pass (like NVDA above) the actual 4 enhancement
    filter results are shown instead of a misleading 'check filters above'.
    """
    ema20_v   = float(df["EMA20"].iloc[-1])
    ema50_v   = float(df["EMA50"].iloc[-1])
    macd_v    = float(df["MACD"].iloc[-1])
    sig_v     = float(df["Signal"].iloc[-1])
    rsi_v     = latest_rsi
    vol_ratio = vol_now / vol_avg if vol_avg else 0

    stack_bull = latest_price > ema20_v > ema50_v
    stack_bear = latest_price < ema20_v < ema50_v
    macd_bull  = macd_v > sig_v
    macd_bear  = macd_v < sig_v
    vol_floor  = vol_ratio >= 0.70

    def chk(ok): return "✅" if ok else "❌"

    if stack_bull:
        implied      = "Bullish"
        macd_aligned = macd_bull
        rsi_ok       = 30 < rsi_v < 75
        macd_label   = f"need MACD > Signal (MACD {macd_v:.3f} {'>' if macd_bull else '<'} Signal {sig_v:.3f})"
        rsi_label    = f"need RSI 30–75 (RSI {rsi_v:.1f})"
    elif stack_bear:
        implied      = "Bearish"
        macd_aligned = macd_bear
        rsi_ok       = 25 < rsi_v < 70
        macd_label   = f"need MACD < Signal (MACD {macd_v:.3f} {'<' if macd_bear else '>'} Signal {sig_v:.3f})"
        rsi_label    = f"need RSI 25–70 (RSI {rsi_v:.1f})"
    else:
        implied      = None
        macd_aligned = False
        rsi_ok       = False
        macd_label   = f"EMA stack must align first (MACD {macd_v:.3f} vs Signal {sig_v:.3f})"
        rsi_label    = f"EMA stack must align first (RSI {rsi_v:.1f})"

    all_base = (stack_bull or stack_bear) and macd_aligned and rsi_ok and vol_floor

    # ── Base condition summary ──
    st.markdown(f"**Implied direction: {'🟢 ' + implied if implied else '⚪ Mixed/No trend'}**")
    st.caption(f"{chk(stack_bull or stack_bear)} Trend stack — "
               f"Price \\${latest_price:,.2f} / EMA20 \\${ema20_v:,.2f} / EMA50 \\${ema50_v:,.2f}")
    st.caption(f"{chk(macd_aligned)} MACD — {macd_label}")
    st.caption(f"{chk(rsi_ok)} RSI band — {rsi_label}")
    st.caption(f"{chk(vol_floor)} Volume floor — {vol_ratio:.2f}× avg (need ≥ 0.70×)")

    if not all_base:
        st.caption("MACD lagging an EMA stack is the most common miss — usually resolves within 1–3 bars.")
        return

    # ── Base conditions ALL passed ──
    # NOTE: this function now covers BASE CONDITIONS ONLY. The 4 enhancement
    # filters are rendered separately by the caller (Signal Filters tab, §2)
    # so we don't duplicate the same scorecard in two places.
    block_reason = diag.get("block_reason") if diag else None
    filters      = diag.get("filters", {}) if diag else {}

    st.success("✅ All base conditions passed.")

    if block_reason == "rr":
        st.warning(
            f"…but blocked by **Reward:Risk** — calculated R:R is "
            f"**{diag.get('rr')}**, below your **{MIN_RR}** minimum. "
            f"See the 💼 Swing Trade tab for the proposed levels."
        )
    elif block_reason == "zero_risk":
        st.warning(
            f"…but blocked — **stop is too tight to be tradeable**. "
            f"Risk is only **\\${diag.get('risk', 0):,.2f}** vs a minimum of "
            f"**\\${diag.get('min_risk', 0):,.2f}** (0.3% of price). A stop this "
            f"close would be hit by normal intraday noise."
        )
    elif filters:
        failed = [n for n, f in filters.items() if not f["pass"]]
        if failed:
            st.warning(
                f"…but blocked by **{len(failed)} enhancement filter(s)**: "
                f"{', '.join(failed)} — details in §2 below."
            )


def render_price_chart(df: pd.DataFrame, ticker: str):
    """FIX #2: candlestick-style line chart with EMA20/50 overlay."""
    chart_df = df.tail(60)[["Close","EMA20","EMA50"]].copy()
    chart_df.columns = ["Close", "EMA 20", "EMA 50"]
    st.line_chart(chart_df, height=220, width="stretch")
    st.caption(f"{ticker} — Close price with EMA 20 & EMA 50 (last 60 bars)")


# ─────────────────────────────────────────────
# MARKET STATUS + REGIME BANNER
# Both fetched lazily (inside a cached wrapper)
# so they don't fire at module level on every
# Streamlit rerun / startup health check.
# ─────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def get_market_context() -> dict:
    """Single lazy call for market open + SPY regime — cached 5 min."""
    return {
        "market_open": is_market_open(),
        "spy_regime":  get_spy_regime(),
    }

# ─────────────────────────────────────────────
# TOP-LEVEL TABS
# ─────────────────────────────────────────────
(TAB_SCAN, TAB_STOCK, TAB_CHECK, TAB_POSITIONS,
 TAB_ALERTS, TAB_JOURNAL) = st.tabs([
    "📡 Watchlist Scan", "🔍 Stock Analysis", "✅ Contract Check", "📍 Positions",
    "🔔 Alert History", "📓 Trade Journal",
])


# ═══════════════════════════════════════════════
# TAB 1 — WATCHLIST SCAN
# ═══════════════════════════════════════════════
with TAB_SCAN:
    # ── Lazy market context (not fetched at module level) ──
    ctx         = get_market_context()
    market_open = ctx["market_open"]
    spy_regime  = ctx["spy_regime"]

    # ── Market status + regime banner ──
    col_status, col_regime = st.columns([1, 2])
    with col_status:
        if market_open:
            st.success("🟢 Market OPEN")
        else:
            st.warning("🔴 Market CLOSED")
    with col_regime:
        regime       = spy_regime.get("regime", "Unknown")
        regime_color = {"Bull":"🟢","Bear":"🔴","Neutral":"🟡"}.get(regime, "⚪")
        st.info(f"{regime_color} **Macro Regime: {regime}** — {spy_regime.get('reasoning','')}")

    st.divider()

    # FIX cause 2: scan is GATED behind a button — no auto-run on startup.
    # Streamlit Cloud sends a healthz probe immediately after startup;
    # if the app tries to fetch 5 tickers + options chains before responding,
    # it segfaults. Button click is required for first scan.
    sc1, sc2 = st.columns([3, 1])
    with sc1:
        st.caption(f"Tickers: {', '.join(SCAN_LIST)} · Cache: 5 min · Sorted by R:R ↓")
    with sc2:
        if st.button("🔄 Run / Refresh Scan", type="primary", key="refresh_scan"):
            st.session_state["scan_triggered"] = True
            run_watchlist_scan.clear()
            st.rerun()

    if "scan_triggered" not in st.session_state:
        st.session_state["scan_triggered"] = False

    regime_key = spy_regime.get("regime", "Unknown")

    if not st.session_state["scan_triggered"]:
        st.info("👆 Click **Run / Refresh Scan** to scan the watchlist.")
    else:
        with st.spinner("Scanning watchlist…"):
            all_setups = run_watchlist_scan(tuple(SCAN_LIST), regime_key,
                                            get_settings_key())

        # Defensive: only keep well-formed setup dicts (must have "ticker").
        # analyze() returns diagnostic dicts with "blocked": True for failed
        # setups — those lack display keys and must never reach the UI.
        all_setups = [s for s in all_setups if isinstance(s, dict) and "ticker" in s]

        # Store in session_state so other tabs can reuse the last scan
        # can safely read it without a NameError when no scan has run yet.
        st.session_state["all_setups"] = all_setups

        high_quality = [s for s in all_setups if s["high_quality"]]
        partial      = [s for s in all_setups
                        if not s.get("high_quality") and s.get("all_pass", True)]
        weak         = [s for s in all_setups if not s.get("all_pass", True)]

        for a in high_quality:
            alert_created = log_alert(
                ticker=a["ticker"], trend=a["trend"], strength=a["strength"],
                entry=a["entry"], stop=a["stop"], target=a["target"],
                rr=a["rr"], price=a["price"], filters_passed=a["filters"])
            # Only notify when a NEW alert was actually recorded. Without this
            # check the cooldown silenced the journal but not the phone.
            if alert_created and market_open:
                fs = " | ".join(f"{'✅' if f['pass'] else '❌'} {n}"
                                for n,f in a["filters"].items())
                send_telegram_alert(a["ticker"], (
                    f"🚨 HIGH QUALITY ({a['filters_pass']}/{a['filters_total']} filters)\n"
                    f"{a['ticker']} → {a['trend']} ({a['strength']})\n"
                    f"Price: {a['price']} | RR: {a['rr']} | ADX: {a['adx']}\n"
                    f"Entry: {a['entry']} | Stop: {a['stop']} | Target: {a['target']}\n{fs}"
                ))

        c1,c2,c3 = st.columns(3)
        c1.metric("🔥 High Quality",  len(high_quality))
        c2.metric("✅ All Filters",   len(partial))
        c3.metric("⚠️ Partial Setup", len(weak))
        st.divider()

        st.markdown("### 🔥 High-Quality Setups")
        if high_quality:
            for a in high_quality:
                with st.container(border=True):
                    h1,h2,h3,h4,h5 = st.columns(5)
                    h1.metric("Ticker",  a["ticker"])
                    h2.metric("Trend",   f"{a['trend']} ({a['strength']})")
                    h3.metric("R:R",     a["rr"])
                    h4.metric("ADX",     a["adx"])
                    h5.metric("Filters", f"{a['filters_pass']}/{a['filters_total']}")
                    st.caption(f"Entry {a['entry']} · Stop {a['stop']} · Target {a['target']} · RSI {a['rsi']}")
                    # Pass the option mid when the chain resolved, so contract
                    # sizing is based on PREMIUM (true max loss on a debit
                    # option) rather than on the stock stop distance.
                    _opt = a.get("option") or {}
                    _prem = _opt.get("mid") if not _opt.get("error") else None
                    ps = calc_position_size(a["entry"], a["stop"], ACCOUNT_SIZE,
                                            RISK_PCT, option_premium=_prem)
                    if ps["affordable"]:
                        st.caption(
                            f"💰 Position sizing — Risk \\${ps['risk_dollars']:,.2f} · "
                            f"**{ps['shares']} shares** or **{ps['contracts']} contract(s)** "
                            f"at \\${ps['cost_per_contract']:,.2f} each "
                            f"(\\${ACCOUNT_SIZE:,} acct · {RISK_PCT}% risk)"
                        )
                    elif ps.get("option_known"):
                        st.caption(
                            f"💰 Position sizing — Risk \\${ps['risk_dollars']:,.2f} · "
                            f"**{ps['shares']} shares** · ⚠️ **0 contracts** "
                            f"(1 contract costs \\${ps['cost_per_contract']:,.2f}; "
                            f"your limit affords up to \\${ps['max_premium']:,.2f}/share)"
                        )
                    else:
                        st.caption(
                            f"💰 Position sizing — Risk \\${ps['risk_dollars']:,.2f} · "
                            f"**{ps['shares']} shares** · contracts n/a (no chain data)"
                        )
        else:
            st.info("No high-quality setups right now — all 4 filters must pass.")

        st.markdown("### ✅ Valid Setups")
        if partial:
            for a in partial:
                with st.container(border=True):
                    p1,p2,p3,p4 = st.columns(4)
                    p1.write(f"**{a['ticker']}**")
                    p2.write(a["trend"])
                    p3.write(f"RR {a['rr']}")
                    p4.write(f"ADX {a['adx']} · RSI {a['rsi']}")
        else:
            st.info("No additional valid setups")

        with st.expander(f"⚠️ Partial / failed signals ({len(weak)} tickers)"):
            for a in weak:
                failed = [n for n,f in a["filters"].items() if not f["pass"]]
                st.write(f"**{a['ticker']}** — {a['trend']} | RR {a['rr']} | Failed: {', '.join(failed)}")


# ═══════════════════════════════════════════════
# TAB 2 — SINGLE STOCK ANALYSIS
# ═══════════════════════════════════════════════
with TAB_STOCK:
    st.subheader("🔍 Single Stock Analysis")
    # Get spy_regime lazily (already cached from TAB_SCAN or fetches fresh)
    _ctx_stock = get_market_context()
    spy_regime  = _ctx_stock["spy_regime"]
    query = st.text_input("Enter ticker (e.g. TSLA, NVDA, AAPL)", placeholder="TSLA", key="ticker_input")

    if query:
        ticker = query.strip().upper()
        with st.spinner(f"Fetching {ticker}…"):
            df, fetch_error = get_data_with_error(ticker)
            intraday = get_data(ticker, period="5d", interval="5m", min_rows=MIN_ROWS)

        if df is None:
            st.error(f"❌ {fetch_error or f'Could not load data for {ticker}'}")
            if fetch_error and "Rate limited" in fetch_error:
                st.caption("Data is cached 10 min once loaded — only affects fresh lookups.")
        else:
            df = compute(df)
            df, dropped_partial = drop_partial_bar(df)
            if not has_sufficient_history(df, ticker):
                st.error(
                    f"❌ **{ticker}** — not enough price history for reliable "
                    f"indicators. Only {len(df)} usable bars after warm-up "
                    f"(need {MIN_BARS_AFTER_WARMUP}+). Newly-listed tickers and "
                    f"thinly-traded names often fail this check."
                )
                st.caption(
                    "Indicators like EMA50, MACD and ADX need ~100 bars of history "
                    "before their values converge. Trading on unconverged indicators "
                    "produces materially wrong signals."
                )
            else:
                if dropped_partial:
                    st.info(
                        "📊 Market is open — analysing the **last completed daily bar**. "
                        "Today's bar is still forming (its volume is only partial), so "
                        "including it would make the volume filter depend on the time of "
                        "day rather than on actual market activity."
                    )
                latest_price = float(df["Close"].iloc[-1])
                latest_rsi   = float(df["RSI"].iloc[-1])
                latest_atr   = float(df["ATR"].iloc[-1])
                latest_adx   = float(df["ADX"].iloc[-1])
                vol_now      = float(df["Volume"].iloc[-1])
                vol_avg      = float(df["VOL_AVG20"].iloc[-1])

                pc1,pc2,pc3,pc4,pc5 = st.columns(5)
                pc1.metric("Last Price", f"${latest_price:,.2f}")
                pc2.metric("RSI (14)",   f"{latest_rsi:.1f}")
                pc3.metric("ATR (14)",   f"${latest_atr:.2f}")
                pc4.metric("ADX (14)",   f"{latest_adx:.1f}",
                           delta="Trending" if latest_adx>=ADX_MIN else "Choppy",
                           delta_color="normal" if latest_adx>=ADX_MIN else "inverse")
                pc5.metric("Vol vs Avg", f"{vol_now/vol_avg:.2f}×")

                st.divider()
                # FIX #2: price chart always visible
                render_price_chart(df, ticker)
                st.divider()

                latest_bar_key = f"{ticker}_{df.index[-1]}"
                r = analyze(df, ticker, latest_bar_key, get_settings_key(),
                            spy_regime=spy_regime)

                stab1, stab2, stab3, stab4, stab5 = st.tabs([
                    "💼 Swing Trade","🔬 Signal Filters",
                    "🧠 Options","⚡ Intraday Scalp","💸 Budget Options"
                ])

                with stab1:
                    # ── SWING TRADE = the TRADE PLAN (entry/stop/target/size) ──
                    if r.get("blocked"):
                        reason = r.get("block_reason")
                        if reason == "base":
                            st.warning(
                                "⚠️ **No trade plan** — the base signal conditions "
                                "(EMA stack / MACD / RSI / volume) don't align yet."
                            )
                            st.caption(
                                "👉 See the **🔬 Signal Filters** tab for a full "
                                "condition-by-condition breakdown of what's missing."
                            )
                        elif reason == "rr":
                            st.warning(
                                f"⚠️ **Trade plan rejected — poor Reward:Risk** "
                                f"({r.get('rr')} < your {MIN_RR} minimum)"
                            )
                            # Still show the levels — the user may want to override
                            s1,s2,s3,s4 = st.columns(4)
                            s1.metric("Entry",  f"${r.get('entry','—')}")
                            s2.metric("Stop",   f"${r.get('stop','—')}")
                            s3.metric("Target", f"${r.get('target','—')}")
                            s4.metric("R:R",    r.get("rr","—"), delta="below min",
                                      delta_color="inverse")
                            st.caption(
                                "The trend is valid but the levels don't offer enough "
                                "reward for the risk. Lower **Min Reward/Risk** in the "
                                "sidebar to see it, or wait for a better entry."
                            )
                        else:
                            st.warning(f"⚠️ No trade plan — blocked ({reason}).")
                    else:
                        badge = ("🔥 HIGH QUALITY" if r["high_quality"]
                                 else "✅ VALID — all filters pass" if r.get("all_pass")
                                 else f"⚠️ PARTIAL — {r['filters_pass']}/{r['filters_total']} filters pass")
                        st.markdown(f"### {badge} — {r['trend']} ({r['strength']})")
                        s1,s2,s3,s4 = st.columns(4)
                        s1.metric("Entry",  f"${r['entry']}")
                        s2.metric("Stop",   f"${r['stop']}")
                        s3.metric("Target", f"${r['target']}")
                        s4.metric("R:R",    r["rr"])
                        risk_amt   = abs(r["entry"]-r["stop"])
                        reward_amt = abs(r["target"]-r["entry"])
                        st.progress(min(reward_amt/(risk_amt+reward_amt),1.0),
                                    text=f"Reward ${reward_amt:.2f} vs Risk ${risk_amt:.2f}")
                        _opt  = r.get("option") or {}
                        _prem = _opt.get("mid") if not _opt.get("error") else None
                        ps = calc_position_size(r["entry"], r["stop"], ACCOUNT_SIZE,
                                                RISK_PCT, option_premium=_prem)
                        if ps["affordable"]:
                            st.info(
                                f"💰 **Position Sizing** — "
                                f"Risk \\${ps['risk_dollars']:,.2f} ({RISK_PCT}% of \\${ACCOUNT_SIZE:,}) · "
                                f"**{ps['shares']} shares** · **{ps['contracts']} option contract(s)** "
                                f"at \\${ps['cost_per_contract']:,.2f} each "
                                f"(premium × 100 = max loss on a long option)"
                            )
                        elif ps.get("option_known"):
                            st.warning(
                                f"⚠️ **This contract exceeds your risk limit** — "
                                f"Risk budget is \\${ps['risk_dollars']:,.2f} "
                                f"({RISK_PCT}% of \\${ACCOUNT_SIZE:,}).\n\n{ps['note']}"
                            )
                        else:
                            st.info(
                                f"💰 **Position Sizing** — "
                                f"Risk \\${ps['risk_dollars']:,.2f} ({RISK_PCT}% of \\${ACCOUNT_SIZE:,}) · "
                                f"**{ps['shares']} shares**.\n\n{ps['note']}"
                            )
                        st.caption(
                            "⚠️ **Gap risk:** a stop is not a guarantee. Swing positions are held "
                            "overnight and an adverse gap can open *beyond* your stop, making the "
                            "realised loss larger than the planned risk shown above."
                        )
                        if not r.get("all_pass", True):
                            failed = [n for n,f in r["filters"].items() if not f["pass"]]
                            st.caption(
                                f"⚠️ {len(failed)} enhancement filter(s) failing: "
                                f"**{', '.join(failed)}** — see 🔬 Signal Filters tab."
                            )

                with stab2:
                    # ── SIGNAL FILTERS = WHY the signal passed or failed ──
                    st.markdown("### 🔬 Signal Diagnostics")
                    st.caption(
                        "This tab explains **why** a signal did or didn't fire. "
                        "The 💼 Swing Trade tab shows the resulting **trade plan**."
                    )
                    st.divider()

                    # Layer 1 — base conditions (always shown)
                    st.markdown("#### 1️⃣ Base Signal Conditions")
                    render_no_signal_diagnostic(df, latest_price, latest_rsi,
                                                vol_now, vol_avg, diag=r)

                    # Layer 2 — the 4 enhancement filters (only meaningful once base passes)
                    st.divider()
                    st.markdown("#### 2️⃣ Enhancement Filters")
                    if r.get("blocked") and r.get("block_reason") == "base":
                        st.info(
                            "Enhancement filters are only evaluated once the base "
                            "conditions pass. Fix the base conditions above first."
                        )
                    elif r.get("filters"):
                        render_filter_scorecard(r["filters"],
                                                r.get("filters_pass", 0),
                                                r.get("filters_total", 4))
                    else:
                        st.info("No filter results available.")

                    st.divider()
                    st.markdown("**Filter Definitions**")
                    st.caption(f"1. **ADX ≥ {ADX_MIN}** — real trend, not chop/sideways")
                    st.caption("2. **Multi-TF Alignment** — weekly EMA must agree with daily direction")
                    st.caption(f"3. **Earnings Blackout** — blocks within {EARNINGS_DAYS}d of earnings "
                               f"(and {POST_EARNINGS_DAYS}d after)")
                    st.caption("4. **Macro Regime** — no longs in SPY Bear; no shorts in SPY Bull")

                with stab3:
                    if r.get("blocked"):
                        st.warning("Swing trade setup required for options recommendation.")
                    else:
                        opt = r["option"]
                        if "error" in opt:
                            st.error(f"⚠️ {opt['error']}")
                        else:
                            emoji = "📈" if opt["label"]=="CALL" else "📉"
                            st.markdown(f"### {emoji} {opt['label']} — Exp {opt['expiry']} ({opt['dte']} DTE)")
                            o1,o2,o3,o4 = st.columns(4)
                            o1.metric("Strike",    f"${opt['strike']}")
                            o2.metric("Mid Price", f"${opt['mid']}")
                            o3.metric("Volume",    f"{opt['volume']:,}")
                            o4.metric("Open Int.", f"{opt['oi']:,}")
                            spread_pct = (opt["spread"]/opt["mid"]*100) if opt["mid"] else 0
                            st.caption(f"Spread: \\${opt['spread']} ({spread_pct:.1f}% of mid) · Last: \\${opt['last_price']}")

                            # ── DTE adequacy (theta trap warning) ──
                            if not opt.get("dte_adequate", True):
                                st.error(
                                    f"⏳ **Theta risk — expiry may be too short.** This contract has "
                                    f"**{opt['dte']} DTE**, but your target ({ATR_TGT_MULT}× ATR) typically needs "
                                    f"**~{opt.get('days_needed', 8)} sessions** of favourable movement. "
                                    f"The trade thesis may be right and the option still expire worthless. "
                                    f"Raise **Min DTE** in the sidebar to search further out."
                                )
                            else:
                                st.caption(
                                    f"⏳ {opt['dte']} DTE vs ~{opt.get('days_needed', 8)} sessions needed "
                                    f"for the target — adequate time cushion. ✅"
                                )

                            # ── Search-window transparency ──
                            with st.expander("🔍 What was searched"):
                                st.caption(
                                    f"**Expiries:** up to {_OPT_MAX_EXPIRIES} nearest with "
                                    f"DTE ≥ {MIN_DTE} (your sidebar setting)."
                                )
                                if "strike_lo" in opt:
                                    st.caption(
                                        f"**Strikes:** \\${opt['strike_lo']:,.2f} → \\${opt['strike_hi']:,.2f} "
                                        f"(window sized to ±2× ATR around spot, clamped 3–12%; "
                                        f"'{r['strength']}' strength shifts the window slightly ITM)."
                                    )
                                st.caption(
                                    "**Liquidity gates:** bid > 0, volume > 0, open interest > 0, "
                                    "and bid-ask spread ≤ 15% of mid."
                                )
                                st.caption(
                                    "**Ranking:** (volume + OI) × volume-weight ÷ spread-penalty, "
                                    "then scaled down quadratically if DTE is below what the target needs."
                                )

                            if opt["is_budget"]:
                                st.success(f"💸 Budget pick — \\${opt['mid']}/contract (under \\${BUDGET_MAX:.2f})")
                            if not r.get("all_pass", True):
                                st.warning("⚠️ Not all filters pass — trade at your own discretion.")

                            # ── Quick log: Bought / Skip, right from this contract ──
                            #
                            # Everything needed to open a position (ticker, right,
                            # strike, expiry) is already sitting in `opt` above.
                            # Before this, logging a trade meant re-typing all of
                            # that by hand in the Positions tab — friction that
                            # risks a skipped or mistyped log during the 30-trade
                            # test, which is exactly what this test can't afford.
                            # The Positions tab's manual form still exists for
                            # anything seen outside this view (e.g. via Telegram).
                            st.divider()
                            st.markdown("##### Log this contract")
                            _qkey = f"{ticker}_{opt['expiry']}_{opt['strike']}_{opt['label']}"

                            lc1, lc2 = st.columns(2)
                            with lc1:
                                if st.button("✅ Bought this — log position",
                                            key=f"qbuy_btn_{_qkey}",
                                            width="stretch"):
                                    st.session_state[f"qbuy_open_{_qkey}"] = True
                                    st.session_state[f"qskip_open_{_qkey}"] = False
                            with lc2:
                                if st.button("🚫 Skipping this — log reason",
                                            key=f"qskip_btn_{_qkey}",
                                            width="stretch"):
                                    st.session_state[f"qskip_open_{_qkey}"] = True
                                    st.session_state[f"qbuy_open_{_qkey}"] = False

                            if st.session_state.get(f"qbuy_open_{_qkey}"):
                                with st.container(border=True):
                                    st.caption(
                                        f"{ticker} {opt['expiry']} ${opt['strike']:g} "
                                        f"{opt['label']} · app mid was ${opt['mid']:.2f}"
                                    )
                                    bq1, bq2 = st.columns(2)
                                    with bq1:
                                        # No default from opt['mid'] on purpose: the
                                        # 30-trade test is measuring your REAL fill
                                        # cost against the model's assumed spread.
                                        # Defaulting to the mid would let a click-
                                        # through silently corrupt that number.
                                        q_prem = st.number_input(
                                            "Your actual fill premium (per share)",
                                            min_value=0.0, step=0.01, value=0.0,
                                            key=f"qbuy_prem_{_qkey}",
                                            help="Type your real fill, not the app's "
                                                 "quoted mid — that gap IS the data "
                                                 "this test is measuring.")
                                        q_qty = st.number_input(
                                            "Contracts", min_value=1, value=1,
                                            step=1, key=f"qbuy_qty_{_qkey}")
                                    with bq2:
                                        q_tp = st.number_input(
                                            "Take profit +%", min_value=0, value=200,
                                            step=25, key=f"qbuy_tp_{_qkey}")
                                        q_sl = st.number_input(
                                            "Stop loss −%", min_value=0, max_value=100,
                                            value=50, step=10, key=f"qbuy_sl_{_qkey}")
                                        q_dte = st.number_input(
                                            "Time exit at DTE", min_value=0, value=7,
                                            step=1, key=f"qbuy_dte_{_qkey}")
                                        q_hold = int(st.number_input(
                                            "Max hold (sessions)", min_value=0,
                                            value=MAX_HOLD, step=5,
                                            key=f"qbuy_hold_{_qkey}",
                                            help="Trading sessions, not calendar "
                                                 "days. 0 disables."))
                                        q_thesis = st.checkbox(
                                            "Thesis invalidation (EMA20)", value=False,
                                            key=f"qbuy_thesis_{_qkey}",
                                            help="Off by default — the option backtest "
                                                 "showed this rule cutting winners.")
                                    q_notes = st.text_input(
                                        "Notes", key=f"qbuy_notes_{_qkey}")

                                    if q_prem > 0:
                                        cost = q_prem * 100 * q_qty
                                        st.caption(
                                            f"Total cost **\\${cost:,.0f}** — your "
                                            f"maximum loss on this contract "
                                            f"({cost/ACCOUNT_SIZE*100:.1f}% of "
                                            f"\\${ACCOUNT_SIZE:,}).")
                                        if not (q_tp or q_sl or q_dte or q_hold or q_thesis):
                                            st.error("All exit rules are off — the "
                                                     "monitor would never alert on "
                                                     "this position.")
                                        else:
                                            if st.button("📍 Confirm — start monitoring",
                                                        type="primary",
                                                        key=f"qbuy_confirm_{_qkey}"):
                                                open_option_position(
                                                    ticker=ticker, right=opt["label"],
                                                    strike=opt["strike"],
                                                    expiry=opt["expiry"],
                                                    contracts=q_qty,
                                                    entry_premium=q_prem,
                                                    rules={"tp_pct": q_tp, "sl_pct": q_sl,
                                                           "dte_exit": q_dte,
                                                           "max_hold_bars": q_hold,
                                                           "invalidate_ema": q_thesis},
                                                    notes=q_notes,
                                                    entry_features=capture_entry_features(ticker))
                                                st.session_state[f"qbuy_open_{_qkey}"] = False
                                                st.success(
                                                    f"Logged {ticker} {opt['expiry']} "
                                                    f"${opt['strike']:g} {opt['label']} "
                                                    f"— now monitoring.")
                                                st.rerun()
                                    else:
                                        st.caption("Enter your fill premium to continue.")

                            if st.session_state.get(f"qskip_open_{_qkey}"):
                                with st.container(border=True):
                                    st.caption(
                                        f"{ticker} {opt['expiry']} ${opt['strike']:g} "
                                        f"{opt['label']} — logging as skipped"
                                    )
                                    q_reason = st.selectbox("Why skip it?", [
                                        "Contract too expensive for my risk rule",
                                        "Spread too wide",
                                        "Didn't like the chart / my own read",
                                        "Already at max positions",
                                        "Earnings or event risk",
                                        "Missed it / saw too late",
                                        "Daily loss or trade limit reached",
                                        "Other",
                                    ], key=f"qskip_reason_{_qkey}")
                                    q_snotes = st.text_input(
                                        "Notes (optional)", key=f"qskip_notes_{_qkey}")
                                    if st.button("🚫 Confirm — log skip",
                                                key=f"qskip_confirm_{_qkey}"):
                                        log_skipped_signal(
                                            ticker=ticker, trend=r["trend"],
                                            reason=q_reason, notes=q_snotes,
                                            price=r["price"])
                                        st.session_state[f"qskip_open_{_qkey}"] = False
                                        st.success(f"Logged skip: {ticker} — {q_reason}")
                                        st.rerun()

                with stab4:
                    if intraday is None or len(intraday) < 30:
                        st.warning("Not enough intraday bars (need ≥ 30). "
                                   "Try again once the session has more data.")
                    else:
                        intraday = compute(intraday)
                        # NOTE: this `sc` is the scalp RESULT DICT. It used to
                        # shadow `import signal_core as sc` — app.py executes
                        # top-to-bottom at module level, so this assignment
                        # replaced the module for the rest of the script and
                        # _signal_params() then failed with "'dict' object has
                        # no attribute 'SignalParams'". The module is now
                        # imported under its full name; keep it that way.
                        sc = scalp(intraday)
                        if sc["direction"] is None:
                            st.info(f"ℹ️ {sc['signal']}")
                        else:
                            arrow = "↑" if sc["direction"]=="Long" else "↓"
                            st.markdown(f"### ⚡ {sc['signal']} {arrow}")
                            sc1,sc2 = st.columns(2)
                            sc1.metric("Scalp Stop",   f"${sc.get('stop','N/A')}")
                            sc2.metric("Scalp Target", f"${sc.get('target','N/A')}")
                            st.caption("Scalp targets are intraday — tight stops, monitor closely.")

                with stab5:
                    st.markdown(f"### 💸 Options under ${BUDGET_MAX:.2f}/contract")
                    if r.get("blocked"):
                        st.warning("A valid swing setup is needed.")
                    else:
                        opt = r["option"]
                        if "error" in opt:
                            st.error(f"⚠️ {opt['error']}")
                        elif opt["is_budget"]:
                            st.success(
                                f"✅ **{opt['label']}** · Strike \\${opt['strike']} · "
                                f"Exp {opt['expiry']} ({opt['dte']} DTE) · "
                                f"Mid **\\${opt['mid']}** · Vol {opt['volume']:,} · OI {opt['oi']:,}"
                            )
                            st.caption("Budget options carry higher gamma risk — size accordingly.")
                        else:
                            st.info(f"Best contract is \\${opt['mid']}/contract — above \\${BUDGET_MAX:.2f}. "
                                    "Try a wider strike or longer expiry.")

                st.divider()
                st.caption("⚠️ Not financial advice. Rule-based signals only.")


# ═══════════════════════════════════════════════
# TAB — OPEN POSITIONS  (feeds the exit monitor)
# ═══════════════════════════════════════════════
with TAB_POSITIONS:
    st.subheader("📍 Open Option Positions")
    st.caption("Log the contracts you bought. `exit_monitor.py` watches them on "
               "a schedule and Telegrams you when an exit rule fires.")

    positions = load_positions()

    with st.expander("⚙️ How exit alerts work — read once", expanded=not positions):
        st.markdown(
            "**A Streamlit app only runs while a browser tab is open**, and "
            "Streamlit Cloud sleeps idle apps. This app therefore *cannot* "
            "reliably check exits in the background — if your phone locks, "
            "checks stop. An alert you might not receive is worse than none, "
            "because you'd stop watching manually."
        )
        st.markdown(
            f"`exit_monitor.py` runs standalone on a scheduler (GitHub Actions "
            f"cron is free), reads `open_positions.json`, and alerts you "
            f"independently of this app. Interval: **{EXIT_CHECK_MINUTES} min** "
            f"during market hours."
        )
        st.markdown("**The four exit triggers, checked in this order:**")
        st.markdown(
            "1. **STOP** — premium fell to −X% of what you paid *(risk first)*\n"
            "2. **TARGET** — premium rose to +Y%\n"
            "3. **TIME** — DTE hit your floor; theta decay accelerates sharply "
            "in the final weeks and this is the rule most often skipped\n"
            "4. **THESIS** — the underlying closed the wrong side of its EMA20, "
            "so the reason you bought the contract is gone"
        )
        st.info(
            "**Limitation, stated plainly:** premium checks use the current "
            "quoted mid, not intraday bars — reliable per-contract intraday "
            "history isn't available from this data source. A spike that hit "
            "your target and reverted *between* two checks can be missed. "
            "Shorter intervals reduce but never eliminate this. Option quotes "
            "are also delayed and can be wide or stale."
        )
        st.code(f"python exit_monitor.py --dry-run --force   # safe test\n"
                f"python exit_monitor.py --interval {EXIT_CHECK_MINUTES}",
                language="bash")

    # ── Exits the monitor has flagged ──
    signalled = [p for p in positions if p.get("status") == "EXIT_SIGNALLED"]
    if signalled:
        st.markdown("### 🔔 Exit signalled — close these")
        for p in signalled:
            icon = {"TARGET":"🎯","STOP":"🛑","TIME":"⏳","THESIS":"📉"}.get(
                p.get("exit_reason"), "⚠️")
            with st.container(border=True):
                st.markdown(
                    f"{icon} **{p['ticker']} {p['expiry']} "
                    f"\\${p['strike']:g} {p['right']}** — **{p.get('exit_reason')}**"
                )
                st.caption(p.get("exit_detail", ""))
                if p.get("exit_premium") is not None:
                    st.caption(f"Premium \\${p['entry_premium']:.2f} → "
                               f"\\${p['exit_premium']:.2f} "
                               f"({p.get('exit_pnl_pct', 0):+.1f}%) · "
                               f"detected {p.get('exit_detected','')}")
                fill = st.number_input(
                    "Your actual fill premium (per share)", min_value=0.0, step=0.01,
                    value=float(p.get("exit_premium") or p["entry_premium"]),
                    key=f"fill_{p['id']}")
                pnl = (fill - p["entry_premium"]) * 100 * float(p.get("contracts") or 0)
                st.caption(f"P&L at that fill: **\\${pnl:+,.0f}** "
                           f"on {p.get('contracts')} contract(s)")
                default_oc = 0 if p.get("exit_reason") == "TARGET" else 1
                oc = st.radio("Outcome", ["WIN","LOSS","BREAKEVEN"], horizontal=True,
                              index=default_oc, key=f"oc_{p['id']}")
                nt = st.text_input("Notes", key=f"nt_{p['id']}")
                if st.button("📓 Close & log to journal", type="primary",
                             key=f"close_{p['id']}"):
                    close_position(p["id"], fill, oc, nt)
                    st.success(f"{p['ticker']} closed and logged.")
                    st.rerun()
        st.divider()

    # ── Currently open ──
    live = [p for p in positions if p.get("status") == "OPEN"]
    st.markdown(f"### Currently open ({len(live)})")
    if not live:
        st.info("No open positions. Log a contract below so the monitor can watch it.")
    else:
        today = datetime.now(pytz.timezone("America/New_York")).date()
        for p in live:
            with st.container(border=True):
                try:
                    dte = (datetime.strptime(p["expiry"], "%Y-%m-%d").date() - today).days
                except Exception:
                    dte = None
                c1,c2,c3,c4,c5 = st.columns(5)
                c1.metric("Contract", f"{p['ticker']} {p['right']}")
                c2.metric("Strike",   f"${p['strike']:g}")
                c3.metric("Expiry",   p["expiry"])
                c4.metric("DTE",      dte if dte is not None else "—")
                c5.metric("Paid",     f"${p['entry_premium']:.2f}")
                r = p.get("rules", {})
                bits = []
                if r.get("tp_pct"):        bits.append(f"TP +{r['tp_pct']:g}%")
                if r.get("sl_pct"):        bits.append(f"SL −{r['sl_pct']:g}%")
                if r.get("dte_exit"):      bits.append(f"TIME ≤{r['dte_exit']:g} DTE")
                if r.get("max_hold_bars"): bits.append(f"HOLD ≤{r['max_hold_bars']:g} sessions")
                if r.get("invalidate_ema"):bits.append("THESIS EMA20")
                st.caption(f"{p.get('contracts')} contract(s) · cost "
                           f"\\${p['entry_premium']*100*float(p.get('contracts') or 0):,.0f} · "
                           f"opened {p['opened']}")
                st.caption("Exit rules: " + (" · ".join(bits) if bits else
                           "⚠️ none set — the monitor will never alert on this position"))
                if p.get("notes"):
                    st.caption(f"📝 {p['notes']}")
                if dte is not None and r.get("dte_exit") and dte <= r["dte_exit"] + 3:
                    st.warning(f"⏳ {dte} DTE — approaching your time-exit floor "
                               f"of {r['dte_exit']:g}.")
                if st.button("Close manually", key=f"man_{p['id']}"):
                    st.session_state[f"closing_{p['id']}"] = True
                if st.session_state.get(f"closing_{p['id']}"):
                    mf = st.number_input("Exit fill premium (per share)",
                                         min_value=0.0, step=0.01,
                                         value=float(p["entry_premium"]),
                                         key=f"mf_{p['id']}")
                    mo = st.radio("Outcome", ["WIN","LOSS","BREAKEVEN"],
                                  horizontal=True, key=f"mo_{p['id']}")
                    mn = st.text_input("Notes", key=f"mn_{p['id']}")
                    if st.button("Confirm close", type="primary", key=f"cf_{p['id']}"):
                        close_position(p["id"], mf, mo, mn)
                        st.session_state.pop(f"closing_{p['id']}", None)
                        st.success(f"{p['ticker']} closed and logged.")
                        st.rerun()

    st.divider()

    # ── Log a new option position ──
    st.markdown("### ➕ Log an option contract you bought")
    o1, o2, o3 = st.columns(3)
    with o1:
        o_tkr = st.text_input("Underlying", placeholder="AAPL",
                              key="op_tkr").strip().upper()
        o_right = st.radio("Type", ["CALL","PUT"], horizontal=True, key="op_right")
    with o2:
        o_strike = st.number_input("Strike", min_value=0.0, step=0.5, key="op_strike")
        o_expiry = st.date_input("Expiry", key="op_expiry")
    with o3:
        o_qty = st.number_input("Contracts", min_value=1, value=1, step=1, key="op_qty")
        o_prem = st.number_input("Premium paid (per share)", min_value=0.0,
                                 step=0.01, key="op_prem",
                                 help="The quoted per-share price. One contract "
                                      "costs this × 100.")
    o_notes = st.text_input("Notes / thesis", key="op_notes")

    if o_prem > 0 and o_qty:
        cost = o_prem * 100 * o_qty
        pct_acct = cost / ACCOUNT_SIZE * 100
        msg = (f"Total cost **\\${cost:,.0f}** — that is your **maximum loss** "
               f"on a long option, and **{pct_acct:.1f}%** of your "
               f"\\${ACCOUNT_SIZE:,} account.")
        if pct_acct > RISK_PCT:
            st.warning(f"⚠️ {msg} Your risk setting is {RISK_PCT}% "
                       f"(\\${ACCOUNT_SIZE*RISK_PCT/100:,.0f}). Logging is still "
                       f"allowed — this is a warning, not a block.")
        else:
            st.success(f"✅ {msg}")

    st.markdown("**Exit rules** — the monitor needs at least one of these to alert you.")
    r1, r2, r3, r4 = st.columns(4)
    with r1:
        rule_tp = st.number_input("Take profit +%", min_value=0, value=200, step=25,
                                  key="op_tp",
                                  help="Exit when the premium gains this %. 0 disables.\n\n"
                                       "DEFAULT IS 200. At TP+50/SL-50 the "
                                       "payoff is 1:1, which needs a 50% win rate just to "
                                       "break even — but this entry signal wins ~40% of the "
                                       "time, giving -0.10 expected value per unit risked "
                                       "BEFORE costs. TP+100/SL-50 is 2:1, breakeven at "
                                       "33%, so the same signal turns positive. A "
                                       "trend-following entry needs asymmetric exits; "
                                       "capping winners at +50% throws away the property "
                                       "that makes it work.")
    with r2:
        rule_sl = st.number_input("Stop loss −%", min_value=0, max_value=100, value=50,
                                  step=10, key="op_sl",
                                  help="Exit when the premium loses this %. 0 disables. "
                                       "Max loss on a long option is 100% regardless.\n\n"
                                       "Keep this WELL BELOW your take-profit. The ratio "
                                       "between them is what decides whether you make money: "
                                       "breakeven win rate = SL / (TP + SL).")
    with r3:
        rule_dte = st.number_input("Time exit at DTE", min_value=0, value=7, step=1,
                                   key="op_dte",
                                   help="Exit when days-to-expiry falls to this. 0 "
                                        "disables. Theta decay accelerates sharply "
                                        "in the final weeks.")
        rule_hold = int(st.number_input("Max hold (sessions)", min_value=0,
                                        value=MAX_HOLD, step=5, key="op_hold",
                                        help="Exit after this many TRADING SESSIONS "
                                             "held — weekends and holidays do not "
                                             "count. 0 disables. Mirrors backtest.py "
                                             "--max-hold."))
    with r4:
        rule_thesis = st.checkbox("Thesis invalidation", value=False, key="op_thesis",
                                  help="Exit if the underlying closes below EMA20 "
                                       "(CALL) or above it (PUT) — the setup that "
                                       "justified the trade is gone.")

    # Show the arithmetic the rules imply, so a losing payoff can't be set by accident.
    if rule_tp and rule_sl:
        payoff = rule_tp / rule_sl
        breakeven = rule_sl / (rule_tp + rule_sl) * 100
        # WIN RATE SOURCE — this was previously hardcoded at 40%, taken from the
        # SHARE backtest (buy stock, exit at 3xATR or 1xATR). That number does
        # NOT transfer to options: an option needs a far larger underlying move
        # to gain 100% than a share needs to reach 3xATR, and theta works
        # against you the whole time. option_backtest.py measured the actual
        # option-level win rate at 23.8% (TP+100/SL-50, 5y, 7 tickers).
        # Using 40% here made losing configurations look profitable.
        OPT_WIN_RATE = 0.238
        ev = OPT_WIN_RATE * (rule_tp / 100) - (1 - OPT_WIN_RATE) * (rule_sl / 100)
        line = (f"Payoff **{payoff:.1f}:1** → breakeven win rate **{breakeven:.0f}%**. "
                f"Measured option-level win rate for this signal is "
                f"**{OPT_WIN_RATE*100:.1f}%**, giving expected value "
                f"**{ev:+.2f}** per unit risked — before spread and commissions.")
        if breakeven >= OPT_WIN_RATE * 100:
            st.error("⚠️ " + line + " These rules lose money at the win rate this "
                     "signal actually achieves on OPTIONS. Widen take-profit, or "
                     "accept that this is a data-collection trade rather than a "
                     "positive-expectancy one.")
        elif ev < 0.05:
            st.warning("⚠️ " + line + " Thin — bid-ask spread alone could erase it.")
        else:
            st.success("✅ " + line)
        st.caption(
            "Note: even the best configuration measured (TP+300, thesis off) came "
            "out at −0.27% expectancy. No setting here makes this signal "
            "profitable — the rules limit damage and enforce consistency."
        )

    if not (rule_tp or rule_sl or rule_dte or rule_hold or rule_thesis):
        st.error("All exit rules are off — the monitor would never alert on this "
                 "position. Enable at least one.")
    elif o_tkr and o_strike > 0 and o_prem > 0:
        st.caption(f"Will alert on: "
                   + " · ".join(filter(None, [
                       f"premium +{rule_tp}%" if rule_tp else "",
                       f"premium −{rule_sl}%" if rule_sl else "",
                       f"{rule_dte} DTE" if rule_dte else "",
                       f"{rule_hold} sessions held" if rule_hold else "",
                       "EMA20 invalidation" if rule_thesis else ""])))
        if st.button("📍 Start monitoring this contract", type="primary", key="op_save"):
            open_option_position(
                ticker=o_tkr, right=o_right, strike=o_strike,
                expiry=o_expiry.strftime("%Y-%m-%d"), contracts=o_qty,
                entry_premium=o_prem,
                rules={"tp_pct": rule_tp, "sl_pct": rule_sl,
                       "dte_exit": rule_dte, "max_hold_bars": rule_hold,
                       "invalidate_ema": rule_thesis},
                notes=o_notes,
                entry_features=capture_entry_features(o_tkr))
            st.success(f"Now monitoring {o_tkr} {o_expiry} ${o_strike:g} {o_right}.")
            st.rerun()
    else:
        st.caption("Fill in underlying, strike and premium to begin monitoring.")

    st.divider()

    # ── Signals NOT taken ──
    st.markdown("### 🚫 Log a signal you did NOT take")
    st.caption("Skipped signals are the control group for your 30-trade test. "
               "Without them you only see the outcomes of trades you chose, which "
               "makes any judgment about your own selection look better than it is.")

    sk1, sk2 = st.columns(2)
    with sk1:
        sk_tkr = st.text_input("Ticker", key="sk_tkr").strip().upper()
        sk_dir = st.radio("Signal direction", ["Bullish", "Bearish"],
                          horizontal=True, key="sk_dir")
        sk_px  = st.number_input("Underlying price at signal", min_value=0.0,
                                 step=0.01, key="sk_px")
    with sk2:
        sk_reason = st.selectbox("Why did you skip it?", [
            "Contract too expensive for my risk rule",
            "Spread too wide",
            "Didn't like the chart / my own read",
            "Already at max positions",
            "Earnings or event risk",
            "Missed it / saw too late",
            "Daily loss or trade limit reached",
            "Other",
        ], key="sk_reason")
        sk_notes = st.text_input("Notes (optional)", key="sk_notes")

    if sk_tkr:
        if st.button("🚫 Log skipped signal", key="sk_save"):
            log_skipped_signal(sk_tkr, sk_dir, sk_reason, sk_notes, sk_px)
            st.success(f"Logged skip: {sk_tkr} {sk_dir} — {sk_reason}")
            st.rerun()

    _skips = load_skipped()
    if _skips:
        with st.expander(f"Skipped signals logged ({len(_skips)})"):
            for s in reversed(_skips[-25:]):
                px = f" @ {s['price']}" if s.get("price") else ""
                st.caption(f"**{s['ticker']}** {s['trend']}{px} · {s['date']} — "
                           f"{s['reason']}" + (f" · {s['notes']}" if s.get("notes") else ""))

    st.divider()
    st.caption("⚠️ The monitor reports when a rule was met on delayed quotes. It is "
               "not a broker, places no orders, and your real fill will differ — "
               "especially on wide spreads.")


# ═══════════════════════════════════════════════
# TAB 4 — ALERT HISTORY
# ═══════════════════════════════════════════════
with TAB_ALERTS:
    st.subheader("🔔 Alert History")
    alerts = load_alerts()

    if not alerts:
        st.info("No alerts fired yet. Run the watchlist scan to generate alerts.")
    else:
        total_alerts  = len(alerts)
        journaled_cnt = sum(1 for a in alerts if a.get("journaled"))
        ac1,ac2,ac3 = st.columns(3)
        ac1.metric("Total Alerts",    total_alerts)
        ac2.metric("Journaled",       journaled_cnt)
        ac3.metric("Pending Journal", total_alerts - journaled_cnt)
        st.divider()

        cf1,cf2,cf3 = st.columns(3)
        with cf1:
            ticker_filter = st.selectbox("Ticker",
                ["All"]+sorted(set(a["ticker"] for a in alerts)), key="alert_ticker_filter")
        with cf2:
            trend_filter = st.selectbox("Trend",
                ["All","Bullish","Bearish"], key="alert_trend_filter")
        with cf3:
            journal_filter = st.selectbox("Journal status",
                ["All","Pending","Journaled"], key="alert_journal_filter")

        filtered = alerts
        if ticker_filter  != "All": filtered = [a for a in filtered if a["ticker"]==ticker_filter]
        if trend_filter   != "All": filtered = [a for a in filtered if a["trend"]==trend_filter]
        if journal_filter == "Pending":    filtered = [a for a in filtered if not a.get("journaled")]
        elif journal_filter == "Journaled": filtered = [a for a in filtered if a.get("journaled")]

        st.markdown(f"**{len(filtered)} alert(s) shown**")
        for a in reversed(filtered):
            tb  = "🟢" if a["trend"]=="Bullish" else "🔴"
            jb  = "✅" if a.get("journaled") else "⏳"
            fp  = a.get("filters_passed",{})
            nfp = sum(1 for f in fp.values() if f.get("pass",True)) if fp else "—"
            with st.container(border=True):
                ca,cb,cc,cd,ce,cf = st.columns([1.5,1,1,1.5,1,1])
                ca.markdown(f"**{a['ticker']}** {tb} {a['trend']}")
                cb.markdown(f"RR **{a['rr']}**")
                cc.markdown(f"Filters **{nfp}/4**")
                cd.markdown(f"Entry `{a['entry']}` → Target `{a['target']}`")
                # FIX #6: compact timestamp
                ce.markdown(f"🕒 {short_ts(a['timestamp'])}")
                cf.markdown(f"{jb} {'Logged' if a.get('journaled') else 'Pending'}")

        st.divider()
        if st.button("🗑️ Clear all alert history", type="secondary"):
            save_alerts([]); st.success("Alert history cleared."); st.rerun()


# ═══════════════════════════════════════════════
# TAB 5 — TRADE JOURNAL
# ═══════════════════════════════════════════════
with TAB_JOURNAL:
    st.subheader("📓 Trade Journal — Auto Win/Loss Tracker")

    journal = load_journal()
    alerts  = load_alerts()
    stats   = journal_stats(journal)

    # ── BUG FIX #4: data-safety warning + export/import ──
    with st.expander("⚠️ Data Safety — read this if hosting on Streamlit Cloud", expanded=False):
        if gh_sync.gh_enabled():
            st.success(
                f"🟢 Data is synced to **{gh_sync.GITHUB_REPO}** — it survives container "
                f"restarts and is shared with the exit monitor. Export below is "
                f"still a useful offline backup."
            )
        else:
            st.warning(
                "**No GITHUB_TOKEN set — Streamlit Cloud containers are "
                "stateless.** Data is written to a disk that is wiped on every "
                "redeploy, restart or idle timeout. **Export regularly**, or set "
                "up GitHub sync so nothing is lost."
            )
        ex1, ex2 = st.columns(2)
        with ex1:
            st.markdown("**📥 Export**")
            # BUG FIX: positions were missing from the backup payload, so a
            # lost session had no recovery path for open trades at all.
            backup = {
                "exported_at": datetime.now(pytz.timezone("America/New_York")).isoformat(),
                "journal":     journal,
                "alerts":      alerts,
                "positions":   load_positions(),
                "skipped":     load_skipped(),
            }
            st.download_button(
                "⬇️ Download backup (.json)",
                data=json.dumps(backup, indent=2, default=str),
                file_name=f"trading_copilot_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
                mime="application/json",
                width="stretch",
            )
            st.caption(f"{len(journal)} trades · {len(alerts)} alerts · "
                       f"{len(load_positions())} open position(s)")

        with ex2:
            st.markdown("**📤 Restore**")
            uploaded = st.file_uploader("Upload a backup .json", type=["json"],
                                        key="journal_restore", label_visibility="collapsed")
            if uploaded is not None:
                try:
                    payload = json.load(uploaded)
                    n_j = len(payload.get("journal", []))
                    n_a = len(payload.get("alerts", []))
                    n_p = len(payload.get("positions", []))
                    st.caption(f"Found {n_j} trades · {n_a} alerts · {n_p} position(s)")
                    if st.button("♻️ Restore (overwrites current)", type="primary",
                                 width="stretch", key="do_restore"):
                        save_journal(payload.get("journal", []))
                        save_alerts(payload.get("alerts", []))
                        if "positions" in payload:
                            save_positions(payload.get("positions", []))
                        if "skipped" in payload:
                            save_skipped(payload.get("skipped", []))
                        st.success(f"Restored {n_j} trades, {n_a} alerts, "
                                   f"{n_p} position(s).")
                        st.rerun()
                except Exception as e:
                    st.error(f"Invalid backup file: {e}")

    if stats and stats.get("total"):
        st.markdown("### 📊 Performance Dashboard")
        if stats.get("open"):
            st.caption(
                f"ℹ️ {stats['open']} OPEN trade(s) are excluded from every metric "
                f"below — performance is computed on closed trades only."
            )
        # Sample-size caveat, same discipline oos_validate.py pre-registers
        # (min_trades=60 there, for a statistical significance test across
        # many tickers). This is a much smaller, informal floor for a single
        # live journal — not a formal test, just the same honesty: a handful
        # of closed trades is not evidence the signal works or doesn't, and
        # eyeballing it is exactly how the August parameter sweep found a
        # false edge in the first place (see signal_core.py / oos_validate.py).
        if stats["total"] < MIN_JOURNAL_TRADES_FOR_SIGNAL:
            st.caption(
                f"⚠️ Only {stats['total']} closed trade(s) — win rate, profit "
                f"factor and total R below are noise at this sample size, not "
                f"a verdict on the strategy. The 591-trade out-of-sample test "
                f"(see oos_validate.py) is the actual evidence on the signal; "
                f"this dashboard is your execution log, not a replacement "
                f"for it. Treat these numbers as informative again past "
                f"~{MIN_JOURNAL_TRADES_FOR_SIGNAL} closed trades.")
        m1,m2,m3,m4,m5,m6 = st.columns(6)
        m1.metric("Closed Trades", stats["total"])
        m2.metric("Win Rate",      f"{stats['win_rate']}%")
        m3.metric("Wins/Losses",   f"{stats['wins']} / {stats['losses']}")
        m4.metric("Avg Win (R)",   stats["avg_win_r"])
        pf_disp = "∞" if stats["profit_factor"]==float("inf") else stats["profit_factor"]
        m5.metric("Profit Factor", pf_disp)
        m6.metric("Total R",       stats["total_r"])
        streak_emoji = "🔥" if stats["streak_type"]=="WIN" else "❄️"
        st.caption(f"{streak_emoji} Current streak: **{stats['streak']} {stats['streak_type']}** in a row")

        # FIX #5: equity curve chart
        eq_data = stats.get("equity_curve",[])
        if len(eq_data) > 1:
            eq_df = pd.DataFrame(eq_data).set_index("date")
            st.line_chart(eq_df, height=200, width="stretch")
            st.caption("Cumulative R over time — rising = consistent edge · steep drop = drawdown period to review")

        st.divider()

    unjournaled = [a for a in alerts if not a.get("journaled")]
    st.markdown("### ➕ Log Trade Outcome")

    if not unjournaled:
        st.info("No pending alerts to journal. Alerts appear here automatically from the scan.")
    else:
        labels = [f"{a['ticker']} | {a['trend']} | Entry {a['entry']} | {short_ts(a['timestamp'])}"
                  for a in unjournaled]
        selected_label = st.selectbox("Select alert to journal", options=labels, key="journal_select")
        sel = unjournaled[labels.index(selected_label)]

        with st.container(border=True):
            st.markdown(
                f"**{sel['ticker']}** · {sel['trend']} ({sel['strength']}) · "
                f"Entry `{sel['entry']}` · Stop `{sel['stop']}` · Target `{sel['target']}` · "
                f"R:R `{sel['rr']}` · {short_ts(sel['timestamp'])}"
            )
            jc1,jc2 = st.columns(2)
            with jc1:
                exit_price = st.number_input("Exit Price ($)", min_value=0.01,
                    value=float(sel["entry"]), step=0.01, key="exit_price_input")
                outcome = st.radio("Outcome", ["WIN","LOSS","BREAKEVEN"],
                    horizontal=True, key="outcome_radio")
            with jc2:
                notes = st.text_area("Notes (setup, mistakes, lessons)",
                    placeholder="e.g. Held through news, stopped out early…",
                    key="journal_notes", height=100)

            risk = abs(sel["entry"]-sel["stop"])
            if risk > 0:
                preview_r = round((exit_price-sel["entry"])/risk, 2) \
                            if sel["trend"]=="Bullish" \
                            else round((sel["entry"]-exit_price)/risk, 2)
                color = "green" if preview_r>0 else "red"
                st.markdown(f"**Actual R: :{color}[{preview_r}R]**")

            if st.button("💾 Save to Journal", type="primary", key="save_journal_btn"):
                add_journal_trade(alert_id=sel["id"], ticker=sel["ticker"], trend=sel["trend"],
                    entry=sel["entry"], stop=sel["stop"], target=sel["target"],
                    rr=sel["rr"], exit_price=exit_price, outcome=outcome,
                    notes=notes, setup_date=sel["timestamp"])
                st.success(f"✅ {sel['ticker']} → {outcome} logged")
                st.rerun()

    st.divider()
    st.markdown("### 📋 Trade History")

    if not journal:
        st.info("No trades logged yet.")
    else:
        jf1,jf2,jf3 = st.columns(3)
        with jf1:
            j_ticker = st.selectbox("Ticker",
                ["All"]+sorted(set(j["ticker"] for j in journal)), key="j_ticker_filter")
        with jf2:
            j_outcome = st.selectbox("Outcome",
                ["All","WIN","LOSS","BREAKEVEN"], key="j_outcome_filter")
        with jf3:
            j_trend = st.selectbox("Direction",
                ["All","Bullish","Bearish"], key="j_trend_filter")

        # Cross-app safety: skip OPEN trades (restored from the discipline-
        # enforcer app's backups) — they have no exit_price/closed to show.
        filtered_j = [j for j in journal
                      if j.get("outcome") in ("WIN", "LOSS", "BREAKEVEN")]
        if j_ticker  != "All": filtered_j=[j for j in filtered_j if j["ticker"]==j_ticker]
        if j_outcome != "All": filtered_j=[j for j in filtered_j if j["outcome"]==j_outcome]
        if j_trend   != "All": filtered_j=[j for j in filtered_j if j["trend"]==j_trend]

        for j in reversed(filtered_j):
            oe = {"WIN":"✅","LOSS":"❌","BREAKEVEN":"➖"}.get(j["outcome"],"❓")
            rc = "🟢" if j["actual_rr"]>0 else ("🔴" if j["actual_rr"]<0 else "⚪")
            with st.expander(
                f"{oe} {j['ticker']} · {j['trend']} · Actual: {rc} {j['actual_rr']}R · {short_ts(j['closed'])}"
            ):
                d1,d2,d3,d4 = st.columns(4)
                d1.metric("Entry",       f"${j['entry']}")
                d2.metric("Exit",        f"${j['exit_price']}")
                d3.metric("Planned R:R", j["planned_rr"])
                d4.metric("Actual R",    j["actual_rr"])
                st.caption(f"Stop: \\${j['stop']} · Target: \\${j['target']} · Alerted: {short_ts(j['date'])}")
                if j.get("notes"):
                    st.markdown(f"📝 *{j['notes']}*")
                if st.button("🗑️ Delete", key=f"del_{j['id']}", type="secondary"):
                    save_journal([x for x in journal if x["id"]!=j["id"]])
                    al = load_alerts()
                    for a in al:
                        if a["id"]==j["id"]: a["journaled"]=False
                    save_alerts(al)
                    st.rerun()

        st.divider()
        if st.button("🗑️ Clear entire journal", type="secondary", key="clear_journal"):
            save_journal([])
            al = load_alerts()
            for a in al: a["journaled"]=False
            save_alerts(al)
            st.success("Journal cleared.")
            st.rerun()

    st.caption("⚠️ Not financial advice. Journal is for personal tracking only.")


# ═══════════════════════════════════════════════
# TAB — CONTRACT CHECK
# ═══════════════════════════════════════════════
with TAB_CHECK:
    st.subheader("Contract Check")
    st.caption(
        "Check a contract you picked yourself against the same rules the "
        "scanner applies. This answers *does this meet my criteria* — not "
        "*is this a good trade*. Nothing is logged unless you choose to log it."
    )

    cc1, cc2, cc3 = st.columns([2, 1, 1])
    with cc1:
        chk_ticker = st.text_input("Ticker", key="chk_ticker",
                                   placeholder="NVDA").strip().upper()
    with cc2:
        chk_right = st.selectbox("Type", ["CALL", "PUT"], key="chk_right")
    with cc3:
        chk_strike = st.number_input("Strike", min_value=0.0, step=1.0,
                                     value=0.0, key="chk_strike")

    cc4, cc5 = st.columns(2)
    with cc4:
        # Plain calendar by design. Looking up the real expiry list cost a
        # Yahoo call (up to four with retries) purely to validate a date the
        # user already knows is correct — and it was the slowest thing on the
        # tab. If the date is not a real expiry the chain fetch below says so
        # anyway, at no extra cost.
        chk_expiry = st.date_input(
            "Expiry", value=_date.today() + _timedelta(days=max(MIN_DTE, 21)),
            min_value=_date.today() + _timedelta(days=1),
            key="chk_expiry_date",
            help="Pick the contract's expiry date. Not validated against the "
                 "listed chain up front — if the date is wrong the check "
                 "below will tell you.").strftime("%Y-%m-%d")
    with cc5:
        chk_premium = st.number_input(
            "Entry premium (per share)", min_value=0.0, step=0.05, value=0.0,
            key="chk_premium",
            help="What you paid, or would pay. Compared against the current "
                 "mid — a fill far from mid changes the trade's maths even "
                 "when every rule passes.")

    if st.button("Check contract", type="primary", key="chk_run"):
        if not chk_ticker or chk_strike <= 0 or not chk_expiry:
            st.warning("Ticker, strike and expiry are all required.")
        else:
            with st.spinner(f"Checking {chk_ticker} {chk_expiry} "
                            f"${chk_strike:g} {chk_right}..."):
                st.session_state["chk_result"] = check_manual_contract(
                    chk_ticker, chk_right, chk_strike, chk_expiry, chk_premium)

    res = st.session_state.get("chk_result")
    if res:
        if res.get("error"):
            st.error(res["error"])
        else:
            label = (f"{res['ticker']} {res['expiry']} "
                     f"${res['strike']:g} {res['right']}")
            if res["passed"]:
                st.success(f"PASS — {label} meets all "
                           f"{res['n_total']} rules")
                st.caption(
                    "This means the contract matches your stated criteria. It "
                    "is not a prediction: the 591-trade out-of-sample test "
                    "found no measurable edge in this entry logic, so a pass "
                    "carries no expectancy claim."
                )
            else:
                st.error(f"FAIL — {label} misses "
                         f"{len(res['failures'])} of {res['n_total']} rules")
                st.caption("Failed: " + ", ".join(res["failures"]))

            st.markdown("---")
            for c in res["checks"]:
                icon = "✅" if c["pass"] else "❌"
                suffix = "" if c["blocking"] else "  *(informational)*"
                css = "filter-pass" if c["pass"] else "filter-fail"
                st.markdown(
                    f'<div class="{css}">{icon} <b>{c["name"]}</b> — '
                    f'{c["detail"]}{suffix}</div>', unsafe_allow_html=True)

            ct = res.get("contract")
            if ct:
                st.markdown("---")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Mid", f"${ct['mid']:.2f}")
                m2.metric("Spread", f"{ct['spread_pct']:.1f}%")
                m3.metric("Vol / OI", f"{ct['volume']} / {ct['oi']}")
                m4.metric("DTE", ct["dte"])

            st.markdown("---")
            if res["passed"]:
                st.markdown("**Log this position?**")
                lg1, lg2, lg3, lg4 = st.columns(4)
                with lg1:
                    chk_contracts = int(st.number_input(
                        "Contracts", min_value=1, value=1, step=1,
                        key="chk_contracts"))
                with lg2:
                    chk_tp = st.number_input("Take profit %", min_value=0,
                                             value=200, step=25, key="chk_tp")
                with lg3:
                    chk_sl = st.number_input("Stop loss %", min_value=0,
                                             value=50, step=5, key="chk_sl")
                with lg4:
                    chk_dte_exit = st.number_input("Exit at DTE", min_value=0,
                                                   value=7, step=1,
                                                   key="chk_dte_exit")
                chk_hold = int(st.number_input(
                    "Max hold (sessions)", min_value=0, value=MAX_HOLD, step=5,
                    key="chk_hold"))
                chk_notes = st.text_input(
                    "Notes", key="chk_notes",
                    placeholder="DAY if this is a day trade — see the journal "
                                "convention")

                if st.button("Log position", key="chk_log"):
                    if not (chk_tp or chk_sl or chk_dte_exit or chk_hold):
                        st.warning("At least one exit rule is required — a "
                                   "position with no exit rule is never "
                                   "monitored.")
                    else:
                        try:
                            open_option_position(
                                ticker=res["ticker"], expiry=res["expiry"],
                                strike=res["strike"], right=res["right"],
                                entry_premium=res["entry_premium"] or
                                              (ct["mid"] if ct else 0.0),
                                contracts=chk_contracts,
                                rules={"tp_pct": chk_tp, "sl_pct": chk_sl,
                                       "dte_exit": chk_dte_exit,
                                       "max_hold_bars": chk_hold,
                                       "invalidate_ema": False},
                                notes=chk_notes,
                                entry_features=capture_entry_features(res["ticker"]))
                            st.success("Logged. The exit monitor will track it "
                                       "from the next run.")
                            st.session_state.pop("chk_result", None)
                        except Exception as e:
                            st.error(f"Could not log position: {e}")
            else:
                st.info(
                    "Not logging a failed check. If you take this trade "
                    "anyway, that is your call — but it is worth noticing "
                    "that you overrode your own rules, because that is the "
                    "kind of thing a journal is for."
                )
