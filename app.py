import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import os
import requests
import time

st.set_page_config(page_title="Trading Copilot ELITE", layout="wide")

st.title("🤖 Trading Copilot ELITE")
st.caption("Full trading + options + real-time Telegram alerts")

query = st.chat_input("Enter ticker (TSLA, NVDA, AAPL)")

# ✅ WATCHLIST
WATCHLIST = ["TSLA","NVDA","AAPL","MSFT","AMZN","META","AMD","SPY","QQQ",
             "INTC","NFLX","BABA","CSCO","GOOGL"]

# ✅ PERFORMANCE MODE
FAST_MODE = True
SCAN_LIST = WATCHLIST[:10] if FAST_MODE else WATCHLIST

# ✅ TELEGRAM (SECURE)
SENT_ALERTS = {}
COOLDOWN = 600  # 10 minutes


def send_telegram_alert(ticker, message):
    TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

    if not TOKEN or not CHAT_ID:
        return

    now = time.time()

    # ✅ prevent spam
    if ticker in SENT_ALERTS and (now - SENT_ALERTS[ticker]) < COOLDOWN:
        return

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    try:
        requests.post(url, data={
            "chat_id": CHAT_ID,
            "text": message
        })
        SENT_ALERTS[ticker] = now
    except:
        pass


# ✅ DATA FETCH
def get_data(ticker, period="3mo", interval="1d"):
    df = yf.download(ticker, period=period, interval=interval)
    if df is None or df.empty:
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


# ✅ OPTIONS ENGINE
def get_option_data(ticker, price, trend, strength):
    try:
        stock = yf.Ticker(ticker)
        expiries = stock.options[:3]

        best = None
        best_score = 0

        for expiry in expiries:
            try:
                chain = stock.option_chain(expiry)
                opts = chain.calls if trend=="Bullish" else chain.puts
                opts = opts.fillna(0)

                # ✅ ITM vs ATM
                if strength == "Strong":
                    opts = opts[(opts['strike'] < price) if trend=="Bullish"
                                else (opts['strike'] > price)]
                else:
                    opts = opts[(opts['strike'] > price*0.95) & (opts['strike'] < price*1.05)]

                if opts.empty:
                    continue

                opts['spread'] = opts['ask'] - opts['bid']
                opts['mid'] = (opts['ask'] + opts['bid']) / 2

                # ✅ strict (10%)
                strict = opts[(opts['mid'] > 0) & (opts['spread']/opts['mid'] <= 0.10)]

                # ✅ fallback (15%)
                if strict.empty:
                    strict = opts[(opts['mid'] > 0) & (opts['spread']/opts['mid'] <= 0.15)]

                if strict.empty:
                    continue

                strict['liq'] = strict['volume'] + strict['openInterest']
                top = strict.sort_values(by="liq", ascending=False).iloc[0]

                if top['liq'] > best_score:
                    best = (top, expiry)
                    best_score = top['liq']

            except:
                continue

        if best is None:
            return "⚠️ No suitable options"

        row, expiry = best
        return f"{'CALL' if trend=='Bullish' else 'PUT'} ({strength}) | Strike {row['strike']} | Exp {expiry}"

    except:
        return "⚠️ Option data unavailable"


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

    # ✅ trend
    if price > ema20 > ema50:
        trend = "Bullish"
    elif price < ema20 < ema50:
        trend = "Bearish"
    else:
        return None

    strength = "Strong" if (rsi > 60 and macd > signal) else "Normal"

    breakout_high = df['High'].tail(5).max()
    breakout_low = df['Low'].tail(5).min()

    if trend == "Bullish":
        entry = max(ema20, breakout_high)
        stop = price - atr
        target = price + atr * 2
    else:
        entry = min(ema20, breakout_low)
        stop = price + atr
        target = price - atr * 2

    rr = abs(target - entry) / abs(entry - stop)

    if rr < 1.5:
        return None

    option = get_option_data(ticker, price, trend, strength)

    return {
        "ticker": ticker,
        "trend": trend,
        "strength": strength,
        "entry": entry,
        "stop": stop,
        "target": target,
        "rr": rr,
        "option": option,
        "high_quality": (rr >= 2 and strength == "Strong")
    }


# ✅ SCALPING
def scalp(df):
    latest = df.iloc[-1]
    price = latest['Close']

    high = df['High'].tail(5).max()
    low = df['Low'].tail(5).min()

    if (high - low) / price < 0.01:
        return "Low volatility → avoid"

    if price > high:
        return f"Breakout scalp → {round(price,2)}"
    elif price < low:
        return f"Breakdown scalp → {round(price,2)}"
    else:
        return "No clear scalp"


# ✅ ALERT ENGINE
alerts = []

for t in SCAN_LIST:
    df = get_data(t)
    if df is None:
        continue

    df = compute(df)
    r = analyze(df, t)

    if r and r["high_quality"]:
        alerts.append(r)

        msg = (
            f"🚨 TRADE ALERT\n"
            f"{r['ticker']} → {r['trend']} ({r['strength']})\n"
            f"RR: {round(r['rr'],2)}\n"
            f"Entry: {round(r['entry'],2)}"
        )

        send_telegram_alert(t, msg)


if alerts:
    st.subheader("🚨 HIGH QUALITY ALERTS")
    for a in alerts:
        st.success(f"{a['ticker']} → {a['trend']} | RR {round(a['rr'],2)}")
else:
    st.info("No high-quality setups right now")


# ✅ SINGLE STOCK VIEW
if query:
    ticker = query.strip().upper()

    df = get_data(ticker)
    intraday = get_data(ticker, "5d", "5m")

    if df is None:
        st.error("Invalid ticker")
    else:
        df = compute(df)
        intraday = compute(intraday)

        r = analyze(df, ticker)

        if r is None:
            st.warning("⚠️ No high-quality trade setup")
        else:
            st.subheader(f"{ticker} Analysis")

            col1, col2 = st.columns(2)

            with col1:
                st.write("### 💼 Swing Trade")
                st.write(f"Trend: {r['trend']} ({r['strength']})")
                st.write(f"Entry: {round(r['entry'],2)}")
                st.write(f"Stop: {round(r['stop'],2)}")
                st.write(f"Target: {round(r['target'],2)}")
                st.write(f"RR: {round(r['rr'],2)}")

            with col2:
                st.write("### 🧠 Options Strategy")
                st.text(r["option"])

                st.write("### ⚡ Intraday")
                st.write(scalp(intraday))

        st.warning("⚠️ Not financial advice")
