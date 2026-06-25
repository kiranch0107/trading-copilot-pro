import streamlit as st
import yfinance as yf
import ta

st.set_page_config(page_title="Trading Copilot PRO", layout="wide")

st.title("🤖 Trading Copilot PRO")
st.caption("AI-powered stock & options trading assistant")

query = st.chat_input("Enter ticker (e.g., TSLA, NVDA)")

def get_data(ticker):
    return yf.download(ticker, period="3mo", interval="1d")

def compute(df):
    df['EMA20'] = ta.trend.ema_indicator(df['Close'], 20)
    df['EMA50'] = ta.trend.ema_indicator(df['Close'], 50)
    df['EMA200'] = ta.trend.ema_indicator(df['Close'], 200)
    df['RSI'] = ta.momentum.rsi(df['Close'], 14)

    macd = ta.trend.MACD(df['Close'])
    df['MACD'] = macd.macd()
    df['Signal'] = macd.macd_signal()

    df['ATR'] = ta.volatility.average_true_range(
        df['High'], df['Low'], df['Close'], 14
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
        trend = "Strong Bullish"; score = 85
    elif price > ema50 > ema200:
        trend = "Bullish"; score = 75
    elif price < ema20 < ema50 < ema200:
        trend = "Strong Bearish"; score = 85
    elif price < ema50 < ema200:
        trend = "Bearish"; score = 75
    else:
        trend = "Neutral"; score = 50

    if rsi > 55: score += 5
    if rsi < 45: score -= 5
    if macd > signal: score += 5
    else: score -= 5

    score = max(0, min(score, 100))

    support = df['Low'].tail(20).min()
    resistance = df['High'].tail(20).max()

    if "Bullish" in trend:
        entry = ema20
        stop = price - atr*1.5
        t1 = price + atr*2
        t2 = price + atr*4
        option = f"CALL (ATM {round(price)}) exp 2–3 weeks"
    elif "Bearish" in trend:
        entry = ema20
        stop = price + atr*1.5
        t1 = price - atr*2
        t2 = price - atr*4
        option = f"PUT (ATM {round(price)}) exp 2–3 weeks"
    else:
        entry = price
        stop = price - atr*1.2
        t1 = price + atr*1.5
        t2 = price + atr*2
        option = "No clear setup"

    rr = abs(t2-entry)/abs(entry-stop)

    return {
        "trend": trend, "score": score, "price": price,
        "entry": entry, "stop": stop, "t1": t1, "t2": t2,
        "rr": rr, "support": support, "resistance": resistance,
        "rsi": rsi, "atr": atr, "option": option
    }

if query:
    ticker = query.strip().upper()
    df = get_data(ticker)

    if df.empty:
        st.error("Invalid ticker")
    else:
        df = compute(df)
        r = analyze(df)

        col1, col2 = st.columns(2)

        with col1:
            st.subheader(f"📊 {ticker}")
            st.metric("Trend", r["trend"])
            st.metric("Confidence", f"{r['score']}%")

            st.write("### 💼 Trade Plan")
            st.write(f"Entry: {round(r['entry'],2)}")
            st.write(f"Stop: {round(r['stop'],2)}")
            st.write(f"T1: {round(r['t1'],2)}")
            st.write(f"T2: {round(r['t2'],2)}")
            st.write(f"RR: 1:{round(r['rr'],2)}")

        with col2:
            st.write("### 🔹 Levels")
            st.write(f"Support: {round(r['support'],2)}")
            st.write(f"Resistance: {round(r['resistance'],2)}")

            st.write("### 🧠 Options")
            st.write(r["option"])

            st.write("### 🤖 Insight")
            st.write(
                f"Market is {r['trend']} with RSI {round(r['rsi'],2)}. "
                f"Confidence score {r['score']}%"
            )

        st.warning("⚠️ Not financial advice")
