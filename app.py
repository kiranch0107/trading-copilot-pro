import pandas as pd

def compute(df):
    # ✅ FIX: flatten columns if needed
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

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
