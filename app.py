import streamlit as st
import yfinance as yf
import pandas as pd
import ta

st.set_page_config(page_title="Trading Copilot PRO", layout="wide")

st.title("🤖 Trading Copilot PRO")
st.caption("AI-powered stock, options & intraday assistant")

st.info("👋 Enter ticker OR use auto scanner below")

query = st.chat_input("Enter ticker (TSLA, NVDA, AAPL)")

# ✅ STOCK LIST
STOCKS = ["TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "META", "AMD", "SPY", "QQQ"]

# ✅ FETCH DATA
def get_data(ticker, period="3mo", interval="1d"):
    df = yf.download(ticker, period=period, interval=interval)

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

# ✅ ANALYSIS
def analyze(df):
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

    # ✅ trade levels
    entry = ema20
    stop = price - atr if trend == "Bullish" else price + atr
    target = price + atr*2 if trend == "Bullish" else price - atr*2

    # ✅ IV proxy
    iv = atr / price

    if iv > 0.04:
        vol = "High Volatility"
    elif iv > 0.02:
        vol = "Normal Volatility"
    else:
        vol = "Low Volatility"

    # ✅ OPTIONS LOGIC (RESTORED + IMPROVED)
    if trend == "Bullish":
        if iv > 0.04:
            option = f"🔥 Bull Call Spread → Buy {round(price)}C / Sell {round(price*1.05)}C"
        else:
            option = f"✅ CALL | Strike: {round(price)} | Delta ~0.55 | Exp: 2–3 weeks"

    elif trend == "Bearish":
        if iv > 0.04:
            option = f"🔥 Bear Put Spread → Buy {round(price)}P / Sell {round(price*0.95)}P"
        else:
            option = f"✅ PUT | Strike: {round(price)} | Delta ~0.55 | Exp: 2–3 weeks"
    else:
        option = "⚠️ No strong options setup"

    return {
        "trend": trend,
        "score": score,
        "entry": entry,
        "stop": stop,
        "target": target,
        "iv": iv,
        "vol": vol,
        "option": option
    }

# ✅ SCALPING
def scalp(df):
    latest = df.iloc[-1]
    price = latest['Close']
    rsi = latest['RSI']

    high = df['High'].tail(10).max()
    low = df['Low'].tail(10).min()

    if price > high and rsi > 55:
        return f"⚡ Bullish breakout scalp → Entry {round(price,2)}"
    elif price < low and rsi < 45:
        return f"⚡ Bearish breakdown scalp → Entry {round(price,2)}"
    else:
        return "No clear scalp setup"

# ✅ AUTO SCANNER
def scan_market():
    results = []

    for ticker in STOCKS:
        df = get_data(ticker)
        if df is None:
            continue

        df = compute(df)
        r = analyze(df)

        if r["score"] >= 70:
            results.append({
                "ticker": ticker,
                "trend": r["trend"],
                "score": r["score"]
            })

    return sorted(results, key=lambda x: x["score"], reverse=True)[:5]

# ✅ SHOW SCANNER
st.subheader("🔥 Top Trade Opportunities")
for r in scan_market():
    st.write(f"✅ {r['ticker']} | {r['trend']} | Score: {r['score']}%")

# ✅ USER INPUT ANALYSIS
if query:
    ticker = query.strip().upper()

    df = get_data(ticker)
    intraday = get_data(ticker, period="5d", interval="5m")

    if df is None:
        st.error("Invalid ticker")
    else:
        df = compute(df)
        r = analyze(df)

        intraday = compute(intraday)

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("📊 Swing Trade")
            st.write(f"Trend: {r['trend']}")
            st.write(f"Entry: {round(r['entry'],2)}")
            st.write(f"Stop: {round(r['stop'],2)}")
            st.write(f"Target: {round(r['target'],2)}")
            st.write(f"Confidence: {r['score']}%")

        with col2:
            st.subheader("🧠 Options Strategy")
            st.write(r["option"])
            st.write(f"Volatility: {r['vol']}")

            st.subheader("⚡ Intraday Scalp")
            st.write(scalp(intraday))

        st.warning("⚠️ Not financial advice")
