# app.py
import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import numpy as np
import altair as alt
from datetime import datetime
import pytz
import json
from pathlib import Path
import time
import logging

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("trading_copilot")

# ---------------------------
# PAGE CONFIG
# ---------------------------
st.set_page_config(page_title="Trading Copilot ELITE", page_icon="🤖", layout="wide")
st.title("🤖 Trading Copilot ELITE")
st.caption("Swing · Options Flow · Alerts · Journal · Quant Confidence · Dashboards")

# ---------------------------
# CONFIG
# ---------------------------
WATCHLIST = ["TSLA","NVDA","AAPL","MSFT","AMZN","META","SPY","AMD","NFLX","SHOP"]
FAST_MODE = st.sidebar.checkbox("Fast mode (scan first 5 only)", value=False)
SCAN_LIST = WATCHLIST[:5] if FAST_MODE else WATCHLIST

ADX_MIN = st.sidebar.number_input("ADX minimum", value=25, min_value=1, max_value=100)
MIN_ROWS = st.sidebar.number_input("Min history bars", value=50, min_value=10)
BUDGET_MAX = st.sidebar.number_input("Budget max (option mid)", value=2.00, min_value=0.01, step=0.1)
EARNINGS_DAYS = st.sidebar.number_input("Earnings blackout days", value=3, min_value=0, max_value=30)

single_ticker = st.sidebar.text_input("🔍 Lookup single ticker", value="")

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
    except Exception as e:
        logger.exception("Failed to load %s: %s", path, e)
        return []

def _save(path: Path, data: list) -> None:
    try:
        path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.exception("Failed to save %s: %s", path, e)

def load_alerts(): return _load(ALERT_LOG_FILE)
def save_alerts(alerts): _save(ALERT_LOG_FILE, alerts)
def load_journal(): return _load(JOURNAL_FILE)
def save_journal(journal): _save(JOURNAL_FILE, journal)

# ---------------------------
# Data fetch
# ---------------------------
@st.cache_data(ttl=900, show_spinner=False)
def get_data(ticker, period="3mo", interval="1d"):
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False)
        if df is None or df.empty or len(df) < MIN_ROWS:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        logger.exception("get_data failed for %s: %s", ticker, e)
        return None

# ---------------------------
# Indicators
# ---------------------------
def compute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["EMA20"] = ta.trend.ema_indicator(df["Close"], window=20)
    df["EMA50"] = ta.trend.ema_indicator(df["Close"], window=50)
    df["RSI"] = ta.momentum.rsi(df["Close"], window=14)
    df["ADX"] = ta.trend.adx(df["High"], df["Low"], df["Close"], window=14)
    df["ATR"] = ta.volatility.average_true_range(df["High"], df["Low"], df["Close"], window=14)
    # VWAP cumulative (approx for daily series)
    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    cum_vol = df["Volume"].cumsum().replace(0, np.nan)
    df["VWAP"] = (typical * df["Volume"]).cumsum() / cum_vol
    df["CMF"] = ta.volume.chaikin_money_flow(df["High"], df["Low"], df["Close"], df["Volume"], window=20)
    return df.dropna()

# ---------------------------
# Confidence scoring
# ---------------------------
def generate_signal(ticker, df):
    price = float(df["Close"].iloc[-1])
    ema20, ema50 = df["EMA20"].iloc[-1], df["EMA50"].iloc[-1]
    adx_val, rsi_val, cmf_val = df["ADX"].iloc[-1], df["RSI"].iloc[-1], df["CMF"].iloc[-1]

    if price > ema20 > ema50:
        trend = "Bullish"
    elif price < ema20 < ema50:
        trend = "Bearish"
    else:
        trend = "Neutral"

    score = 0.0
    score += min(40.0, float(adx_val))
    score += 20.0 if (trend == "Bullish" and cmf_val > 0) or (trend == "Bearish" and cmf_val < 0) else 0.0
    score += 10.0 if 40.0 <= float(rsi_val) <= 60.0 else 0.0
    confidence = "High" if score >= 70 else "Medium" if score >= 50 else "Low"

    return {
        "ticker": ticker,
        "trend": trend,
        "price": round(price, 2),
        "adx": round(float(adx_val), 1),
        "rsi": round(float(rsi_val), 1),
        "cmf": round(float(cmf_val), 3),
        "confidence_score": round(score, 1),
        "confidence": confidence,
    }

# ---------------------------
# Option chain (robust)
# ---------------------------
@st.cache_data(ttl=900, show_spinner=False)
def get_option_chain(ticker):
    """
    Robust option chain fetch:
    - Handles yfinance rate errors and returns an empty DataFrame on failure.
    - Returns combined calls+puts with mid and spread columns where available.
    """
    try:
        t = yf.Ticker(ticker)
        expiries = []
        try:
            expiries = t.options or []
        except Exception as e:
            logger.warning("Could not fetch expiries for %s: %s", ticker, e)
            expiries = []

        expiries = expiries[:2]  # limit to first 2 expiries to reduce load
        frames = []
        for expiry in expiries:
            try:
                oc = t.option_chain(expiry)
                for side_df in (oc.calls, oc.puts):
                    if side_df is None or side_df.empty:
                        continue
                    df_side = side_df.copy()
                    # normalize column names
                    df_side = df_side.rename(columns={c: c.strip() if isinstance(c, str) else c for c in df_side.columns})
                    if "bid" in df_side.columns and "ask" in df_side.columns:
                        df_side["mid"] = (df_side["bid"].fillna(0) + df_side["ask"].fillna(0)) / 2.0
                        df_side["spread"] = (df_side["ask"].fillna(0) - df_side["bid"].fillna(0)).abs()
                    else:
                        df_side["mid"] = np.nan
                        df_side["spread"] = np.nan
                    df_side["expiry"] = expiry
                    # add a 'type' column if not present to indicate call/put
                    if "contractSymbol" in df_side.columns and "type" not in df_side.columns:
                        # infer type from presence of call/put columns: oc.calls vs oc.puts
                        df_side["type"] = "CALL" if side_df is oc.calls else "PUT"
                    frames.append(df_side)
                # small delay to be polite
                time.sleep(0.15)
            except Exception as e:
                logger.warning("Failed to fetch option chain for %s expiry %s: %s", ticker, expiry, e)
                continue

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True, sort=False)
    except Exception as e:
        logger.exception("get_option_chain top-level failure for %s: %s", ticker, e)
        return pd.DataFrame()

# ---------------------------
# Alerts & journal helpers
# ---------------------------
def log_alert(ticker, trend, entry, stop, target, rr, price, filters_passed: dict):
    alerts = load_alerts()
    alerts.append({
        "id": f"{ticker}_{int(time.time())}",
        "timestamp": datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d %H:%M ET"),
        "ticker": ticker,
        "trend": trend,
        "price": price,
        "entry": entry,
        "stop": stop,
        "target": target,
        "rr": rr,
        "filters_passed": filters_passed,
        "journaled": False,
    })
    save_alerts(alerts)

def add_journal_trade(alert_id, ticker, trend, entry, stop, target, rr, exit_price, outcome, notes, setup_date):
    journal = load_journal()
    risk = abs(entry - stop) if entry is not None and stop is not None else 1.0
    pnl_r = round((exit_price - entry) / risk, 2) if trend == "Bullish" else round((entry - exit_price) / risk, 2)
    journal = [j for j in journal if j.get("id") != alert_id]
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

def journal_stats(journal: list) -> dict:
    if not journal: return {}
    wins = [j for j in journal if j.get("outcome") == "WIN"]
    losses = [j for j in journal if j.get("outcome") == "LOSS"]
    total = len(journal)
    wr = round(len(wins) / total * 100, 1) if total else 0
    avg_win = round(sum(j.get("actual_rr", 0) for j in wins) / len(wins), 2) if wins else 0
    avg_loss = round(sum(j.get("actual_rr", 0) for j in losses) / len(losses), 2) if losses else 0
    pf = round((sum(j.get("actual_rr", 0) for j in wins) / max(1e-6, abs(sum(j.get("actual_rr", 0) for j in losses)))) if losses else 0, 2)
    return {"trades": total, "win_rate_%": wr, "avg_win_R": avg_win, "avg_loss_R": avg_loss, "profit_factor": pf}

# ---------------------------
# Dashboard (sorted by confidence)
# ---------------------------
def show_dashboard(results):
    st.subheader("📊 Signal Dashboard")
    df = pd.DataFrame(results)
    if df.empty:
        st.info("No signals to display.")
        return

    # Sort by confidence_score descending, then confidence label (High/Medium/Low)
    df = df.sort_values(by=["confidence_score", "confidence"], ascending=[False, True]).reset_index(drop=True)

    st.dataframe(df)

    chart = alt.Chart(df).mark_bar().encode(
        x=alt.X("ticker:N", sort=None),
        y="confidence_score:Q",
        color=alt.condition(alt.datum.trend == "Bullish", alt.value("green"), alt.value("red")),
        tooltip=["ticker", "trend", "confidence_score", "adx", "rsi"]
    ).properties(title="Trend Confidence by Ticker")
    st.altair_chart(chart, use_container_width=True)

    rsi_chart = alt.Chart(df).mark_bar().encode(
        x=alt.X("rsi:Q", bin=alt.Bin(maxbins=10)),
        y="count()",
        tooltip=["rsi"]
    ).properties(title="RSI Distribution")
    st.altair_chart(rsi_chart, use_container_width=True)

# ---------------------------
# Main scan
# ---------------------------
results = []
for t in SCAN_LIST:
    df = get_data(t)
    if df is None:
        logger.info("No data for %s or insufficient history", t)
        continue
    try:
        dfc = compute(df)
    except Exception as e:
        logger.exception("Indicator compute failed for %s: %s", t, e)
        continue
    sig = generate_signal(t, dfc)
    results.append(sig)

if results:
    show_dashboard(results)
else:
    st.warning("No signals generated — check settings or watchlist.")

# ---------------------------
# Single ticker diagnostics & trade suggestion (option chain sorted)
# ---------------------------
if single_ticker:
    df = get_data(single_ticker)
    if df is None:
        st.error(f"No data or insufficient history for {single_ticker}")
    else:
        try:
            dfc = compute(df)
        except Exception as e:
            st.exception(f"Indicator compute failed for {single_ticker}: {e}")
            dfc = None

        if dfc is not None:
            sig = generate_signal(single_ticker, dfc)
            st.subheader(f"📈 {single_ticker} Diagnostics")
            st.write(sig)

            # Trade suggestion (ATR-based)
            atr = float(dfc["ATR"].iloc[-1])
            entry = sig["price"]
            if sig["trend"] == "Bullish":
                stop, target = round(entry - atr, 2), round(entry + 2 * atr, 2)
            elif sig["trend"] == "Bearish":
                stop, target = round(entry + atr, 2), round(entry - 2 * atr, 2)
            else:
                stop, target = None, None

            if stop is not None and target is not None:
                rr = round(abs(target - entry) / max(1e-6, abs(entry - stop)), 2)
                st.markdown(f"**Trade Idea:** Entry **{entry}**, Stop **{stop}**, Target **{target}**, RR **{rr}**")
                if st.button(f"Log alert for {single_ticker}"):
                    filters_passed = {"adx": sig["adx"] >= ADX_MIN}
                    log_alert(single_ticker, sig["trend"], entry, stop, target, rr, sig["price"], filters_passed)
                    st.success("Alert logged")

            # Option chain (robust) and sorted by liquidity/confidence proxies
            opt_chain = get_option_chain(single_ticker)
            st.subheader("💹 Option Chain (sorted by volume, OI, tight spread)")
            if opt_chain is None or opt_chain.empty:
                st.info("Option chain not available or failed to fetch for this ticker.")
            else:
                # Ensure numeric columns exist
                for col in ["volume", "openInterest", "spread", "mid"]:
                    if col not in opt_chain.columns:
                        opt_chain[col] = np.nan
                # Sort: highest volume, highest OI, lowest spread
                sort_cols = []
                if "volume" in opt_chain.columns:
                    sort_cols.append("volume")
                if "openInterest" in opt_chain.columns:
                    sort_cols.append("openInterest")
                if "spread" in opt_chain.columns:
                    sort_cols.append("spread")
                # Build ascending flags: volume desc, OI desc, spread asc
                ascending = [False if c in ("volume", "openInterest") else True for c in sort_cols]
                opt_sorted = opt_chain.sort_values(by=sort_cols, ascending=ascending, na_position="last")
                display_cols = [c for c in ["expiry", "contractSymbol", "type", "strike", "lastPrice", "mid", "spread", "volume", "openInterest"] if c in opt_sorted.columns]
                st.dataframe(opt_sorted[display_cols].head(20))

            # Journal stats display and quick add
            journal = load_journal()
            stats = journal_stats(journal)
            if stats:
                st.subheader("📒 Journal Performance")
                st.write(stats)

            st.subheader("✍️ Add Journal Entry (manual)")
            with st.form("journal_form", clear_on_submit=True):
                alert_id = st.text_input("Alert ID (optional)")
                exit_price = st.number_input("Exit price", value=entry)
                outcome = st.selectbox("Outcome", ["WIN", "LOSS", "BREAKEVEN"])
                notes = st.text_area("Notes")
                submitted = st.form_submit_button("Add to Journal")
                if submitted:
                    setup_date = datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d")
                    add_journal_trade(alert_id or f"{single_ticker}_{int(time.time())}", single_ticker, sig["trend"], entry, stop, target, rr if 'rr' in locals() else None, exit_price, outcome, notes, setup_date)
                    st.success("Journal entry added")

# ---------------------------
# Alerts list and quick view
# ---------------------------
st.sidebar.markdown("### Alerts & Journal")
alerts = load_alerts()
if alerts:
    st.sidebar.write(f"Alerts: {len(alerts)}")
    for a in alerts[-10:][::-1]:
        st.sidebar.markdown(f"- **{a.get('ticker','?')}** {a.get('timestamp','?')} RR:{a.get('rr','?')}")
else:
    st.sidebar.write("No alerts logged")

# ---------------------------
# Footer / tips
# ---------------------------
st.markdown("---")
st.markdown("**Notes:** This app uses yfinance for data and option chains. Option chain fetches can fail or be rate-limited by the provider; the app handles failures gracefully and shows an empty table when unavailable.")
