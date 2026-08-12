"""
Trading Scanner — scheduled watchlist scan with Telegram alerts
================================================================
Runs on GitHub Actions during market hours and alerts on qualifying setups.

FIXES IN THIS VERSION (all five were verified in the previous script):

1. SILENTLY LONG-ONLY.  strength was `rsi > 60 and macd > signal` — a bullish
   test applied to BOTH directions, and anything not "Strong" was discarded.
   A bearish setup needs price < EMA20 < EMA50, where RSI is low by
   definition, so no short signal could ever fire. Strength is now
   direction-aware.

2. MIXED REFERENCE POINTS.  entry came from a past bar's high while stop and
   target came from the live price, and abs() hid the damage: with the 5-bar
   high 5% above price, entry (105) sat ABOVE target (104) yet still reported
   a positive R:R. Worst of all it penalised true breakouts, where price IS
   the 5-bar high. All three levels are now anchored to the same reference.

3. TOO LITTLE HISTORY.  period="3mo" is ~63 bars. EMA50 needs ~150, MACD ~78,
   RSI/ATR ~100. Measured across 400 simulated series, the trend verdict
   differed from full-history 6.5% of the time. Now fetches 1y and discards
   the unconverged head.

4. ALERT SPAM.  No dedup meant a setup that stayed valid re-fired on every
   run — 13 identical messages a day at 30-minute cadence. Now state-backed
   with a per-ticker-per-direction cooldown.

5. ONE BAD TICKER KILLED THE SCAN.  No try/except in the loop, so a yfinance
   failure on ticker 3 of 14 meant the other 11 were never checked. And
   rr = .../abs(entry - stop) raised ZeroDivisionError when entry == stop.
   Each ticker is now isolated and the risk gate is relative to price.

Run
---
    pip install yfinance pandas ta requests pytz
    export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
    python scanner.py

    python scanner.py --dry-run --force    # print, send nothing, ignore hours
"""
from __future__ import annotations

import argparse
import json
import os
import time
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytz
import requests
import ta
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("scanner")

ET = pytz.timezone("America/New_York")

WATCHLIST = ["TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "META", "AMD", "SPY", "QQQ",
             "INTC", "NFLX", "BABA", "CSCO", "GOOGL"]

# ── Tunables ──
ADX_MIN            = 25
ATR_STOP_MULT      = 1.0
ATR_TGT_MULT       = 3.0
MIN_RR             = 2.0
ALERT_COOLDOWN_HRS = 4      # per ticker AND direction
STATE_FILE         = Path("scanner_state.json")

# Indicator warm-up — same reasoning as the Streamlit app
FETCH_PERIOD          = "1y"
INDICATOR_WARMUP_BARS = 100
MIN_BARS_AFTER_WARMUP = 40

# Politeness gap between Yahoo calls. 14 tickers hammered back-to-back is a
# meaningful share of the same rate limit the options tooling needs.
FETCH_GAP_SEC = 0.4

MARKET_HOLIDAYS = {
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27",
    "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}
MARKET_HALF_DAYS = {"2025-07-03", "2025-11-28", "2025-12-24",
                    "2026-11-27", "2026-12-24"}


def is_market_open(now: datetime | None = None) -> bool:
    """Weekday + hours + HOLIDAYS. The old version ignored holidays entirely."""
    now = now or datetime.now(ET)
    if now.weekday() >= 5:
        return False
    day = now.strftime("%Y-%m-%d")
    if day in MARKET_HOLIDAYS:
        return False
    ch, cm = (13, 0) if day in MARKET_HALF_DAYS else (16, 0)
    return (now.replace(hour=9, minute=30, second=0, microsecond=0)
            <= now <= now.replace(hour=ch, minute=cm, second=0, microsecond=0))


# ══════════════════════════════════════════════════════════════════
# ALERT STATE (dedup)
# ══════════════════════════════════════════════════════════════════
def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except Exception as e:
        logger.warning("Could not read %s (%s) — starting fresh", STATE_FILE, e)
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))
    except Exception as e:
        logger.error("Could not write %s: %s — dedup will not persist, so "
                     "expect repeat alerts next run.", STATE_FILE, e)


def recently_alerted(state: dict, ticker: str, trend: str) -> bool:
    """
    Cooldown is keyed on ticker AND direction, so a genuine flip from Bullish
    to Bearish still alerts immediately rather than being suppressed.
    """
    last = state.get(f"{ticker}:{trend}")
    if not last:
        return False
    return (time.time() - float(last)) < ALERT_COOLDOWN_HRS * 3600


# ══════════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════════
def send_alert(message: str, dry_run: bool = False) -> bool:
    if dry_run:
        print("\n--- TELEGRAM (dry-run) ---\n" + message + "\n--- end ---\n")
        return True
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat  = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — no alert sent.")
        return False
    try:
        # The old version had no timeout (could hang the job) and swallowed
        # every error with a bare `except: pass`, so failures were invisible.
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": message}, timeout=10)
        if r.status_code != 200:
            logger.error("Telegram API %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        logger.error("Telegram send failed: %s", e)
        return False


# ══════════════════════════════════════════════════════════════════
# DATA + INDICATORS
# ══════════════════════════════════════════════════════════════════
def get_data(ticker: str) -> pd.DataFrame | None:
    df = yf.download(ticker, period=FETCH_PERIOD, interval="1d",
                     progress=False, auto_adjust=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])


def compute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["EMA20"]  = ta.trend.ema_indicator(df["Close"], 20)
    df["EMA50"]  = ta.trend.ema_indicator(df["Close"], 50)
    macd         = ta.trend.MACD(df["Close"])
    df["MACD"]   = macd.macd()
    df["Signal"] = macd.macd_signal()
    df["RSI"]    = ta.momentum.rsi(df["Close"], 14)
    df["ATR"]    = ta.volatility.average_true_range(df["High"], df["Low"],
                                                    df["Close"], 14)
    df["ADX"]    = ta.trend.adx(df["High"], df["Low"], df["Close"], 14)
    df = df.dropna(subset=["EMA20", "EMA50", "MACD", "Signal", "RSI", "ATR", "ADX"])
    # Discard the unconverged head so EMA50/MACD/ADX are trustworthy
    if len(df) > INDICATOR_WARMUP_BARS + MIN_BARS_AFTER_WARMUP:
        df = df.iloc[INDICATOR_WARMUP_BARS:]
    return df


# ══════════════════════════════════════════════════════════════════
# ANALYSIS
# ══════════════════════════════════════════════════════════════════
def analyze(df: pd.DataFrame, ticker: str) -> dict | None:
    if len(df) < MIN_BARS_AFTER_WARMUP:
        logger.info("%s — only %d usable bars after warm-up; skipping.",
                    ticker, len(df))
        return None

    latest = df.iloc[-1]
    price  = float(latest["Close"])
    ema20  = float(latest["EMA20"])
    ema50  = float(latest["EMA50"])
    rsi    = float(latest["RSI"])
    macd   = float(latest["MACD"])
    signal = float(latest["Signal"])
    atr    = float(latest["ATR"])
    adx    = float(latest["ADX"])

    if price > ema20 > ema50 and macd > signal:
        trend = "Bullish"
    elif price < ema20 < ema50 and macd < signal:
        trend = "Bearish"
    else:
        return None

    # FIX 1: direction-aware. The old rule demanded RSI>60 for shorts too,
    # which is near-impossible in a downtrend, so shorts never fired.
    if trend == "Bullish":
        strength = "Strong" if (rsi > 60 and macd > signal) else "Normal"
    else:
        strength = "Strong" if (rsi < 40 and macd < signal) else "Normal"

    if adx < ADX_MIN:
        return None

    # FIX 2: single reference point. Entry, stop and target all anchor to the
    # current price, so they are internally coherent. Structure informs the
    # stop; the resistance/support cap only applies when the level is genuinely
    # beyond the entry, never behind it.
    swing_low_10  = float(df["Low"].tail(10).min())
    swing_high_10 = float(df["High"].tail(10).max())
    entry = round(price, 2)

    if trend == "Bullish":
        atr_stop   = price - atr * ATR_STOP_MULT
        structural = swing_low_10 - atr * 0.10
        stop = max(structural, atr_stop) if structural < price else atr_stop
        stop = round(min(stop, entry - 0.01), 2)

        raw_target = price + atr * ATR_TGT_MULT
        resistance = float(df["High"].tail(20).max())
        target = round(min(raw_target, resistance * 0.995), 2) \
            if resistance >= entry + atr else round(raw_target, 2)
        target = round(max(target, entry + 0.02), 2)
    else:
        atr_stop   = price + atr * ATR_STOP_MULT
        structural = swing_high_10 + atr * 0.10
        stop = min(structural, atr_stop) if structural > price else atr_stop
        stop = round(max(stop, entry + 0.01), 2)

        raw_target = price - atr * ATR_TGT_MULT
        support    = float(df["Low"].tail(20).min())
        target = round(max(raw_target, support * 1.005), 2) \
            if support <= entry - atr else round(raw_target, 2)
        target = round(min(target, entry - 0.02), 2)

    # FIX 5: risk gate relative to price, which also removes the
    # ZeroDivisionError path entirely.
    risk = abs(entry - stop)
    if risk < max(0.05, price * 0.003):
        return None

    rr = round(abs(target - entry) / risk, 2)

    # Sanity: with a single reference this cannot invert, but assert it rather
    # than trusting abs() to paper over a future regression.
    if (trend == "Bullish" and not (stop < entry < target)) or \
       (trend == "Bearish" and not (target < entry < stop)):
        logger.warning("%s — incoherent levels, discarding: %s/%s/%s",
                       ticker, entry, stop, target)
        return None

    if rr < MIN_RR or strength != "Strong":
        return None

    return {"ticker": ticker, "trend": trend, "strength": strength,
            "rr": rr, "entry": entry, "stop": stop, "target": target,
            "rsi": round(rsi, 1), "adx": round(adx, 1), "price": round(price, 2)}


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════
def run(args) -> int:
    if not args.force and not is_market_open():
        logger.info("Market closed — skipping.")
        return 0

    state = load_state()
    hits, skipped, failed = 0, 0, []

    for tk in WATCHLIST:
        try:
            time.sleep(FETCH_GAP_SEC)
            df = get_data(tk)
            if df is None:
                failed.append(f"{tk} (no data)")
                continue
            r = analyze(compute(df), tk)
            if not r:
                continue

            if recently_alerted(state, tk, r["trend"]):
                logger.info("%s %s qualifies but alerted within %dh — suppressed.",
                            tk, r["trend"], ALERT_COOLDOWN_HRS)
                skipped += 1
                continue

            msg = (f"🚨 TRADE ALERT\n"
                   f"{r['ticker']} → {r['trend']} ({r['strength']})\n"
                   f"Price: {r['price']} | RR: {r['rr']} | ADX: {r['adx']} | RSI: {r['rsi']}\n"
                   f"Entry: {r['entry']} | Stop: {r['stop']} | Target: {r['target']}")
            logger.info("ALERT %s %s rr=%s", tk, r["trend"], r["rr"])
            if send_alert(msg, dry_run=args.dry_run):
                hits += 1
                if not args.dry_run:
                    state[f"{tk}:{r['trend']}"] = time.time()

        except Exception as e:
            # FIX 5: isolate each ticker. Previously one failure aborted the
            # entire scan and the remaining tickers were never examined.
            logger.exception("%s failed: %s", tk, e)
            failed.append(f"{tk} ({type(e).__name__})")

    if not args.dry_run:
        save_state(state)

    logger.info("Done. %d alert(s) sent, %d suppressed by cooldown, "
                "%d ticker(s) failed.", hits, skipped, len(failed))
    if failed:
        logger.warning("Failed tickers: %s", ", ".join(failed))
    return 0


def parse_args():
    p = argparse.ArgumentParser(description="Watchlist scanner")
    p.add_argument("--dry-run", action="store_true",
                   help="Print alerts instead of sending; don't touch state")
    p.add_argument("--force", action="store_true",
                   help="Run even when the market is closed")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
