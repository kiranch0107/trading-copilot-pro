import streamlit as st
import yfinance as yf
import pandas as pd
import ta

st.set_page_config(page_title="Trading Copilot ELITE", layout="wide")

st.title("🤖 Trading Copilot ELITE")
st.caption("Professional-grade trading + options system")

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


# ✅ ROBUST OPTIONS FETCH (FIXED)
def get_option_data(ticker, price, trend, strength):
    try:
        stock = yf.Ticker(ticker)
        expiries = stock.options

        if not expiries or len(expiries) == 0:
            return "⚠️ No options data available"

        expiries = expiries[:3]  # scan first 3 expiries

        best = None
        best_score = 0

        for expiry in expiries:
            try:
                chain = stock.option_chain(expiry)
                options = chain.calls if trend == "Bullish" else chain.puts

                if options is None or options.empty:
                    continue

                options = options.fillna(0)

                # ✅ Strike selection logic
                if strength == "Strong":
                    options = options[
                        (options['strike'] < price) if trend=="Bullish"
                        else (options['strike'] > price)
                    ]
                else:
                    options = options[
                        (options['strike'] > price*0.95) &
                        (options['strike'] < price*1.05)
                    ]

                if options.empty:
                    continue

                # ✅ Spread logic
                options['spread'] = options['ask'] - options['bid']
                options['mid'] = (options['ask'] + options['bid']) / 2

                options = options[
                    (options['mid'] > 0) &
                    (options['spread'] > 0) &
                    ((options['spread'] / options['mid']) <= 0.10)
                ]

                if options.empty:
                    continue

                # ✅ Liquidity score
                options['liq'] = options['volume'] + options['openInterest']

                top = options.sort_values(by="liq", ascending=False).iloc[0]

                if top['liq'] > best_score:
                    best_score = top['liq']
                    best = (top, expiry)

            except:
                continue  # ✅ skip bad expiry instead of crashing

        if best is None:
            return "⚠️ No high-quality contracts found (low liquidity or wide spreads)"

        row, expiry = best

        return (
            f"{'CALL' if trend=='Bullish' else 'PUT'} ({strength})\n"
            f"Strike: {row['strike']} | Exp: {expiry}\n"
            f"Price: ${round(row['lastPrice'],2)}\n"
            f"Vol: {int(row['volume'])} | OI: {int(row['openInterest'])}\n"
            f"Spread: {round(row['spread'],2)} ✅"
        )

    except Exception as e:
        return f"⚠️ Options temporarily unavailable"


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

    option = get_option_data(ticker, price, trend, strength)

    return {
        "trend": trend,
        "strength": strength,
        "entry": entry,
        "stop": stop,
        "target": target,
        "rr": rr,
        "option": option
    }


# ✅ SCALPING
def scalp(df):
    latest = df.iloc[-1]
    price = latest['Close']

    high = df['High'].tail(5).max()
    low = df['Low'].tail(5).min()

    range_pct = (high - low) / price

    if range_pct < 0.01:
        return "Low volatility → avoid"

    if price > high:
        return f"Breakout scalp → {round(price,2)}"
    elif price < low:
        return f"Breakdown scalp → {round(price,2)}"
    else:
        return "No clear scalp"


# ✅ RUN
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
            st.warning("⚠️ No high-quality trade setup")
        else:
            st.subheader(f"{ticker} Analysis")

            st.write(f"Trend: {result['trend']} ({result['strength']})")
            st.write(f"Entry: {round(result['entry'],2)}")
            st.write(f"Stop: {round(result['stop'],2)}")
            st.write(f"Target: {round(result['target'],2)}")
            st.write(f"RR: {round(result['rr'],2)}")

            st.write("Options")
            st.text(result["option"])

            st.write("Intraday")
            st.write(scalp(intraday))

        st.warning("Not financial advice")
