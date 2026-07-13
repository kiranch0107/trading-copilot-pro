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
import time
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import pytz

# --------------------------------------------------
# LOGGING
# --------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("trading_copilot")

# --------------------------------------------------
# PAGE CONFIG
# --------------------------------------------------
st.set_page_config(
    page_title="Trading Copilot ELITE",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
.block-container {padding-top: 1.5rem}
.stAlert { border-radius: 8px }
div[data-testid="metric-container"]{
    background:#1e1e2e;
    border:1px solid #333;
    border-radius:8px;
    padding:12px;
}
.filter-pass{
    background:#0d2b1a;
    border-left:3px solid #22c55e;
    padding:6px 10px;
    border-radius:5px;
    margin:3px 0;
    font-size:.85em;
}
.filter-fail{
    background:#2b0d0d;
    border-left:3px solid #ef4444;
    padding:6px 10px;
    border-radius:5px;
    margin:3px 0;
    font-size:.85em;
}
</style>
""",
    unsafe_allow_html=True,
)

st.title("Trading Copilot ELITE")
st.caption("Swing · Options · Alerts · Journal · ADX · Multi-TF · Earnings Guard")

# --------------------------------------------------
# SIDEBAR - CONFIG & TUNABLES
# --------------------------------------------------
st.sidebar.header("Scan Settings")

WATCHLIST = ["TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "META", "SPY"]

# FAST MODE
FAST_MODE = st.sidebar.checkbox("Fast Mode (top 5 only)", value=True)
SCAN_LIST = WATCHLIST[:5] if FAST_MODE else WATCHLIST
st.sidebar.caption(f"Scanning: {', '.join(SCAN_LIST)}")
st.sidebar.divider()

ADX_MIN = st.sidebar.number_input("ADX minimum", value=25.0, min_value=0.0)
EARNINGS_DAYS = int(st.sidebar.number_input("Earnings blackout days", value=3, min_value=0))
POST_EARNINGS_DAYS = int(
    st.sidebar.number_input(
        "Post-earnings cooling (days)",
        value=2,
        min_value=0,
        help="Also block signals N days AFTER earnings (avoids IV crush residual).",
    )
)
BUDGET_MAX = st.sidebar.number_input("Budget max (option mid)", value=2.00, min_value=0.0)
MIN_DTE = int(st.sidebar.number_input("Min DTE for options", value=1, min_value=0))
MIN_RR = st.sidebar.number_input("Min Reward/Risk", value=0.5, min_value=0.0)
MIN_ROWS = int(st.sidebar.number_input("Min history bars", value=50, min_value=10))
VOLUME_MULT = st.sidebar.number_input("Volume multiplier", value=1.0, min_value=0.0)

st.sidebar.divider()
WEEKLY_CONFIRM = st.sidebar.checkbox("Require weekly TF alignment", value=True)
SPY_REGIME = st.sidebar.checkbox("Apply SPY regime filter", value=True)
st.sidebar.divider()

# Position sizing
st.sidebar.header("$ Position Sizing")
ACCOUNT_SIZE = int(st.sidebar.number_input("Account size ($)", value=1500, min_value=1))
RISK_PCT = st.sidebar.number_input("Risk per trade (%)", value=1.0, min_value=0.0)

COOLDOWN = 600
ALERT_LOG_FILE = Path("alert_history.json")
JOURNAL_FILE = Path("trade_journal.json")

# --------------------------------------------------
# PERSISTENCE HELPERS
# --------------------------------------------------
def _load(path: Path) -> list:
    try:
        return json.loads(path.read_text()) if path.exists() else []
    except Exception as e:
        logger.exception("Failed to load %s: %s", path, e)
        return []

def _save(path: Path, data: list) -> None:
    try:
        path.write_text(json.dumps(data, indent=2, default=str))
    except Exception as e:
        logger.exception("Failed to save %s: %s", path, e)

def load_alerts() -> list:
    return _load(ALERT_LOG_FILE)

def save_alerts(d: list) -> None:
    _save(ALERT_LOG_FILE, d)

def load_journal() -> list:
    return _load(JOURNAL_FILE)

def save_journal(d: list) -> None:
    _save(JOURNAL_FILE, d)

def log_alert(ticker, trend, strength, entry, stop, target, rr, price, filters_passed: dict) -> None:
    alerts = load_alerts()
    recent = [a for a in alerts if a["ticker"] == ticker]
    if recent:
        try:
            last_epoch = datetime.strptime(
                recent[-1]["timestamp"], "%Y-%m-%d %H:%M ET"
            ).timestamp()
            if time.time() - last_epoch < COOLDOWN:
                return
        except Exception:
            pass

    alerts.append(
        {
            "id": f"{ticker}_{int(time.time())}",
            "timestamp": datetime.now(pytz.timezone("America/New_York")).strftime(
                "%Y-%m-%d %H:%M ET"
            ),
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
        }
    )
    save_alerts(alerts)

def add_journal_trade(
    alert_id,
    ticker,
    trend,
    entry,
    stop,
    target,
    rr,
    exit_price,
    outcome,
    notes,
    setup_date,
) -> None:
    journal = load_journal()
    risk = abs(entry - stop)
    pnl_r = round((exit_price - entry) / risk, 2) if trend == "Bullish" else round(
        (entry - exit_price) / risk, 2
    )
    journal = [j for j in journal if j["id"] != alert_id]
    journal.append(
        {
            "id": alert_id,
            "date": setup_date,
            "closed": datetime.now(pytz.timezone("America/New_York")).strftime(
                "%Y-%m-%d %H:%M"
            ),
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
        }
    )
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
    wr = round(len(wins) / total * 100, 1) if total > 0 else 0.0
    avg_win = round(sum(j["actual_rr"] for j in wins) / len(wins), 2) if wins else 0.0
    avg_loss = round(sum(j["actual_rr"] for j in losses) / len(losses), 2) if losses else 0.0
    total_r = round(sum(j["actual_rr"] for j in journal), 2)

    gp = sum(j["actual_rr"] for j in wins if j["actual_rr"] > 0.05)
    gl = abs(sum(j["actual_rr"] for j in losses if j["actual_rr"] < -0.05))
    if gp == 0:
        gp = sum(j["actual_rr"] for j in wins if j["actual_rr"] > 0)
    if gl == 0:
        gl = abs(sum(j["actual_rr"] for j in losses if j["actual_rr"] < 0)
                 )
    pf = round(gp / gl, 2) if gl else float("inf")

    outcomes = [j["outcome"] for j in sorted(journal, key=lambda x: x["closed"])]
    streak = 0
    streak_type = outcomes[-1] if outcomes else ""
    for o in reversed(outcomes):
        if o == streak_type:
            streak += 1
        else:
            break

    sorted_j = sorted(journal, key=lambda x: x["closed"])
    cum_r = 0.0
    eq_curve = []
    for j in sorted_j:
        cum_r += j["actual_rr"]
        eq_curve.append({"date": j["closed"][:10], "Cumulative R": round(cum_r, 2)})

    return {
        "total": total,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(be),
        "win_rate": wr,
        "avg_win_r": avg_win,
        "avg_loss_r": avg_loss,
        "total_r": total_r,
        "profit_factor": pf,
        "streak": streak,
        "streak_type": streak_type,
        "equity_curve": eq_curve,
    }

# --------------------------------------------------
# POSITION SIZING
# --------------------------------------------------
def calc_position_size(entry: float, stop: float) -> dict:
    risk_dollars = round(ACCOUNT_SIZE * RISK_PCT / 100, 2)
    per_share = abs(entry - stop)
    shares = int(risk_dollars / per_share) if per_share > 0 else 0
    contracts = max(1, shares // 100)
    return {"risk_dollars": risk_dollars, "shares": shares, "contracts": contracts}

# --------------------------------------------------
# HELPERS
# --------------------------------------------------
def short_ts(ts: str) -> str:
    """Compact timestamp - 'Jul 1 14:32' instead of '2025-07-01 14:32 ET'."""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M ET")
        return dt.strftime("%b %-d %H:%M")
    except Exception:
        return ts

def is_market_open() -> bool:
    try:
        tz = pytz.timezone("America/New_York")
        now = datetime.now(tz)
        if now.weekday() >= 5:
            return False
        return (
            now.replace(hour=9, minute=30, second=0, microsecond=0)
            <= now
            <= now.replace(hour=16, minute=0, second=0, microsecond=0)
        )
    except Exception:
        return False

def send_telegram_alert(ticker: str, message: str) -> None:
    TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": message},
            timeout=5,
        )
    except Exception:
        logger.exception("Failed to send Telegram alert for %s", ticker)

# --------------------------------------------------
# RATE LIMITER
# --------------------------------------------------
class RateLimiter:
    def __init__(self, min_gap: float = 0.35):
        self.min_gap = min_gap
        self.lock = threading.Lock()
        self.last_ts = 0.0

    def wait(self) -> None:
        with self.lock:
            elapsed = time.time() - self.last_ts
            if elapsed < self.min_gap:
                time.sleep(self.min_gap - elapsed)
            self.last_ts = time.time()

rl = RateLimiter(min_gap=0.35)       # default gap for data + options calls
rl_slow = RateLimiter(min_gap=0.80)  # slower gap for weekly trend + earnings

SPY_ADX_THRESHOLD = 20
YF_RETRY_TRIES = 3
YF_RETRY_DELAY = 2.0

def _is_rate_limit_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "too many requests" in msg or "rate limit" in msg or "429" in msg

def _yf_download_with_retry(ticker: str, period: str, interval: str) -> pd.DataFrame | None:
    delay = YF_RETRY_DELAY
    last_err = None
    for attempt in range(YF_RETRY_TRIES):
        rl.wait()
        try:
            return yf.download(ticker, period=period, interval=interval, progress=False)
        except Exception as e:
            last_err = e
            if _is_rate_limit_error(e) and attempt < YF_RETRY_TRIES - 1:
                logger.warning("Rate limited yf.download(%s). Backing off %ss", ticker, delay)
                time.sleep(delay)
                delay *= 2
                continue
            raise
    if last_err:
        raise last_err
    return None

def _normalise_df(df: pd.DataFrame, min_rows: int) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    return df if len(df) >= min_rows else None

@st.cache_data(ttl=600, show_spinner=False)
def get_data(ticker: str, period: str = "3mo", interval: str = "1d") -> pd.DataFrame | None:
    try:
        return _normalise_df(_yf_download_with_retry(ticker, period, interval), MIN_ROWS)
    except Exception as e:
        logger.info("get_data(%s) failed: %s", ticker, e)
        return None

def get_data_with_error(
    ticker: str, period: str = "3mo", interval: str = "1d"
) -> tuple[pd.DataFrame | None, str | None]:
    try:
        df = _yf_download_with_retry(ticker, period, interval)
    except Exception as e:
        if _is_rate_limit_error(e):
            return None, "Rate limited by Yahoo Finance - please wait a moment and retry."
        return None, f"Data fetch failed: {e}"
    df = _normalise_df(df, MIN_ROWS)
    if df is None:
        return None, f"No usable data for '{ticker}' - check the symbol or try a longer period."
    return df, None

@st.cache_data(ttl=600, show_spinner=False)
def batch_get_data(tickers: tuple, period: str = "3mo", interval: str = "1d") -> dict[str, pd.DataFrame]:
    if not tickers:
        return {}
    rl.wait()
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
                df = _normalise_df(raw[t].copy(), MIN_ROWS)
                if df is not None:
                    result[t] = df
            except Exception:
                pass
        if result:
            return result

    for t in tickers:
        df = get_data(t, period, interval)
        if df is not None:
            result[t] = df
    return result

# --------------------------------------------------
# INDICATORS
# --------------------------------------------------
def compute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["EMA20"] = ta.trend.ema_indicator(df["Close"], window=20)
    df["EMA50"] = ta.trend.ema_indicator(df["Close"], window=50)
    macd = ta.trend.MACD(df["Close"])
    df["MACD"] = macd.macd()
    df["Signal"] = macd.macd_signal()
    df["RSI"] = ta.momentum.rsi(df["Close"], window=14)
    df["ATR"] = ta.volatility.average_true_range(df["High"], df["Low"], df["Close"])
    df["VOL_AVG20"] = df["Volume"].rolling(20).mean()
    df["ADX"] = ta.trend.adx(df["High"], df["Low"], df["Close"], window=14)
    return df.dropna(subset=["EMA20", "EMA50", "MACD", "Signal", "RSI", "ATR", "ADX"])

# --------------------------------------------------
# FILTER HELPERS
# --------------------------------------------------
def check_adx(df: pd.DataFrame) -> tuple[bool, float]:
    adx_val = float(df["ADX"].iloc[-1])
    return adx_val >= ADX_MIN, round(adx_val, 1)

@st.cache_data(ttl=900, show_spinner=False)
def get_weekly_trend(ticker: str) -> str | None:
    try:
        rl_slow.wait()
        df = yf.download(ticker, period="1y", interval="1wk", progress=False)
        if df is None or df.empty or len(df) < 20:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Close"])
        df["EMA10w"] = ta.trend.ema_indicator(df["Close"], window=10)
        df["EMA20w"] = ta.trend.ema_indicator(df["Close"], window=20)
        df = df.dropna(subset=["EMA10w", "EMA20w"])
        e10 = float(df["EMA10w"].iloc[-1])
        e20 = float(df["EMA20w"].iloc[-1])
        if e10 > e20:
            return "Bullish"
        elif e10 < e20:
            return "Bearish"
        return None
    except Exception as e:
        logger.exception("get_weekly_trend(%s): %s", ticker, e)
        return None

def check_weekly_alignment(daily: str, weekly: str | None) -> tuple[bool, str]:
    if weekly is None:
        return False, "Weekly data unavailable"
    if daily == weekly:
        return True, f"Weekly {weekly}"
    return False, f"Daily {daily} vs Weekly {weekly} - misaligned"

@st.cache_data(ttl=3600, show_spinner=False)
def get_next_earnings(ticker: str) -> str | None:
    try:
        rl_slow.wait()
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
                ts = pd.to_datetime(cal["Earnings Date"].iloc[0], errors="coerce")
            else:
                first = cal.iloc[0].dropna().iloc[0] if not cal.empty else None
                ts = pd.to_datetime(first, errors="coerce")
        else:
            ts = pd.NaT
        return None if pd.isna(ts) else str(ts.date())
    except Exception as e:
        logger.exception("get_next_earnings(%s): %s", ticker, e)
        return None

def check_earnings_blackout(ticker: str) -> tuple[bool, str]:
    ds = get_next_earnings(ticker)
    if ds is None:
        return True, "Earnings date unknown - proceed with caution"
    try:
        edt = datetime.strptime(ds, "%Y-%m-%d").date()
        today = datetime.now(pytz.timezone("America/New_York")).date()
        days = (edt - today).days
        if 0 <= days <= EARNINGS_DAYS:
            return False, f"! Earnings in {days}d ({ds}) - signal blocked"
        elif days < 0:
            if abs(days) <= POST_EARNINGS_DAYS:
                return False, f"! Earnings was {abs(days)}d ago ({ds}) - post-earnings cooling"
            return True, f"Last earnings: {ds} ({abs(days)}d ago)"
        return True, f"Next earnings: {ds} ({days}d away)"
    except Exception as e:
        logger.exception("check_earnings_blackout(%s): %s", ticker, e)
        return True, "Earnings check failed - proceed with caution"

@st.cache_data(ttl=1800, show_spinner=False)
def get_spy_regime() -> dict:
    try:
        rl_slow.wait()
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
        if price > sma200 and adx_val >= SPY_ADX_THRESHOLD:
            regime = "Bull"
            reasoning = f"SPY ${price:.0f} above 200-SMA ${sma200:.0f} (ADX {adx_val:.1f})"
        elif price <= sma200 and adx_val >= SPY_ADX_THRESHOLD:
            regime = "Bear"
            reasoning = f"SPY ${price:.0f} below 200-SMA ${sma200:.0f} (ADX {adx_val:.1f})"
        else:
            regime = "Neutral"
            reasoning = f"SPY ${price:.0f} near 200-SMA ${sma200:.0f} - choppy (ADX {adx_val:.1f})"
        return {
            "regime": regime,
            "price": round(price, 2),
            "sma200": round(sma200, 2),
            "adx": round(adx_val, 1),
            "reasoning": reasoning,
        }
    except Exception as e:
        logger.exception("get_spy_regime: %s", e)
        return {"regime": "Unknown", "reasoning": str(e)}

def check_regime_alignment(daily_trend: str, spy_regime: dict) -> tuple[bool, str]:
    regime = spy_regime.get("regime", "Unknown")
    if regime == "Unknown":
        return True, "Regime unknown - no filter applied"
    if daily_trend == "Bullish" and regime == "Bear":
        return False, "Counter-regime: going Long in SPY Bear market"
    if daily_trend == "Bearish" and regime == "Bull":
        return False, "Counter-regime: going Short in SPY Bull market"
    return True, f"Regime aligned: {daily_trend} in {regime} market"

# --------------------------------------------------
# OPTIONS ENGINE
# --------------------------------------------------
OPT_RETRY_ATTEMPTS = 3
OPT_RETRY_DELAY = 2.0
OPT_EXPIRY_DELAY = 0.4
OPT_MAX_EXPIRIES = 3

def _fetch_chain_with_retry(stock, expiry: str):
    delay = OPT_RETRY_DELAY
    for attempt in range(OPT_RETRY_ATTEMPTS):
        rl.wait()
        try:
            return stock.option_chain(expiry)
        except Exception as e:
            msg = str(e).lower()
            if ("too many requests" in msg or "rate limit" in msg or "429" in msg) and attempt < OPT_RETRY_ATTEMPTS - 1:
                logger.warning("Rate limited chain %s %s; backoff %ss", stock.ticker, expiry, delay)
                time.sleep(delay)
                delay *= 2
                continue
            raise
    return None

@st.cache_data(ttl=900, show_spinner=False)
def get_full_chain_data(ticker: str) -> dict:
    try:
        stock = yf.Ticker(ticker)
        rl.wait()
        try:
            all_expiries = stock.options
        except Exception as e:
            if _is_rate_limit_error(e):
                time.sleep(3)
                rl.wait()
                all_expiries = stock.options
            else:
                raise
        if not all_expiries:
            return {"error": "No option chain available", "expiries": []}

        today = pd.Timestamp.today().normalize()
        result = []
        checked = 0
        for expiry in all_expiries:
            if checked >= OPT_MAX_EXPIRIES:
                break
            try:
                dte = (pd.Timestamp(expiry) - today).days
            except Exception:
                continue
            if dte < MIN_DTE:
                continue
            checked += 1
            try:
                time.sleep(OPT_EXPIRY_DELAY)
                chain = _fetch_chain_with_retry(stock, expiry)
                if chain is None:
                    continue
                result.append(
                    {
                        "expiry": expiry,
                        "dte": dte,
                        "calls": chain.calls.fillna(0),
                        "puts": chain.puts.fillna(0),
                    }
                )
            except Exception as e:
                logger.exception("Skipping expiry %s for %s: %s", expiry, ticker, e)
        if not result:
            return {"error": "No valid expiries found", "expiries": []}
        return {"error": None, "expiries": result}
    except Exception as e:
        msg = str(e)
        if _is_rate_limit_error(Exception(msg)):
            return {"error": "Rate limited by Yahoo Finance - try again shortly", "expiries": []}
        return {"error": f"Option chain fetch failed ({msg})", "expiries": []}

def get_option_data(ticker: str, price: float, trend: str, strength: str) -> dict:
    chain_data = get_full_chain_data(ticker)
    if chain_data.get("error"):
        return {"error": chain_data["error"]}
    best = None
    best_score = 0.0
    for entry in chain_data["expiries"]:
        expiry, dte = entry["expiry"], entry["dte"]
        opts = entry["calls"] if trend == "Bullish" else entry["puts"]
        if opts.empty:
            continue
        if strength == "Strong":
            opts = opts[
                (opts["strike"] <= price * 1.02)
                if trend == "Bullish"
                else (opts["strike"] >= price * 0.98)
            ]
        else:
            opts = opts[
                (opts["strike"] >= price * 0.95)
                & (opts["strike"] <= price * 1.05)
            ]
        if opts.empty:
            continue
        opts = opts.copy()
        opts["spread"] = opts["ask"] - opts["bid"]
        opts["mid"] = (opts["ask"] + opts["bid"]) / 2

        valid = opts[
            (opts["mid"] > 0)
            & (opts["bid"] > 0)
            & (opts["volume"] > 0)
            & (opts["spread"] / opts["mid"] <= 0.15)
        ]
        valid = valid[valid["openInterest"] > 0]
        if valid.empty:
            continue
        valid = valid.copy()
        valid["liq"] = valid["volume"] + valid["openInterest"]
        valid["vol_weight"] = valid["volume"].apply(lambda v: 0.1 if v == 0 else 1.0 + v / 100.0)
        valid["score"] = (valid["liq"] * valid["vol_weight"]) / (1 + (valid["spread"] / (valid["mid"] + 1e-6)))
        top = valid.sort_values("score", ascending=False).iloc[0]
        if top["score"] > best_score:
            best = (top, expiry, dte)
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

# --------------------------------------------------
# UNUSUAL ACTIVITY ENGINE
# --------------------------------------------------
UA_VOL_OI_RATIO_MIN = 2.0
UA_VOL_OI_RATIO_HIGH = 4.0
UA_PEER_MULTIPLE_MIN = 3.0
UA_MIN_VOLUME = 100

def _score_unusual_contract(row: pd.Series, peer_med: float) -> dict:
    vol = float(row.get("volume", 0) or 0)
    oi = float(row.get("openInterest", 0) or 0)
    if vol < UA_MIN_VOLUME:
        return {"unusual": False}
    vol_oi = vol / oi if oi > 0 else (float("inf") if vol > 0 else 0)
    peer_r = vol / peer_med if peer_med > 0 else 0
    voi_f = vol_oi >= UA_VOL_OI_RATIO_MIN
    peer_f = peer_r >= UA_PEER_MULTIPLE_MIN
    if not (voi_f or peer_f):
        return {"unusual": False}
    if vol_oi >= UA_VOL_OI_RATIO_HIGH and peer_f:
        sev = "Extreme"
    elif voi_f and peer_f:
        sev = "High"
    else:
        sev = "Moderate"
    reasons = []
    if voi_f:
        reasons.append(f"Vol {int(vol):,} is {vol_oi:.1f}x OI ({int(oi):,})")
    if peer_f:
        reasons.append(f"Vol is {peer_r:.1f}x chain median volume")
    return {
        "unusual": True,
        "severity": sev,
        "vol_oi_ratio": round(vol_oi, 1) if vol_oi != float("inf") else None,
        "peer_ratio": round(peer_r, 1),
        "reasons": reasons,
        "volume": int(vol),
        "oi": int(oi),
    }

def scan_unusual_activity(ticker: str) -> dict:
    chain = get_full_chain_data(ticker)
    if chain.get("error"):
        return {"error": chain["error"], "flagged": []}
    flagged = []
    checked = 0
    for e in chain["expiries"]:
        expiry, dte = e["expiry"], e["dte"]
        checked += 1
        for label, opts in (("CALL", e["calls"]), ("PUT", e["puts"])):
            if opts.empty:
                continue
            peer_med = float(opts["volume"].median())
            for _, row in opts.iterrows():
                s = _score_unusual_contract(row, peer_med)
                if s.get("unusual"):
                    flagged.append(
                        {
                            "ticker": ticker,
                            "type": label,
                            "strike": round(float(row["strike"]), 2),
                            "expiry": expiry,
                            "dte": dte,
                            "last_price": round(float(row.get("lastPrice", 0) or 0), 2),
                            "severity": s["severity"],
                            "vol_oi_ratio": s["vol_oi_ratio"],
                            "peer_ratio": s["peer_ratio"],
                            "reasons": s["reasons"],
                            "volume": s["volume"],
                            "oi": s["oi"],
                        }
                    )
    sev_rank = {"Extreme": 3, "High": 2, "Moderate": 1}
    flagged.sort(key=lambda x: (sev_rank.get(x["severity"], 0), x["volume"]), reverse=True)
    return {"flagged": flagged, "expiries_checked": checked}

def check_pick_unusual_activity(ticker: str, opt: dict) -> dict | None:
    if not opt or "error" in opt:
        return None
    ua = scan_unusual_activity(ticker)
    if "error" in ua or not ua.get("flagged"):
        return None
    for f in ua["flagged"]:
        if (
            f["type"] == opt["label"]
            and abs(f["strike"] - opt["strike"]) < 0.01
            and f["expiry"] == opt["expiry"]
        ):
            return f
    return None

# --------------------------------------------------
# TRADE ANALYSIS
# --------------------------------------------------
def _analyze_uncached(df: pd.DataFrame, ticker: str, spy_regime: dict | None = None) -> dict:
    latest = df.iloc[-1]
    price = float(latest["Close"])
    ema20 = float(latest["EMA20"])
    ema50 = float(latest["EMA50"])
    rsi = float(latest["RSI"])
    macd = float(latest["MACD"])
    signal = float(latest["Signal"])
    atr = float(latest["ATR"])
    volume = float(latest["Volume"])
    vol_avg = float(latest["VOL_AVG20"])
    vol_ok = volume >= vol_avg * VOLUME_MULT
    vol_soft_ok = volume >= vol_avg * 0.70

    if price > ema20 > ema50 and macd > signal and 30 < rsi < 75 and vol_soft_ok:
        trend = "Bullish"
    elif price < ema20 < ema50 and macd < signal and 25 < rsi < 70 and vol_soft_ok:
        trend = "Bearish"
    else:
        return {
            "blocked": True,
            "block_reason": "base",
            "price": round(price, 2),
            "ema20": round(ema20, 2),
            "ema50": round(ema50, 2),
            "rsi": round(rsi, 1),
            "macd": round(macd, 4),
            "signal_line": round(signal, 4),
            "vol_ratio": round(volume / vol_avg, 2) if vol_avg else 0,
            "filters": {},
        }

    strength = "Strong" if (
        (rsi > 60 and trend == "Bullish") or (rsi < 40 and trend == "Bearish")
    ) else "Normal"

    filters: dict[str, dict] = {}
    adx_ok, adx_val = check_adx(df)
    filters["ADX Trend Strength"] = {
        "pass": adx_ok,
        "detail": f"ADX {adx_val} {'>' if adx_ok else '<'} {ADX_MIN} threshold",
    }
    weekly = get_weekly_trend(ticker) if WEEKLY_CONFIRM else None
    mtf_ok, mtf_detail = check_weekly_alignment(trend, weekly)
    filters["Multi-TF Alignment"] = {"pass": mtf_ok, "detail": mtf_detail}
    earnings_ok, earnings_detail = check_earnings_blackout(ticker)
    filters["Earnings Blackout"] = {"pass": earnings_ok, "detail": earnings_detail}
    if SPY_REGIME and spy_regime:
        regime_ok, regime_detail = check_regime_alignment(trend, spy_regime)
    else:
        regime_ok, regime_detail = True, "Regime filter disabled"
    filters["Macro Regime"] = {"pass": regime_ok, "detail": regime_detail}

    n_pass = sum(1 for f in filters.values() if f["pass"])
    n_total = len(filters)
    all_pass = (n_pass == n_total)

    lookback_high = df["High"].iloc[-6:-1].max()
    lookback_low = df["Low"].iloc[-6:-1].min()
    swing_low_10 = float(df["Low"].tail(10).min())
    swing_high_10 = float(df["High"].tail(10).max())

    if trend == "Bullish":
        entry = round(float(lookback_high), 2)
        raw_stop = min(swing_low_10, price - atr)
        stop = round(min(raw_stop, price - 0.01), 2)
        resistance = float(df["High"].tail(20).max())
        target = round(min(price + atr * 2.5, resistance * 0.99), 2)
    else:
        entry = round(float(lookback_low), 2)
        raw_stop = max(swing_high_10, price + atr)
        stop = round(max(raw_stop, price + 0.01), 2)
        support = float(df["Low"].tail(20).min())
        target = round(max(price - atr * 2.5, support * 1.01), 2)

    risk = abs(entry - stop)
    if risk < 0.01:
        return {
            "blocked": True,
            "block_reason": "zero_risk",
            "trend": trend,
            "price": round(price, 2),
            "filters": filters,
            "filters_pass": n_pass,
            "filters_total": n_total,
        }

    rr = round(abs(target - entry) / risk, 2)
    if rr < MIN_RR:
        return {
            "blocked": True,
            "block_reason": "rr",
            "trend": trend,
            "strength": strength,
            "price": round(price, 2),
            "entry": entry,
            "stop": stop,
            "target": target,
            "rr": rr,
            "filters": filters,
            "filters_pass": n_pass,
            "filters_total": n_total,
            "rsi": round(rsi, 1),
            "adx": adx_val,
        }

    option = get_option_data(ticker, price, trend, strength)
    high_quality = (rr >= 2.0 and strength == "Strong" and all_pass)

    return {
        "blocked": False,
        "ticker": ticker,
        "price": round(price, 2),
        "trend": trend,
        "strength": strength,
        "entry": entry,
        "stop": stop,
        "target": target,
        "rr": rr,
        "rsi": round(rsi, 1),
        "atr": round(atr, 2),
        "adx": adx_val,
        "option": option,
        "filters": filters,
        "filters_pass": n_pass,
        "filters_total": n_total,
        "all_pass": all_pass,
        "high_quality": high_quality,
    }

@st.cache_data(ttl=300, show_spinner=False)
def analyze(_df: pd.DataFrame, ticker: str, latest_bar_key: str, spy_regime: dict | None = None) -> dict:
    return _analyze_uncached(_df, ticker, spy_regime=spy_regime)

# --------------------------------------------------
# WATCHLIST SCAN (SAFE VERSION)
# --------------------------------------------------
SCAN_MAX_WORKERS = 3

def _scan_one_ticker(ticker: str, data_map: dict, spy_regime: dict | None) -> dict | None:
    df = data_map.get(ticker)
    if df is None:
        return None
    df = compute(df)
    if df.empty:
        return None
    r = analyze(df, ticker, f"{ticker}_{df.index[-1]}", spy_regime)
    # analyze() never returns None anymore - it returns a diagnostic dict
    # with "blocked": True when no valid setup. Blocked dicts lack "ticker"
    # and other display keys, so exclude them from watchlist results.
    return r if r and not r.get("blocked") else None

# --------------------------------------------------
# MAIN APP LAYOUT
# --------------------------------------------------
spy_regime = get_spy_regime() if SPY_REGIME else None
data_map = batch_get_data(tuple(SCAN_LIST))

all_setups: list[dict] = []

with ThreadPoolExecutor(max_workers=SCAN_MAX_WORKERS) as executor:
    futures = {
        executor.submit(_scan_one_ticker, t, data_map, spy_regime): t
        for t in SCAN_LIST
    }
    for future in as_completed(futures):
        ticker = futures[future]
        try:
            result = future.result()
            if result:
                all_setups.append(result)
        except Exception as e:
            logger.exception("Scan failed for %s: %s", ticker, e)

# Defensive: only keep well-formed setup dicts (must have "ticker")
all_setups = [s for s in all_setups if isinstance(s, dict) and "ticker" in s]
scanned_tickers = [s["ticker"] for s in all_setups] if all_setups else []

col_left, col_right = st.columns([2, 1])

with col_left:
    st.subheader("Scan Results")
    if not all_setups:
        st.info("No valid setups found with current filters.")
    else:
        for s in all_setups:
            trend_color = "green" if s["trend"] == "Bullish" else "red"
            st.markdown(
                f"**{s['ticker']}** — "
                f"<span style='color:{trend_color}'>{s['trend']}</span> "
                f"(RR: {s['rr']}, ADX: {s['adx']}, RSI: {s['rsi']})",
                unsafe_allow_html=True,
            )
            st.write(
                f"Entry: {s['entry']} · Stop: {s['stop']} · Target: {s['target']} · "
                f"High-quality: {s['high_quality']}"
            )
            opt = s.get("option", {})
            if opt and "error" not in opt:
                st.write(
                    f"Option: {opt['label']} {opt['strike']} @ {opt['mid']} "
                    f"(DTE: {opt['dte']}, Vol: {opt['volume']}, OI: {opt['oi']})"
                )
            elif opt.get("error"):
                st.write(f"Option: {opt['error']}")

with col_right:
    st.subheader("Journal & Stats")
    journal = load_journal()
    stats = journal_stats(journal)
    if not stats:
        st.info("No journal entries yet.")
    else:
        st.metric("Win Rate", f"{stats['win_rate']}%")
        st.metric("Profit Factor", stats["profit_factor"])
        st.metric("Total R", stats["total_r"])
        st.metric("Streak", f"{stats['streak']} {stats['streak_type']}")
        st.write("Equity Curve (Cumulative R):")
        eq_df = pd.DataFrame(stats["equity_curve"])
        if not eq_df.empty:
            st.line_chart(eq_df.set_index("date")["Cumulative R"])

st.caption(f"Scanned tickers: {', '.join(scanned_tickers) if scanned_tickers else 'None'}")
