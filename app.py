import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import os
import json
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
    .win-row  { background: #0d2b1a; border-left: 3px solid #22c55e; padding: 8px 12px; border-radius: 6px; margin-bottom: 6px; }
    .loss-row { background: #2b0d0d; border-left: 3px solid #ef4444; padding: 8px 12px; border-radius: 6px; margin-bottom: 6px; }
    .open-row { background: #1a1a2e; border-left: 3px solid #f59e0b; padding: 8px 12px; border-radius: 6px; margin-bottom: 6px; }
    .alert-row{ background: #1e1e2e; border-left: 3px solid #6366f1; padding: 8px 12px; border-radius: 6px; margin-bottom: 6px; }
</style>
""", unsafe_allow_html=True)

st.title("🤖 Trading Copilot ELITE")
st.caption("Swing trading · Options · Alerts · Auto Journal · Win/Loss Tracking")

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
COOLDOWN      = 600
BUDGET_MAX    = 2.00
MIN_DTE       = 7
MIN_RR        = 1.5
MIN_ROWS      = 50
VOLUME_MULT   = 1.0

# ── Persistent storage files (written alongside the script) ──
ALERT_LOG_FILE  = "alert_history.json"
JOURNAL_FILE    = "trade_journal.json"

SENT_ALERTS: dict[str, float] = {}


# ─────────────────────────────────────────────
# PERSISTENCE HELPERS
# ─────────────────────────────────────────────
def _load(path: str) -> list:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(path: str, data: list) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def load_alerts() -> list:
    return _load(ALERT_LOG_FILE)


def save_alerts(data: list) -> None:
    _save(ALERT_LOG_FILE, data)


def load_journal() -> list:
    return _load(JOURNAL_FILE)


def save_journal(data: list) -> None:
    _save(JOURNAL_FILE, data)


def log_alert(ticker: str, trend: str, strength: str,
              entry: float, stop: float, target: float,
              rr: float, price: float) -> None:
    """Append a fired alert to the persistent alert history."""
    alerts = load_alerts()
    record = {
        "id":        f"{ticker}_{int(time.time())}",
        "timestamp": datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d %H:%M ET"),
        "ticker":    ticker,
        "trend":     trend,
        "strength":  strength,
        "price":     price,
        "entry":     entry,
        "stop":      stop,
        "target":    target,
        "rr":        rr,
        "journaled": False,   # True once user logs outcome
    }
    # Avoid duplicates within the last 10 min
    recent = [a for a in alerts if a["ticker"] == ticker]
    if recent:
        last_ts = recent[-1]["timestamp"]
        try:
            last_epoch = datetime.strptime(last_ts, "%Y-%m-%d %H:%M ET").timestamp()
            if time.time() - last_epoch < COOLDOWN:
                return
        except Exception:
            pass

    alerts.append(record)
    save_alerts(alerts)


def add_journal_trade(alert_id: str, ticker: str, trend: str,
                      entry: float, stop: float, target: float,
                      rr: float, exit_price: float,
                      outcome: str, notes: str, setup_date: str) -> None:
    """Write a completed trade to the journal."""
    journal = load_journal()
    pnl_r   = round((exit_price - entry) / abs(entry - stop), 2) \
              if trend == "Bullish" \
              else round((entry - exit_price) / abs(entry - stop), 2)

    record = {
        "id":         alert_id,
        "date":       setup_date,
        "closed":     datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d %H:%M ET"),
        "ticker":     ticker,
        "trend":      trend,
        "entry":      entry,
        "stop":       stop,
        "target":     target,
        "planned_rr": rr,
        "exit_price": exit_price,
        "outcome":    outcome,   # "WIN" | "LOSS" | "BREAKEVEN"
        "actual_rr":  pnl_r,
        "notes":      notes,
    }
    # Overwrite if same id exists
    journal = [j for j in journal if j["id"] != alert_id]
    journal.append(record)
    save_journal(journal)

    # Mark alert as journaled
    alerts = load_alerts()
    for a in alerts:
        if a["id"] == alert_id:
            a["journaled"] = True
    save_alerts(alerts)


def journal_stats(journal: list) -> dict:
    if not journal:
        return {}
    wins   = [j for j in journal if j["outcome"] == "WIN"]
    losses = [j for j in journal if j["outcome"] == "LOSS"]
    be     = [j for j in journal if j["outcome"] == "BREAKEVEN"]
    total  = len(journal)
    wr     = round(len(wins) / total * 100, 1) if total else 0

    avg_win  = round(sum(j["actual_rr"] for j in wins)   / len(wins),   2) if wins   else 0
    avg_loss = round(sum(j["actual_rr"] for j in losses) / len(losses),  2) if losses else 0
    total_r  = round(sum(j["actual_rr"] for j in journal), 2)

    gross_profit = sum(j["actual_rr"] for j in wins   if j["actual_rr"] > 0)
    gross_loss   = abs(sum(j["actual_rr"] for j in losses if j["actual_rr"] < 0))
    pf = round(gross_profit / gross_loss, 2) if gross_loss else float("inf")

    # Streak
    outcomes = [j["outcome"] for j in sorted(journal, key=lambda x: x["closed"])]
    streak = 0
    streak_type = outcomes[-1] if outcomes else ""
    for o in reversed(outcomes):
        if o == streak_type:
            streak += 1
        else:
            break

    return {
        "total": total, "wins": len(wins), "losses": len(losses),
        "breakeven": len(be), "win_rate": wr,
        "avg_win_r": avg_win, "avg_loss_r": avg_loss,
        "total_r": total_r, "profit_factor": pf,
        "streak": streak, "streak_type": streak_type,
    }


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
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    return df if len(df) >= MIN_ROWS else None


# ─────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────
def compute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["EMA20"]     = ta.trend.ema_indicator(df["Close"], window=20)
    df["EMA50"]     = ta.trend.ema_indicator(df["Close"], window=50)
    macd_obj        = ta.trend.MACD(df["Close"])
    df["MACD"]      = macd_obj.macd()
    df["Signal"]    = macd_obj.macd_signal()
    df["RSI"]       = ta.momentum.rsi(df["Close"], window=14)
    df["ATR"]       = ta.volatility.average_true_range(df["High"], df["Low"], df["Close"], window=14)
    bb              = ta.volatility.BollingerBands(df["Close"], window=20)
    df["BB_UP"]     = bb.bollinger_hband()
    df["BB_LO"]     = bb.bollinger_lband()
    df["VOL_AVG20"] = df["Volume"].rolling(20).mean()
    return df.dropna(subset=["EMA20", "EMA50", "MACD", "Signal", "RSI", "ATR"])


# ─────────────────────────────────────────────
# OPTIONS ENGINE  (cached 5 min)
# ─────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def get_option_data(ticker: str, price: float, trend: str, strength: str) -> dict:
    try:
        stock    = yf.Ticker(ticker)
        expiries = stock.options
        if not expiries:
            return {"error": "No option chain available"}

        today      = pd.Timestamp.today().normalize()
        best       = None
        best_score = 0

        for expiry in expiries[:4]:
            dte = (pd.Timestamp(expiry) - today).days
            if dte < MIN_DTE:
                continue
            try:
                chain = stock.option_chain(expiry)
                opts  = chain.calls if trend == "Bullish" else chain.puts
                opts  = opts.fillna(0)
                if strength == "Strong":
                    opts = opts[(opts["strike"] <= price * 1.02) if trend == "Bullish"
                                else (opts["strike"] >= price * 0.98)]
                else:
                    opts = opts[(opts["strike"] >= price * 0.95) & (opts["strike"] <= price * 1.05)]
                if opts.empty:
                    continue
                opts          = opts.copy()
                opts["spread"]= opts["ask"] - opts["bid"]
                opts["mid"]   = (opts["ask"] + opts["bid"]) / 2
                valid = opts[(opts["mid"] > 0) & (opts["spread"] / opts["mid"] <= 0.15)]
                valid = valid[(valid["volume"] > 0) | (valid["openInterest"] > 0)]
                if valid.empty:
                    continue
                valid["liq"] = valid["volume"] + valid["openInterest"]
                top = valid.sort_values("liq", ascending=False).iloc[0]
                if top["liq"] > best_score:
                    best       = (top, expiry, dte)
                    best_score = top["liq"]
            except Exception:
                continue

        if best is None:
            return {"error": "No liquid options found (spread too wide or no OI)"}

        row, expiry, dte = best
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
            "is_budget":  row["mid"] <= BUDGET_MAX,
        }
    except Exception as e:
        return {"error": f"Option data unavailable ({e})"}


# ─────────────────────────────────────────────
# TRADE ANALYSIS
# ─────────────────────────────────────────────
def analyze(df: pd.DataFrame, ticker: str) -> dict | None:
    latest  = df.iloc[-1]
    price   = float(latest["Close"])
    ema20   = float(latest["EMA20"])
    ema50   = float(latest["EMA50"])
    rsi     = float(latest["RSI"])
    macd    = float(latest["MACD"])
    signal  = float(latest["Signal"])
    atr     = float(latest["ATR"])
    volume  = float(latest["Volume"])
    vol_avg = float(latest["VOL_AVG20"])

    vol_ok = volume >= vol_avg * VOLUME_MULT

    if price > ema20 > ema50 and macd > signal and 40 < rsi < 75 and vol_ok:
        trend = "Bullish"
    elif price < ema20 < ema50 and macd < signal and 25 < rsi < 60 and vol_ok:
        trend = "Bearish"
    else:
        return None

    strength = "Strong" if (
        (rsi > 60 and trend == "Bullish") or (rsi < 40 and trend == "Bearish")
    ) else "Normal"

    lookback_high = df["High"].iloc[-6:-1].max()
    lookback_low  = df["Low"].iloc[-6:-1].min()

    if trend == "Bullish":
        entry      = round(lookback_high * 1.002, 2)
        stop       = round(price - atr, 2)
        resistance = float(df["High"].tail(20).max())
        target     = round(min(price + atr * 2.5, resistance * 0.99), 2)
    else:
        entry   = round(lookback_low * 0.998, 2)
        stop    = round(price + atr, 2)
        support = float(df["Low"].tail(20).min())
        target  = round(max(price - atr * 2.5, support * 1.01), 2)

    risk = abs(entry - stop)
    if risk < 0.01:
        return None

    rr = round(abs(target - entry) / risk, 2)
    if rr < MIN_RR:
        return None

    option = get_option_data(ticker, price, trend, strength)

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
        "high_quality": (rr >= 2.0 and strength == "Strong"),
    }


# ─────────────────────────────────────────────
# SCALP ENGINE
# ─────────────────────────────────────────────
def scalp(df: pd.DataFrame) -> dict:
    latest     = df.iloc[-1]
    price      = float(latest["Close"])
    atr        = float(latest["ATR"]) if "ATR" in df.columns else 0
    prior_high = float(df["High"].iloc[-6:-1].max())
    prior_low  = float(df["Low"].iloc[-6:-1].min())

    if (prior_high - prior_low) / price < 0.005:
        return {"signal": "Low volatility — avoid scalping", "direction": None}

    rsi  = float(latest["RSI"])   if "RSI"    in df.columns else 50
    macd = float(latest["MACD"])  if "MACD"   in df.columns else 0
    sig  = float(latest["Signal"])if "Signal" in df.columns else 0

    if price > prior_high and macd > sig and rsi < 75:
        return {"signal": f"Breakout scalp ↑ {round(price,2)}", "direction": "Long",
                "stop": round(prior_high - atr * 0.5, 2), "target": round(price + atr, 2)}
    elif price < prior_low and macd < sig and rsi > 25:
        return {"signal": f"Breakdown scalp ↓ {round(price,2)}", "direction": "Short",
                "stop": round(prior_low + atr * 0.5, 2), "target": round(price - atr, 2)}
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
# MARKET STATUS
# ─────────────────────────────────────────────
market_open = is_market_open()
if market_open:
    st.success("🟢 Market is OPEN — live alerts active")
else:
    st.warning("🔴 Market is CLOSED — alerts paused (analysis still available)")

st.divider()

# ─────────────────────────────────────────────
# TOP-LEVEL TABS
# ─────────────────────────────────────────────
TAB_SCAN, TAB_STOCK, TAB_ALERTS, TAB_JOURNAL = st.tabs([
    "📡 Watchlist Scan",
    "🔍 Stock Analysis",
    "🔔 Alert History",
    "📓 Trade Journal",
])


# ═══════════════════════════════════════════════
# TAB 1 — WATCHLIST SCAN
# ═══════════════════════════════════════════════
with TAB_SCAN:
    with st.spinner("Scanning watchlist…"):
        all_setups   = run_watchlist_scan(tuple(SCAN_LIST))

    high_quality = [s for s in all_setups if s["high_quality"]]
    normal       = [s for s in all_setups if not s["high_quality"]]

    # Auto-log high-quality alerts and send Telegram
    for a in high_quality:
        log_alert(
            ticker=a["ticker"], trend=a["trend"], strength=a["strength"],
            entry=a["entry"], stop=a["stop"], target=a["target"],
            rr=a["rr"], price=a["price"],
        )
        if market_open:
            msg = (
                f"🚨 HIGH QUALITY ALERT\n"
                f"{a['ticker']} → {a['trend']} ({a['strength']})\n"
                f"Price: {a['price']} | RR: {a['rr']}\n"
                f"Entry: {a['entry']} | Stop: {a['stop']} | Target: {a['target']}"
            )
            send_telegram_alert(a["ticker"], msg)

    col_hq, col_norm = st.columns(2)

    with col_hq:
        st.markdown("### 🔥 High-Quality Setups")
        if high_quality:
            for a in high_quality:
                with st.container(border=True):
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Ticker", a["ticker"])
                    c2.metric("Trend",  f"{a['trend']} ({a['strength']})")
                    c3.metric("R:R",    a["rr"])
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


# ═══════════════════════════════════════════════
# TAB 2 — SINGLE STOCK ANALYSIS
# ═══════════════════════════════════════════════
with TAB_STOCK:
    st.subheader("🔍 Single Stock Analysis")
    query = st.text_input("Enter ticker (e.g. TSLA, NVDA, AAPL)", placeholder="TSLA", key="ticker_input")

    if query:
        ticker = query.strip().upper()

        with st.spinner(f"Fetching {ticker}…"):
            df       = get_data(ticker)
            intraday = get_data(ticker, period="5d", interval="5m")

        if df is None:
            st.error(f"❌ Could not load data for **{ticker}** — check the ticker symbol.")
        else:
            df = compute(df)

            latest_price = float(df["Close"].iloc[-1])
            latest_rsi   = float(df["RSI"].iloc[-1])
            latest_atr   = float(df["ATR"].iloc[-1])
            vol_now      = float(df["Volume"].iloc[-1])
            vol_avg      = float(df["VOL_AVG20"].iloc[-1])

            pc1, pc2, pc3, pc4 = st.columns(4)
            pc1.metric("Last Price",  f"${latest_price:,.2f}")
            pc2.metric("RSI (14)",    f"{latest_rsi:.1f}")
            pc3.metric("ATR (14)",    f"${latest_atr:.2f}")
            pc4.metric("Vol vs Avg",  f"{vol_now/vol_avg:.2f}×")

            st.divider()
            r = analyze(df, ticker)

            stab1, stab2, stab3, stab4 = st.tabs(
                ["💼 Swing Trade", "🧠 Options", "⚡ Intraday Scalp", "💸 Budget Options"]
            )

            with stab1:
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
                    s4.metric("R:R",    r["rr"])
                    risk_amt   = abs(r["entry"] - r["stop"])
                    reward_amt = abs(r["target"] - r["entry"])
                    st.progress(
                        min(reward_amt / (risk_amt + reward_amt), 1.0),
                        text=f"Reward ${reward_amt:.2f} vs Risk ${risk_amt:.2f}"
                    )

            with stab2:
                if r is None:
                    st.warning("Swing trade setup required for options recommendation.")
                else:
                    opt = r["option"]
                    if "error" in opt:
                        st.error(f"⚠️ {opt['error']}")
                    else:
                        emoji = "📈" if opt["label"] == "CALL" else "📉"
                        st.markdown(f"### {emoji} {opt['label']} — Exp {opt['expiry']} ({opt['dte']} DTE)")
                        o1, o2, o3, o4 = st.columns(4)
                        o1.metric("Strike",    f"${opt['strike']}")
                        o2.metric("Mid Price", f"${opt['mid']}")
                        o3.metric("Volume",    f"{opt['volume']:,}")
                        o4.metric("Open Int.", f"{opt['oi']:,}")
                        spread_pct = (opt["spread"] / opt["mid"] * 100) if opt["mid"] else 0
                        st.caption(f"Spread: ${opt['spread']} ({spread_pct:.1f}% of mid) · Last: ${opt['last_price']}")
                        if opt["is_budget"]:
                            st.success(f"💸 Budget pick — ${opt['mid']}/contract (under ${BUDGET_MAX:.2f})")

            with stab3:
                if intraday is None or len(intraday) < 20:
                    st.warning("Not enough intraday data.")
                else:
                    intraday = compute(intraday)
                    sc = scalp(intraday)
                    if sc["direction"] is None:
                        st.info(f"ℹ️ {sc['signal']}")
                    else:
                        arrow = "↑" if sc["direction"] == "Long" else "↓"
                        st.markdown(f"### ⚡ {sc['signal']} {arrow}")
                        sc1, sc2 = st.columns(2)
                        sc1.metric("Scalp Stop",   f"${sc.get('stop','N/A')}")
                        sc2.metric("Scalp Target", f"${sc.get('target','N/A')}")
                        st.caption("Scalp targets are intraday — tight stops, monitor closely.")

            with stab4:
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
                        st.caption("Budget options carry higher gamma risk — size accordingly.")
                    else:
                        st.info(
                            f"Best contract is ${opt['mid']}/contract — above the ${BUDGET_MAX:.2f} threshold. "
                            "Try a wider strike or longer expiry to reduce premium."
                        )

            st.divider()
            st.caption("⚠️ Not financial advice. Rule-based signals only. Use your own judgment.")


# ═══════════════════════════════════════════════
# TAB 3 — ALERT HISTORY
# ═══════════════════════════════════════════════
with TAB_ALERTS:
    st.subheader("🔔 Alert History")

    alerts = load_alerts()

    if not alerts:
        st.info("No alerts fired yet. Run the watchlist scan to generate alerts.")
    else:
        # ── Summary strip ──
        total_alerts  = len(alerts)
        journaled_cnt = sum(1 for a in alerts if a.get("journaled"))
        pending_cnt   = total_alerts - journaled_cnt

        ac1, ac2, ac3 = st.columns(3)
        ac1.metric("Total Alerts",   total_alerts)
        ac2.metric("Journaled",      journaled_cnt)
        ac3.metric("Pending Journal",pending_cnt)

        st.divider()

        # ── Filter controls ──
        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            ticker_filter = st.selectbox(
                "Filter by ticker",
                ["All"] + sorted(set(a["ticker"] for a in alerts)),
                key="alert_ticker_filter"
            )
        with col_f2:
            trend_filter = st.selectbox(
                "Filter by trend",
                ["All", "Bullish", "Bearish"],
                key="alert_trend_filter"
            )
        with col_f3:
            journal_filter = st.selectbox(
                "Journal status",
                ["All", "Pending", "Journaled"],
                key="alert_journal_filter"
            )

        filtered = alerts
        if ticker_filter != "All":
            filtered = [a for a in filtered if a["ticker"] == ticker_filter]
        if trend_filter != "All":
            filtered = [a for a in filtered if a["trend"] == trend_filter]
        if journal_filter == "Pending":
            filtered = [a for a in filtered if not a.get("journaled")]
        elif journal_filter == "Journaled":
            filtered = [a for a in filtered if a.get("journaled")]

        st.markdown(f"**{len(filtered)} alert(s) shown**")

        # ── Alert rows (newest first) ──
        for a in reversed(filtered):
            trend_badge = "🟢" if a["trend"] == "Bullish" else "🔴"
            jrnl_badge  = "✅" if a.get("journaled") else "⏳"

            with st.container(border=True):
                col_a, col_b, col_c, col_d, col_e = st.columns([1.5, 1, 1.5, 1.5, 1])
                col_a.markdown(f"**{a['ticker']}** {trend_badge} {a['trend']}")
                col_b.markdown(f"RR **{a['rr']}**")
                col_c.markdown(f"Entry `{a['entry']}` → Target `{a['target']}`")
                col_d.markdown(f"🕒 {a['timestamp']}")
                col_e.markdown(f"{jrnl_badge} {'Logged' if a.get('journaled') else 'Pending'}")

        st.divider()

        # ── Clear history button ──
        if st.button("🗑️ Clear all alert history", type="secondary"):
            save_alerts([])
            st.success("Alert history cleared.")
            st.rerun()


# ═══════════════════════════════════════════════
# TAB 4 — TRADE JOURNAL
# ═══════════════════════════════════════════════
with TAB_JOURNAL:
    st.subheader("📓 Trade Journal — Auto Win/Loss Tracker")

    journal = load_journal()
    alerts  = load_alerts()

    # ── Stats dashboard ──
    stats = journal_stats(journal)

    if stats:
        st.markdown("### 📊 Performance Dashboard")
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Total Trades",   stats["total"])
        m2.metric("Win Rate",       f"{stats['win_rate']}%")
        m3.metric("Wins / Losses",  f"{stats['wins']} / {stats['losses']}")
        m4.metric("Avg Win (R)",    stats["avg_win_r"])
        m5.metric("Profit Factor",  stats["profit_factor"])
        m6.metric("Total R",        stats["total_r"])

        streak_emoji = "🔥" if stats["streak_type"] == "WIN" else "❄️"
        st.caption(
            f"{streak_emoji} Current streak: **{stats['streak']} {stats['streak_type']}** in a row"
        )
        st.divider()

    # ── Log a new trade from an alert ──
    unjournaled = [a for a in alerts if not a.get("journaled")]

    st.markdown("### ➕ Log Trade Outcome")

    if not unjournaled:
        st.info("No pending alerts to journal. Alerts from the watchlist scan appear here automatically.")
    else:
        selected_label = st.selectbox(
            "Select alert to journal",
            options=[f"{a['ticker']} | {a['trend']} | Entry {a['entry']} | {a['timestamp']}"
                     for a in unjournaled],
            key="journal_select"
        )
        selected_idx = [
            f"{a['ticker']} | {a['trend']} | Entry {a['entry']} | {a['timestamp']}"
            for a in unjournaled
        ].index(selected_label)
        sel = unjournaled[selected_idx]

        with st.container(border=True):
            st.markdown(
                f"**{sel['ticker']}** · {sel['trend']} ({sel['strength']}) · "
                f"Entry `{sel['entry']}` · Stop `{sel['stop']}` · Target `{sel['target']}` · "
                f"R:R `{sel['rr']}` · Alerted: {sel['timestamp']}"
            )

            jc1, jc2 = st.columns(2)
            with jc1:
                exit_price = st.number_input(
                    "Exit Price ($)", min_value=0.01, value=float(sel["entry"]),
                    step=0.01, key="exit_price_input"
                )
                outcome = st.radio(
                    "Outcome", ["WIN", "LOSS", "BREAKEVEN"],
                    horizontal=True, key="outcome_radio"
                )
            with jc2:
                notes = st.text_area(
                    "Notes (setup quality, mistakes, lessons)",
                    placeholder="e.g. Held through news, stopped out early, thesis played out…",
                    key="journal_notes", height=100
                )

            # Preview actual R
            risk = abs(sel["entry"] - sel["stop"])
            if risk > 0:
                if sel["trend"] == "Bullish":
                    preview_r = round((exit_price - sel["entry"]) / risk, 2)
                else:
                    preview_r = round((sel["entry"] - exit_price) / risk, 2)
                color = "green" if preview_r > 0 else "red"
                st.markdown(f"**Actual R: :{color}[{preview_r}R]**")

            if st.button("💾 Save to Journal", type="primary", key="save_journal_btn"):
                add_journal_trade(
                    alert_id=sel["id"], ticker=sel["ticker"], trend=sel["trend"],
                    entry=sel["entry"], stop=sel["stop"], target=sel["target"],
                    rr=sel["rr"], exit_price=exit_price,
                    outcome=outcome, notes=notes, setup_date=sel["timestamp"],
                )
                st.success(f"✅ Trade logged: {sel['ticker']} → {outcome}")
                st.rerun()

    st.divider()

    # ── Journal history ──
    st.markdown("### 📋 Trade History")

    if not journal:
        st.info("No trades logged yet. Log your first trade above.")
    else:
        # Filter controls
        jf1, jf2, jf3 = st.columns(3)
        with jf1:
            j_ticker = st.selectbox(
                "Ticker", ["All"] + sorted(set(j["ticker"] for j in journal)),
                key="j_ticker_filter"
            )
        with jf2:
            j_outcome = st.selectbox(
                "Outcome", ["All", "WIN", "LOSS", "BREAKEVEN"],
                key="j_outcome_filter"
            )
        with jf3:
            j_trend = st.selectbox(
                "Direction", ["All", "Bullish", "Bearish"],
                key="j_trend_filter"
            )

        filtered_j = journal
        if j_ticker  != "All": filtered_j = [j for j in filtered_j if j["ticker"]  == j_ticker]
        if j_outcome != "All": filtered_j = [j for j in filtered_j if j["outcome"] == j_outcome]
        if j_trend   != "All": filtered_j = [j for j in filtered_j if j["trend"]   == j_trend]

        for j in reversed(filtered_j):
            outcome_emoji = {"WIN": "✅", "LOSS": "❌", "BREAKEVEN": "➖"}.get(j["outcome"], "❓")
            r_color = "🟢" if j["actual_rr"] > 0 else ("🔴" if j["actual_rr"] < 0 else "⚪")

            with st.expander(
                f"{outcome_emoji} {j['ticker']} · {j['trend']} · "
                f"Actual: {r_color} {j['actual_rr']}R · Closed: {j['closed']}"
            ):
                d1, d2, d3, d4 = st.columns(4)
                d1.metric("Entry",      f"${j['entry']}")
                d2.metric("Exit",       f"${j['exit_price']}")
                d3.metric("Planned R:R",j["planned_rr"])
                d4.metric("Actual R",   j["actual_rr"])

                st.caption(f"Stop: ${j['stop']} · Target: ${j['target']} · Alerted: {j['date']}")
                if j.get("notes"):
                    st.markdown(f"📝 *{j['notes']}*")

                if st.button("🗑️ Delete entry", key=f"del_{j['id']}", type="secondary"):
                    new_journal = [x for x in journal if x["id"] != j["id"]]
                    save_journal(new_journal)
                    # Un-mark alert as journaled
                    alerts_list = load_alerts()
                    for al in alerts_list:
                        if al["id"] == j["id"]:
                            al["journaled"] = False
                    save_alerts(alerts_list)
                    st.rerun()

        st.divider()
        if st.button("🗑️ Clear entire journal", type="secondary", key="clear_journal"):
            save_journal([])
            # Reset journaled flags
            al_list = load_alerts()
            for al in al_list:
                al["journaled"] = False
            save_alerts(al_list)
            st.success("Journal cleared.")
            st.rerun()

    st.caption("⚠️ Not financial advice. Journal is for personal tracking only.")
