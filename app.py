import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import os
import requests
import time
from datetime import datetime
import pytz

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Trading Copilot ELITE",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; }
    .stAlert { border-radius: 8px; }
    div[data-testid="metric-container"] {
        background: #1e1e2e;
        border: 1px solid #333;
        border-radius: 8px;
        padding: 12px;
    }
</style>
""", unsafe_allow_html=True)

st.title("🤖 Trading Copilot ELITE")
st.caption("Swing trading · Options · Real-time alerts · Technical analysis")

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
WATCHLIST = [
    "TSLA", "NVDA", "AAPL", "MSFT", "AMZN",
    "META", "AMD", "SPY", "QQQ", "INTC",
    "NFLX", "BABA", "CSCO", "GOOGL"
]

FAST_MODE     = True
SCAN_LIST     = WATCHLIST[:10] if FAST_MODE else WATCHLIST
COOLDOWN      = 600          # seconds between Telegram alerts per ticker
BUDGET_MAX    = 2.00         # max option mid-price for "budget" picks
MIN_DTE       = 7            # skip options expiring in fewer than 7 days
MIN_RR        = 1.5          # minimum reward:risk ratio
MIN_ROWS      = 50           # minimum candle rows before analysis
VOLUME_MULT   = 1.0          # volume must be ≥ this × 20-day avg

SENT_ALERTS: dict[str, float] = {}


# ─────────────────────────────────────────────
# MARKET HOURS
# ─────────────────────────────────────────────
def is_market_open() -> bool:
    try:
        tz   = pytz.timezone("America/New_York")
        now  = datetime.now(tz)
        if now.weekday() >= 5:
            return False
        open_  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
        close_ = now.replace(hour=16, minute=0,  second=0, microsecond=0)
        return open_ <= now <= close_
    except Exception:
        return False


# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
def send_telegram_alert(ticker: str, message: str) -> None:
    TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
    CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
    if not TOKEN or not CHAT_ID:
        return

    now = time.time()
    if ticker in SENT_ALERTS and (now - SENT_ALERTS[ticker]) < COOLDOWN:
        return

    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": message}, timeout=5)
        SENT_ALERTS[ticker] = now
    except Exception:
        pass


# ─────────────────────────────────────────────
# DATA FETCH  (cached 5 min)
# ─────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def get_data(ticker: str, period: str = "3mo", interval: str = "1d") -> pd.DataFrame | None:
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False)
    except Exception:
        return None

    if df is None or df.empty or len(df) < MIN_ROWS:
        return None

    # Flatten MultiIndex columns produced by newer yfinance versions
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Drop rows where core OHLCV data is missing
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])

    if len(df) < MIN_ROWS:
        return None

    return df


# ─────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────
def compute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Trend
    df["EMA20"] = ta.trend.ema_indicator(df["Close"], window=20)
    df["EMA50"] = ta.trend.ema_indicator(df["Close"], window=50)

    # Momentum
    macd_obj    = ta.trend.MACD(df["Close"])
    df["MACD"]  = macd_obj.macd()
    df["Signal"]= macd_obj.macd_signal()
    df["RSI"]   = ta.momentum.rsi(df["Close"], window=14)

    # Volatility
    df["ATR"]   = ta.volatility.average_true_range(
        df["High"], df["Low"], df["Close"], window=14
    )

    # Bollinger band width (extra volatility context)
    bb          = ta.volatility.BollingerBands(df["Close"], window=20)
    df["BB_UP"] = bb.bollinger_hband()
    df["BB_LO"] = bb.bollinger_lband()

    # Volume baseline
    df["VOL_AVG20"] = df["Volume"].rolling(20).mean()

    # Drop rows that still carry NaN after indicator warm-up
    df = df.dropna(subset=["EMA20", "EMA50", "MACD", "Signal", "RSI", "ATR"])

    return df


# ─────────────────────────────────────────────
# OPTIONS ENGINE  (cached 5 min)
# ─────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def get_option_data(ticker: str, price: float, trend: str, strength: str) -> dict:
    """
    Returns a dict with keys:
        label, strike, expiry, mid, last_price,
        volume, oi, spread, dte, is_budget
    or a dict with key 'error'.
    """
    try:
        stock   = yf.Ticker(ticker)
        expiries= stock.options  # all available dates

        if not expiries:
            return {"error": "No option chain available"}

        today = pd.Timestamp.today().normalize()
        best  = None
        best_score = 0

        for expiry in expiries[:4]:   # check up to 4 nearest expiries
            dte = (pd.Timestamp(expiry) - today).days
            if dte < MIN_DTE:
                continue  # skip contracts too close to expiry (theta risk)

            try:
                chain = stock.option_chain(expiry)
                opts  = chain.calls if trend == "Bullish" else chain.puts
                opts  = opts.fillna(0)

                # Strike selection based on strength
                if strength == "Strong":
                    # Slightly in-the-money
                    opts = opts[
                        (opts["strike"] <= price * 1.02)
                        if trend == "Bullish"
                        else (opts["strike"] >= price * 0.98)
                    ]
                else:
                    # Near-the-money ±5 %
                    opts = opts[
                        (opts["strike"] >= price * 0.95) &
                        (opts["strike"] <= price * 1.05)
                    ]

                if opts.empty:
                    continue

                opts = opts.copy()
                opts["spread"] = opts["ask"] - opts["bid"]
                opts["mid"]    = (opts["ask"] + opts["bid"]) / 2

                # Liquidity filter: tight spread ≤ 15 % of mid
                valid = opts[(opts["mid"] > 0) & (opts["spread"] / opts["mid"] <= 0.15)]

                # Volume + open interest must exist
                valid = valid[(valid["volume"] > 0) | (valid["openInterest"] > 0)]

                if valid.empty:
                    continue

                valid["liq"] = valid["volume"] + valid["openInterest"]
                top = valid.sort_values("liq", ascending=False).iloc[0]

                if top["liq"] > best_score:
                    best        = (top, expiry, dte)
                    best_score  = top["liq"]

            except Exception:
                continue

        if best is None:
            return {"error": "No liquid options found (spread too wide or no OI)"}

        row, expiry, dte = best
        is_budget = row["mid"] <= BUDGET_MAX

        return {
            "label":      "CALL" if trend == "Bullish" else "PUT",
            "strike":     round(float(row["strike"]), 2),
            "expiry":     expiry,
            "mid":        round(float(row["mid"]), 2),
            "last_price": round(float(row["lastPrice"]), 2),
            "volume":     int(row["volume"]),
            "oi":         int(row["openInterest"]),
            "spread":     round(float(row["spread"]), 2),
            "dte":        dte,
            "is_budget":  is_budget,
        }

    except Exception as e:
        return {"error": f"Option data unavailable ({e})"}


# ─────────────────────────────────────────────
# TRADE ANALYSIS
# ─────────────────────────────────────────────
def analyze(df: pd.DataFrame, ticker: str) -> dict | None:
    latest = df.iloc[-1]

    price  = float(latest["Close"])
    ema20  = float(latest["EMA20"])
    ema50  = float(latest["EMA50"])
    rsi    = float(latest["RSI"])
    macd   = float(latest["MACD"])
    signal = float(latest["Signal"])
    atr    = float(latest["ATR"])
    volume = float(latest["Volume"])
    vol_avg= float(latest["VOL_AVG20"])

    # ── Trend: require EMA stack + MACD + RSI + volume confirmation ──
    macd_bull  = macd > signal
    macd_bear  = macd < signal
    vol_ok     = volume >= vol_avg * VOLUME_MULT

    if price > ema20 > ema50 and macd_bull and 40 < rsi < 75 and vol_ok:
        trend = "Bullish"
    elif price < ema20 < ema50 and macd_bear and 25 < rsi < 60 and vol_ok:
        trend = "Bearish"
    else:
        return None   # mixed / low-conviction signal

    # ── Signal strength ──
    strength = "Strong" if (
        (rsi > 60 and trend == "Bullish") or
        (rsi < 40 and trend == "Bearish")
    ) else "Normal"

    # ── Breakout level (exclude current bar) ──
    lookback_high = df["High"].iloc[-6:-1].max()
    lookback_low  = df["Low"].iloc[-6:-1].min()

    # ── Entry: breakout with small buffer ──
    if trend == "Bullish":
        entry  = round(lookback_high * 1.002, 2)   # 0.2 % above 5-bar high
        stop   = round(price - atr, 2)
        raw_tgt= price + atr * 2.5
        # Cap target at 20-bar resistance
        resistance = float(df["High"].tail(20).max())
        target = round(min(raw_tgt, resistance * 0.99), 2)
    else:
        entry  = round(lookback_low * 0.998, 2)    # 0.2 % below 5-bar low
        stop   = round(price + atr, 2)
        raw_tgt= price - atr * 2.5
        # Floor target at 20-bar support
        support = float(df["Low"].tail(20).min())
        target = round(max(raw_tgt, support * 1.01), 2)

    # Guard against zero-division
    risk = abs(entry - stop)
    if risk < 0.01:
        return None

    reward = abs(target - entry)
    rr     = round(reward / risk, 2)

    if rr < MIN_RR:
        return None

    option = get_option_data(ticker, price, trend, strength)

    high_quality = (rr >= 2.0 and strength == "Strong")

    return {
        "ticker":       ticker,
        "price":        round(price, 2),
        "trend":        trend,
        "strength":     strength,
        "entry":        entry,
        "stop":         stop,
        "target":       target,
        "rr":           rr,
        "rsi":          round(rsi, 1),
        "atr":          round(atr, 2),
        "option":       option,
        "high_quality": high_quality,
    }


# ─────────────────────────────────────────────
# SCALP ENGINE  (intraday 5-min bars)
# ─────────────────────────────────────────────
def scalp(df: pd.DataFrame) -> dict:
    latest = df.iloc[-1]
    price  = float(latest["Close"])
    atr    = float(latest["ATR"]) if "ATR" in df.columns else 0

    # Exclude current bar so price can't equal its own high/low
    prior_high = float(df["High"].iloc[-6:-1].max())
    prior_low  = float(df["Low"].iloc[-6:-1].min())

    rng = prior_high - prior_low
    if rng / price < 0.005:   # < 0.5 % range → low volatility
        return {"signal": "Low volatility — avoid scalping", "direction": None}

    rsi   = float(latest["RSI"]) if "RSI" in df.columns else 50
    macd  = float(latest["MACD"])   if "MACD"   in df.columns else 0
    sig   = float(latest["Signal"]) if "Signal" in df.columns else 0

    if price > prior_high and macd > sig and rsi < 75:
        stop   = round(prior_high - atr * 0.5, 2)
        target = round(price + atr, 2)
        return {
            "signal":    f"Breakout scalp ↑ {round(price, 2)}",
            "direction": "Long",
            "stop":      stop,
            "target":    target,
        }
    elif price < prior_low and macd < sig and rsi > 25:
        stop   = round(prior_low  + atr * 0.5, 2)
        target = round(price - atr, 2)
        return {
            "signal":    f"Breakdown scalp ↓ {round(price, 2)}",
            "direction": "Short",
            "stop":      stop,
            "target":    target,
        }
    else:
        return {"signal": "No clear intraday setup", "direction": None}


# ─────────────────────────────────────────────
# WATCHLIST SCAN  (cached 5 min)
# ─────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def run_watchlist_scan(scan_list: tuple) -> list[dict]:
    results = []
    for t in scan_list:
        df = get_data(t)
        if df is None:
            continue
        df = compute(df)
        r  = analyze(df, t)
        if r:
            results.append(r)
    return results


# ─────────────────────────────────────────────
# MARKET STATUS BANNER
# ─────────────────────────────────────────────
market_open = is_market_open()
if market_open:
    st.success("🟢 Market is OPEN — live alerts active")
else:
    st.warning("🔴 Market is CLOSED — alerts paused (analysis still available)")

st.divider()

# ─────────────────────────────────────────────
# WATCHLIST SCAN + ALERTS
# ─────────────────────────────────────────────
with st.spinner("Scanning watchlist…"):
    all_setups = run_watchlist_scan(tuple(SCAN_LIST))

high_quality = [s for s in all_setups if s["high_quality"]]
normal       = [s for s in all_setups if not s["high_quality"]]

# Send Telegram only during market hours
if market_open:
    for a in high_quality:
        msg = (
            f"🚨 HIGH QUALITY ALERT\n"
            f"{a['ticker']} → {a['trend']} ({a['strength']})\n"
            f"Price: {a['price']} | RR: {a['rr']}\n"
            f"Entry: {a['entry']} | Stop: {a['stop']} | Target: {a['target']}"
        )
        send_telegram_alert(a["ticker"], msg)

# ── Display scan results ──
st.subheader("📡 Watchlist Scan")

col_hq, col_norm = st.columns(2)

with col_hq:
    st.markdown("### 🔥 High-Quality Setups")
    if high_quality:
        for a in high_quality:
            with st.container(border=True):
                c1, c2, c3 = st.columns(3)
                c1.metric("Ticker",  a["ticker"])
                c2.metric("Trend",   f"{a['trend']} ({a['strength']})")
                c3.metric("R:R",     a["rr"])
                st.caption(f"Entry {a['entry']} · Stop {a['stop']} · Target {a['target']} · RSI {a['rsi']}")
    else:
        st.info("No high-quality setups right now")

with col_norm:
    st.markdown("### 📋 Normal Setups")
    if normal:
        for a in normal:
            with st.container(border=True):
                st.write(f"**{a['ticker']}** — {a['trend']} | RR {a['rr']} | RSI {a['rsi']}")
    else:
        st.info("No additional setups found")

st.divider()

# ─────────────────────────────────────────────
# SINGLE STOCK DEEP-DIVE
# ─────────────────────────────────────────────
st.subheader("🔍 Single Stock Analysis")
query = st.text_input("Enter ticker (e.g. TSLA, NVDA, AAPL)", placeholder="TSLA")

if query:
    ticker = query.strip().upper()

    with st.spinner(f"Fetching data for {ticker}…"):
        df       = get_data(ticker)
        intraday = get_data(ticker, period="5d", interval="5m")

    if df is None:
        st.error(f"❌ Could not load data for **{ticker}** — check the ticker symbol.")
    else:
        df = compute(df)

        latest_price = float(df["Close"].iloc[-1])
        latest_rsi   = float(df["RSI"].iloc[-1])
        latest_atr   = float(df["ATR"].iloc[-1])

        # ── Price snapshot ──
        pc1, pc2, pc3, pc4 = st.columns(4)
        pc1.metric("Last Price", f"${latest_price:,.2f}")
        pc2.metric("RSI (14)",   f"{latest_rsi:.1f}")
        pc3.metric("ATR (14)",   f"${latest_atr:.2f}")
        vol_now = float(df["Volume"].iloc[-1])
        vol_avg = float(df["VOL_AVG20"].iloc[-1])
        pc4.metric("Vol vs Avg", f"{vol_now/vol_avg:.2f}×")

        st.divider()

        # ── Run analysis ──
        r = analyze(df, ticker)

        tab1, tab2, tab3, tab4 = st.tabs(
            ["💼 Swing Trade", "🧠 Options", "⚡ Intraday Scalp", "💸 Budget Options"]
        )

        # ── TAB 1: Swing Trade ──
        with tab1:
            if r is None:
                st.warning("⚠️ No high-quality swing setup — signal is mixed or low-volume.")
                st.markdown("""
**What's needed for a signal:**
- EMA20 > EMA50 (bullish) or EMA20 < EMA50 (bearish)
- MACD line crossed above/below signal line
- RSI in healthy range (40–75 bull / 25–60 bear)
- Volume ≥ 20-day average
""")
            else:
                badge = "🔥 HIGH QUALITY" if r["high_quality"] else "✅ VALID SETUP"
                st.markdown(f"### {badge} — {r['trend']} ({r['strength']})")

                s1, s2, s3, s4 = st.columns(4)
                s1.metric("Entry",  f"${r['entry']}")
                s2.metric("Stop",   f"${r['stop']}")
                s3.metric("Target", f"${r['target']}")
                s4.metric("R:R",    r['rr'])

                risk_amt   = abs(r["entry"] - r["stop"])
                reward_amt = abs(r["target"] - r["entry"])
                st.progress(
                    min(reward_amt / (risk_amt + reward_amt), 1.0),
                    text=f"Reward ${reward_amt:.2f} vs Risk ${risk_amt:.2f}"
                )

        # ── TAB 2: Options ──
        with tab2:
            if r is None:
                st.warning("Swing trade setup required for options recommendation.")
            else:
                opt = r["option"]
                if "error" in opt:
                    st.error(f"⚠️ {opt['error']}")
                else:
                    direction_emoji = "📈" if opt["label"] == "CALL" else "📉"
                    st.markdown(f"### {direction_emoji} {opt['label']} — Exp {opt['expiry']} ({opt['dte']} DTE)")

                    o1, o2, o3, o4 = st.columns(4)
                    o1.metric("Strike",    f"${opt['strike']}")
                    o2.metric("Mid Price", f"${opt['mid']}")
                    o3.metric("Volume",    f"{opt['volume']:,}")
                    o4.metric("Open Int.", f"{opt['oi']:,}")

                    spread_pct = (opt["spread"] / opt["mid"] * 100) if opt["mid"] else 0
                    st.caption(
                        f"Bid-Ask Spread: ${opt['spread']} ({spread_pct:.1f}% of mid) · "
                        f"Last Traded: ${opt['last_price']}"
                    )

                    if opt["is_budget"]:
                        st.success(f"💸 Budget pick — mid price ${opt['mid']} is under ${BUDGET_MAX:.2f}/contract")

        # ── TAB 3: Intraday Scalp ──
        with tab3:
            if intraday is None or len(intraday) < 20:
                st.warning("Not enough intraday data (need 5-min bars).")
            else:
                intraday = compute(intraday)
                sc = scalp(intraday)

                if sc["direction"] is None:
                    st.info(f"ℹ️ {sc['signal']}")
                else:
                    arrow = "↑" if sc["direction"] == "Long" else "↓"
                    st.markdown(f"### ⚡ {sc['signal']} {arrow}")
                    sc1, sc2 = st.columns(2)
                    sc1.metric("Scalp Stop",   f"${sc.get('stop', 'N/A')}")
                    sc2.metric("Scalp Target", f"${sc.get('target', 'N/A')}")
                    st.caption("Scalp targets are intraday — use tight stops and monitor closely.")

        # ── TAB 4: Budget Options ──
        with tab4:
            st.markdown(f"### 💸 Options under ${BUDGET_MAX:.2f}/contract")

            if r is None:
                st.warning("A valid swing setup is needed to filter budget options.")
            else:
                opt = r["option"]
                if "error" in opt:
                    st.error(f"⚠️ {opt['error']}")
                elif opt["is_budget"]:
                    st.success(
                        f"✅ **{opt['label']}** · Strike ${opt['strike']} · "
                        f"Exp {opt['expiry']} ({opt['dte']} DTE) · "
                        f"Mid **${opt['mid']}** · Vol {opt['volume']:,} · OI {opt['oi']:,}"
                    )
                    st.caption(
                        "Budget options carry higher gamma risk — position size accordingly. "
                        "Verify bid-ask spread in your broker before entering."
                    )
                else:
                    st.info(
                        f"Best available contract is ${opt['mid']}/contract, "
                        f"above the ${BUDGET_MAX:.2f} budget threshold. "
                        "Consider a wider strike or longer expiry to reduce premium."
                    )

        st.divider()
        st.caption(
            "⚠️ Not financial advice. All signals are rule-based technical analysis only. "
            "Always use your own judgment and position sizing before entering any trade."
        )
