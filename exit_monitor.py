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

THE EXIT TRIGGERS
-----------------
Price stops on the underlying fit options badly: an option can lose 40% while
the stock barely moves, purely from theta and IV. These trigger on the things
that actually govern an option position, and are checked in this priority:

  1. STOP      — premium fell to −X% of what you paid (risk first)
  1b. TARGET_U — the underlying CLOSED at or beyond the alert's target (part
                 of the measured structure since 2026-11-04, owner: "go with
                 the target exit"; rules underlying_target)
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
import importlib.util
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
# requests is no longer imported here — notify.py owns the Telegram call — but
# it IS still a hard dependency through notify and data_source. The check is
# kept so a missing package fails immediately with an actionable message
# instead of surfacing as an obscure ImportError two modules down. find_spec
# rather than an import, so there is no unused name for the linter to flag.
if importlib.util.find_spec("requests") is None:
    raise SystemExit("Missing requests. Run: pip install requests")

import data_source
import signal_core as sc
import risk_params
import notify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("exit_monitor")

ET = pytz.timezone("America/New_York")

MARKET_HOLIDAYS = {
    "2025-01-01","2025-01-09","2025-01-20","2025-02-17","2025-04-18",
    "2025-05-26","2025-06-19","2025-07-04","2025-09-01","2025-11-27","2025-12-25",
    "2026-01-01","2026-01-19","2026-02-16","2026-04-03","2026-05-25",
    "2026-06-19","2026-07-03","2026-09-07","2026-11-26","2026-12-25",
    "2027-01-01","2027-01-18","2027-02-15","2027-03-26","2027-05-31",
    "2027-06-18","2027-07-05","2027-09-06","2027-11-25","2027-12-24",
}
MARKET_HALF_DAYS = {"2025-07-03","2025-11-28","2025-12-24","2026-11-27","2026-12-24",
    "2027-11-26",}

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


# THE CLOSING WINDOW (review 2026-11-03 D6; owner: "go with D6"). The
# measured thesis exit (option_backtest) leaves at the CLOSE that breaks
# EMA20. A settled close exists only after 16:00 and no run happens after
# 16:00, so THESIS used to fire on the next session's first run, carrying
# the overnight gap. Inside this many minutes before the close (13:00 on a
# half day) THESIS reads TODAY'S forming bar, the live price, as a stand-in
# for the close, against an EMA20 that includes it. The 15:30 dispatch is in
# it (no schedule change is needed) and leaves a person time to act; a run
# earlier in the day still reads only settled closes, so an intraday dip
# that recovers cannot fire it (the bug drop_unsettled_bars exists to stop).
CLOSING_WINDOW_MIN = 35


def in_closing_window(now: datetime | None = None) -> bool:
    """True from CLOSING_WINDOW_MIN minutes before today's close to the
    close, on a trading day (D6)."""
    now = now or datetime.now(ET)
    if not is_market_open(now):
        return False
    day = now.strftime("%Y-%m-%d")
    ch, cm = (13, 0) if day in MARKET_HALF_DAYS else (16, 0)
    close = now.replace(hour=ch, minute=cm, second=0, microsecond=0)
    return close - timedelta(minutes=CLOSING_WINDOW_MIN) <= now <= close


def drop_unsettled_bars(df: "pd.DataFrame",
                        now: datetime | None = None) -> "pd.DataFrame":
    """
    Remove any bar dated today while the market is still open.

    Separate from is_market_open() so it can be tested without a clock or a
    network fetch — the THESIS rule had no test at all, and the bug it carried
    was invisible precisely because nothing could assert on it.
    """
    # ROUTED THROUGH signal_core.drop_unsettled(). DROP_UNTIL_CLOSE is the live
    # policy: today's bar is unusable before the open (a stub) and during the
    # session (in progress), and is the freshest real data once the close has
    # passed. (This said the 20:00/21:00 UTC runs see the session that just
    # ended; they do not: run() skips every run after 16:00:00 ET, so THESIS is
    # read on the NEXT session's first run, one session after the close that
    # broke EMA20, where option_backtest exits at that close. Decided
    # 2026-11-03 (D6): in the closing window THESIS reads the live price
    # instead; see CLOSING_WINDOW_MIN above.)
    #
    # The local version asked `not is_market_open(now)` and returned early, so it
    # kept the stub bar BEFORE the open. signal_core asks whether today has
    # CLOSED instead, which is the question that distinguishes the two.
    if df is None or len(df) == 0:
        return df
    out, _n = sc.drop_unsettled(df, policy=sc.DROP_UNTIL_CLOSE,
                               now=now or datetime.now(ET))
    return out


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
    # notify.send() retries transient failures (timeouts, 429, 5xx). This used
    # to be a bare requests.post with no retry, so one blip between the Actions
    # runner and Telegram silently dropped an EXIT alert — the single most
    # consequential message this system sends.
    return notify.send(message, parse_mode="HTML")


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
        # A CROSSED quote (ask under bid) is bad data, not a market (review
        # 2026-11-03, N7): bid 0.60 / ask 0.20 read as a reliable mid of 0.40
        # and could fire a STOP on a 1.00 entry. spread_log refuses it too.
        if bid > 0 and ask > 0 and ask >= bid:
            return {"mid": round((bid + ask) / 2, 2), "bid": bid, "ask": ask,
                    "reliable": True,
                    "spread_pct": round((ask - bid) / ((bid + ask) / 2) * 100, 1)}
        if last > 0:
            return {"mid": round(last, 2), "bid": bid, "ask": ask,
                    "reliable": False, "spread_pct": None}
        if (bid > 0) != (ask > 0):
            # ONE SIDE AND NO LAST TRADE: still a bound (N7). The price rules
            # below can read a STOP proven by the ask or a TARGET proven by
            # the bid; nothing else is inferred from it.
            return {"mid": None, "bid": bid, "ask": ask,
                    "reliable": False, "spread_pct": None}
        return None
    except Exception as e:
        logger.warning("get_option_quote(%s) failed: %s", ticker, e)
        return None


def get_underlying_state(ticker: str,
                         now: datetime | None = None,
                         live_close: bool = False) -> dict | None:
    """
    Last daily close and EMA20, for the thesis-invalidation check.

    Yahoo first, then data_source's fallback (Tiingo, if TIINGO_API_KEY is
    set) when Yahoo comes back empty — this is the THESIS rule's only price
    input, and before this it had no fallback at all: a Yahoo outage silently
    disabled the one exit rule that doesn't depend on option quotes.
    """
    try:
        df, source = data_source.fetch_daily(
            ticker, period="6mo", interval="1d",
            yahoo_fetch=lambda t, p, i: yf.download(
                t, period=p, interval=i, progress=False, auto_adjust=False))
        if df is None or df.empty:
            return None
        if source != "yahoo":
            logger.info("%s: Yahoo unavailable, used %s fallback", ticker, source)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Close"])
        # NEVER EVALUATE "CLOSED BELOW EMA20" ON A BAR THAT HAS NOT CLOSED.
        #
        # This workflow runs hourly THROUGH the session
        # (cron "0 13,14,...,21 * * 1-5"), which is the point of an exit
        # monitor. While the market is open, Yahoo's last daily row is today's
        # in-progress bar: its Close is the live price. Taking iloc[-1] meant the
        # THESIS rule compared an INTRADAY print against an EMA20 that also
        # included that partial bar — and then the alert said the ticker
        # "closed" there.
        #
        # The effect is a premature exit on a real position: an intraday dip
        # through EMA20 that recovers by the close fired THESIS anyway. This is
        # the same partial-bar defect already fixed in signal_core
        # (drop_partial_bar), app.py, scanner.py, backtest (_drop_todays_bar) and
        # universe.py (drop_unsettled) — and the only place it closes a position
        # instead of mis-reporting a number.
        #
        # Dropped only while the market is OPEN: after the close today's bar is
        # settled and is the one the rule should use. (run() skips after 16:00 ET,
        # so in practice THESIS reads the close on the next session's first run;
        # see drop_unsettled_bars and the review of 2026-11-03.)
        _now = now or datetime.now(ET)
        if live_close:
            # D6: today's forming bar stands in for the close (asked only in the
            # closing window); a bar dated after today is still never data.
            df = drop_unsettled_bars(df, now=_now.replace(hour=23, minute=59))
        else:
            df = drop_unsettled_bars(df, now=now)
        if len(df) < 25:
            return None
        # Matches the app's ta.trend.ema_indicator(window=20)
        ema20 = df["Close"].ewm(span=20, adjust=False).mean()
        _as_of = str(pd.to_datetime(df.index[-1]).date())
        return {"close": round(float(df["Close"].iloc[-1]), 2),
                "ema20": round(float(ema20.iloc[-1]), 2),
                "as_of": _as_of,
                "live": bool(live_close and _as_of == _now.strftime("%Y-%m-%d"))}
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

    MARKET_HOLIDAYS covers 2025-2027; beyond its last entry the count drifts
    slightly long (holidays get counted as sessions), which is the safe
    direction — it holds a position marginally longer rather than exiting
    on a day the market was shut. consistency_check.check_calendar_runway()
    is the forcing function that keeps the calendar ahead of today.
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
    """
    DTE measured against the ET trading date.

    date.today() reads the HOST clock, and GitHub Actions runners are UTC.
    Any run after 20:00 ET is already tomorrow in UTC, so DTE came back one
    day short — and the TIME rule fires on `dte <= floor`, so a position at
    exactly the floor could exit a session early. Every other date in this
    module is ET; this one was the exception.
    """
    try:
        return (datetime.strptime(expiry, "%Y-%m-%d").date()
                - datetime.now(ET).date()).days
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
# EXIT RULES
# ══════════════════════════════════════════════════════════════════
# WHICH EXIT REASONS OUTRANK WHICH — and why a position keeps being watched
# after its first alert.
#
# THE GAP THIS CLOSES. run() used to select `status == "OPEN"` and skip
# everything else, so the FIRST rule to fire flipped a position to
# EXIT_SIGNALLED and it left monitoring permanently. Nothing in the app could
# put it back: `status: OPEN` is assigned in exactly one place
# (journal_store.open_option_position), and the app's "close these" list has no
# dismiss and no re-arm.
#
# That is ordinary, not exotic. THESIS fires on a single close through EMA20 and
# HOLD on a session count; both are judgement calls the owner may reasonably
# decline. The moment either fired, the STOP rule was dead for the life of that
# position — on a long option where the premium is the entire maximum loss. Same
# shape as the 92-hour gap that widened this workflow's cron, except unbounded.
#
# So an already-signalled position is still checked, and re-alerts ONLY on a
# reason that outranks the one already sent. The ranking is the rules' own
# evaluation order in check_option_position() — risk before reward, both before
# the softer signals — so "outranks" means "more urgent", not "newer".
#
#   STOP after THESIS   -> alerts. This is the case the gap was losing.
#   STOP after TARGET   -> alerts. You did not take the profit and it turned.
#   THESIS after STOP   -> silent. You have already been told the worse thing.
#   THESIS after THESIS -> silent. No new information, and a repeat every hour
#                          is what the exit_alerted flag was added to stop.
#   TARGET_U ranks between STOP and the premium TARGET, the order the rules
#   are evaluated in (and option_backtest's arm B): the alert's own target
#   reached outranks the premium level and the softer signals.
EXIT_PRIORITY = {"STOP": 0, "TARGET_U": 1, "TARGET": 2, "TIME": 3, "HOLD": 4, "THESIS": 5}

# Statuses run() still evaluates. EXIT_SIGNALLED is in here deliberately; see
# EXIT_PRIORITY above.
MONITORED_STATUSES = ("OPEN", "EXIT_SIGNALLED")


def outranks(new_reason: str, previous_reason: str | None) -> bool:
    """
    True when `new_reason` is urgent enough to alert again.

    An unknown or missing previous reason is treated as the LOWEST rank, so a
    position carrying a status this module does not recognise still gets its
    stop. Failing the other way would re-create the silence this exists to end.
    """
    if not previous_reason:
        return True
    lo = len(EXIT_PRIORITY)
    return EXIT_PRIORITY.get(new_reason, lo) < EXIT_PRIORITY.get(previous_reason, lo)


def check_option_position(pos: dict) -> dict:
    """
    Evaluate every enabled exit rule. Priority: STOP, TARGET_U, TARGET,
    TIME, HOLD, THESIS — risk before reward, and both before the softer
    signals. The underlying is fetched at most once, for TARGET_U and THESIS.

    HOLD sits after TIME because the theta cliff is the more urgent of the
    two clocks: a contract at 5 DTE needs out today regardless of how many
    sessions it has been held.
    Returns {"exit": bool, ...}.
    """
    rules = pos.get("rules", {}) or {}
    entry_prem = float(pos["entry_premium"])
    dte = days_to_expiry(pos["expiry"])
    info = {"dte": dte}
    _ucache: dict = {}

    def _under():
        # One read of the underlying per position per run, shared by TARGET_U
        # and THESIS; in the closing window it is the live price (D6).
        if "u" not in _ucache:
            _ucache["u"] = get_underlying_state(pos["ticker"], live_close=in_closing_window())
        return _ucache["u"]

    quote = get_option_quote(pos["ticker"], pos["expiry"],
                             pos["strike"], pos["right"])
    if quote and quote.get("mid") is None:
        # One side only, no last trade (N7): no mid to report, but the bound
        # below may still prove a level.
        mid = pnl_pct = None
        info.update(mid=None, pnl_pct=None, reliable=False)
    elif quote:
        mid = quote["mid"]
        # THE RULES READ THE BID (review 2026-11-03 D5; owner: "go with D5").
        # A long sells at the bid. The rules read the mid, and the backtest
        # marked every exit half its modelled spread BELOW the mid: at bid
        # 0.30 / ask 0.80 on a 1.00 entry the mid said -45 % (no STOP) where
        # a sale fetched -70 %, and 1.80 / 4.40 fired TARGET at "+210 %" on a
        # bid of +80 %. On a two-sided quote the price the rules read (the
        # mark) is the bid: the backtest's own convention with the market's
        # spread in place of the modelled 5 %. A one-sided or last-only quote
        # is unchanged (below): a zero bid is read as no quote, not as zero.
        mark = float(quote["bid"]) if quote.get("reliable") and quote.get("bid") else mid
        # A non-positive entry premium makes every percentage rule meaningless.
        # This used to set pnl_pct = 0, which is NOT missing data — it is a
        # number that satisfies `is not None` and then fails both comparisons,
        # so STOP and TARGET silently never fired for the rest of the position's
        # life. Treated as absent, like a missing quote.
        if entry_prem > 0:
            pnl_pct = (mark - entry_prem) / entry_prem * 100
        else:
            pnl_pct = None
            logger.warning("%s: entry_premium is %s — price rules cannot be "
                           "evaluated", pos.get("ticker"), entry_prem)
        info.update(mid=mid, mark=mark, bid=quote.get("bid"), ask=quote.get("ask"),
                    pnl_pct=round(pnl_pct, 1) if pnl_pct is not None else None,
                    reliable=quote["reliable"], spread_pct=quote.get("spread_pct"))
    else:
        mid = pnl_pct = None
        info.update(mid=None, pnl_pct=None, reliable=False)

    # PRICE RULES REQUIRE A PRICE WORTH ACTING ON.
    #
    # get_option_quote() falls back to lastPrice when there is no two-sided
    # market and flags reliable=False, and its own docstring says why: lastPrice
    # "can be hours stale on a quiet contract and would produce phantom exits".
    # That flag was put into `info` and then never read — STOP and TARGET fired
    # on the stale number anyway. The module identified the hazard, labelled it,
    # and acted on it regardless.
    #
    # So price rules are skipped when the quote is unreliable, and the position
    # is SURFACED rather than passed over: this is an alerting tool (the workflow
    # says so), and "I could not price this" is information the human needs. The
    # clock and thesis rules below do not depend on the quote and still run.
    price_ok = pnl_pct is not None and info.get("reliable") is True
    if pnl_pct is not None and not info.get("reliable"):
        info["price_rules_skipped"] = (
            f"quote unreliable (no two-sided market; lastPrice {mid}) — STOP and "
            f"TARGET not evaluated. Check this contract by hand.")

    # 1. STOP — premium fell by more than the allowed %
    sl = rules.get("sl_pct") or 0
    if sl and price_ok and pnl_pct <= -abs(sl):
        return {**info, "exit": True, "reason": "STOP",
                "detail": f"Premium {pnl_pct:+.1f}% at the bid (limit −{abs(sl):.0f}%)"}

    # A ONE-SIDED QUOTE THAT PROVES A LEVEL (review 2026-11-03, N7). The rule
    # reads the mid. With no bid, the mid lies between ask/2 and the ask, so
    # an ASK at or under the stop level proves the mid is too: bid 0 / ask
    # 0.10 on a 1.00 entry is at best −90 %, and it used to be skipped as
    # "unreliable" in exactly the deepest-loss state (and blocked BACKLOG
    # 19's STOP-after-THESIS escalation there). With no ask, a BID at or
    # over the target level proves the mid is above it. The levels are the
    # rules' own; nothing is inferred beyond the bound.
    _bid = float((quote or {}).get("bid") or 0)
    _ask = float((quote or {}).get("ask") or 0)
    if not price_ok and quote and entry_prem > 0:
        if sl and _bid <= 0 < _ask and (_ask - entry_prem) / entry_prem * 100 <= -abs(sl):
            _p = (_ask - entry_prem) / entry_prem * 100
            info.pop("price_rules_skipped", None)
            return {**info, "exit": True, "reason": "STOP", "mid": None, "pnl_pct": round(_p, 1),   # the bound, not a stale last
                    "detail": f"No bid; even the ask {_ask:.2f} is {_p:+.1f}% "
                              f"(limit −{abs(sl):.0f}%)"}

    # 1b. TARGET_U — the underlying CLOSED at or beyond the alert's target
    # (review 2026-11-03 D3; owner 2026-11-04: "go with the target exit").
    # Part of the measured structure: option_backtest's arm B sells at the
    # close that reaches the signal's target, after STOP and before the
    # premium take-profit, and the larger D3 read it EQUIVALENT within +/-5
    # points of premium (results/option_target_exit_large_run1.md). Read like
    # THESIS: settled closes, and in the closing window the live price as a
    # stand-in for the close the measured exit leaves at (D6). It reads the
    # underlying, not the quote, so it runs on an unreliable or missing quote.
    try:
        tgt_u = float(rules.get("underlying_target") or 0)
    except (TypeError, ValueError):
        tgt_u = 0.0
    if tgt_u > 0:
        u = _under()
        if u:
            info.update(underlying=u["close"], target_u=tgt_u)
            _call = pos["right"].upper() == "CALL"
            if (u["close"] >= tgt_u) if _call else (u["close"] <= tgt_u):
                _how = (f"is trading {u['close']} near the close (live price, D6: the "
                        f"measured exit is at this close; sell before it)"
                        if u.get("live") else f"closed {u['close']}")
                return {**info, "exit": True, "reason": "TARGET_U",
                        "detail": f"{pos['ticker']} {_how} — at or {'above' if _call else 'below'} "
                                  f"the alert's target {tgt_u:g}. The measured structure sells "
                                  f"the option here."}

    if not price_ok and quote and entry_prem > 0:
        tp_ = rules.get("tp_pct") or 0
        if tp_ and _ask <= 0 < _bid and (_bid - entry_prem) / entry_prem * 100 >= abs(tp_):
            _p = (_bid - entry_prem) / entry_prem * 100
            info.pop("price_rules_skipped", None)
            return {**info, "exit": True, "reason": "TARGET", "mid": None, "pnl_pct": round(_p, 1),   # the bound, not a stale last
                    "detail": f"No ask; the bid alone {_bid:.2f} is {_p:+.1f}% "
                              f"(target +{abs(tp_):.0f}%)"}

    # 2. TARGET — premium rose past the take-profit
    tp = rules.get("tp_pct") or 0
    if tp and price_ok and pnl_pct >= abs(tp):
        return {**info, "exit": True, "reason": "TARGET",
                "detail": f"Premium {pnl_pct:+.1f}% at the bid (target +{abs(tp):.0f}%)"}

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
        u = _under()
        if u:
            info.update(underlying=u["close"], ema20=u["ema20"])
            broke = (u["close"] < u["ema20"]) if pos["right"].upper() == "CALL" \
                else (u["close"] > u["ema20"])
            if broke:
                side = "below" if pos["right"].upper() == "CALL" else "above"
                _how = (f"is trading {u['close']} near the close (live price, D6: the "
                        f"measured exit is at this close; sell before it)"
                        if u.get("live") else f"closed {u['close']}")
                return {**info, "exit": True, "reason": "THESIS",
                        "detail": f"{pos['ticker']} {_how} — {side} "
                                  f"EMA20 {u['ema20']}. The setup that justified "
                                  f"this {pos['right']} is no longer valid."}

    return {**info, "exit": False}


def format_alert(pos: dict, ev: dict, escalated_from: str | None = None) -> str:
    icons = {"TARGET": "🎯", "TARGET_U": "🎯", "STOP": "🛑", "TIME": "⏳", "HOLD": "📆",
             "THESIS": "📉"}
    icon = icons.get(ev["reason"], "⚠️")
    contract = (f"{pos['ticker']} {pos['expiry']} "
                f"${pos['strike']:g} {pos['right'].upper()}")
    head = f"{icon} <b>EXIT {ev['reason']}</b>"
    if escalated_from:
        # Say plainly that this is a SECOND alert on a position already
        # flagged, or it reads as a duplicate and gets dismissed as noise —
        # which is the opposite of what an escalation to STOP needs.
        head += f" (escalated from {escalated_from})"
    lines = [head, "", f"<b>{contract}</b>", ""]
    lines.append(f"Reason:  {ev['detail']}")
    if ev.get("mid") is not None:
        _mk = ev.get("mark") if ev.get("mark") is not None else ev["mid"]
        _on = "at the bid" if ev.get("reliable") and ev.get("mark") is not None else "last traded"
        lines.append(f"Premium: {pos['entry_premium']:.2f} → {_mk:.2f} {_on} "
                     f"({ev['pnl_pct']:+.1f}%)")
        if ev.get("reliable") and ev.get("ask"):
            lines.append(f"Quote:   bid {float(ev['bid']):.2f} / ask {float(ev['ask']):.2f} "
                         f"(mid {ev['mid']:.2f})")
        if pos.get("contracts"):
            n = float(pos["contracts"])
            pnl_usd = (_mk - float(pos["entry_premium"])) * 100 * n
            lines.append(f"P&L:     ${pnl_usd:+,.0f} on {n:g} contract(s) {_on}")
    if ev.get("dte") is not None:
        lines.append(f"DTE:     {ev['dte']}")
    if ev.get("bars_held") is not None:
        lines.append(f"Held:    {ev['bars_held']} sessions")
    if ev.get("reliable") is False and ev.get("mid") is not None:
        lines += ["", "⚠️ No two-sided market — price is last-traded and may be "
                      "stale. Verify before acting."]
    # Was a hardcoded 15 — the fifth copy of a ceiling that moved to 8%, so this
    # stayed silent on spreads between 8% and 15% that the entry gate would have
    # refused outright.
    elif ev.get("spread_pct") and ev["spread_pct"] > risk_params.MAX_OPTION_SPREAD_PCT:
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
    # EXIT_SIGNALLED positions are still monitored (BACKLOG 19), so they are
    # diagnosed too (review 2026-11-03: this read OPEN only and then told a
    # signalled position's owner that "the app is not syncing").
    open_pos = [p for p in positions if p.get("status") in MONITORED_STATUSES]
    print(f"    OPEN            : {sum(1 for p in positions if p.get('status') == 'OPEN')}")
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
              f"DTE<={rules.get('dte_exit')}  HOLD<={rules.get('max_hold_bars')}  "
              f"thesis={rules.get('invalidate_ema')}  target={rules.get('underlying_target')}")
        # max_hold_bars was missing from this check, so a position whose only
        # active rule was HOLD got reported as "can never alert" — the exact
        # opposite of the truth, from the tool you reach for when an alert
        # does not arrive.
        if not any([rules.get("tp_pct"), rules.get("sl_pct"),
                    rules.get("dte_exit"), rules.get("max_hold_bars"),
                    rules.get("invalidate_ema"), rules.get("underlying_target")]):
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
            # THE VERDICT IS run()'s OWN (review 2026-11-03): this used to price
            # the mid itself and fire STOP on a stale lastPrice that run()
            # refuses, so the diagnosis disagreed with the monitor it explains.
            ev = check_option_position(p)
            if ev.get("mark") is not None:
                print(f"    Rules read     : {ev['mark']:.2f} "
                      f"[{'the bid (D5)' if ev.get('reliable') else 'lastPrice, NO two-sided market'}]")
            if ev.get("pnl_pct") is not None:
                print(f"    P&L            : {ev['pnl_pct']:+.1f}%")
            if ev.get("price_rules_skipped"):
                print(f"    -> {ev['price_rules_skipped']}")
            elif ev.get("mid") is None and not ev.get("exit"):
                print("    -> No usable price: STOP and TARGET are skipped this run (TIME and")
                print("       THESIS still run). The monitor sends one notice a day about it.")
            _held = ev.get("bars_held")
            if _held is not None:
                print(f"    Sessions held  : {_held}"
                      f"{' / ' + str(rules['max_hold_bars']) if rules.get('max_hold_bars') else ''}")
            print(f"    Would fire     : {ev['reason'] + ' — ' + ev['detail'] if ev.get('exit') else 'nothing — all rules within limits'}")
            _prev = p.get("exit_reason") if p.get("exit_alerted") else None
            if ev.get("exit") and _prev and not outranks(ev["reason"], _prev):
                print(f"    -> Rule met, but {_prev} was already alerted and {ev['reason']} does not")
                print("       outrank it, so no repeat is sent. Close the position in the app.")
        except Exception as e:
            print(f"    !! Chain fetch failed: {e}")

    print("\n" + "=" * 72)


EXPIRY_REMINDER_SESSIONS = 2   # remind when this many sessions (or fewer) remain after today


def expiry_reminder(pos: dict, today=None) -> tuple | None:
    """
    THE EXPIRY REMINDER (review 2026-11-03 D5; owner: "go with D5"). Once
    STOP, TARGET_U or TARGET has alerted, nothing lower ever alerts (EXIT_PRIORITY): no
    TIME, and nothing on expiry day, so an unsold in-the-money call rides to
    automatic exercise (one 150 call is $15,000 of stock) and an expired
    position sits EXIT_SIGNALLED for ever. This is sent whatever the rules
    say: ("near", text) once when EXPIRY_REMINDER_SESSIONS or fewer sessions
    remain after today, ("past", text) once after expiry. None otherwise,
    or when already sent (pos["expiry_reminded"]).
    """
    today = today or datetime.now(ET).date()
    try:
        exp = datetime.strptime(str(pos["expiry"])[:10], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        return None
    done = pos.get("expiry_reminded")
    contract = (f"{pos['ticker']} {pos['expiry']} ${float(pos['strike']):g} "
                f"{str(pos.get('right', '')).upper()}")
    if exp < today:
        if done == "past":
            return None
        return "past", "\n".join([
            f"⏰ <b>EXPIRED {exp}</b>", "", f"<b>{contract}</b> is past expiry and still open in "
            f"the monitor.", "Log the close in the app (an option that expired worthless closes at 0)."])
    left = trading_sessions_between(today, exp)
    if left > EXPIRY_REMINDER_SESSIONS or done in ("near", "past"):
        return None
    n = float(pos.get("contracts") or 1)
    _was = (f" ({pos.get('exit_reason')} was alerted {pos.get('exit_detected', '')})"
            if pos.get("exit_alerted") else "")
    return "near", "\n".join([
        f"⏰ <b>EXPIRING {exp}</b> ({left} more session{'s' if left != 1 else ''} after today)", "",
        f"<b>{contract}</b> is still open in the monitor{_was}.",
        "Sell it or log the close in the app. An in-the-money call left to expiry is usually "
        f"exercised automatically: 100 shares at ${float(pos['strike']):g} a contract, "
        f"${float(pos['strike']) * 100 * n:,.0f} for {n:g}."])


def _price_matters(pos: dict, ev: dict) -> bool:
    """
    Whether a missing price costs this position anything (N7). Not when a
    STOP has already been alerted (nothing outranks it, so no price rule can
    add an alert), and not past expiry (there is no contract to price; the
    position is the owner's to log). Every other unpriced position has two
    of its rules switched off until a price comes back.
    """
    if pos.get("exit_alerted") and pos.get("exit_reason") == "STOP":
        return False
    dte = ev.get("dte")
    return not (dte is not None and dte < 0)


def format_unpriced(pos: dict, ev: dict) -> str:
    """The once-a-day notice for a position the monitor could not price (N7)."""
    contract = (f"{pos['ticker']} {pos['expiry']} "
                f"${pos['strike']:g} {pos['right'].upper()}")
    why = ev.get("price_rules_skipped") or "no quote for this contract"
    return "\n".join([
        "⚠️ <b>CANNOT PRICE A POSITION</b>", "", f"<b>{contract}</b>", "",
        f"Why: {why}",
        "STOP and TARGET could not run; TIME and THESIS still do.",
        "Check the contract's bid and ask by hand. This notice is sent once a day "
        "while it lasts."])


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

    open_pos = [p for p in positions if p.get("status") in MONITORED_STATUSES]
    if not open_pos:
        logger.info("No OPEN or EXIT_SIGNALLED positions — nothing to check.")
        return 0
    if not args.force and not is_market_open():
        logger.info("Market closed — skipping. (--force to override.)")
        return 0

    logger.info("Checking %d open position(s)…", len(open_pos))
    now_epoch = time.time()
    sent = 0
    unpriced: list[str] = []
    # TOTAL FAILURE IS NOT SUCCESS (external review 2026-10-19, BACKLOG 35).
    # A check that raised was logged and skipped, and run() returned 0, so a
    # morning on which every check failed was a green workflow. Counted here;
    # the exit status at the end says so.
    attempted = 0
    failed_checks = 0
    # N7 (review 2026-11-03): a position whose price rules could not run
    # (no quote, or one not worth acting on) used to reach the Actions log
    # only, behind a green run, while last_check_epoch kept the coverage
    # alarm quiet. Counted here, told to the owner once an ET day, and a run
    # that could price NOTHING that needed it exits nonzero, as a run where
    # every check raised already did. A failed send exits nonzero too.
    unmonitored = 0
    send_failed = 0
    today_et = datetime.now(ET).strftime("%Y-%m-%d")

    for pos in positions:
        # EXIT_SIGNALLED is still watched — see EXIT_PRIORITY. Only a
        # higher-priority reason gets through to an alert below.
        if pos.get("status") not in MONITORED_STATUSES:
            continue
        if not pos.get("right"):
            logger.info("%s — not an option position, skipping.",
                        pos.get("ticker", "?"))
            continue

        attempted += 1
        # D5: the expiry reminder goes whatever the rules say this run.
        _rem = expiry_reminder(pos)
        if _rem:
            if send_telegram(_rem[1], dry_run=args.dry_run):
                if not args.dry_run:
                    pos["expiry_reminded"] = _rem[0]
            else:
                send_failed += 1
        try:
            ev = check_option_position(pos)
        except Exception as e:
            failed_checks += 1
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
            elif ev.get("price_rules_skipped"):
                # A PRICE EXISTS BUT IS NOT WORTH ACTING ON. Same consequence as
                # no quote at all — TP and SL did not run — so it gets the same
                # visibility instead of the reassuring "open" line, which would
                # report a position as fine when two of its rules were inert.
                logger.warning("%s — %s", tag, ev["price_rules_skipped"])
                unpriced.append(f"{tag} (unreliable quote)")
            else:
                logger.info("%s open — premium %.2f (%+.1f%%), %s DTE",
                            tag, ev["mid"], ev["pnl_pct"], ev.get("dte"))
            if not args.dry_run:
                pos["last_check_epoch"] = now_epoch
            if (ev.get("mid") is None or ev.get("price_rules_skipped")) and _price_matters(pos, ev):
                unmonitored += 1
                if pos.get("unpriced_alerted_on") != today_et:
                    if send_telegram(format_unpriced(pos, ev), dry_run=args.dry_run):
                        if not args.dry_run:
                            pos["unpriced_alerted_on"] = today_et
                    else:
                        send_failed += 1
            continue

        logger.info("%s EXIT %s — %s", tag, ev["reason"], ev["detail"])
        prev = pos.get("exit_reason") if pos.get("exit_alerted") else None
        if prev and not outranks(ev["reason"], prev):
            # Already alerted, and this reason is no more urgent than the one
            # sent. Not silence: the position stays watched and its clock keeps
            # ticking, so a STOP after this can still get through.
            logger.info("  already alerted %s, and %s does not outrank it — "
                        "not re-sending (still watching).", prev, ev["reason"])
            if not args.dry_run:
                pos["last_check_epoch"] = now_epoch
            continue
        if prev:
            logger.warning("  ESCALATION: %s outranks the %s already alerted.",
                           ev["reason"], prev)

        if send_telegram(format_alert(pos, ev, escalated_from=prev),
                         dry_run=args.dry_run):
            sent += 1
            if not args.dry_run:
                # The earlier verdict is KEPT, not overwritten in place. A
                # position that went THESIS then STOP is a different history
                # from one that only ever stopped, and the journal is the one
                # record of what the rules actually said.
                if prev:
                    pos.setdefault("exit_history", []).append({
                        "reason": prev, "detail": pos.get("exit_detail"),
                        "premium": pos.get("exit_premium"),
                        "pnl_pct": pos.get("exit_pnl_pct"),
                        "detected": pos.get("exit_detected"),
                    })
                pos.update({
                    "exit_alerted": True, "status": "EXIT_SIGNALLED",
                    "exit_reason": ev["reason"], "exit_detail": ev["detail"],
                    "exit_premium": ev.get("mark", ev.get("mid")), "exit_pnl_pct": ev.get("pnl_pct"),
                    "exit_detected": datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"),
                    "last_check_epoch": now_epoch,
                })
        else:
            # NOT DELIVERED (N7). The state is left as it was, so the next run
            # retries, as before; the run now says so instead of ending green.
            send_failed += 1
            logger.error("%s — the %s exit alert was NOT delivered; the next run retries.",
                         tag, ev["reason"])

    write_failed = False
    if not args.dry_run:
        try:
            path.write_text(json.dumps(positions, indent=2, default=str))
        except Exception as e:
            write_failed = True
            logger.error("Could not write %s: %s — alerts sent but state not "
                         "saved, so expect a duplicate next run.", path, e)

    if unpriced:
        logger.warning("%d position(s) had NO PRICE this run: %s — their TP/SL "
                       "rules did not run.", len(unpriced), ", ".join(unpriced))
    if failed_checks:
        logger.warning("%d of %d position check(s) raised and were skipped — "
                       "partial coverage this run.", failed_checks, attempted)
    logger.info("Done. %d exit alert(s) sent.", sent)
    if attempted and failed_checks == attempted:
        logger.error("EVERY position check failed (%d of %d): nothing was "
                     "monitored this run. Exiting nonzero so the workflow shows "
                     "it instead of a green run.", failed_checks, attempted)
        return 1
    if attempted and unmonitored and failed_checks + unmonitored == attempted:
        logger.error("NO position that needed a price could be priced (%d of %d): STOP and "
                     "TARGET ran on nothing this run. Exiting nonzero so the workflow shows "
                     "it.", unmonitored, attempted)
        return 1
    if send_failed:
        logger.error("%d alert(s) were NOT delivered this run. Exiting nonzero.", send_failed)
        return 1
    if write_failed:
        return 1
    return 0


# ══════════════════════════════════════════════════════════════════
# SELFTEST — offline, no network, no clock dependence
# ══════════════════════════════════════════════════════════════════
# This module had NO test and was not in CI, while running hourly through the
# session and deciding when to close live positions. Both bugs it carried were
# invisible for the same reason: nothing could assert on them.
#
# Everything here injects its inputs. get_option_quote() and
# get_underlying_state() are monkeypatched, so no Yahoo call is made and the
# result does not depend on what day it is run.
def selftest() -> int:
    import pandas as _pd

    print("exit_monitor.py selftest")
    print("=" * 66)

    # ── session counting ──
    # Fri 2026-09-11 -> Mon 2026-09-14 is ONE session, not three days.
    assert trading_sessions_between(date(2026, 9, 11), date(2026, 9, 14)) == 1, \
        "a weekend must not age a position by three sessions"
    assert trading_sessions_between(date(2026, 9, 9), date(2026, 9, 9)) == 0
    # Thanksgiving 2026-11-26 is a holiday; 11-25 -> 11-27 is one session.
    assert trading_sessions_between(date(2026, 11, 25), date(2026, 11, 27)) == 1, \
        "a market holiday is not a session"
    # Half-days ARE sessions.
    assert trading_sessions_between(date(2026, 11, 26), date(2026, 11, 27)) == 1
    print("sessions          : weekends and holidays skipped, half-days counted")

    # ── bars_held prefers the epoch and survives a missing one ──
    _open_dt = datetime(2026, 9, 4, 14, 30, tzinfo=ET)
    _pos = {"opened_epoch": _open_dt.timestamp()}
    assert bars_held(_pos) is not None
    assert bars_held({"opened": "2026-09-04 14:30 ET"}) is not None, \
        "the literal ' ET' suffix must not defeat the fallback parser"
    assert bars_held({}) is None, \
        "with no usable date the HOLD rule must be disabled, not guessed"
    print("bars_held         : epoch preferred, string fallback, None disables")

    # ── the partial-bar guard on the THESIS input ──
    _idx = _pd.bdate_range(end=_pd.Timestamp("2026-09-10"), periods=6)
    _df = _pd.DataFrame({"Close": [10, 11, 12, 13, 14, 99.0]}, index=_idx)
    assert _pd.Timestamp("2026-09-10") in _df.index, "fixture needs a today bar"
    _mid_session = datetime(2026, 9, 10, 11, 0, tzinfo=ET)     # market OPEN
    _after_close = datetime(2026, 9, 10, 17, 30, tzinfo=ET)    # market CLOSED
    assert is_market_open(_mid_session) and not is_market_open(_after_close), \
        "fixture timestamps must straddle the close, or this proves nothing"
    _kept = drop_unsettled_bars(_df, now=_mid_session)
    assert len(_kept) == len(_df) - 1 and float(_kept["Close"].iloc[-1]) == 14.0, \
        "while the market is open, today's in-progress bar must be dropped — " \
        "its Close is the live price, and THESIS claims to read a CLOSE"
    _full = drop_unsettled_bars(_df, now=_after_close)
    assert len(_full) == len(_df) and float(_full["Close"].iloc[-1]) == 99.0, \
        "after the close today's bar is settled and is the one the rule wants"
    print("partial bar       : dropped intraday, kept after the close")

    # ── the exit rules, with the quote injected ──
    _real_quote, _real_under = get_option_quote, get_underlying_state
    _g = globals()
    try:
        def _q(mid, reliable=True):
            _g["get_option_quote"] = lambda *a, **k: {
                "mid": mid, "bid": mid, "ask": mid,
                "reliable": reliable, "spread_pct": 2.0}

        def _base(**over):
            pos = {"ticker": "TEST", "right": "CALL", "strike": 100.0,
                   "expiry": (datetime.now(ET).date() + timedelta(days=40)
                              ).strftime("%Y-%m-%d"),
                   "entry_premium": 2.00,
                   "rules": {"tp_pct": 200, "sl_pct": 50, "dte_exit": 7,
                             "max_hold_bars": 30, "invalidate_ema": False},
                   "opened_epoch": datetime.now(ET).timestamp()}
            pos.update(over)
            return pos

        _g["get_underlying_state"] = lambda *a, **k: None

        _q(0.90)                                    # -55% on a 2.00 entry
        ev = check_option_position(_base())
        assert ev["exit"] and ev["reason"] == "STOP", ev
        _q(6.10)                                    # +205%
        ev = check_option_position(_base())
        assert ev["exit"] and ev["reason"] == "TARGET", ev
        _q(2.00)
        ev = check_option_position(_base())
        assert not ev["exit"], ev
        print("STOP / TARGET     : fire at the limits, quiet in between")

        # TIME beats HOLD: a contract at the theta cliff needs out today.
        _near = (datetime.now(ET).date() + timedelta(days=3)).strftime("%Y-%m-%d")
        ev = check_option_position(_base(expiry=_near))
        assert ev["exit"] and ev["reason"] == "TIME", ev
        # HOLD fires on sessions, not calendar days.
        _old = datetime.now(ET).timestamp() - 90 * 86400
        ev = check_option_position(_base(opened_epoch=_old))
        assert ev["exit"] and ev["reason"] == "HOLD", ev
        print("TIME / HOLD       : DTE floor takes priority, HOLD counts sessions")

        # ── an UNRELIABLE quote must not drive a price exit ──
        # get_option_quote() falls back to lastPrice with reliable=False and its
        # own docstring says that "would produce phantom exits". The flag was
        # recorded and never read.
        _q(0.90, reliable=False)                    # would be a -55% STOP
        ev = check_option_position(_base())
        assert not ev["exit"], (
            "a STOP fired on an unreliable lastPrice — this is the phantom exit "
            f"the quote docstring warns about: {ev}")
        assert ev.get("price_rules_skipped"), \
            "skipping the price rules silently is worse than acting; it must be " \
            "surfaced so the human checks by hand"
        _q(6.10, reliable=False)
        assert not check_option_position(_base())["exit"], \
            "TARGET must not fire on an unreliable quote either"
        # ...but the clock rules do not depend on the quote and must still run.
        ev = check_option_position(_base(expiry=_near))
        assert ev["exit"] and ev["reason"] == "TIME", (
            "an unreliable quote must not disable the rules that never needed "
            f"it: {ev}")
        print("unreliable quote  : price rules skipped and flagged, clock still runs")

        # ── a non-positive entry premium makes % rules meaningless ──
        _q(1.00)
        ev = check_option_position(_base(entry_premium=0.0))
        assert ev["pnl_pct"] is None, \
            "entry_premium 0 used to give pnl_pct = 0, which passes " \
            "`is not None` and then fails every comparison — so STOP and " \
            "TARGET were silently dead for the life of the position"
        assert not ev["exit"] or ev["reason"] in ("TIME", "HOLD"), ev
        print("bad entry premium : treated as missing, not as 0% P&L")

        # ── THESIS reads the injected close, and respects direction ──
        _q(2.00)
        _g["get_underlying_state"] = lambda *a, **k: {
            "close": 95.0, "ema20": 100.0, "as_of": "2026-09-09"}
        ev = check_option_position(_base(rules={"invalidate_ema": True}))
        assert ev["exit"] and ev["reason"] == "THESIS", ev
        _g["get_underlying_state"] = lambda *a, **k: {
            "close": 105.0, "ema20": 100.0, "as_of": "2026-09-09"}
        assert not check_option_position(
            _base(rules={"invalidate_ema": True}))["exit"], \
            "a CALL above its EMA20 is not invalidated"
        ev = check_option_position(_base(right="PUT",
                                        rules={"invalidate_ema": True}))
        assert ev["exit"] and ev["reason"] == "THESIS", \
            "a PUT is invalidated ABOVE the EMA20, not below"
        print("THESIS            : direction-aware for CALL and PUT")

        # ── TARGET_U (D3; owner 2026-11-04): the underlying CLOSES at the alert's target ──
        _tgt = {"tp_pct": 200, "sl_pct": 50, "dte_exit": 7, "invalidate_ema": True,
                "underlying_target": 110.0}
        _calls = []

        def _ust(close, ema=100.0, live=False):
            def _f(*a, **k):
                _calls.append(k)
                return {"close": close, "ema20": ema, "as_of": "2026-11-04", "live": live}
            return _f
        _q(2.00)
        _g["get_underlying_state"] = _ust(110.0)
        ev = check_option_position(_base(rules=_tgt))
        assert ev["exit"] and ev["reason"] == "TARGET_U" and "closed 110.0" in ev["detail"] \
            and "target 110" in ev["detail"], ev
        _g["get_underlying_state"] = _ust(109.99)
        assert not check_option_position(_base(rules=_tgt))["exit"], "a close under the target is not the target"
        _g["get_underlying_state"] = _ust(130.0)
        assert not check_option_position(_base(rules=dict(_tgt, underlying_target=0)))["exit"], \
            "no alert target: the rule cannot fire"
        _g["get_underlying_state"] = _ust(88.0, ema=80.0)
        ev = check_option_position(_base(right="PUT", rules=dict(_tgt, underlying_target=90.0)))
        assert ev["exit"] and ev["reason"] == "TARGET_U" and "below" in ev["detail"], "a PUT's target is BELOW"
        # the order: STOP first, then TARGET_U, then the premium TARGET
        _g["get_underlying_state"] = _ust(115.0)
        _q(0.90)
        assert check_option_position(_base(rules=_tgt))["reason"] == "STOP", "risk first"
        _q(6.10)
        assert check_option_position(_base(rules=_tgt))["reason"] == "TARGET_U", \
            "the alert's target is checked before the premium take-profit"
        # it reads the underlying, so an unreliable quote does not silence it
        _q(2.00, reliable=False)
        ev = check_option_position(_base(rules=_tgt))
        assert ev["exit"] and ev["reason"] == "TARGET_U" and ev.get("price_rules_skipped"), ev
        # one read of the underlying, shared with THESIS; the live read in the closing window
        _q(2.00)
        _calls.clear()
        _g["get_underlying_state"] = _ust(105.0)
        assert not check_option_position(_base(rules=_tgt))["exit"]
        assert len(_calls) == 1, (_calls, "TARGET_U and THESIS share one fetch")
        _calls.clear()
        _real_cw0 = _g["in_closing_window"]
        _g["in_closing_window"] = lambda *a, **k: True
        _g["get_underlying_state"] = _ust(111.0, live=True)
        try:
            ev = check_option_position(_base(rules=_tgt))
        finally:
            _g["in_closing_window"] = _real_cw0
        assert ev["reason"] == "TARGET_U" and "near the close (live price" in ev["detail"] \
            and _calls == [{"live_close": True}], (ev, _calls)
        assert "EXIT TARGET_U" in format_alert(_base(rules=_tgt), ev) and "🎯" in format_alert(_base(rules=_tgt), ev)
        print("TARGET_U          : a close at the alert's target sells, after STOP and before the premium "
              "TARGET; direction-aware; quote-free; one fetch with THESIS; live near the close")

        # ── and the WIRING: get_underlying_state() must itself drop the bar ──
        # Testing drop_unsettled_bars() alone passed even with the call removed
        # from get_underlying_state — a guard that exists but is not reached is
        # the same as no guard. This asserts the path, not the helper.
        _g["get_underlying_state"] = _real_under
        import data_source as _ds
        _real_fetch = _ds.fetch_daily
        try:
            _idx2 = _pd.bdate_range(end=_pd.Timestamp("2026-09-10"), periods=30)
            _closes = [100.0] * 29 + [1.0]      # today gaps to 1.00 intraday
            _frame = _pd.DataFrame({"Close": _closes}, index=_idx2)
            _ds.fetch_daily = lambda *a, **k: (_frame, "yahoo")

            _u_open = get_underlying_state("TEST", now=_mid_session)
            assert _u_open is not None, _u_open
            assert _u_open["as_of"] == "2026-09-09", (
                f"while the market is open get_underlying_state() must report "
                f"the last SETTLED session, got {_u_open['as_of']}")
            assert _u_open["close"] == 100.0, (
                f"it returned the live intraday print {_u_open['close']} — the "
                f"THESIS rule would fire on a dip that may recover by the close")

            _u_closed = get_underlying_state("TEST", now=_after_close)
            assert _u_closed["as_of"] == "2026-09-10" and _u_closed["close"] == 1.0, (
                f"after the close today's settled bar is the one to use, got "
                f"{_u_closed}")
            print("THESIS input      : settled close intraday, today's after the close")
        finally:
            _ds.fetch_daily = _real_fetch
    finally:
        _g["get_option_quote"] = _real_quote
        _g["get_underlying_state"] = _real_under

    # ══════════════════════════════════════════════════════════════
    # AN EXIT_SIGNALLED POSITION IS STILL WATCHED
    # ══════════════════════════════════════════════════════════════
    #
    # THE GAP THIS PINS. run() selected `status == "OPEN"`, so the first rule to
    # fire ended monitoring for good and nothing could put it back. Decline a
    # THESIS or a HOLD — both judgement calls — and the STOP rule was dead for
    # the life of the position, on a long option where the premium is the whole
    # risk.
    #
    # Driven through run() itself, not through outranks(). The bug was never in
    # the ranking; it was in which positions run() looked at, and a unit test on
    # the comparator would have passed throughout.
    import argparse as _ap
    import tempfile as _tf
    from pathlib import Path as _P

    assert outranks("STOP", "THESIS") and outranks("STOP", "TARGET")
    assert not outranks("THESIS", "STOP") and not outranks("THESIS", "THESIS")
    assert outranks("STOP", None), "a never-alerted position always alerts"
    assert outranks("STOP", "WHAT_IS_THIS"), \
        "an unrecognised previous reason must not be able to suppress a STOP"
    assert outranks("STOP", "TARGET_U") and outranks("TARGET_U", "TARGET") and outranks("TARGET_U", "THESIS")
    assert not outranks("TARGET", "TARGET_U") and not outranks("THESIS", "TARGET_U") \
        and not outranks("TARGET_U", "TARGET_U")
    print("priority          : STOP outranks THESIS/TARGET; THESIS never outranks; TARGET_U between STOP "
          "and TARGET")

    def _run_once(rows, quote):
        """run() over a temp positions file. Returns (sent, rows_after)."""
        d = _P(_tf.mkdtemp()) / "pos.json"
        d.write_text(json.dumps(rows))
        _g["get_option_quote"] = lambda *a, **k: quote
        _g["get_underlying_state"] = lambda *a, **k: None
        _g["send_telegram"] = lambda *a, **k: True
        try:
            args = _ap.Namespace(positions=str(d), diagnose=False, force=True,
                                 dry_run=False)
            _sent = run(args)
            assert _sent == 0, _sent
            return json.loads(d.read_text())
        finally:
            _g["get_option_quote"] = _real_quote
            _g["get_underlying_state"] = _real_under
            _g["send_telegram"] = _real_send

    _real_send = _g["send_telegram"]

    # ── TOTAL CHECK FAILURE EXITS NONZERO; PARTIAL FAILURE DOES NOT ──
    # (external review 2026-10-19). Two positions: when every check raises
    # the run is a failure the workflow must show; when one of two raises the
    # other was still monitored and the run is a (logged) partial.
    def _run_rc(rows, checker):
        d = _P(_tf.mkdtemp()) / "pos.json"
        d.write_text(json.dumps(rows))
        _real_check = _g["check_option_position"]
        _g["check_option_position"] = checker
        _g["send_telegram"] = lambda *a, **k: True
        try:
            return run(_ap.Namespace(positions=str(d), diagnose=False, force=True, dry_run=False))
        finally:
            _g["check_option_position"] = _real_check
            _g["send_telegram"] = _real_send
    _two = [{"id": "A", "ticker": "AAA", "right": "CALL", "strike": 10.0, "expiry": "2099-01-01",
             "contracts": 1.0, "entry_premium": 1.0, "rules": {"tp_pct": 200, "sl_pct": 50},
             "status": "OPEN"},
            {"id": "B", "ticker": "BBB", "right": "CALL", "strike": 10.0, "expiry": "2099-01-01",
             "contracts": 1.0, "entry_premium": 1.0, "rules": {"tp_pct": 200, "sl_pct": 50},
             "status": "OPEN"}]
    def _boom(pos):
        raise RuntimeError("quote service down")
    def _half(pos):
        if pos["id"] == "A":
            raise RuntimeError("one bad quote")
        return {"exit": False, "mid": 1.0, "pnl_pct": 0.0, "dte": 30}
    assert _run_rc([dict(r) for r in _two], _boom) == 1, "every check failed -> nonzero"
    assert _run_rc([dict(r) for r in _two], _half) == 0, "one of two failed -> partial, exit 0"
    assert _run_rc([], _boom) == 0, "nothing to check is not a failure"
    print("exit status       : all checks failed -> 1; one of two -> 0 (partial); none -> 0")

    _base = {"id": "X", "ticker": "TEST", "right": "CALL", "strike": 10.0,
             "expiry": "2099-01-01", "contracts": 1.0, "entry_premium": 1.00,
             "rules": {"tp_pct": 200, "sl_pct": 50},
             "status": "EXIT_SIGNALLED", "exit_alerted": True,
             "exit_reason": "THESIS", "exit_detail": "closed below EMA20"}

    # A STOP after a declined THESIS MUST get through. This is the whole point.
    _after = _run_once([dict(_base)], {"mid": 0.40, "reliable": True,
                                       "spread_pct": 3.0})[0]
    assert _after["exit_reason"] == "STOP", (
        f"a STOP on a position already flagged THESIS was not alerted "
        f"(reason is still {_after['exit_reason']!r}). This is the gap: "
        f"declining a soft exit used to kill the stop for good.")
    assert _after["exit_history"][0]["reason"] == "THESIS", \
        "the earlier verdict must be kept, not overwritten in place"
    print("escalation        : STOP after a declined THESIS alerts, "
          "and THESIS is kept in exit_history")

    # The same THESIS again must NOT re-alert — that is the noise the
    # exit_alerted flag was added to stop, and it must survive this change.
    _quiet = dict(_base, exit_reason="STOP", exit_detail="premium -60%")
    _after2 = _run_once([_quiet], {"mid": 0.40, "reliable": True,
                                   "spread_pct": 3.0})[0]
    assert "exit_history" not in _after2, (
        "a STOP on a position already flagged STOP re-alerted; the hourly "
        "duplicate is exactly what the already-alerted flag exists to prevent")
    assert _after2["last_check_epoch"] > 0, \
        "a suppressed position must still have been CHECKED, or it is not " \
        "being watched at all — only quietly ignored"
    print("no repeat         : same reason stays silent, but the clock still ticks")

    # And a position in neither status is left alone.
    _closed = _run_once([dict(_base, status="CLOSED")], {"mid": 0.40,
                                                         "reliable": True})[0]
    assert _closed["exit_reason"] == "THESIS" and "exit_history" not in _closed, \
        "only OPEN and EXIT_SIGNALLED are monitored"
    print("scope             : CLOSED positions are not touched")

    # ── N7 (review 2026-11-03): quotes the monitor used to misread ──
    class _Chain:
        def __init__(self, rows):
            self.calls = self.puts = _pd.DataFrame(rows)
    _real_tk = yf.Ticker
    def _quote_of(bid, ask, last):
        class _Tk:
            def __init__(self, t): pass
            def option_chain(self, e): return _Chain([{"strike": 10.0, "bid": bid, "ask": ask, "lastPrice": last}])
        yf.Ticker = _Tk
        try:
            return get_option_quote("T", "2099-01-01", 10.0, "CALL")
        finally:
            yf.Ticker = _real_tk
    assert _quote_of(0.60, 0.20, 0) is None, "a crossed quote is not a market"
    assert _quote_of(0.60, 0.20, 0.40)["reliable"] is False, "crossed with a last trade: unreliable, never a mid"
    assert _quote_of(0.45, 0.55, 0.5)["reliable"] is True
    assert _quote_of(0, 0.05, 0) == {"mid": None, "bid": 0.0, "ask": 0.05, "reliable": False, "spread_pct": None}, \
        "one side and no last trade is still a bound"
    _pos1 = {"ticker": "T", "expiry": "2099-01-01", "strike": 10.0, "right": "CALL", "entry_premium": 1.0,
             "rules": {"tp_pct": 200, "sl_pct": 50}}
    def _ev(q):
        _g["get_option_quote"] = lambda *a, **k: q
        _g["get_underlying_state"] = lambda *a, **k: None
        try:
            return check_option_position(_pos1)
        finally:
            _g["get_option_quote"] = _real_quote
            _g["get_underlying_state"] = _real_under
    _e = _ev({"mid": 0.60, "bid": 0.0, "ask": 0.10, "reliable": False, "spread_pct": None})
    assert _e["exit"] and _e["reason"] == "STOP" and _e["mid"] is None and "even the ask 0.10" in _e["detail"], _e
    assert _ev({"mid": None, "bid": 0.0, "ask": 0.05, "reliable": False})["reason"] == "STOP"
    assert not _ev({"mid": None, "bid": 0.0, "ask": 0.60, "reliable": False})["exit"], "an ask above the stop proves nothing"
    assert _ev({"mid": None, "bid": 3.2, "ask": 0.0, "reliable": False})["reason"] == "TARGET"
    assert not _ev({"mid": None, "bid": 2.0, "ask": 0.0, "reliable": False})["exit"]
    print("one-sided quotes  : an ask under the stop proves STOP, a bid over the target proves TARGET; "
          "a crossed quote is not a market (N7)")

    def _run_full(rows, quote, send_ok=True):
        d = _P(_tf.mkdtemp()) / "pos.json"
        d.write_text(json.dumps(rows))
        msgs = []
        _g["get_option_quote"] = lambda *a, **k: quote
        _g["get_underlying_state"] = lambda *a, **k: None
        _g["send_telegram"] = lambda m, *a, **k: (msgs.append(m) or send_ok)
        try:
            rc = run(_ap.Namespace(positions=str(d), diagnose=False, force=True, dry_run=False))
            return rc, msgs, json.loads(d.read_text())
        finally:
            _g["get_option_quote"] = _real_quote
            _g["get_underlying_state"] = _real_under
            _g["send_telegram"] = _real_send
    _open = dict(_base, status="OPEN", exit_alerted=False, exit_reason=None)
    _rc, _m, _rows = _run_full([dict(_open)], None)
    assert _rc == 1 and len(_m) == 1 and "CANNOT PRICE" in _m[0] and _rows[0]["unpriced_alerted_on"], (_rc, _m)
    _rc2, _m2, _ = _run_full(_rows, None)
    assert _rc2 == 1 and not _m2, "the notice is once an ET day; the run still says nothing was priced"
    _rc3, _m3, _ = _run_full([dict(_open), dict(_open, id="Y", ticker="OK")],
                             None)
    assert _rc3 == 1 and len(_m3) == 2
    _rc4, _m4, _ = _run_full([dict(_base, exit_reason="STOP")], None)
    assert _rc4 == 0 and not _m4, "a STOP already alerted: no price rule can add anything, so no notice"
    _rc5, _m5, _r5 = _run_full([dict(_open)], {"mid": 0.30, "reliable": True, "spread_pct": 3.0}, send_ok=False)
    assert _rc5 == 1 and len(_m5) == 1 and _r5[0]["status"] == "OPEN", "an undelivered STOP exits nonzero and retries"
    _rc6, _m6, _ = _run_full([dict(_open)], {"mid": 1.0, "reliable": True, "spread_pct": 3.0})
    assert _rc6 == 0 and not _m6, "a priced, quiet position is a green run"
    print("unpriced/undelivered: one notice a day, nonzero when nothing needing a price was priced or "
          "an alert did not go; a STOP already sent needs no price (N7)")

    # ── D5 (owner 2026-11-03): THE RULES READ THE BID ──
    _q = lambda b, a: {"mid": round((b + a) / 2, 2), "bid": b, "ask": a, "reliable": True,  # noqa: E731
                       "spread_pct": round((a - b) / ((a + b) / 2) * 100, 1)}
    _s = _ev(_q(0.30, 0.80))
    assert _s["exit"] and _s["reason"] == "STOP" and _s["pnl_pct"] == -70.0 and "at the bid" in _s["detail"], _s
    assert not _ev(_q(1.80, 4.40))["exit"], "the mid says +210 %; the bid +80 %: no TARGET"
    assert _ev(_q(3.00, 3.10))["reason"] == "TARGET"
    assert _ev(_q(0.50, 0.60))["reason"] == "STOP", "exactly -50 % at the bid fires (the mid, -45 %, did not)"
    assert not _ev(_q(0.51, 0.53))["exit"]
    _fa = format_alert(dict(_pos1, contracts=2), _s)
    assert "1.00 → 0.30 at the bid (-70.0%)" in _fa and "bid 0.30 / ask 0.80 (mid 0.55)" in _fa \
        and "$-140 on 2 contract(s) at the bid" in _fa, _fa
    _rcb, _mb, _rb = _run_full([dict(_open)], _q(0.30, 0.80))
    assert _rcb == 0 and len(_mb) == 1 and _rb[0]["exit_premium"] == 0.30 and _rb[0]["exit_pnl_pct"] == -70.0, _rb
    print("D5 bid            : STOP and TARGET read the bid on a two-sided quote; the alert shows bid/ask/mid "
          "and the P&L at the bid; the recorded exit premium is the bid")

    # ── D5: THE EXPIRY REMINDER, whatever the rules say ──
    _nw = {"ticker": "NOW", "expiry": "2026-11-20", "strike": 150.0, "right": "CALL", "contracts": 2,
           "exit_alerted": True, "exit_reason": "STOP", "exit_detected": "2026-10-30 14:00 ET"}
    assert expiry_reminder(_nw, date(2026, 11, 17)) is None, "three sessions left: not yet"
    _n = expiry_reminder(_nw, date(2026, 11, 18))
    assert _n[0] == "near" and "2 more sessions after today" in _n[1] and "STOP was alerted" in _n[1] \
        and "$30,000 for 2" in _n[1], _n
    assert expiry_reminder(_nw, date(2026, 11, 20))[0] == "near", "expiry day itself"
    assert expiry_reminder(dict(_nw, expiry_reminded="near"), date(2026, 11, 20)) is None, "once"
    assert expiry_reminder(dict(_nw, expiry_reminded="near"), date(2026, 11, 21))[0] == "past"
    assert expiry_reminder(dict(_nw, expiry_reminded="past"), date(2026, 11, 23)) is None
    _soon = datetime.now(ET).date()
    while True:                                   # the next trading day: one session after today
        _soon += timedelta(days=1)
        if trading_sessions_between(datetime.now(ET).date(), _soon) == 1:
            break
    _ex = dict(_base, exit_reason="STOP", expiry=_soon.isoformat())
    _rcr, _mr, _rr = _run_full([dict(_ex)], None)
    assert _rcr == 0 and len(_mr) == 1 and "EXPIRING" in _mr[0] and _rr[0]["expiry_reminded"] == "near", (_rcr, _mr)
    _rcr2, _mr2, _ = _run_full(_rr, None)
    assert _rcr2 == 0 and not _mr2, "sent once"
    _rcr3, _mr3, _rr3 = _run_full([dict(_ex)], None, send_ok=False)
    assert _rcr3 == 1 and "expiry_reminded" not in _rr3[0], "an undelivered reminder is retried and the run says so"
    # --diagnose reports run()'s own verdict, EXIT_SIGNALLED positions included
    import io as _io, contextlib as _cl
    class _DTk:
        options = ["2099-01-01"]
        def __init__(self, t): pass
        def option_chain(self, e):
            return _Chain([{"strike": 10.0, "bid": 0.30, "ask": 0.80, "lastPrice": 0.95}])
    _buf = _io.StringIO()
    yf.Ticker = _DTk
    _g["get_underlying_state"] = lambda *a, **k: None
    try:
        with _cl.redirect_stdout(_buf):
            diagnose([dict(_base, exit_reason="THESIS")])
    finally:
        yf.Ticker = _real_tk
        _g["get_underlying_state"] = _real_under
    assert "Rules read     : 0.30 [the bid (D5)]" in _buf.getvalue() and \
        "Would fire     : STOP" in _buf.getvalue() and "EXIT_SIGNALLED  : 1" in _buf.getvalue(), _buf.getvalue()
    # ── D6 (owner 2026-11-03): THESIS ON THE LIVE PRICE IN THE CLOSING WINDOW ──
    _at = lambda d, h, m: ET.localize(datetime(*d, h, m))                     # noqa: E731
    assert not in_closing_window(_at((2026, 11, 4), 15, 24)) and in_closing_window(_at((2026, 11, 4), 15, 25))
    assert in_closing_window(_at((2026, 11, 4), 15, 30)) and in_closing_window(_at((2026, 11, 4), 16, 0))
    assert not in_closing_window(_at((2026, 11, 4), 16, 1)) and not in_closing_window(_at((2026, 11, 4), 11, 0))
    assert in_closing_window(_at((2026, 11, 27), 12, 25)) and not in_closing_window(_at((2026, 11, 27), 15, 30)), "half day"
    assert not in_closing_window(_at((2026, 11, 7), 15, 30)) and not in_closing_window(_at((2026, 11, 26), 15, 30))
    # a rising series whose LAST bar (today, still forming) drops under EMA20
    _idx = _pd.bdate_range(end="2026-11-04", periods=60)
    _cl = [100 + i * 0.5 for i in range(59)] + [100.0]
    _fr6 = _pd.DataFrame({"Open": _cl, "High": _cl, "Low": _cl, "Close": _cl, "Volume": 1e6}, index=_idx)
    _real_fd = data_source.fetch_daily
    try:
        data_source.fetch_daily = lambda *a, **k: (_fr6.copy(), "yahoo")
        _u0 = get_underlying_state("X", now=_at((2026, 11, 4), 15, 30))
        _u1 = get_underlying_state("X", now=_at((2026, 11, 4), 15, 30), live_close=True)
    finally:
        data_source.fetch_daily = _real_fd
    assert _u0["as_of"] == "2026-11-03" and _u0["close"] > _u0["ema20"] and not _u0["live"], _u0
    assert _u1["as_of"] == "2026-11-04" and _u1["close"] < _u1["ema20"] and _u1["live"], _u1
    _uk = []
    _real_cw = _g["in_closing_window"]
    _g["in_closing_window"] = lambda *a, **k: True
    _g["get_underlying_state"] = lambda *a, **k: (_uk.append(k) or
                                                  {"close": 100.0, "ema20": 121.5, "as_of": "2026-11-04", "live": True})
    _g["get_option_quote"] = lambda *a, **k: None
    try:
        _th = check_option_position(dict(_pos1, rules={"invalidate_ema": True}))
    finally:
        _g["get_underlying_state"] = _real_under
        _g["get_option_quote"] = _real_quote
        _g["in_closing_window"] = _real_cw
    assert _th["reason"] == "THESIS" and "near the close (live price" in _th["detail"], _th
    assert _uk == [{"live_close": True}], ("THESIS asks for the live read in the closing window", _uk)
    print("D6 thesis         : from 35 min before the close THESIS reads the live price against an EMA20 "
          "that includes it; earlier runs read settled closes only")
    print("D5 expiry         : one reminder within 2 sessions of expiry and one after it, even after STOP; "
          "undelivered retries")

    print("=" * 66)
    print("All self-tests passed.")
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
    p.add_argument("--selftest", action="store_true",
                   help="Run the offline rule tests. No network, no alerts.")
    return p.parse_args()


if __name__ == "__main__":
    _args = parse_args()
    if _args.selftest:
        sys.exit(selftest())
    if _args.test_telegram:
        sys.exit(test_telegram())
    sys.exit(run(_args))
