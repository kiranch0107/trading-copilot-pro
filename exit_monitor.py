"""
Trading Copilot ELITE — Option Exit Monitor
===========================================
Watches the OPTION positions in open_positions.json and sends a Telegram alert
when an exit rule fires. Runs STANDALONE — it does not import the Streamlit app
— so it works on a scheduler whether or not any browser tab is open.

WHY A SEPARATE SCRIPT
---------------------
A Streamlit app only executes while a browser session is active, and Streamlit
Cloud sleeps idle apps. A "check every 30 minutes" loop inside the app silently
stops the moment you close the tab — exactly when you'd be relying on it. So
the monitor lives here, driven by an external scheduler (GitHub Actions cron).

THE FOUR EXIT TRIGGERS
----------------------
Price stops on the underlying fit options badly: an option can lose 40% while
the stock barely moves, purely from theta and IV. These trigger on the things
that actually govern an option position, and are checked in this priority:

  1. STOP      — premium fell to −X% of what you paid (risk first)
  2. TARGET    — premium rose to +Y%
  3. TIME      — DTE at or below your floor; theta decay accelerates sharply
                 in the final weeks and this is the rule people most often skip
  4. THESIS    — the underlying invalidated the setup (closed the wrong side of
                 its EMA20), so the reason you bought the contract is gone

Each is optional per position. Set a rule to 0 / off to disable it.

AN IMPORTANT LIMITATION, STATED PLAINLY
---------------------------------------
Premium checks use the CURRENT quoted mid, not intraday bars, because reliable
per-contract intraday history isn't available from this data source. So a spike
that hit your target and reverted BETWEEN two checks can be missed. Shorter
intervals reduce but never eliminate this. The underlying-based THESIS check
does use daily closes and is not affected. Option quotes are also delayed and
can be wide or stale — treat the reported premium as indicative, not a fill.

Run
---
    pip install yfinance pandas pytz requests
    export TELEGRAM_BOT_TOKEN=...
    export TELEGRAM_CHAT_ID=...
    python exit_monitor.py

    python exit_monitor.py --dry-run --force   # safe test, sends nothing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import logging
from datetime import datetime, date

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("exit_monitor")

ET = pytz.timezone("America/New_York")

MARKET_HOLIDAYS = {
    "2025-01-01","2025-01-09","2025-01-20","2025-02-17","2025-04-18",
    "2025-05-26","2025-06-19","2025-07-04","2025-09-01","2025-11-27","2025-12-25",
    "2026-01-01","2026-01-19","2026-02-16","2026-04-03","2026-05-25",
    "2026-06-19","2026-07-03","2026-09-07","2026-11-26","2026-12-25",
}
MARKET_HALF_DAYS = {"2025-07-03","2025-11-28","2025-12-24","2026-11-27","2026-12-24"}


def is_market_open(now: datetime | None = None) -> bool:
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
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat_id, "text": message,
                                "parse_mode": "HTML"}, timeout=10)
        if r.status_code != 200:
            logger.error("Telegram API %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        logger.exception("Telegram send failed: %s", e)
        return False


# ══════════════════════════════════════════════════════════════════
# MARKET DATA
# ══════════════════════════════════════════════════════════════════
def get_option_quote(ticker: str, expiry: str, strike: float,
                     right: str) -> dict | None:
    """
    Current quote for one specific contract.

    Uses the bid/ask MID rather than lastPrice: lastPrice can be hours stale on
    a quiet contract and would produce phantom exits. If there's no two-sided
    market we fall back to lastPrice but flag it as unreliable.
    """
    try:
        chain = yf.Ticker(ticker).option_chain(expiry)
        df = chain.calls if right.upper() == "CALL" else chain.puts
        if df is None or df.empty:
            return None
        row = df[(df["strike"] - float(strike)).abs() < 0.01]
        if row.empty:
            logger.warning("%s %s %s %s — strike not found in chain",
                           ticker, expiry, strike, right)
            return None
        row = row.iloc[0]
        bid = float(row.get("bid") or 0)
        ask = float(row.get("ask") or 0)
        last = float(row.get("lastPrice") or 0)
        if bid > 0 and ask > 0:
            return {"mid": round((bid + ask) / 2, 2), "bid": bid, "ask": ask,
                    "reliable": True,
                    "spread_pct": round((ask - bid) / ((bid + ask) / 2) * 100, 1)}
        if last > 0:
            return {"mid": round(last, 2), "bid": bid, "ask": ask,
                    "reliable": False, "spread_pct": None}
        return None
    except Exception as e:
        logger.warning("get_option_quote(%s) failed: %s", ticker, e)
        return None


def get_underlying_state(ticker: str) -> dict | None:
    """Last daily close and EMA20, for the thesis-invalidation check."""
    try:
        df = yf.download(ticker, period="6mo", interval="1d",
                         progress=False, auto_adjust=False)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Close"])
        if len(df) < 25:
            return None
        # Matches the app's ta.trend.ema_indicator(window=20)
        ema20 = df["Close"].ewm(span=20, adjust=False).mean()
        return {"close": round(float(df["Close"].iloc[-1]), 2),
                "ema20": round(float(ema20.iloc[-1]), 2)}
    except Exception as e:
        logger.warning("get_underlying_state(%s) failed: %s", ticker, e)
        return None


def days_to_expiry(expiry: str) -> int | None:
    try:
        return (datetime.strptime(expiry, "%Y-%m-%d").date() - date.today()).days
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
# EXIT RULES
# ══════════════════════════════════════════════════════════════════
def check_option_position(pos: dict) -> dict:
    """
    Evaluate every enabled exit rule. Priority: STOP, TARGET, TIME, THESIS —
    risk before reward, and both before the softer signals.
    Returns {"exit": bool, ...}.
    """
    rules = pos.get("rules", {}) or {}
    entry_prem = float(pos["entry_premium"])
    dte = days_to_expiry(pos["expiry"])
    info = {"dte": dte}

    quote = get_option_quote(pos["ticker"], pos["expiry"],
                             pos["strike"], pos["right"])
    if quote:
        mid = quote["mid"]
        pnl_pct = ((mid - entry_prem) / entry_prem * 100) if entry_prem > 0 else 0
        info.update(mid=mid, pnl_pct=round(pnl_pct, 1),
                    reliable=quote["reliable"], spread_pct=quote.get("spread_pct"))
    else:
        mid = pnl_pct = None
        info.update(mid=None, pnl_pct=None, reliable=False)

    # 1. STOP — premium fell by more than the allowed %
    sl = rules.get("sl_pct") or 0
    if sl and pnl_pct is not None and pnl_pct <= -abs(sl):
        return {**info, "exit": True, "reason": "STOP",
                "detail": f"Premium {pnl_pct:+.1f}% (limit −{abs(sl):.0f}%)"}

    # 2. TARGET — premium rose past the take-profit
    tp = rules.get("tp_pct") or 0
    if tp and pnl_pct is not None and pnl_pct >= abs(tp):
        return {**info, "exit": True, "reason": "TARGET",
                "detail": f"Premium {pnl_pct:+.1f}% (target +{abs(tp):.0f}%)"}

    # 3. TIME — theta decay accelerates in the final weeks
    dte_floor = rules.get("dte_exit") or 0
    if dte_floor and dte is not None and dte <= dte_floor:
        return {**info, "exit": True, "reason": "TIME",
                "detail": f"{dte} DTE left (floor {dte_floor}) — theta decay "
                          f"accelerates from here regardless of P&L"}

    # 4. THESIS — underlying invalidated the setup
    if rules.get("invalidate_ema"):
        u = get_underlying_state(pos["ticker"])
        if u:
            info.update(underlying=u["close"], ema20=u["ema20"])
            broke = (u["close"] < u["ema20"]) if pos["right"].upper() == "CALL" \
                else (u["close"] > u["ema20"])
            if broke:
                side = "below" if pos["right"].upper() == "CALL" else "above"
                return {**info, "exit": True, "reason": "THESIS",
                        "detail": f"{pos['ticker']} closed {u['close']} — {side} "
                                  f"EMA20 {u['ema20']}. The setup that justified "
                                  f"this {pos['right']} is no longer valid."}

    return {**info, "exit": False}


def format_alert(pos: dict, ev: dict) -> str:
    icons = {"TARGET": "🎯", "STOP": "🛑", "TIME": "⏳", "THESIS": "📉"}
    icon = icons.get(ev["reason"], "⚠️")
    contract = (f"{pos['ticker']} {pos['expiry']} "
                f"${pos['strike']:g} {pos['right'].upper()}")
    lines = [f"{icon} <b>EXIT {ev['reason']}</b>", "", f"<b>{contract}</b>", ""]
    lines.append(f"Reason:  {ev['detail']}")
    if ev.get("mid") is not None:
        lines.append(f"Premium: {pos['entry_premium']:.2f} → {ev['mid']:.2f} "
                     f"({ev['pnl_pct']:+.1f}%)")
        if pos.get("contracts"):
            n = float(pos["contracts"])
            pnl_usd = (ev["mid"] - float(pos["entry_premium"])) * 100 * n
            lines.append(f"P&L:     ${pnl_usd:+,.0f} on {n:g} contract(s)")
    if ev.get("dte") is not None:
        lines.append(f"DTE:     {ev['dte']}")
    if ev.get("reliable") is False and ev.get("mid") is not None:
        lines += ["", "⚠️ No two-sided market — price is last-traded and may be "
                      "stale. Verify before acting."]
    elif ev.get("spread_pct") and ev["spread_pct"] > 15:
        lines += ["", f"⚠️ Wide spread ({ev['spread_pct']:.0f}% of mid) — the mid "
                      f"shown is optimistic versus a real fill."]
    lines += ["", f"Opened {pos.get('opened','')}",
              "", "Close it in the app to log it to your journal."]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════
def run(args) -> int:
    from pathlib import Path
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
        logger.info("Market closed — skipping. (--force to override.)")
        return 0

    logger.info("Checking %d open position(s)…", len(open_pos))
    now_epoch = time.time()
    sent = 0

    for pos in positions:
        if pos.get("status") != "OPEN":
            continue
        if not pos.get("right"):
            logger.info("%s — not an option position, skipping.",
                        pos.get("ticker", "?"))
            continue

        try:
            ev = check_option_position(pos)
        except Exception as e:
            logger.exception("Check failed for %s: %s", pos.get("ticker"), e)
            continue

        tag = f"{pos['ticker']} {pos['expiry']} {pos['strike']:g}{pos['right'][0]}"
        if not ev["exit"]:
            prem = f"{ev['mid']:.2f} ({ev['pnl_pct']:+.1f}%)" if ev.get("mid") is not None else "no quote"
            logger.info("%s open — premium %s, %s DTE", tag, prem, ev.get("dte"))
            if not args.dry_run:
                pos["last_check_epoch"] = now_epoch
            continue

        logger.info("%s EXIT %s — %s", tag, ev["reason"], ev["detail"])
        if pos.get("exit_alerted"):
            logger.info("  already alerted — not re-sending.")
            continue

        if send_telegram(format_alert(pos, ev), dry_run=args.dry_run):
            sent += 1
            if not args.dry_run:
                pos.update({
                    "exit_alerted": True, "status": "EXIT_SIGNALLED",
                    "exit_reason": ev["reason"], "exit_detail": ev["detail"],
                    "exit_premium": ev.get("mid"), "exit_pnl_pct": ev.get("pnl_pct"),
                    "exit_detected": datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"),
                    "last_check_epoch": now_epoch,
                })

    if not args.dry_run:
        try:
            path.write_text(json.dumps(positions, indent=2, default=str))
        except Exception as e:
            logger.error("Could not write %s: %s — alerts sent but state not "
                         "saved, so expect a duplicate next run.", path, e)

    logger.info("Done. %d exit alert(s) sent.", sent)
    return 0


def parse_args():
    p = argparse.ArgumentParser(description="Option exit monitor")
    p.add_argument("--positions", default="open_positions.json")
    p.add_argument("--interval", type=int, default=30,
                   help="Scheduler interval in minutes (informational)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(run(parse_args()))
