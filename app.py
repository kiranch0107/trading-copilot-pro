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

# Clean up ticker input
single_ticker = single_ticker.strip().upper() if single_ticker else ""

# ---------------------------
# Persistence helpers
# ---------------------------
def _load(path: Path):
    try:
        if not path.exists():
            return []
        return json.loads(path.read_text())
    except Exception as e:
        logger.exception("Failed to load %s: %s", path, e)
        return []

def _save(path: Path, data) -> None:
    try:
        path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.exception("Failed to save %s: %s", path, e)

def load_alerts(): return _load(ALERT_LOG_FILE)
def save_alerts(alerts): _save(ALERT_LOG_FILE, alerts)
def load_journal(): return _load(JOURNAL_FILE)
def save_journal(journal): _save(JOURNAL_FILE, journal)

def load_iv_history():
    try:
        if not IV_HISTORY_FILE.exists():
            return {}
        return json.loads(IV_HISTORY_FILE.read_text())
    except Exception as e:
        logger.exception("Failed to load IV history: %s", e)
        return {}

def save_iv_history(data):
    try:
        IV_HISTORY_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.exception("Failed to save IV history: %s", e)

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
# Black-Scholes Greeks (uses scipy.norm for pdf/cdf)
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
# Option chain (with IV Rank, Greeks, strategy-aware scoring)
# ---------------------------
@st.cache_data(ttl=900, show_spinner=False)
def get_option_chain_with_greeks(ticker, strategy="sell_premium", risk_free_rate=0.03, iv_history_lookback_days=365):
    """
    Fetch option chain, compute mid/spread, compute IV Rank using stored IV history,
    compute Greeks per contract using Black-Scholes, and compute a strategy-aware option_score.
    """
    try:
        t = yf.Ticker(ticker)
        expiries = []
        try:
            expiries = t.options or []
        except Exception as e:
            logger.warning("Could not fetch expiries for %s: %s", ticker, e)
            expiries = []

        expiries = expiries[:4]  # limit to first few expiries to reduce load
        frames = []
        for expiry in expiries:
            try:
                oc = t.option_chain(expiry)
                for side_name, side_df in (("CALL", oc.calls), ("PUT", oc.puts)):
                    if side_df is None or side_df.empty:
                        continue
                    df_side = side_df.copy()
                    df_side = df_side.rename(columns={c: c.strip() if isinstance(c, str) else c for c in df_side.columns})
                    if "bid" in df_side.columns and "ask" in df_side.columns:
                        df_side["mid"] = (df_side["bid"].fillna(0) + df_side["ask"].fillna(0)) / 2.0
                        df_side["spread"] = (df_side["ask"].fillna(0) - df_side["bid"].fillna(0)).abs()
                    else:
                        df_side["mid"] = np.nan
                        df_side["spread"] = np.nan
                    df_side["expiry"] = expiry
                    df_side["type"] = side_name
                    frames.append(df_side)
                time.sleep(0.12)
            except Exception as e:
                logger.warning("Failed to fetch option chain for %s expiry %s: %s", ticker, expiry, e)
                continue

        if not frames:
            return pd.DataFrame()

        opt = pd.concat(frames, ignore_index=True, sort=False)

        # Normalize impliedVolatility column name variants
        iv_col = None
        for candidate in ["impliedVolatility", "impliedVol", "iv"]:
            if candidate in opt.columns:
                iv_col = candidate
                break
        if iv_col is None:
            opt["impliedVolatility"] = np.nan
        else:
            opt["impliedVolatility"] = pd.to_numeric(opt[iv_col], errors="coerce")

        # Compute a per-ticker current IV metric (median of available IVs)
        ivs = opt["impliedVolatility"].dropna()
        current_iv = float(ivs.median()) if not ivs.empty else np.nan

        # Load IV history, update with today's current_iv
        iv_history = load_iv_history()
        today_str = datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d")
        ticker_hist = iv_history.get(ticker, [])
        if not np.isnan(current_iv):
            if not ticker_hist or ticker_hist[-1].get("date") != today_str:
                ticker_hist.append({"date": today_str, "iv": current_iv})
            cutoff = datetime.now() - timedelta(days=iv_history_lookback_days)
            ticker_hist = [h for h in ticker_hist if datetime.strptime(h["date"], "%Y-%m-%d") >= cutoff]
            iv_history[ticker] = ticker_hist
            save_iv_history(iv_history)

        # Compute iv_min and iv_max from history; fallback to chain min/max
        hist_ivs = [h["iv"] for h in ticker_hist] if ticker_hist else []
        if hist_ivs:
            iv_min, iv_max = min(hist_ivs), max(hist_ivs)
        else:
            iv_min, iv_max = (float(ivs.min()) if not ivs.empty else np.nan, float(ivs.max()) if not ivs.empty else np.nan)

        # Get spot price
        spot = np.nan
        try:
            hist = t.history(period="1d")
            if not hist.empty:
                spot = float(hist["Close"].iloc[-1])
        except Exception:
            spot = np.nan
        if np.isnan(spot):
            try:
                info = t.info or {}
                spot = float(info.get("regularMarketPrice") or info.get("previousClose") or np.nan)
            except Exception:
                spot = np.nan

        rows = []
        for _, row in opt.iterrows():
            try:
                K = float(row.get("strike", np.nan))
                expiry_str = row.get("expiry")
                try:
                    expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%d")
                except Exception:
                    expiry_dt = pd.to_datetime(expiry_str).to_pydatetime()
                days_to_expiry = max((expiry_dt - datetime.now()).days, 0)
                T = max(days_to_expiry / 365.0, 1e-6)
                iv = float(row.get("impliedVolatility", np.nan)) if not pd.isna(row.get("impliedVolatility", np.nan)) else np.nan
                if not np.isnan(iv) and not np.isnan(iv_min) and not np.isnan(iv_max) and iv_max > iv_min:
                    iv_rank = float((iv - iv_min) / (iv_max - iv_min))
                    iv_rank = max(0.0, min(1.0, iv_rank))
                else:
                    iv_rank = np.nan

                opt_type = row.get("type", "CALL").upper()
                option_type = "call" if "CALL" in opt_type else "put"
                r = float(risk_free_rate)

                if not np.isnan(spot) and not np.isnan(iv):
                    greeks = black_scholes_greeks(S=spot, K=K, T=T, r=r, sigma=iv, option_type=option_type)
                else:
                    greeks = {"delta": np.nan, "gamma": np.nan, "theta": np.nan, "vega": np.nan}

                vol = float(row.get("volume", 0) if not pd.isna(row.get("volume", np.nan)) else 0)
                oi = float(row.get("openInterest", 0) if not pd.isna(row.get("openInterest", np.nan)) else 0)
                spread = float(row.get("spread", np.nan)) if not pd.isna(row.get("spread", np.nan)) else np.nan
                mid = float(row.get("mid", np.nan)) if not pd.isna(row.get("mid", np.nan)) else np.nan

                # Strategy-aware option scoring
                score = 0.0
                # IV Rank weighting
                if not np.isnan(iv_rank):
                    if strategy == "sell_premium":
                        score += iv_rank * 40.0
                    elif strategy == "buy_directional":
                        score += (1.0 - iv_rank) * 30.0
                    elif strategy == "hedge":
                        score += iv_rank * 10.0
                # Delta weighting
                delta = greeks.get("delta", np.nan)
                if not np.isnan(delta):
                    if strategy == "buy_directional":
                        score += max(0.0, (0.5 - abs(delta - 0.5))) * 25.0
                    elif strategy == "hedge":
                        score += min(20.0, abs(delta) * 20.0)
                # Vega weighting
                vega = greeks.get("vega", np.nan)
                if not np.isnan(vega):
                    if strategy == "sell_premium":
                        score += min(15.0, vega / 10.0)
                    elif strategy == "buy_directional":
                        score += min(8.0, vega / 20.0)
                # Liquidity
                score += min(20.0, np.log1p(vol) * 2.0) if vol > 0 else 0.0
                score += min(15.0, np.log1p(oi) * 1.5) if oi > 0 else 0.0
                # Spread penalty
                if not np.isnan(spread) and spread > 0:
                    score -= min(10.0, spread * 5.0)
                # small mid price bonus
                if not np.isnan(mid) and mid > 0:
                    score += min(5.0, mid / max(1.0, spot) * 5.0)

                row_out = row.to_dict()
                row_out.update({
                    "impliedVolatility": iv,
                    "iv_rank": round(iv_rank, 3) if not np.isnan(iv_rank) else np.nan,
                    "delta": round(greeks.get("delta", np.nan), 4) if greeks.get("delta", np.nan) is not None else np.nan,
                    "gamma": round(greeks.get("gamma", np.nan), 6) if greeks.get("gamma", np.nan) is not None else np.nan,
                    "theta": round(greeks.get("theta", np.nan), 6) if greeks.get("theta", np.nan) is not None else np.nan,
                    "vega": round(greeks.get("vega", np.nan), 4) if greeks.get("vega", np.nan) is not None else np.nan,
                    "days_to_expiry": int(days_to_expiry),
                    "option_score": round(score, 3),
                    "underlying_price": round(spot, 2) if not np.isnan(spot) else np.nan
                })
                rows.append(row_out)
            except Exception as e:
                logger.exception("Failed to process option row: %s", e)
                continue

        opt_enhanced = pd.DataFrame(rows)
        for col in ["iv_rank", "delta", "gamma", "theta", "vega", "option_score", "mid", "spread", "volume", "openInterest"]:
            if col in opt_enhanced.columns:
                opt_enhanced[col] = pd.to_numeric(opt_enhanced[col], errors="coerce")

        return opt_enhanced
    except Exception as e:
        logger.exception("get_option_chain_with_greeks top-level failure for %s: %s", ticker, e)
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
# Single ticker diagnostics & trade suggestion (option chain sorted by strategy)
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

            # Option chain (robust) with IV Rank, Greeks, and strategy-aware scoring
            opt_chain = get_option_chain_with_greeks(single_ticker, strategy=strategy)
            st.subheader(f"💹 Option Chain (sorted by {strategy} score)")
            if opt_chain is None or opt_chain.empty:
                st.info("Option chain not available or failed to fetch for this ticker.")
            else:
                opt_sorted = opt_chain.sort_values(by="option_score", ascending=False, na_position="last")
                display_cols = [c for c in [
                    "expiry", "contractSymbol", "type", "strike", "lastPrice", "mid", "spread",
                    "volume", "openInterest", "impliedVolatility", "iv_rank", "delta", "theta", "vega", "option_score", "days_to_expiry"
                ] if c in opt_sorted.columns]
                st.dataframe(opt_sorted[display_cols].head(30))

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
st.markdown("**Notes:** This app uses yfinance for data and option chains. Option chain fetches can fail or be rate-limited by the provider; the app handles failures gracefully and shows an empty table when unavailable. IV Rank is computed from a local IV history cache (iv_history.json) updated daily; Greeks are Black-Scholes approximations. Adjust strategy weights in the function to match your trading preferences.")
