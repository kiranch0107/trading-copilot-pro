import streamlit as st
import yfinance as yf
import pandas as pd
import ta

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


# ✅ MULTI-EXPIRY BEST CONTRACT SELECTION
def get_option_data(ticker, price, trend):
    try:
        stock = yf.Ticker(ticker)
        expiries = stock.options[:3]  # ✅ scan first 3 expiries

        best_contract = None
        best_score = 0

        for expiry in expiries:
            chain = stock.option_chain(expiry)
            options = chain.calls if trend == "Bullish" else chain.puts

            options = options[
                (options['strike'] > price * 0.9) &
                (options['strike'] < price * 1.1)
            ].copy()

            if options.empty:
                continue

            options = options.fillna(0)

            # ✅ Calculate spread + midpoint
            options['spread'] = options['ask'] - options['bid']
            options['mid_price'] = (options['ask'] + options['bid']) / 2

            # ✅ Tight spread filter (<=10%)
            options = options[
                (options['spread'] > 0) &
                (options['mid_price'] > 0) &
                ((options['spread'] / options['mid_price']) <= 0.10)
            ]

            if options.empty:
                continue

            # ✅ Liquidity score
            options['liquidity'] = options['volume'] + options['openInterest']

            top = options.sort_values(by="liquidity", ascending=False).iloc[0]

            score = top['liquidity']

            if score > best_score:
                best_score = score
                best_contract = (top, expiry)

        if best_contract is None:
            return "⚠️ No high-quality contracts found across expiries"

        best, expiry = best_contract

        return (
            f"🎯 Best Contract (Multi-Expiry)\n"
            f"Type: {'CALL' if trend=='Bullish' else 'PUT'}\n"
            f"Strike: {best['strike']}\n"
            f"Expiry: {expiry}\n"
            f"Price: ${round(best['lastPrice'],2)}\n"
            f"Volume: {int(best['volume'])}\n"
            f"Open Interest: {int(best['openInterest'])}\n"
            f"Bid/Ask: {round(best['bid'],2)} / {round(best['ask'],2)}\n"
            f"Spread: {round(best['spread'],2)}\n"
            f"✅ Best Liquidity Across Expiries"
        )

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

    if trend in ["Bullish", "Bearish"]:
        option = get_option_data(ticker, price, trend)
    else:
        option = "No strong options setup"

    return {
        "trend": trend,
        "score": score,
        "entry": entry,
        "stop": stop,
        "target": target,
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
        return f"⚡ Bullish Breakout → Entry {round(price,2)}"
    elif price < low and rsi < 45:
        return f"⚡ Bearish Breakdown → Entry {round(price,2)}"
    else:
        return "No clear intraday setup"


# ✅ RUN
if query:
    ticker = query.strip().upper()

    df = get_data(ticker)
    intraday = get_data(ticker, period="5d", interval="5m")

    if df is None or intraday is None:
        st.error("Invalid ticker")
    else:
        df = compute(df)
        intraday = compute(intraday)

        result = analyze(df, ticker)

        st.subheader(f"📊 {ticker} Analysis")

        col1, col2 = st.columns(2)

        with col1:
            st.write("### 💼 Swing Trade")
            st.write(f"Trend: {result['trend']}")
            st.write(f"Entry: {round(result['entry'],2)}")
            st.write(f"Stop: {round(result['stop'],2)}")
            st.write(f"Target: {round(result['target'],2)}")
            st.write(f"Confidence: {result['score']}%")

        with col2:
            st.write("### 🧠 Options Strategy")
            st.text(result["option"])

            st.write("### ⚡ Intraday Scalp")
            st.write(scalp(intraday))

        st.warning("⚠️ Not financial advice")
