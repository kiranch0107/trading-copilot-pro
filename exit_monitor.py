"""
Trading Copilot ELITE — Exit Monitor
====================================
Watches the positions in open_positions.json and sends a Telegram alert when a
stop or target is hit. Runs STANDALONE — it does not import the Streamlit app —
so it works on a scheduler whether or not any browser tab is open.

WHY THIS IS A SEPARATE SCRIPT
-----------------------------
A Streamlit app only executes while a browser session is active, and Streamlit
Cloud sleeps idle apps. Any "check every 30 minutes" loop living inside the app
silently stops the moment you close the tab or your phone locks — which is
exactly when you'd be relying on it. An exit alert you might not receive is
worse than no alert, because you stop watching manually. So the monitor lives
here and is driven by an external scheduler (GitHub Actions cron, or plain cron).

WHY IT CHECKS BARS, NOT THE LAST PRICE
--------------------------------------
Polling the current price every 30 minutes would MISS a stop that was touched
and recovered inside the window — the single most important case to catch. The
monitor instead pulls intraday bars covering the whole interval since the last
check and tests the HIGH/LOW of that range. It also handles gap-through: if
price opened beyond your stop, the realistic fill is the open, not the stop.

Run
---
    pip install yfinance pandas pytz requests
    export TELEGRAM_BOT_TOKEN=...   # same bot as the app
    export TELEGRAM_CHAT_ID=...
    python exit_monitor.py

Options
-------
    python exit_monitor.py --interval 30      # minutes of history to scan
    python exit_monitor.py --dry-run          # print, don't send or mutate
    python exit_monitor.py --force            # ignore market-hours gate
    python exit_monitor.py --positions path/to/open_positions.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytz

try:
    import yfinance as yf
except ImportError:
    raise SystemExit("Missing yfinance. Run: pip install yfinance pandas pytz requests")
try:
    import requests
except ImportError:
    raise SystemExit("Missing requests. Run: pip install requests")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("exit_monitor")

ET = pytz.timezone("America/New_York")

# Mirrors the app's calendar so the monitor doesn't fire on closed days.
MARKET_HOLIDAYS = {
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27",
    "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}
MARKET_HALF_DAYS = {
    "2025-07-03", "2025-11-28", "2025-12-24",
    "2026-11-27", "2026-12-24",
}


def is_market_open(now: datetime | None = None) -> bool:
    now = now or datetime.now(ET)
    if now.weekday() >= 5:
        return False
    day = now.strftime("%Y-%m-%d")
    if day in MARKET_HOLIDAYS:
        return False
    close_h, close_m = (13, 0) if day in MARKET_HALF_DAYS else (16, 0)
    return (now.replace(hour=9, minute=30, second=0, microsecond=0)
            <= now <=
            now.replace(hour=close_h, minute=close_m, second=0, microsecond=0))


# ══════════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════════
def send_telegram(message: str, dry_run: bool = False) -> bool:
    token   = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if dry_run:
        print("\n--- TELEGRAM (dry-run, not sent) ---")
        print(message)
        print("--- end ---\n")
        return True
    if not token or not chat_id:
        logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — cannot alert. "
                     "This is the most common reason no message arrives.")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.error("Telegram API returned %s: %s", resp.status_code, resp.text[:200])
            return False
        return True
    except Exception as e:
        logger.exception("Telegram send failed: %s", e)
        return False


# ══════════════════════════════════════════════════════════════════
# EXIT DETECTION
# ══════════════════════════════════════════════════════════════════
def fetch_window(ticker: str, since_epoch: float,
                 interval_min: int) -> pd.DataFrame | None:
    """
    Intraday bars covering everything since the last check.

    We deliberately fetch a bit MORE than the interval (yfinance only offers
    fixed periods, and a scheduler run can be late), then slice by timestamp.
    Missing a bar is worse than re-checking one — a re-check is idempotent
    because we compare against fixed stop/target levels.
    """
    try:
        lookback_days = 1 if interval_min <= 240 else 5
        df = yf.download(ticker, period=f"{lookback_days}d", interval="5m",
                         progress=False, auto_adjust=False)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df = df.tz_convert(ET)
        cutoff = datetime.fromtimestamp(since_epoch, tz=ET)
        window = df[df.index >= cutoff]
        # If the slice is empty the scheduler may have run twice quickly —
        # fall back to the most recent bar so we still have a price to report.
        return window if not window.empty else df.tail(1)
    except Exception as e:
        logger.warning("fetch_window(%s) failed: %s", ticker, e)
        return None


def check_position(pos: dict, interval_min: int) -> dict | None:
    """
    Decide whether this position hit its stop or target in the window.

    Returns an exit event dict, or None if still open.

    Conservative rules, matching backtest.py:
      • If BOTH stop and target were touched in the same window, assume the
        STOP filled first. We cannot know the intra-bar sequence, and assuming
        the good outcome would flatter every ambiguous case.
      • Gap-through: if a bar OPENED beyond the stop, the realistic fill is
        that open, not the stop level. Reporting the stop would understate
        the loss — the exact thing the app's gap-risk caption warns about.
    """
    df = fetch_window(pos["ticker"], pos.get("last_check_epoch", pos["opened_epoch"]),
                      interval_min)
    if df is None or df.empty:
        return None

    hi = float(df["High"].max())
    lo = float(df["Low"].min())
    last = float(df["Close"].iloc[-1])
    stop, target = float(pos["stop"]), float(pos["target"])
    long_side = pos["trend"] == "Bullish"

    stop_hit   = (lo <= stop) if long_side else (hi >= stop)
    target_hit = (hi >= target) if long_side else (lo <= target)

    if not stop_hit and not target_hit:
        return {"still_open": True, "last": last, "high": hi, "low": lo}

    # Stop takes precedence on ambiguity (conservative).
    if stop_hit:
        # Gap-through detection: find the first bar that breached the stop and
        # check whether it OPENED already beyond it.
        breach = df[df["Low"] <= stop] if long_side else df[df["High"] >= stop]
        exit_px, gapped = stop, False
        if not breach.empty:
            first_open = float(breach["Open"].iloc[0])
            if (long_side and first_open < stop) or ((not long_side) and first_open > stop):
                exit_px, gapped = first_open, True
        reason = "STOP"
    else:
        breach = df[df["High"] >= target] if long_side else df[df["Low"] <= target]
        exit_px, gapped = target, False
        if not breach.empty:
            first_open = float(breach["Open"].iloc[0])
            if (long_side and first_open > target) or ((not long_side) and first_open < target):
                exit_px, gapped = first_open, True
        reason = "TARGET"

    risk = abs(pos["entry"] - stop)
    r_mult = ((exit_px - pos["entry"]) / risk) if long_side else ((pos["entry"] - exit_px) / risk)

    return {
        "still_open": False,
        "reason":     reason,
        "exit_price": round(exit_px, 2),
        "gapped":     gapped,
        "r_multiple": round(r_mult, 2),
        "last":       last,
        "high":       hi,
        "low":        lo,
    }


def format_alert(pos: dict, ev: dict) -> str:
    icon = "🎯" if ev["reason"] == "TARGET" else "🛑"
    side = "LONG" if pos["trend"] == "Bullish" else "SHORT"
    lines = [
        f"{icon} <b>EXIT {ev['reason']} — {pos['ticker']}</b> ({side})",
        "",
        f"Entry:  {pos['entry']}",
        f"{'Target' if ev['reason']=='TARGET' else 'Stop'}: "
        f"{pos['target'] if ev['reason']=='TARGET' else pos['stop']}",
        f"Exit:   {ev['exit_price']}",
        f"Result: {ev['r_multiple']:+.2f}R",
    ]
    if pos.get("qty"):
        lines.append(f"Size:   {pos['qty']} {pos.get('instrument','shares')}")
    if ev["gapped"]:
        lines += ["", ("⚠️ GAPPED THROUGH the level — the fill shown is the bar "
                       "open, which is worse than the level. Your actual fill may "
                       "differ again.")]
    lines += ["", f"Opened {pos['opened']}",
              "", "Close the position in the app to log it to your journal."]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════
def run(args) -> int:
    path = Path(args.positions)
    if not path.exists():
        logger.info("No positions file at %s — nothing to monitor.", path)
        return 0

    try:
        positions = json.loads(path.read_text())
    except Exception as e:
        logger.error("Could not read %s: %s", path, e)
        return 1

    open_pos = [p for p in positions if p.get("status") == "OPEN"]
    if not open_pos:
        logger.info("No OPEN positions — nothing to check.")
        return 0

    if not args.force and not is_market_open():
        logger.info("Market closed — skipping. (Use --force to override.)")
        return 0

    logger.info("Checking %d open position(s)…", len(open_pos))
    now_epoch = time.time()
    exits = 0

    for pos in positions:
        if pos.get("status") != "OPEN":
            continue
        ev = check_position(pos, args.interval)
        if ev is None:
            logger.warning("%s — no data this run, leaving untouched.", pos["ticker"])
            continue

        if ev["still_open"]:
            logger.info("%s open — last %.2f (window %.2f–%.2f), stop %.2f target %.2f",
                        pos["ticker"], ev["last"], ev["low"], ev["high"],
                        pos["stop"], pos["target"])
            if not args.dry_run:
                pos["last_check_epoch"] = now_epoch
            continue

        # Exit detected
        logger.info("%s %s HIT at %.2f (%+.2fR)%s", pos["ticker"], ev["reason"],
                    ev["exit_price"], ev["r_multiple"],
                    " [GAPPED]" if ev["gapped"] else "")
        if pos.get("exit_alerted"):
            logger.info("  already alerted — not re-sending.")
            continue

        if send_telegram(format_alert(pos, ev), dry_run=args.dry_run):
            exits += 1
            if not args.dry_run:
                pos["exit_alerted"]   = True
                pos["status"]         = "EXIT_SIGNALLED"
                pos["exit_reason"]    = ev["reason"]
                pos["exit_price"]     = ev["exit_price"]
                pos["exit_r"]         = ev["r_multiple"]
                pos["exit_gapped"]    = ev["gapped"]
                pos["exit_detected"]  = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
                pos["last_check_epoch"] = now_epoch

    if not args.dry_run:
        try:
            path.write_text(json.dumps(positions, indent=2, default=str))
        except Exception as e:
            logger.error("Could not write %s: %s — alerts sent but state not saved, "
                         "so you may get a duplicate next run.", path, e)

    logger.info("Done. %d exit alert(s) sent.", exits)
    return 0


def parse_args():
    p = argparse.ArgumentParser(description="Exit monitor for open positions")
    p.add_argument("--positions", default="open_positions.json")
    p.add_argument("--interval", type=int, default=30,
                   help="Minutes of history to scan (match your cron interval)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print alerts instead of sending; do not mutate state")
    p.add_argument("--force", action="store_true",
                   help="Run even when the market is closed")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(run(parse_args()))
