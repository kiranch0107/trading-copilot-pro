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
from datetime import datetime, date, timedelta

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

# NOTE: there is deliberately no MAX_HOLD_BARS_DEFAULT constant here. The
# limit is written into each position's rules block by app.py when the
# position is opened, so a module-level default would never be consulted —
# and a constant that looks like a setting but changes nothing is worse than
# no constant at all. Positions opened before this rule existed have no
# max_hold_bars and are simply not subject to it.


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


def trading_sessions_between(start: date, end: date) -> int:
    """
    Count completed trading sessions from `start` (exclusive) to `end`
    (inclusive), skipping weekends and known market holidays.

    A bar-count time stop only means anything in sessions. Calendar days
    would make a Friday entry "3 bars old" by Monday morning, which would
    fire the stop early on every weekend the position is held.

    MARKET_HOLIDAYS only covers 2025-2026; beyond that the count drifts
    slightly long (holidays get counted as sessions), which is the safe
    direction — it holds a position marginally longer rather than exiting
    on a day the market was shut.
    """
    if end <= start:
        return 0
    sessions = 0
    cur = start
    one = timedelta(days=1)
    while cur < end:
        cur += one
        if cur.weekday() >= 5:
            continue
        if cur.strftime("%Y-%m-%d") in MARKET_HOLIDAYS:
            continue
        sessions += 1
    return sessions


def bars_held(pos: dict) -> int | None:
    """
    Sessions elapsed since the position was opened.

    Prefers `opened_epoch` (unambiguous). Falls back to parsing the
    `opened` string, which app.py writes as "%Y-%m-%d %H:%M ET" — note the
    literal "ET" is not a parseable timezone, so only the date part is used.
    Returns None if neither field is usable, which disables the rule rather
    than guessing.
    """
    opened_date = None
    epoch = pos.get("opened_epoch")
    if epoch:
        try:
            opened_date = datetime.fromtimestamp(float(epoch), ET).date()
        except Exception:
            opened_date = None
    if opened_date is None:
        raw = (pos.get("opened") or "").replace(" ET", "").strip()
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                opened_date = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                continue
    if opened_date is None:
        return None
    return trading_sessions_between(opened_date, datetime.now(ET).date())


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
    Evaluate every enabled exit rule. Priority: STOP, TARGET, TIME, HOLD,
    THESIS — risk before reward, and both before the softer signals.

    HOLD sits after TIME because the theta cliff is the more urgent of the
    two clocks: a contract at 5 DTE needs out today regardless of how many
    sessions it has been held.
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

    # 4. HOLD — position has consumed its allotted sessions
    #
    # The premise of a swing setup is that it resolves within a bounded number
    # of bars. Past that, the thesis has not been proven wrong so much as it
    # has failed to be proven right, and the capital is better freed than left
    # bleeding theta on a trade that is going nowhere.
    max_bars = rules.get("max_hold_bars") or 0
    held = bars_held(pos) if max_bars else None
    if held is not None:
        info["bars_held"] = held
    if max_bars and held is not None and held >= max_bars:
        return {**info, "exit": True, "reason": "HOLD",
                "detail": f"{held} sessions held (limit {max_bars:g}) — the setup "
                          f"has had its window and has not resolved."}

    # 5. THESIS — underlying invalidated the setup
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
    icons = {"TARGET": "🎯", "STOP": "🛑", "TIME": "⏳", "HOLD": "📆",
             "THESIS": "📉"}
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
    if ev.get("bars_held") is not None:
        lines.append(f"Held:    {ev['bars_held']} sessions")
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
# DIAGNOSTICS
#
# Silent failure has been the recurring problem with this system, so this mode
# reports exactly what the monitor can and cannot see, rather than leaving you
# to infer it from a missing notification.
# ══════════════════════════════════════════════════════════════════
def diagnose(positions: list) -> None:
    print("=" * 72)
    print("EXIT MONITOR DIAGNOSTIC")
    print("=" * 72)

    # 1. Credentials
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = os.environ.get("TELEGRAM_CHAT_ID")
    print("\n[1] TELEGRAM CREDENTIALS")
    print(f"    TELEGRAM_BOT_TOKEN : {'set (' + tok[:8] + '…)' if tok else '*** MISSING ***'}")
    print(f"    TELEGRAM_CHAT_ID   : {cid if cid else '*** MISSING ***'}")
    if not tok or not cid:
        print("    -> No alert can EVER be sent without both.")
        print("       In GitHub Actions these are repo secrets and are SEPARATE")
        print("       from your Streamlit secrets — setting one does not set the other.")

    # 2. Market status
    now = datetime.now(ET)
    print("\n[2] MARKET STATUS")
    print(f"    Now (ET)     : {now.strftime('%Y-%m-%d %H:%M')} ({now.strftime('%A')})")
    print(f"    Market open  : {is_market_open()}")
    if not is_market_open():
        print("    -> A normal scheduled run would SKIP here. Option bid/ask are")
        print("       also frequently 0 outside market hours, which makes premium")
        print("       checks impossible. Use --force to test anyway.")

    # 3. Positions
    print(f"\n[3] POSITIONS FILE — {len(positions)} record(s)")
    open_pos = [p for p in positions if p.get("status") == "OPEN"]
    print(f"    OPEN            : {len(open_pos)}")
    print(f"    EXIT_SIGNALLED  : {sum(1 for p in positions if p.get('status')=='EXIT_SIGNALLED')}")
    if not open_pos:
        print("    -> Nothing to check. If you logged a position in the app but it")
        print("       is not here, the app is not syncing to this repo file.")
        return

    # 4. Per-position deep check
    for p in open_pos:
        print("\n" + "-" * 72)
        print(f"[4] {p['ticker']} {p['expiry']} ${p['strike']:g} {p['right']}")
        print(f"    Paid           : ${p['entry_premium']:.2f} x {p.get('contracts')} contract(s)")
        rules = p.get("rules", {}) or {}
        print(f"    Rules          : TP +{rules.get('tp_pct')}%  SL -{rules.get('sl_pct')}%  "
              f"DTE<={rules.get('dte_exit')}  thesis={rules.get('invalidate_ema')}")
        if not any([rules.get("tp_pct"), rules.get("sl_pct"),
                    rules.get("dte_exit"), rules.get("invalidate_ema")]):
            print("    -> ALL RULES DISABLED. This position can never alert.")
            continue

        dte = days_to_expiry(p["expiry"])
        print(f"    DTE            : {dte}")

        # Does the expiry exist in the chain at all?
        try:
            avail = list(yf.Ticker(p["ticker"]).options or [])
        except Exception as e:
            avail = []
            print(f"    !! Could not list expiries: {e}")
        if avail:
            match = p["expiry"] in avail
            print(f"    Expiry in chain: {match}")
            if not match:
                print(f"    -> '{p['expiry']}' is NOT a listed expiry. Nearest are:")
                for a in avail[:6]:
                    print(f"         {a}")
                print("       The strike lookup cannot run, so premium rules are")
                print("       skipped entirely and you get no alert.")
                continue

        # Strike present?
        try:
            chain = yf.Ticker(p["ticker"]).option_chain(p["expiry"])
            df = chain.calls if p["right"].upper() == "CALL" else chain.puts
            row = df[(df["strike"] - float(p["strike"])).abs() < 0.01]
            if row.empty:
                near = df.iloc[(df["strike"] - float(p["strike"])).abs().argsort()[:5]]
                print(f"    Strike found   : NO")
                print(f"    -> ${p['strike']:g} not in the {p['right']} chain. Nearest strikes:")
                print(f"       {', '.join(str(s) for s in near['strike'].tolist())}")
                print("       Premium rules are skipped. Check the strike you entered.")
                continue
            r0 = row.iloc[0]
            bid, ask, last = (float(r0.get("bid") or 0), float(r0.get("ask") or 0),
                              float(r0.get("lastPrice") or 0))
            print(f"    Strike found   : YES")
            print(f"    bid/ask/last   : {bid:.2f} / {ask:.2f} / {last:.2f}")
            if bid > 0 and ask > 0:
                mid = (bid + ask) / 2
                src_ = "bid/ask mid (reliable)"
            elif last > 0:
                mid = last
                src_ = "lastPrice (NO two-sided market — may be stale)"
            else:
                print("    -> No usable price at all (bid, ask and last are 0).")
                print("       This is common when the market is closed or the")
                print("       contract is illiquid. PREMIUM RULES ARE SKIPPED —")
                print("       this is the most likely reason you got no alert.")
                continue
            pnl = (mid - float(p["entry_premium"])) / float(p["entry_premium"]) * 100
            print(f"    Price used     : {mid:.2f}  [{src_}]")
            print(f"    P&L            : {pnl:+.1f}%")

            fired = []
            if rules.get("sl_pct") and pnl <= -abs(rules["sl_pct"]): fired.append("STOP")
            if rules.get("tp_pct") and pnl >= abs(rules["tp_pct"]):  fired.append("TARGET")
            if rules.get("dte_exit") and dte is not None and dte <= rules["dte_exit"]:
                fired.append("TIME")
            print(f"    Would fire     : {', '.join(fired) if fired else 'nothing — all rules within limits'}")
            if fired and p.get("exit_alerted"):
                print("    -> Rule met BUT exit_alerted is already True, so no repeat")
                print("       alert is sent. Close the position in the app.")
        except Exception as e:
            print(f"    !! Chain fetch failed: {e}")

    print("\n" + "=" * 72)


def test_telegram() -> int:
    print("Sending a test message…")
    ok = send_telegram("✅ <b>Exit monitor test</b>\n\nIf you can read this, "
                       "credentials and delivery are working.")
    print("Sent." if ok else "FAILED — see the error above.")
    return 0 if ok else 1


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

    if args.diagnose:
        diagnose(positions)
        return 0

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
    unpriced: list[str] = []

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
            if ev.get("mid") is None:
                # NOT harmless: with no price, the TP and SL rules are skipped
                # entirely this run. Say so plainly rather than reporting the
                # position as quietly fine.
                logger.warning("%s — NO QUOTE available. Premium rules (TP/SL) "
                               "were SKIPPED this run; only TIME/THESIS could "
                               "have fired. Run --diagnose to see why.", tag)
                unpriced.append(tag)
            else:
                logger.info("%s open — premium %.2f (%+.1f%%), %s DTE",
                            tag, ev["mid"], ev["pnl_pct"], ev.get("dte"))
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

    if unpriced:
        logger.warning("%d position(s) had NO PRICE this run: %s — their TP/SL "
                       "rules did not run.", len(unpriced), ", ".join(unpriced))
    logger.info("Done. %d exit alert(s) sent.", sent)
    return 0


def parse_args():
    p = argparse.ArgumentParser(description="Option exit monitor")
    p.add_argument("--positions", default="open_positions.json")
    p.add_argument("--interval", type=int, default=30,
                   help="Scheduler interval in minutes (informational)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--diagnose", action="store_true",
                   help="Report exactly what the monitor can see and why an "
                        "alert did or did not fire. Sends nothing.")
    p.add_argument("--test-telegram", action="store_true",
                   help="Send one test message to verify credentials.")
    return p.parse_args()


if __name__ == "__main__":
    _args = parse_args()
    if _args.test_telegram:
        sys.exit(test_telegram())
    sys.exit(run(_args))
