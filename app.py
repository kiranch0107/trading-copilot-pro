import streamlit as st
import yfinance as yf
import pandas as pd
import ta

st.set_page_config(page_title="Trading Copilot PRO", layout="wide")

st.title("🤖 Trading Copilot PRO")
st.caption("AI-powered stock & options trading assistant")

st.info("👋 Enter a stock ticker below to analyze (e.g., TSLA, AAPL, NVDA)")

query = st.chat_input("Enter ticker (e.g., TSLA)")


def get_data(ticker):
    df = yf.download(ticker, period="3mo", interval="1d")

    if df is None or df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return df


def compute(df):
    close = df['Close']
    high = df['High']
    low = df['Low']

    df['EMA20'] = ta.trend.ema_indicator(close=close, window=20)
    df['EMA50'] = ta.trend.ema_indicator(close=close, window=50)
    df['EMA200'] = ta.trend.ema_indicator(close=close, window=200)

    df['RSI'] = ta.momentum.rsi(close=close, window=14)

    macd = ta.trend.MACD(close=close)
    df['MACD'] = macd.macd()
    df['Signal'] = macd.macd_signal()

    df['ATR'] = ta.volatility.average_true_range(
        high=high, low=low, close=close, window=14
    )

    return df


def analyze(df):
    latest = df.iloc[-1]

    price = latest['Close']
    ema20 = latest['EMA20']
    ema50 = latest['EMA50']
    ema200 = latest['EMA200']
    rsi = latest['RSI']
    macd = latest['MACD']
    signal = latest['Signal']
    atr = latest['ATR']

    if price > ema20 > ema50 > ema200:
        trend = "Strong Bullish"
        score = 85
    elif price > ema50 > ema200:
        trend = "Bullish"
        score = 75
    elif price < ema20 < ema50 < ema200:
        trend = "Strong Bearish"
        score = 85
    elif price < ema50 < ema200:
        trend = "Bearish"
        score = 75
    else:
        trend = "Neutral"
        score = 50

    if rsi > 55:
        score += 5
    if rsi < 45:
        score -= 5
    if macd > signal:
        score += 5
    else:
        score -= 5

    score = max(0, min(score, 100))

    support = df['Low'].tail(20).min()
    resistance = df['High'].tail(20).max()

    if "Bullish" in trend:
        entry = ema20
        stop = price - atr * 1.5
        t1 = price + atr * 2
        t2 = price + atr * 4
        option = f"CALL (Strike ~ {round(price)}) exp 2–3 weeks"
    elif "Bearish" in trend:
        entry = ema20
        stop = price + atr * 1.5
        t1 = price - atr * 2
        t2 = price - atr * 4
        option = f"PUT (Strike ~ {round(price)}) exp 2–3 weeks"
    else:
        entry = price
        stop = price - atr * 1.2
        t1 = price + atr * 1.5
        t2 = price + atr * 2
        option = "No strong setup"

    rr = abs(t2 - entry) / abs(entry - stop)

    return {
        "trend": trend,
        "score": score,
        "price": price,
        "entry": entry,
        "stop": stop,
        "t1": t1,
        "t2": t2,
        "rr": rr,
        "support": support,
        "resistance": resistance,
        "rsi": rsi,
        "atr": atr,
        "option": option
    }


if query:
    ticker = query.strip().upper()

    df = get_data(ticker)

    if df is None:
        st.error("Invalid ticker or no data found")
    else:
        df = compute(df)
        r = analyze(df)

        col1, col2 = st.columns(2)

        with col1:
            st.subheader(ticker)
            st.metric("Trend", r["trend"])
            st.metric("Confidence", f"{r['score']}%")

            st.write("Trade Plan")
            st.write(f"Entry: {round(r['entry'], 2)}")
            st.write(f"Stop: {round(r['stop'], 2)}")
            st.write(f"Target1: {round(r['t1'], 2)}")
            st.write(f"Target2: {round(r['t2'], 2)}")
            st.write(f"RR: 1:{round(r['rr'], 2)}")

        with col2:
            st.write("Levels")
            st.write(f"Support: {round(r['support'], 2)}")
            st.write(f"Resistance: {round(r['resistance'], 2)}")

            st.write("Options")
            st.write(r["option"])

            st.write("Insight")
            st.write(
                f"{r['trend']} trend, RSI {round(r['rsi'],2)}, confidence {r['score']}%"
            )

        st.warning("Not financial advice")
