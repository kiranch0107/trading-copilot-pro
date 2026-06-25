import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import time

st.set_page_config(page_title="Trading Copilot ELITE", layout="wide")

st.title("🤖 Trading Copilot ELITE")
st.caption("Professional trading + alert system")

# ✅ STOCK UNIVERSE FOR ALERTS
WATCHLIST = ["TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "META", "AMD", "SPY"]


query = st.chat_input("Enter ticker")


# ✅ DATA FETCH
def get_data(ticker, period="3mo", interval="1d"):
    df = yf.download(ticker, period=period, interval=interval)
    if df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


# ✅ INDICATORS
def compute(df):
    df['EMA20'] = ta.trend.ema_indicator(df['Close'], 20)
    df['EMA50'] = ta.trend.ema_indicator(df['Close'], 50)

    macd = ta.trend.MACD(df['Close'])
    df['MACD'] = macd.macd()
    df['Signal'] = macd.macd_signal()

    df['RSI'] = ta.momentum.rsi(df['Close'], 14)

    df['ATR'] = ta.volatility.average_true_range(
        df['High'], df['Low'], df['Close'], 14
    )
    return df


# ✅ ANALYSIS CORE
def analyze(df, ticker):
    latest = df.iloc[-1]

    price = latest['Close']
    ema20 = latest['EMA20']
    ema50 = latest['EMA50']
    rsi = latest['RSI']
    macd = latest['MACD']
    signal = latest['Signal']
    atr = latest['ATR']

    # ✅ Trend filter
    if price > ema20 > ema50:
        trend = "Bullish"
    elif price < ema20 < ema50:
        trend = "Bearish"
    else:
        return None

    strength = "Strong" if (rsi > 60 and macd > signal) else "Normal"

    # ✅ Entry logic
    breakout = df['High'].tail(5).max()

    if trend == "Bullish":
        entry = max(ema20, breakout)
        stop = price - atr
        target = price + atr * 2
    else:
        entry = min(ema20, df['Low'].tail(5).min())
        stop = price + atr
        target = price - atr * 2

    rr = abs(target - entry) / abs(entry - stop)

    if rr < 1.5:
        return None

    # ✅ HIGH-QUALITY FLAG (for alerts)
    high_quality = True if (rr >= 2 and strength == "Strong") else False

    return {
        "ticker": ticker,
        "trend": trend,
        "strength": strength,
        "entry": entry,
        "stop": stop,
        "target": target,
        "rr": rr,
        "high_quality": high_quality
    }


# ✅ SCAN FOR ALERTS
def scan_alerts():
    alerts = []

    for ticker in WATCHLIST:
        df = get_data(ticker)
        if df is None:
            continue

        df = compute(df)
        result = analyze(df, ticker)

        if result and result["high_quality"]:
            alerts.append(result)

    return alerts


# ✅ DISPLAY ALERTS
alerts = scan_alerts()

if alerts:
    st.subheader("🚨 HIGH-QUALITY TRADE ALERTS")

    for a in alerts:
        st.success(
            f"{a['ticker']} → {a['trend']} ({a['strength']}) | "
            f"RR: {round(a['rr'],2)} | Entry: {round(a['entry'],2)}"
        )
else:
    st.info("No high-quality trade alerts right now")


# ✅ SINGLE STOCK ANALYSIS
if query:
    ticker = query.strip().upper()

    df = get_data(ticker)
    intraday = get_data(ticker, "5d", "5m")

    if df is None:
        st.error("Invalid ticker")
    else:
        df = compute(df)
        intraday = compute(intraday)

        result = analyze(df, ticker)

        if result is None:
            st.warning("No high-quality trade setup")
        else:
            st.subheader(f"{ticker} Analysis")

            st.write(f"Trend: {result['trend']} ({result['strength']})")
            st.write(f"Entry: {round(result['entry'],2)}")
            st.write(f"Stop: {round(result['stop'],2)}")
            st.write(f"Target: {round(result['target'],2)}")
            st.write(f"RR: {round(result['rr'],2)}")

        st.warning("Not financial advice")
