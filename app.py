# trading_copilot_elite.py
# Single-file Streamlit app with:
#  - Rate-limit circuit breaker (tuned)
#  - Batched weekly prefetch and longer TTLs for slow-changing data
#  - Batched data fetch for watchlist
#  - Cached compute to avoid duplicate indicator work
#  - Option-chain fetches gated behind filter pass

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
import numpy as np
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
    page_icon="🤖",
    layout="wide",
)

st.markdown(
    """
<style>
    .filter-pass  { background:#022c22; border-left:3px solid #22c55e; padding:6px 10px; border-radius:5px; margin:3px 0; font-size:0.85em; }
    .filter-fail  { background:#2b0d0d; border-left:3px solid #ef4444; padding:6px 10px; border-radius:5px; margin:3px 0; font-size:0.85em; }
    .filter-warn  { background:#2b2000; border-left:3px solid #f59e0b; padding:6px 10px; border-radius:5px; margin:3px 0; font-size:0.85em; }
</style>
""",
    unsafe_allow_html=True,
)

st.title("🤖 Trading Copilot ELITE")
st.caption("Swing · Options · Alerts · Journal · ADX · Multi-TF · Earnings Guard · Regime Filter")

# ---------------------------
# CONFIG
# ---------------------------
WATCHLIST = [
    "TSLA", "NVDA", "AAPL", "MSFT", "AMZN",
    "META", "SPY"
]

FAST_MODE = True
SCAN_LIST = WATCHLIST[:5] if FAST_MODE else WATCHLIST

st.sidebar.header("Scan Settings")
ADX_MIN = st.sidebar.number_input("ADX minimum", value=25, min_value=1, max_value=100)
EARNINGS_DAYS = st.sidebar.number_input("Earnings blackout days", value=3, min_value=0, max_value=30)
BUDGET_MAX = st.sidebar.number_input("Budget max (option mid)", value=2.00, min_value=0.01, step=0.1)
MIN_DTE = st.sidebar.number_input("Min DTE for options", value=0, min_value=0, max_value=30)
MAX_DTE = st.sidebar.number_input("Max DTE for options", value=30, min_value=0, max_value=30)
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
        path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.exception("Failed to save %s: %s", path, e)

def load_alerts() -> list:
    return _load(ALERT_LOG_FILE)

def save_alerts(alerts: list) -> None:
    _save(ALERT_LOG_FILE, alerts)

def load_journal() -> list:
    return _load(JOURNAL_FILE)

def save_journal(journal: list) -> None:
    _save(JOURNAL_FILE, journal)

# ---------------------------
# Rate limit circuit breaker
# ---------------------------
class RateLimitCircuitBreaker:
    def __init__(self, cooldown_seconds: int = 900, max_events: int = 3, window_seconds: int = 900):
        self.cooldown_seconds = cooldown_seconds
        self.max_events = max_events
        self.window_seconds = window_seconds
        self.events = []
        self.tripped_until = 0
        self.lock = threading.Lock()

    def record_rate_limit(self):
        with self.lock:
            now = time.time()
            self.events = [e for e in self.events if now - e < self.window_seconds]
            self.events.append(now)
            if len(self.events) >= self.max_events:
                self.tripped_until = now + self.cooldown_seconds
                logger.warning("Rate limit breaker TRIPPED for %s seconds", self.cooldown_seconds)

    def is_tripped(self) -> bool:
        with self.lock:
            now = time.time()
            if now >= self.tripped_until:
                return False
            return True

_rate_limit_breaker = RateLimitCircuitBreaker()

def _is_rate_limit_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "too many requests" in msg or "rate limit" in msg

# ---------------------------
# Simple rate limiter
# ---------------------------
class SimpleRateLimiter:
    def __init__(self, min_interval: float = 0.5):
        self.min_interval = min_interval
        self.last_call = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            elapsed = now - self.last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self.last_call = time.time()

_rate_limiter = SimpleRateLimiter(min_interval=0.5)

# ---------------------------
# Data fetch helpers
# ---------------------------
@st.cache_data(ttl=900, show_spinner=False)
def _yf_download_with_retry(ticker: str, period: str, interval: str):
    delay = 1.0
    for attempt in range(3):
        try:
            _rate_limiter.wait()
            df = yf.download(ticker, period=period, interval=interval, progress=False)
            if df is None or df.empty:
                raise ValueError("Empty dataframe")
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df
        except Exception as e:
            if _is_rate_limit_error(e):
                _rate_limit_breaker.record_rate_limit()
            logger.warning("YF download error for %s (attempt %s): %s", ticker, attempt + 1, e)
            time.sleep(delay)
            delay *= 2
    return None

def get_data_with_error(ticker: str, period: str = "3mo", interval: str = "1d") -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    if _rate_limit_breaker.is_tripped():
        return None, "Rate limit breaker tripped — data fetch temporarily paused"
    df = _yf_download_with_retry(ticker, period, interval)
    if df is None or df.empty:
        return None, "No data or fetch failed"
    if len(df) < MIN_ROWS:
        return None, f"Not enough history ({len(df)} < {MIN_ROWS})"
    return df, None

@st.cache_data(ttl=900, show_spinner=False)
def batch_get_data(tickers: list, period: str = "3mo", interval: str = "1d") -> Dict[str, Optional[pd.DataFrame]]:
    result = {}
    for t in tickers:
        df = _yf_download_with_retry(t, period, interval)
        if df is None or df.empty or len(df) < MIN_ROWS:
            result[t] = None
        else:
            result[t] = df
    return result

# ---------------------------
# Indicator compute
# ---------------------------
def compute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["EMA20"] = ta.trend.ema_indicator(df["Close"], window=20)
    df["EMA50"] = ta.trend.ema_indicator(df["Close"], window=50)
    macd = ta.trend.macd(df["Close"])
    df["MACD"] = macd
    df["Signal"] = ta.trend.macd_signal(df["Close"])
    df["RSI"] = ta.momentum.rsi(df["Close"], window=14)
    df["ATR"] = ta.volatility.average_true_range(df["High"], df["Low"], df["Close"], window=14)
    bb = ta.volatility.BollingerBands(df["Close"], window=20)
    df["BB_UP"] = bb.bollinger_hband()
    df["BB_LO"] = bb.bollinger_lband()
    df["VOL_AVG20"] = df["Volume"].rolling(20).mean()
    df["ADX"] = ta.trend.adx(df["High"], df["Low"], df["Close"], window=14)
    return df.dropna(subset=["EMA20", "EMA50", "MACD", "Signal", "RSI", "ATR", "ADX"])

@st.cache_data(ttl=600, show_spinner=False)
def compute_cached(ticker: str, df_serialized_key: str, df: pd.DataFrame) -> pd.DataFrame:
    return compute(df)

# ---------------------------
# ADX check
# ---------------------------
def check_adx(df: pd.DataFrame) -> Tuple[bool, float]:
    adx_val = float(df["ADX"].iloc[-1])
    return adx_val >= ADX_MIN, round(adx_val, 1)

# ---------------------------
# Weekly trend
# ---------------------------
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_weekly_trend(ticker: str) -> Optional[str]:
    if _rate_limit_breaker.is_tripped():
        logger.info("Skipping weekly trend for %s due to rate-limit breaker", ticker)
        return None
    df = _yf_download_with_retry(ticker, period="1y", interval="1wk")
    if df is None or df.empty or len(df) < 20:
        return None
    sub = df.dropna(subset=["Close"])
    sub["EMA10w"] = ta.trend.ema_indicator(sub["Close"], window=10)
    sub["EMA20w"] = ta.trend.ema_indicator(sub["Close"], window=20)
    sub = sub.dropna(subset=["EMA10w", "EMA20w"])
    price = float(sub["Close"].iloc[-1])
    ema10w = float(sub["EMA10w"].iloc[-1])
    ema20w = float(sub["EMA20w"].iloc[-1])
    if price > ema10w > ema20w:
        return "Bullish"
    elif price < ema10w < ema20w:
        return "Bearish"
    else:
        return None

@st.cache_data(ttl=6 * 3600, show_spinner=False)
def prefetch_weekly_for_tickers(tickers: list) -> Dict[str, Optional[str]]:
    if _rate_limit_breaker.is_tripped():
        logger.info("Skipping weekly prefetch due to rate-limit breaker")
        return {t: None for t in tickers}
    result = {}
    for t in tickers:
        df = _yf_download_with_retry(t, period="1y", interval="1wk")
        if df is None or df.empty or len(df) < 20:
            result[t] = None
            continue
        try:
            sub = df.dropna(subset=["Close"])
            sub["EMA10w"] = ta.trend.ema_indicator(sub["Close"], window=10)
            sub["EMA20w"] = ta.trend.ema_indicator(sub["Close"], window=20)
            sub = sub.dropna(subset=["EMA10w", "EMA20w"])
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

def check_weekly_alignment(daily_trend: str, weekly_trend: Optional[str]) -> Tuple[bool, str]:
    if weekly_trend is None:
        return False, "Weekly trend unknown or neutral"
    if daily_trend == "Bullish" and weekly_trend == "Bullish":
        return True, "Daily & weekly both Bullish ✓"
    if daily_trend == "Bearish" and weekly_trend == "Bearish":
        return True, "Daily & weekly both Bearish ✓"
    return False, f"Daily {daily_trend} vs weekly {weekly_trend} — misaligned"

# ---------------------------
# FIXED: Earnings blackout (robust parsing)
# ---------------------------
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_next_earnings(ticker: str) -> Optional[str]:
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
            raw = cal.get("Earnings Date")
        else:
            if "Earnings Date" not in cal.index:
                return None
            raw = cal.loc["Earnings Date"].values[0]

        if raw is None:
            return None

        if isinstance(raw, (list, tuple, pd.Series)):
            raw = raw[0]
        if isinstance(raw, np.ndarray):
            raw = raw.item()

        dt = pd.to_datetime(raw).date()
        return dt.strftime("%Y-%m-%d")

    except Exception as e:
        if _is_rate_limit_error(e):
            _rate_limit_breaker.record_rate_limit()
        logger.exception("get_next_earnings failed for %s: %s", ticker, e)
        return None

def check_earnings_blackout(ticker: str) -> Tuple[bool, str]:
    earnings_date_str = get_next_earnings(ticker)
    if earnings_date_str is None:
        return True, "No earnings date found — no blackout"
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
        df = df.dropna(subset=["Close", "High", "Low"])
        df["SMA200"] = df["Close"].rolling(200).mean()
        df["ADX"] = ta.trend.adx(df["High"], df["Low"], df["Close"], window=14)
        df = df.dropna(subset=["SMA200", "ADX"])
        price = float(df["Close"].iloc[-1])
        sma200 = float(df["SMA200"].iloc[-1])
        adx_val = float(df["ADX"].iloc[-1])
        above_200 = price > sma200
        trending = adx_val >= 20
        if above_200 and trending:
            regime = "Bull"
            reasoning = f"SPY ${price:.0f} above 200-SMA ${sma200:.0f} (ADX {adx_val:.0f})"
        elif not above_200 and trending:
            regime = "Bear"
            reasoning = f"SPY ${price:.0f} below 200-SMA ${sma200:.0f} (ADX {adx_val:.0f})"
        else:
            regime = "Neutral"
            reasoning = f"SPY ${price:.0f} near 200-SMA ${sma200:.0f} — choppy (ADX {adx_val:.0f})"
        return {
            "regime": regime,
            "price": round(price, 2),
            "sma200": round(sma200, 2),
            "adx": round(adx_val, 1),
            "reasoning": reasoning,
        }
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
        return False, "Counter-regime: going Long in SPY Bear market"
    if daily_trend == "Bearish" and regime == "Bull":
        return False, "Counter-regime: going Short in SPY Bull market"
    return True, f"Regime aligned: {daily_trend} in {regime} market ✓"

# ---------------------------
# Options engine (0–30 DTE, fixed DTE calc)
# ---------------------------
_OPT_RETRY_ATTEMPTS = 3
_OPT_RETRY_DELAY = 2.0
_OPT_EXPIRY_DELAY = 0.25
_OPT_MAX_EXPIRIES = 3

@st.cache_data(ttl=900, show_spinner=False)
def get_full_chain_data(stock: str) -> dict:
    if _rate_limit_breaker.is_tripped():
        return {"error": "Rate limit breaker tripped — options chain temporarily paused"}
    try:
        _rate_limiter.wait()
        t = yf.Ticker(stock)
        expiries = t.options
        if not expiries:
            return {"error": "No expiries"}
        expiries = expiries[:_OPT_MAX_EXPIRIES]
        chain_data = []
        for expiry in expiries:
            time.sleep(_OPT_EXPIRY_DELAY)
            oc = t.option_chain(expiry)
            df_calls = oc.calls
            df_puts = oc.puts
            for df_side in (df_calls, df_puts):
                if "bid" in df_side.columns and "ask" in df_side.columns:
                    df_side["mid"] = (df_side["bid"] + df_side["ask"]) / 2.0
                    df_side["spread"] = (df_side["ask"] - df_side["bid"]).abs()
            chain_data.append({"expiry": expiry, "calls": df_calls, "puts": df_puts})
        return {"expiries": chain_data}
    except Exception as e:
        if _is_rate_limit_error(e):
            _rate_limit_breaker.record_rate_limit()
        logger.exception("get_full_chain_data failed for %s: %s", stock, e)
        return {"error": str(e)}

def get_option_data(stock: str, price: float, trend: str, strength: str) -> dict:
    chain = get_full_chain_data(stock)
    if chain.get("error"):
        return {"error": chain.get("error")}

    best = None
    best_score = -1e9

    for e in chain["expiries"]:
        expiry = e["expiry"]
        df_calls = e["calls"]
        df_puts = e["puts"]

        side_df = df_calls if trend == "Bullish" else df_puts
        if side_df is None or side_df.empty:
            continue

        side_df = side_df.copy()

        if "bid" in side_df.columns and "ask" in side_df.columns:
            side_df["mid"] = (side_df["bid"] + side_df["ask"]) / 2.0
            side_df["spread"] = (side_df["ask"] - side_df["bid"]).abs()
        else:
            continue

        if "mid" not in side_df.columns or "spread" not in side_df.columns:
            continue

        # FIXED: scalar expiry → date, then DTE
        try:
            exp_dt = pd.to_datetime(expiry).date()
        except Exception:
            # fallback: assume expiry is already date-like string
            exp_dt = datetime.strptime(str(expiry), "%Y-%m-%d").date()

        dte_val = (exp_dt - datetime.now().date()).days
        side_df["dte"] = int(max(0, dte_val))

        side_df = side_df[(side_df["dte"] >= MIN_DTE) & (side_df["dte"] <= MAX_DTE)]
        if side_df.empty:
            continue

        side_df = side_df.dropna(subset=["mid", "spread"])

        side_df["score"] = 0.0
        side_df["score"] += -side_df["spread"]

        if "volume" in side_df.columns:
            side_df["score"] += (side_df["volume"].fillna(0) / 1000.0)

        side_df["score"] += (BUDGET_MAX - side_df["mid"]).clip(lower=-10, upper=10)

        top = side_df.sort_values("score", ascending=False).iloc[0]

        if top["score"] > best_score:
            best = (top, expiry, int(top["dte"]))
            best_score = top["score"]

    if best is None:
        return {"error": "No liquid options found"}

    row, expiry, dte = best

    return {
        "label": "CALL" if trend == "Bullish" else "PUT",
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
    vol_oi_ratio = volume / max(1.0, oi)
    peer_ratio = volume / max(1.0, peer_median_vol)
    vol_oi_flag = vol_oi_ratio >= UA_VOL_OI_RATIO_MIN
    peer_flag = peer_ratio >= UA_PEER_MULTIPLE_MIN
    if not (vol_oi_flag or peer_flag):
        return {"unusual": False}
    severity = "Moderate"
    if vol_oi_ratio >= UA_VOL_OI_RATIO_HIGH or peer_ratio >= UA_PEER_MULTIPLE_MIN * 2:
        severity = "High"
    reasons = []
    if vol_oi_flag:
        reasons.append(f"Vol/OI {vol_oi_ratio:.1f}x")
    if peer_flag:
        reasons.append(f"PeerVol {peer_ratio:.1f}x")
    return {"unusual": True, "severity": severity, "reasons": "; ".join(reasons)}

# ---------------------------
# Alerts & journal
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
        "id": alert_id,
        "date": setup_date,
        "closed": datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d %H:%M ET"),
        "ticker": ticker,
        "trend": trend,
        "entry": entry,
        "stop": stop,
        "target": target,
        "planned_rr": rr,
        "exit_price": exit_price,
        "outcome": outcome,
        "actual_rr": pnl_r,
        "notes": notes,
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
    wr = round(len(wins) / total * 100, 1) if total else 0
    avg_win = round(sum(j["actual_rr"] for j in wins) / len(wins), 2) if wins else 0
    avg_loss = round(sum(j["actual_rr"] for j in losses) / len(losses), 2) if losses else 0
    pf = round(
        (sum(j["actual_rr"] for j in wins) / max(1e-6, abs(sum(j["actual_rr"] for j in losses))))
        if losses else 0,
        2,
    )
    return {
        "trades": total,
        "win_rate_%": wr,
        "avg_win_R": avg_win,
        "avg_loss_R": avg_loss,
        "profit_factor": pf,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(be),
    }

# ---------------------------
# Telegram
# ---------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def send_telegram_alert(ticker: str, message: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.info("Telegram not configured")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        logger.exception("Telegram send failed: %s", e)

# ---------------------------
# Swing signal generator (options-aware)
# ---------------------------
def generate_swing_signal(
    ticker: str,
    dfc: pd.DataFrame,
    weekly_trend: Optional[str],
    spy_regime: dict,
) -> dict:
    price = float(dfc["Close"].iloc[-1])
    ema20 = float(dfc["EMA20"].iloc[-1])
    ema50 = float(dfc["EMA50"].iloc[-1])
    atr = float(dfc["ATR"].iloc[-1])

    if price > ema20 > ema50:
        trend = "Bullish"
    elif price < ema20 < ema50:
        trend = "Bearish"
    else:
        trend = "Neutral"

    adx_ok, adx_val = check_adx(dfc)
    weekly_ok, weekly_reason = check_weekly_alignment(trend, weekly_trend)
    earnings_ok, earnings_reason = check_earnings_blackout(ticker)
    regime_ok, regime_reason = check_regime_alignment(trend, spy_regime)

    filters = {
        "adx": (adx_ok, f"ADX {adx_val}"),
        "weekly": (weekly_ok, weekly_reason),
        "earnings": (earnings_ok, earnings_reason),
        "regime": (regime_ok, regime_reason),
    }

    if trend == "Neutral" or not all(v[0] for v in filters.values()):
        return {
            "ticker": ticker,
            "direction": "NONE",
            "trend": trend,
            "price": price,
            "filters": filters,
            "underlying": None,
            "options": None,
            "rr": None,
            "confidence": "None",
            "reasoning": [v[1] for k, v in filters.items() if not v[0]],
        }

    entry = price
    if trend == "Bullish":
        stop = price - atr
        target = price + 2 * atr
    else:
        stop = price + atr
        target = price - 2 * atr
    rr = round(abs(target - entry) / max(1e-6, abs(entry - stop)), 2)

    opt = get_option_data(ticker, price, trend, "Strong")
    options_info = None if opt.get("error") else opt

    confidence = "High"
    reasoning = [
        f"Daily trend: {trend}",
        f"Weekly: {weekly_trend or 'Unknown'} — {weekly_reason}",
        f"Earnings: {earnings_reason}",
        f"SPY regime: {spy_regime.get('regime')} — {spy_regime.get('reasoning')}",
        f"ADX {adx_val} (min {ADX_MIN})",
    ]

    return {
        "ticker": ticker,
        "direction": "LONG" if trend == "Bullish" else "SHORT",
        "trend": trend,
        "price": price,
        "filters": filters,
        "underlying": {
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            "atr": round(atr, 4),
        },
        "options": options_info,
        "rr": rr,
        "confidence": confidence,
        "reasoning": reasoning,
    }

# ---------------------------
# Watchlist scan
# ---------------------------
def scan_watchlist(tickers: list):
    spy_regime = get_spy_regime()
    data_map = batch_get_data(tickers, period="3mo", interval="1d")
    weekly_map = prefetch_weekly_for_tickers(tickers)
    results = []
    for t in tickers:
        df = data_map.get(t)
        if df is None:
            st.info(f"No data for {t} or not enough history")
            continue
        last_key = str(df.index[-1])
        dfc = compute_cached(t, last_key, df)
        weekly_trend = weekly_map.get(t)
        signal = generate_swing_signal(t, dfc, weekly_trend, spy_regime)

        if signal["direction"] == "NONE":
            continue

        opt = signal["options"]
        rr = signal["rr"]
        price = signal["price"]
        trend = signal["trend"]

        if opt and not opt.get("error") and (opt.get("is_budget") or (rr is not None and rr >= MIN_RR)):
            log_alert(
                t,
                trend,
                "Strong",
                signal["underlying"]["entry"],
                signal["underlying"]["stop"],
                signal["underlying"]["target"],
                rr,
                price,
                signal["filters"],
            )
            results.append((t, trend, price, opt))
    return results

# ---------------------------
# Single-ticker lookup
# ---------------------------
st.sidebar.header("Single Ticker Lookup")
lookup_ticker = st.sidebar.text_input("Ticker symbol (e.g., AAPL)", value="", max_chars=10)
lookup_period = st.sidebar.selectbox("Period", options=["1mo", "3mo", "6mo", "1y", "2y"], index=1)
lookup_interval = st.sidebar.selectbox("Interval", options=["1d", "1wk", "1mo", "1h", "5m"], index=0)
lookup_strength = st.sidebar.selectbox("Option strength preference", options=["Strong", "Normal"], index=0)

if _rate_limit_breaker.is_tripped():
    st.warning("Yahoo Finance rate limits detected — some checks (earnings, weekly, options) are temporarily paused.")

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

                st.subheader(f"{ticker} Diagnostics")
                col1, col2, col3 = st.columns(3)
                price = float(dfc["Close"].iloc[-1])
                col1.metric("Last Price", f"${price:.2f}")
                col1.metric("ATR (14)", f"{dfc['ATR'].iloc[-1]:.4f}")
                col2.metric("EMA20", f"{dfc['EMA20'].iloc[-1]:.2f}")
                col2.metric("EMA50", f"{dfc['EMA50'].iloc[-1]:.2f}")
                adx_ok, adx_val = check_adx(dfc)
                col3.metric("ADX", f"{adx_val}", delta="OK" if adx_ok else "Low")

                ema20 = float(dfc["EMA20"].iloc[-1])
                ema50 = float(dfc["EMA50"].iloc[-1])
                trend = "Bullish" if price > ema20 > ema50 else "Bearish" if price < ema20 < ema50 else "Neutral"
                st.write(f"**Daily trend:** {trend}")

                weekly = get_weekly_trend(ticker)
                weekly_ok, weekly_reason = check_weekly_alignment(trend, weekly)
                st.write(f"**Weekly alignment:** {weekly or 'Unknown'} — {weekly_reason}")

                earnings_ok, earnings_reason = check_earnings_blackout(ticker)
                st.write(f"**Earnings check:** {earnings_reason}")

                spy_regime = get_spy_regime()
                regime_ok, regime_reason = check_regime_alignment(trend, spy_regime)
                st.write(f"**SPY regime:** {spy_regime.get('regime')} — {spy_regime.get('reasoning')}")
                st.write(f"**Regime alignment:** {regime_reason}")

                st.subheader("Swing Signal (Daily + Weekly, Options-aware)")
                swing_signal = generate_swing_signal(ticker, dfc, weekly, spy_regime)
                if swing_signal["direction"] == "NONE":
                    st.info("No high-probability swing signal — filters not fully aligned.")
                    for k, (ok, reason) in swing_signal["filters"].items():
                        css_class = "filter-pass" if ok else "filter-fail"
                        st.markdown(f"<div class='{css_class}'><strong>{k}:</strong> {reason}</div>", unsafe_allow_html=True)
                else:
                    u = swing_signal["underlying"]
                    st.markdown(
                        f"**Direction:** {swing_signal['direction']}  "
                        f"Entry: ${u['entry']:.2f}  Stop: ${u['stop']:.2f}  Target: ${u['target']:.2f}  "
                        f"RR: {swing_signal['rr']}"
                    )
                    for k, (ok, reason) in swing_signal["filters"].items():
                        css_class = "filter-pass" if ok else "filter-pass"
                        st.markdown(f"<div class='{css_class}'><strong>{k}:</strong> {reason}</div>", unsafe_allow_html=True)

                    opt = swing_signal["options"]
                    if opt:
                        st.write("**Best option contract (scored)**")
                        st.write(
                            f"Type: {opt.get('label')}  Strike: {opt.get('strike')}  "
                            f"Expiry: {opt.get('expiry')}  Mid: {opt.get('mid')}"
                        )
                        st.write(
                            f"Volume: {opt.get('volume')}  OI: {opt.get('oi')}  Spread: {opt.get('spread')}  "
                            f"Budget-friendly: {'Yes' if opt.get('is_budget') else 'No'}"
                        )
                    else:
                        st.warning("No suitable options contract found for this swing setup.")

                col_a, col_b = st.columns(2)
                if col_a.button("Log manual alert for this ticker"):
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
# Main controls
# ---------------------------
st.sidebar.header("Actions")
if st.sidebar.button("Run scan now"):
    with st.spinner("Scanning..."):
        res = scan_watchlist(SCAN_LIST)
        st.success(f"Scan complete — {len(res)} alerts logged")
        for r in res:
            t, trend, price, opt = r
            st.write(
                f"Alert: {t} {trend} ${price:.2f} — "
                f"Option: {opt.get('label')} {opt.get('strike')} exp {opt.get('expiry')} mid {opt.get('mid')}"
            )

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
