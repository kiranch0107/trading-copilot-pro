# trading_copilot_elite.py
# Single-file Streamlit app with:
#  - Rate-limit circuit breaker (tuned)
#  - Batched weekly prefetch and longer TTLs for slow-changing data
#  - Batched data fetch for watchlist
#  - Cached compute to avoid duplicate indicator work
#  - Option-chain fetches gated behind filter pass
# Paste into your environment and run with `streamlit run trading_copilot_elite.py`

import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import os
import json
import requests
import time
import threading
import logging
from datetime import datetime
from pathlib import Path
import pytz
from typing import Optional, Tuple, Dict

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("trading_copilot")

# ---------------------------
# PAGE CONFIG
# ---------------------------
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

# ---------------------------
# CONFIG (exposed in sidebar for quick tuning)
# ---------------------------
WATCHLIST = [
    "TSLA","NVDA","AAPL","MSFT","AMZN",
    "META","SPY"
]

FAST_MODE = True
SCAN_LIST = WATCHLIST[:5] if FAST_MODE else WATCHLIST

# Tunables (expose in sidebar)
st.sidebar.header("Scan Settings")
ADX_MIN = st.sidebar.number_input("ADX minimum", value=25, min_value=1, max_value=100)
EARNINGS_DAYS = st.sidebar.number_input("Earnings blackout days", value=3, min_value=0, max_value=30)
BUDGET_MAX = st.sidebar.number_input("Budget max (option mid)", value=2.00, min_value=0.01, step=0.1)
MIN_DTE = st.sidebar.number_input("Min DTE for options", value=7, min_value=1)
MIN_RR = st.sidebar.number_input("Min Reward/Risk", value=1.5, min_value=0.1)
MIN_ROWS = st.sidebar.number_input("Min history bars", value=50, min_value=10)
VOLUME_MULT = st.sidebar.number_input("Volume multiplier", value=1.0, min_value=0.1)

ALERT_LOG_FILE = Path("alert_history.json")
JOURNAL_FILE = Path("trade_journal.json")

# ---------------------------
# Persistence helpers
# ---------------------------
def _load(path: Path) -> list:
    try:
        if not path.exists():
            return []
        return json.loads(path.read_text())
    except (json.JSONDecodeError, Exception) as e:
        logger.exception("Failed to load %s: %s", path, e)
        return []

def _save(path: Path, data: list) -> None:
    try:
        path.write_text(json.dumps(data, indent=2, default=str))
    except Exception as e:
        logger.exception("Failed to save %s: %s", path, e)

def load_alerts() -> list:  return _load(ALERT_LOG_FILE)
def save_alerts(d: list):   _save(ALERT_LOG_FILE, d)
def load_journal() -> list: return _load(JOURNAL_FILE)
def save_journal(d: list):  _save(JOURNAL_FILE, d)

# ---------------------------
# Rate limiter encapsulation
# ---------------------------
class RateLimiter:
    """
    Simple global rate limiter to ensure a minimum gap between yfinance calls.
    Encapsulates lock and last-call timestamp for easier testing and reuse.
    """
    def __init__(self, min_gap: float = 0.35):
        self._min_gap = min_gap
        self._lock = threading.Lock()
        self._last_ts = 0.0

    def wait(self):
        with self._lock:
            elapsed = time.time() - self._last_ts
            if elapsed < self._min_gap:
                to_sleep = self._min_gap - elapsed
                time.sleep(to_sleep)
            self._last_ts = time.time()

_rate_limiter = RateLimiter(min_gap=0.35)

# ---------------------------
# Rate limit circuit breaker (tuned)
# ---------------------------
class RateLimitCircuitBreaker:
    """
    Tracks recent YF rate-limit events. When the number of rate-limit hits
    within a short window exceeds a threshold, the breaker trips and
    suppresses non-essential yfinance calls for `cooldown_seconds`.
    """
    def __init__(self, window_seconds: int = 120, threshold: int = 4, cooldown_seconds: int = 300):
        self.window_seconds = window_seconds
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self._events = []  # timestamps of recent rate-limit events
        self._tripped_until = 0.0
        self._lock = threading.Lock()

    def record_rate_limit(self):
        now = time.time()
        with self._lock:
            self._events.append(now)
            cutoff = now - self.window_seconds
            self._events = [t for t in self._events if t >= cutoff]
            if len(self._events) >= self.threshold:
                self._tripped_until = now + self.cooldown_seconds
                logger.warning("RateLimitCircuitBreaker tripped until %s", datetime.fromtimestamp(self._tripped_until))

    def is_tripped(self) -> bool:
        with self._lock:
            return time.time() < self._tripped_until

_rate_limit_breaker = RateLimitCircuitBreaker(window_seconds=120, threshold=4, cooldown_seconds=300)

# ---------------------------
# YFinance helpers with retry/backoff
# ---------------------------
_YF_RETRY_TRIES = 3
_YF_RETRY_DELAY = 2.0

def _is_rate_limit_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "too many requests" in msg or "rate limit" in msg or "429" in msg or "yf ratelimiterror" in msg

def _yf_download_with_retry(ticker: str, period: str, interval: str) -> pd.DataFrame:
    delay = _YF_RETRY_DELAY
    last_err = None
    for attempt in range(_YF_RETRY_TRIES):
        _rate_limiter.wait()
        try:
            df = yf.download(ticker, period=period, interval=interval, progress=False)
            return df
        except Exception as e:
            last_err = e
            if _is_rate_limit_error(e):
                logger.warning("Rate limited on yf.download(%s). Attempt %s/%s", ticker, attempt+1, _YF_RETRY_TRIES)
                _rate_limit_breaker.record_rate_limit()
                if attempt < _YF_RETRY_TRIES - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
            logger.exception("yf.download failed for %s: %s", ticker, e)
            raise
    if last_err:
        raise last_err
    return pd.DataFrame()

def get_data_with_error(ticker: str, period: str = "3mo", interval: str = "1d") -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """
    Fetch data and return (df, error_message). This surfaces why a fetch failed.
    """
    # If breaker is tripped, avoid calling yfinance for non-essential fetches
    if _rate_limit_breaker.is_tripped():
        return None, "Skipped due to Yahoo Finance rate-limit cooldown"

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
    df = df.dropna(subset=["Open","High","Low","Close","Volume"], how="any")
    if len(df) < MIN_ROWS:
        return None, f"Not enough trading history for '{ticker}' (need {MIN_ROWS}+ bars)."
    return df, None

@st.cache_data(ttl=600, show_spinner=False)
def get_data(ticker: str, period: str = "3mo", interval: str = "1d") -> Optional[pd.DataFrame]:
    df, err = get_data_with_error(ticker, period, interval)
    if err:
        logger.info("get_data: %s -> %s", ticker, err)
        return None
    return df

# ---------------------------
# Batched fetch for multiple tickers (watchlist) with breaker and fallback
# ---------------------------
@st.cache_data(ttl=300, show_spinner=False)
def batch_get_data(tickers: list[str], period: str = "3mo", interval: str = "1d") -> Dict[str, pd.DataFrame]:
    """
    Batched fetch with:
      - early filtering of obviously invalid tickers (leading $ or non-alphanumeric)
      - circuit-breaker check to avoid batch calls when YF is rate-limited
      - fallback to per-ticker fetch for resilience
    """
    if not tickers:
        return {}
    # quick filter: skip tickers that look like indices or invalid symbols
    cleaned = []
    for t in tickers:
        tstr = str(t).strip()
        if not tstr:
            continue
        if tstr.startswith("$") or " " in tstr or "/" in tstr:
            logger.info("Skipping suspicious ticker format: %s", tstr)
            continue
        cleaned.append(tstr.upper())

    if not cleaned:
        return {}

    # If breaker is tripped, avoid a batch call and fall back to per-ticker fetch
    if _rate_limit_breaker.is_tripped():
        logger.info("Rate-limit breaker tripped: using per-ticker fallback for %s", cleaned)
        result = {}
        for t in cleaned:
            d, _ = get_data_with_error(t, period, interval)
            if d is not None:
                result[t] = d
        return result

    # Try a single batched download
    try:
        _rate_limiter.wait()
        df = yf.download(cleaned, period=period, interval=interval, progress=False, group_by='ticker')
    except Exception as e:
        msg = str(e).lower()
        if _is_rate_limit_error(e):
            logger.warning("Batch yf.download rate-limited: %s", e)
            _rate_limit_breaker.record_rate_limit()
        else:
            logger.exception("Batch yf.download failed: %s", e)
        # fallback to per-ticker fetch
        result = {}
        for t in cleaned:
            d, _ = get_data_with_error(t, period, interval)
            if d is not None:
                result[t] = d
        return result

    result = {}
    if isinstance(df.columns, pd.MultiIndex):
        for t in cleaned:
            try:
                sub = df[t].dropna(subset=["Open","High","Low","Close","Volume"], how="any")
                if len(sub) >= MIN_ROWS:
                    result[t] = sub
            except Exception:
                continue
    else:
        if len(cleaned) == 1:
            sub = df.dropna(subset=["Open","High","Low","Close","Volume"], how="any")
            if len(sub) >= MIN_ROWS:
                result[cleaned[0]] = sub
    return result

# ---------------------------
# Indicators
# ---------------------------
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
    df["ADX"]       = ta.trend.adx(df["High"], df["Low"], df["Close"], window=14)
    return df.dropna(subset=["EMA20","EMA50","MACD","Signal","RSI","ATR","ADX"])

# Cached compute keyed by ticker + last index to avoid recomputing on reruns
@st.cache_data(ttl=600, show_spinner=False)
def compute_cached(ticker: str, df_serialized_key: str, df: pd.DataFrame) -> pd.DataFrame:
    """
    df_serialized_key should change when the underlying data changes (e.g., last timestamp).
    We pass the DataFrame to compute but use the key for cache identity.
    """
    return compute(df)

# ---------------------------
# ADX check
# ---------------------------
def check_adx(df: pd.DataFrame) -> Tuple[bool, float]:
    adx_val = float(df["ADX"].iloc[-1])
    return adx_val >= ADX_MIN, round(adx_val, 1)

# ---------------------------
# Weekly trend (multi-timeframe) - longer TTL and batch prefetch below
# ---------------------------
@st.cache_data(ttl=6*3600, show_spinner=False)
def get_weekly_trend(ticker: str) -> Optional[str]:
    # This function remains available for single-ticker lookups, but scans use the batched prefetch.
    if _rate_limit_breaker.is_tripped():
        logger.info("Skipping weekly trend for %s due to rate-limit breaker", ticker)
        return None
    try:
        _rate_limiter.wait()
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
    except Exception as e:
        if _is_rate_limit_error(e):
            _rate_limit_breaker.record_rate_limit()
            logger.warning("Rate limit while fetching weekly trend for %s", ticker)
            return None
        logger.exception("get_weekly_trend failed for %s: %s", ticker, e)
        return None

def check_weekly_alignment(daily_trend: str, weekly_trend: Optional[str]) -> Tuple[bool, str]:
    if weekly_trend is None:
        return False, "Weekly data unavailable"
    if daily_trend == weekly_trend:
        return True, f"Weekly {weekly_trend} ✓"
    return False, f"Daily {daily_trend} vs Weekly {weekly_trend} — misaligned"

# ---------------------------
# Batched weekly prefetch for scans (new)
# ---------------------------
@st.cache_data(ttl=6*3600, show_spinner=False)
def prefetch_weekly_for_tickers(tickers: list[str]) -> dict:
    """
    Batch-download weekly bars for multiple tickers and compute weekly EMA alignment.
    Returns dict[ticker] -> 'Bullish'|'Bearish'|None
    """
    if not tickers:
        return {}
    # Respect breaker
    if _rate_limit_breaker.is_tripped():
        return {t: None for t in tickers}

    try:
        _rate_limiter.wait()
        df = yf.download(tickers, period="2y", interval="1wk", progress=False, group_by='ticker')
    except Exception as e:
        logger.warning("Batch weekly fetch failed: %s", e)
        return {t: None for t in tickers}

    result = {}
    if isinstance(df.columns, pd.MultiIndex):
        for t in tickers:
            try:
                sub = df[t].dropna(subset=["Close"])
                if len(sub) < 20:
                    result[t] = None
                    continue
                sub["EMA10w"] = ta.trend.ema_indicator(sub["Close"], window=10)
                sub["EMA20w"] = ta.trend.ema_indicator(sub["Close"], window=20)
                sub = sub.dropna(subset=["EMA10w","EMA20w"])
                if sub.empty:
                    result[t] = None
                    continue
                price = float(sub["Close"].iloc[-1])
                ema10w = float(sub["EMA10w"].iloc[-1])
                ema20w = float(sub["EMA20w"].iloc[-1])
                if price > ema10w > ema20w:
                    result[t] = "Bullish"
                elif price < ema10w < ema20w:
                    result[t] = "Bearish"
                else:
                    result[t] = None
            except Exception:
                result[t] = None
    else:
        # single-ticker case
        t = tickers[0]
        try:
            sub = df.dropna(subset=["Close"])
            sub["EMA10w"] = ta.trend.ema_indicator(sub["Close"], window=10)
            sub["EMA20w"] = ta.trend.ema_indicator(sub["Close"], window=20)
            sub = sub.dropna(subset=["EMA10w","EMA20w"])
            price = float(sub["Close"].iloc[-1])
            ema10w = float(sub["EMA10w"].iloc[-1])
            ema20w = float(sub["EMA20w"].iloc[-1])
            if price > ema10w > ema20w:
                result[t] = "Bullish"
            elif price < ema10w < ema20w:
                result[t] = "Bearish"
            else:
                result[t] = None
        except Exception:
            result[t] = None
    return result

# ---------------------------
# Earnings blackout (robust parsing, longer TTL)
# ---------------------------
@st.cache_data(ttl=6*3600, show_spinner=False)
def get_next_earnings(ticker: str) -> Optional[str]:
    """
    Robust earnings fetch:
      - Respects the circuit breaker (suppresses calls when YF is rate-limited).
      - Catches YFRateLimitError and records it in the breaker.
      - Normalizes returned date using pd.to_datetime.
      - Returns None on any parse/fetch failure.
    """
    if _rate_limit_breaker.is_tripped():
        logger.info("Skipping earnings fetch for %s because rate-limit breaker is tripped", ticker)
        return None

    try:
        _rate_limiter.wait()
        t = yf.Ticker(ticker)
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
                val = cal["Earnings Date"].iloc[0]
                ts = pd.to_datetime(val, errors="coerce")
            else:
                first = cal.iloc[0].dropna().iloc[0] if not cal.empty else None
                ts = pd.to_datetime(first, errors="coerce")
        else:
            ts = pd.NaT
        if pd.isna(ts):
            return None
        return str(ts.date())
    except Exception as e:
        msg = str(e).lower()
        if _is_rate_limit_error(e):
            logger.warning("YF rate limit detected while fetching earnings for %s: %s", ticker, e)
            _rate_limit_breaker.record_rate_limit()
            return None
        logger.exception("get_next_earnings failed for %s: %s", ticker, e)
        return None

def check_earnings_blackout(ticker: str) -> Tuple[bool, str]:
    earnings_date_str = get_next_earnings(ticker)
    if earnings_date_str is None:
        return True, "Earnings date unknown — proceed with caution"
    try:
        earnings_dt = datetime.strptime(earnings_date_str, "%Y-%m-%d").date()
        today = datetime.now(pytz.timezone("America/New_York")).date()
        days_away = (earnings_dt - today).days
        if 0 <= days_away <= EARNINGS_DAYS:
            return False, f"⚠️ Earnings in {days_away}d ({earnings_date_str}) — signal blocked"
        elif days_away < 0:
            return True, f"Last earnings: {earnings_date_str}"
        else:
            return True, f"Next earnings: {earnings_date_str} ({days_away}d away)"
    except Exception as e:
        logger.exception("Earnings blackout check failed for %s: %s", ticker, e)
        return True, "Earnings check failed — proceed with caution"

# ---------------------------
# SPY regime
# ---------------------------
@st.cache_data(ttl=1800, show_spinner=False)
def get_spy_regime() -> dict:
    try:
        _rate_limiter.wait()
        df = yf.download("SPY", period="14mo", interval="1d", progress=False)
        if df is None or df.empty:
            return {"regime": "Unknown", "reasoning": "SPY data unavailable"}
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Close","High","Low"])
        df["SMA200"] = df["Close"].rolling(200).mean()
        df["ADX"]    = ta.trend.adx(df["High"], df["Low"], df["Close"], window=14)
        df = df.dropna(subset=["SMA200","ADX"])
        price   = float(df["Close"].iloc[-1])
        sma200  = float(df["SMA200"].iloc[-1])
        adx_val = float(df["ADX"].iloc[-1])
        above_200 = price > sma200
        trending  = adx_val >= 20
        if above_200 and trending:
            regime = "Bull"
            reasoning = f"SPY ${price:.0f} above 200-SMA ${sma200:.0f} (ADX {adx_val:.0f})"
        elif not above_200 and trending:
            regime = "Bear"
            reasoning = f"SPY ${price:.0f} below 200-SMA ${sma200:.0f} (ADX {adx_val:.0f})"
        else:
            regime = "Neutral"
            reasoning = f"SPY ${price:.0f} near 200-SMA ${sma200:.0f} — choppy (ADX {adx_val:.0f})"
        return {"regime": regime, "price": round(price,2), "sma200": round(sma200,2), "adx": round(adx_val,1), "reasoning": reasoning}
    except Exception as e:
        if _is_rate_limit_error(e):
            _rate_limit_breaker.record_rate_limit()
            logger.warning("Rate limit while fetching SPY regime")
            return {"regime": "Unknown", "reasoning": "Rate limited"}
        logger.exception("get_spy_regime failed: %s", e)
        return {"regime": "Unknown", "reasoning": str(e)}

def check_regime_alignment(daily_trend: str, spy_regime: dict) -> Tuple[bool, str]:
    regime = spy_regime.get("regime", "Unknown")
    if regime == "Unknown":
        return True, "Regime unknown — no filter applied"
    if daily_trend == "Bullish" and regime == "Bear":
        return False, f"Counter-regime: going Long in SPY Bear market"
    if daily_trend == "Bearish" and regime == "Bull":
        return False, f"Counter-regime: going Short in SPY Bull market"
    return True, f"Regime aligned: {daily_trend} in {regime} market ✓"

# ---------------------------
# Options engine (shared chain cache)
# ---------------------------
_OPT_RETRY_ATTEMPTS = 3
_OPT_RETRY_DELAY = 2.0
_OPT_EXPIRY_DELAY = 0.25
_OPT_MAX_EXPIRIES = 3

def _fetch_chain_with_retry(stock, expiry: str):
    delay = _OPT_RETRY_DELAY
    for attempt in range(_OPT_RETRY_ATTEMPTS):
        _rate_limiter.wait()
        try:
            return stock.option_chain(expiry)
        except Exception as e:
            msg = str(e).lower()
            if _is_rate_limit_error(e):
                logger.warning("Rate limited fetching option chain %s %s; attempt %s", getattr(stock, "ticker", "unknown"), expiry, attempt+1)
                _rate_limit_breaker.record_rate_limit()
                if attempt < _OPT_RETRY_ATTEMPTS - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
            logger.exception("Option chain fetch failed for %s expiry %s: %s", getattr(stock, "ticker", "unknown"), expiry, e)
            raise
    return None

@st.cache_data(ttl=900, show_spinner=False)
def get_full_chain_data(ticker: str, min_dte: int = MIN_DTE, max_expiries: int = _OPT_MAX_EXPIRIES) -> dict:
    try:
        stock = yf.Ticker(ticker)
        _rate_limiter.wait()
        try:
            all_expiries = stock.options
        except Exception as e:
            if _is_rate_limit_error(e):
                _rate_limit_breaker.record_rate_limit()
                logger.warning("Rate limited listing expiries for %s", ticker)
                return {"error": "Rate limited when listing expiries", "expiries": []}
            logger.exception("Failed to get options list for %s: %s", ticker, e)
            return {"error": f"Failed to list expiries: {e}", "expiries": []}
        if not all_expiries:
            return {"error": "No option chain available", "expiries": []}
        today = pd.Timestamp.today().normalize()
        result = []
        checked = 0
        for expiry in all_expiries:
            if checked >= max_expiries:
                break
            try:
                dte = (pd.Timestamp(expiry) - today).days
            except Exception:
                continue
            if dte < min_dte:
                continue
            checked += 1
            try:
                time.sleep(_OPT_EXPIRY_DELAY)
                chain = _fetch_chain_with_retry(stock, expiry)
                if chain is None:
                    continue
                calls = chain.calls.fillna(0)
                puts = chain.puts.fillna(0)
                result.append({"expiry": expiry, "dte": dte, "calls": calls, "puts": puts})
            except Exception as e:
                logger.exception("Skipping expiry %s for %s due to error: %s", expiry, ticker, e)
                continue
        if not result:
            return {"error": "No valid expiries found (all below MIN_DTE or fetch failed)", "expiries": []}
        return {"error": None, "expiries": result}
    except Exception as e:
        if _is_rate_limit_error(e):
            _rate_limit_breaker.record_rate_limit()
            return {"error": "Rate limited by Yahoo Finance — try again shortly (cached 15 min)", "expiries": []}
        logger.exception("get_full_chain_data failed for %s: %s", ticker, e)
        return {"error": f"Option chain fetch failed ({e})", "expiries": []}

def get_option_data(ticker: str, price: float, trend: str, strength: str) -> dict:
    chain_data = get_full_chain_data(ticker)
    if chain_data.get("error"):
        return {"error": chain_data["error"]}
    best = None
    best_score = 0
    for entry in chain_data["expiries"]:
        expiry, dte = entry["expiry"], entry["dte"]
        opts = entry["calls"] if trend == "Bullish" else entry["puts"]
        if opts.empty:
            continue
        if strength == "Strong":
            opts = opts[(opts["strike"] <= price*1.02) if trend=="Bullish" else (opts["strike"] >= price*0.98)]
        else:
            opts = opts[(opts["strike"] >= price*0.95) & (opts["strike"] <= price*1.05)]
        if opts.empty:
            continue
        opts = opts.copy()
        opts["spread"] = opts["ask"] - opts["bid"]
        opts["mid"] = (opts["ask"] + opts["bid"]) / 2
        valid = opts[(opts["mid"] > 0) & (opts["spread"]/opts["mid"] <= 0.15)]
        valid = valid[(valid["volume"] > 0) | (valid["openInterest"] > 0)]
        if valid.empty:
            continue
        valid = valid.copy()
        valid["liq"] = valid["volume"] + valid["openInterest"]
        valid["score"] = valid["liq"] / (1 + (valid["spread"] / (valid["mid"] + 1e-6)))
        top = valid.sort_values("score", ascending=False).iloc[0]
        if top["score"] > best_score:
            best = (top, expiry, dte)
            best_score = top["score"]
    if best is None:
        return {"error": "No liquid options found"}
    row, expiry, dte = best
    return {
        "label": "CALL" if trend=="Bullish" else "PUT",
        "strike": round(float(row["strike"]), 2),
        "expiry": expiry,
        "mid": round(float(row["mid"]), 2),
        "last_price": round(float(row.get("lastPrice", 0)), 2),
        "volume": int(row.get("volume", 0)),
        "oi": int(row.get("openInterest", 0)),
        "spread": round(float(row["spread"]), 2),
        "dte": dte,
        "is_budget": row["mid"] <= BUDGET_MAX,
    }

# ---------------------------
# Unusual activity scoring
# ---------------------------
UA_VOL_OI_RATIO_MIN = 2.0
UA_VOL_OI_RATIO_HIGH = 4.0
UA_PEER_MULTIPLE_MIN = 3.0
UA_MIN_VOLUME = 100

def _score_unusual_contract(row: pd.Series, peer_median_vol: float) -> dict:
    volume = float(row.get("volume", 0) or 0)
    oi = float(row.get("openInterest", 0) or 0)
    if volume < UA_MIN_VOLUME:
        return {"unusual": False}
    vol_oi_ratio = volume / oi if oi > 0 else (float("inf") if volume > 0 else 0)
    peer_ratio = volume / peer_median_vol if peer_median_vol > 0 else 0
    vol_oi_flag = vol_oi_ratio >= UA_VOL_OI_RATIO_MIN
    peer_flag = peer_ratio >= UA_PEER_MULTIPLE_MIN
    if not (vol_oi_flag or peer_flag):
        return {"unusual": False}
    if vol_oi_ratio >= UA_VOL_OI_RATIO_HIGH and peer_flag:
        severity = "Extreme"
    elif vol_oi_flag and peer_flag:
        severity = "High"
    else:
        severity = "Moderate"
    reasons = []
    if vol_oi_flag:
        reasons.append(f"Vol/OI {vol_oi_ratio:.1f}x")
    if peer_flag:
        reasons.append(f"PeerVol {peer_ratio:.1f}x")
    return {"unusual": True, "severity": severity, "reasons": "; ".join(reasons)}

# ---------------------------
# Alerts and journal
# ---------------------------
def log_alert(ticker, trend, strength, entry, stop, target, rr, price, filters_passed: dict) -> None:
    alerts = load_alerts()
    recent = [a for a in alerts if a["ticker"] == ticker]
    if recent:
        try:
            last_epoch = datetime.strptime(recent[-1]["timestamp"], "%Y-%m-%d %H:%M ET").timestamp()
            if time.time() - last_epoch < 600:
                logger.info("Skipping alert for %s due to cooldown", ticker)
                return
        except Exception:
            pass
    alerts.append({
        "id": f"{ticker}_{int(time.time())}",
        "timestamp": datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d %H:%M ET"),
        "ticker": ticker,
        "trend": trend,
        "strength": strength,
        "price": price,
        "entry": entry,
        "stop": stop,
        "target": target,
        "rr": rr,
        "filters_passed": filters_passed,
        "journaled": False,
    })
    save_alerts(alerts)

def add_journal_trade(alert_id, ticker, trend, entry, stop, target, rr, exit_price, outcome, notes, setup_date) -> None:
    journal = load_journal()
    risk = abs(entry - stop)
    pnl_r = round((exit_price - entry) / risk, 2) if trend == "Bullish" else round((entry - exit_price) / risk, 2)
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
    wins = [j for j in journal if j["outcome"] == "WIN"]
    losses = [j for j in journal if j["outcome"] == "LOSS"]
    be = [j for j in journal if j["outcome"] == "BREAKEVEN"]
    total = len(journal)
    wr = round(len(wins)/total*100, 1) if total else 0
    avg_win = round(sum(j["actual_rr"] for j in wins) / len(wins), 2) if wins else 0
    avg_loss = round(sum(j["actual_rr"] for j in losses) / len(losses), 2) if losses else 0
    total_r = round(sum(j["actual_rr"] for j in journal), 2)
    gp = sum(j["actual_rr"] for j in wins if j["actual_rr"] > 0)
    gl = abs(sum(j["actual_rr"] for j in losses if j["actual_rr"] < 0))
    pf = round(gp/gl, 2) if gl else float("inf")
    outcomes = [j["outcome"] for j in sorted(journal, key=lambda x: x["closed"])]
    streak = 0
    streak_type = outcomes[-1] if outcomes else ""
    for o in reversed(outcomes):
        if o == streak_type:
            streak += 1
        else:
            break
    return {"total":total,"wins":len(wins),"losses":len(losses),"breakeven":len(be),"win_rate":wr,"avg_win_r":avg_win,"avg_loss_r":avg_loss,"total_r":total_r,"profit_factor":pf,"streak":streak,"streak_type":streak_type}

# ---------------------------
# Telegram (unchanged but safe)
# ---------------------------
def send_telegram_alert(ticker: str, message: str) -> None:
    TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": message}, timeout=5
        )
    except Exception:
        logger.exception("Failed to send telegram alert")

# ---------------------------
# Market hours
# ---------------------------
def is_market_open() -> bool:
    try:
        tz = pytz.timezone("America/New_York")
        now = datetime.now(tz)
        if now.weekday() >= 5:
            return False
        return now.replace(hour=9,minute=30,second=0,microsecond=0) <= now <= now.replace(hour=16,minute=0,second=0,microsecond=0)
    except Exception:
        return False

# ---------------------------
# Watchlist scan (uses batched weekly prefetch and gates option fetch)
# ---------------------------
def scan_watchlist(tickers: list[str]):
    spy_regime = get_spy_regime()
    data_map = batch_get_data(tickers, period="3mo", interval="1d")
    weekly_map = prefetch_weekly_for_tickers(tickers)   # batch weekly prefetch
    results = []
    for t in tickers:
        df = data_map.get(t)
        if df is None:
            st.info(f"No data for {t} or not enough history")
            continue
        last_key = str(df.index[-1])
        dfc = compute_cached(t, last_key, df)
        price = float(dfc["Close"].iloc[-1])
        ema20 = float(dfc["EMA20"].iloc[-1])
        ema50 = float(dfc["EMA50"].iloc[-1])
        trend = "Bullish" if price > ema20 > ema50 else "Bearish" if price < ema20 < ema50 else "Neutral"
        adx_ok, adx_val = check_adx(dfc)
        weekly = weekly_map.get(t)   # use batch result
        weekly_ok, weekly_reason = check_weekly_alignment(trend, weekly)
        earnings_ok, earnings_reason = check_earnings_blackout(t)
        regime_ok, regime_reason = check_regime_alignment(trend, spy_regime)
        filters = {
            "adx": (adx_ok, f"ADX {adx_val}"),
            "weekly": (weekly_ok, weekly_reason),
            "earnings": (earnings_ok, earnings_reason),
            "regime": (regime_ok, regime_reason),
        }
        if all(v[0] for v in filters.values()):
            # Only now fetch option chain / pick contract
            opt = get_option_data(t, price, trend, "Strong")
            rr = None
            entry = price
            stop = price - dfc["ATR"].iloc[-1] if trend == "Bullish" else price + dfc["ATR"].iloc[-1]
            target = price + 2 * dfc["ATR"].iloc[-1] if trend == "Bullish" else price - 2 * dfc["ATR"].iloc[-1]
            try:
                rr = round(abs(target - entry) / max(1e-6, abs(entry - stop)), 2)
            except Exception:
                rr = None
            if opt and not opt.get("error") and (opt.get("is_budget") or rr and rr >= MIN_RR):
                log_alert(t, trend, "Strong", entry, stop, target, rr, price, filters)
                results.append((t, trend, price, opt))
        else:
            logger.info("Filters failed for %s: %s", t, {k:v[1] for k,v in filters.items() if not v[0]})
    return results

# ---------------------------
# Single-ticker lookup panel
# ---------------------------
st.sidebar.header("Single Ticker Lookup")
lookup_ticker = st.sidebar.text_input("Ticker symbol (e.g., AAPL)", value="", max_chars=10)
lookup_period = st.sidebar.selectbox("Period", options=["1mo","3mo","6mo","1y","2y"], index=1)
lookup_interval = st.sidebar.selectbox("Interval", options=["1d","1wk","1mo","1h","5m"], index=0)
lookup_strength = st.sidebar.selectbox("Option strength preference", options=["Strong","Normal"], index=0)

# Show breaker banner if tripped
if _rate_limit_breaker.is_tripped():
    st.warning("Yahoo Finance rate limits detected — some checks (earnings, weekly, options) are temporarily paused to avoid further rate limiting.")

if st.sidebar.button("Lookup ticker"):
    ticker = lookup_ticker.strip().upper()
    if not ticker:
        st.sidebar.error("Enter a ticker symbol first.")
    else:
        with st.spinner(f"Fetching data for {ticker}..."):
            df, err = get_data_with_error(ticker, period=lookup_period, interval=lookup_interval)
            if err:
                st.error(f"Data error: {err}")
            else:
                st.success(f"Data fetched: {len(df)} bars")
                last_key = str(df.index[-1])
                dfc = compute_cached(ticker, last_key, df)
                # Basic diagnostics
                st.subheader(f"{ticker} Diagnostics")
                col1, col2, col3 = st.columns(3)
                price = float(dfc["Close"].iloc[-1])
                col1.metric("Last Price", f"${price:.2f}", delta=None)
                col1.metric("ATR (14)", f"{dfc['ATR'].iloc[-1]:.4f}")
                col2.metric("EMA20", f"{dfc['EMA20'].iloc[-1]:.2f}")
                col2.metric("EMA50", f"{dfc['EMA50'].iloc[-1]:.2f}")
                adx_ok, adx_val = check_adx(dfc)
                col3.metric("ADX", f"{adx_val}", delta="OK" if adx_ok else "Low")
                # Trend and alignment
                ema20 = float(dfc["EMA20"].iloc[-1])
                ema50 = float(dfc["EMA50"].iloc[-1])
                trend = "Bullish" if price > ema20 > ema50 else "Bearish" if price < ema20 < ema50 else "Neutral"
                st.write(f"**Daily trend:** {trend}")
                weekly = get_weekly_trend(ticker)
                weekly_ok, weekly_reason = check_weekly_alignment(trend, weekly)
                st.write(f"**Weekly alignment:** {weekly or 'Unknown'} — {weekly_reason}")
                # Earnings
                earnings_ok, earnings_reason = check_earnings_blackout(ticker)
                st.write(f"**Earnings check:** {earnings_reason}")
                # SPY regime
                spy_regime = get_spy_regime()
                regime_ok, regime_reason = check_regime_alignment(trend, spy_regime)
                st.write(f"**SPY regime:** {spy_regime.get('regime')} — {spy_regime.get('reasoning')}")
                st.write(f"**Regime alignment:** {regime_reason}")
                # Option pick
                with st.spinner("Selecting option contract..."):
                    opt = get_option_data(ticker, price, trend, lookup_strength)
                    if opt.get("error"):
                        st.warning(f"Option pick: {opt.get('error')}")
                    else:
                        st.write("**Best option contract (scored)**")
                        st.write(f"Type: {opt.get('label')}  Strike: {opt.get('strike')}  Expiry: {opt.get('expiry')}  Mid: {opt.get('mid')}")
                        st.write(f"Volume: {opt.get('volume')}  OI: {opt.get('oi')}  Spread: {opt.get('spread')}")
                        st.write(f"Budget-friendly: {'Yes' if opt.get('is_budget') else 'No'}")
                # Unusual activity quick scan (uses first expiry if available)
                chain = get_full_chain_data(ticker)
                if chain.get("error"):
                    st.info(f"Options chain: {chain.get('error')}")
                else:
                    st.write("**Unusual activity scan (top strikes)**")
                    ua_rows = []
                    for e in chain["expiries"]:
                        df_calls = e["calls"]
                        df_puts = e["puts"]
                        for side_df, side in ((df_calls, "CALL"), (df_puts, "PUT")):
                            if side_df.empty:
                                continue
                            peer_med = side_df["volume"].median() if "volume" in side_df.columns else 0
                            top = side_df.sort_values("volume", ascending=False).head(5)
                            for _, r in top.iterrows():
                                score = _score_unusual_contract(r, peer_med)
                                if score.get("unusual"):
                                    ua_rows.append({
                                        "expiry": e["expiry"], "dte": e["dte"], "side": side,
                                        "strike": r.get("strike"), "vol": int(r.get("volume",0)),
                                        "oi": int(r.get("openInterest",0)), "severity": score.get("severity"),
                                        "reasons": score.get("reasons")
                                    })
                    if not ua_rows:
                        st.write("No unusual activity detected (per current heuristics).")
                    else:
                        ua_df = pd.DataFrame(ua_rows).sort_values(["severity","vol"], ascending=[False, False])
                        st.dataframe(ua_df.reset_index(drop=True))
                # Quick action buttons
                st.markdown("---")
                st.write("Actions")
                col_a, col_b = st.columns(2)
                if col_a.button("Log alert for this ticker"):
                    entry = price
                    stop = price - dfc["ATR"].iloc[-1] if trend == "Bullish" else price + dfc["ATR"].iloc[-1]
                    target = price + 2 * dfc["ATR"].iloc[-1] if trend == "Bullish" else price - 2 * dfc["ATR"].iloc[-1]
                    rr = round(abs(target - entry) / max(1e-6, abs(entry - stop)), 2)
                    filters = {"adx": (adx_ok, f"ADX {adx_val}"), "weekly": (weekly_ok, weekly_reason), "earnings": (earnings_ok, earnings_reason)}
                    log_alert(ticker, trend, "Manual", entry, stop, target, rr, price, filters)
                    st.success("Alert logged to alert_history.json")
                if col_b.button("Send test Telegram message"):
                    msg = f"Test alert for {ticker} {trend} ${price:.2f}"
                    send_telegram_alert(ticker, msg)
                    st.info("Telegram send attempted (check bot/chat settings).")

# ---------------------------
# Streamlit main controls
# ---------------------------
st.sidebar.header("Actions")
if st.sidebar.button("Run scan now"):
    with st.spinner("Scanning..."):
        res = scan_watchlist(SCAN_LIST)
        st.success(f"Scan complete — {len(res)} alerts logged")
        for r in res:
            t, trend, price, opt = r
            st.write(f"Alert: {t} {trend} ${price:.2f} — Option: {opt.get('label')} {opt.get('strike')} exp {opt.get('expiry')} mid {opt.get('mid')}")

st.sidebar.markdown("---")
st.sidebar.header("Alerts & Journal")
alerts = load_alerts()
journal = load_journal()
st.sidebar.write(f"Alerts stored: {len(alerts)}")
st.sidebar.write(f"Journal entries: {len(journal)}")

st.header("Recent Alerts")
for a in sorted(alerts, key=lambda x: x["timestamp"], reverse=True)[:20]:
    st.markdown(f"**{a['timestamp']}** — {a['ticker']} — {a['trend']} — RR {a.get('rr')}")

st.header("Journal Stats")
stats = journal_stats(journal)
st.write(stats)

# ---------------------------
# End of file
# ---------------------------
