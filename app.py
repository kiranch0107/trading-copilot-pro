# app.py
import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import pytz

# ---------------------------
# PAGE CONFIG
# ---------------------------
st.set_page_config(page_title="Trading Copilot ELITE", page_icon="🤖", layout="wide")
st.title("🤖 Trading Copilot ELITE")
st.caption("Swing · Options Flow · Alerts · Journal · Quant Confidence · Dashboards")

# ---------------------------
# CONFIG
# ---------------------------
WATCHLIST = ["TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "META", "SPY", "AMD", "NFLX", "SHOP"]

FAST_MODE = st.sidebar.checkbox("Fast mode (scan first 5 only)", value=False)
SCAN_LIST = WATCHLIST[:5] if FAST_MODE else WATCHLIST

ADX_MIN = st.sidebar.number_input("ADX minimum", value=25, min_value=1, max_value=100)
MIN_ROWS = st.sidebar.number_input("Min history bars", value=50, min_value=10)
BUDGET_MAX = st.sidebar.number_input("Budget max (option mid)", value=2.00, min_value=0.01, step=0.1)

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
    score += min(40, adx_val)        # ADX strength
    score += 20 if (trend == "Bullish" and cmf_val > 0) or (trend == "Bearish" and cmf_val < 0) else 0
    score += 10 if 40 <= rsi_val <= 60 else 0
    confidence = "High" if score >= 70 else "Medium" if score >= 50 else "Low"

    return {
        "ticker": ticker,
        "trend": trend,
        "price": round(price, 2),
        "adx": round(adx_val, 1),
        "rsi": round(rsi_val, 1),
        "cmf": round(cmf_val, 2),
        "confidence_score": score,
        "confidence": confidence,
    }

# ---------------------------
# Dashboard
# ---------------------------
def show_dashboard(results):
    st.subheader("📊 Signal Dashboard")

    df = pd.DataFrame(results)
    st.dataframe(df)

    # Heatmap of confidence scores
    fig, ax = plt.subplots(figsize=(8,4))
    ax.bar(df["ticker"], df["confidence_score"], color=["green" if t=="Bullish" else "red" for t in df["trend"]])
    ax.set_ylabel("Confidence Score")
    ax.set_title("Trend Confidence by Ticker")
    st.pyplot(fig)

    # RSI distribution
    fig2, ax2 = plt.subplots(figsize=(8,4))
    ax2.hist(df["rsi"], bins=10, color="blue", alpha=0.7)
    ax2.set_title("RSI Distribution")
    st.pyplot(fig2)

# ---------------------------
# Main scan
# ---------------------------
results = []
for t in SCAN_LIST:
    df = get_data(t)
    if df is None:
        continue
    dfc = compute(df)
    sig = generate_signal(t, dfc)
    results.append(sig)

if results:
    show_dashboard(results)
else:
    st.warning("No signals generated — check settings or watchlist.")
