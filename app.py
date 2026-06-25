import streamlit as st
import yfinance as yf
import pandas as pd
import ta

st.set_page_config(page_title="Trading Copilot PRO", layout="wide")

st.title("🤖 Trading Copilot PRO")
st.caption("AI-powered stock & options trading assistant")

st.info("👋 Enter ticker OR view auto scanner below")

query = st.chat_input("Enter ticker (TSLA, NVDA, AAPL)")

# ✅ STOCK UNIVERSE (you can expand later)
STOCKS = ["TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "META", "AMD", "SPY", "QQQ"]


# ✅ FETCH DATA
def get_data(ticker):
    df = yf.download(ticker, period="3mo", interval="1d")
    if df is None or df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return df


# ✅ INDICATORS
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


# ✅ ANALYZE (used for both scanner + single)
def analyze(df):
    latest = df.iloc[-1]

    price = latest['Close']
    ema20 = latest['EMA20']
    ema50 = latest['EMA50']
    rsi = latest['RSI']
    macd = latest['MACD']
    signal = latest['Signal']
    atr = latest['ATR']

    # ✅ Trend + score
    if price > ema20 > ema50:
        trend = "Bullish"
        score = 75
    elif price < ema20 < ema50:
        trend = "Bearish"
        score = 75
    else:
        trend = "Neutral"
        score = 50

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

    return {
        "trend": trend,
        "score": score,
        "entry": entry,
        "stop": stop,
        "target": target
    }


# ✅ AUTO SCANNER
def scan_market():
    results = []

    for ticker in STOCKS:
        df = get_data(ticker)
        if df is None:
            continue

        df = compute(df)
        r = analyze(df)

        if r["score"] >= 70:  # ✅ filter strong setups
            results.append({
                "ticker": ticker,
                "trend": r["trend"],
                "score": r["score"]
            })

    # ✅ sort by best trades
    results = sorted(results, key=lambda x: x["score"], reverse=True)

    return results[:5]  # ✅ top 5 trades


# ✅ SHOW SCANNER
st.subheader("🔥 Top Trade Opportunities (Auto Scanner)")

scan_results = scan_market()

for r in scan_results:
    st.write(f"✅ {r['ticker']} | {r['trend']} | Score: {r['score']}%")


# ✅ SINGLE STOCK ANALYSIS
if query:
    ticker = query.strip().upper()
    df = get_data(ticker)

    if df is None:
        st.error("❌ Invalid ticker")
    else:
        df = compute(df)
        r = analyze(df)

        st.subheader(f"📊 {ticker} Analysis")

        st.write(f"Trend: {r['trend']}")
        st.write(f"Entry: {round(r['entry'],2)}")
        st.write(f"Stop: {round(r['stop'],2)}")
        st.write(f"Target: {round(r['target'],2)}")
        st.write(f"Confidence: {r['score']}%")

        st.warning("⚠️ Not financial advice")
