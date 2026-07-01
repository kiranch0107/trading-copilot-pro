# app.py
import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import numpy as np
import altair as alt
from datetime import datetime, timedelta
import pytz
import json
from pathlib import Path
import time
import logging
from math import log, sqrt, exp
from scipy.stats import norm

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
# CONFIG / SIDEBAR
# ---------------------------
WATCHLIST = ["TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "META", "SPY", "AMD", "NFLX", "SHOP"]
FAST_MODE = st.sidebar.checkbox("Fast mode (scan first 5 only)", value=False)
SCAN_LIST = WATCHLIST[:5] if FAST_MODE else WATCHLIST

ADX_MIN = st.sidebar.number_input("ADX minimum", value=25, min_value=1, max_value=100)
MIN_ROWS = st.sidebar.number_input("Min history bars", value=50, min_value=10)
BUDGET_MAX = st.sidebar.number_input("Budget max (option mid)", value=2.00, min_value=0.01, step=0.1)
EARNINGS_DAYS = st.sidebar.number_input("Earnings blackout days", value=3, min_value=0, max_value=30)

single_ticker = st.sidebar.text_input("🔍 Lookup single ticker", value="")

# Strategy selector
strategy = st.sidebar.selectbox(
    "Option Scoring Strategy",
    ["sell_premium", "buy_directional", "hedge"],
    format_func=lambda x: {
        "sell_premium": "Premium Selling",
        "buy_directional": "Directional Buying",
        "hedge": "Hedging"
    }[x]
)

ALERT_LOG_FILE = Path("alert_history.json")
JOURNAL_FILE = Path("trade_journal.json")
IV_HISTORY_FILE = Path("iv_history.json")

# ---------------------------
# Black-Scholes Greeks
# ---------------------------
def black_scholes_greeks(S, K, T, r, sigma, option_type="call"):
    try:
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return {"delta": np.nan, "gamma": np.nan, "theta": np.nan, "vega": np.nan}
        d1 = (log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt(T))
        d2 = d1 - sigma * sqrt(T)
        if option_type.lower() in ("call", "c"):
            delta = norm.cdf(d1)
            theta = -(S * norm.pdf(d1) * sigma) / (2 * sqrt(T)) - r * K * exp(-r * T) * norm.cdf(d2)
        else:
            delta = -norm.cdf(-d1)
            theta = -(S * norm.pdf(d1) * sigma) / (2 * sqrt(T)) + r * K * exp(-r * T) * norm.cdf(-d2)
        gamma = norm.pdf(d1) / (S * sigma * sqrt(T))
        vega = S * norm.pdf(d1) * sqrt(T)
        return {"delta": float(delta), "gamma": float(gamma), "theta": float(theta), "vega": float(vega)}
    except Exception as e:
        logger.exception("Black-Scholes greeks calculation failed: %s", e)
        return {"delta": np.nan, "gamma": np.nan, "theta": np.nan, "vega": np.nan}

# ---------------------------
# Option chain with IV Rank, Greeks, and strategy-aware scoring
# ---------------------------
@st.cache_data(ttl=900, show_spinner=False)
def get_option_chain_with_greeks(ticker, strategy="sell_premium", risk_free_rate=0.03):
    try:
        t = yf.Ticker(ticker)
        expiries = t.options[:3] if t.options else []
        frames = []
        for expiry in expiries:
            oc = t.option_chain(expiry)
            for side_name, side_df in (("CALL", oc.calls), ("PUT", oc.puts)):
                if side_df is None or side_df.empty:
                    continue
                df_side = side_df.copy()
                df_side["mid"] = (df_side["bid"].fillna(0) + df_side["ask"].fillna(0)) / 2.0
                df_side["spread"] = (df_side["ask"].fillna(0) - df_side["bid"].fillna(0)).abs()
                df_side["expiry"] = expiry
                df_side["type"] = side_name
                frames.append(df_side)
        if not frames:
            return pd.DataFrame()
        opt = pd.concat(frames, ignore_index=True, sort=False)
        opt["impliedVolatility"] = pd.to_numeric(opt.get("impliedVolatility", np.nan), errors="coerce")

        spot = float(t.history(period="1d")["Close"].iloc[-1]) if not t.history(period="1d").empty else np.nan
        rows = []
        iv_min, iv_max = opt["impliedVolatility"].min(), opt["impliedVolatility"].max()
        for _, row in opt.iterrows():
            K = float(row.get("strike", np.nan))
            expiry_dt = pd.to_datetime(row["expiry"]).to_pydatetime()
            days_to_expiry = max((expiry_dt - datetime.now()).days, 0)
            T = max(days_to_expiry / 365.0, 1e-6)
            iv = float(row.get("impliedVolatility", np.nan))
            iv_rank = (iv - iv_min) / (iv_max - iv_min) if iv_max > iv_min else np.nan
            greeks = black_scholes_greeks(S=spot, K=K, T=T, r=risk_free_rate, sigma=iv, option_type=row["type"].lower())

            vol = float(row.get("volume", 0))
            oi = float(row.get("openInterest", 0))
            spread = float(row.get("spread", np.nan))
            mid = float(row.get("mid", np.nan))

            # Strategy-aware scoring
            score = 0.0
            if not np.isnan(iv_rank):
                if strategy == "sell_premium":
                    score += iv_rank * 40.0
                elif strategy == "buy_directional":
                    score += (1.0 - iv_rank) * 30.0
                elif strategy == "hedge":
                    score += iv_rank * 10.0
            delta = greeks.get("delta", np.nan)
            if not np.isnan(delta):
                if strategy == "buy_directional":
                    score += max(0.0, (0.5 - abs(delta - 0.5))) * 25.0
                elif strategy == "hedge":
                    score += min(20.0, abs(delta) * 20.0)
            vega = greeks.get("vega", np.nan)
            if not np.isnan(vega):
                if strategy == "sell_premium":
                    score += min(15.0, vega / 10.0)
                elif strategy == "buy_directional":
                    score += min(8.0, vega / 20.0)
            score += min(20.0, np.log1p(vol) * 2.0) if vol > 0 else 0.0
            score += min(15.0, np.log1p(oi) * 1.5) if oi > 0 else 0.0
            if not np.isnan(spread) and spread > 0:
                score -= min(10.0, spread * 5.0)
            if not np.isnan(mid) and mid > 0:
                score += min(5.0, mid / max(1.0, spot) * 5.0)

            row_out = row.to_dict()
            row_out.update({
                "iv_rank": round(iv_rank, 3) if not np.isnan(iv_rank) else np.nan,
                "delta": round(delta, 4) if not np.isnan(delta) else np.nan,
                "theta": round(greeks.get("theta", np.nan), 6),
                "vega": round(vega, 4) if not np.isnan(vega) else np.nan,
                "option_score": round(score, 3),
                "days_to_expiry": days_to_expiry
            })
            rows.append(row_out)
        return pd.DataFrame(rows)
    except Exception as e:
        logger.exception("get_option_chain_with_greeks failed: %s", e)
        return pd.DataFrame()

# ---------------------------
#
