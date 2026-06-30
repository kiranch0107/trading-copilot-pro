import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import os
import json
import requests
import time
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import pytz

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Trading Copilot ELITE",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; }
    .stAlert { border-radius: 8px; }
    div[data-testid="metric-container"] {
        background: #1e1e2e;
        border: 1px solid #333;
        border-radius: 8px;
        padding: 12px;
    }
    .filter-pass  { background:#0d2b1a; border-left:3px solid #22c55e; padding:6px 10px; border-radius:5px; margin:3px 0; font-size:0.85em; }
    .filter-fail  { background:#2b0d0d; border-left:3px solid #ef4444; padding:6px 10px; border-radius:5px; margin:3px 0; font-size:0.85em; }
    .filter-warn  { background:#2b2000; border-left:3px solid #f59e0b; padding:6px 10px; border-radius:5px; margin:3px 0; font-size:0.85em; }
</style>
""", unsafe_allow_html=True)

st.title("🤖 Trading Copilot ELITE")
st.caption("Swing · Options · Alerts · Journal · ADX · Multi-TF · Earnings Guard · Regime Filter")

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
WATCHLIST = [
    "TSLA","NVDA","AAPL","MSFT","AMZN",
    "META","SPY"
]
FAST_MODE       = True
SCAN_LIST       = WATCHLIST[:5] if FAST_MODE else WATCHLIST
COOLDOWN        = 600
BUDGET_MAX      = 2.00
MIN_DTE         = 7
MIN_RR          = 1.5
MIN_ROWS        = 50
VOLUME_MULT     = 1.0

# ── Enhancement thresholds ──
ADX_MIN         = 25      # 1. Trend must be strong enough (ADX)
WEEKLY_CONFIRM  = True    # 2. Require weekly EMA alignment
EARNINGS_DAYS   = 3       # 3. Block signals within N days of earnings
SPY_REGIME      = True    # 4. Only trade in confirmed macro regime

ALERT_LOG_FILE  = "alert_history.json"
JOURNAL_FILE    = "trade_journal.json"


# ─────────────────────────────────────────────
# PERSISTENCE HELPERS
# ─────────────────────────────────────────────
def _load(path: str) -> list:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def _save(path: str, data: list) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

def load_alerts() -> list:  return _load(ALERT_LOG_FILE)
def save_alerts(d: list):    _save(ALERT_LOG_FILE, d)
def load_journal() -> list:  return _load(JOURNAL_FILE)
def save_journal(d: list):   _save(JOURNAL_FILE, d)


def log_alert(ticker, trend, strength, entry, stop, target, rr, price,
              filters_passed: dict) -> None:
    alerts = load_alerts()
    recent = [a for a in alerts if a["ticker"] == ticker]
    if recent:
        try:
            last_epoch = datetime.strptime(
                recent[-1]["timestamp"], "%Y-%m-%d %H:%M ET").timestamp()
            if time.time() - last_epoch < COOLDOWN:
                return
        except Exception:
            pass
    alerts.append({
        "id":             f"{ticker}_{int(time.time())}",
        "timestamp":      datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d %H:%M ET"),
        "ticker":         ticker,
        "trend":          trend,
        "strength":       strength,
        "price":          price,
        "entry":          entry,
        "stop":           stop,
        "target":         target,
        "rr":             rr,
        "filters_passed": filters_passed,
        "journaled":      False,
    })
    save_alerts(alerts)


def add_journal_trade(alert_id, ticker, trend, entry, stop, target,
                      rr, exit_price, outcome, notes, setup_date) -> None:
    journal = load_journal()
    risk    = abs(entry - stop)
    pnl_r   = round((exit_price - entry) / risk, 2) if trend == "Bullish" \
              else round((entry - exit_price) / risk, 2)
    journal = [j for j in journal if j["id"] != alert_id]
    journal.append({
        "id": alert_id, "date": setup_date,
        "closed": datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d %H:%M ET"),
        "ticker": ticker, "trend": trend,
        "entry": entry, "stop": stop, "target": target,
        "planned_rr": rr, "exit_price": exit_price,
        "outcome": outcome, "actual_rr": pnl_r, "notes": notes,
    })
    save_journal(journal)
    alerts = load_alerts()
    for a in alerts:
        if a["id"] == alert_id:
            a["journaled"] = True
    save_alerts(alerts)


def journal_stats(journal: list) -> dict:
    if not journal:
        return {}
    wins   = [j for j in journal if j["outcome"] == "WIN"]
    losses = [j for j in journal if j["outcome"] == "LOSS"]
    be     = [j for j in journal if j["outcome"] == "BREAKEVEN"]
    total  = len(journal)
    wr     = round(len(wins)/total*100, 1)
    avg_win  = round(sum(j["actual_rr"] for j in wins)  /len(wins),  2) if wins   else 0
    avg_loss = round(sum(j["actual_rr"] for j in losses)/len(losses), 2) if losses else 0
    total_r  = round(sum(j["actual_rr"] for j in journal), 2)
    gp = sum(j["actual_rr"] for j in wins   if j["actual_rr"] > 0)
    gl = abs(sum(j["actual_rr"] for j in losses if j["actual_rr"] < 0))
    pf = round(gp/gl, 2) if gl else float("inf")
    outcomes   = [j["outcome"] for j in sorted(journal, key=lambda x: x["closed"])]
    streak     = 0
    streak_type= outcomes[-1] if outcomes else ""
    for o in reversed(outcomes):
        if o == streak_type: streak += 1
        else: break
    return {
        "total":total,"wins":len(wins),"losses":len(losses),"breakeven":len(be),
        "win_rate":wr,"avg_win_r":avg_win,"avg_loss_r":avg_loss,
        "total_r":total_r,"profit_factor":pf,"streak":streak,"streak_type":streak_type,
    }


# ─────────────────────────────────────────────
# MARKET HOURS
# ─────────────────────────────────────────────
def is_market_open() -> bool:
    try:
        tz  = pytz.timezone("America/New_York")
        now = datetime.now(tz)
        if now.weekday() >= 5:
            return False
        return now.replace(hour=9,minute=30,second=0,microsecond=0) \
               <= now <= \
               now.replace(hour=16,minute=0,second=0,microsecond=0)
    except Exception:
        return False


# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
def send_telegram_alert(ticker: str, message: str) -> None:
    """
    Sends a Telegram alert. Deduplication/cooldown is handled upstream by
    log_alert() (file-based, persists across Streamlit reruns) — no in-memory
    dict here since that would reset to empty on every script rerun anyway.
    """
    TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
    CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": message}, timeout=5
        )
    except Exception:
        pass


# ─────────────────────────────────────────────
# DATA FETCH  (cached 10 min)
#
# RATE-LIMIT FIX (balanced approach):
#   1. Shared retry-with-backoff wrapper around yf.download, mirroring the
#      one already used for options chains — single-ticker lookups were
#      failing because this fetch path had ZERO retry protection before.
#   2. A lightweight global pacer (_throttle_yf_call) adds a small minimum
#      gap between ANY yfinance call across the whole app — watchlist scan,
#      single lookups, options, weekly trend, earnings, SPY regime — since
#      Yahoo's rate limit is per-IP/session, not per-function.
#   3. Cache TTL raised 5min → 10min for daily bars (intraday 5m bars keep
#      a shorter TTL further down, since those need to stay fresher).
#   4. On failure, the real error reason is preserved (not swallowed) so
#      the UI can tell the user "rate limited" vs "bad ticker".
# ─────────────────────────────────────────────
_YF_MIN_GAP      = 0.35   # seconds — minimum spacing between any two yfinance calls
_YF_RETRY_TRIES  = 3
_YF_RETRY_DELAY  = 2.0    # doubles each attempt: 2s → 4s → 8s
_last_yf_call_ts = {"t": 0.0}
_yf_throttle_lock = threading.Lock()


def _throttle_yf_call() -> None:
    """Global pacer: ensures at least _YF_MIN_GAP seconds between yfinance calls,
    regardless of which function or thread is making them. Lock-protected so
    concurrent threads (watchlist scan) don't race past each other and both
    fire within the same gap window."""
    with _yf_throttle_lock:
        elapsed = time.time() - _last_yf_call_ts["t"]
        if elapsed < _YF_MIN_GAP:
            time.sleep(_YF_MIN_GAP - elapsed)
        _last_yf_call_ts["t"] = time.time()


def _is_rate_limit_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "too many requests" in msg or "rate limit" in msg or "429" in msg


def _yf_download_with_retry(ticker: str, period: str, interval: str) -> pd.DataFrame | None:
    """yf.download wrapped with the same retry-with-backoff pattern used for options."""
    delay = _YF_RETRY_DELAY
    last_err = None
    for attempt in range(_YF_RETRY_TRIES):
        _throttle_yf_call()
        try:
            return yf.download(ticker, period=period, interval=interval, progress=False)
        except Exception as e:
            last_err = e
            if _is_rate_limit_error(e) and attempt < _YF_RETRY_TRIES - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    if last_err:
        raise last_err
    return None


@st.cache_data(ttl=600, show_spinner=False)
def get_data(ticker: str, period: str = "3mo", interval: str = "1d") -> pd.DataFrame | None:
    try:
        df = _yf_download_with_retry(ticker, period, interval)
    except Exception:
        return None   # caller treats None as "no data"; UI layer shows a friendly message separately

    if df is None or df.empty or len(df) < MIN_ROWS:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Open","High","Low","Close","Volume"])
    return df if len(df) >= MIN_ROWS else None


def get_data_with_error(ticker: str, period: str = "3mo", interval: str = "1d") -> tuple[pd.DataFrame | None, str | None]:
    """
    Same as get_data() but also surfaces WHY it failed, for the single-stock
    lookup UI — distinguishes 'rate limited, try again' from 'bad ticker'.
    Not cached itself (get_data() below it is), so this stays cheap.
    """
    try:
        df = _yf_download_with_retry(ticker, period, interval)
    except Exception as e:
        if _is_rate_limit_error(e):
            return None, "Rate limited by Yahoo Finance — please wait a moment and try again."
        return None, f"Data fetch failed: {e}"

    if df is None or df.empty:
        return None, f"No data returned for '{ticker}' — check the ticker symbol."
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Open","High","Low","Close","Volume"])
    if len(df) < MIN_ROWS:
        return None, f"Not enough trading history for '{ticker}' (need {MIN_ROWS}+ bars)."
    return df, None


# ─────────────────────────────────────────────
# INDICATORS  (daily)
# ─────────────────────────────────────────────
def compute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["EMA20"]     = ta.trend.ema_indicator(df["Close"], window=20)
    df["EMA50"]     = ta.trend.ema_indicator(df["Close"], window=50)
    macd            = ta.trend.MACD(df["Close"])
    df["MACD"]      = macd.macd()
    df["Signal"]    = macd.macd_signal()
    df["RSI"]       = ta.momentum.rsi(df["Close"], window=14)
    df["ATR"]       = ta.volatility.average_true_range(df["High"],df["Low"],df["Close"],window=14)
    bb              = ta.volatility.BollingerBands(df["Close"], window=20)
    df["BB_UP"]     = bb.bollinger_hband()
    df["BB_LO"]     = bb.bollinger_lband()
    df["VOL_AVG20"] = df["Volume"].rolling(20).mean()
    # ── ENHANCEMENT 1: ADX ──
    df["ADX"]       = ta.trend.adx(df["High"], df["Low"], df["Close"], window=14)
    return df.dropna(subset=["EMA20","EMA50","MACD","Signal","RSI","ATR","ADX"])


# ─────────────────────────────────────────────
# ENHANCEMENT 1 — ADX TREND STRENGTH
# ─────────────────────────────────────────────
def check_adx(df: pd.DataFrame) -> tuple[bool, float]:
    """Returns (passes, adx_value). ADX > ADX_MIN means real trend, not chop."""
    adx_val = float(df["ADX"].iloc[-1])
    return adx_val >= ADX_MIN, round(adx_val, 1)


# ─────────────────────────────────────────────
# ENHANCEMENT 2 — MULTI-TIMEFRAME CONFIRMATION
# ─────────────────────────────────────────────
@st.cache_data(ttl=900, show_spinner=False)
def get_weekly_trend(ticker: str) -> str | None:
    """Returns 'Bullish', 'Bearish', or None on the weekly timeframe."""
    try:
        _throttle_yf_call()
        df = yf.download(ticker, period="1y", interval="1wk", progress=False)
        if df is None or df.empty or len(df) < 20:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Close"])
        df["EMA10w"] = ta.trend.ema_indicator(df["Close"], window=10)
        df["EMA20w"] = ta.trend.ema_indicator(df["Close"], window=20)
        df = df.dropna(subset=["EMA10w","EMA20w"])
        price  = float(df["Close"].iloc[-1])
        ema10w = float(df["EMA10w"].iloc[-1])
        ema20w = float(df["EMA20w"].iloc[-1])
        if price > ema10w > ema20w:
            return "Bullish"
        elif price < ema10w < ema20w:
            return "Bearish"
        return None
    except Exception:
        return None


def check_weekly_alignment(daily_trend: str, weekly_trend: str | None) -> tuple[bool, str]:
    """Returns (aligns, reason_string)."""
    if weekly_trend is None:
        return False, "Weekly data unavailable"
    if daily_trend == weekly_trend:
        return True, f"Weekly {weekly_trend} ✓"
    return False, f"Daily {daily_trend} vs Weekly {weekly_trend} — misaligned"


# ─────────────────────────────────────────────
# ENHANCEMENT 3 — EARNINGS BLACKOUT
# ─────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)   # cache 1 hr — earnings don't change intraday
def get_next_earnings(ticker: str) -> str | None:
    """Returns next earnings date string (YYYY-MM-DD) or None."""
    try:
        _throttle_yf_call()
        t   = yf.Ticker(ticker)
        cal = t.calendar
        if cal is None:
            return None
        # yfinance returns a dict or DataFrame depending on version
        if isinstance(cal, dict):
            date_val = cal.get("Earnings Date")
            if date_val is None:
                return None
            if isinstance(date_val, (list, tuple)):
                date_val = date_val[0]
            return str(pd.Timestamp(date_val).date())
        elif isinstance(cal, pd.DataFrame):
            if "Earnings Date" in cal.columns:
                val = cal["Earnings Date"].iloc[0]
                return str(pd.Timestamp(val).date())
        return None
    except Exception:
        return None


def check_earnings_blackout(ticker: str) -> tuple[bool, str]:
    """
    Returns (safe_to_trade, reason).
    safe_to_trade=False means earnings within EARNINGS_DAYS — block the signal.
    """
    earnings_date_str = get_next_earnings(ticker)
    if earnings_date_str is None:
        return True, "Earnings date unknown — proceed with caution"

    try:
        earnings_dt = datetime.strptime(earnings_date_str, "%Y-%m-%d").date()
        today       = datetime.now(pytz.timezone("America/New_York")).date()
        days_away   = (earnings_dt - today).days

        if 0 <= days_away <= EARNINGS_DAYS:
            return False, f"⚠️ Earnings in {days_away}d ({earnings_date_str}) — signal blocked"
        elif days_away < 0:
            return True, f"Last earnings: {earnings_date_str}"
        else:
            return True, f"Next earnings: {earnings_date_str} ({days_away}d away)"
    except Exception:
        return True, "Earnings check failed — proceed with caution"


# ─────────────────────────────────────────────
# ENHANCEMENT 4 — SPY MACRO REGIME
# ─────────────────────────────────────────────
@st.cache_data(ttl=1800, show_spinner=False)   # cache 30 min
def get_spy_regime() -> dict:
    """
    Returns regime dict:
        regime: 'Bull' | 'Bear' | 'Neutral'
        price, sma200, adx, reasoning
    """
    try:
        _throttle_yf_call()
        df = yf.download("SPY", period="14mo", interval="1d", progress=False)
        if df is None or df.empty:
            return {"regime": "Unknown", "reasoning": "SPY data unavailable"}
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Close","High","Low"])

        df["SMA200"] = df["Close"].rolling(200).mean()
        df["ADX"]    = ta.trend.adx(df["High"], df["Low"], df["Close"], window=14)
        df           = df.dropna(subset=["SMA200","ADX"])

        price   = float(df["Close"].iloc[-1])
        sma200  = float(df["SMA200"].iloc[-1])
        adx_val = float(df["ADX"].iloc[-1])

        above_200 = price > sma200
        trending  = adx_val >= 20   # gentler threshold for index

        if above_200 and trending:
            regime    = "Bull"
            reasoning = f"SPY ${price:.0f} above 200-SMA ${sma200:.0f} (ADX {adx_val:.0f})"
        elif not above_200 and trending:
            regime    = "Bear"
            reasoning = f"SPY ${price:.0f} below 200-SMA ${sma200:.0f} (ADX {adx_val:.0f})"
        else:
            regime    = "Neutral"
            reasoning = f"SPY ${price:.0f} near 200-SMA ${sma200:.0f} — choppy (ADX {adx_val:.0f})"

        return {
            "regime":    regime,
            "price":     round(price, 2),
            "sma200":    round(sma200, 2),
            "adx":       round(adx_val, 1),
            "reasoning": reasoning,
        }
    except Exception as e:
        return {"regime": "Unknown", "reasoning": str(e)}


def check_regime_alignment(daily_trend: str, spy_regime: dict) -> tuple[bool, str]:
    """Block counter-regime trades: no longs in Bear, no shorts in Bull."""
    regime = spy_regime.get("regime", "Unknown")
    if regime == "Unknown":
        return True, "Regime unknown — no filter applied"
    if daily_trend == "Bullish" and regime == "Bear":
        return False, f"Counter-regime: going Long in SPY Bear market"
    if daily_trend == "Bearish" and regime == "Bull":
        return False, f"Counter-regime: going Short in SPY Bull market"
    return True, f"Regime aligned: {daily_trend} in {regime} market ✓"


# ─────────────────────────────────────────────
# OPTIONS ENGINE  (cached 15 min — options data
#                  is slow-moving; longer TTL
#                  dramatically cuts API calls)
#
# PERFORMANCE FIX: a single shared fetch function
# (`get_full_chain_data`) now does ONE pass over the
# option chain per ticker. Both the "best contract pick"
# logic and the "unusual activity scan" logic read from
# this same cached result — previously each one called
# stock.option_chain() independently, doubling network
# calls for every high-quality alert.
# ─────────────────────────────────────────────
_OPT_RETRY_ATTEMPTS = 3      # max retries per expiry on rate-limit
_OPT_RETRY_DELAY    = 2.0    # seconds between retries (doubles each attempt)
_OPT_EXPIRY_DELAY   = 0.4    # polite pause between expiry fetches
_OPT_MAX_EXPIRIES   = 3      # only check 3 nearest valid expiries


def _fetch_chain_with_retry(stock, expiry: str):
    """Fetch one option chain with exponential back-off on rate limits."""
    delay = _OPT_RETRY_DELAY
    for attempt in range(_OPT_RETRY_ATTEMPTS):
        _throttle_yf_call()
        try:
            return stock.option_chain(expiry)
        except Exception as e:
            msg = str(e).lower()
            if "too many requests" in msg or "rate limit" in msg or "429" in msg:
                if attempt < _OPT_RETRY_ATTEMPTS - 1:
                    time.sleep(delay)
                    delay *= 2        # exponential back-off: 2s → 4s → 8s
                    continue
            raise   # re-raise non-rate-limit errors immediately
    return None     # all retries exhausted


@st.cache_data(ttl=900, show_spinner=False)   # 15-min cache — ONE fetch serves everything below
def get_full_chain_data(ticker: str) -> dict:
    """
    Single source of truth for option chain data on a ticker.
    Fetches up to _OPT_MAX_EXPIRIES nearest valid (DTE >= MIN_DTE) expiries
    ONCE, and returns raw calls/puts DataFrames per expiry so downstream
    functions (best-pick selection, unusual-activity scan) never re-fetch.

    Returns:
        {
          "error": str | None,
          "expiries": [
              {"expiry": str, "dte": int, "calls": DataFrame, "puts": DataFrame},
              ...
          ]
        }
    """
    try:
        stock = yf.Ticker(ticker)

        try:
            _throttle_yf_call()
            all_expiries = stock.options
        except Exception as e:
            if "too many requests" in str(e).lower() or "429" in str(e):
                time.sleep(3)
                _throttle_yf_call()
                all_expiries = stock.options   # one retry
            else:
                raise

        if not all_expiries:
            return {"error": "No option chain available", "expiries": []}

        today   = pd.Timestamp.today().normalize()
        result  = []
        checked = 0

        for expiry in all_expiries:
            if checked >= _OPT_MAX_EXPIRIES:
                break

            dte = (pd.Timestamp(expiry) - today).days
            if dte < MIN_DTE:
                continue

            checked += 1

            try:
                time.sleep(_OPT_EXPIRY_DELAY)    # polite pause before each fetch
                chain = _fetch_chain_with_retry(stock, expiry)
                if chain is None:
                    continue

                result.append({
                    "expiry": expiry,
                    "dte":    dte,
                    "calls":  chain.calls.fillna(0),
                    "puts":   chain.puts.fillna(0),
                })
            except Exception:
                continue   # skip this expiry, try next

        if not result:
            return {"error": "No valid expiries found (all below MIN_DTE or fetch failed)", "expiries": []}

        return {"error": None, "expiries": result}

    except Exception as e:
        msg = str(e)
        if "too many requests" in msg.lower() or "429" in msg:
            return {"error": "Rate limited by Yahoo Finance — try again shortly (cached 15 min)", "expiries": []}
        return {"error": f"Option chain fetch failed ({msg})", "expiries": []}


def get_option_data(ticker: str, price: float, trend: str, strength: str) -> dict:
    """
    Picks the best liquid contract from the SHARED chain cache
    (get_full_chain_data) — does not fetch anything itself.
    """
    chain_data = get_full_chain_data(ticker)
    if chain_data["error"]:
        return {"error": chain_data["error"]}

    best       = None
    best_score = 0

    for entry in chain_data["expiries"]:
        expiry, dte = entry["expiry"], entry["dte"]
        opts = entry["calls"] if trend == "Bullish" else entry["puts"]

        if opts.empty:
            continue

        if strength == "Strong":
            opts = opts[(opts["strike"] <= price*1.02) if trend=="Bullish"
                        else (opts["strike"] >= price*0.98)]
        else:
            opts = opts[(opts["strike"] >= price*0.95) & (opts["strike"] <= price*1.05)]

        if opts.empty:
            continue

        opts           = opts.copy()
        opts["spread"] = opts["ask"] - opts["bid"]
        opts["mid"]    = (opts["ask"] + opts["bid"]) / 2

        valid = opts[(opts["mid"] > 0) & (opts["spread"]/opts["mid"] <= 0.15)]
        valid = valid[(valid["volume"] > 0) | (valid["openInterest"] > 0)]

        if valid.empty:
            continue

        valid = valid.copy()
        valid["liq"] = valid["volume"] + valid["openInterest"]
        top = valid.sort_values("liq", ascending=False).iloc[0]

        if top["liq"] > best_score:
            best       = (top, expiry, dte)
            best_score = top["liq"]

    if best is None:
        return {"error": "No liquid options found"}

    row, expiry, dte = best
    return {
        "label":      "CALL" if trend=="Bullish" else "PUT",
        "strike":     round(float(row["strike"]), 2),
        "expiry":     expiry,
        "mid":        round(float(row["mid"]), 2),
        "last_price": round(float(row["lastPrice"]), 2),
        "volume":     int(row["volume"]),
        "oi":         int(row["openInterest"]),
        "spread":     round(float(row["spread"]), 2),
        "dte":        dte,
        "is_budget":  row["mid"] <= BUDGET_MAX,
    }


# ─────────────────────────────────────────────
# UNUSUAL OPTIONS ACTIVITY ENGINE
# ─────────────────────────────────────────────
# Two signals combined (yfinance has no historical avg-volume-per-contract,
# so we approximate "unusual" using the two metrics that ARE available):
#   1. Volume / Open Interest ratio  → high = fresh same-day positioning
#      (OI updates overnight, so Vol >> OI means new contracts opened today,
#       not just existing positions trading hands)
#   2. Volume / that contract's own recent average volume (proxy: today's
#      volume vs the median volume across the rest of the same expiry's
#      chain) → flags single strikes trading far above their peers
UA_VOL_OI_RATIO_MIN   = 2.0     # Volume >= 2x Open Interest
UA_VOL_OI_RATIO_HIGH  = 4.0     # Volume >= 4x Open Interest → "Extreme"
UA_PEER_MULTIPLE_MIN  = 3.0     # Volume >= 3x the median volume of peer strikes
UA_MIN_VOLUME         = 100     # ignore noise — require some minimum contracts traded
# Note: expiry count is now controlled by _OPT_MAX_EXPIRIES in get_full_chain_data()
# since both this scan and the options engine share that single fetch.


def _score_unusual_contract(row: pd.Series, peer_median_vol: float) -> dict:
    """Score a single option contract row for unusual activity."""
    volume = float(row.get("volume", 0) or 0)
    oi     = float(row.get("openInterest", 0) or 0)

    if volume < UA_MIN_VOLUME:
        return {"unusual": False}

    vol_oi_ratio = volume / oi if oi > 0 else float("inf") if volume > 0 else 0
    peer_ratio   = volume / peer_median_vol if peer_median_vol > 0 else 0

    vol_oi_flag  = vol_oi_ratio >= UA_VOL_OI_RATIO_MIN
    peer_flag    = peer_ratio   >= UA_PEER_MULTIPLE_MIN

    if not (vol_oi_flag or peer_flag):
        return {"unusual": False}

    # Severity tiering
    if vol_oi_ratio >= UA_VOL_OI_RATIO_HIGH and peer_flag:
        severity = "Extreme"
    elif vol_oi_flag and peer_flag:
        severity = "High"
    else:
        severity = "Moderate"

    reasons = []
    if vol_oi_flag:
        reasons.append(f"Vol {int(volume):,} is {vol_oi_ratio:.1f}x Open Interest ({int(oi):,})")
    if peer_flag:
        reasons.append(f"Vol is {peer_ratio:.1f}x the chain's median strike volume")

    return {
        "unusual":      True,
        "severity":     severity,
        "vol_oi_ratio": round(vol_oi_ratio, 1) if vol_oi_ratio != float("inf") else None,
        "peer_ratio":   round(peer_ratio, 1),
        "reasons":      reasons,
        "volume":       int(volume),
        "oi":           int(oi),
    }


def scan_unusual_activity(ticker: str) -> dict:
    """
    Scans the option chain (calls + puts, nearest expiries) for unusual
    activity using Volume/OI ratio + Volume vs peer-strike-median.

    PERFORMANCE FIX: reads from the SHARED get_full_chain_data() cache —
    no independent network fetch. If get_option_data() already ran for
    this ticker this session, this call costs nothing extra.
    """
    chain_data = get_full_chain_data(ticker)
    if chain_data["error"]:
        return {"error": chain_data["error"], "flagged": []}

    flagged = []
    checked = 0

    for entry in chain_data["expiries"]:
        expiry, dte = entry["expiry"], entry["dte"]
        checked += 1

        for label, opts in (("CALL", entry["calls"]), ("PUT", entry["puts"])):
            if opts.empty:
                continue

            peer_median_vol = float(opts["volume"].median())

            for _, row in opts.iterrows():
                score = _score_unusual_contract(row, peer_median_vol)
                if score.get("unusual"):
                    flagged.append({
                        "ticker":      ticker,
                        "type":        label,
                        "strike":      round(float(row["strike"]), 2),
                        "expiry":      expiry,
                        "dte":         dte,
                        "last_price":  round(float(row.get("lastPrice", 0) or 0), 2),
                        "severity":    score["severity"],
                        "vol_oi_ratio":score["vol_oi_ratio"],
                        "peer_ratio":  score["peer_ratio"],
                        "reasons":     score["reasons"],
                        "volume":      score["volume"],
                        "oi":          score["oi"],
                    })

    sev_rank = {"Extreme": 3, "High": 2, "Moderate": 1}
    flagged.sort(key=lambda x: (sev_rank.get(x["severity"], 0), x["volume"]), reverse=True)

    return {"flagged": flagged, "expiries_checked": checked}


def check_pick_unusual_activity(ticker: str, opt: dict) -> dict | None:
    """
    Cross-references our already-fetched option pick (from get_option_data)
    against the unusual-activity scan, so the Options tab can show a badge
    without an extra fetch.
    """
    if not opt or "error" in opt:
        return None

    ua = scan_unusual_activity(ticker)
    if "error" in ua or not ua.get("flagged"):
        return None

    for f in ua["flagged"]:
        if (f["type"] == opt["label"]
                and abs(f["strike"] - opt["strike"]) < 0.01
                and f["expiry"] == opt["expiry"]):
            return f
    return None


# ─────────────────────────────────────────────
# TRADE ANALYSIS  — all 4 filters woven in
# ─────────────────────────────────────────────
def _analyze_uncached(df: pd.DataFrame, ticker: str,
                       spy_regime: dict | None = None) -> dict | None:

    latest  = df.iloc[-1]
    price   = float(latest["Close"])
    ema20   = float(latest["EMA20"])
    ema50   = float(latest["EMA50"])
    rsi     = float(latest["RSI"])
    macd    = float(latest["MACD"])
    signal  = float(latest["Signal"])
    atr     = float(latest["ATR"])
    volume  = float(latest["Volume"])
    vol_avg = float(latest["VOL_AVG20"])

    # ── SWING-TRADING CALIBRATION ──
    # Original thresholds (RSI 40-75 bull / Volume >= 1.0x avg) were tuned
    # for momentum/breakout entries already in motion — which excludes the
    # classic swing-trade pullback entry (price still trending, RSI cooled
    # to neutral/oversold-ish, volume not yet confirmed). Widened below to
    # catch setups EARLIER in the swing without lowering trend-quality bar:
    #   - RSI bull band: 40-75 → 30-75 (catches pullback buys, not just
    #     already-extended momentum)
    #   - RSI bear band: 25-60 → 25-70 (mirrors the bull widening)
    #   - Volume: removed hard floor, now scored as a strength input instead
    #     of a binary gate — a quiet-volume day no longer kills an otherwise
    #     valid EMA+MACD+RSI trend signal, it just can't reach "Strong"
    vol_ok       = volume >= vol_avg * VOLUME_MULT       # still computed, used for strength scoring
    vol_soft_ok  = volume >= vol_avg * 0.70               # minimum floor — filters out dead/illiquid days only

    # ── Base trend ──
    if price > ema20 > ema50 and macd > signal and 30 < rsi < 75 and vol_soft_ok:
        trend = "Bullish"
    elif price < ema20 < ema50 and macd < signal and 25 < rsi < 70 and vol_soft_ok:
        trend = "Bearish"
    else:
        return None

    # "Strong" now requires the FULL original strict bar (extended RSI +
    # above-average volume) — so high-quality alerts keep the same
    # standard as before. "Normal" setups are the newly-unlocked pullback
    # entries: valid trend, just not yet at peak momentum.
    strength = "Strong" if (
        ((rsi > 60 and trend=="Bullish") or (rsi < 40 and trend=="Bearish"))
        and vol_ok
    ) else "Normal"

    # ── Run all 4 enhancement filters ──
    filters: dict[str, dict] = {}

    # 1. ADX
    adx_ok, adx_val = check_adx(df)
    filters["ADX Trend Strength"] = {
        "pass": adx_ok,
        "detail": f"ADX {adx_val} {'≥' if adx_ok else '<'} {ADX_MIN} threshold",
    }

    # 2. Multi-timeframe
    weekly_trend = get_weekly_trend(ticker) if WEEKLY_CONFIRM else None
    mtf_ok, mtf_detail = check_weekly_alignment(trend, weekly_trend)
    filters["Multi-TF Alignment"] = {"pass": mtf_ok, "detail": mtf_detail}

    # 3. Earnings blackout
    earnings_ok, earnings_detail = check_earnings_blackout(ticker)
    filters["Earnings Blackout"] = {"pass": earnings_ok, "detail": earnings_detail}

    # 4. SPY regime
    if SPY_REGIME and spy_regime:
        regime_ok, regime_detail = check_regime_alignment(trend, spy_regime)
    else:
        regime_ok, regime_detail = True, "Regime filter disabled"
    filters["Macro Regime"] = {"pass": regime_ok, "detail": regime_detail}

    # ── Score how many filters pass ──
    n_pass   = sum(1 for f in filters.values() if f["pass"])
    n_total  = len(filters)
    all_pass = (n_pass == n_total)

    # ── Entry / stop / target ──
    lookback_high = df["High"].iloc[-6:-1].max()
    lookback_low  = df["Low"].iloc[-6:-1].min()

    if trend == "Bullish":
        entry      = round(lookback_high * 1.002, 2)
        stop       = round(price - atr, 2)
        resistance = float(df["High"].tail(20).max())
        target     = round(min(price + atr*2.5, resistance*0.99), 2)
    else:
        entry   = round(lookback_low * 0.998, 2)
        stop    = round(price + atr, 2)
        support = float(df["Low"].tail(20).min())
        target  = round(max(price - atr*2.5, support*1.01), 2)

    risk = abs(entry - stop)
    if risk < 0.01:
        return None

    rr = round(abs(target - entry) / risk, 2)
    if rr < MIN_RR:
        return None

    option = get_option_data(ticker, price, trend, strength)

    # High quality = RR ≥ 2, Strong strength, AND all 4 filters pass
    high_quality = (rr >= 2.0 and strength == "Strong" and all_pass)

    return {
        "ticker":       ticker,
        "price":        round(price, 2),
        "trend":        trend,
        "strength":     strength,
        "entry":        entry,
        "stop":         stop,
        "target":       target,
        "rr":           rr,
        "rsi":          round(rsi, 1),
        "atr":          round(atr, 2),
        "adx":          adx_val,
        "option":       option,
        "filters":      filters,
        "filters_pass": n_pass,
        "filters_total":n_total,
        "all_pass":     all_pass,
        "high_quality": high_quality,
    }


@st.cache_data(ttl=300, show_spinner=False)
def analyze(_df: pd.DataFrame, ticker: str, latest_bar_key: str,
            spy_regime: dict | None = None) -> dict | None:
    """
    PERFORMANCE FIX: cached wrapper around _analyze_uncached.

    Streamlit reruns the entire script on every interaction (button click,
    text input, etc). Without this cache, every rerun would re-trigger the
    full filter pipeline — weekly trend fetch, earnings fetch, options chain
    fetch — even if nothing about the underlying data changed.

    Cache key includes `latest_bar_key` (the timestamp of the most recent
    candle) so the cache correctly invalidates once new daily data arrives,
    while staying hit during repeated reruns within the same trading day.
    The leading underscore on `_df` tells Streamlit not to hash the
    DataFrame itself (expensive) — we hash latest_bar_key instead.
    """
    return _analyze_uncached(_df, ticker, spy_regime=spy_regime)


# ─────────────────────────────────────────────
# SCALP ENGINE
# ─────────────────────────────────────────────
def scalp(df: pd.DataFrame) -> dict:
    latest     = df.iloc[-1]
    price      = float(latest["Close"])
    atr        = float(latest["ATR"]) if "ATR" in df.columns else 0
    prior_high = float(df["High"].iloc[-6:-1].max())
    prior_low  = float(df["Low"].iloc[-6:-1].min())
    if (prior_high - prior_low) / price < 0.005:
        return {"signal":"Low volatility — avoid scalping","direction":None}
    rsi  = float(latest["RSI"])    if "RSI"    in df.columns else 50
    macd = float(latest["MACD"])   if "MACD"   in df.columns else 0
    sig  = float(latest["Signal"]) if "Signal" in df.columns else 0
    if price > prior_high and macd > sig and rsi < 75:
        return {"signal":f"Breakout scalp ↑ {round(price,2)}","direction":"Long",
                "stop":round(prior_high-atr*0.5,2),"target":round(price+atr,2)}
    elif price < prior_low and macd < sig and rsi > 25:
        return {"signal":f"Breakdown scalp ↓ {round(price,2)}","direction":"Short",
                "stop":round(prior_low+atr*0.5,2),"target":round(price-atr,2)}
    return {"signal":"No clear intraday setup","direction":None}


# ─────────────────────────────────────────────
# WATCHLIST SCAN  (cached 5 min)
#
# PERFORMANCE FIX: tickers are now fetched + analyzed
# CONCURRENTLY via ThreadPoolExecutor instead of one
# at a time. yfinance calls are I/O-bound (waiting on
# network), so threads give a real speedup here without
# needing async rewrites.
#
# BALANCED rate-limit tuning: workers reduced 4 → 3, and
# the global _throttle_yf_call() pacer (shared module-level
# state) now spaces out calls EVEN ACROSS threads — so more
# workers no longer means more simultaneous requests hitting
# Yahoo at once, just more requests queued up close together.
# ─────────────────────────────────────────────
_SCAN_MAX_WORKERS = 3


def _scan_one_ticker(ticker: str, spy_regime: dict) -> dict | None:
    """Fetch + analyze a single ticker. Designed to run inside a thread."""
    df = get_data(ticker)
    if df is None:
        return None
    df = compute(df)
    if df.empty:
        return None
    latest_bar_key = f"{ticker}_{df.index[-1]}"
    return analyze(df, ticker, latest_bar_key, spy_regime=spy_regime)


@st.cache_data(ttl=300, show_spinner=False)
def run_watchlist_scan(scan_list: tuple) -> list[dict]:
    spy_regime = get_spy_regime()
    results    = []

    with ThreadPoolExecutor(max_workers=_SCAN_MAX_WORKERS) as executor:
        futures = {
            executor.submit(_scan_one_ticker, t, spy_regime): t
            for t in scan_list
        }
        for future in as_completed(futures):
            try:
                r = future.result()
                if r:
                    results.append(r)
            except Exception:
                continue   # one ticker failing shouldn't kill the whole scan

    return results


# ─────────────────────────────────────────────
# FILTER SCORECARD HELPER (UI)
# ─────────────────────────────────────────────
def render_filter_scorecard(filters: dict, n_pass: int, n_total: int):
    st.markdown(f"**Signal Filters: {n_pass}/{n_total} passed**")
    icons = {True: "✅", False: "❌"}
    for name, f in filters.items():
        color_class = "filter-pass" if f["pass"] else "filter-fail"
        st.markdown(
            f'<div class="{color_class}">{icons[f["pass"]]} <b>{name}</b> — {f["detail"]}</div>',
            unsafe_allow_html=True
        )


# ─────────────────────────────────────────────
# MARKET STATUS + REGIME BANNER
# ─────────────────────────────────────────────
market_open = is_market_open()
spy_regime  = get_spy_regime()

col_status, col_regime = st.columns([1, 2])
with col_status:
    if market_open:
        st.success("🟢 Market OPEN")
    else:
        st.warning("🔴 Market CLOSED")

with col_regime:
    regime = spy_regime.get("regime","Unknown")
    regime_color = {"Bull":"🟢","Bear":"🔴","Neutral":"🟡"}.get(regime,"⚪")
    st.info(f"{regime_color} **Macro Regime: {regime}** — {spy_regime.get('reasoning','')}")

st.divider()

# ─────────────────────────────────────────────
# TOP-LEVEL TABS
# ─────────────────────────────────────────────
TAB_SCAN, TAB_STOCK, TAB_UNUSUAL, TAB_ALERTS, TAB_JOURNAL = st.tabs([
    "📡 Watchlist Scan",
    "🔍 Stock Analysis",
    "🌊 Unusual Activity",
    "🔔 Alert History",
    "📓 Trade Journal",
])


# ═══════════════════════════════════════════════
# TAB 1 — WATCHLIST SCAN
# ═══════════════════════════════════════════════
with TAB_SCAN:
    with st.spinner("Scanning watchlist…"):
        all_setups = run_watchlist_scan(tuple(SCAN_LIST))

    high_quality = [s for s in all_setups if s["high_quality"]]
    partial      = [s for s in all_setups if not s["high_quality"] and s["all_pass"]]
    weak         = [s for s in all_setups if not s["all_pass"]]

    for a in high_quality:
        log_alert(ticker=a["ticker"], trend=a["trend"], strength=a["strength"],
                  entry=a["entry"], stop=a["stop"], target=a["target"],
                  rr=a["rr"], price=a["price"], filters_passed=a["filters"])
        if market_open:
            filter_summary = " | ".join(
                f"{'✅' if f['pass'] else '❌'} {n}"
                for n, f in a["filters"].items()
            )
            msg = (
                f"🚨 HIGH QUALITY ALERT ({a['filters_pass']}/{a['filters_total']} filters)\n"
                f"{a['ticker']} → {a['trend']} ({a['strength']})\n"
                f"Price: {a['price']} | RR: {a['rr']} | ADX: {a['adx']}\n"
                f"Entry: {a['entry']} | Stop: {a['stop']} | Target: {a['target']}\n"
                f"{filter_summary}"
            )
            send_telegram_alert(a["ticker"], msg)

    # ── Display ──
    c1, c2, c3 = st.columns(3)
    c1.metric("🔥 High Quality",  len(high_quality))
    c2.metric("✅ All Filters",   len(partial))
    c3.metric("⚠️ Partial Setup", len(weak))

    st.divider()

    st.markdown("### 🔥 High-Quality Setups (all 4 filters + RR≥2 + Strong)")
    if high_quality:
        for a in high_quality:
            with st.container(border=True):
                h1, h2, h3, h4, h5 = st.columns(5)
                h1.metric("Ticker",   a["ticker"])
                h2.metric("Trend",    f"{a['trend']} ({a['strength']})")
                h3.metric("R:R",      a["rr"])
                h4.metric("ADX",      a["adx"])
                h5.metric("Filters",  f"{a['filters_pass']}/{a['filters_total']}")
                st.caption(f"Entry {a['entry']} · Stop {a['stop']} · Target {a['target']} · RSI {a['rsi']}")
    else:
        st.info("No high-quality setups right now — all 4 filters must pass.")

    st.markdown("### ✅ Valid Setups (all filters pass, Normal strength or RR<2)")
    if partial:
        for a in partial:
            with st.container(border=True):
                st.write(f"**{a['ticker']}** — {a['trend']} | RR {a['rr']} | ADX {a['adx']} | RSI {a['rsi']}")
    else:
        st.info("No partial setups")

    with st.expander(f"⚠️ Signals with filter failures ({len(weak)} tickers)"):
        for a in weak:
            failed = [n for n, f in a["filters"].items() if not f["pass"]]
            st.write(f"**{a['ticker']}** — {a['trend']} | Failed: {', '.join(failed)}")


# ═══════════════════════════════════════════════
# TAB 2 — SINGLE STOCK ANALYSIS
# ═══════════════════════════════════════════════
with TAB_STOCK:
    st.subheader("🔍 Single Stock Analysis")
    query = st.text_input("Enter ticker (e.g. TSLA, NVDA, AAPL)", placeholder="TSLA", key="ticker_input")

    if query:
        ticker = query.strip().upper()

        with st.spinner(f"Fetching {ticker}…"):
            df, fetch_error = get_data_with_error(ticker)
            intraday = get_data(ticker, period="5d", interval="5m")

        if df is None:
            st.error(f"❌ {fetch_error or f'Could not load data for {ticker}'}")
            if fetch_error and "Rate limited" in fetch_error:
                st.caption("Daily data is cached for 10 minutes once it loads successfully — this only affects fresh lookups.")
        else:
            df = compute(df)
            latest_price = float(df["Close"].iloc[-1])
            latest_rsi   = float(df["RSI"].iloc[-1])
            latest_atr   = float(df["ATR"].iloc[-1])
            latest_adx   = float(df["ADX"].iloc[-1])
            vol_now      = float(df["Volume"].iloc[-1])
            vol_avg      = float(df["VOL_AVG20"].iloc[-1])

            pc1, pc2, pc3, pc4, pc5 = st.columns(5)
            pc1.metric("Last Price", f"${latest_price:,.2f}")
            pc2.metric("RSI (14)",   f"{latest_rsi:.1f}")
            pc3.metric("ATR (14)",   f"${latest_atr:.2f}")
            pc4.metric("ADX (14)",   f"{latest_adx:.1f}",
                       delta="Trending" if latest_adx>=ADX_MIN else "Choppy",
                       delta_color="normal" if latest_adx>=ADX_MIN else "inverse")
            pc5.metric("Vol vs Avg", f"{vol_now/vol_avg:.2f}×")

            st.divider()
            latest_bar_key = f"{ticker}_{df.index[-1]}"
            r = analyze(df, ticker, latest_bar_key, spy_regime=spy_regime)

            stab1, stab2, stab3, stab4, stab5 = st.tabs([
                "💼 Swing Trade", "🔬 Signal Filters",
                "🧠 Options", "⚡ Intraday Scalp", "💸 Budget Options"
            ])

            with stab1:
                if r is None:
                    st.warning("⚠️ No valid trade setup — base signal conditions not met.")

                    # ── Live diagnostic: show actual values vs what's required ──
                    ema20_v = float(df["EMA20"].iloc[-1])
                    ema50_v = float(df["EMA50"].iloc[-1])
                    macd_v  = float(df["MACD"].iloc[-1])
                    sig_v   = float(df["Signal"].iloc[-1])
                    rsi_v   = latest_rsi
                    vol_ratio = vol_now / vol_avg if vol_avg else 0

                    trend_stack_bull = latest_price > ema20_v > ema50_v
                    trend_stack_bear = latest_price < ema20_v < ema50_v
                    macd_bull = macd_v > sig_v
                    macd_bear = macd_v < sig_v
                    rsi_bull_ok = 30 < rsi_v < 75
                    rsi_bear_ok = 25 < rsi_v < 70
                    vol_floor_ok = vol_ratio >= 0.70

                    def chk(ok): return "✅" if ok else "❌"

                    # Determine which direction the EMA stack implies,
                    # so every downstream condition is evaluated against
                    # THAT direction — not a loose "either direction" check
                    # (that was the bug: MACD showed ✅ even when it was
                    #  bearish while the EMA stack was bullish)
                    if trend_stack_bull:
                        implied = "Bullish"
                        macd_aligned  = macd_bull
                        rsi_in_band   = rsi_bull_ok
                        macd_label    = f"need MACD > Signal for Bullish (MACD {macd_v:.3f} {'>' if macd_bull else '<'} Signal {sig_v:.3f})"
                        rsi_label     = f"need RSI 30–75 for Bullish (RSI {rsi_v:.1f})"
                    elif trend_stack_bear:
                        implied = "Bearish"
                        macd_aligned  = macd_bear
                        rsi_in_band   = rsi_bear_ok
                        macd_label    = f"need MACD < Signal for Bearish (MACD {macd_v:.3f} {'<' if macd_bear else '>'} Signal {sig_v:.3f})"
                        rsi_label     = f"need RSI 25–70 for Bearish (RSI {rsi_v:.1f})"
                    else:
                        implied = None
                        macd_aligned  = False
                        rsi_in_band   = False
                        macd_label    = f"MACD {macd_v:.3f} vs Signal {sig_v:.3f} — EMA stack must align first"
                        rsi_label     = f"RSI {rsi_v:.1f} — EMA stack must align first"

                    all_base_ok = (trend_stack_bull or trend_stack_bear) and macd_aligned and rsi_in_band and vol_floor_ok

                    st.markdown(
                        f"**Why no signal fired — implied direction: "
                        f"{'🟢 ' + implied if implied else '⚪ Mixed/No trend'} "
                        f"({'all base conditions met — check 4 filters above' if all_base_ok else 'base condition(s) failed below'})**"
                    )
                    st.caption(
                        f"{chk(trend_stack_bull or trend_stack_bear)} "
                        f"Trend stack — Price ${latest_price:.2f} / EMA20 ${ema20_v:.2f} / EMA50 ${ema50_v:.2f} "
                        f"({'✓ ' + implied + ' stack' if implied else '✗ no clean stack — price is between EMAs or EMAs crossed'})"
                    )
                    st.caption(
                        f"{chk(macd_aligned)} MACD momentum — {macd_label}"
                    )
                    st.caption(
                        f"{chk(rsi_in_band)} RSI band — {rsi_label}"
                    )
                    st.caption(
                        f"{chk(vol_floor_ok)} Volume floor — "
                        f"{vol_ratio:.2f}× the 20-day average (need ≥ 0.70×)"
                    )
                    st.caption(
                        "All four base conditions must align in the same direction "
                        "on the same day. The most common miss for swing setups is "
                        "MACD lagging the EMA stack — the trend has resumed but "
                        "momentum confirmation hasn't crossed yet. Usually resolves "
                        "within 1–3 daily bars."
                    )
                else:
                    n_pass  = r["filters_pass"]
                    n_total = r["filters_total"]
                    if r["high_quality"]:
                        badge = f"🔥 HIGH QUALITY ({n_pass}/{n_total} filters)"
                    elif r["all_pass"]:
                        badge = f"✅ VALID — all filters pass"
                    else:
                        badge = f"⚠️ PARTIAL — {n_pass}/{n_total} filters pass"

                    st.markdown(f"### {badge} — {r['trend']} ({r['strength']})")
                    s1, s2, s3, s4 = st.columns(4)
                    s1.metric("Entry",  f"${r['entry']}")
                    s2.metric("Stop",   f"${r['stop']}")
                    s3.metric("Target", f"${r['target']}")
                    s4.metric("R:R",    r["rr"])
                    risk_amt   = abs(r["entry"] - r["stop"])
                    reward_amt = abs(r["target"] - r["entry"])
                    st.progress(
                        min(reward_amt / (risk_amt + reward_amt), 1.0),
                        text=f"Reward ${reward_amt:.2f} vs Risk ${risk_amt:.2f}"
                    )

            with stab2:
                st.markdown("### 🔬 Signal Filter Scorecard")
                if r is None:
                    st.warning("No base signal to filter.")
                else:
                    render_filter_scorecard(r["filters"], r["filters_pass"], r["filters_total"])

                    st.divider()
                    st.markdown("**Filter Definitions**")
                    st.caption(f"1. **ADX ≥ {ADX_MIN}** — confirms real trend, blocks chop/sideways markets")
                    st.caption("2. **Multi-TF Alignment** — weekly EMA stack must agree with daily direction")
                    st.caption(f"3. **Earnings Blackout** — blocks signals within {EARNINGS_DAYS} days of earnings")
                    st.caption("4. **Macro Regime** — no longs in SPY Bear market; no shorts in SPY Bull market")

            with stab3:
                if r is None:
                    st.warning("Swing trade setup required for options recommendation.")
                else:
                    opt = r["option"]
                    if "error" in opt:
                        st.error(f"⚠️ {opt['error']}")
                    else:
                        emoji = "📈" if opt["label"] == "CALL" else "📉"
                        st.markdown(f"### {emoji} {opt['label']} — Exp {opt['expiry']} ({opt['dte']} DTE)")
                        o1, o2, o3, o4 = st.columns(4)
                        o1.metric("Strike",    f"${opt['strike']}")
                        o2.metric("Mid Price", f"${opt['mid']}")
                        o3.metric("Volume",    f"{opt['volume']:,}")
                        o4.metric("Open Int.", f"{opt['oi']:,}")
                        spread_pct = (opt["spread"]/opt["mid"]*100) if opt["mid"] else 0
                        st.caption(f"Spread: ${opt['spread']} ({spread_pct:.1f}% of mid) · Last: ${opt['last_price']}")
                        if opt["is_budget"]:
                            st.success(f"💸 Budget pick — ${opt['mid']}/contract (under ${BUDGET_MAX:.2f})")
                        if not r["all_pass"]:
                            st.warning("⚠️ Not all signal filters pass — trade at your own discretion.")

                        # ── Unusual activity badge on our own pick ──
                        ua_hit = check_pick_unusual_activity(ticker, opt)
                        if ua_hit:
                            sev_emoji = {"Extreme":"🔴","High":"🟠","Moderate":"🟡"}.get(ua_hit["severity"],"⚪")
                            st.markdown(f"### {sev_emoji} Unusual Activity Detected — {ua_hit['severity']}")
                            for reason in ua_hit["reasons"]:
                                st.caption(f"• {reason}")
                        else:
                            st.caption("🌊 No unusual activity flagged on this contract — see Unusual Activity tab for full chain scan.")

            with stab4:
                if intraday is None or len(intraday) < 20:
                    st.warning("Not enough intraday data.")
                else:
                    intraday = compute(intraday)
                    sc = scalp(intraday)
                    if sc["direction"] is None:
                        st.info(f"ℹ️ {sc['signal']}")
                    else:
                        arrow = "↑" if sc["direction"] == "Long" else "↓"
                        st.markdown(f"### ⚡ {sc['signal']} {arrow}")
                        sc1, sc2 = st.columns(2)
                        sc1.metric("Scalp Stop",   f"${sc.get('stop','N/A')}")
                        sc2.metric("Scalp Target", f"${sc.get('target','N/A')}")
                        st.caption("Scalp targets are intraday — tight stops, monitor closely.")

            with stab5:
                st.markdown(f"### 💸 Options under ${BUDGET_MAX:.2f}/contract")
                if r is None:
                    st.warning("A valid swing setup is needed.")
                else:
                    opt = r["option"]
                    if "error" in opt:
                        st.error(f"⚠️ {opt['error']}")
                    elif opt["is_budget"]:
                        st.success(
                            f"✅ **{opt['label']}** · Strike ${opt['strike']} · "
                            f"Exp {opt['expiry']} ({opt['dte']} DTE) · "
                            f"Mid **${opt['mid']}** · Vol {opt['volume']:,} · OI {opt['oi']:,}"
                        )
                        st.caption("Budget options carry higher gamma risk — size accordingly.")
                    else:
                        st.info(
                            f"Best contract is ${opt['mid']}/contract — above ${BUDGET_MAX:.2f}. "
                            "Try a wider strike or longer expiry."
                        )

            st.divider()
            st.caption("⚠️ Not financial advice. Rule-based signals only.")


# ═══════════════════════════════════════════════
# TAB 3 — UNUSUAL OPTIONS ACTIVITY
# ═══════════════════════════════════════════════
with TAB_UNUSUAL:
    st.subheader("🌊 Unusual Options Activity Scanner")
    st.caption(
        "Flags contracts where Volume far exceeds Open Interest (fresh same-day "
        "positioning) or Volume far exceeds peer strikes in the same chain. "
        "Built from yfinance data only — no paid options-flow feed."
    )

    ua_col1, ua_col2 = st.columns([2, 1])
    with ua_col1:
        ua_ticker_input = st.text_input(
            "Ticker to scan", placeholder="TSLA", key="ua_ticker_input"
        )
    with ua_col2:
        ua_scan_watchlist = st.checkbox(
            "Scan full watchlist instead", key="ua_scan_watchlist"
        )

    st.divider()

    def render_unusual_table(flagged: list, ticker_label: str = "", top_n: int = 5):
        if not flagged:
            st.info(f"No unusual activity detected{f' for {ticker_label}' if ticker_label else ''}.")
            return

        sev_rank = {"Extreme": 3, "High": 2, "Moderate": 1}
        sev_emoji_map = {"Extreme": "🔴", "High": "🟠", "Moderate": "🟡"}

        # ── Group by ticker ──
        by_ticker: dict[str, list] = {}
        for f in flagged:
            by_ticker.setdefault(f["ticker"], []).append(f)

        # Order tickers by their single highest-severity / highest-volume contract
        def ticker_sort_key(t):
            best = max(by_ticker[t], key=lambda x: (sev_rank.get(x["severity"], 0), x["volume"]))
            return (sev_rank.get(best["severity"], 0), best["volume"])

        ordered_tickers = sorted(by_ticker.keys(), key=ticker_sort_key, reverse=True)

        for t in ordered_tickers:
            contracts = sorted(
                by_ticker[t],
                key=lambda x: (sev_rank.get(x["severity"], 0), x["volume"]),
                reverse=True
            )
            total_count = len(contracts)
            top_contracts = contracts[:top_n]

            extreme_n = sum(1 for c in contracts if c["severity"] == "Extreme")
            high_n    = sum(1 for c in contracts if c["severity"] == "High")

            header = f"**{t}** — {total_count} flagged"
            if total_count > top_n:
                header += f" (showing top {top_n})"
            badges = " ".join(filter(None, [
                f"🔴 x{extreme_n}" if extreme_n else "",
                f"🟠 x{high_n}" if high_n else "",
            ]))

            st.markdown(f"### {header}  {badges}")

            for f in top_contracts:
                sev_emoji  = sev_emoji_map.get(f["severity"], "⚪")
                type_emoji = "📈" if f["type"] == "CALL" else "📉"
                with st.container(border=True):
                    u1, u2, u3, u4, u5 = st.columns([1, 1, 1.2, 1, 1.5])
                    u1.markdown(f"{type_emoji} **{f['type']}**")
                    u2.markdown(f"Strike **${f['strike']}**")
                    u3.markdown(f"Exp {f['expiry']} ({f['dte']}d)")
                    u4.markdown(f"{sev_emoji} **{f['severity']}**")
                    u5.markdown(f"Vol **{f['volume']:,}** / OI {f['oi']:,}")
                    for reason in f["reasons"]:
                        st.caption(f"• {reason}")

            st.divider()

    if ua_scan_watchlist:
        st.markdown(f"### Scanning {len(SCAN_LIST)} watchlist tickers…")
        all_flagged = []
        progress = st.progress(0, text="Starting scan…")
        for i, t in enumerate(SCAN_LIST):
            progress.progress((i + 1) / len(SCAN_LIST), text=f"Scanning {t}…")
            result = scan_unusual_activity(t)
            if "error" not in result:
                all_flagged.extend(result.get("flagged", []))
        progress.empty()

        sev_rank = {"Extreme": 3, "High": 2, "Moderate": 1}
        all_flagged.sort(key=lambda x: (sev_rank.get(x["severity"], 0), x["volume"]), reverse=True)

        wc1, wc2, wc3 = st.columns(3)
        wc1.metric("Total Flagged", len(all_flagged))
        wc2.metric("Extreme", sum(1 for f in all_flagged if f["severity"] == "Extreme"))
        wc3.metric("Tickers Affected", len(set(f["ticker"] for f in all_flagged)))
        st.divider()
        render_unusual_table(all_flagged)

    elif ua_ticker_input:
        ticker_ua = ua_ticker_input.strip().upper()
        with st.spinner(f"Scanning {ticker_ua} option chain…"):
            result = scan_unusual_activity(ticker_ua)

        if "error" in result:
            st.error(f"⚠️ {result['error']}")
        else:
            flagged = result.get("flagged", [])
            fc1, fc2, fc3 = st.columns(3)
            fc1.metric("Flagged Contracts", len(flagged))
            fc2.metric("Extreme", sum(1 for f in flagged if f["severity"] == "Extreme"))
            fc3.metric("Expiries Checked", result.get("expiries_checked", 0))
            st.divider()
            render_unusual_table(flagged, ticker_ua)
    else:
        st.info("Enter a ticker above, or check the box to scan your full watchlist.")

    st.divider()
    st.markdown("**How severity is scored**")
    st.caption(f"🟡 Moderate — Vol ≥ {UA_VOL_OI_RATIO_MIN}x OI **or** ≥ {UA_PEER_MULTIPLE_MIN}x peer median volume")
    st.caption("🟠 High — both conditions met simultaneously")
    st.caption(f"🔴 Extreme — Vol ≥ {UA_VOL_OI_RATIO_HIGH}x OI **and** ≥ {UA_PEER_MULTIPLE_MIN}x peer median volume")
    st.caption(f"Contracts with fewer than {UA_MIN_VOLUME} contracts traded are ignored as noise.")
    st.caption("⚠️ Not financial advice. This is a heuristic screen, not confirmed institutional options flow.")


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
        pending_cnt   = total_alerts - journaled_cnt

        ac1, ac2, ac3 = st.columns(3)
        ac1.metric("Total Alerts",    total_alerts)
        ac2.metric("Journaled",       journaled_cnt)
        ac3.metric("Pending Journal", pending_cnt)
        st.divider()

        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            ticker_filter = st.selectbox("Filter by ticker",
                ["All"] + sorted(set(a["ticker"] for a in alerts)), key="alert_ticker_filter")
        with col_f2:
            trend_filter = st.selectbox("Filter by trend",
                ["All","Bullish","Bearish"], key="alert_trend_filter")
        with col_f3:
            journal_filter = st.selectbox("Journal status",
                ["All","Pending","Journaled"], key="alert_journal_filter")

        filtered = alerts
        if ticker_filter  != "All": filtered = [a for a in filtered if a["ticker"] == ticker_filter]
        if trend_filter   != "All": filtered = [a for a in filtered if a["trend"]  == trend_filter]
        if journal_filter == "Pending":   filtered = [a for a in filtered if not a.get("journaled")]
        elif journal_filter == "Journaled": filtered = [a for a in filtered if a.get("journaled")]

        st.markdown(f"**{len(filtered)} alert(s) shown**")

        for a in reversed(filtered):
            trend_badge = "🟢" if a["trend"] == "Bullish" else "🔴"
            jrnl_badge  = "✅" if a.get("journaled") else "⏳"
            fp = a.get("filters_passed", {})
            n_fp = sum(1 for f in fp.values() if f.get("pass", True)) if fp else "—"

            with st.container(border=True):
                col_a, col_b, col_c, col_d, col_e, col_f = st.columns([1.5,1,1,1.5,1,1])
                col_a.markdown(f"**{a['ticker']}** {trend_badge} {a['trend']}")
                col_b.markdown(f"RR **{a['rr']}**")
                col_c.markdown(f"Filters **{n_fp}/4**")
                col_d.markdown(f"Entry `{a['entry']}` → Target `{a['target']}`")
                col_e.markdown(f"🕒 {a['timestamp']}")
                col_f.markdown(f"{jrnl_badge} {'Logged' if a.get('journaled') else 'Pending'}")

        st.divider()
        if st.button("🗑️ Clear all alert history", type="secondary"):
            save_alerts([])
            st.success("Alert history cleared.")
            st.rerun()


# ═══════════════════════════════════════════════
# TAB 5 — TRADE JOURNAL
# ═══════════════════════════════════════════════
with TAB_JOURNAL:
    st.subheader("📓 Trade Journal — Auto Win/Loss Tracker")

    journal = load_journal()
    alerts  = load_alerts()
    stats   = journal_stats(journal)

    if stats:
        st.markdown("### 📊 Performance Dashboard")
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Total Trades",  stats["total"])
        m2.metric("Win Rate",      f"{stats['win_rate']}%")
        m3.metric("Wins/Losses",   f"{stats['wins']} / {stats['losses']}")
        m4.metric("Avg Win (R)",   stats["avg_win_r"])
        pf_display = "∞" if stats["profit_factor"] == float("inf") else stats["profit_factor"]
        m5.metric("Profit Factor", pf_display)
        m6.metric("Total R",       stats["total_r"])
        streak_emoji = "🔥" if stats["streak_type"] == "WIN" else "❄️"
        st.caption(f"{streak_emoji} Current streak: **{stats['streak']} {stats['streak_type']}** in a row")
        st.divider()

    unjournaled = [a for a in alerts if not a.get("journaled")]
    st.markdown("### ➕ Log Trade Outcome")

    if not unjournaled:
        st.info("No pending alerts to journal. Alerts appear here automatically from the scan.")
    else:
        selected_label = st.selectbox(
            "Select alert to journal",
            options=[f"{a['ticker']} | {a['trend']} | Entry {a['entry']} | {a['timestamp']}"
                     for a in unjournaled],
            key="journal_select"
        )
        sel = unjournaled[[
            f"{a['ticker']} | {a['trend']} | Entry {a['entry']} | {a['timestamp']}"
            for a in unjournaled
        ].index(selected_label)]

        with st.container(border=True):
            st.markdown(
                f"**{sel['ticker']}** · {sel['trend']} ({sel['strength']}) · "
                f"Entry `{sel['entry']}` · Stop `{sel['stop']}` · Target `{sel['target']}` · "
                f"R:R `{sel['rr']}` · Alerted: {sel['timestamp']}"
            )
            jc1, jc2 = st.columns(2)
            with jc1:
                exit_price = st.number_input("Exit Price ($)", min_value=0.01,
                    value=float(sel["entry"]), step=0.01, key="exit_price_input")
                outcome = st.radio("Outcome", ["WIN","LOSS","BREAKEVEN"],
                    horizontal=True, key="outcome_radio")
            with jc2:
                notes = st.text_area("Notes (setup quality, mistakes, lessons)",
                    placeholder="e.g. Held through news, stopped out early…",
                    key="journal_notes", height=100)

            risk = abs(sel["entry"] - sel["stop"])
            if risk > 0:
                preview_r = round((exit_price - sel["entry"]) / risk, 2) \
                            if sel["trend"] == "Bullish" \
                            else round((sel["entry"] - exit_price) / risk, 2)
                color = "green" if preview_r > 0 else "red"
                st.markdown(f"**Actual R: :{color}[{preview_r}R]**")

            if st.button("💾 Save to Journal", type="primary", key="save_journal_btn"):
                add_journal_trade(
                    alert_id=sel["id"], ticker=sel["ticker"], trend=sel["trend"],
                    entry=sel["entry"], stop=sel["stop"], target=sel["target"],
                    rr=sel["rr"], exit_price=exit_price,
                    outcome=outcome, notes=notes, setup_date=sel["timestamp"],
                )
                st.success(f"✅ Trade logged: {sel['ticker']} → {outcome}")
                st.rerun()

    st.divider()
    st.markdown("### 📋 Trade History")

    if not journal:
        st.info("No trades logged yet.")
    else:
        jf1, jf2, jf3 = st.columns(3)
        with jf1:
            j_ticker = st.selectbox("Ticker",
                ["All"] + sorted(set(j["ticker"] for j in journal)), key="j_ticker_filter")
        with jf2:
            j_outcome = st.selectbox("Outcome",
                ["All","WIN","LOSS","BREAKEVEN"], key="j_outcome_filter")
        with jf3:
            j_trend = st.selectbox("Direction",
                ["All","Bullish","Bearish"], key="j_trend_filter")

        filtered_j = journal
        if j_ticker  != "All": filtered_j = [j for j in filtered_j if j["ticker"]  == j_ticker]
        if j_outcome != "All": filtered_j = [j for j in filtered_j if j["outcome"] == j_outcome]
        if j_trend   != "All": filtered_j = [j for j in filtered_j if j["trend"]   == j_trend]

        for j in reversed(filtered_j):
            oe = {"WIN":"✅","LOSS":"❌","BREAKEVEN":"➖"}.get(j["outcome"],"❓")
            rc = "🟢" if j["actual_rr"] > 0 else ("🔴" if j["actual_rr"] < 0 else "⚪")
            with st.expander(
                f"{oe} {j['ticker']} · {j['trend']} · Actual: {rc} {j['actual_rr']}R · {j['closed']}"
            ):
                d1,d2,d3,d4 = st.columns(4)
                d1.metric("Entry",       f"${j['entry']}")
                d2.metric("Exit",        f"${j['exit_price']}")
                d3.metric("Planned R:R", j["planned_rr"])
                d4.metric("Actual R",    j["actual_rr"])
                st.caption(f"Stop: ${j['stop']} · Target: ${j['target']} · Alerted: {j['date']}")
                if j.get("notes"):
                    st.markdown(f"📝 *{j['notes']}*")
                if st.button("🗑️ Delete", key=f"del_{j['id']}", type="secondary"):
                    save_journal([x for x in journal if x["id"] != j["id"]])
                    al = load_alerts()
                    for a in al:
                        if a["id"] == j["id"]: a["journaled"] = False
                    save_alerts(al)
                    st.rerun()

        st.divider()
        if st.button("🗑️ Clear entire journal", type="secondary", key="clear_journal"):
            save_journal([])
            al = load_alerts()
            for a in al: a["journaled"] = False
            save_alerts(al)
            st.success("Journal cleared.")
            st.rerun()

    st.caption("⚠️ Not financial advice. Journal is for personal tracking only.")
