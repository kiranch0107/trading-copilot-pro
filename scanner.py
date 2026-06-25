import yfinance as yf
import pandas as pd
import ta
import requests
import os
from datetime import datetime
import pytz

WATCHLIST = ["TSLA","NVDA","AAPL","MSFT","AMZN","META","AMD","SPY","QQQ",
             "INTC","NFLX","BABA","CSCO","GOOGL"]


# ✅ MARKET HOURS
def is_market_open():
    tz = pytz.timezone("America/New_York")
    now = datetime.now(tz)

    if now.weekday() >= 5:
        return False

    open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_time = now.replace(hour=16, minute=0, second=0, microsecond=0)

    return open_time <= now <= close_time


# ✅ TELEGRAM ALERT
def send_alert(message):
    TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

    if not TOKEN or not CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": message})
    except:
        pass


# ✅ DATA
def get_data(ticker):
    df = yf.download(ticker, period="3mo", interval="1d")
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

    strength = "Strong" if rsi > 60 and macd > signal else "Normal"

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

    if rr < 2 or strength != "Strong":
        return None

    return {
        "ticker": ticker,
        "trend": trend,
        "rr": rr,
        "entry": entry
    }


# ✅ MAIN EXECUTION (NO LOOP)
def run():
    if not is_market_open():
        print("Market closed — skipping scan")
        return

    for t in WATCHLIST:
        df = get_data(t)
        if df is None:
            continue

        df = compute(df)
        r = analyze(df, t)

        if r:
            msg = (
                f"🚨 TRADE ALERT\n"
                f"{r['ticker']} → {r['trend']}\n"
                f"RR: {round(r['rr'],2)}\n"
                f"Entry: {round(r['entry'],2)}"
            )
            print(msg)
            send_alert(msg)


if __name__ == "__main__":
    run()