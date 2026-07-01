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
    except:
        return []

def _save(path: Path, data: list) -> None:
    path.write_text(json.dumps(data, indent=2))

def load_alerts(): return _load(ALERT_LOG_FILE)
def save_alerts(alerts): _save(ALERT_LOG_FILE, alerts)
def load_journal(): return _load(JOURNAL_FILE)
def save_journal(journal): _save(JOURNAL_FILE, journal)

# ---------------------------
# Data fetch
# ---------------------------
@st.cache_data(ttl=900)
def get_data(ticker, period="3mo", interval="1d"):
    df = yf.download(ticker, period=period, interval=interval, progress=False)
    if df is None or df.empty or len(df) < MIN_ROWS:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

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
    df["VWAP"] = (df["Volume"] * (df["High"]+df["Low"]+df["Close"])/3).cumsum() / df["Volume"].cumsum()
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

    score = 0
    score += min(40, adx_val)
    score += 20 if (trend=="Bullish" and cmf_val>0) or (trend=="Bearish" and cmf_val<0) else 0
    score += 10 if 40 <= rsi_val <= 60 else 0
    confidence = "High" if score >= 70 else "Medium" if score >= 50 else "Low"

    return {
        "ticker": ticker,
        "trend": trend,
        "price": round(price,2),
        "adx": round(adx_val,1),
        "rsi": round(rsi_val,1),
        "cmf": round(cmf_val,2),
        "confidence_score": score,
        "confidence": confidence,
    }

# ---------------------------
# Option chain
# ---------------------------
@st.cache_data(ttl=900)
def get_option_chain(ticker):
    t = yf.Ticker(ticker)
    expiries = t.options[:2]
    chain = []
    for expiry in expiries:
        oc = t.option_chain(expiry)
        calls = oc.calls.copy()
        calls["mid"] = (calls["bid"]+calls["ask"])/2
        calls["spread"] = (calls["ask"]-calls["bid"]).abs()
        calls["expiry"] = expiry
        chain.append(calls)
    return pd.concat(chain)

# ---------------------------
# Dashboard
# ---------------------------
def show_dashboard(results):
    st.subheader("📊 Signal Dashboard")
    df = pd.DataFrame(results)
    st.dataframe(df)

    chart = alt.Chart(df).mark_bar().encode(
        x="ticker", y="confidence_score",
        color=alt.condition(alt.datum.trend=="Bullish", alt.value("green"), alt.value("red"))
    ).properties(title="Trend Confidence by Ticker")
    st.altair_chart(chart, use_container_width=True)

    rsi_chart = alt.Chart(df).mark_bar().encode(
        x=alt.X("rsi", bin=alt.Bin(maxbins=10)), y="count()"
    ).properties(title="RSI Distribution")
    st.altair_chart(rsi_chart, use_container_width=True)

# ---------------------------
# Journal stats
# ---------------------------
def journal_stats(journal: list) -> dict:
    if not journal: return {}
    wins = [j for j in journal if j["outcome"]=="WIN"]
    losses = [j for j in journal if j["outcome"]=="LOSS"]
    total = len(journal)
    wr = round(len(wins)/total*100,1) if total else 0
    avg_win = round(sum(j["actual_rr"] for j in wins)/len(wins),2) if wins else 0
    avg_loss = round(sum(j["actual_rr"] for j in losses)/len(losses),2) if losses else 0
    pf = round((sum(j["actual_rr"] for j in wins)/max(1e-6,abs(sum(j["actual_rr"] for j in losses)))) if losses else 0,2)
    return {"trades":total,"win_rate_%":wr,"avg_win_R":avg_win,"avg_loss_R":avg_loss,"profit_factor":pf}

# ---------------------------
# Main scan
# ---------------------------
results = []
for t in SCAN_LIST:
    df = get_data(t)
    if df is None: continue
    dfc = compute(df)
    sig = generate_signal(t, dfc)
    results.append(sig)

if results: show_dashboard(results)
else: st.warning("No signals generated — check settings or watchlist.")

# ---------------------------
# Single ticker diagnostics
# ---------------------------
if single_ticker:
    df = get_data(single_ticker)
    if df is not None:
        dfc = compute(df)
        sig = generate_signal(single_ticker, dfc)
        st.subheader(f"📈 {single_ticker} Diagnostics")
        st.write(sig)

        atr = dfc["ATR"].iloc[-1]
        entry = sig["price"]
        if sig["trend"]=="Bullish":
            stop, target = entry-atr, entry+2*atr
        elif sig["trend"]=="Bearish":
            stop, target = entry+atr, entry-2*atr
        else:
            stop, target = None, None

        if stop and target:
            rr = round(abs(target-entry)/abs(entry-stop),2)
            st.markdown(f"**Trade Idea:** Entry {entry}, Stop {stop},
