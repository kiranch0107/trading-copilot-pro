import streamlit as st
import yfinance as yf
import pandas as pd
import ta
from datetime import datetime

st.set_page_config(page_title="Trading Copilot PRO", layout="wide")

st.title("🤖 Trading Copilot PRO")
st.caption("AI-powered stock, options & intraday assistant")

st.info("👋 Enter ticker to analyze (e.g., TSLA, NVDA, AAPL)")

query = st.chat_input("Enter ticker")


# ✅ FETCH DATA
def get_data(ticker, period="3mo", interval="1d"):
    df = yf.download(ticker, period=period, interval=interval)

    if df is None or df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return df


# ✅ COMPUTE INDICATORS
def compute(df):
    close = df['Close']
    high = df['High']
    low = df['Low']

    df['EMA20'] = ta.trend.ema_indicator(close, 20)
    df['EMA50'] = ta.trend.ema_indicator(close, 50)
    df['RSI'] = ta.momentum.rsi(close, 14)

    macd = ta.trend.MACD(close)
    df['MACD'] = macd.macd()
    df['Signal'] = macd.macd_signal()

    df['ATR'] = ta.volatility.average_true_range(high, low, close, 14)

    return df


# ✅ GET OPTION PRICE
def get_option_data(ticker, price, trend):
    try:
        stock = yf.Ticker(ticker)
        expiries = stock.options

        if not expiries:
            return "No options data"

        expiry = expiries[0]  # nearest expiry

        chain = stock.option_chain(expiry)

        if trend == "Bullish":
            calls = chain.calls
            strike = min(calls['strike'], key=lambda x: abs(x - price))
            option_row = calls[calls['strike'] == strike].iloc[0]

        else:
            puts = chain.puts
            strike = min(puts['strike'], key=lambda x: abs(x - price))
            option_row = puts[puts['strike'] == strike].iloc[0]

        last_price = option_row['lastPrice']

        return f"Strike: {strike} | Expiry: {expiry} | Price: ${round(last_price,2)}"

    except:
        return "Option data unavailable"


# ✅ ANALYSIS
def analyze(df, ticker):
    latest = df.iloc[-1]

    price = latest['Close']
    ema20 = latest['EMA20']
    ema50 = latest['EMA50']
    rsi = latest['RSI']
    macd = latest['MACD']
    signal = latest['Signal']
    atr = latest['ATR']

    if price > ema20 > ema50:
        trend = "Bullish"; score = 75
    elif price < ema20 < ema50:
        trend = "Bearish"; score = 75
    else:
        trend = "Neutral"; score = 50

    if macd > signal:
        score += 5
    else:
        score -= 5

    if rsi > 55:
        score += 5

    score = max(0, min(score, 100))

    entry = ema20
    stop = price - atr if trend == "Bullish" else price + atr
    target = price + atr * 2 if trend == "Bullish" else price - atr * 2

    # ✅ VOLATILITY
    iv = atr / price
    vol = "High" if iv > 0.04 else "Normal" if iv > 0.02 else "Low"

    # ✅ OPTIONS + REAL DATA
    if trend in ["Bullish", "Bearish"]:
        option_data = get_option_data(ticker, price, trend)

        if trend == "Bullish":
            strategy = "CALL" if iv < 0.04 else "Bull Call Spread"
        else:
            strategy = "PUT" if iv < 0.04 else "Bear Put Spread"

        option = f"{strategy} → {option_data}"
    else:
        option = "No strong options setup"

    return {
        "price": price,
        "trend": trend,
        "score": score,
        "entry": entry,
        "stop": stop,
        "target": target,
        "option": option,
        "vol": vol
    }


# ✅ SCALPING
def scalp(df):
    latest = df.iloc[-1]
    price = latest['Close']
    rsi = latest['RSI']

    high = df['High'].tail(10).max()
    low = df['Low'].tail(10).min()

    if price > high and rsi > 55:
        return f"⚡ Bullish Breakout → {round(price,2)}"
    elif price < low and rsi < 45:
        return f"⚡ Bearish Breakdown → {round(price,2)}"
    else:
        return "No clear scalp setup"


# ✅ RUN
if query:
    ticker = query.strip().upper()

    df = get_data(ticker)
    intraday = get_data(ticker, period="5d", interval="5m")

    if df is None:
        st.error("Invalid ticker")
    else:
        df = compute(df)
        intraday = compute(intraday)

        r = analyze(df, ticker)

        # ✅ SHOW TICKER CLEARLY
        st.subheader(f"📊 {ticker} Analysis")

        col1, col2 = st.columns(2)

        with col1:
            st.write("### 💼 Swing Trade")
            st.write(f"Trend: {r['trend']}")
            st.write(f"Entry: {round(r['entry'],2)}")
            st.write(f"Stop: {round(r['stop'],2)}")
            st.write(f"Target: {round(r['target'],2)}")
            st.write(f"Confidence: {r['score']}%")

        with col2:
            st.write("### 🧠 Options")
            st.write(r["option"])
            st.write(f"Volatility: {r['vol']}")

            st.write("### ⚡ Intraday Scalp")
            st.write(scalp(intraday))

        st.warning("⚠️ Not financial advice")
