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

# ─────────────────────────────────────────────
# SIDEBAR — CONFIG & TUNABLES
# ─────────────────────────────────────────────
st.sidebar.header("⚙️ Scan Settings")

WATCHLIST = ["TSLA","NVDA","AAPL","MSFT","AMZN","META","SPY"]

# FIX #11: FAST_MODE exposed as sidebar toggle
FAST_MODE  = st.sidebar.checkbox("Fast Mode (top 5 only)", value=True)
SCAN_LIST  = WATCHLIST[:5] if FAST_MODE else WATCHLIST
st.sidebar.caption(f"Scanning: {', '.join(SCAN_LIST)}")
st.sidebar.divider()

ADX_MIN       = st.sidebar.number_input("ADX minimum",              value=25,   min_value=1,    max_value=100)
EARNINGS_DAYS      = int(st.sidebar.number_input("Earnings blackout days",      value=3,   min_value=0, max_value=30))
POST_EARNINGS_DAYS = int(st.sidebar.number_input("Post-earnings cooling (days)", value=1,   min_value=0, max_value=7,
    help="Also block signals N days AFTER earnings (avoids IV crush residual)"))
BUDGET_MAX    = st.sidebar.number_input("Budget max (option mid)",   value=2.00, min_value=0.01, step=0.10)
MIN_DTE       = int(st.sidebar.number_input("Min DTE for options",   value=1,    min_value=1))
MIN_RR        = st.sidebar.number_input("Min Reward/Risk",           value=0.5,  min_value=0.1,  step=0.1)
HQ_MIN_RR     = st.sidebar.number_input("High-Quality R:R threshold", value=1.5,  min_value=0.2,  step=0.1,
    help="R:R needed to qualify as a 🔥 HIGH QUALITY setup (these trigger Telegram alerts). "
         "Must also be 'Strong' strength with all 4 filters passing.")
MIN_ROWS      = int(st.sidebar.number_input("Min history bars",      value=50,   min_value=10))
VOLUME_MULT   = st.sidebar.number_input("Volume multiplier",         value=1.0,  min_value=0.1,  step=0.1)
st.sidebar.divider()
WEEKLY_CONFIRM = st.sidebar.checkbox("Require weekly TF alignment",  value=True)
SPY_REGIME     = st.sidebar.checkbox("Apply SPY regime filter",      value=True)
st.sidebar.divider()

# FIX #10: account settings for position sizing
st.sidebar.header("💰 Position Sizing")
ACCOUNT_SIZE = int(st.sidebar.number_input("Account size ($)",   value=1500, min_value=100, step=500))
RISK_PCT     = st.sidebar.number_input("Risk per trade (%)",     value=1.0,   min_value=0.1, max_value=10.0, step=0.1)

COOLDOWN       = 600
# ─────────────────────────────────────────────
# PERSISTENCE
#
# BUG FIX #4: Streamlit Cloud containers are STATELESS. Files written to disk
# (alert_history.json / trade_journal.json) are destroyed on redeploy, restart,
# or idle timeout — silently wiping the user's entire trade journal.
#
# Mitigation (3 layers):
#   1. st.session_state is the primary read source (survives reruns instantly)
#   2. Disk is still written as a best-effort backup (works locally, and
#      survives short-lived reruns on cloud)
#   3. Export / Import buttons in the Journal tab so the user can persist
#      their data themselves — the only true fix on ephemeral hosting.
# ─────────────────────────────────────────────
ALERT_LOG_FILE = Path("alert_history.json")
JOURNAL_FILE   = Path("trade_journal.json")

_SS_ALERTS  = "_alerts_store"
_SS_JOURNAL = "_journal_store"


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
        # On some hosts the FS is read-only — session_state still holds the data
        logger.warning("Could not persist %s to disk (%s) — session_state only", path, e)


def load_alerts() -> list:
    """Read from session_state first; hydrate from disk on first access."""
    if _SS_ALERTS not in st.session_state:
        st.session_state[_SS_ALERTS] = _load(ALERT_LOG_FILE)
    return st.session_state[_SS_ALERTS]


def save_alerts(d: list) -> None:
    st.session_state[_SS_ALERTS] = d
    _save(ALERT_LOG_FILE, d)


def load_journal() -> list:
    if _SS_JOURNAL not in st.session_state:
        st.session_state[_SS_JOURNAL] = _load(JOURNAL_FILE)
    return st.session_state[_SS_JOURNAL]


def save_journal(d: list) -> None:
    st.session_state[_SS_JOURNAL] = d
    _save(JOURNAL_FILE, d)


def log_alert(ticker, trend, strength, entry, stop, target, rr, price,
              filters_passed: dict) -> None:
    alerts = load_alerts()
    now_epoch = time.time()

    # BUG FIX #2: cooldown was comparing against strptime("... ET") which
    # produces a NAIVE datetime — the literal "ET" is not parsed as a timezone.
    # .timestamp() then interpreted it in the SERVER's local tz (UTC on
    # Streamlit Cloud), a 4-5 hour offset, so the cooldown never triggered and
    # duplicate Telegram alerts fired on every scan.
    # Fix: store a real epoch alongside the display string and compare on that.
    recent = [a for a in alerts if a["ticker"] == ticker]
    if recent:
        last = recent[-1]
        last_epoch = last.get("epoch")
        if last_epoch is None:
            # Legacy record without epoch — fall back to a tz-aware parse
            try:
                naive = datetime.strptime(last["timestamp"], "%Y-%m-%d %H:%M ET")
                aware = pytz.timezone("America/New_York").localize(naive)
                last_epoch = aware.timestamp()
            except Exception:
                last_epoch = 0
        if now_epoch - float(last_epoch) < COOLDOWN:
            logger.info("Cooldown active for %s — alert suppressed", ticker)
            return

    alerts.append({
        "id":             f"{ticker}_{int(now_epoch)}",
        "timestamp":      datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d %H:%M ET"),
        "epoch":          now_epoch,   # tz-safe cooldown source of truth
        "ticker":  ticker, "trend":    trend,    "strength": strength,
        "price":   price,  "entry":    entry,    "stop":     stop,
        "target":  target, "rr":       rr,
        "filters_passed": filters_passed, "journaled": False,
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
        "ticker": ticker, "trend": trend, "entry": entry, "stop": stop, "target": target,
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
    gp = sum(j["actual_rr"] for j in wins   if j["actual_rr"] > 0.05)   # J1 FIX: ignore dust trades
    gl = abs(sum(j["actual_rr"] for j in losses if j["actual_rr"] < -0.05))  # same floor on loss side
    # If all wins/losses are below 0.05R, fall back to full set so pf isn't 0/inf
    if gp == 0: gp = sum(j["actual_rr"] for j in wins if j["actual_rr"] > 0)
    if gl == 0: gl = abs(sum(j["actual_rr"] for j in losses if j["actual_rr"] < 0))
    pf = round(gp/gl, 2) if gl else float("inf")
    outcomes    = [j["outcome"] for j in sorted(journal, key=lambda x: x["closed"])]
    streak      = 0
    streak_type = outcomes[-1] if outcomes else ""
    for o in reversed(outcomes):
        if o == streak_type: streak += 1
        else: break
    # FIX #5: build equity curve for chart
    sorted_j = sorted(journal, key=lambda x: x["closed"])
    cum_r    = 0.0
    eq_curve = []
    for j in sorted_j:
        cum_r += j["actual_rr"]
        eq_curve.append({"date": j["closed"][:10], "Cumulative R": round(cum_r, 2)})
    return {
        "total": total, "wins": len(wins), "losses": len(losses), "breakeven": len(be),
        "win_rate": wr, "avg_win_r": avg_win, "avg_loss_r": avg_loss,
        "total_r": total_r, "profit_factor": pf, "streak": streak,
        "streak_type": streak_type, "equity_curve": eq_curve,
    }


# ─────────────────────────────────────────────
# POSITION SIZING
# ─────────────────────────────────────────────
SHARES_PER_CONTRACT = 100


def calc_position_size(entry: float, stop: float) -> dict:
    """
    BUG FIX #3: the old code did `contracts = max(1, shares // 100)`.

    With a $1,500 account at 1% risk = $15 max risk. If per-share risk is $5,
    that's 3 shares. But `max(1, 0)` returned **1 contract**, which controls
    100 shares = $500 of real risk — 33% of the account, NOT the 1% configured.
    The floor silently blew through the user's risk limit by up to 33x.

    Now: contracts is a true floor-division with NO minimum. If 0, we surface
    that explicitly along with what the account/risk would need to be to afford
    a single contract, so the user can make an informed decision.
    """
    risk_dollars = round(ACCOUNT_SIZE * RISK_PCT / 100, 2)
    per_share    = abs(entry - stop)

    if per_share <= 0:
        return {
            "risk_dollars": risk_dollars, "shares": 0, "contracts": 0,
            "affordable": False,
            "note": "Invalid stop (zero risk per share).",
        }

    shares_by_risk = int(risk_dollars / per_share)

    # NOTIONAL CAP (bug fix): risk-based sizing alone can suggest more stock
    # than the account can buy. Example: $1 stock with a $0.01 stop → risk
    # sizing says 1,500 shares = $1,500 notional = 100% of a $1,500 account
    # (before commissions, and ignoring that cash accounts can't even fill it).
    # Cap shares by buying power (95% of account to leave room for fees).
    shares_by_cash  = int((ACCOUNT_SIZE * 0.95) / entry) if entry > 0 else 0
    shares          = min(shares_by_risk, shares_by_cash)
    notional_capped = shares_by_cash < shares_by_risk

    contracts = shares // SHARES_PER_CONTRACT   # NO max(1, ...) floor

    # What one contract would actually cost in risk terms
    risk_per_contract = round(per_share * SHARES_PER_CONTRACT, 2)

    if contracts >= 1:
        note = None
        affordable = True
    else:
        affordable = False
        # Minimum account needed to afford 1 contract at this risk %
        min_account = round(risk_per_contract / (RISK_PCT / 100), 0)
        note = (
            f"1 contract = {SHARES_PER_CONTRACT} shares × ${per_share:.2f} risk "
            f"= **${risk_per_contract:,.2f}** at risk — that's "
            f"**{risk_per_contract / ACCOUNT_SIZE * 100:.1f}%** of your "
            f"${ACCOUNT_SIZE:,} account (limit: {RISK_PCT}%). "
            f"You'd need ~${min_account:,.0f} to take 1 contract within your risk rule. "
            f"Consider trading **{shares} shares** instead."
        )

    if notional_capped:
        cap_note = (
            f"⚠️ Share count limited by buying power: risk sizing suggested "
            f"{shares_by_risk:,} shares but ${ACCOUNT_SIZE:,} only covers "
            f"{shares_by_cash:,} at ${entry:.2f}/share."
        )
        note = f"{cap_note}\n\n{note}" if note else cap_note

    return {
        "risk_dollars":      risk_dollars,
        "shares":            shares,
        "contracts":         contracts,
        "risk_per_contract": risk_per_contract,
        "affordable":        affordable,
        "note":              note,
    }


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def short_ts(ts: str) -> str:
    """FIX #6: compact timestamp — 'Jul 1 14:32' instead of '2025-07-01 14:32 ET'"""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M ET")
        return dt.strftime("%b %-d %H:%M")
    except Exception:
        return ts


# ─────────────────────────────────────────────
# MARKET HOURS
# ─────────────────────────────────────────────
def is_market_open() -> bool:
    try:
        tz  = pytz.timezone("America/New_York")
        now = datetime.now(tz)
        if now.weekday() >= 5:
            return False
        return (now.replace(hour=9,  minute=30, second=0, microsecond=0)
                <= now <=
                now.replace(hour=16, minute=0,  second=0, microsecond=0))
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
# RATE LIMITER
# ─────────────────────────────────────────────
class RateLimiter:
    def __init__(self, min_gap: float = 0.35):
        self._min_gap = min_gap
        self._lock    = threading.Lock()
        self._last_ts = 0.0

    def wait(self) -> None:
        with self._lock:
            elapsed = time.time() - self._last_ts
            if elapsed < self._min_gap:
                time.sleep(self._min_gap - elapsed)
            self._last_ts = time.time()

_rl = RateLimiter(min_gap=0.35)          # default gap for data + options calls
_rl_slow = RateLimiter(min_gap=0.80)    # D1 FIX: slower gap for weekly trend + earnings
                                         # — these fire per-ticker (5 tickers = 10 calls)
                                         # and don't need to be fast (cached 15-60 min).
                                         # Keeps them from crowding the main data fetches.
# F1 FIX: SPY regime uses ADX=20 deliberately (index trends are smoother than
# individual stocks so a lower threshold is appropriate). Documented here so
# it's not confused with the per-ticker ADX_MIN (default 25, user-tunable).
SPY_ADX_THRESHOLD = 20

_YF_RETRY_TRIES = 3
_YF_RETRY_DELAY = 2.0


def _is_rate_limit_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "too many requests" in msg or "rate limit" in msg or "429" in msg


def _yf_download_with_retry(ticker: str, period: str, interval: str) -> pd.DataFrame | None:
    delay = _YF_RETRY_DELAY
    last_err = None
    for attempt in range(_YF_RETRY_TRIES):
        _rl.wait()
        try:
            return yf.download(ticker, period=period, interval=interval, progress=False)
        except Exception as e:
            last_err = e
            if _is_rate_limit_error(e) and attempt < _YF_RETRY_TRIES - 1:
                logger.warning("Rate limited yf.download(%s). Backing off %ss", ticker, delay)
                time.sleep(delay); delay *= 2; continue
            raise
    if last_err: raise last_err
    return None


def _normalise_df(df: pd.DataFrame, min_rows: int) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Open","High","Low","Close","Volume"])
    return df if len(df) >= min_rows else None


@st.cache_data(ttl=600, show_spinner=False)
def get_data(ticker: str, period: str = "3mo", interval: str = "1d") -> pd.DataFrame | None:
    try:
        return _normalise_df(_yf_download_with_retry(ticker, period, interval), MIN_ROWS)
    except Exception as e:
        logger.info("get_data(%s) failed: %s", ticker, e)
        return None


def get_data_with_error(ticker: str, period: str = "3mo",
                        interval: str = "1d") -> tuple[pd.DataFrame | None, str | None]:
    try:
        df = _yf_download_with_retry(ticker, period, interval)
    except Exception as e:
        if _is_rate_limit_error(e):
            return None, "Rate limited by Yahoo Finance — please wait a moment and try again."
        return None, f"Data fetch failed: {e}"
    df = _normalise_df(df, MIN_ROWS)
    if df is None:
        return None, f"No usable data for '{ticker}' — check the symbol or try a longer period."
    return df, None


@st.cache_data(ttl=600, show_spinner=False)
def batch_get_data(tickers: tuple, period: str = "3mo",
                   interval: str = "1d") -> dict[str, pd.DataFrame]:
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


# ─────────────────────────────────────────────
# INDICATORS  (FIX #13: BB removed — computed but never used)
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
    df["VOL_AVG20"] = df["Volume"].rolling(20).mean()
    df["ADX"]       = ta.trend.adx(df["High"],df["Low"],df["Close"],window=14)
    return df.dropna(subset=["EMA20","EMA50","MACD","Signal","RSI","ATR","ADX"])


# ─────────────────────────────────────────────
# FILTER HELPERS
# ─────────────────────────────────────────────
def check_adx(df: pd.DataFrame) -> tuple[bool, float]:
    adx_val = float(df["ADX"].iloc[-1])
    return adx_val >= ADX_MIN, round(adx_val, 1)


@st.cache_data(ttl=900, show_spinner=False)
def get_weekly_trend(ticker: str) -> str | None:
    try:
        _rl_slow.wait()
        df = yf.download(ticker, period="1y", interval="1wk", progress=False)
        if df is None or df.empty or len(df) < 20:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Close"])
        df["EMA10w"] = ta.trend.ema_indicator(df["Close"], window=10)
        df["EMA20w"] = ta.trend.ema_indicator(df["Close"], window=20)
        df = df.dropna(subset=["EMA10w","EMA20w"])
        e10 = float(df["EMA10w"].iloc[-1])
        e20 = float(df["EMA20w"].iloc[-1])
        # B1 FIX: use EMA crossover only (e10 vs e20), not triple-chain
        # price>e10>e20 was too strict — in ranging markets where price dips
        # below e10 temporarily it returned None even in a clear uptrend.
        if e10 > e20:   return "Bullish"
        elif e10 < e20: return "Bearish"
        return None
    except Exception as e:
        logger.exception("get_weekly_trend(%s): %s", ticker, e)
        return None


def check_weekly_alignment(daily: str, weekly: str | None) -> tuple[bool, str]:
    if weekly is None:     return False, "Weekly data unavailable"
    if daily == weekly:    return True,  f"Weekly {weekly} ✓"
    return False, f"Daily {daily} vs Weekly {weekly} — misaligned"


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
    try:
        _rl_slow.wait()   # SPY fetched once per 30 min — use slow limiter to avoid crowding data calls
        df = yf.download("SPY", period="14mo", interval="1d", progress=False)
        if df is None or df.empty:
            return {"regime":"Unknown","reasoning":"SPY data unavailable"}
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Close","High","Low"])
        df["SMA200"] = df["Close"].rolling(200).mean()
        df["ADX"]    = ta.trend.adx(df["High"],df["Low"],df["Close"],window=14)
        df           = df.dropna(subset=["SMA200","ADX"])
        price   = float(df["Close"].iloc[-1])
        sma200  = float(df["SMA200"].iloc[-1])
        adx_val = float(df["ADX"].iloc[-1])
        if price > sma200 and adx_val >= SPY_ADX_THRESHOLD:
            regime    = "Bull"
            reasoning = f"SPY ${price:.0f} above 200-SMA ${sma200:.0f} (ADX {adx_val:.0f})"
        elif price <= sma200 and adx_val >= SPY_ADX_THRESHOLD:
            regime    = "Bear"
            reasoning = f"SPY ${price:.0f} below 200-SMA ${sma200:.0f} (ADX {adx_val:.0f})"
        else:
            regime    = "Neutral"
            reasoning = f"SPY ${price:.0f} near 200-SMA ${sma200:.0f} — choppy (ADX {adx_val:.0f})"
        return {"regime":regime,"price":round(price,2),"sma200":round(sma200,2),
                "adx":round(adx_val,1),"reasoning":reasoning}
    except Exception as e:
        logger.exception("get_spy_regime: %s", e)
        return {"regime":"Unknown","reasoning":str(e)}


def check_regime_alignment(daily_trend: str, spy_regime: dict) -> tuple[bool, str]:
    regime = spy_regime.get("regime","Unknown")
    if regime == "Unknown":           return True,  "Regime unknown — no filter applied"
    if daily_trend=="Bullish" and regime=="Bear":
        return False, "Counter-regime: going Long in SPY Bear market"
    if daily_trend=="Bearish" and regime=="Bull":
        return False, "Counter-regime: going Short in SPY Bull market"
    return True, f"Regime aligned: {daily_trend} in {regime} market ✓"


# ─────────────────────────────────────────────
# OPTIONS ENGINE
# ─────────────────────────────────────────────
_OPT_RETRY_ATTEMPTS = 3
_OPT_RETRY_DELAY    = 2.0
_OPT_EXPIRY_DELAY   = 0.4
_OPT_MAX_EXPIRIES   = 3


def _fetch_chain_with_retry(stock, expiry: str):
    delay = _OPT_RETRY_DELAY
    for attempt in range(_OPT_RETRY_ATTEMPTS):
        _rl.wait()
        try:
            return stock.option_chain(expiry)
        except Exception as e:
            msg = str(e).lower()
            if ("too many requests" in msg or "rate limit" in msg or "429" in msg) \
               and attempt < _OPT_RETRY_ATTEMPTS - 1:
                logger.warning("Rate limited chain %s %s; backoff %ss", stock.ticker, expiry, delay)
                time.sleep(delay); delay *= 2; continue
            raise
    return None


@st.cache_data(ttl=900, show_spinner=False)
def get_full_chain_data(ticker: str) -> dict:
    try:
        stock = yf.Ticker(ticker)
        _rl.wait()
        try:
            all_expiries = stock.options
        except Exception as e:
            if _is_rate_limit_error(e):
                time.sleep(3); _rl.wait(); all_expiries = stock.options
            else:
                raise
        if not all_expiries:
            return {"error":"No option chain available","expiries":[]}

        today   = pd.Timestamp.today().normalize()
        result  = []
        checked = 0
        for expiry in all_expiries:
            if checked >= _OPT_MAX_EXPIRIES:
                break
            try:
                dte = (pd.Timestamp(expiry) - today).days
            except Exception:
                continue
            if dte < MIN_DTE:
                continue
            checked += 1
            try:
                time.sleep(_OPT_EXPIRY_DELAY)
                chain = _fetch_chain_with_retry(stock, expiry)
                if chain is None:
                    continue
                result.append({"expiry":expiry,"dte":dte,
                                "calls":chain.calls.fillna(0),
                                "puts":chain.puts.fillna(0)})
            except Exception as e:
                logger.exception("Skipping expiry %s for %s: %s", expiry, ticker, e)
        if not result:
            return {"error":"No valid expiries found","expiries":[]}
        return {"error":None,"expiries":result}
    except Exception as e:
        msg = str(e)
        if _is_rate_limit_error(Exception(msg)):
            return {"error":"Rate limited by Yahoo Finance — try again shortly","expiries":[]}
        return {"error":f"Option chain fetch failed ({msg})","expiries":[]}


def get_option_data(ticker: str, price: float, trend: str, strength: str) -> dict:
    chain_data = get_full_chain_data(ticker)
    if chain_data.get("error"):
        return {"error": chain_data["error"]}
    best = None; best_score = 0.0
    for entry in chain_data["expiries"]:
        expiry, dte = entry["expiry"], entry["dte"]
        opts = entry["calls"] if trend=="Bullish" else entry["puts"]
        if opts.empty: continue
        if strength=="Strong":
            opts = opts[(opts["strike"]<=price*1.02) if trend=="Bullish"
                        else (opts["strike"]>=price*0.98)]
        else:
            opts = opts[(opts["strike"]>=price*0.95)&(opts["strike"]<=price*1.05)]
        if opts.empty: continue
        opts = opts.copy()
        opts["spread"] = opts["ask"] - opts["bid"]
        opts["mid"]    = (opts["ask"] + opts["bid"]) / 2
        # O2 FIX: require bid > 0 — mid can pass even when bid=0 (wide/illiquid)
        # O1 FIX: require volume > 0 explicitly — vol_weight=0.1 penalised but
        # didn't exclude. A zero-volume contract is untradeable regardless of OI.
        valid = opts[
            (opts["mid"] > 0) &
            (opts["bid"] > 0) &
            (opts["volume"] > 0) &
            (opts["spread"] / opts["mid"] <= 0.15)
        ]
        valid = valid[valid["openInterest"] > 0]   # also require some existing interest
        if valid.empty: continue
        valid = valid.copy()
        valid["liq"]   = valid["volume"] + valid["openInterest"]
        # O1 FIX: multiply score by volume_weight so zero-volume high-OI contracts
        # don't outscore genuinely active contracts. volume=0 → weight=0.1 (minimal
        # credit for existence), volume>0 → weight scales with actual activity.
        valid["vol_weight"] = valid["volume"].apply(lambda v: 0.1 if v == 0 else 1.0 + (v / (v + 100)))
        valid["score"] = (valid["liq"] * valid["vol_weight"]) / (1 + (valid["spread"] / (valid["mid"] + 1e-6)))
        top = valid.sort_values("score", ascending=False).iloc[0]
        if top["score"] > best_score:
            best = (top, expiry, dte); best_score = top["score"]
    if best is None:
        return {"error":"No liquid options found"}
    row, expiry, dte = best
    return {"label":"CALL" if trend=="Bullish" else "PUT",
            "strike":round(float(row["strike"]),2),
            "expiry":expiry,"mid":round(float(row["mid"]),2),
            "last_price":round(float(row.get("lastPrice",0)),2),
            "volume":int(row.get("volume",0)),"oi":int(row.get("openInterest",0)),
            "spread":round(float(row["spread"]),2),"dte":dte,
            "is_budget":row["mid"]<=BUDGET_MAX}


# ─────────────────────────────────────────────
# UNUSUAL ACTIVITY ENGINE
# ─────────────────────────────────────────────
UA_VOL_OI_RATIO_MIN  = 2.0
UA_VOL_OI_RATIO_HIGH = 4.0
UA_PEER_MULTIPLE_MIN = 3.0
UA_MIN_VOLUME        = 100


def _score_unusual_contract(row: pd.Series, peer_med: float) -> dict:
    vol = float(row.get("volume",0) or 0)
    oi  = float(row.get("openInterest",0) or 0)
    if vol < UA_MIN_VOLUME:
        return {"unusual":False}
    vol_oi  = vol/oi if oi>0 else (float("inf") if vol>0 else 0)
    peer_r  = vol/peer_med if peer_med>0 else 0
    voi_f   = vol_oi  >= UA_VOL_OI_RATIO_MIN
    peer_f  = peer_r  >= UA_PEER_MULTIPLE_MIN
    if not (voi_f or peer_f):
        return {"unusual":False}
    if vol_oi >= UA_VOL_OI_RATIO_HIGH and peer_f: sev = "Extreme"
    elif voi_f and peer_f:                         sev = "High"
    else:                                          sev = "Moderate"
    reasons = []
    if voi_f:  reasons.append(f"Vol {int(vol):,} is {vol_oi:.1f}x OI ({int(oi):,})")
    if peer_f: reasons.append(f"Vol is {peer_r:.1f}x chain median volume")
    return {"unusual":True,"severity":sev,
            "vol_oi_ratio":round(vol_oi,1) if vol_oi!=float("inf") else None,
            "peer_ratio":round(peer_r,1),"reasons":reasons,
            "volume":int(vol),"oi":int(oi)}


def scan_unusual_activity(ticker: str) -> dict:
    chain = get_full_chain_data(ticker)
    if chain.get("error"):
        return {"error":chain["error"],"flagged":[]}
    flagged = []; checked = 0
    for e in chain["expiries"]:
        expiry, dte = e["expiry"], e["dte"]; checked += 1
        for label, opts in (("CALL",e["calls"]),("PUT",e["puts"])):
            if opts.empty: continue
            peer_med = float(opts["volume"].median())
            for _, row in opts.iterrows():
                s = _score_unusual_contract(row, peer_med)
                if s.get("unusual"):
                    flagged.append({"ticker":ticker,"type":label,
                        "strike":round(float(row["strike"]),2),"expiry":expiry,"dte":dte,
                        "last_price":round(float(row.get("lastPrice",0) or 0),2),
                        "severity":s["severity"],"vol_oi_ratio":s["vol_oi_ratio"],
                        "peer_ratio":s["peer_ratio"],"reasons":s["reasons"],
                        "volume":s["volume"],"oi":s["oi"]})
    sev_rank = {"Extreme":3,"High":2,"Moderate":1}
    flagged.sort(key=lambda x:(sev_rank.get(x["severity"],0),x["volume"]),reverse=True)
    return {"flagged":flagged,"expiries_checked":checked}


def check_pick_unusual_activity(ticker: str, opt: dict) -> dict | None:
    if not opt or "error" in opt: return None
    ua = scan_unusual_activity(ticker)
    if "error" in ua or not ua.get("flagged"): return None
    for f in ua["flagged"]:
        if f["type"]==opt["label"] and abs(f["strike"]-opt["strike"])<0.01 and f["expiry"]==opt["expiry"]:
            return f
    return None


# ─────────────────────────────────────────────
# TRADE ANALYSIS
#
# Returns a dict on success, OR a diagnostic dict
# with "blocked": True so the UI can always show
# exactly WHY — base conditions / filters / RR.
# Never returns bare None anymore.
# ─────────────────────────────────────────────
def _analyze_uncached(df: pd.DataFrame, ticker: str,
                      spy_regime: dict | None = None) -> dict:
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

    vol_ok      = volume >= vol_avg * VOLUME_MULT
    vol_soft_ok = volume >= vol_avg * 0.70

    # ── Base conditions ──
    if price > ema20 > ema50 and macd > signal and 30 < rsi < 75 and vol_soft_ok:
        trend = "Bullish"
    elif price < ema20 < ema50 and macd < signal and 25 < rsi < 70 and vol_soft_ok:
        trend = "Bearish"
    else:
        # Base conditions failed — return diagnostic so UI can show exactly what failed
        return {
            "blocked":       True,
            "block_reason":  "base",
            "price":         round(price, 2),
            "ema20":         round(ema20, 2),
            "ema50":         round(ema50, 2),
            "rsi":           round(rsi, 1),
            "macd":          round(macd, 4),
            "signal_line":   round(signal, 4),
            "vol_ratio":     round(volume / vol_avg, 2) if vol_avg else 0,
            "filters":       {},
        }

    strength = "Strong" if (
        ((rsi > 60 and trend == "Bullish") or (rsi < 40 and trend == "Bearish")) and vol_ok
    ) else "Normal"

    # ── 4 Enhancement filters ──
    filters: dict[str, dict] = {}
    adx_ok, adx_val = check_adx(df)
    filters["ADX Trend Strength"] = {"pass": adx_ok,
        "detail": f"ADX {adx_val} {'≥' if adx_ok else '<'} {ADX_MIN} threshold"}

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

    n_pass   = sum(1 for f in filters.values() if f["pass"])
    n_total  = len(filters)
    all_pass = (n_pass == n_total)

    # ── Entry / stop / target ──
    #
    # ROOT-CAUSE FIX: the old code mixed reference points, which made almost
    # every setup fail MIN_RR:
    #
    #   entry  = lookback_high            <- a PAST bar's high
    #   stop   = min(swing_low, price-atr)<- based on CURRENT price
    #   target = min(price+2.5atr, res*.99) <- based on CURRENT price
    #
    # Two failure modes resulted:
    #   1) In an uptrend price runs ABOVE the 5-bar lookback high, so `entry`
    #      sat BELOW price. Reward (target-entry) collapsed toward zero.
    #   2) The resistance cap `res_20 * 0.99` is BELOW price whenever price is
    #      at/near the 20-bar high — i.e. on the strongest breakouts — pulling
    #      the target BELOW the entry and giving R:R ~0.03. The logic actively
    #      penalised the exact setups it should reward.
    #
    # New approach: ALL THREE levels are anchored to the SAME reference —
    # current price — so they're internally coherent. Structure (swing levels)
    # informs the stop, and the resistance/support cap is only applied when it
    # is actually beyond the entry (i.e. a real obstacle), never behind it.

    swing_low_10  = float(df["Low"].tail(10).min())
    swing_high_10 = float(df["High"].tail(10).max())

    if trend == "Bullish":
        # Enter at market — trend/momentum conditions already confirmed.
        entry = round(price, 2)

        # Stop: prefer the structural level (just under the 10-bar swing low),
        # but ONLY if that level is actually below price. On a gap-down the
        # 10-bar low can sit ABOVE current price, which makes it meaningless
        # as a stop — in that case fall back to the pure ATR stop.
        #
        # BUG (fixed): the old `max(structural_stop, atr_stop)` would pick the
        # invalid above-price structural value, then the entry-0.01 clamp jammed
        # risk to one cent, producing phantom setups with R:R ≈ 750 that would be
        # stopped out on the first tick.
        atr_stop = price - (atr * 1.5)
        structural_stop = swing_low_10 - (atr * 0.10)

        if structural_stop < price:
            # Structure is valid — take the tighter (higher) of the two
            stop = max(structural_stop, atr_stop)
        else:
            # Structure unusable (swing low is at/above price) — ATR only
            stop = atr_stop

        stop = round(min(stop, entry - 0.01), 2)   # final safety clamp

        # Target: 2.5 ATR up. Cap at overhead resistance ONLY if that level is
        # at least 1 ATR above entry.
        #
        # BUG v2 (fixed): the previous threshold was a fixed 0.5% (entry*1.005).
        # In a steady uptrend the 20-bar high IS the most recent bar's high —
        # typically 0.5–1.5% above the close — so the cap fired on virtually
        # every clean trend and crushed the target to just above entry
        # (rr ≈ 0.2). "Meaningful resistance" must be measured in ATR: if the
        # 20-bar high is within 1 ATR, price is effectively AT its highs
        # (breakout) and there is no genuine overhead level to cap against.
        raw_target = price + (atr * 2.5)
        resistance = float(df["High"].tail(20).max())
        if resistance >= entry + (atr * 1.0):
            target = round(min(raw_target, resistance * 0.995), 2)
        else:
            target = round(raw_target, 2)     # at/near highs — no cap
        target = round(max(target, entry + 0.02), 2)

    else:  # Bearish
        entry = round(price, 2)

        atr_stop = price + (atr * 1.5)
        structural_stop = swing_high_10 + (atr * 0.10)

        if structural_stop > price:
            # Structure valid — take the tighter (lower) of the two
            stop = min(structural_stop, atr_stop)
        else:
            # Structure unusable (swing high is at/below price) — ATR only
            stop = atr_stop

        stop = round(max(stop, entry + 0.01), 2)

        raw_target = price - (atr * 2.5)
        support    = float(df["Low"].tail(20).min())
        # Same ATR-based threshold as bullish: only cap at support if it's at
        # least 1 ATR below entry. In a steady downtrend the 20-bar low is the
        # most recent bar's low — capping against it crushed every clean trend.
        if support <= entry - (atr * 1.0):
            target = round(max(raw_target, support * 1.005), 2)
        else:
            target = round(raw_target, 2)     # at/near lows — no cap
        target = round(min(target, entry - 0.02), 2)

    # ── Risk sanity gate ──
    # Guard must be RELATIVE to price, not an absolute penny. A $0.01 stop on a
    # $100 stock is 0.01% — it would be stopped out by any tick. Anything under
    # 0.3% of price is not a tradeable stop for a swing setup.
    risk     = abs(entry - stop)
    min_risk = max(0.05, price * 0.003)     # 0.3% of price, floor of 5 cents

    if risk < min_risk:
        return {
            "blocked": True, "block_reason": "zero_risk",
            "trend": trend, "price": round(price, 2),
            "entry": entry, "stop": stop,
            "risk": round(risk, 2), "min_risk": round(min_risk, 2),
            "filters": filters, "filters_pass": n_pass, "filters_total": n_total,
        }

    rr = round(abs(target - entry) / risk, 2)
    if rr < MIN_RR:
        return {
            "blocked": True, "block_reason": "rr",
            "trend": trend, "strength": strength,
            "price": round(price, 2), "entry": entry,
            "stop": stop, "target": target, "rr": rr,
            "filters": filters, "filters_pass": n_pass, "filters_total": n_total,
            "rsi": round(rsi, 1), "adx": adx_val,
        }

    option = get_option_data(ticker, price, trend, strength)

    # ── High-quality tier ──
    # Was hardcoded `rr >= 2.0`. With MIN_RR now user-tunable (default 0.5),
    # a fixed 2.0 bar meant the HIGH QUALITY tier almost never fired — and since
    # Telegram alerts only fire on high_quality, alerts went silent.
    # Now the bar is a sidebar tunable (HQ_MIN_RR) that defaults to 2× MIN_RR,
    # so it scales sensibly with whatever the user sets.
    high_quality = (rr >= HQ_MIN_RR and strength == "Strong" and all_pass)

    return {
        "blocked":       False,
        "ticker":        ticker,
        "price":         round(price, 2),
        "trend":         trend,
        "strength":      strength,
        "entry":         entry,
        "stop":          stop,
        "target":        target,
        "rr":            rr,
        "rsi":           round(rsi, 1),
        "atr":           round(atr, 2),
        "adx":           adx_val,
        "option":        option,
        "filters":       filters,
        "filters_pass":  n_pass,
        "filters_total": n_total,
        "all_pass":      all_pass,
        "high_quality":  high_quality,
    }


@st.cache_data(ttl=300, show_spinner=False)
def analyze(_df: pd.DataFrame, ticker: str, latest_bar_key: str,
            settings_key: str, spy_regime: dict | None = None) -> dict:
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
    return _analyze_uncached(_df, ticker, spy_regime=spy_regime)


def get_settings_key() -> str:
    """Fingerprint of all sidebar tunables that affect signal logic."""
    return (
        f"adx{ADX_MIN}_rr{MIN_RR}_hqrr{HQ_MIN_RR}_vol{VOLUME_MULT}"
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
    if df.empty: return None
    r = analyze(df, ticker, f"{ticker}_{df.index[-1]}", settings_key,
                spy_regime=spy_regime)
    return r if r and not r.get("blocked") else None


def _run_scan_uncached(scan_list: tuple, spy_regime: dict,
                       settings_key: str) -> list[dict]:
    """Does the actual parallel work — not cached so threads don't OOM cache."""
    data_map = batch_get_data(scan_list)
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
               f"Price ${latest_price:.2f} / EMA20 ${ema20_v:.2f} / EMA50 ${ema50_v:.2f}")
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
            f"Risk is only **${diag.get('risk', 0):.2f}** vs a minimum of "
            f"**${diag.get('min_risk', 0):.2f}** (0.3% of price). A stop this "
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
    st.line_chart(chart_df, height=220, use_container_width=True)
    st.caption(f"{ticker} — Close price with EMA 20 & EMA 50 (last 60 bars)")


def render_unusual_table(flagged: list, ticker_label: str = "", top_n: int = 5):
    if not flagged:
        st.info(f"No unusual activity detected{f' for {ticker_label}' if ticker_label else ''}.")
        return
    sev_rank = {"Extreme":3,"High":2,"Moderate":1}
    sev_map  = {"Extreme":"🔴","High":"🟠","Moderate":"🟡"}
    by_t: dict[str,list] = {}
    for f in flagged:
        by_t.setdefault(f["ticker"],[]).append(f)

    def t_key(t):
        best = max(by_t[t], key=lambda x:(sev_rank.get(x["severity"],0),x["volume"]))
        return (sev_rank.get(best["severity"],0), best["volume"])

    for t in sorted(by_t.keys(), key=t_key, reverse=True):
        contracts = sorted(by_t[t],
                           key=lambda x:(sev_rank.get(x["severity"],0),x["volume"]),
                           reverse=True)
        total_cnt = len(contracts)
        top_c     = contracts[:top_n]
        ext_n     = sum(1 for c in contracts if c["severity"]=="Extreme")
        high_n    = sum(1 for c in contracts if c["severity"]=="High")
        header    = f"**{t}** — {total_cnt} flagged" + (f" (top {top_n})" if total_cnt>top_n else "")
        badges    = " ".join(filter(None,[f"🔴 x{ext_n}" if ext_n else "",
                                          f"🟠 x{high_n}" if high_n else ""]))
        st.markdown(f"### {header}  {badges}")
        for f in top_c:
            se  = sev_map.get(f["severity"],"⚪")
            te  = "📈" if f["type"]=="CALL" else "📉"
            with st.container(border=True):
                u1,u2,u3,u4,u5 = st.columns([1,1,1.2,1,1.5])
                u1.markdown(f"{te} **{f['type']}**")
                u2.markdown(f"Strike **${f['strike']}**")
                u3.markdown(f"Exp {f['expiry']} ({f['dte']}d)")
                u4.markdown(f"{se} **{f['severity']}**")
                u5.markdown(f"Vol **{f['volume']:,}** / OI {f['oi']:,}")
                for reason in f["reasons"]:
                    st.caption(f"• {reason}")
        st.divider()


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
TAB_SCAN, TAB_STOCK, TAB_UNUSUAL, TAB_ALERTS, TAB_JOURNAL = st.tabs([
    "📡 Watchlist Scan", "🔍 Stock Analysis",
    "🌊 Unusual Activity", "🔔 Alert History", "📓 Trade Journal",
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

        # Store in session_state so other tabs (e.g. Unusual Activity quick-pick)
        # can safely read it without a NameError when no scan has run yet.
        st.session_state["all_setups"] = all_setups

        high_quality = [s for s in all_setups if s["high_quality"]]
        partial      = [s for s in all_setups if not s["high_quality"] and s["all_pass"]]
        weak         = [s for s in all_setups if not s["all_pass"]]

        for a in high_quality:
            log_alert(ticker=a["ticker"], trend=a["trend"], strength=a["strength"],
                      entry=a["entry"], stop=a["stop"], target=a["target"],
                      rr=a["rr"], price=a["price"], filters_passed=a["filters"])
            if market_open:
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
                    ps = calc_position_size(a["entry"], a["stop"])
                    if ps["affordable"]:
                        st.caption(
                            f"💰 Position sizing — Risk ${ps['risk_dollars']} · "
                            f"**{ps['shares']} shares** or **{ps['contracts']} contract(s)** "
                            f"(${ACCOUNT_SIZE:,} acct · {RISK_PCT}% risk)"
                        )
                    else:
                        st.caption(
                            f"💰 Position sizing — Risk ${ps['risk_dollars']} · "
                            f"**{ps['shares']} shares** · ⚠️ **0 contracts** "
                            f"(1 contract = ${ps['risk_per_contract']:,.2f} risk, "
                            f"over your {RISK_PCT}% limit)"
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
            intraday = get_data(ticker, period="5d", interval="5m")

        if df is None:
            st.error(f"❌ {fetch_error or f'Could not load data for {ticker}'}")
            if fetch_error and "Rate limited" in fetch_error:
                st.caption("Data is cached 10 min once loaded — only affects fresh lookups.")
        else:
            df = compute(df)
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
                             else "✅ VALID — all filters pass" if r["all_pass"]
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
                    ps = calc_position_size(r["entry"], r["stop"])
                    if ps["affordable"]:
                        st.info(
                            f"💰 **Position Sizing** — "
                            f"Risk ${ps['risk_dollars']} ({RISK_PCT}% of ${ACCOUNT_SIZE:,}) · "
                            f"**{ps['shares']} shares** · **{ps['contracts']} option contract(s)** "
                            f"(1 contract = ${ps['risk_per_contract']:,.2f} risk)"
                        )
                    else:
                        st.warning(
                            f"⚠️ **Options exceed your risk limit** — "
                            f"Risk budget is ${ps['risk_dollars']} "
                            f"({RISK_PCT}% of ${ACCOUNT_SIZE:,}).\n\n{ps['note']}"
                        )
                    if not r["all_pass"]:
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
                        st.caption(f"Spread: ${opt['spread']} ({spread_pct:.1f}% of mid) · Last: ${opt['last_price']}")
                        if opt["is_budget"]:
                            st.success(f"💸 Budget pick — ${opt['mid']}/contract (under ${BUDGET_MAX:.2f})")
                        if not r["all_pass"]:
                            st.warning("⚠️ Not all filters pass — trade at your own discretion.")
                        ua_hit = check_pick_unusual_activity(ticker, opt)
                        if ua_hit:
                            se = {"Extreme":"🔴","High":"🟠","Moderate":"🟡"}.get(ua_hit["severity"],"⚪")
                            st.markdown(f"### {se} Unusual Activity — {ua_hit['severity']}")
                            for reason in ua_hit["reasons"]:
                                st.caption(f"• {reason}")
                        else:
                            st.caption("🌊 No unusual activity on this contract.")

            with stab4:
                if intraday is None or len(intraday) < 30:
                    st.warning("Not enough intraday bars (need ≥ 30). "
                               "Try again once the session has more data.")
                else:
                    intraday = compute(intraday)
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
                            f"✅ **{opt['label']}** · Strike ${opt['strike']} · "
                            f"Exp {opt['expiry']} ({opt['dte']} DTE) · "
                            f"Mid **${opt['mid']}** · Vol {opt['volume']:,} · OI {opt['oi']:,}"
                        )
                        st.caption("Budget options carry higher gamma risk — size accordingly.")
                    else:
                        st.info(f"Best contract is ${opt['mid']}/contract — above ${BUDGET_MAX:.2f}. "
                                "Try a wider strike or longer expiry.")

            st.divider()
            st.caption("⚠️ Not financial advice. Rule-based signals only.")


# ═══════════════════════════════════════════════
# TAB 3 — UNUSUAL ACTIVITY
# ═══════════════════════════════════════════════
with TAB_UNUSUAL:
    st.subheader("🌊 Unusual Options Activity Scanner")
    st.caption("Flags contracts where Volume >> Open Interest (fresh same-day positioning) "
               "or Volume >> peer strikes in the same chain.")

    ua_c1, ua_c2 = st.columns([2,1])
    with ua_c1:
        # FIX #7: quick-pick dropdown of already-scanned tickers.
        # Read from session_state — all_setups is only defined inside TAB_SCAN's
        # else-branch, so referencing it directly would NameError before a scan.
        _setups         = st.session_state.get("all_setups", [])
        scanned_tickers = [s["ticker"] for s in _setups if isinstance(s, dict) and "ticker" in s]
        quick_picks     = ["— type below —"] + sorted(scanned_tickers) + ["Other…"]
        quick_choice    = st.selectbox("Quick-pick from watchlist scan",
                                       quick_picks, key="ua_quick_pick")
        if quick_choice not in ("— type below —","Other…"):
            ua_ticker_input = quick_choice
        else:
            ua_ticker_input = st.text_input("Or enter any ticker",
                                            placeholder="TSLA", key="ua_ticker_input")
    with ua_c2:
        ua_scan_watchlist = st.checkbox("Scan full watchlist instead", key="ua_scan_watchlist")

    st.divider()

    if ua_scan_watchlist:
        all_flagged = []
        prog = st.progress(0, text="Starting scan…")
        for i, t in enumerate(SCAN_LIST):
            prog.progress((i+1)/len(SCAN_LIST), text=f"Scanning {t}…")
            res = scan_unusual_activity(t)
            if "error" not in res:
                all_flagged.extend(res.get("flagged",[]))
        prog.empty()
        sev_rank = {"Extreme":3,"High":2,"Moderate":1}
        all_flagged.sort(key=lambda x:(sev_rank.get(x["severity"],0),x["volume"]),reverse=True)
        wc1,wc2,wc3 = st.columns(3)
        wc1.metric("Total Flagged",    len(all_flagged))
        wc2.metric("Extreme",          sum(1 for f in all_flagged if f["severity"]=="Extreme"))
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
            flagged = result.get("flagged",[])
            fc1,fc2,fc3 = st.columns(3)
            fc1.metric("Flagged Contracts", len(flagged))
            fc2.metric("Extreme",           sum(1 for f in flagged if f["severity"]=="Extreme"))
            fc3.metric("Expiries Checked",  result.get("expiries_checked",0))
            st.divider()
            render_unusual_table(flagged, ticker_ua)
    else:
        st.info("Pick a ticker from the dropdown or type one above, "
                "or tick the box to scan the full watchlist.")

    st.divider()
    st.markdown("**Severity guide**")
    st.caption(f"🟡 Moderate — Vol ≥ {UA_VOL_OI_RATIO_MIN}x OI or ≥ {UA_PEER_MULTIPLE_MIN}x peer median")
    st.caption("🟠 High — both conditions simultaneously")
    st.caption(f"🔴 Extreme — Vol ≥ {UA_VOL_OI_RATIO_HIGH}x OI AND ≥ {UA_PEER_MULTIPLE_MIN}x peer median")
    st.caption(f"Contracts with < {UA_MIN_VOLUME} traded are ignored as noise.")
    st.caption("⚠️ Not financial advice. Heuristic screen — not confirmed institutional flow.")


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
        st.warning(
            "**Streamlit Cloud containers are stateless.** Your journal is written "
            "to disk, but that disk is wiped on every redeploy, restart, or idle "
            "timeout. **Export regularly** to avoid losing your trade history."
        )
        ex1, ex2 = st.columns(2)
        with ex1:
            st.markdown("**📥 Export**")
            backup = {
                "exported_at": datetime.now(pytz.timezone("America/New_York")).isoformat(),
                "journal":     journal,
                "alerts":      alerts,
            }
            st.download_button(
                "⬇️ Download backup (.json)",
                data=json.dumps(backup, indent=2, default=str),
                file_name=f"trading_copilot_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
                mime="application/json",
                use_container_width=True,
            )
            st.caption(f"{len(journal)} trades · {len(alerts)} alerts")

        with ex2:
            st.markdown("**📤 Restore**")
            uploaded = st.file_uploader("Upload a backup .json", type=["json"],
                                        key="journal_restore", label_visibility="collapsed")
            if uploaded is not None:
                try:
                    payload = json.load(uploaded)
                    n_j = len(payload.get("journal", []))
                    n_a = len(payload.get("alerts", []))
                    st.caption(f"Found {n_j} trades · {n_a} alerts")
                    if st.button("♻️ Restore (overwrites current)", type="primary",
                                 use_container_width=True, key="do_restore"):
                        save_journal(payload.get("journal", []))
                        save_alerts(payload.get("alerts", []))
                        st.success(f"Restored {n_j} trades and {n_a} alerts.")
                        st.rerun()
                except Exception as e:
                    st.error(f"Invalid backup file: {e}")

    if stats:
        st.markdown("### 📊 Performance Dashboard")
        m1,m2,m3,m4,m5,m6 = st.columns(6)
        m1.metric("Total Trades",  stats["total"])
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
            st.line_chart(eq_df, height=200, use_container_width=True)
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

        filtered_j = journal
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
                st.caption(f"Stop: ${j['stop']} · Target: ${j['target']} · Alerted: {short_ts(j['date'])}")
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
