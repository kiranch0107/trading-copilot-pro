import streamlit as st
import yfinance as yf
import pandas as pd
import ta

st.set_page_config(page_title="Trading Copilot ELITE", layout="wide")

st.title("🤖 Trading Copilot ELITE")
st.caption("Full trading + options + alert system")

query = st.chat_input("Enter ticker (TSLA, NVDA, AAPL)")

# ✅ FULL WATCHLIST
WATCHLIST = ["TSLA","NVDA","AAPL","MSFT","AMZN","META","AMD","SPY","QQQ",
             "INTC","NFLX","BABA","CSCO","GOOGL"]

# ✅ PERFORMANCE MODE
FAST_MODE = True
SCAN_LIST = WATCHLIST[:10] if FAST_MODE else WATCHLIST


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


# ✅ OPTIONS ENGINE (FINAL ELITE VERSION)
def get_option_data(ticker, price, trend, strength):
    try:
        stock = yf.Ticker(ticker)
        expiries = stock.options[:3]

        best = None
        best_score = 0

        for expiry in expiries:
            try:
                chain = stock.option_chain(expiry)
                opts = chain.calls if trend == "Bullish" else chain.puts
                opts = opts.fillna(0)

                # ✅ ITM vs ATM logic
                if strength == "Strong":
                    opts = opts[(opts['strike'] < price) if trend=="Bullish"
                                else (opts['strike'] > price)]
                else:
                    opts = opts[(opts['strike'] > price*0.95) & (opts['strike'] < price*1.05)]

                if opts.empty:
                    continue

                opts['spread'] = opts['ask'] - opts['bid']
                opts['mid'] = (opts['ask'] + opts['bid']) / 2

                # ✅ STRICT FILTER (10%)
                strict = opts[(opts['mid'] > 0) & (opts['spread']/opts['mid'] <= 0.10)]

                # ✅ FALLBACK (15%)
                if strict.empty:
                    strict = opts[(opts['mid'] > 0) & (opts['spread']/opts['mid'] <= 0.15)]

                if strict.empty:
                    continue

                strict['liq'] = strict['volume'] + strict['openInterest']
                top = strict.sort_values(by="liq", ascending=False).iloc[0]

                if top['liq'] > best_score:
                    best_score = top['liq']
                    best = (top, expiry)

            except:
                continue

        if best is None:
            return "⚠️ No suitable options found"

        row, expiry = best

        quality = "✅ Tight" if (row['spread']/row['mid']) <= 0.10 else "⚠️ Acceptable"

        return (
            f"{'CALL' if trend=='Bullish' else 'PUT'} ({strength})\n"
            f"Strike: {row['strike']} | Exp: {expiry}\n"
            f"Price: ${round(row['lastPrice'],2)}\n"
            f"Vol: {int(row['volume'])} | OI: {int(row['openInterest'])}\n"
            f"Spread: {round(row['spread'],2)} {quality}"
        )

    except:
        return "⚠️ Options temporarily unavailable"


# ✅ ANALYSIS ENGINE
def analyze(df, ticker):
    latest = df.iloc[-1]

    price = latest['Close']
    ema20 = latest['EMA20']
    ema50 = latest['EMA50']
    rsi = latest['RSI']
    macd = latest['MACD']
    signal = latest['Signal']
    atr = latest['ATR']

    # ✅ Trend
    if price > ema20 > ema50:
        trend = "Bullish"
    elif price < ema20 < ema50:
        trend = "Bearish"
    else:
        return None

    # ✅ Strength
    strength = "Strong" if (rsi > 60 and macd > signal) else "Normal"

    breakout_high = df['High'].tail(5).max()
    breakout_low = df['Low'].tail(5).min()

    # ✅ Entry logic
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


# ✅ ALERT SYSTEM
alerts = []
for t in SCAN_LIST:
    df = get_data(t)
    if df is None:
        continue
    df = compute(df)
    r = analyze(df, t)

    if r and r["high_quality"]:
        alerts.append(r)

if alerts:
    st.subheader("🚨 HIGH QUALITY ALERTS")
    for a in alerts:
        st.success(
            f"{a['ticker']} → {a['trend']} ({a['strength']}) | "
            f"RR: {round(a['rr'],2)} | Entry: {round(a['entry'],2)}"
        )
else:
    st.info("No high-quality setups right now")


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
