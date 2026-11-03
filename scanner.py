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
    python scanner.py --record-only        # post-close: forward-log rows +
                                           # heartbeat for today's bar, no alerts
"""
from __future__ import annotations

import argparse
import json
import os
import time
import logging
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
import pytz
import ta
import yfinance as yf

import data_source
import market_context
import iv_snapshot
import basket_log
import spread_log
from datetime import date as _date_cls
import option_chain

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("scanner")

ET = pytz.timezone("America/New_York")

# ── Watchlist ──
# STATIC_WATCHLIST is the frozen baseline — the config selected by the Aug 2026
# sweep. Kept as the fallback because it is the best-DEFINED hypothesis tested,
# not because it is profitable: the 591-trade out-of-sample validation returned
# -0.014 R with PF 0.98.
#
# When USE_DYNAMIC_UNIVERSE is set, the list comes from the newest snapshot in
# universe_history/, written by the weekly scheduled job. Same env var app.py
# reads, so the two can never scan different universes — alerting on tickers
# the app will not show you is worse than either choice on its own.
STATIC_WATCHLIST = ["NVDA", "META", "MSFT"]


def resolve_watchlist() -> list[str]:
    """
    Resolve the scan list at startup and log which source won.

    allow_live=False on purpose: ranking the universe pulls ~67 symbols
    through yf.download, and this process still has a whole watchlist to fetch
    through the same Yahoo rate budget. If no snapshot exists, fall back to the
    static list rather than spending the budget on ranking.
    """
    try:
        import universe as _u
    except Exception as e:
        logger.warning("universe.py unavailable (%s) — using static watchlist", e)
        return STATIC_WATCHLIST

    if not _u.dynamic_enabled():
        logger.info("Watchlist: static baseline (%s). Set %s=1 for the "
                    "RS-ranked universe.", ", ".join(STATIC_WATCHLIST),
                    _u.ENV_TOGGLE)
        return STATIC_WATCHLIST

    tickers, status, age, source = _u.load_universe(
        STATIC_WATCHLIST, allow_live=False)
    if source == "snapshot":
        logger.info("Watchlist: dynamic — %s (%dd old): %s",
                    status, age, ", ".join(tickers))
    elif source == "snapshot-stale":
        logger.warning("Watchlist: %s. Scanning it anyway: %s",
                       status, ", ".join(tickers))
    else:
        logger.warning("Watchlist: %s — falling back to %s",
                       status, ", ".join(tickers))
    return tickers


WATCHLIST = resolve_watchlist()


def pool_tickers(watchlist: list[str]) -> list[str]:
    """The candidate pool beyond the watchlist: what the pool bucket scans.
    universe.CANDIDATE_POOL already excludes every reserved tranche
    (check_universe_not_spending_reserved), so nothing here spends one."""
    try:
        import universe as _u
    except Exception as e:                             # noqa: BLE001
        logger.warning("universe.py unavailable (%s) — no pool scan", e)
        return []
    return sorted(set(_u.CANDIDATE_POOL) - set(watchlist))


def relative_strength(df, bench, lookback: int | None = None,
                      now: datetime | None = None) -> float | None:
    """universe.score_ticker()'s RS — the return spread against SPY over
    RS_LOOKBACK sessions, in percentage points — on the scan's own frames,
    so pool alerts rank on the number the universe already ranks on.

    SETTLED AND DATE-ALIGNED (review 2026-10-13): today's partial bar is
    dropped from both frames, and the two returns are taken over the same
    calendar dates, so a halt or a provider gap in one frame cannot shift
    its window against the benchmark's."""
    try:
        import universe as _u
        lb = lookback or _u.RS_LOOKBACK
        if df is None or bench is None:
            return None
        _now = now or datetime.now(ET)
        a, _ = sc.drop_partial_bar(df, now=_now)
        b, _ = sc.drop_partial_bar(bench, now=_now)
        sa = pd.Series(a["Close"].to_numpy(dtype=float), index=sc._bar_dates(a, None))
        sb = pd.Series(b["Close"].to_numpy(dtype=float), index=sc._bar_dates(b, None))
        common = sa.index.intersection(sb.index)
        if len(common) < lb + 1:
            return None
        common = common[-(lb + 1):]
        t = float(sa[common].iloc[-1]) / float(sa[common].iloc[0]) - 1
        bb = float(sb[common].iloc[-1]) / float(sb[common].iloc[0]) - 1
        return round((t - bb) * 100, 2)
    except Exception:                                  # noqa: BLE001
        return None


def pool_due(state: dict, today: str | None = None) -> bool:
    """Once a session: the pool scan runs on the first alerting scan of each
    ET date, so the 11:07 dispatch carries it and a backup slot catches a
    missed one."""
    today = today or datetime.now(ET).strftime("%Y-%m-%d")
    return state.get(POOL_STATE_KEY) != today

import signal_core as sc
import forward_log
import pullback_signal
import risk_params
import notify

# ── Tunables ──
# These now come from signal_core.DEFAULTS so app.py and scanner.py cannot
# drift again. Overriding any of them here would recreate the exact bug this
# refactor removed, so don't — change signal_core.SignalParams instead.
PARAMS             = sc.DEFAULTS

# FIX: these had drifted from app.py, which meant Telegram alerts were firing
# on a looser configuration than the app scanned with — ADX 25 vs 35, target
# 3.0x vs 4.0x, R:R 2.0 vs 0.5. Two systems disagreeing about what counts as a
# signal is worse than either being wrong, because you cannot tell which one
# produced a given trade. Now matched to app.py's frozen baseline.
# NOTE: ADX_MIN / ATR_STOP_MULT / ATR_TGT_MULT / MIN_RR used to live here as
# module constants. They were removed once analyze() started reading PARAMS
# directly: leaving them would be actively misleading, since editing them
# would look like it changed the signal and would in fact do nothing. Change
# signal_core.SignalParams instead.
EARNINGS_BLACKOUT_DAYS = 3   # matches app.py sidebar default
POST_EARNINGS_DAYS     = 1   # matches app.py sidebar default
# RELEASE DATES ALREADY SEEN, per ticker (review 2026-11-03, N8): Yahoo's
# calendar drops a release once it has happened, so the straddle rule (J2)
# reads this memory beside it. run() binds it to scanner_state.json under
# this key; market_context.remember_release_dates() keeps and prunes it.
EARNINGS_STATE_KEY = "EARNINGS:SEEN"
_EARNINGS_SEEN: dict = {}
# The dates the last earnings check read for each ticker this run, for the
# alert's "next earnings" line (N9). Never persisted.
_EARNINGS_LAST: dict = {}
ALERT_COOLDOWN_HRS = 4      # per ticker AND direction
STATE_FILE         = Path("scanner_state.json")
STATE_MAX_AGE_DAYS = 30     # cooldown stamps older than this are pruned
POST_CLOSE_WINDOW_HRS = 2   # --record-only runs this long after the close

# ── Option suggestion ──
# DTE window is centred on 30 deliberately: option_backtest.py measured this
# strategy with a 30-DTE entry, so suggesting 7-DTE contracts would be
# recommending something never tested. 21-45 keeps live trades comparable to
# the backtest they are supposed to be validating.
SUGGEST_OPTIONS  = True

# THE POOL BUCKET (BACKLOG 30, owner's decision 2026-10-13). Once a session
# the scanner also evaluates the whole candidate pool beyond the watchlist,
# alerts on the HIGH-QUALITY tier only, at most POOL_ALERT_CAP a session
# ranked by relative strength, and records every pool signal under its own
# bucket in the forward log. Pre-registered as a dated amendment to
# results/forward_record_preregistration.md.
POOL_ALERT_CAP   = 5
# IV GRADING FETCHES DEPARTED NAMES (external review 2026-10-19, N05;
# BACKLOG 35). The grader was handed closes only for names in `frames`, the
# current watchlist, so a snapshot taken on a name that then rotated out
# was never graded: with 3 of 8 names rotating weekly, most snapshots would
# have been censored by the time their window closed. The post-close pass
# now fetches each unresolved name once, up to this many per pass; the rest
# wait for the next pass and the log says so.
IV_GRADE_FETCH_CAP = 20
POOL_STATE_KEY   = "POOL:DATE"   # the ET date the pool was last scanned
# The chain window is the measured structure's (risk_params, BACKLOG 32).
OPT_MIN_DTE      = int(risk_params.MEASURED_OPTION_STRUCTURE["min_dte"])
OPT_MAX_DTE      = int(risk_params.MEASURED_OPTION_STRUCTURE["max_dte"])
OPT_MAX_EXPIRIES = 3        # each expiry is one chain fetch — keep it lean
# Single-sourced from risk_params.py, not chosen here.
#
# This said "at the old 15% the strategy needed a 26.5% win rate against a
# measured 23.8%". That 26.5% was a TP+200 breakeven while OPT_WIN_RATE was
# measured at TP+100 — the basis mismatch corrected on 2026-09-10. At the
# measured basis 15% needs 44.1% and the 8% ceiling needs 38.9%, so no spread
# clears 23.8%: this is a loss cap, not a profitability threshold.
OPT_MAX_SPREAD   = risk_params.MAX_OPTION_SPREAD_PCT

# Shown in the alert for context. NOT used to filter during the test phase —
# you asked to see every suggestion and judge affordability yourself.
# Sizing constants come from risk_params.py so app.py and this file cannot
# drift. NOTE the rename: this percentage is the MAX PREMIUM PER CONTRACT,
# because on a long option the premium is the maximum loss. app.py's RISK_PCT
# is a different quantity — the fraction of the account risked between entry
# and stop. They were both called RISK_PCT and a review read the 1% vs 5% gap
# as a 5x position-sizing bug; it was not, but one name meaning two things is
# exactly how the earlier app/scanner divergences started.
ACCOUNT_SIZE      = risk_params.DEFAULT_ACCOUNT_SIZE
OPTION_BUDGET_PCT = risk_params.DEFAULT_OPTION_BUDGET_PCT

# Indicator warm-up — same reasoning as the Streamlit app
FETCH_PERIOD          = "1y"
INDICATOR_WARMUP_BARS = 100
MIN_BARS_AFTER_WARMUP = 40

# Politeness gap between Yahoo calls. 14 tickers hammered back-to-back is a
# meaningful share of the same rate limit the options tooling needs.
FETCH_GAP_SEC = 1.2     # was 0.6. The shared-signal refactor added the weekly,
                        # earnings and regime fetches the scanner previously
                        # skipped, taking a scan from ~1 call per ticker to ~3
                        # plus one SPY call — roughly 3x the Yahoo traffic.
                        # At 8 tickers that is ~25 calls; 1.2s spacing spreads
                        # them over ~30s, which is nothing against the job's
                        # timeout and cheap insurance against a limiter trip.
                        # Rate limiting costs a whole scan cycle; 15 extra
                        # seconds costs nothing.

# Retry/backoff for Yahoo calls. Every OTHER Yahoo-calling function across this
# project (the app's get_data, its options engine, exit_monitor.py) already
# retries with escalating backoff. get_data() and suggest_option() here did
# not — a single 429 failed the ticker outright with no second attempt. That
# gap, combined with GitHub Actions running from a shared datacenter IP range
# (heavily used by other jobs hitting Yahoo the same hour), is what produced
# "always rate limited": no retry meant no chance to wait out a busy moment.
YF_RETRY_ATTEMPTS = 4
YF_RETRY_DELAY    = 3.0    # seconds; doubles each attempt (3 -> 6 -> 12)


def _is_rate_limit_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "too many requests" in msg or "rate limit" in msg or "429" in msg


MARKET_HOLIDAYS = {
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27",
    "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}
MARKET_HALF_DAYS = {"2025-07-03", "2025-11-28", "2025-12-24",
                    "2026-11-27", "2026-12-24",
    "2027-11-26",}


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


def is_post_close_window(now: datetime | None = None) -> bool:
    """
    The POST_CLOSE_WINDOW_HRS after the close on a trading day — when today's
    bar is settled and the market is shut. This is when --record-only runs.

    Same calendar as is_market_open(): weekday, not a holiday, and a half day
    closes at 13:00. Strictly AFTER the close: at 16:00:00 exactly the session
    is still "open" to is_market_open(), and the two must never both be true.
    """
    now = now or datetime.now(ET)
    if now.weekday() >= 5:
        return False
    day = now.strftime("%Y-%m-%d")
    if day in MARKET_HOLIDAYS:
        return False
    ch = 13 if day in MARKET_HALF_DAYS else 16
    close = now.replace(hour=ch, minute=0, second=0, microsecond=0)
    return close < now <= close + timedelta(hours=POST_CLOSE_WINDOW_HRS)


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


def prune_state(state: dict, now: float | None = None,
                max_age_days: int = STATE_MAX_AGE_DAYS) -> int:
    """
    Drop cooldown stamps older than `max_age_days`. Returns how many went.

    Every value in scanner_state.json is an epoch: "TICKER:Trend" for the
    alert cooldown, "SCANNER:OUTAGE" and "SCANNER:COVERAGE" for the
    operational ones. The longest cooldown is 20 hours, so a stamp a month
    old gates nothing — it is just a line the file carries forever, and the
    file is committed on every run. Entries from August were still there in
    October. A value that is not a number is left alone rather than guessed
    at.
    """
    now = time.time() if now is None else now
    cutoff = now - max_age_days * 86400
    gone = [k for k, v in state.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
            and v < cutoff]
    for k in gone:
        del state[k]
    return len(gone)


def is_total_outage(failed: list, watchlist: list) -> bool:
    """
    Every ticker failed to return usable data — the scan did not happen.

    Kept separate from the alerting so it can be tested. An empty watchlist is
    NOT an outage: nothing was asked for, so nothing failing is consistent.
    Without that guard a misconfigured watchlist would page you every run.
    """
    return bool(watchlist) and len(failed) == len(watchlist)


def alerted_on_bar(signal_key: str | None) -> bool:
    """
    Whether the record already holds a DELIVERED alert for this signal key
    (logic review 2026-10-29, L1; owner: "cooldown by signal key"). The
    4-hour stamp equals the spacing of the 11:07 and 15:07 dispatches and
    held on 10-29 by 7 and 8 seconds; a dispatch a few seconds later would
    have sent the same bar's alert twice. The signal's identity is its key
    (ticker|trend|bar_date), which the alert row carries, so the same bar is
    never alerted twice whatever the clock says. A failed delivery has no
    delivered row and is re-sent, as before; the stamp stays as the
    Telegram-outage backstop. A missing key (no bar date) never suppresses.
    """
    if not signal_key:
        return False
    try:
        return any(a.get("key") == signal_key and a.get("delivered")
                   for a in forward_log.alerts_sent())
    except Exception as exc:                               # noqa: BLE001
        logger.warning("alert rows unreadable (%s) — the cooldown stamp alone applies", exc)
        return False


def gapped_at_fill(r: dict, open_px, last_px=None) -> str | None:
    """
    THE FILL CHECK THE GRADER APPLIES, asked BEFORE the alert goes out
    (review 2026-10-30, J1). The grader voids a row `gapped_before_fill`
    when the next open is at or beyond the stop or the target, and the
    alert path never asked: on 2026-10-29 the 11:07 scan alerted NOW's
    10-28 bar with the stock open 27 % below the recorded stop after the
    release, and the 16:09 pass voided the same row. The alert said "buy
    at 149.85, stop 142.34" of a stock trading at 109. Asked of the first
    bar after the signal bar (`open_px`, the fill) and, when the run has
    one, the latest price (`last_px`): a stock that opened inside the
    levels and has since run through them is the same trade gone. Returns
    the reason to withhold, or None to send. Unreadable prices never
    withhold (the grader still voids).

    THE GRADER IS ASKED, NOT COPIED (review 2026-11-03, N1). This used to
    restate the gap rule as `px <= stop or px >= target` on the raw open.
    The grader's void has two more legs: the 2 bps slippage on the fill,
    and the risk floor (grading amendment 2026-10-29, L2), which voids a
    fill within max($0.05, 0.3 %) of the stop. The floor landed a day
    before this gate and was never wired into it, so an open in that band
    (stop 97.50, open 97.55-97.70) was SENT and then voided at 16:09, the
    exact NOW sequence J1 exists to stop. `_grader_voids_fill()` hands
    the price to forward_log.resolve_signal() itself, so every leg the
    grader has, or is amended to have, is the gate's leg too.
    """
    try:
        stop, target = float(r["stop"]), float(r["target"])
    except (KeyError, TypeError, ValueError):
        return None
    long = r.get("trend") == "Bullish"
    for label, px in (("fill (next open)", open_px), ("last price", last_px)):
        try:
            px = float(px)
        except (TypeError, ValueError):
            continue
        if px != px or px <= 0:                             # NaN, or no price
            continue
        why = _grader_voids_fill(long, stop, target, px)
        if why:
            return (f"{label} {px:.2f} {why} (stop {stop:.2f}, target {target:.2f}) "
                    f"— the grader would void this fill")
    return None


def _grader_voids_fill(long: bool, stop: float, target: float, px: float) -> str | None:
    """
    Whether forward_log.resolve_signal() would VOID a fill at `px`
    (gapped_before_fill, either note), asked of the grader itself on a
    one-bar probe: a signal bar and one bar after it that opens, trades
    and closes at `px`. The recorded close and the probe signal bar's
    close are the same number, so the split check can never fire; the
    grader reads nothing else of them. None when the grader would take
    the fill (it then walks the bar, which is not this question).
    """
    ref = stop                                          # any positive close; see above
    row = {"trend": "Bullish" if long else "Bearish", "bar_date": "2000-01-03",
           "setup": {"price": ref, "stop": stop, "target": target}}
    bars = [{"date": "2000-01-03", "open": ref, "high": ref, "low": ref, "close": ref},
            {"date": "2000-01-04", "open": px, "high": px, "low": px, "close": px}]
    try:
        v = forward_log.resolve_signal(row, bars, today=date(2000, 1, 4))
    except Exception:                                   # noqa: BLE001
        return None
    if not v or v.get("outcome") != "void" or v.get("reason") != "gapped_before_fill":
        return None
    if (px <= stop) if long else (px >= stop):
        return "is beyond the stop"
    if (px >= target) if long else (px <= target):
        return "is beyond the target"
    d = v.get("detail") or {}
    if d.get("note") == "fill inside the risk floor":
        return (f"is inside the risk floor of the stop (risk {d.get('fill_risk', 0):.2f} "
                f"< floor {d.get('risk_floor', 0):.2f})")
    return "is within the fill slippage of the target"


def fill_probe(raw_df, cdf) -> tuple:
    """
    The first bar after the signal bar in the RAW frame (the one
    drop_partial_bar() trimmed), as (open, last). During the session that
    is TODAY'S PARTIAL BAR, whatever pass recorded the signal bar (the
    signal bar is always the last settled one, so the bar after it is the
    session in progress): its Open is the fill the grader will read and
    its Close is the latest print. (None, None) when the frame ends on the
    signal bar (the post-close pass: nothing has traded since, the fill is
    tomorrow's open and cannot be checked yet).
    """
    try:
        last_sig = sc._bar_dates(cdf, None).max()
        raw_dates = sc._bar_dates(raw_df, None)
        after = raw_df[raw_dates > last_sig]
        if after.empty:
            return None, None
        return float(after.iloc[0]["Open"]), float(after.iloc[-1]["Close"])
    except Exception:                                      # noqa: BLE001
        return None, None


def at_send(r: dict, open_px, last_px) -> dict | None:
    """
    WHERE THE TRADE STANDS WHEN THE ALERT GOES OUT (review 2026-11-03, N2).
    Every level in an alert is computed at the signal bar's CLOSE; the
    alert is sent at 11:07 ET the next session, and the record grades the
    trade from that session's OPEN. Neither is a price the owner can fill
    at. In the record's first 7 graded HQ alerts the open alone had moved
    WMT +0.84 R and V +0.60 R toward the target, leaving R:R 0.24 and 0.65
    at the fill against the tier's 1.0. This says, in R of the planned
    risk, how far the stock has already moved, and the R:R from the latest
    price to the same stop and target. Display and measurement only: it
    withholds nothing (the fill gate above does that) and changes no level.
    None without a usable latest price.
    """
    try:
        entry, stop, target = float(r["entry"]), float(r["stop"]), float(r["target"])
    except (KeyError, TypeError, ValueError):
        return None
    long = r.get("trend") == "Bullish"
    def _px(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v if v == v and v > 0 else None
    o, last = _px(open_px), _px(last_px)
    now = last if last is not None else o
    plan = (entry - stop) if long else (stop - entry)
    if now is None or plan <= 0:
        return None
    risk_now = (now - stop) if long else (stop - now)
    reward_now = (target - now) if long else (now - target)
    rr_now = (reward_now / risk_now
              if risk_now >= risk_params.min_risk(now) and reward_now > 0 else None)
    return {"open": o, "last": last, "moved_r": round(((now - entry) if long else (entry - now)) / plan, 2),
            "rr_now": None if rr_now is None else round(rr_now, 2),
            "below_hq": rr_now is None or not sc.gate_rr(rr_now, PARAMS.hq_min_rr)}


def earnings_cap_dte(r: dict, today) -> int | None:
    """
    D1 (review 2026-11-03; owner: "go with D1"): the latest DTE whose
    structure exit (dte_exit days before expiry) still falls BEFORE the next
    release: (release - today) + dte_exit - 1. A contract expiring later is
    held through the release (event IV paid at entry, the crush after it),
    which the measured structure never priced. Conservative by a day: the
    calendar does not say whether the release is before the open or after
    the close. None when the next release is unknown (fails open, and the
    alert says so).
    """
    ne = r.get("next_earnings")
    if not ne:
        return None
    try:
        ned = date.fromisoformat(str(ne)[:10])
    except ValueError:
        return None
    return (ned - today).days + int(risk_params.MEASURED_OPTION_STRUCTURE["dte_exit"]) - 1


def withhold_before_send(r: dict, today) -> str | None:
    """
    The reason to withhold an alert that passed the fill gate, or None to
    send (review 2026-11-03; owner: "go with D1, D2, D4 and D5"). Asked after
    at_send() and before any chain fetch; a withheld alert records ONE
    "NOT SENT" row per key, as the fill gate does, and the record (the row,
    its tag, the grader) is untouched.

    D4: the R:R from the price at send is under the tier's minimum. The tier
        asks R:R at the signal close, a price no one can fill at; this asks
        it where the owner would enter. No readable price: send.
    D1: the next release is so close that every contract in the structure's
        window would be held through it (its dte_exit falls on or after the
        release). A later release only narrows the expiries the alert may
        name (earnings_cap_dte, in compose_alert).
    """
    a = r.get("at_send")
    if a and a.get("below_hq"):
        rr = a.get("rr_now")
        return (f"R:R from the price at send {'n/a' if rr is None else f'{rr:.2f}'} is under the "
                f"tier's {PARAMS.hq_min_rr:.1f} (it was {r.get('rr')} at the signal close)")
    cap = earnings_cap_dte(r, today)
    if cap is not None and cap < OPT_MIN_DTE:
        _dx = int(risk_params.MEASURED_OPTION_STRUCTURE["dte_exit"])
        return (f"earnings {r.get('next_earnings')} is {(cap - _dx + 1)}d away: every {OPT_MIN_DTE}-"
                f"{OPT_MAX_DTE} DTE contract would be held through it (its {_dx}-DTE exit falls on "
                f"or after the release)")
    return None


def withheld_on_bar(signal_key: str | None) -> bool:
    """Whether the record already holds a NOT SENT row for this key (J1): the
    withheld alert is recorded once, not on every later run of the day."""
    if not signal_key:
        return False
    try:
        return any(a.get("key") == signal_key and str(a.get("header", "")).startswith("NOT SENT")
                   for a in forward_log.alerts_sent())
    except Exception:                                      # noqa: BLE001
        return False


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
    """
    Delegates to notify.py, which retries transient failures.

    This used to be a bare requests.post with no retry: a single blip between
    the Actions runner and Telegram dropped the alert silently, and "it did
    not send" looked exactly like "there was nothing to send".
    """
    return notify.send(message, dry_run=dry_run)


# ══════════════════════════════════════════════════════════════════
# DATA + INDICATORS
# ══════════════════════════════════════════════════════════════════
def _yf_download_with_retry(ticker: str, period: str, interval: str) -> pd.DataFrame | None:
    """
    BUG FIX: previously called yf.download() ONCE with no retry, unlike every
    other Yahoo-calling function in this project. A single 429 failed the
    ticker outright. Now retries with escalating backoff (3s -> 6s -> 12s)
    before giving up, matching get_data() in app.py and exit_monitor.py.

    This is the Yahoo LEG only — wrapped so data_source.fetch_daily() can try
    a second provider when Yahoo keeps coming back empty after every retry,
    instead of the whole scan failing on that ticker.
    """
    delay = YF_RETRY_DELAY
    last_err = None
    for attempt in range(YF_RETRY_ATTEMPTS):
        try:
            return yf.download(ticker, period=period, interval=interval,
                               progress=False, auto_adjust=False)
        except Exception as e:
            last_err = e
            if _is_rate_limit_error(e) and attempt < YF_RETRY_ATTEMPTS - 1:
                logger.warning("Rate limited get_data(%s); backoff %ss "
                               "(attempt %d/%d)", ticker, delay, attempt + 1,
                               YF_RETRY_ATTEMPTS)
                time.sleep(delay)
                delay *= 2
                continue
            logger.warning("get_data(%s) Yahoo leg failed: %s", ticker, e)
            return None
    logger.warning("get_data(%s) exhausted Yahoo retries: %s", ticker, last_err)
    return None


def _yf_download_nonempty(ticker: str, period: str, interval: str) -> pd.DataFrame | None:
    """
    The SPY regime's Yahoo leg, retried on an EMPTY frame as well as on a
    raised rate limit (review 2026-11-03, N8). yfinance 1.x returns an empty
    frame when throttled instead of raising, so the retry above never ran
    for it, and one throttled SPY call turned the regime leg off for every
    alert of the run ("Regime filter disabled"). app.py fixed its copy of
    this; the scanner's did not. Used for the one fetch that gates a whole
    run, not per ticker: a throttled pool would otherwise wait out the
    backoff eighty times.
    """
    delay = YF_RETRY_DELAY
    out = None
    for attempt in range(YF_RETRY_ATTEMPTS):
        out = _yf_download_with_retry(ticker, period, interval)
        if out is not None and not getattr(out, "empty", True):
            return out
        if attempt < YF_RETRY_ATTEMPTS - 1:
            logger.warning("Empty frame for %s (likely throttled); backing off %ss", ticker, delay)
            time.sleep(delay)
            delay *= 2
    return out


def get_data(ticker: str) -> pd.DataFrame | None:
    """
    Yahoo first, then data_source's fallback (Tiingo, if TIINGO_API_KEY is
    set) when Yahoo comes back empty after every retry above. Before this,
    the scanner had no fallback at all — only app.py did — so a Yahoo outage
    or throttle took the scanner down even on days the app kept working via
    Stooq/Tiingo. See data_source.py's docstring for what the fallback does
    and does not cover.
    """
    df, source = data_source.fetch_daily(
        ticker, period=FETCH_PERIOD, interval="1d",
        yahoo_fetch=lambda t, p, i: _yf_download_with_retry(t, p, i))
    if df is None or df.empty:
        return None
    if source != "yahoo":
        logger.info("%s: Yahoo unavailable, used %s fallback", ticker, source)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    # BACKLOG 36 group A: one validator, a refusal with its reason. A frame
    # with a NaN close, a negative price or a duplicated date is not scanned
    # and the ticker counts as failed, which run() surfaces.
    _why = sc.validate_bars(df)
    if _why:
        logger.warning("%s — bars refused: %s", ticker, _why)
        return None
    return df


def bars_for_grading(df: pd.DataFrame, now: datetime | None = None) -> list:
    """
    A raw OHLC frame as the plain rows forward_log.resolve_signal() reads:
    [{"date": "YYYY-MM-DD", "open", "high", "low", "close"}, ...], ascending.

    SETTLED BARS ONLY (external review 2026-10-19, BACKLOG 35). The frame the
    scan fetched mid-session still carries today's forming bar, and the
    grader read it: a 20-bar timeout was marked at an 11:10 ET price, and a
    level touched before noon was graded as if the bar had closed. Today's
    bar is dropped here until the session closes, so an outcome that forms
    intraday is written on the next run after the bar settles. The resolver
    and its rules are unchanged; only the bars it is shown are.

    forward_log is stdlib-only by design (it is the tamper-evident record and
    must stay importable anywhere), so pandas stops here.
    """
    df, _dropped = sc.drop_partial_bar(df, now=now if now is not None else datetime.now(ET))
    _why = sc.validate_bars(df)
    if _why:
        # The grader must not see a NaN low (it never touches a stop) or an
        # unordered frame; grade_open_signals() counts the raise as a failed
        # fetch and says why (BACKLOG 36 group A).
        raise ValueError(f"bars refused for grading: {_why}")
    out = []
    dates = sc._bar_dates(df, None)
    for d, (o, h, l, c) in zip(dates, df[["Open", "High", "Low", "Close"]].itertuples(index=False)):
        out.append({"date": str(pd.Timestamp(d).date()), "open": float(o),
                    "high": float(h), "low": float(l), "close": float(c)})
    out.sort(key=lambda b: b["date"])
    return out


def fetch_atm_chain(ticker: str, today=None, min_dte: int | None = None) -> dict | None:
    """
    The nearest expiry inside OPT_MIN_DTE..OPT_MAX_DTE and its calls/puts as
    plain rows for iv_snapshot.atm_iv_from_chain(). One expiries call, one
    chain call, with the same rate-limit backoff suggest_option() uses.
    Returns {"expiry", "dte", "calls", "puts"} or None. With `min_dte`, the
    FIRST listed expiry at least that many calendar days out instead (the
    spread arm's expiry rule, results/spread_preregistration.md), and the
    rows carry bid/ask when the chain has them.
    """
    today = today or option_chain.et_today()     # ET, not the host clock (N3)
    try:
        stock = yf.Ticker(ticker)
        expiries, delay = None, YF_RETRY_DELAY
        for attempt in range(YF_RETRY_ATTEMPTS):
            try:
                expiries = list(stock.options or []); break
            except Exception as e:                      # noqa: BLE001
                if _is_rate_limit_error(e) and attempt < YF_RETRY_ATTEMPTS - 1:
                    time.sleep(delay); delay *= 2; continue
                logger.warning("%s expiries fetch failed: %s", ticker, e)
                return None
        pick = None
        for exp in expiries or []:
            try:
                dte = (pd.Timestamp(exp) - today).days
            except Exception:                           # noqa: BLE001
                continue
            if min_dte is not None:
                if dte >= min_dte:
                    pick = (exp, dte); break
                continue
            if option_chain.in_dte_window(dte, OPT_MIN_DTE, OPT_MAX_DTE):
                pick = (exp, dte); break
        if pick is None:
            return None
        chain, delay = None, YF_RETRY_DELAY
        for attempt in range(YF_RETRY_ATTEMPTS):
            try:
                time.sleep(0.5); chain = stock.option_chain(pick[0]); break
            except Exception as e:                      # noqa: BLE001
                if _is_rate_limit_error(e) and attempt < YF_RETRY_ATTEMPTS - 1:
                    time.sleep(delay); delay *= 2; continue
                logger.warning("%s chain %s failed: %s", ticker, pick[0], e)
                return None
        if chain is None:
            return None
        def _rows(df):
            if df is None or df.empty or "impliedVolatility" not in df.columns:
                return []
            cols = ["strike", "impliedVolatility"] + [c for c in ("bid", "ask") if c in df.columns]
            return df[cols].to_dict("records")
        return {"expiry": str(pick[0])[:10], "dte": pick[1],
                "calls": _rows(chain.calls), "puts": _rows(chain.puts)}
    except Exception as e:                              # noqa: BLE001
        logger.warning("%s ATM chain failed: %s", ticker, e)
        return None


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
    df["VOL_AVG20"] = df["Volume"].rolling(20).mean()
    df = df.dropna(subset=["EMA20", "EMA50", "MACD", "Signal", "RSI", "ATR", "ADX", "VOL_AVG20"])
    # Discard the unconverged head so EMA50/MACD/ADX are trustworthy
    if len(df) > INDICATOR_WARMUP_BARS + MIN_BARS_AFTER_WARMUP:
        df = df.iloc[INDICATOR_WARMUP_BARS:]
    return df


# ══════════════════════════════════════════════════════════════════
# OPTION SUGGESTION
#
# Picks a liquid, near-the-money contract for the signal so the alert tells you
# WHAT to buy, not just which way to lean. Ported from the app's selection
# logic, with two differences: the DTE window is pinned to the range the
# backtest used, and cost is reported against your account so you can see
# affordability at a glance.
#
# Called ONLY after the cooldown check passes, so suppressed alerts never spend
# option-chain API calls. That ordering matters: chains are the single most
# rate-limit-expensive thing this script can do.
# ══════════════════════════════════════════════════════════════════
def suggest_option(ticker: str, price: float, trend: str,
                   atr: float, strength: str = "Normal",
                   chain_data: dict | None = None,
                   target: float | None = None,
                   max_dte: int | None = None) -> dict | None:
    """
    The contract an alert names. The FETCH is this module's (its retry and
    rate-limit backoff, its 21-45 DTE window, at most OPT_MAX_EXPIRIES
    expiries: the policy inputs the alert text states). The SELECTION is
    option_chain.get_option_data(), the one selector the app reads, so the
    scanner and the app name the same strike on the same chain (external
    review 2026-10-20, group C: "scanner 103 versus app 100 on one fixture";
    this module ranked by its own score). `chain_data` is injectable for the
    selftest, in option_chain's shape.
    """
    try:
        if chain_data is None:
            chain_data = fetch_chain_data(ticker, max_dte=max_dte)
        if not chain_data or chain_data.get("error") or not chain_data.get("expiries"):
            return None
        budget = risk_params.option_budget(ACCOUNT_SIZE, OPTION_BUDGET_PCT)
        pick = option_chain.get_option_data(
            ticker, price, trend, strength, OPT_MIN_DTE, PARAMS.atr_tgt_mult,
            budget / 100.0, is_market_open, atr=atr, chain_data=chain_data,
            target=target)                       # D2: never a strike beyond the target
        if not pick or pick.get("error"):
            if pick and pick.get("error"):
                logger.info("%s — no contract: %s", ticker, pick["error"])
            return None
        mid = float(pick["mid"])
        spread = float(pick["spread"])
        bid, ask = mid - spread / 2.0, mid + spread / 2.0
        cost = mid * 100
        return {
            "right": pick["label"], "strike": float(pick["strike"]),
            "expiry": pick["expiry"], "dte": int(pick["dte"]), "mid": round(mid, 2),
            "bid": round(bid, 2), "ask": round(ask, 2),
            "spread_pct": round(spread / mid * 100, 1) if mid else 0.0,
            "moneyness_pct": pick.get("moneyness_pct"),
            "volume": int(pick.get("volume") or 0), "oi": int(pick.get("oi") or 0),
            "cost": round(cost, 2),
            "pct_account": round(cost / ACCOUNT_SIZE * 100, 1),
            "within_budget": cost <= budget,
            "budget": round(budget, 2),
            "dte_adequate": bool(pick.get("dte_adequate", True)),
            "days_needed": int(pick.get("days_needed") or 0),
        }
    except Exception as e:
        logger.warning("suggest_option(%s) failed: %s", ticker, e)
        return None


def fetch_chain_data(ticker: str, max_dte: int | None = None) -> dict | None:
    """
    The chain in option_chain.get_option_data()'s injectable shape, fetched
    with this module's retry and backoff and bounded to the alert window
    (OPT_MIN_DTE..OPT_MAX_DTE, OPT_MAX_EXPIRIES expiries).
    """
    stock = yf.Ticker(ticker)
    expiries = None
    delay = YF_RETRY_DELAY
    for attempt in range(YF_RETRY_ATTEMPTS):
        try:
            expiries = list(stock.options or [])
            break
        except Exception as e:
            if _is_rate_limit_error(e) and attempt < YF_RETRY_ATTEMPTS - 1:
                logger.warning("Rate limited options(%s); backoff %ss", ticker, delay)
                time.sleep(delay)
                delay *= 2
                continue
            logger.warning("%s expiries fetch failed: %s", ticker, e)
            return None
    if not expiries:
        return None
    today = option_chain.et_today()               # ET, not the host clock (N3)
    out, checked = [], 0
    for exp in expiries:
        if checked >= OPT_MAX_EXPIRIES:
            break
        try:
            dte = (pd.Timestamp(exp) - today).days
        except Exception:
            continue
        if not option_chain.in_dte_window(dte, OPT_MIN_DTE, OPT_MAX_DTE):
            continue
        if max_dte is not None and dte > max_dte:      # D1: its exit must come before the release
            continue
        checked += 1
        chain = None
        c_delay = YF_RETRY_DELAY
        for c_attempt in range(YF_RETRY_ATTEMPTS):
            try:
                time.sleep(0.5)
                chain = stock.option_chain(exp)
                break
            except Exception as e:
                if _is_rate_limit_error(e) and c_attempt < YF_RETRY_ATTEMPTS - 1:
                    logger.warning("Rate limited chain %s %s; backoff %ss",
                                   ticker, exp, c_delay)
                    time.sleep(c_delay)
                    c_delay *= 2
                    continue
                logger.warning("%s chain %s failed: %s", ticker, exp, e)
                chain = None
                break
        if chain is None:
            continue
        out.append({"expiry": exp, "dte": int(dte),
                    "calls": chain.calls.fillna(0), "puts": chain.puts.fillna(0)})
    return {"error": None, "expiries": out} if out else None


# ══════════════════════════════════════════════════════════════════
# ANALYSIS
# ══════════════════════════════════════════════════════════════════
def get_weekly_trend(ticker: str) -> str | None:
    """
    Weekly-timeframe direction, for the multi-timeframe filter.

    Now delegates to market_context — the SAME rule app.py uses. app.py used to
    run an EMA10w-vs-EMA20w crossover here while this file used price-vs-EMA20w:
    different questions, opposite verdicts on ordinary pullbacks, on a BLOCKING
    filter. Returns None on any failure — signal_core treats None as blocking
    rather than guessing.
    """
    def _fetch(t, period, interval):
        time.sleep(FETCH_GAP_SEC)
        df, source = data_source.fetch_daily(
            t, period=period, interval=interval,
            yahoo_fetch=lambda tt, pp, ii: yf.download(
                tt, period=pp, interval=ii, progress=False, auto_adjust=False))
        if df is not None and source != "yahoo":
            logger.info("%s weekly: Yahoo unavailable, used %s fallback",
                        t, source)
        return df
    return market_context.get_weekly_trend(ticker, _fetch)


def _earnings_calendar(ticker: str):
    """The raw yfinance calendar payload, behind this module's fetch gap."""
    time.sleep(FETCH_GAP_SEC)
    return yf.Ticker(ticker).calendar


def check_earnings_blackout(ticker: str, bar_date=None) -> tuple[bool, str]:
    """
    ROUTED THROUGH market_context — one implementation, as with the weekly
    trend and the SPY regime. `bar_date` is the signal bar's date: a release
    between it and the fill is inside the blackout (review 2026-10-30 J2).

    This copy and app.py's parsed the yfinance calendar differently: app read
    only the FIRST earnings date, this one iterated all of them. yfinance
    routinely returns a RANGE of two estimates for one event, so the two could
    reach opposite verdicts on the same ticker at the same moment. Reading
    every date — the behaviour this copy already had — is what the shared rule
    keeps, because a calendar entry inside the window should block whichever
    position it holds in the list.

    Still ET, still fails open. `today` is passed explicitly because
    market_context refuses to default it: a host-clock default is the bug this
    repo has already fixed twice, and the runners here are UTC.
    """
    _today = datetime.now(ET).date()
    return market_context.get_earnings_blackout(
        ticker, lambda t: _remembered_calendar(t, _today), _today,
        blackout_days=EARNINGS_BLACKOUT_DAYS, post_days=POST_EARNINGS_DAYS, bar_date=bar_date)


def _remembered_calendar(ticker: str, today) -> list:
    """
    The calendar's dates plus the releases already seen for `ticker` (N8),
    via market_context.remember_release_dates(), which owns the window.
    A failed calendar fetch still returns the remembered dates when there
    are any (a release seen yesterday is still a release); with none, the
    failure is raised so the gate says "unavailable", as before.
    """
    try:
        dates = market_context.calendar_dates(_earnings_calendar(ticker))
    except Exception:                                  # noqa: BLE001
        mem = market_context.remember_release_dates(_EARNINGS_SEEN, ticker, [], today,
                                                    blackout_days=EARNINGS_BLACKOUT_DAYS,
                                                    post_days=POST_EARNINGS_DAYS)
        _EARNINGS_LAST[ticker] = mem or None
        if not mem:
            raise
        return mem
    out = market_context.remember_release_dates(_EARNINGS_SEEN, ticker, dates, today,
                                                blackout_days=EARNINGS_BLACKOUT_DAYS,
                                                post_days=POST_EARNINGS_DAYS)
    _EARNINGS_LAST[ticker] = out
    return out


def get_spy_regime() -> dict | None:
    """
    SPY vs its 200-SMA — the macro regime gate, via market_context so app.py
    and this file cannot diverge. Two-state and ungated, matching
    backtest.build_regime_series(), which is the version the 591-trade
    out-of-sample test actually ran (oos_validate FROZEN use_regime=True).

    Returns None when the regime is Unknown, preserving this function's
    previous contract with run(): signal_core skips the filter on a falsy
    spy_regime, which is the right response to "no data".
    """
    def _fetch(t, period, interval):
        time.sleep(FETCH_GAP_SEC)
        spy, source = data_source.fetch_daily(
            t, period=period, interval=interval,
            yahoo_fetch=lambda tt, pp, ii: _yf_download_nonempty(tt, pp, ii))
        if spy is not None and source != "yahoo":
            logger.info("SPY regime: Yahoo unavailable, used %s fallback", source)
        return spy
    r = market_context.get_spy_regime(_fetch)
    if r.get("regime") in (None, "Unknown"):
        logger.warning("SPY regime unavailable (%s)", r.get("reasoning"))
        return None
    return r


def control_bars(df: pd.DataFrame, bar_date) -> list:
    """
    The settled bars strictly BEFORE `bar_date`, ascending, as the plain
    {"date", "close"} rows forward_log.record_control() draws from (amendment
    2026-10-28 A). The frame is the one the scan already holds: no fetch.
    """
    _dates = sc._bar_dates(df, None)
    _cut = pd.Timestamp(str(bar_date)[:10])
    out = [{"date": str(pd.Timestamp(d).date()), "close": float(c)}
           for d, c in zip(_dates, df["Close"].tolist()) if pd.Timestamp(d) < _cut]
    out.sort(key=lambda r: r["date"])
    return out


def analyze(df: pd.DataFrame, ticker: str,
            spy_regime: dict | None = None,
            tally: dict | None = None, bucket: str = "live",
            rs: float | None = None) -> dict | None:
    """
    Thin adapter over signal_core.evaluate().

    THIS USED TO BE A SECOND IMPLEMENTATION. It applied four gates — trend
    stack, MACD, ADX, R:R — while app.py applied those plus volume, weekly
    alignment, earnings blackout and macro regime. The scanner was structurally
    looser, so on 2026-08-25 it alerted on TGT, ABT and TMO while the app
    rejected all three. Syncing the CONSTANTS did not help, because the LOGIC
    was duplicated.

    Now there is one implementation and this function only translates its
    output into the shape send_alert() expects. Returns None when no tradeable
    signal fires, matching the previous contract.

    `tally`, when given, has its "signals" count incremented once for every
    bar on which THE RULES PRODUCED A SIGNAL — taken or skipped — so run() can
    write that count on the bar's `scan` row. The return value cannot carry
    it: a skipped signal returns None, exactly like a blocked bar.
    """
    if len(df) < MIN_BARS_AFTER_WARMUP:
        logger.info("%s — only %d usable bars after warm-up; skipping.",
                    ticker, len(df))
        return None

    # THE BASE CONDITION FIRST (review 2026-10-13). evaluate() needs the
    # earnings calendar, one network call a name, and the pool loop runs this
    # for ~80 names a session; most fail the base condition, which is pure
    # arithmetic on the last bar. Same verdict as evaluate()'s "blocked:
    # base", from the function evaluate() itself calls, so nothing is fetched
    # for a bar that could not signal.
    if all(c in df.columns for c in ("EMA20", "EMA50", "MACD", "Signal", "RSI")) \
            and sc.base_trend(df.iloc[-1], PARAMS) is None:
        logger.debug("%s — no signal (base)", ticker)
        return None

    try:                                               # the signal bar, for the blackout's straddle rule (J2)
        _bar_for_earnings = sc._bar_dates(df, None).max().date()
    except Exception:                                  # noqa: BLE001
        _bar_for_earnings = None
    r = sc.evaluate(
        df, ticker, PARAMS,
        weekly_trend=get_weekly_trend(ticker) if PARAMS.weekly_confirm else None,
        earnings=check_earnings_blackout(ticker, bar_date=_bar_for_earnings),
        spy_regime=spy_regime,
    )
    if r["blocked"]:
        logger.debug("%s — no signal (%s)", ticker, r.get("block_reason"))
        return None

    if tally is not None:
        tally["signals"] = tally.get("signals", 0) + 1

    # FROM HERE A SIGNAL EXISTS. Every one is recorded, whatever happens to it
    # next — a log of only the alerts that went out describes what the filters
    # let through, not what the rules produced, and the gap between those two
    # is the thing worth measuring. Blocked bars are NOT logged: that is most
    # bars of most tickers and would bury the signals in noise.
    _setup = {k: r.get(k) for k in
              ("price", "entry", "stop", "target", "rr", "rsi", "adx", "atr",
               "strength", "high_quality", "filters_pass", "filters_total")}
    # ONE CONFIG DICT FOR EVERY ROW. This was a second literal beside
    # _scan_cfg(): when the fingerprint fields were completed on 2026-10-19
    # the scan rows moved to the new hash and the signal rows did not. Found
    # in the 2026-10-20 log; the selftest now asserts a recorded signal row
    # carries the same fingerprint as the scan row.
    _cfg = _scan_cfg()

    # THE HQ TIER IS A TAG ON THE RECORD, NOT A GATE ON IT. Owner decision
    # 2026-10-08, pre-registered in results/forward_record_preregistration.md:
    # two of the tier's legs (ADX, volume >= 1.2x) were tested here and
    # found non-predictive, and gating the record's `taken` set on them left
    # it at 0 taken in three weeks. The row now says what the rules produced
    # AND what the gate would have selected. Alerts still fire on the tier.
    _hq_fail = []
    if not sc.gate_rr(r.get("rr_raw", r.get("rr") or 0), PARAMS.hq_min_rr):
        _hq_fail.append(f"rr<{PARAMS.hq_min_rr:g}")
    if PARAMS.hq_requires_strong and r.get("strength") != "Strong":
        _hq_fail.append("not Strong")          # a leg only while the tier requires the tag (not since amendment G)
    _hq_fail += [n for n, f in (r.get("filters") or {}).items()
                 if not (f or {}).get("pass")]
    _setup["hq_fail"] = _hq_fail
    # LEGS THAT PASSED UNCHECKED (review 2026-11-03, N8). Both external legs
    # of the tier fail OPEN: no SPY regime reads "filter disabled" and no
    # calendar reads "proceed with caution", and both pass. The row now says
    # which legs were not checked, so a tier reading can tell checked rows
    # from unchecked ones. Nothing is gated on it.
    _unchecked = []
    if PARAMS.spy_regime_on and not spy_regime:
        _unchecked.append("regime")
    if market_context.earnings_unknown(((r.get("filters") or {}).get("Earnings Blackout") or {}).get("detail")):
        _unchecked.append("earnings")
    _setup["unchecked"] = _unchecked
    if rs is not None:
        # D8 (review 2026-11-03): the pool ranks its alerts by RS; the number
        # is on the row so the cut can be audited (it never was).
        _setup["rs"] = rs
    _today_et = datetime.now(ET).date()
    _next_er = min((d for d in (_EARNINGS_LAST.get(ticker) or []) if d >= _today_et), default=None)
    _hq = bool(r["high_quality"])
    # THE SIGNAL BAR'S DATE, once, for the row and for the alert. The alert
    # prints it so the owner can log the trade against exactly this signal
    # (journal_store.open_option_position(signal_key=...), BACKLOG 28).
    try:
        _bar_date = str(sc._bar_dates(df, None).max().date())
    except Exception:                                  # noqa: BLE001
        _bar_date = None

    def _log(decision: str, reason: str) -> None:
        # "taken" MEANS "THE RULES PRODUCED A TRADEABLE SIGNAL", not "a Telegram
        # message went out". This runs before run()'s alert cooldown and before
        # send_alert(), so a signal suppressed as a 4-hour duplicate, or lost to
        # a Telegram outage, is still recorded as taken. Since 2026-10-08 the
        # rules are the BASE rules: a long that fails the HQ tier is taken too,
        # with hq=False on the row.
        #
        # That is the right semantic for a record of what the RULES produced --
        # which is what this log is for -- but it is not what the word implies,
        # so it is written down here rather than left to be rediscovered from a
        # rate that does not match the alerts received.
        #
        # Never let a logging failure lose an alert. The record matters; it
        # does not matter more than the trade.
        try:
            # THE SIGNAL BAR'S DATE, not the time of the scan. This module runs
            # three times a trading day and drop_partial_bar() removes today's
            # in-progress bar, so all three runs evaluate THE SAME SETTLED BAR.
            # Without this the log recorded one row per SCAN — three per signal
            # per day — in a chain that is append-only and cannot be corrected.
            # record_signal() dedupes on it and returns None for a repeat.
            _bar = sc._bar_dates(df, None).max().date()
            # THE BAR REVISION NOTE (BACKLOG 39): this bar was recorded by an
            # earlier pass (the post-close one, ten minutes after the close).
            # If this read of the SAME settled bar differs, one `revision` row
            # says which fields moved; the recorded row stands (append-only).
            _prev = forward_log.find_signal(ticker, r["trend"], _bar)
            if _prev is not None:
                _rev = forward_log.record_revision(_prev, _setup, decision, _hq)
                if _rev is not None:
                    logger.info("%s — bar %s read differently from its recorded row (seq %s): "
                                "fields %s, decision %s, hq %s", ticker, _bar, _prev.get("seq"),
                                _rev["fields"], _rev["decision"], _rev["hq"])
            _row = forward_log.record_signal(ticker, r["trend"], _setup, decision,
                                             reason, _cfg, bar_date=_bar,
                                             hq=_hq, bucket=bucket)
            if _row is None:
                logger.debug("%s — %s bar %s already in the forward log",
                             ticker, r["trend"], _bar)
            elif decision == "taken":
                # THE CONTROL ROW (amendment 2026-10-28 A): one per taken row,
                # drawn from the 20 settled bars before the signal bar in the
                # frame already in hand. Its absence is logged, never fatal.
                if forward_log.record_control(_row, control_bars(df, _bar)) is None:
                    logger.info("%s — no control row for bar %s (fewer than %d settled "
                                "bars before it)", ticker, _bar, forward_log.CONTROL_LOOKBACK)
        except Exception as exc:                       # noqa: BLE001
            logger.warning("%s — forward log write failed: %s", ticker, exc)

    # DIRECTION GATE. Shorts measured worse than random entry, so the live
    # system does not send them. See risk_params.LONGS_ONLY for the numbers.
    # Logged at INFO, not debug: a dropped alert should be visible in the
    # scanner's own output, or "the scanner went quiet" and "the scanner is
    # suppressing half its signals" look identical from outside.
    _dir = risk_params.direction_blocked(r["trend"])
    if _dir:
        logger.info("%s — %s setup suppressed: %s", ticker, r["trend"], _dir)
        _log("skipped", "direction gate: shorts are switched off")
        return None

    # FROM HERE THE RULES PRODUCED A LONG BASE SIGNAL: recorded as taken,
    # whatever the tier says. The row's hq tag carries the tier's verdict.
    _log("taken", "")

    # Alerts fire on the high-quality tier only, exactly as app.py defines it.
    # This gate decides what is SENT. It no longer decides what is recorded.
    if not r["high_quality"]:
        logger.debug("%s — signal but not high-quality (rr %.2f, %s, "
                     "filters %d/%d; recorded, not alerted)", ticker, r["rr"],
                     r["strength"], r["filters_pass"], r["filters_total"])
        return None

    return {
        "ticker": ticker, "trend": r["trend"], "strength": r["strength"],
        "price": r["price"], "entry": r["entry"], "stop": r["stop"],
        "target": r["target"], "rr": r["rr"], "rsi": r["rsi"],
        "adx": r["adx"], "atr": r["atr"],
        "filters_pass": r["filters_pass"], "filters_total": r["filters_total"],
        "bar_date": _bar_date,
        "signal_key": (forward_log.signal_key(ticker, r["trend"], _bar_date)
                       if _bar_date else None),
        "unchecked": _unchecked,
        "next_earnings": _next_er.isoformat() if _next_er else None,
    }

def selftest() -> int:
    """
    Run the checks with the forward log pointed at a throwaway file.

    THE BUG THIS FIXES. analyze() writes to forward_log.LOG, and the
    direction-gate check below drives analyze() directly — so every selftest
    run appended two fabricated "ZZ" rows to the REAL forward_log.jsonl. Twenty
    two of them reached a commit before anyone noticed, and CI would have kept
    adding more on every push.

    A research log that quietly accumulates test fixtures is worse than no log:
    it looks like a record. The redirect has to wrap the WHOLE function, not
    the one block that happens to mention the log, because any check that
    drives analyze() writes as a side effect.
    """
    import tempfile as _tf, pathlib as _pl
    real = forward_log.LOG
    real_b = basket_log.LOG
    real_sp = spread_log.LOG
    try:
        forward_log.LOG = _pl.Path(_tf.mkdtemp()) / "selftest.jsonl"
        # basket_log.jsonl has the same exposure: run()'s post-close pass
        # records a basket for the newest REAL snapshot in universe_history.
        basket_log.LOG = _pl.Path(_tf.mkdtemp()) / "basket_selftest.jsonl"
        spread_log.LOG = _pl.Path(_tf.mkdtemp()) / "spread_selftest.jsonl"
        return _selftest_body()
    finally:
        forward_log.LOG = real
        basket_log.LOG = real_b
        spread_log.LOG = real_sp


def compose_alert(r: dict, tk: str, header: str = "🚨 TRADE ALERT") -> str:
    """The Telegram text for one HQ signal: the underlying's levels, the
    signal bar, and the suggested contract. One body for the watchlist and
    the pool bucket; only the header differs."""
    lines = [
        header,
        f"{r['ticker']} → {r['trend']} ({r['strength']})",
        f"Price: {r['price']} | RR: {r['rr']} | ADX: {r['adx']} | RSI: {r['rsi']}",
        f"Underlying: entry {r['entry']} | stop {r['stop']} | target {r['target']}",
        f"Signal bar: {r.get('bar_date') or '?'} — enter this date when "
        f"you log the trade as a system signal, so its outcome attaches "
        f"to exactly this signal.",
    ]
    _now = r.get("at_send")
    if _now:
        # N2: the levels above are the signal close's; this is the price now.
        _px = _now["last"] if _now.get("last") is not None else _now["open"]
        _o = _now.get("open")
        _rrn = _now.get("rr_now")
        lines.append(f"Now {_px:.2f} (open {'?' if _o is None else f'{_o:.2f}'}): "
                     f"{_now['moved_r']:+.2f} R from the signal close; R:R from here "
                     f"{'n/a' if _rrn is None else f'{_rrn:.2f}'} (at the close {r['rr']})")
        if _now.get("below_hq"):
            lines.append(f"⚠️ R:R from the current price is under the tier's {PARAMS.hq_min_rr:.1f}. "
                         f"The record grades this signal from today's open, not from your fill.")

    # Chain fetch happens here — AFTER the cooldown check — so
    # suppressed alerts cost no API calls.
    # THE STRIKE WINDOW IS CENTRED ON THE PRICE AT SEND (review 2026-11-03,
    # N4). The selector's window is "the ATM window" around the price it is
    # given, and the measured structure buys at the money AT THE FILL
    # (option_backtest: strike = round(the entry session's open)). This
    # passed the signal bar's close: a stock up 4 % by 11:07 was offered the
    # strike 4 % in the money, and on 10-29 NOW's window around 149.85
    # named 150 calls with the stock near 110. The latest price the fill
    # probe read is the centre; the close when the run has none.
    _now = r.get("at_send") or {}
    _centre = next((float(v) for v in (_now.get("last"), _now.get("open"))
                    if v is not None), float(r["price"]))
    _cap = earnings_cap_dte(r, datetime.now(ET).date())
    _cap = _cap if (_cap is not None and _cap < OPT_MAX_DTE) else None      # D1: binding only
    if "contract" in r:                  # D8: the pool loop fetched it to check tradeability
        opt = r["contract"]
    else:
        opt = suggest_option(tk, _centre, r["trend"], r["atr"], r.get("strength", "Normal"),
                             target=r.get("target"), max_dte=_cap) \
            if SUGGEST_OPTIONS else None

    if opt:
        lines += [
            "",
            f"📄 CONTRACT: {tk} {opt['expiry']} ${opt['strike']:g} {opt['right']}",
            f"Mid ${opt['mid']:.2f}  (bid {opt['bid']:.2f} / ask {opt['ask']:.2f}, "
            f"spread {opt['spread_pct']:.0f}%)",
            f"Cost ${opt['cost']:,.0f} per contract = {opt['pct_account']:.1f}% of account",
            f"{opt['dte']} DTE · vol {opt['volume']:,} · OI {opt['oi']:,}"
            + (f" · strike {opt['moneyness_pct']:+.1f}% vs price" if opt.get("moneyness_pct") is not None else ""),
        ]
        if not opt["within_budget"]:
            lines.append(f"⚠️ Above your ${opt['budget']:,.0f} risk budget "
                         f"({OPTION_BUDGET_PCT:g}% of ${ACCOUNT_SIZE:,}) — on a long "
                         f"option the premium IS the max loss.")
        if opt["spread_pct"] > OPT_MAX_SPREAD * 0.75:
            lines.append(f"⚠️ Spread {opt['spread_pct']:.0f}% of mid — a wide "
                         f"round trip can erase the edge on its own.")
        # THE RULES LINE READS THE STRUCTURE (review 2026-10-29 late, H1): it
        # said "thesis OFF" while the measurement ran with the thesis exit on.
        _m = risk_params.MEASURED_OPTION_STRUCTURE
        lines += ["", f"Rules (the measured structure): TP +{_m['tp_pct']:g}% · SL −{_m['sl_pct']:g}% · "
                      f"exit at {_m['dte_exit']} DTE · thesis {'ON (EMA20 break)' if _m.get('thesis') else 'OFF'}"]
        if _cap is not None:
            lines.append(f"Expiry kept to {_cap} DTE or less so the {risk_params.MEASURED_OPTION_STRUCTURE['dte_exit']}-DTE "
                         f"exit comes before earnings {r.get('next_earnings')} (D1).")
    elif SUGGEST_OPTIONS and _cap is not None:
        _ne = r.get("next_earnings")
        lines += ["", f"📄 No liquid contract found in the {OPT_MIN_DTE}-{_cap} DTE window (only "
                      f"expiries that exit before earnings {_ne}; spread/volume/OI gates). Do not "
                      f"substitute a contract held through the release."]
    elif SUGGEST_OPTIONS:
        lines += ["", "📄 No liquid contract found in the 21-45 DTE window "
                      "(spread/volume/OI gates). Check the chain yourself."]
    _er = earnings_line(r, opt)
    if _er:
        lines += ["", _er]
    _unc = [u for u in (r.get("unchecked") or []) if u != "earnings"]
    if _unc:
        lines.append(f"⚠️ Not checked this run: {', '.join(_unc)} (the data was unavailable, "
                     f"so the leg passed open). Check it before trading.")
    return "\n".join(lines)


def earnings_line(r: dict, opt: dict | None) -> str | None:
    """
    WHERE THE NEXT RELEASE FALLS AGAINST THE CONTRACT (review 2026-11-03, N9).
    The blackout looks three days ahead of the alert; the contract is 21-45
    DTE and the structure holds it to 7 DTE, so most releases inside the
    position's life pass the gate. In the record, 17 stops that gapped past
    -1.3 R cost -41 R against a +10.5 R total, clustered on releases (NOW
    10-28, PM about 10-21). The measured structure priced no event IV and no
    crush. This says plainly whether the named contract holds through the
    next release. Display only: it gates nothing (that is the owner's call).
    """
    if "earnings" in (r.get("unchecked") or []):
        return ("⚠️ Earnings date unknown this run: check it before buying. A release "
                "inside the contract's life is a different bet from the one measured.")
    ne = r.get("next_earnings")
    if not ne:
        return None
    try:
        ned = date.fromisoformat(str(ne)[:10])
    except ValueError:
        return None
    n = (ned - datetime.now(ET).date()).days
    if not opt:
        return f"Next earnings {ned} ({n}d away)."
    try:
        exp = date.fromisoformat(str(opt["expiry"])[:10])
    except (KeyError, ValueError):
        return f"Next earnings {ned} ({n}d away)."
    _dx = int(risk_params.MEASURED_OPTION_STRUCTURE["dte_exit"])
    out_by = exp - timedelta(days=_dx)
    if ned <= out_by:
        return (f"⚠️ Earnings {ned} ({n}d away) is before this contract's {_dx}-DTE exit "
                f"({out_by}): you would hold long premium THROUGH the release. Its IV is in "
                f"today's price and comes out after it; the measured structure priced none of it.")
    if ned <= exp:
        return (f"Earnings {ned} ({n}d away) falls after the {_dx}-DTE exit ({out_by}) but "
                f"before expiry: the exit comes first; today's price still carries some event IV.")
    return f"Next earnings {ned} ({n}d away), after this contract's expiry."


def _scan_cfg() -> dict:
    """The rules in force, as the scan heartbeat fingerprints them."""
    # COMPLETE (external review 2026-10-19, BACKLOG 35): the four fields
    # added on 2026-10-19 were already in force but not named, so a change
    # to any of them would not have moved the fingerprint. Naming them moves
    # it once, from e7d75b855a51; forward_log.KNOWN_CONFIGS declares both as
    # one dataset with the same rules.
    return {"adx_min": PARAMS.adx_min,
            "min_rr": PARAMS.min_rr,
            "hq_min_rr": PARAMS.hq_min_rr,
            "atr_stop_mult": PARAMS.atr_stop_mult,
            "atr_tgt_mult": PARAMS.atr_tgt_mult,
            "volume_mult": PARAMS.volume_mult,
            "volume_soft_mult": PARAMS.volume_soft_mult,
            "weekly_confirm": PARAMS.weekly_confirm,
            "spy_regime_on": PARAMS.spy_regime_on,
            "longs_only": risk_params.LONGS_ONLY,
            # Amendment 2026-10-28 E: the ADX and R:R gates compare unrounded
            # values. Named here so the fingerprint moved once, declared in
            # forward_log.KNOWN_CONFIGS with the magnitude of the change.
            "gate_precision": "unrounded",
            # Amendment 2026-10-28 G: the HQ tier no longer requires the
            # "Strong" tag. Named so the fingerprint moved once more.
            "hq_requires_strong": PARAMS.hq_requires_strong}


def _record_alert(tk: str, r: dict, bucket: str, delivered: bool, header: str,
                  dry_run: bool) -> None:
    """
    One forward-log row per alert attempt (journal review 2026-10-13,
    BACKLOG 32): the record then says what was SENT and whether it arrived,
    and the app's alert history reads it. Not on a dry run (nothing was
    sent). Non-fatal, like every record write.
    """
    if dry_run:
        return
    try:
        forward_log.record_alert(tk, r["trend"], r.get("bar_date"), bucket, delivered, header,
                                 at_send=r.get("at_send"))
    except Exception as exc:                           # noqa: BLE001
        logger.warning("%s — alert row failed: %s", tk, exc)


def sector_of(tk: str) -> str | None:
    """universe.SECTORS' label for `tk` (D8), or None: an unlabelled name is
    not grouped with others (every pool candidate is labelled; the universe
    selftest asserts it)."""
    try:
        import universe as _u
        return _u.SECTORS.get(tk)
    except Exception:                                  # noqa: BLE001
        return None


def pool_gate(r: dict, tk: str, sector_n: dict) -> str | None:
    """
    D8 (review 2026-11-03; owner: "go with D8"). The pool's top five used to
    be filled by RS alone: on 10-30 V, MA and CME went out, three Financials,
    while the universe itself caps a sector at two; and a name with no
    contract inside the structure's gates (UNP: tightest spread 13.2 %) took
    a slot it could not use. Asked after the D4/D1 hold, before the send:
    a sector already at universe.MAX_PER_SECTOR attempted pool alerts this
    session is withheld; then the contract the alert would name is fetched
    (compose_alert reuses it) and a name with none is withheld. Neither
    takes a cap slot, so the next name by rank is tried. The reason, or
    None to send.
    """
    try:
        import universe as _u
        _cap = int(_u.MAX_PER_SECTOR)
    except Exception:                                  # noqa: BLE001
        _cap = 0
    sec = sector_of(tk)
    if sec and _cap > 0 and sector_n.get(sec, 0) >= _cap:
        return (f"sector cap: {sector_n[sec]} {sec} pool alert(s) already this session "
                f"(at most {_cap}, the universe's own cap)")
    if SUGGEST_OPTIONS:
        _now = r.get("at_send") or {}
        _centre = next((float(v) for v in (_now.get("last"), _now.get("open")) if v is not None),
                       float(r["price"]))
        _c = earnings_cap_dte(r, datetime.now(ET).date())
        _c = _c if (_c is not None and _c < OPT_MAX_DTE) else None
        opt = suggest_option(tk, _centre, r["trend"], r["atr"], r.get("strength", "Normal"),
                             target=r.get("target"), max_dte=_c)
        if not opt:
            return ("no contract passes the structure's gates (spread, volume, open interest, "
                    "DTE window, moneyness, target)")
        r["contract"] = opt
    return None


def _withhold(tk: str, r: dict, bucket: str, why: str, dry_run: bool) -> None:
    """An alert withheld before send (D4, D1): logged, and ONE "NOT SENT" row
    per key, as the fill gate (J1) records. The row carries at_send."""
    logger.warning("%s%s %s bar %s NOT SENT: %s", "pool: " if bucket == "pool" else "",
                   tk, r.get("trend"), r.get("bar_date"), why)
    if not withheld_on_bar(r.get("signal_key")):
        _record_alert(tk, r, bucket, False, "NOT SENT: " + why, dry_run)


def _record_pullback(tk: str, raw_df, cdf, spy_regime, bucket_src: str,
                     tally: dict | None = None) -> bool:   # tally: the run's pullback counts
    """
    THE PULLBACK BUCKET (BACKLOG 31, results/pullback_preregistration.md).
    Evaluated on the frame the base signal just read, for every name the
    scanner evaluates (watchlist and pool), recorded under bucket=pullback
    through the record's one writer, and NEVER alerted: the bucket is a
    measurement. The SPY regime is tagged on the row and gates nothing.
    Non-fatal, like every record write.
    """
    try:
        pb = pullback_signal.evaluate_pullback(raw_df, cdf, PARAMS, spy_regime, tally)
        if not pb:
            return False
        _bar = sc._bar_dates(cdf, None).max().date()
        setup = {k: pb.get(k) for k in pullback_signal.SETUP_KEYS}
        setup["source_bucket"] = bucket_src
        _prow = forward_log.record_signal(tk, "Bullish", setup, "taken", "", _scan_cfg(),
                                          bar_date=_bar, bucket="pullback")
        if _prow is None:
            logger.debug("%s — pullback bar %s already in the forward log", tk, _bar)
        else:
            logger.info("%s — PULLBACK signal recorded (bar %s, regime %s, rr %s); "
                        "never alerted", tk, _bar, pb["regime"], pb["rr"])
            if forward_log.record_control(_prow, control_bars(cdf, _bar)) is None:
                logger.info("%s — no control row for pullback bar %s", tk, _bar)
        return True
    except Exception as exc:                           # noqa: BLE001
        logger.warning("%s — pullback record failed: %s", tk, exc)
        return False


def post_close_already_recorded(now=None) -> dict | None:
    """
    TODAY's post-close heartbeat, or None (review 2026-10-28, improvement
    4). When the schedule lands on time the 16:20 dispatch used to repeat
    the whole record pass, every Yahoo fetch and chain call included, to
    find everything already recorded. The record pass stamps its live
    heartbeat `pass: "post_close"`; a repeat on the same ET date is skipped
    on that row and `--force` runs it anyway. An in-session heartbeat (no
    stamp) never skips: the pass must run after a day's scans.
    """
    now = now or datetime.now(ET)
    try:
        ls = forward_log.last_scan(bucket="live")
        if not ls or ls.get("pass") != "post_close" or not ls.get("ts"):
            return None
        ts = datetime.fromisoformat(str(ls["ts"])).astimezone(ET)
    except (ValueError, TypeError):
        return None
    return ls if ts.date() == now.date() else None


def clocks_line() -> str:
    """
    Every forward clock on one line for the post-close log (review
    2026-10-28, improvement 5): the pool gate, the RS basket, the spread
    arm, the bar revisions and the IV premium. Never raises.
    """
    parts = []
    try:
        s = forward_log.summary()
        parts.append(f"pool {s['gate_settled_episodes']}/{forward_log.GATE_EPISODES} settled episodes")
        # THE SLOT STAYS (owner 2026-11-03, BACKLOG 39: "keep the slot"). The
        # number that reopens it is the one printed: a revision that moved a
        # decision or the HQ tag. A price a few cents off is the measurement.
        parts.append(f"bar revisions {s.get('revisions', 0)} "
                     f"({s.get('revisions_tag_moved', 0)} moved a decision or the tag; the slot moves if that is ever > 0)")
    except Exception as exc:                               # noqa: BLE001
        parts.append(f"forward log unreadable ({exc})")
    try:
        b = basket_log.summary()
        parts.append(f"basket {b['graded']}/{basket_log.RESULT_WEEKS} weeks graded "
                     f"({b['baskets']} recorded)")
    except Exception as exc:                               # noqa: BLE001
        parts.append(f"basket unreadable ({exc})")
    try:
        sp = spread_log.summary()
        least = min((a["graded"] for a in sp["arms"].values()), default=0)
        parts.append(f"spread {least}/{spread_log.RESULT_CYCLES} cycles graded per arm "
                     f"({sp['cycles']} opened, {sp['unquoted_cycles']} unquoted)")
    except Exception as exc:                               # noqa: BLE001
        parts.append(f"spread unreadable ({exc})")
    try:
        iv = iv_snapshot.summary()
        parts.append(f"IV premium {iv['graded']} graded of {iv['snapshots']} snapshots"
                     + (f", oldest unresolved {iv['oldest_unresolved']}" if iv.get("oldest_unresolved") else ""))
    except Exception as exc:                               # noqa: BLE001
        parts.append(f"IV unreadable ({exc})")
    return "; ".join(parts)


def basket_record_pass(closes_for, snap: dict | None) -> str:
    """
    The RS basket row for the newest universe snapshot, once per snapshot
    (results/rs_basket_preregistration.md, BACKLOG 34 B). `closes_for(tk)`
    returns settled [{"date", "close"}] rows or None (deferred / no data).
    The anchor is the last settled session on or before the snapshot date;
    every name and the benchmark must share it, or nothing is written.
    Returns one line for the log. Never raises.
    """
    try:
        if not snap or not snap.get("as_of") or not snap.get("tickers"):
            return "no snapshot to record"
        as_of = str(snap["as_of"])[:10]
        if basket_log.already_recorded(as_of):
            return f"{as_of} already recorded"
        names = [str(t).upper() for t in snap["tickers"]]
        closes, anchors, missing = {}, set(), []
        for tk in names + [basket_log.BENCH]:
            pre = [b for b in (closes_for(tk) or []) if str(b["date"])[:10] <= as_of]
            if not pre:
                missing.append(tk)
                continue
            closes[tk] = float(pre[-1]["close"])
            anchors.add(str(pre[-1]["date"])[:10])
        if missing or len(anchors) != 1:
            return (f"{as_of} not recorded: closes missing for {missing or 'none'}, "
                    f"anchors {sorted(anchors)}")
        refused: dict = {}
        row = basket_log.record(as_of, names, closes, closes[basket_log.BENCH],
                                next(iter(anchors)), refused=refused)
        return (f"recorded {as_of}: {len(names)} names on anchor {row['anchor']}" if row
                else f"{as_of} refused {refused}")
    except Exception as exc:                               # noqa: BLE001
        return f"basket record failed: {exc}"


def spread_record_pass(closes_for, vol_close_for, chain_for, today=None) -> str:
    """
    One forward `cycle` per spread arm without an open cycle
    (results/spread_preregistration.md, BACKLOG 34 E): the entry is the
    ticker's last settled close, the vol index must close on the SAME
    session, the expiry is the first listed one at least spread_log.DTE_DAYS
    out, the legs and model credit are spread_backtest.build()'s at its
    REALISTIC_SKEW (flat beside it), and the market's quotes on those legs
    are read from the same chain. `closes_for(tk)` -> settled [{"date",
    "close"}] or None; `vol_close_for(name)` -> {"date", "close"} or None;
    `chain_for(tk, min_dte)` -> {"expiry", "dte", "calls", "puts"} or None.
    Returns one line for the log. Never raises.
    """
    try:
        import spread_backtest as _sb
        open_now = spread_log.open_cycles()
        wrote, skipped, refused = 0, [], {}
        for tk in spread_log.TICKERS:
            arms = [a for a in spread_log.ARMS if a["ticker"] == tk
                    and spread_log.arm_key(a) not in open_now]
            if not arms:
                continue
            bars = closes_for(tk) or []
            if not bars:
                skipped.append(f"{tk}: no closes"); continue
            entry, spot = str(bars[-1]["date"])[:10], float(bars[-1]["close"])
            vol = vol_close_for(spread_log.VOL_INDEX[tk])
            if not vol or str(vol.get("date"))[:10] != entry:
                skipped.append(f"{tk}: {spread_log.VOL_INDEX[tk]} not closed on {entry}"); continue
            vol_pts = float(vol["close"])
            ch = chain_for(tk, spread_log.DTE_DAYS)
            if not ch or not ch.get("expiry"):
                skipped.append(f"{tk}: no chain at {spread_log.DTE_DAYS}+ days"); continue
            days = (_date_cls.fromisoformat(str(ch["expiry"])[:10]) - _date_cls.fromisoformat(entry)).days
            quotes = {}
            for right, rows in (("CALL", ch.get("calls") or []), ("PUT", ch.get("puts") or [])):
                for q in rows:
                    try:
                        quotes[(right, float(q["strike"]))] = (float(q.get("bid")), float(q.get("ask")))
                    except (KeyError, TypeError, ValueError):
                        continue
            for a in arms:
                pos = _sb.build(a["structure"], spot, vol_pts / 100.0, days / 365.0,
                                a["width"], a["otm_pct"], _sb.REALISTIC_SKEW)
                flat = _sb.build(a["structure"], spot, vol_pts / 100.0, days / 365.0,
                                 a["width"], a["otm_pct"], 0.0)
                model = dict(pos, credit_flat=flat["credit"], skew=_sb.REALISTIC_SKEW)
                market, why = spread_log.market_credit(pos["legs"], quotes)
                row = spread_log.record(entry, a, spot, vol_pts, ch["expiry"], pos["legs"],
                                        model, market, why, refused=refused)
                wrote += 1 if row else 0
        parts = [f"{wrote} cycle(s) opened"]
        if skipped:
            parts.append("skipped " + "; ".join(skipped))
        if refused:
            parts.append(f"refused {refused}")
        return ", ".join(parts)
    except Exception as exc:                               # noqa: BLE001
        return f"spread record failed: {exc}"


def run(args) -> int:
    # RECORD-ONLY is the post-close pass. During the session every scan
    # evaluates YESTERDAY's bar (drop_partial_bar), so a signal that forms at
    # today's close is first recorded by tomorrow's 11:07 scan — or the day
    # after, if tomorrow's dispatches miss. In the two hours after the close
    # today's bar is settled; this pass writes its signal rows and `scan`
    # heartbeat and nothing else. No trade alert (the market is shut; the
    # next session's first scan alerts, because the alert cooldown does not
    # read the log) and no chain fetch (post-close quotes are stale).
    record_only = bool(getattr(args, "record_only", False))
    if record_only:
        if not args.force and not is_post_close_window():
            logger.info("Not in the post-close window — record-only run skipped.")
            return 0
        _dup = None if args.force else post_close_already_recorded()
        if _dup:
            logger.info("post-close pass already recorded today (heartbeat %s, bar %s) — "
                        "the repeat is skipped; --force runs it", _dup.get("ts"), _dup.get("bar_date"))
            return 0
    elif not args.force and not is_market_open():
        logger.info("Market closed — skipping.")
        return 0

    state = load_state()
    global _EARNINGS_SEEN
    _mem = state.get(EARNINGS_STATE_KEY)
    _EARNINGS_SEEN = _mem if isinstance(_mem, dict) else {}
    state[EARNINGS_STATE_KEY] = _EARNINGS_SEEN          # N8: persisted with the state
    _pruned = prune_state(state)
    if _pruned:
        logger.info("state: pruned %d stamp(s) older than %d days",
                    _pruned, STATE_MAX_AGE_DAYS)
    hits, skipped, withheld, failed = 0, 0, 0, []

    # ── ATTACH OUTCOMES TO SETTLED SIGNALS, BEFORE SCANNING ──
    #
    # THE SCANNER DOES THIS, by the owner's decision (BACKLOG 17). The app
    # writes trade_journal.json through the Contents API but must never append
    # to the forward log: two writers on an append-only hash chain collide on
    # seq AND prev, and the merged chain does not verify — permanently, because
    # the hashes cover the seq so it cannot be renumbered. Reading the journal
    # here keeps exactly one writer.
    #
    # Runs first so an outcome lands even on a scan that finds no setups, and
    # before the loop that can raise per ticker.
    #
    # Non-fatal, like the signal write: the record matters, it does not matter
    # more than the trade.
    try:
        _j = Path("trade_journal.json")
        if _j.exists():
            _rep = forward_log.attach_outcomes(json.loads(_j.read_text()))
            if _rep["attached"]:
                logger.info("forward log: attached %d outcome(s)", _rep["attached"])
            # REFUSALS ARE REPORTED, NOT SWALLOWED. An ambiguous match is
            # refused rather than guessed (a wrong row here is permanent), and
            # a silent refusal would look exactly like "no trades closed".
            for _k in ("ambiguous", "no_match"):
                if _rep[_k]:
                    logger.warning("forward log: %d trade(s) %s — %s", _rep[_k],
                                   _k, "; ".join(_rep["detail"][:5]))
    except Exception as _exc:                          # noqa: BLE001
        logger.warning("forward log: attaching outcomes failed: %s", _exc)

    # Fetched ONCE for the whole scan, not per ticker.
    spy_regime = get_spy_regime() if PARAMS.spy_regime_on else None
    if PARAMS.spy_regime_on:
        logger.info("SPY regime: %s",
                    (spy_regime or {}).get("regime", "unavailable"))

    # THE BAR EACH TICKER WAS EVALUATED ON, so the run can record a `scan`
    # heartbeat per bar at the end. Normally one bar per run; tracked as a dict
    # because a frame that lags a day (a stale snapshot, a late Yahoo bar)
    # would otherwise be recorded under the wrong date.
    bars_scanned: dict = {}
    # RAW FRAMES, kept for the grader below so a watchlist ticker with an open
    # signal is not fetched twice in one run.
    frames: dict = {}
    pool_cdfs: dict = {}        # the settled frame each pool name signalled on (J1 fill probe)
    # THE PULLBACK BUCKET'S COUNTS for this run (signals, below_sma200,
    # insufficient_history, rr_below_min): the pre-registration says they
    # are counted, and a bucket that records nothing for weeks must be
    # tellable apart from "no pullbacks happened".
    pullback_tally: dict = {}

    for tk in WATCHLIST:
        try:
            time.sleep(FETCH_GAP_SEC)
            df = get_data(tk)
            if df is None:
                failed.append(f"{tk} (no data)")
                continue
            frames[tk] = df
            # BUG FIX: the scanner used to analyse df.iloc[-1] directly, so a
            # mid-session run read TODAY'S PARTIAL BAR — a Close that is just
            # the live price and a Volume only partly accumulated. That is why
            # the 12:30 run alerted on names the app rejected an hour later.
            cdf = compute(df)
            cdf, dropped = sc.drop_partial_bar(cdf, now=datetime.now(ET))
            if dropped:
                logger.debug("%s — dropped today's partial bar", tk)
            try:
                _bar = str(sc._bar_dates(cdf, None).max().date())
                bars_scanned.setdefault(_bar, {"tickers": [], "signals": 0})
                bars_scanned[_bar]["tickers"].append(tk)
            except Exception:                              # noqa: BLE001
                _bar = None
            # The bar's tally rides along so n_signals on the scan row counts
            # what the RULES produced (taken or skipped), not what reached
            # Telegram. A None return cannot distinguish "no signal" from
            # "signal, skipped", and the first version of this counted only
            # the alert-worthy ones under a name that said otherwise.
            r = analyze(cdf, tk, spy_regime=spy_regime,
                        tally=bars_scanned.get(_bar) if _bar else None)
            _record_pullback(tk, df, cdf, spy_regime, "live", pullback_tally)
            if not r:
                continue
            if record_only:
                logger.info("%s %s — recorded; the next session's scan alerts.",
                            tk, r["trend"])
                continue

            if recently_alerted(state, tk, r["trend"]):
                logger.info("%s %s qualifies but alerted within %dh — suppressed.",
                            tk, r["trend"], ALERT_COOLDOWN_HRS)
                skipped += 1
                continue
            if alerted_on_bar(r.get("signal_key")):
                logger.info("%s %s qualifies but bar %s was already alerted (delivered "
                            "alert row) — suppressed.", tk, r["trend"], r.get("bar_date"))
                skipped += 1
                continue
            _gap = gapped_at_fill(r, *fill_probe(df, cdf))
            if _gap:
                logger.warning("%s %s bar %s NOT SENT: %s", tk, r["trend"], r.get("bar_date"), _gap)
                if not withheld_on_bar(r.get("signal_key")):
                    _record_alert(tk, r, "live", False, "NOT SENT: " + _gap, args.dry_run)
                withheld += 1
                continue

            r["at_send"] = at_send(r, *fill_probe(df, cdf))
            _hold = withhold_before_send(r, datetime.now(ET).date())      # D4, D1
            if _hold:
                _withhold(tk, r, "live", _hold, args.dry_run)
                withheld += 1
                continue
            msg = compose_alert(r, tk)
            logger.info("ALERT %s %s rr=%s", tk, r["trend"], r["rr"])
            _ok = send_alert(msg, dry_run=args.dry_run)
            _record_alert(tk, r, "live", _ok, "TRADE ALERT", args.dry_run)
            if _ok:
                hits += 1
                if not args.dry_run:
                    state[f"{tk}:{r['trend']}"] = time.time()

        except Exception as e:
            # FIX 5: isolate each ticker. Previously one failure aborted the
            # entire scan and the remaining tickers were never examined.
            logger.exception("%s failed: %s", tk, e)
            failed.append(f"{tk} ({type(e).__name__})")

    # A scan where EVERY ticker failed is an outage, not a quiet market — and
    # this runs unattended on a schedule, so the only place it would otherwise
    # appear is a workflow log nobody opens. The app showed the same failure
    # as "0 setups" on 2026-09-02; the scanner's version of that mistake is
    # worse, because silence is its normal output.
    #
    # Cooldown-keyed like any other alert so a day-long Yahoo outage sends one
    # message, not one per scheduled run.
    if is_total_outage(failed, WATCHLIST):
        if not recently_alerted(state, "SCANNER", "OUTAGE"):
            _outage_sent = send_alert(
                "⚠️ SCANNER OUTAGE\n"
                f"All {len(WATCHLIST)} watchlist tickers failed to return "
                f"usable data, so nothing was scanned this run. This is NOT "
                f"'no setups' — it means the scan did not happen.\n\n"
                f"{', '.join(failed)}",
                dry_run=args.dry_run)
            # Stamped only when it was DELIVERED (external review 2026-10-19):
            # a failed send used to start the cooldown, so an outage that
            # coincided with a Telegram failure went unreported for 4 hours.
            if _outage_sent and not args.dry_run:
                state["SCANNER:OUTAGE"] = time.time()
        else:
            logger.warning("Total scan outage, but an outage alert was sent "
                           "within %dh — suppressed.", ALERT_COOLDOWN_HRS)

    # ── THE POOL BUCKET — the whole candidate pool, once a session (BACKLOG 30) ──
    #
    # Owner's decision 2026-10-13: scan the wider universe once a day in
    # session, alert on the HIGH-QUALITY tier only, at most POOL_ALERT_CAP a
    # session ranked by relative strength, and record every pool signal under
    # its own bucket so it never blends with the watchlist's record. Runs on
    # the first alerting scan of each ET date (pool_due), never in the
    # record-only pass (the pool's open rows are graded there instead), and
    # evaluates the last settled bar exactly as the watchlist loop does.
    pool_scanned, pool_alerted, pool_hq_n = 0, 0, 0
    pool_send_failed = False
    pool_bars: dict = {}
    if not record_only and (getattr(args, "force_pool", False) or pool_due(state)):
        pool = pool_tickers(WATCHLIST)
        pool_hq, pool_failed = [], []
        bench = frames.get("SPY")
        if bench is None and pool:
            try:
                time.sleep(FETCH_GAP_SEC)
                bench = get_data("SPY")
            except Exception as _exc:                  # noqa: BLE001
                logger.warning("pool: SPY benchmark fetch failed: %s", _exc)
                bench = None
        if pool:
            logger.info("pool: scanning %d name(s) beyond the watchlist", len(pool))
        for tk in pool:
            try:
                time.sleep(FETCH_GAP_SEC)
                df = get_data(tk)
                if df is None:
                    pool_failed.append(f"{tk} (no data)")
                    continue
                frames[tk] = df
                cdf = compute(df)
                cdf, _dropped = sc.drop_partial_bar(cdf, now=datetime.now(ET))
                pool_cdfs[tk] = cdf
                try:
                    _bar = str(sc._bar_dates(cdf, None).max().date())
                    pool_bars.setdefault(_bar, {"tickers": [], "signals": 0})
                    pool_bars[_bar]["tickers"].append(tk)
                except Exception:                      # noqa: BLE001
                    _bar = None
                pool_scanned += 1
                _rs_tk = relative_strength(df, bench)     # D8: recorded on the row
                r = analyze(cdf, tk, spy_regime=spy_regime,
                            tally=pool_bars.get(_bar) if _bar else None,
                            bucket="pool", rs=_rs_tk)
                _record_pullback(tk, df, cdf, spy_regime, "pool", pullback_tally)
                if r:
                    r["rs"] = _rs_tk
                    pool_hq.append(r)
            except Exception as e:                     # noqa: BLE001
                logger.exception("pool %s failed: %s", tk, e)
                pool_failed.append(f"{tk} ({type(e).__name__})")
        # RANK BY RELATIVE STRENGTH, alert the top POOL_ALERT_CAP. The rest
        # are already recorded by analyze(); they are named here, not sent.
        # D8 (review 2026-11-03): with no benchmark every RS is None and the
        # five were the first five ALPHABETICALLY. Then the order is the
        # record's own declared rank (forward_log.rank_score, H1': ADX, then
        # R:R, then ticker), and the alert says which ranking chose it.
        _by = "RS" if any(x["rs"] is not None for x in pool_hq) else "ADX (RS unavailable)"
        if _by == "RS":
            pool_hq.sort(key=lambda x: (x["rs"] is None, -(x["rs"] or 0.0), x["ticker"]))
        else:
            pool_hq.sort(key=lambda x: forward_log.rank_score(
                {"ticker": x["ticker"], "setup": {"hq_fail": [], "adx": x.get("adx"), "rr": x.get("rr")}}),
                reverse=True)
        pool_hq_n = len(pool_hq)
        _sector_n: dict = {}        # D8: pool alerts attempted per sector this session
        pool_attempted = 0          # THE CAP BOUNDS ATTEMPTS, not deliveries: on a
        for _rank, r in enumerate(pool_hq, 1):   # failed day the five by RS are tried, not all six
            tk = r["ticker"]
            if pool_attempted >= POOL_ALERT_CAP:
                logger.info("pool: %s %s is HQ but beyond the cap of %d — "
                            "recorded, not alerted", tk, r["trend"], POOL_ALERT_CAP)
                # D8: the cut is on the record (one NOT SENT row per key)
                _withhold(tk, r, "pool", f"beyond the pool cap of {POOL_ALERT_CAP} (rank {_rank} "
                                         f"of {pool_hq_n} by {_by})", args.dry_run)
                continue
            if recently_alerted(state, tk, r["trend"]):
                logger.info("pool: %s %s qualifies but alerted within %dh — "
                            "suppressed.", tk, r["trend"], ALERT_COOLDOWN_HRS)
                skipped += 1
                continue
            if alerted_on_bar(r.get("signal_key")):
                logger.info("pool: %s %s qualifies but bar %s was already alerted "
                            "(delivered alert row) — suppressed.", tk, r["trend"], r.get("bar_date"))
                skipped += 1
                continue
            _gap = gapped_at_fill(r, *fill_probe(frames[tk], pool_cdfs.get(tk)))
            if _gap:
                logger.warning("pool: %s %s bar %s NOT SENT: %s", tk, r["trend"], r.get("bar_date"), _gap)
                if not withheld_on_bar(r.get("signal_key")):
                    _record_alert(tk, r, "pool", False, "NOT SENT: " + _gap, args.dry_run)
                withheld += 1
                continue
            _hdr = (f"🚨 POOL ALERT (wide universe, HQ tier, RS {r['rs']:+.1f})"
                    if r["rs"] is not None else f"🚨 POOL ALERT (wide universe, HQ tier, ranked by {_by})")
            r["at_send"] = at_send(r, *fill_probe(frames[tk], pool_cdfs.get(tk)))
            _hold = (withhold_before_send(r, datetime.now(ET).date())      # D4, D1
                     or pool_gate(r, tk, _sector_n))                    # D8
            if _hold:
                _withhold(tk, r, "pool", _hold, args.dry_run)
                withheld += 1
                continue
            msg = compose_alert(r, tk, header=_hdr)
            logger.info("POOL ALERT %s %s rr=%s rs=%s", tk, r["trend"], r["rr"], r["rs"])
            pool_attempted += 1
            _sector_n[sector_of(tk)] = _sector_n.get(sector_of(tk), 0) + 1
            _ok = send_alert(msg, dry_run=args.dry_run)
            _record_alert(tk, r, "pool", _ok, "POOL ALERT", args.dry_run)
            if _ok:
                hits += 1
                pool_alerted += 1
                if not args.dry_run:
                    state[f"{tk}:{r['trend']}"] = time.time()
            else:
                pool_send_failed = True
        # THE ONCE-A-DAY STAMP IS WRITTEN ONLY WHEN THE DAY'S POOL ALERTS WERE
        # DELIVERED (review 2026-10-13): a Telegram failure leaves the pool
        # due, so the next alerting run rescans and resends, exactly as the
        # watchlist's cooldown stamp is written only on a delivered alert.
        if pool_scanned and not args.dry_run and not pool_send_failed:
            state[POOL_STATE_KEY] = datetime.now(ET).strftime("%Y-%m-%d")
        if pool and not pool_scanned:
            logger.warning("pool: all %d name(s) failed — the pool was NOT scanned; "
                           "the next alerting run retries", len(pool))
        if pool:
            logger.info("pool: %d scanned, %d HQ, %d alerted (cap %d), %d failed",
                        pool_scanned, pool_hq_n, pool_alerted, POOL_ALERT_CAP,
                        len(pool_failed))

    # ── GRADE EVERY TAKEN SIGNAL WHOSE WINDOW HAS CLOSED ──
    #
    # results/forward_grading_preregistration.md. The forward record had intake
    # and no grading: outcomes came only from journal trades, and base signals
    # are not alerted, so they were never traded, so they were never graded.
    # forward_log.resolve_signal() is a pure function of the row and the bars
    # that followed it (stdlib only; the frame is handed over as plain dicts);
    # this is the one place that feeds it market data. Runs on every run(),
    # including --record-only. Non-fatal: a grading failure never costs an
    # alert. Placed AFTER the scan loop so a watchlist ticker reuses the
    # frame already fetched; a ticker that has left the watchlist is fetched
    # here, once.
    try:
        # POOL-ONLY NAMES (BACKLOG 30): a ticker whose open rows are all in
        # the pool bucket is fetched on the pool-scan run, where it is already
        # in `frames`, and in the post-close pass. On the other in-session
        # runs its rows wait, so grading does not turn ~6 pool signals a day
        # into eighty extra fetches on every run. A live-bucket signal whose
        # name has left the watchlist is still fetched here, once, as before.
        _live_open = {r.get("ticker") for r in forward_log.read_all()
                      if forward_log.counts_as_taken(r)
                      and forward_log.bucket_of(r) == "live"}
        # Read BEFORE grading: a deferred name's rows stay open, so this is
        # the count the "deferred" line below reports.
        _open_before = forward_log.open_signals()
        _deferred_tk: set = set()

        def _bars_for(tk: str):
            _df = frames.get(tk)
            if _df is None:
                if tk not in _live_open and not record_only:
                    _deferred_tk.add(tk)
                    return None
                time.sleep(FETCH_GAP_SEC)
                _df = get_data(tk)
            return bars_for_grading(_df) if _df is not None else None
        _grep = forward_log.grade_open_signals(_bars_for)
        if _grep["graded"] or _grep["void"]:
            logger.info("forward log: graded %d signal(s), %d void, %d still open",
                        _grep["graded"], _grep["void"], _grep["unsettled"])
        for _d in _grep["detail"][:10]:
            logger.info("forward log: %s", _d)
        # A deferral is not missing data: the grader counts a None fetch as
        # "no_data" either way, so split the two here. Only a fetch that
        # really came back empty is a warning.
        _deferred = sum(1 for s in _open_before if s.get("ticker") in _deferred_tk)
        if _deferred:
            logger.info("forward log: grading deferred %d pool/pullback signal(s) in %d "
                        "name(s) to the pool run or the post-close pass",
                        _deferred, len(_deferred_tk))
        _no_data = _grep["no_data"] - _deferred
        if _grep["failed"] or _no_data:
            logger.warning("forward log: grading skipped %d (no data) / %d (error)",
                           _no_data, _grep["failed"])
    except Exception as _exc:                          # noqa: BLE001
        logger.warning("forward log: grading failed: %s", _exc)

    # ── THE IV SNAPSHOT — post-close only (BACKLOG 25 Part B) ──
    #
    # results/option_iv_preregistration.md. One row per watchlist name per
    # session: ATM implied vol at the nearest 21-45 DTE expiry, with trailing
    # realised vol, into iv_snapshots.jsonl (one writer: this module). Then
    # every snapshot whose 21 sessions have elapsed is graded from the frames
    # already fetched. Post-close because that is when the day's close and
    # the chain agree on a settled spot. Non-fatal, like every record write.
    if record_only:
        try:
            _refused: dict = {}
            _wrote = 0
            for tk, _df in sorted(frames.items()):
                _ch = fetch_atm_chain(tk)
                if not _ch:
                    _refused["no chain in 21-45 DTE"] = _refused.get("no chain in 21-45 DTE", 0) + 1
                    continue
                _closes = [float(c) for c in _df["Close"].tolist()]
                _spot = _closes[-1]
                _atm = iv_snapshot.atm_iv_from_chain(_ch["calls"], _ch["puts"], _spot)
                if not _atm:
                    _refused["no ATM quote"] = _refused.get("no ATM quote", 0) + 1
                    continue
                _row = iv_snapshot.record(
                    str(sc._bar_dates(_df, None).max().date()), tk, _ch["expiry"],
                    _ch["dte"], _atm["strike"], _spot, _atm["iv"],
                    iv_snapshot.realised_vol_points(_closes), "yahoo",
                    refused=_refused)
                _wrote += 1 if _row else 0
            logger.info("iv snapshot: %d row(s) written%s", _wrote,
                        f", refused {_refused}" if _refused else "")
            _iv_fetched: list = []
            _iv_deferred: list = []

            def _closes_for(t):
                _df_iv = frames.get(t)
                if _df_iv is None:
                    # A name no longer on the watchlist: fetch it once, within
                    # the cap, so its open snapshots can settle (N05).
                    if len(_iv_fetched) >= IV_GRADE_FETCH_CAP:
                        _iv_deferred.append(t)
                        return None
                    time.sleep(FETCH_GAP_SEC)
                    _df_iv = get_data(t)
                    _iv_fetched.append(t)
                    if _df_iv is None:
                        return None
                return [{"date": b["date"], "close": b["close"]}
                        for b in bars_for_grading(_df_iv)]
            # The exchange calendar makes the window check exact: consecutive
            # closes must be exactly one session apart (N04).
            _sessions_between = lambda a, b: trading_days_between(
                date.fromisoformat(a), date.fromisoformat(b))
            # ── THE RS BASKET ROW (BACKLOG 34 B), before the IV grading spends
            # the fetch cap: the basket's names are the watchlist, already in
            # `frames`; the benchmark is one fetch.
            try:
                import universe as _u_b
                _snap_b, _ = _u_b.latest_snapshot()
            except Exception as _exc:                      # noqa: BLE001
                _snap_b = None
                logger.warning("rs basket: snapshot unreadable: %s", _exc)
            logger.info("rs basket: %s", basket_record_pass(_closes_for, _snap_b))
            _ig = iv_snapshot.grade(_closes_for, sessions_between=_sessions_between)
            if _ig["graded"] or _ig["failed"] or _iv_fetched or _iv_deferred:
                logger.info("iv snapshot: graded %d, unsettled %d, no data %d, failed %d, "
                            "held %d (anchor missing %d, gap %d, bad close %d); departed "
                            "names fetched %d, deferred %d",
                            _ig["graded"], _ig["unsettled"], _ig["no_data"], _ig["failed"],
                            _ig["anchor_missing"] + _ig["gap"] + _ig["bad_close"],
                            _ig["anchor_missing"], _ig["gap"], _ig["bad_close"],
                            len(_iv_fetched), len(_iv_deferred))
            for _d in _ig["detail"][:10]:
                logger.info("iv snapshot: %s", _d)
            # ── GRADE THE BASKETS whose week has closed (same closes, same calendar) ──
            _bg = basket_log.grade(_closes_for, sessions_between=_sessions_between)
            if _bg["graded"] or _bg["void"] or _bg["held"] or _bg["failed"]:
                logger.info("rs basket: graded %d, void %d, unsettled %d, held %d, no data %d, failed %d",
                            _bg["graded"], _bg["void"], _bg["unsettled"], _bg["held"],
                            _bg["no_data"], _bg["failed"])
            for _d in _bg["detail"][:10]:
                logger.info("rs basket: %s", _d)
            # ── THE SPREAD ARMS (BACKLOG 34 E): settle the cycles whose expiry
            # has closed, THEN open the next one on this session's close, the
            # backtest's own re-entry rule. Two chain fetches at most.
            _sg = spread_log.grade(_closes_for)
            if _sg["graded"] or _sg["void"] or _sg["held"] or _sg["failed"]:
                logger.info("spread arms: graded %d, void %d, unsettled %d, held %d, no data %d, failed %d",
                            _sg["graded"], _sg["void"], _sg["unsettled"], _sg["held"],
                            _sg["no_data"], _sg["failed"])
            for _d in _sg["detail"][:10]:
                logger.info("spread arms: %s", _d)

            def _vol_close_for(name):
                time.sleep(FETCH_GAP_SEC)
                _dv = get_data(name)
                if _dv is None or len(_dv) == 0:
                    return None
                _bv = bars_for_grading(_dv)
                return {"date": _bv[-1]["date"], "close": _bv[-1]["close"]} if _bv else None
            logger.info("spread arms: %s", spread_record_pass(
                _closes_for, _vol_close_for, lambda tk, md: fetch_atm_chain(tk, min_dte=md)))
            logger.info("clocks: %s", clocks_line())
        except Exception as _exc:                          # noqa: BLE001
            logger.warning("iv snapshot failed: %s", _exc)

    # ── RECORD THAT THIS SCAN HAPPENED ──
    #
    # One `scan` row per bar evaluated. Without it a bar with no signal row
    # meant EITHER "quiet market" OR "no scan ran" — and for three weeks the
    # second was true on 1 in 4 trading days while every workflow run reported
    # success. This is the heartbeat coverage_check() reads.
    #
    # Written on dry runs too: a dry run still evaluated the bar. Skipped only
    # when nothing was scanned at all — "scanned zero tickers" is an outage
    # (handled above), not a heartbeat. n_signals is the count of bars on
    # which the rules produced a signal, taken OR skipped — the same rows
    # record_signal() wrote (or deduped) for this bar.
    for _bar, _info in sorted(bars_scanned.items()):
        try:
            forward_log.record_scan(_bar, _info["tickers"], _info["signals"],
                                    _scan_cfg(), pass_kind="post_close" if record_only else None)
        except Exception as _exc:                          # noqa: BLE001
            logger.warning("forward log: scan row for %s failed: %s", _bar, _exc)
    # The pool's heartbeat, under its own bucket, AFTER the live one: it is
    # evidence that the pool was evaluated, never that the watchlist was.
    for _bar, _info in sorted(pool_bars.items()):
        try:
            forward_log.record_scan(_bar, _info["tickers"], _info["signals"],
                                    _scan_cfg(), bucket="pool")
        except Exception as _exc:                          # noqa: BLE001
            logger.warning("forward log: pool scan row for %s failed: %s", _bar, _exc)

    if pullback_tally:
        logger.info("pullback bucket: %s", pullback_tally)
    if not args.dry_run:
        save_state(state)

    logger.info("Done. %d alert(s) sent, %d suppressed by cooldown, %d withheld before send "
                "(fill, R:R at send, earnings), "
                "%d ticker(s) failed.", hits, skipped, withheld, len(failed))
    if failed:
        logger.warning("Failed tickers: %s", ", ".join(failed))
    return 0


# ══════════════════════════════════════════════════════════════════
# COVERAGE — the alarm for silence
# ══════════════════════════════════════════════════════════════════
# is_total_outage() fires when every ticker fails INSIDE a run. It cannot see a
# run that never scans — and from 09-16 to 10-07 GitHub's scheduler delivered
# the scanner in session on 11 of 48 scheduled slots and the exit monitor on 16
# of 112, each late run skipping at the market-hours guard and reporting
# success. Three straight trading days passed with no scan and nothing said so.
# This runs on EVERY workflow run, including the ones that cannot scan, and
# reads the committed state the last real run left behind.

NO_SCAN_ALERT_SESSIONS = 2     # alert once this many trading days pass unscanned
STALE_POSITION_SESSIONS = 1    # an open position unchecked for this many sessions
COVERAGE_COOLDOWN_HRS = 20     # one nag a day, not one per late run


def trading_days_between(start: date, end: date) -> int:
    """Completed trading sessions after `start` up to and including `end`."""
    if end <= start:
        return 0
    n, cur = 0, start
    while cur < end:
        cur += timedelta(days=1)
        if cur.weekday() >= 5 or cur.strftime("%Y-%m-%d") in MARKET_HOLIDAYS:
            continue
        n += 1
    return n


def coverage_report(now: datetime | None = None,
                    positions_path: Path | None = None) -> dict:
    """
    What the committed state says about whether the unattended system has been
    running. Pure read; the alerting decision is coverage_check()'s.

      last_scan_bar       bar date of the last `scan` row, or None
      sessions_unscanned  trading days since that bar whose bar no scan has
                          evaluated (the scanner reads the last SETTLED bar, so
                          on a run day the newest possible bar is yesterday's)
      stale_positions     OPEN / EXIT_SIGNALLED positions whose last_check_epoch
                          is STALE_POSITION_SESSIONS or more sessions old
    """
    now = now or datetime.now(ET)
    today = now.date()
    last = forward_log.last_scan()
    if last and last.get("bar_date"):
        last_bar = date.fromisoformat(last["bar_date"])
        unscanned = max(0, trading_days_between(last_bar, today) - 1)
    else:
        last_bar, unscanned = None, None

    stale = []
    p = positions_path or Path("open_positions.json")
    try:
        for pos in (json.loads(p.read_text()) if p.exists() else []):
            if pos.get("status") not in ("OPEN", "EXIT_SIGNALLED"):
                continue
            ep = pos.get("last_check_epoch")
            if not ep:
                stale.append((pos.get("ticker"), None))
                continue
            checked = datetime.fromtimestamp(float(ep), ET).date()
            gap = trading_days_between(checked, today)
            if gap >= STALE_POSITION_SESSIONS:
                stale.append((pos.get("ticker"), gap))
    except Exception as e:                                 # noqa: BLE001
        logger.warning("coverage: could not read positions: %s", e)

    return {"last_scan_bar": str(last_bar) if last_bar else None,
            "sessions_unscanned": unscanned, "stale_positions": stale,
            "today": str(today)}


def coverage_check(state: dict, now: datetime | None = None,
                   dry_run: bool = False) -> bool:
    """Alert if the system has gone silent. Returns True if an alert was sent."""
    rep = coverage_report(now)
    lines = []
    if rep["sessions_unscanned"] is None:
        lines.append("The forward log has NO scan row at all — the scanner "
                     "has never recorded a completed scan.")
    elif rep["sessions_unscanned"] >= NO_SCAN_ALERT_SESSIONS:
        lines.append(f"No scan has run for {rep['sessions_unscanned']} trading "
                     f"day(s) — last bar evaluated was {rep['last_scan_bar']}.")
    if rep["stale_positions"]:
        bits = ", ".join(f"{t} ({'never' if g is None else f'{g} session(s)'})"
                         for t, g in rep["stale_positions"])
        lines.append(f"Open position(s) not checked by the exit monitor: {bits}.")
    if not lines:
        logger.info("coverage: OK — last scan bar %s, %s session(s) unscanned, "
                    "no stale positions", rep["last_scan_bar"],
                    rep["sessions_unscanned"])
        return False
    last_alert = float(state.get("SCANNER:COVERAGE", 0) or 0)
    if (time.time() - last_alert) < COVERAGE_COOLDOWN_HRS * 3600:
        logger.warning("coverage: PROBLEM, alert suppressed by cooldown — %s",
                       " ".join(lines))
        return False
    msg = ("⚠️ SCANNER COVERAGE\n" + "\n".join(lines) +
           "\n\nA workflow run can report success while skipping the scan — a "
           "late run hits the market-hours guard. This is NOT 'no setups'; it "
           "means the system is not looking. See BACKLOG 22.")
    logger.warning("coverage: %s", " ".join(lines))
    if send_alert(msg, dry_run=dry_run):
        if not dry_run:
            state["SCANNER:COVERAGE"] = time.time()
        return True
    return False


def _selftest_body() -> int:
    """
    Offline checks for the unattended paths. This module sends real alerts on
    a schedule and had NO test coverage at all, which is how it could have
    fetched nothing for days while looking exactly like a quiet market.
    """
    # ── THE DIRECTION GATE MUST BIND IN analyze(), not just in risk_params ──
    # (forward_log.LOG is redirected for the whole of this function by
    # selftest() below — see the note there.)
    # Asserting that direction_blocked() returns a string proves the helper
    # works, not that the scanner calls it. This project has shipped that
    # mistake repeatedly — a guard aimed at the producer while the break sat in
    # the consumer. Drive the real function and read what comes back.
    import pandas as _pd
    # A REAL DATE INDEX. This was a bare RangeIndex, so _bar_dates() read the
    # row numbers as epoch nanoseconds and every bar came back 1970-01-01 — fine
    # while nothing looked at the date, useless the moment the forward log
    # started keying on it. Production frames always carry a DatetimeIndex
    # (compute() runs on yfinance bars); the fixture now does too.
    _frame = _pd.DataFrame(
        {"Close": [100.0] * (MIN_BARS_AFTER_WARMUP + 5)},
        index=_pd.bdate_range("2026-01-05", periods=MIN_BARS_AFTER_WARMUP + 5))

    def _canned(trend):
        return {"blocked": False, "ticker": "ZZ", "trend": trend,
                "strength": "Strong", "price": 100.0, "entry": 100.0,
                "stop": 98.0, "target": 106.0, "rr": 3.0, "rsi": 65.0,
                "adx": 30.0, "atr": 2.0, "high_quality": True,
                "filters_pass": 4, "filters_total": 4}

    _real_eval = sc.evaluate
    _real_wk, _real_earn = get_weekly_trend, check_earnings_blackout
    try:
        globals()["get_weekly_trend"] = lambda t: "Bullish"
        globals()["check_earnings_blackout"] = lambda t, **k: (True, "n/a")
        sc.evaluate = lambda *a, **k: _canned("Bearish")
        assert analyze(_frame, "ZZ") is None, (
            "a high-quality BEARISH signal reached the alert path. Shorts "
            "measured worse than random entry (drift_null: all 200 random "
            "draws beat the real signal, -0.131 R per trade) and the scanner "
            "must not send them")
        sc.evaluate = lambda *a, **k: _canned("Bullish")
        got = analyze(_frame, "ZZ")
        assert got is not None and got["trend"] == "Bullish", (
            f"the gate swallowed a LONG. It must block one direction, not "
            f"stop the scanner: {got}")
        # THE TALLY counts what the rules PRODUCED: the skipped bearish signal
        # above counts, the taken bullish one counts, a blocked bar does not.
        _tally: dict = {}
        sc.evaluate = lambda *a, **k: _canned("Bearish")
        assert analyze(_frame, "ZT", tally=_tally) is None
        sc.evaluate = lambda *a, **k: _canned("Bullish")
        analyze(_frame, "ZU", tally=_tally)
        sc.evaluate = lambda *a, **k: dict(_canned("Bullish"), blocked=True,
                                           block_reason="fixture")
        assert analyze(_frame, "ZV", tally=_tally) is None
        assert _tally.get("signals") == 2, (
            f"tally {_tally}: analyze() must count a skipped signal AND a "
            f"taken one, and not a blocked bar — n_signals on the scan row "
            f"is 'what the rules produced', not 'what was alert-worthy'")
        # THE BAR REVISION NOTE (BACKLOG 39): ZU's bar is on the chain from
        # the read above; the same read again writes nothing, a read whose
        # price and R:R moved writes ONE revision row naming them, and the
        # recorded signal row is unchanged.
        sc.evaluate = lambda *a, **k: _canned("Bullish")
        analyze(_frame, "ZU")
        _rv_rows = lambda: [x for x in forward_log.read_all() if x.get("kind") == "revision"]   # noqa: E731
        assert not _rv_rows(), "an identical re-read must not write a revision"
        sc.evaluate = lambda *a, **k: dict(_canned("Bullish"), price=100.4, rr=2.9)
        analyze(_frame, "ZU")
        _rvs = _rv_rows()
        assert len(_rvs) == 1 and _rvs[0]["ticker"] == "ZU" and _rvs[0]["fields"] == {"price": [100.0, 100.4], "rr": [3.0, 2.9]} \
            and _rvs[0]["hq"] == [True, True], _rvs
        analyze(_frame, "ZU")
        assert len(_rv_rows()) == 1, "one revision per key"
        _zu = [x for x in forward_log.read_all() if x.get("kind") == "signal" and x["ticker"] == "ZU"]
        assert len(_zu) == 1 and _zu[0]["setup"]["price"] == 100.0, "the recorded row stands; the record is append-only"
        # N8: the row says which tier legs passed UNCHECKED (no SPY regime here,
        # so "regime"); with the regime and an unknown calendar, "earnings".
        assert _zu[0]["setup"]["unchecked"] == ["regime"], _zu[0]["setup"]
        sc.evaluate = lambda *a, **k: dict(_canned("Bullish"), filters={"Earnings Blackout": {
            "pass": True, "detail": "No earnings date available — proceed with caution"}})
        _got = analyze(_frame, "ZW", spy_regime={"regime": "Bullish"})
        _zw = [x for x in forward_log.read_all() if x.get("kind") == "signal" and x["ticker"] == "ZW"]
        assert _got["unchecked"] == ["earnings"] and _zw[0]["setup"]["unchecked"] == ["earnings"], (_got, _zw)
        print("unchecked legs          : a row and its alert say which tier legs passed open (N8)")
        # D8: a pool row carries the RS the cut ranks on; a row without one has none
        sc.evaluate = lambda *a, **k: _canned("Bullish")
        analyze(_frame, "ZR", spy_regime={"regime": "Bullish"}, bucket="pool", rs=4.2)
        _zr = [x for x in forward_log.read_all() if x.get("kind") == "signal" and x["ticker"] == "ZR"]
        assert _zr[0]["setup"]["rs"] == 4.2 and "rs" not in _zw[0]["setup"], (_zr, _zw)
        print("bar revision note       : a later read that differs writes one revision row; identical reads none")
    finally:
        sc.evaluate = _real_eval
        globals()["get_weekly_trend"] = _real_wk
        globals()["check_earnings_blackout"] = _real_earn
    print("direction gate          : bearish dropped, bullish still alerts")

    # ── N8: RELEASE DATES SEEN ARE REMEMBERED, through the real gate ──
    # NOW, 2026-10-27: the calendar shows the 10-28 release (blocked). By
    # 10-29 11:07 Yahoo has rolled it to next quarter's estimate; the
    # memory keeps 10-28, so the straddle blocks NOW's 10-28 bar.
    import datetime as _dtm
    global _EARNINGS_SEEN
    _real_cal, _real_dt, _real_seen = _earnings_calendar, datetime, _EARNINGS_SEEN
    class _Clock:
        at = None
        @classmethod
        def now(cls, tz=None):
            return cls.at
    try:
        _EARNINGS_SEEN = {}
        globals()["datetime"] = _Clock
        _Clock.at = _dtm.datetime(2026, 10, 27, 11, 7)
        globals()["_earnings_calendar"] = lambda t: {"Earnings Date": [_dtm.date(2026, 10, 28)]}
        assert check_earnings_blackout("NOW", bar_date=_dtm.date(2026, 10, 26))[0] is False
        assert _EARNINGS_SEEN == {"NOW": ["2026-10-28"]}, _EARNINGS_SEEN
        _Clock.at = _dtm.datetime(2026, 10, 29, 11, 7)
        globals()["_earnings_calendar"] = lambda t: {"Earnings Date": [_dtm.date(2027, 1, 27)]}
        _ok, _why = check_earnings_blackout("NOW", bar_date=_dtm.date(2026, 10, 28))
        assert _ok is False and "straddle" in _why, (_ok, _why)
        _EARNINGS_SEEN = {}
        assert check_earnings_blackout("NOW", bar_date=_dtm.date(2026, 10, 28))[0] is True, \
            "without the memory the rolled calendar clears it: the 10-29 alert"
        # a failed fetch still reads what is remembered; with nothing remembered it says unavailable
        _EARNINGS_SEEN = {"NOW": ["2026-10-28"]}
        def _boom(t):
            raise RuntimeError("calendar down")
        globals()["_earnings_calendar"] = _boom
        assert check_earnings_blackout("NOW", bar_date=_dtm.date(2026, 10, 28))[0] is False
        _EARNINGS_SEEN = {}
        _ok, _why = check_earnings_blackout("NOW", bar_date=_dtm.date(2026, 10, 28))
        assert _ok is True and "unavailable" in _why, _why
    finally:
        globals()["_earnings_calendar"] = _real_cal
        globals()["datetime"] = _real_dt
        _EARNINGS_SEEN = _real_seen
    # run() binds the memory to the state and saves it with the state
    _stt: dict = {}
    _saved_rs = {k: globals()[k] for k in ("load_state", "save_state", "WATCHLIST", "get_spy_regime", "pool_due",
                                           "get_data")}
    try:
        globals()["get_data"] = lambda tk: None             # offline: grading fetches nothing
        globals()["load_state"] = lambda: _stt
        globals()["save_state"] = lambda st: None
        globals()["WATCHLIST"] = []
        globals()["get_spy_regime"] = lambda: None
        globals()["pool_due"] = lambda st: False
        import argparse as _apm
        run(_apm.Namespace(force=True, dry_run=False))
        assert isinstance(_stt.get(EARNINGS_STATE_KEY), dict) and _EARNINGS_SEEN is _stt[EARNINGS_STATE_KEY], _stt
    finally:
        globals().update(_saved_rs)
        _EARNINGS_SEEN = _real_seen
    print("earnings memory         : a release seen before Yahoo drops it still blocks the straddle; "
          "persisted in the scanner state (N8)")

    # ── N8: the SPY regime's Yahoo leg retries an EMPTY frame ──
    _real_dl, _real_delay = _yf_download_with_retry, YF_RETRY_DELAY
    _calls = []
    try:
        globals()["YF_RETRY_DELAY"] = 0
        globals()["_yf_download_with_retry"] = lambda t, p, i: (
            _calls.append(1) or (_pd.DataFrame() if len(_calls) < 3 else _pd.DataFrame({"Close": [1.0]})))
        _out = _yf_download_nonempty("SPY", "2y", "1d")
        assert len(_calls) == 3 and not _out.empty, "two empty frames, then data: three calls"
    finally:
        globals()["_yf_download_with_retry"] = _real_dl
        globals()["YF_RETRY_DELAY"] = _real_delay
    _sr = _src_text = Path(__file__).read_text(encoding="utf-8", errors="replace")
    _gs = _sr[_sr.find("def get_spy_regime("):_sr.find("def control_bars(")]
    assert "_yf_download_nonempty(" in _gs, "get_spy_regime must fetch through the empty-frame retry"
    print("SPY regime retry        : an empty (throttled) frame is retried, not read as no regime (N8)")

    # ── N9: the alert says where the next release falls against the contract ──
    _o = {"expiry": "2026-12-04"}
    _Clock2 = type("C", (), {"now": staticmethod(lambda tz=None: _dtm.datetime(2026, 11, 3, 11, 7))})
    _real_dt2 = datetime
    try:
        globals()["datetime"] = _Clock2
        assert "THROUGH the release" in earnings_line({"next_earnings": "2026-11-12"}, _o)
        assert "after the 7-DTE exit" in earnings_line({"next_earnings": "2026-11-30"}, _o)
        assert "after this contract's expiry" in earnings_line({"next_earnings": "2027-01-28"}, _o)
        assert "unknown" in earnings_line({"unchecked": ["earnings"]}, _o)
        assert earnings_line({}, _o) is None and earnings_line({"next_earnings": "2026-11-12"}, None).startswith("Next earnings")
    finally:
        globals()["datetime"] = _real_dt2
    print("earnings line           : before the 7-DTE exit, before expiry, after expiry, unknown (N9)")

    # ── EVERY SIGNAL MUST REACH THE FORWARD LOG, INCLUDING THE SKIPS ──
    # A log of only the alerts that went out describes what the filters let
    # through, not what the rules produced. Checking that analyze() MENTIONS
    # forward_log would test a string; drive all three paths and read the file.
    import tempfile as _tf, pathlib as _pl
    _tmp = _pl.Path(_tf.mkdtemp()) / "fl.jsonl"
    _real_log = forward_log.LOG
    _real_eval = sc.evaluate
    _real_wk, _real_earn = get_weekly_trend, check_earnings_blackout
    try:
        forward_log.LOG = _tmp
        globals()["get_weekly_trend"] = lambda t: "Bullish"
        globals()["check_earnings_blackout"] = lambda t, **k: (True, "n/a")
        # DISTINCT TICKERS, because a signal's identity is
        # (ticker, trend, bar date) and these are three different signals.
        # They used to share "ZZ" and one frame, which the dedupe now — quite
        # correctly — collapses into one row.
        _ret = {}
        for _tk, _trend, _hq in (("ZA", "Bullish", True),
                                 ("ZB", "Bearish", True),
                                 ("ZC", "Bullish", False)):
            _c = dict(_canned(_trend), high_quality=_hq)
            if not _hq:
                # A base long that fails the tier on two legs, so hq_fail
                # has something to say.
                _c.update(strength="Normal", rr=0.8,
                          filters={"ADX Trend Strength": {"pass": False},
                                   "Macro Regime": {"pass": True}})
            sc.evaluate = lambda *a, **k: _c
            _ret[_tk] = analyze(_frame, _tk)
        _all_rows = forward_log.read_all(_tmp)
        _rows = [x for x in _all_rows if x["kind"] == "signal"]
        assert len(_rows) == 3, (
            f"three signals fired and {len(_rows)} were logged. The ones that "
            f"never became alerts are exactly the ones that make the record "
            f"worth having")
        # THE CONTROL ARM (amendment 2026-10-28 A): one control row beside
        # every TAKEN row, drawn from the frame in hand, none for a skip.
        _ctls = [x for x in _all_rows if x["kind"] == "control"]
        _taken_seqs = {x["seq"] for x in _rows if x["decision"] == "taken"}
        assert {c["ref_seq"] for c in _ctls} == _taken_seqs and len(_ctls) == len(_taken_seqs), (
            _ctls, _taken_seqs, "analyze() must record exactly one control per taken row")
        assert all(c["bar_date"] < x["bar_date"] for c in _ctls for x in _rows if x["seq"] == c["ref_seq"]), \
            "a control bar precedes its signal bar"
        # ONE FINGERPRINT FOR EVERY ROW (2026-10-20): the rows the REAL
        # analyze() writes must carry _scan_cfg()'s hash, the same one the
        # scan rows carry. A second config literal in analyze() split the
        # 10-20 scan's rows across two hashes; this is the path that writes
        # signal rows, so this is where the guard has to sit.
        _fp_sig = forward_log.config_fingerprint(_scan_cfg())
        assert all(x["config"] == _fp_sig for x in _rows), (
            {x["config"] for x in _rows}, _fp_sig,
            "signal rows written by analyze() carry a different fingerprint from _scan_cfg()")
        # THE TIER TAGS; IT DOES NOT GATE (results/forward_record_preregistration.md).
        _byt = {x["ticker"]: x for x in _rows}
        assert _byt["ZC"]["decision"] == "taken" and _byt["ZC"]["hq"] is False, (
            f"a long base signal that fails the HQ tier must be TAKEN with "
            f"hq=False, got {_byt['ZC']['decision']}/{_byt['ZC'].get('hq')}. "
            f"Gating the record on the tier is what starved it (BACKLOG 24)")
        assert _byt["ZC"]["setup"]["hq_fail"] == ["rr<1", "ADX Trend Strength"], \
            (_byt["ZC"]["setup"]["hq_fail"], "'not Strong' is not a leg since amendment G; the tag is recorded beside it")
        assert _byt["ZC"]["setup"]["strength"] == "Normal", "the tag is still recorded"
        assert _ret["ZC"] is None, \
            "a base signal below the tier must still NOT produce an alert payload"
        assert _byt["ZA"]["decision"] == "taken" and _byt["ZA"]["hq"] is True \
            and _byt["ZA"]["setup"]["hq_fail"] == [] and _ret["ZA"] is not None
        assert _ret["ZA"]["bar_date"] == _byt["ZA"]["bar_date"] \
            and _ret["ZA"]["signal_key"] == _byt["ZA"]["key"], (
            "the alert payload must carry the row's bar date and key, so the "
            "owner can log the trade against exactly this signal")
        assert _byt["ZB"]["decision"] == "skipped" and _ret["ZB"] is None, \
            "the direction gate still skips shorts; it rests on a measurement"

        # ── ONE ROW PER SIGNAL BAR, NOT ONE PER SCAN ──
        #
        # THE BUG THIS PINS. This module runs three times a trading day and
        # drop_partial_bar() removes today's in-progress bar, so all three runs
        # evaluate THE SAME SETTLED BAR. run()'s 4-hour cooldown does not help:
        # it is checked AFTER analyze() has already written. So one signal
        # produced three identical rows a day, in a chain that is append-only
        # and cannot be corrected afterwards.
        _c = dict(_canned("Bullish"), high_quality=True)
        sc.evaluate = lambda *a, **k: _c
        for _ in range(3):                      # three scans, one bar
            analyze(_frame, "ZD")
        _zd = [x for x in forward_log.read_all(_tmp) if x["ticker"] == "ZD" and x["kind"] == "signal"]
        assert len(_zd) == 1, (
            f"three scans of one settled bar wrote {len(_zd)} rows. The log "
            f"would count scans, not signals, and inflate any rate taken from "
            f"it roughly threefold")
        assert _zd[0]["bar_date"] == str(sc._bar_dates(_frame, None).max().date()), (
            f"the row must carry the SIGNAL BAR's date, not the scan time: "
            f"{_zd[0].get('bar_date')}")
        # A genuinely NEW bar is a new signal and must still be recorded.
        _next = _frame.copy()
        _next.index = _next.index + _pd.Timedelta(days=1)
        analyze(_next, "ZD")
        assert len([x for x in forward_log.read_all(_tmp)
                    if x["ticker"] == "ZD" and x["kind"] == "signal"]) == 2, (
            "the next bar is a new signal; deduping it away would make the log "
            "record one row per setup forever")
        print("forward log, dedupe     : 3 scans of one bar -> 1 row; "
              "the next bar -> a new row")
        _by = {(x["trend"], x["decision"]) for x in _rows}
        assert ("Bullish", "taken") in _by, _by
        assert ("Bearish", "skipped") in _by, "the direction skip was not logged"
        assert sum(1 for x in _rows if x["decision"] == "skipped") == 1, _by
        assert all(x["reason"] for x in _rows if x["decision"] == "skipped"), (
            "a skip was logged with no reason")
        assert not forward_log.verify(_tmp), forward_log.verify(_tmp)
        assert len({x["config"] for x in _rows}) == 1, (
            "one scan produced two config fingerprints")
    finally:
        forward_log.LOG = _real_log
        sc.evaluate = _real_eval
        globals()["get_weekly_trend"] = _real_wk
        globals()["check_earnings_blackout"] = _real_earn
    print("forward log             : 3 signals -> taken hq=True (alerted), "
          "taken hq=False (recorded, not alerted), short skipped; chain intact")

    # ── A BROKEN LOG MUST NOT COST AN ALERT ──
    # The record matters; it does not matter more than the trade. Nothing
    # exercised the failure path, so raising instead of warning left the suite
    # green — and a full disk would then have silenced the scanner.
    _real_rec = forward_log.record_signal
    _real_eval = sc.evaluate
    _real_wk, _real_earn = get_weekly_trend, check_earnings_blackout
    try:
        def _boom(*a, **k):
            raise OSError("disk full")
        forward_log.record_signal = _boom
        globals()["get_weekly_trend"] = lambda t: "Bullish"
        globals()["check_earnings_blackout"] = lambda t, **k: (True, "n/a")
        sc.evaluate = lambda *a, **k: _canned("Bullish")
        _got = analyze(_frame, "ZZ")
        assert _got is not None and _got["trend"] == "Bullish", (
            "a failed log write killed the alert. The record matters; it does "
            "not matter more than the trade, and a full disk must not silence "
            "the scanner")
    finally:
        forward_log.record_signal = _real_rec
        sc.evaluate = _real_eval
        globals()["get_weekly_trend"] = _real_wk
        globals()["check_earnings_blackout"] = _real_earn
    print("log failure             : alert still goes out, warning logged")

    wl = ["AAA", "BBB", "CCC"]

    assert is_total_outage(["AAA (no data)", "BBB (x)", "CCC (y)"], wl) is True
    print("total outage            : all tickers failed -> True")

    assert is_total_outage(["AAA (no data)"], wl) is False, \
        "a partial failure is not an outage — the scan still looked at the rest"
    assert is_total_outage([], wl) is False
    print("partial / no failure    : not an outage")

    # An empty watchlist must not page you forever. len([]) == len([]) is the
    # trap: a misconfigured universe would alert on every scheduled run.
    assert is_total_outage([], []) is False, \
        "an empty watchlist is a config problem, not a data outage — and " \
        "alerting on it every run would train you to ignore the alert"
    print("empty watchlist         : not an outage (no false page)")

    # The cooldown that stops a day-long outage becoming one alert per run.
    st_ = {}
    assert recently_alerted(st_, "SCANNER", "OUTAGE") is False
    st_["SCANNER:OUTAGE"] = time.time()
    assert recently_alerted(st_, "SCANNER", "OUTAGE") is True
    st_["SCANNER:OUTAGE"] = time.time() - (ALERT_COOLDOWN_HRS * 3600) - 1
    assert recently_alerted(st_, "SCANNER", "OUTAGE") is False, \
        "the outage alert must fire again once the cooldown has elapsed"
    print(f"outage alert cooldown   : one per {ALERT_COOLDOWN_HRS}h, then re-arms")

    # ══════════════════════════════════════════════════════════════
    # COVERAGE — the alarm for silence (BACKLOG 22 / 26)
    # ══════════════════════════════════════════════════════════════
    import tempfile as _tf2, pathlib as _pl2, json as _json2
    from datetime import datetime as _dt2, date as _d2

    # trading_days_between: weekends and holidays are not sessions
    assert trading_days_between(_d2(2026, 10, 2), _d2(2026, 10, 5)) == 1, \
        "Fri -> Mon is ONE session, not three days"
    assert trading_days_between(_d2(2026, 9, 4), _d2(2026, 9, 8)) == 1, \
        "Fri -> Tue across Labor Day (09-07) is ONE session"
    assert trading_days_between(_d2(2026, 10, 7), _d2(2026, 10, 7)) == 0
    print("sessions between        : weekends and holidays excluded")

    _tmpl = _pl2.Path(_tf2.mkdtemp()) / "cov.jsonl"
    _real_log2 = forward_log.LOG
    _real_send2 = globals()["send_alert"]
    _sent: list = []
    try:
        forward_log.LOG = _tmpl
        globals()["send_alert"] = lambda m, dry_run=False: (_sent.append(m) or True)
        _now = _dt2(2026, 10, 7, 12, 0, tzinfo=ET)
        _pos = _pl2.Path(_tf2.mkdtemp()) / "pos.json"

        # No scan row at all -> the report says so, the check alerts.
        _pos.write_text("[]")
        _rep = coverage_report(now=_now, positions_path=_pos)
        assert _rep["sessions_unscanned"] is None and _rep["last_scan_bar"] is None
        # A run that scanned bar 10-06 on 10-07: 0 unscanned (yesterday is the
        # newest bar a scan can evaluate).
        forward_log.record_scan("2026-10-06", ["TMO"], 0, {"k": 1})
        _rep = coverage_report(now=_now, positions_path=_pos)
        assert _rep["sessions_unscanned"] == 0, _rep
        # THE THREE-WEEK FAILURE, reproduced: last scan evaluated 10-01, today is
        # 10-07 -> bars 10-02, 10-05, 10-06 unscanned = 3 sessions.
        _tmpl.write_text("")
        forward_log.record_scan("2026-10-01", ["TMO"], 2, {"k": 1})
        _rep = coverage_report(now=_now, positions_path=_pos)
        assert _rep["sessions_unscanned"] == 3, _rep
        print("coverage report         : none / current / 3 sessions silent")

        # Stale position: checked 10-02, today 10-07 -> 3 sessions.
        _ep = _dt2(2026, 10, 2, 14, 0, tzinfo=ET).timestamp()
        _pos.write_text(_json2.dumps([
            {"ticker": "PFE", "status": "EXIT_SIGNALLED", "last_check_epoch": _ep},
            {"ticker": "NVDA", "status": "OPEN", "last_check_epoch": _now.timestamp()},
            {"ticker": "OLD", "status": "CLOSED", "last_check_epoch": _ep},
            {"ticker": "NEW", "status": "OPEN"}]))
        _rep = coverage_report(now=_now, positions_path=_pos)
        assert ("PFE", 3) in _rep["stale_positions"], _rep
        assert ("NEW", None) in _rep["stale_positions"], "never-checked is stale"
        assert all(t != "NVDA" for t, _ in _rep["stale_positions"]), \
            "a position checked today is not stale"
        assert all(t != "OLD" for t, _ in _rep["stale_positions"]), \
            "a CLOSED position is nobody's to check"
        print("stale positions         : 3 sessions and never-checked flagged; "
              "today and CLOSED not")

        # coverage_check: alerts, then cooldown, then dry-run leaves no state.
        # Point it at the temp positions file by monkeypatching the default.
        import scanner as _self
        _orig_cr = _self.coverage_report
        _self.coverage_report = lambda now=None: _orig_cr(now=now, positions_path=_pos)
        globals()["coverage_report"] = _self.coverage_report
        try:
            _st: dict = {}
            assert coverage_check(_st, now=_now) is True and len(_sent) == 1
            assert "No scan has run for 3 trading day" in _sent[0], _sent[0]
            assert "PFE (3 session" in _sent[0], _sent[0]
            assert "SCANNER:COVERAGE" in _st, "the alert must be recorded for cooldown"
            assert coverage_check(_st, now=_now) is False and len(_sent) == 1, \
                "a second check inside the cooldown must not re-page"
            _st2: dict = {}
            assert coverage_check(_st2, now=_now, dry_run=True) is True
            assert "SCANNER:COVERAGE" not in _st2, "dry run must not touch state"
            # And a healthy state is silent.
            _tmpl.write_text("")
            forward_log.record_scan("2026-10-06", ["TMO"], 0, {"k": 1})
            _pos.write_text("[]")
            _n = len(_sent)
            assert coverage_check({}, now=_now) is False and len(_sent) == _n
            print("coverage check          : pages once, cools down 20h, dry-run "
                  "stateless, silent when healthy")
        finally:
            _self.coverage_report = _orig_cr
            globals()["coverage_report"] = _orig_cr

        # ── run() WRITES A SCAN ROW — the heartbeat is produced, not just read ──
        _tmpl.write_text("")
        import pandas as _pd2
        # ENDS IN THE PAST, so drop_partial_bar() never trims it. A range that
        # ran past today lost its last bars during the session and kept them
        # after the close, and this test passed or failed on the clock.
        _bars = _pd2.bdate_range(end="2026-06-30", periods=MIN_BARS_AFTER_WARMUP + 20)
        _fr = _pd2.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0,
                              "Close": 100.0, "Volume": 1e6}, index=_bars)
        _g = globals()
        _saved = {k: _g[k] for k in ("get_data", "compute", "analyze",
                                     "get_spy_regime", "save_state", "load_state",
                                     "WATCHLIST", "FETCH_GAP_SEC", "pool_tickers")}
        try:
            _g["pool_tickers"] = lambda wl: []        # the pool has its own harness
            _g["get_data"] = lambda tk: _fr
            _g["compute"] = lambda df: df
            _g["analyze"] = lambda df, tk, spy_regime=None, tally=None: None
            _g["get_spy_regime"] = lambda: None
            _g["save_state"] = lambda st: None
            _g["load_state"] = lambda: {}
            _g["WATCHLIST"] = ["AAA", "BBB"]
            _g["FETCH_GAP_SEC"] = 0
            import argparse as _ap2
            run(_ap2.Namespace(force=True, dry_run=True))
        finally:
            _g.update(_saved)
        _rows = forward_log.read_all(_tmpl)
        _scans = [r for r in _rows if r["kind"] == "scan"]
        assert len(_scans) == 1, f"one bar scanned -> one scan row, got {len(_scans)}"
        assert _scans[0]["tickers"] == ["AAA", "BBB"] and _scans[0]["n_signals"] == 0
        assert _scans[0]["bar_date"] == str(_bars[-1].date()), _scans[0]
        print(f"run() heartbeat         : one scan row, bar {_scans[0]['bar_date']}, "
              f"2 tickers, 0 signals (written on a dry run too)")

        # ── ONE ALERT PER SIGNAL BAR (logic review 2026-10-29, L1) ──
        # The 4-hour stamp is cleared between runs here, as a late dispatch
        # would clear it in effect; the delivered alert row still suppresses
        # the same bar, a failed delivery does not, and a new bar alerts.
        _tmpl.write_text("")
        _sentk: list = []
        _bark = str(_bars[-1].date())
        def _hq(df, tk, spy_regime=None, tally=None, bucket="live"):
            return {"ticker": tk, "trend": "Bullish", "strength": "Normal", "price": 100.0,
                    "entry": 100.0, "stop": 98.0, "target": 106.0, "rr": 3.0, "rsi": 61.0,
                    "adx": 35.0, "atr": 2.0, "filters_pass": 4, "filters_total": 4,
                    "bar_date": _bark, "signal_key": forward_log.signal_key(tk, "Bullish", _bark)}
        _savedk = {k: _g[k] for k in ("get_data", "compute", "analyze", "get_spy_regime",
                                      "save_state", "load_state", "WATCHLIST", "FETCH_GAP_SEC",
                                      "pool_tickers", "suggest_option", "send_alert")}
        try:
            _g["pool_tickers"] = lambda wl: []
            _g["get_data"] = lambda tk: _fr
            _g["compute"] = lambda df: df
            _g["analyze"] = _hq
            _g["get_spy_regime"] = lambda: None
            _g["save_state"] = lambda st: None
            _g["suggest_option"] = lambda *a, **k: None
            _g["WATCHLIST"] = ["AAA"]
            _g["FETCH_GAP_SEC"] = 0
            _g["send_alert"] = lambda m, dry_run=False: (_sentk.append(m) or True)
            _g["load_state"] = lambda: {}                    # no stamp, every run
            run(_ap2.Namespace(force=True, dry_run=False))
            assert len(_sentk) == 1, "first run sends"
            run(_ap2.Namespace(force=True, dry_run=False))
            assert len(_sentk) == 1, "the same bar is not alerted twice once a delivered row exists"
            assert alerted_on_bar(forward_log.signal_key("AAA", "Bullish", _bark)) and \
                not alerted_on_bar(forward_log.signal_key("AAA", "Bullish", "2026-01-02")) and \
                not alerted_on_bar(None), "the key, the whole key"
            # A FAILED delivery leaves no delivered row: the next run re-sends.
            _tmpl.write_text("")
            _g["send_alert"] = lambda m, dry_run=False: False
            run(_ap2.Namespace(force=True, dry_run=False))
            assert len([r for r in forward_log.read_all(_tmpl) if r["kind"] == "alert" and not r["delivered"]]) == 1
            _g["send_alert"] = lambda m, dry_run=False: (_sentk.append(m) or True)
            run(_ap2.Namespace(force=True, dry_run=False))
            assert len(_sentk) == 2, "a failed delivery does not suppress the re-send"
            assert len([r for r in forward_log.read_all(_tmpl) if r["kind"] == "alert"]) == 2
            print("one alert per signal bar : a delivered alert row suppresses the same key with no "
                  "stamp; a failed delivery re-sends; another bar or no key never suppresses")
            # ── THE FILL CHECK BEFORE THE ALERT (review 2026-10-30, J1) ──
            # The raw frame carries one bar AFTER the signal bar (the fill);
            # compute() hands back the frame without it, as drop_partial_bar()
            # does during the session. A fill beyond the stop withholds the
            # alert and records ONE "NOT SENT" row (not one per run); a last
            # print beyond the target withholds too; a fill inside sends.
            _tmpl.write_text("")
            del _sentk[:]
            _g["compute"] = lambda df: df.iloc[:-1]
            def _with_fill(open_px, close_px):
                _nxt = _pd2.DataFrame({"Open": [open_px], "High": [max(open_px, close_px) + 1], "Low": [min(open_px, close_px) - 1],
                                       "Close": [close_px], "Volume": [1e6]},
                                      index=_pd2.bdate_range(start=_bars[-1], periods=2)[1:])
                return _pd2.concat([_fr, _nxt])
            _g["get_data"] = lambda tk: _with_fill(97.0, 97.5)      # opened below the stop (98)
            run(_ap2.Namespace(force=True, dry_run=False))
            run(_ap2.Namespace(force=True, dry_run=False))
            _ns = [r for r in forward_log.read_all(_tmpl) if r["kind"] == "alert"]
            assert not _sentk, "a fill beyond the stop is not alerted"
            assert len(_ns) == 1 and not _ns[0]["delivered"] and _ns[0]["header"].startswith("NOT SENT") \
                and "beyond the stop" in _ns[0]["header"] and _ns[0]["key"] == forward_log.signal_key("AAA", "Bullish", _bark), _ns
            assert withheld_on_bar(_ns[0]["key"]) and not withheld_on_bar(None)
            _g["get_data"] = lambda tk: _with_fill(100.0, 107.0)    # opened inside, ran through the target (106)
            run(_ap2.Namespace(force=True, dry_run=False))
            assert not _sentk and len([r for r in forward_log.read_all(_tmpl) if r["kind"] == "alert"]) == 1, \
                "withheld again, and the key already has its NOT SENT row"
            _tmpl.write_text("")
            run(_ap2.Namespace(force=True, dry_run=False))
            assert not _sentk and "last price" in [r for r in forward_log.read_all(_tmpl) if r["kind"] == "alert"][-1]["header"]
            _g["get_data"] = lambda tk: _with_fill(100.0, 101.0)    # a fill inside the levels
            run(_ap2.Namespace(force=True, dry_run=False))
            assert len(_sentk) == 1, "a fill inside the levels is alerted"
            # THE PRICE AT SEND (review 2026-11-03, N2): the alert prints where the
            # stock is now against the signal close's levels, and the alert row
            # carries the same numbers; nothing is withheld on them.
            assert "Now 101.00 (open 100.00): +0.50 R from the signal close; R:R from here 1.67" in _sentk[0] \
                and "under the tier" not in _sentk[0], _sentk[0]
            _as = [r for r in forward_log.read_all(_tmpl) if r["kind"] == "alert" and r["delivered"]][-1].get("at_send")
            assert _as == {"open": 100.0, "last": 101.0, "moved_r": 0.5, "rr_now": 1.67, "below_hq": False}, _as
            _w = at_send({"trend": "Bullish", "entry": 100.0, "stop": 97.5, "target": 103.18}, 102.1, 102.3)
            assert _w["below_hq"] and _w["rr_now"] == 0.18 and _w["moved_r"] == 0.92, _w
            assert at_send({"trend": "Bullish", "entry": 100.0, "stop": 97.5, "target": 103.18}, None, None) is None \
                and at_send({"trend": "Bullish"}, 100.0, 101.0) is None, "no price, or no levels, prints nothing"
            assert "under the tier's 1.0" in compose_alert({**_hq(_fr, "AAA"), "at_send": _w}, "AAA")
            # N4: the contract's strike window is centred on the price at send,
            # the close only when the run has none.
            _centres = []
            _g["suggest_option"] = lambda tk, px, *a, **k: _centres.append(px)
            compose_alert({**_hq(_fr, "AAA"), "at_send": _w}, "AAA")
            compose_alert({**_hq(_fr, "AAA"), "at_send": {**_w, "last": None}}, "AAA")
            compose_alert(_hq(_fr, "AAA"), "AAA")
            assert _centres == [102.3, 102.1, 100.0], _centres
            _g["suggest_option"] = lambda *a, **k: None
            # ── D4 and D1 (owner 2026-11-03: "go with D1, D2, D4 and D5") ──
            # D4: opened inside the levels, but by the alert the last print
            # (104) leaves R:R 0.33 to the 106 target: withheld, one NOT SENT
            # row carrying the prices at send; the record is untouched.
            import datetime as _dt4
            _tmpl.write_text("")
            del _sentk[:]
            _g["get_data"] = lambda tk: _with_fill(100.0, 104.0)
            run(_ap2.Namespace(force=True, dry_run=False))
            run(_ap2.Namespace(force=True, dry_run=False))
            _ns4 = [r for r in forward_log.read_all(_tmpl) if r["kind"] == "alert"]
            assert not _sentk and len(_ns4) == 1 and "R:R from the price at send 0.33" in _ns4[0]["header"] \
                and _ns4[0]["at_send"]["rr_now"] == 0.33, _ns4
            # D1: the next release 5 days out: every 21-45 DTE contract would hold
            # through it (its 7-DTE exit is after the release), so nothing is sent.
            _tmpl.write_text("")
            _g["get_data"] = lambda tk: _with_fill(100.0, 101.0)
            _today4 = datetime.now(ET).date()
            _g["analyze"] = lambda df, tk, **k: dict(_hq(df, tk), next_earnings=(_today4 + _dt4.timedelta(days=5)).isoformat())
            run(_ap2.Namespace(force=True, dry_run=False))
            _ns1 = [r for r in forward_log.read_all(_tmpl) if r["kind"] == "alert"]
            assert not _sentk and len(_ns1) == 1 and "would be held through it" in _ns1[0]["header"], _ns1
            # a release 30 days out narrows the expiries the alert may name: at most 36 DTE
            # (a 36-DTE contract's 7-DTE exit is on day 29, before the release on day 30)
            _kw = []
            _g["suggest_option"] = lambda *a, **k: _kw.append(k)
            _g["analyze"] = lambda df, tk, **k: dict(_hq(df, tk), next_earnings=(_today4 + _dt4.timedelta(days=30)).isoformat())
            _tmpl.write_text("")
            run(_ap2.Namespace(force=True, dry_run=False))
            assert len(_sentk) == 1 and _kw and _kw[-1].get("max_dte") == 36 and _kw[-1].get("target") == 106.0, (_kw, _sentk)
            assert "Expiry kept to 36 DTE" not in _sentk[0] and "only expiries that exit before earnings" in _sentk[0], _sentk[0]
            _g["analyze"] = _hq
            _g["suggest_option"] = lambda *a, **k: None
            # the rule's edges, unrounded: 21 DTE is the floor; a release 15 days out
            # leaves exactly 21 (exit on day 14, before the release), 14 days out 20.
            _t0 = _dt4.date(2026, 11, 3)
            _w = lambda ne, a=None: withhold_before_send({"rr": 1.5, "next_earnings": ne, "at_send": a}, _t0)  # noqa: E731
            assert _w("2026-11-18") is None and earnings_cap_dte({"next_earnings": "2026-11-18"}, _t0) == 21
            assert "would be held through it" in _w("2026-11-17")
            assert _w(None) is None and _w("not a date") is None, "an unknown release fails open (the alert says so)"
            assert "R:R from the price at send" in _w(None, {"below_hq": True, "rr_now": 0.4}) and \
                _w(None, {"below_hq": False, "rr_now": 1.2}) is None and _w(None, None) is None
            # the chain fetch drops every expiry past the cap; the named contract says why
            _t_et = option_chain.et_today()
            _exps = [(_t_et + _pd2.Timedelta(days=d)).strftime("%Y-%m-%d") for d in (22, 30, 37)]
            _cf = _pd2.DataFrame([(100.0, 2.0, 2.1, 50, 500)], columns=["strike", "bid", "ask", "volume", "openInterest"])
            class _TkStub:
                options = _exps
                def __init__(self, t): pass
                def option_chain(self, e):
                    return type("C", (), {"calls": _cf, "puts": _cf})()
            _real_tk, _real_sleep = yf.Ticker, time.sleep
            try:
                yf.Ticker, time.sleep = _TkStub, (lambda *_a: None)
                assert [e["dte"] for e in fetch_chain_data("X", max_dte=30)["expiries"]] == [22, 30]
                assert [e["dte"] for e in fetch_chain_data("X")["expiries"]] == [22, 30, 37]
            finally:
                yf.Ticker, time.sleep = _real_tk, _real_sleep
            _optd = {"right": "CALL", "strike": 100.0, "expiry": _exps[0], "dte": 22, "mid": 2.05, "bid": 2.0,
                     "ask": 2.1, "spread_pct": 4.9, "volume": 50, "oi": 500, "cost": 205.0, "pct_account": 4.1,
                     "within_budget": True, "budget": 250.0, "dte_adequate": True, "days_needed": 12,
                     "moneyness_pct": 0.0}
            _g["suggest_option"] = lambda *a, **k: _optd
            _msg1 = compose_alert(dict(_hq(_fr, "AAA"), next_earnings=(_today4 + _dt4.timedelta(days=30)).isoformat()), "AAA")
            assert "Expiry kept to 36 DTE or less so the 7-DTE exit comes before earnings" in _msg1 and \
                "strike +0.0% vs price" in _msg1, _msg1
            assert "Expiry kept to" not in compose_alert(dict(_hq(_fr, "AAA"), next_earnings=(
                _today4 + _dt4.timedelta(days=60)).isoformat()), "AAA"), "a cap past 45 DTE does not bind"
            _g["suggest_option"] = lambda *a, **k: None
            print("D4 / D1 before send     : R:R under 1.0 at send is withheld; a release before every "
                  "contract's exit is withheld; a later one caps the expiry; unknown sends (owner 2026-11-03)")
            print("price at send            : the alert prints the move already made and the R:R from "
                  "the latest price, warns under the tier's R:R, and the alert row carries it (N2)")
            assert gapped_at_fill({"trend": "Bearish", "stop": 102.0, "target": 94.0}, 103.0) and \
                gapped_at_fill({"trend": "Bearish", "stop": 102.0, "target": 94.0}, 100.0, 93.0) and \
                gapped_at_fill({"trend": "Bearish", "stop": 102.0, "target": 94.0}, 100.0, 100.0) is None and \
                gapped_at_fill({"trend": "Bullish", "stop": 98.0, "target": 106.0}, None, None) is None and \
                gapped_at_fill({"trend": "Bullish", "stop": 98.0, "target": 106.0}, float("nan")) is None and \
                gapped_at_fill({"trend": "Bullish"}, 50.0) is None, "the short side mirrors; unreadable prices never withhold"
            # THE GRADER IS ASKED, NOT COPIED (review 2026-11-03, N1): an open
            # inside the risk floor of the stop, or within the fill slippage of
            # the target, is a fill the grader voids; the restated rule sent both.
            _rf = {"trend": "Bullish", "stop": 97.5, "target": 105.0}
            assert "risk floor" in (gapped_at_fill(_rf, 97.6) or "") and \
                "slippage" in (gapped_at_fill(_rf, 104.99) or "") and gapped_at_fill(_rf, 97.8) is None and \
                "risk floor" in (gapped_at_fill(_rf, 100.0, 97.55) or "") and gapped_at_fill(_rf, 0.0) is None, \
                "the gate withholds exactly what the grader voids: the risk floor and the slippage too (N1)"
            assert fill_probe(_fr, _fr) == (None, None), "a frame ending on the signal bar has no fill yet"
            print("fill check before alert : a next open beyond the stop, or a last print beyond the target, "
                  "withholds the alert and records one NOT SENT row; a fill inside sends (J1)")
        finally:
            _g.update(_savedk)
    finally:
        forward_log.LOG = _real_log2
        globals()["send_alert"] = _real_send2

    # ── THE POST-CLOSE WINDOW ──
    _wed = lambda h, m: _dt2(2026, 10, 7, h, m, tzinfo=ET)       # a Wednesday
    assert is_post_close_window(_wed(16, 20)) is True
    assert is_post_close_window(_wed(18, 0)) is True, "inclusive at the end"
    assert is_post_close_window(_wed(16, 0)) is False, \
        "16:00:00 is still the session to is_market_open(); never both"
    assert is_post_close_window(_wed(15, 59)) is False
    assert is_post_close_window(_wed(18, 1)) is False
    assert is_post_close_window(_dt2(2026, 10, 10, 16, 20, tzinfo=ET)) is False, "Saturday"
    assert is_post_close_window(_dt2(2026, 11, 26, 16, 20, tzinfo=ET)) is False, "Thanksgiving"
    assert is_post_close_window(_dt2(2026, 11, 27, 13, 30, tzinfo=ET)) is True, \
        "half day closes at 13:00"
    assert is_post_close_window(_dt2(2026, 11, 27, 16, 20, tzinfo=ET)) is False, \
        "half day: 16:20 is three hours after the close"
    for _t in (_wed(16, 20), _wed(18, 0), _dt2(2026, 11, 27, 13, 30, tzinfo=ET)):
        assert not is_market_open(_t), "the two windows must never overlap"
    print("post-close window       : 16:00 < t <= 18:00 ET on trading days, "
          "13:00-15:00 on a half day, disjoint from the session")

    # ── STATE PRUNING ──
    _now_ep = 1_800_000_000.0
    _st3 = {"AAPL:Bullish": _now_ep - 31 * 86400,
            "NVDA:Bullish": _now_ep - 3600,
            "SCANNER:COVERAGE": _now_ep - 30 * 86400 - 1,
            "SCANNER:OUTAGE": _now_ep - 29 * 86400,
            "odd": "not a number", "flag": True}
    assert prune_state(_st3, now=_now_ep) == 2, _st3
    assert set(_st3) == {"NVDA:Bullish", "SCANNER:OUTAGE", "odd", "flag"}, _st3
    assert prune_state(_st3, now=_now_ep) == 0, "idempotent"
    print("state pruning           : stamps older than 30 days dropped, "
          "fresh / non-numeric kept")

    # ── RECORD-ONLY: rows and heartbeat, no alert; n_signals counts skipped ──
    _tmpl3 = _pl2.Path(_tf2.mkdtemp()) / "ro.jsonl"
    _real_log3 = forward_log.LOG
    _real_send3 = globals()["send_alert"]
    _sent3: list = []
    try:
        forward_log.LOG = _tmpl3
        globals()["send_alert"] = lambda m, dry_run=False: (_sent3.append(m) or True)
        import pandas as _pd3
        _bars3 = _pd3.bdate_range(end="2026-06-30", periods=MIN_BARS_AFTER_WARMUP + 20)
        _fr3 = _pd3.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0,
                               "Close": 100.0, "Volume": 1e6}, index=_bars3)

        def _fake_analyze(df, tk, spy_regime=None, tally=None):
            # AAA: a tradeable signal. BBB: a signal the rules produced but
            # skipped (returns None, like the direction gate). CCC: blocked.
            if tk in ("AAA", "BBB") and tally is not None:
                tally["signals"] = tally.get("signals", 0) + 1
            if tk == "AAA":
                return {"ticker": tk, "trend": "Bullish", "strength": "Strong",
                        "price": 100.0, "entry": 100.0, "stop": 98.0,
                        "target": 106.0, "rr": 3.0, "rsi": 55.0, "adx": 40.0,
                        "atr": 1.6, "filters_pass": 4, "filters_total": 4}
            return None

        _g = globals()
        _saved = {k: _g[k] for k in ("get_data", "compute", "analyze",
                                     "get_spy_regime", "save_state", "load_state",
                                     "WATCHLIST", "FETCH_GAP_SEC", "suggest_option",
                                     "is_post_close_window", "fetch_atm_chain",
                                     "IV_GRADE_FETCH_CAP", "trading_days_between",
                                     "basket_record_pass", "spread_record_pass")}
        _saved_state: dict = {}
        _iv_tmp = _pl2.Path(_tf2.mkdtemp()) / "iv.jsonl"
        _real_iv_log = iv_snapshot.LOG
        _chain_fetches: list = []
        def _fake_chain(tk, today=None):
            _chain_fetches.append(tk)
            return {"expiry": "2026-07-31", "dte": 31,
                    "calls": [{"strike": 100.0, "impliedVolatility": 0.42}],
                    "puts": [{"strike": 100.0, "impliedVolatility": 0.44}]}
        # DEPARTED NAMES ARE FETCHED FOR IV GRADING (N05), AND THE WINDOW IS
        # CHECKED ON THE EXCHANGE CALENDAR (N04). Three names left the watchlist
        # with snapshots old enough to settle. Cap 2: DDD (whole history) and
        # DDE (history missing one midweek session inside the window) are
        # fetched, EEE is deferred. DDD is graded; DDE is held as a gap, which
        # only the injected calendar can see (a lone missing Tuesday is 2
        # calendar days, under the stdlib fallback's 4). The fixture is a
        # plain business-day range, so the calendar injected is weekdays.
        _iv_departed_date = str(_bars3[-25].date())
        for _nm in ("DDD", "DDE", "EEE"):
            iv_snapshot.record(_iv_departed_date, _nm, "2026-07-31", 31, 100.0, 100.0, 43.0, 0.0,
                               "yahoo", path=_iv_tmp)
        _midweek = next(k for k in range(-20, -6) if _bars3[k].weekday() in (1, 2, 3))
        _fr3_holed = _fr3.drop(_bars3[_midweek])
        _got: list = []
        try:
            iv_snapshot.LOG = _iv_tmp
            _g["IV_GRADE_FETCH_CAP"] = 2
            # The basket pass would spend the cap on the real snapshot's names
            # and SPY; it has its own test below.
            _g["basket_record_pass"] = lambda closes_for, snap: "stubbed in this harness"
            _g["spread_record_pass"] = lambda *a, **k: "stubbed in this harness"
            _g["trading_days_between"] = lambda a, b: len(_pd3.bdate_range(a, b)) - 1
            _g["fetch_atm_chain"] = _fake_chain
            _g["get_data"] = lambda tk: (_got.append(tk) or (_fr3_holed if tk == "DDE" else _fr3))
            _g["compute"] = lambda df: df
            _g["analyze"] = _fake_analyze
            _g["get_spy_regime"] = lambda: None
            _g["save_state"] = lambda st: _saved_state.update(st)
            _g["load_state"] = lambda: {}
            _g["WATCHLIST"] = ["AAA", "BBB", "CCC"]
            _g["FETCH_GAP_SEC"] = 0
            # RECORDS the call rather than raising: run() isolates each
            # ticker in try/except, so a stub that raised was swallowed
            # there and the alert path it was meant to expose never ran.
            _chain_calls: list = []
            _g["suggest_option"] = lambda *a, **k: (_chain_calls.append(a) or None)
            import argparse as _ap3
            # Outside the window, not forced: nothing happens at all.
            _g["is_post_close_window"] = lambda now=None: False
            run(_ap3.Namespace(force=False, dry_run=False, record_only=True))
            assert not forward_log.read_all(_tmpl3) and not _sent3, \
                "a record-only run outside its window must not scan"
            # Inside the window: rows + heartbeat, no alert, no state.
            _g["is_post_close_window"] = lambda now=None: True
            run(_ap3.Namespace(force=False, dry_run=False, record_only=True))
            # THE REPEAT IS SKIPPED (review 2026-10-28, improvement 4): a
            # second record-only run inside today's window writes nothing;
            # --force runs it; a heartbeat from another day does not skip.
            _n_rows = len(forward_log.read_all(_tmpl3))
            assert post_close_already_recorded() is not None, "today's heartbeat must be found"
            run(_ap3.Namespace(force=False, dry_run=False, record_only=True))
            assert len(forward_log.read_all(_tmpl3)) == _n_rows, "the repeat pass must write nothing"
            _n_got, _n_chain = len(_got), len(_chain_fetches)
            _iv_bytes, _fl_bytes = _iv_tmp.read_bytes(), _pl2.Path(_tmpl3).read_bytes()
            run(_ap3.Namespace(force=True, dry_run=False, record_only=True))
            assert len(forward_log.read_all(_tmpl3)) > _n_rows and len(_got) > _n_got, \
                "--force runs the pass past today's heartbeat"
            # The forced repeat's rows, fetches, chain calls and IV grades are
            # not the tests' below: all four are put back as they were.
            del _got[_n_got:]
            del _chain_fetches[_n_chain:]
            _iv_tmp.write_bytes(_iv_bytes)
            _pl2.Path(_tmpl3).write_bytes(_fl_bytes)
            _hb = forward_log.last_scan(_tmpl3, bucket="live")
            assert _hb.get("pass") == "post_close", (_hb, "the record pass stamps its heartbeat")
            assert post_close_already_recorded(now=datetime.fromisoformat(_hb["ts"]).astimezone(ET)
                                               + timedelta(days=1)) is None, \
                "a heartbeat from another day is not today's"
            _cl = clocks_line()
            assert all(w in _cl for w in ("pool ", "/349", "bar revisions", "moved a decision or the tag", "basket ", "/52", "spread ", "/12", "IV premium")), _cl
            print("post-close repeat       : skipped on today's heartbeat; clocks line reads every forward log")
        finally:
            _g.update(_saved)
            iv_snapshot.LOG = _real_iv_log
        assert not _sent3, f"record-only sent an alert: {_sent3}"
        _departed_fetched = [t for t in _got if t in ("DDD", "DDE", "EEE")]
        assert sorted(_departed_fetched) == ["DDD", "DDE"], (
            _got, "two departed names are fetched under a cap of 2, in name order; EEE is deferred")
        _prem3 = {r["ticker"] for r in iv_snapshot.read_all(_iv_tmp) if r["kind"] == "premium"}
        assert _prem3 == {"DDD"}, (
            _prem3, "DDD graded from its fetch; DDE held (a session missing inside its window); "
                    "EEE deferred, not graded")
        _p3 = next(r for r in iv_snapshot.read_all(_iv_tmp) if r["kind"] == "premium")
        assert _p3["anchor"] == _iv_departed_date and _p3["window_end"] == str(_bars3[-4].date()) \
            and len(_p3["closes_hash"]) == 16, _p3
        print("iv departed names       : fetched within the cap, graded on the exact anchor; a missing "
              "midweek session is held by the exchange calendar; the rest deferred")

        # ── THE RS BASKET ROW (BACKLOG 34 B): once per snapshot, anchored on the
        # last settled session on or before the snapshot date, every name AND
        # the benchmark on that same session, else nothing is written.
        import tempfile as _tf_bk, pathlib as _pl_bk
        _bk_tmp = _pl_bk.Path(_tf_bk.mkdtemp()) / "bk.jsonl"
        _real_bk = basket_log.LOG
        try:
            basket_log.LOG = _bk_tmp
            _bk_bars = [{"date": str(d.date()), "close": 100.0 + i} for i, d in enumerate(_bars3)]
            _bk_snap = {"as_of": "2026-06-28", "tickers": ["AAA", "BBB"]}       # a Sunday; last bar 06-26
            _bk_calls: list = []
            _bk_closes = lambda tk: (_bk_calls.append(tk) or _bk_bars)
            _msg = basket_record_pass(_bk_closes, _bk_snap)
            _bk_rows = basket_log.read_all(_bk_tmp)
            assert len(_bk_rows) == 1 and _bk_rows[0]["as_of"] == "2026-06-28" \
                and _bk_rows[0]["anchor"] == "2026-06-26" and _bk_rows[0]["names"] == ["AAA", "BBB"] \
                and _bk_rows[0]["bench"] == "SPY" and sorted(_bk_calls) == ["AAA", "BBB", "SPY"], (_msg, _bk_rows)
            assert "already recorded" in basket_record_pass(_bk_closes, _bk_snap), "once per snapshot"
            _msg2 = basket_record_pass(lambda tk: None if tk == "SPY" else _bk_bars,
                                       {"as_of": "2026-07-05", "tickers": ["AAA", "BBB"]})
            assert "not recorded" in _msg2 and len(basket_log.read_all(_bk_tmp)) == 1, \
                (_msg2, "no benchmark close -> no row, never a basket without its benchmark")
            _late = _bk_bars + [{"date": "2026-07-01", "close": 1.0}]
            _msg3 = basket_record_pass(lambda tk: _late if tk == "AAA" else _bk_bars,
                                       {"as_of": "2026-07-05", "tickers": ["AAA", "BBB"]})
            assert "not recorded" in _msg3 and "anchors" in _msg3, \
                (_msg3, "names on different anchor sessions -> no row")
            assert "no snapshot" in basket_record_pass(_bk_closes, None)
        finally:
            basket_log.LOG = _real_bk
        print("rs basket row           : once per snapshot on one shared anchor; no benchmark or "
              "mixed anchors -> nothing written")

        # ── THE SPREAD ARMS (BACKLOG 34 E): one cycle per arm without an open
        # one, on the ticker's last settled close, the vol index closed on the
        # same session, the first listed expiry >= DTE_DAYS out, the model's
        # legs and credit, and the market's quotes on those legs.
        _sp_tmp = _pl_bk.Path(_tf_bk.mkdtemp()) / "sp.jsonl"
        _real_sp = spread_log.LOG
        try:
            spread_log.LOG = _sp_tmp
            _sp_bars = {"SPY": [{"date": "2026-06-25", "close": 398.0}, {"date": "2026-06-26", "close": 400.0}],
                        "QQQ": [{"date": "2026-06-25", "close": 348.0}, {"date": "2026-06-26", "close": 350.0}]}
            _sp_vol = {"^VIX": {"date": "2026-06-26", "close": 18.0}, "^VXN": {"date": "2026-06-26", "close": 22.0}}
            _sp_chain_calls: list = []
            def _sp_chain(tk, md):
                _sp_chain_calls.append((tk, md))
                spot = _sp_bars[tk][-1]["close"]
                strikes = range(int(spot * 0.90), int(spot * 1.10) + 1)
                return {"expiry": "2026-07-27", "dte": 31,
                        "calls": [{"strike": float(k), "bid": 1.00, "ask": 1.10} for k in strikes],
                        "puts": [{"strike": float(k), "bid": 2.00, "ask": 2.20} for k in strikes]}
            _msg = spread_record_pass(lambda tk: _sp_bars.get(tk), lambda nm: _sp_vol.get(nm), _sp_chain)
            _sp_rows = spread_log.read_all(_sp_tmp)
            assert len(_sp_rows) == 8 and "8 cycle(s) opened" in _msg and sorted(_sp_chain_calls) == [("QQQ", 30), ("SPY", 30)], \
                (_msg, len(_sp_rows), _sp_chain_calls)
            _r0 = next(r for r in _sp_rows if r["arm"] == "SPY put_spread w10 otm5")
            import spread_backtest as _sb_t
            _want = _sb_t.build("put_spread", 400.0, 0.18, 31 / 365, 10.0, 5.0, _sb_t.REALISTIC_SKEW)
            assert _r0["entry"] == "2026-06-26" and _r0["expiry"] == "2026-07-27" and _r0["days"] == 31 \
                and _r0["vol_pts"] == 18.0 and abs(_r0["model"]["credit"] - _want["credit"]) < 1e-4 \
                and _r0["model"]["skew"] == _sb_t.REALISTIC_SKEW and _r0["legs"] == [[lg[0], float(lg[1]), lg[2]] for lg in _want["legs"]] \
                and _r0["market"]["credit_mid"] == 0.0 and _r0["market"]["credit_fill"] == -0.2, _r0
            assert "0 cycle(s) opened" in spread_record_pass(lambda tk: _sp_bars.get(tk), lambda nm: _sp_vol.get(nm), _sp_chain), \
                "an arm with an open cycle is not opened again"
            # the vol index must close on the entry session, else nothing is written
            spread_log.LOG = _pl_bk.Path(_tf_bk.mkdtemp()) / "sp2.jsonl"
            _stale = {"^VIX": {"date": "2026-06-25", "close": 18.0}, "^VXN": {"date": "2026-06-26", "close": 22.0}}
            _msg3 = spread_record_pass(lambda tk: _sp_bars.get(tk), lambda nm: _stale.get(nm), _sp_chain)
            assert "4 cycle(s) opened" in _msg3 and "^VIX not closed on 2026-06-26" in _msg3, _msg3
            # unquoted legs: the row is kept with the reason, the model side still grades
            spread_log.LOG = _pl_bk.Path(_tf_bk.mkdtemp()) / "sp3.jsonl"
            _msg4 = spread_record_pass(lambda tk: _sp_bars.get(tk), lambda nm: _sp_vol.get(nm),
                                       lambda tk, md: dict(_sp_chain(tk, md), puts=[]))
            _unq = spread_log.read_all(spread_log.LOG)
            assert "8 cycle(s) opened" in _msg4 and all(r["market"] is None and "unquoted" in r["market_reason"] for r in _unq), _msg4
            spread_log.LOG = _pl_bk.Path(_tf_bk.mkdtemp()) / "sp4.jsonl"
            assert "no chain" in spread_record_pass(lambda tk: _sp_bars.get(tk), lambda nm: _sp_vol.get(nm), lambda tk, md: None)
            assert not spread_log.read_all(spread_log.LOG), "no chain -> nothing written"
        finally:
            spread_log.LOG = _real_sp
        print("spread arms             : eight cycles from two chains on one close; open arms, a stale vol index, "
              "an unquoted leg and a missing chain handled as declared")

        # ── THE EXPIRY RULE, offline: with min_dte the chain is the FIRST listed
        # expiry at least that far out (not the 21-45 window), and its rows
        # carry bid/ask; without it the nearest 21-45 DTE expiry, as before.
        class _FakeChain:
            def __init__(self, exp):
                self.calls = _pd3.DataFrame({"strike": [395.0, 400.0], "impliedVolatility": [0.2, 0.21],
                                             "bid": [6.0, 3.0], "ask": [6.2, 3.1]})
                self.puts = _pd3.DataFrame({"strike": [380.0, 390.0], "impliedVolatility": [0.22, 0.21],
                                            "bid": [2.0, 3.5], "ask": [2.1, 3.6]})
        class _FakeTicker:
            def __init__(self, tk):
                self.options = ["2026-07-02", "2026-07-17", "2026-07-24", "2026-07-27", "2026-08-21"]
            def option_chain(self, exp):
                return _FakeChain(exp)
        _real_yf = _g["yf"]
        try:
            _g["yf"] = type("_YF", (), {"Ticker": staticmethod(_FakeTicker)})
            _t0 = _pd3.Timestamp("2026-06-26")
            _c30 = fetch_atm_chain("SPY", today=_t0, min_dte=30)
            assert _c30 and _c30["expiry"] == "2026-07-27" and _c30["dte"] == 31 \
                and _c30["puts"][0]["bid"] == 2.0 and _c30["puts"][0]["ask"] == 2.1, _c30
            _c45 = fetch_atm_chain("SPY", today=_t0, min_dte=45)
            assert _c45 and _c45["expiry"] == "2026-08-21", _c45
            _cdef = fetch_atm_chain("SPY", today=_t0)
            assert _cdef and _cdef["expiry"] == "2026-07-17" and _cdef["dte"] == 21, _cdef
            assert fetch_atm_chain("SPY", today=_t0, min_dte=90) is None, "no listed expiry far enough -> None"
        finally:
            _g["yf"] = _real_yf
        print("expiry rule             : min_dte picks the first listed expiry at least that far out, with bid/ask; "
              "default picks the nearest 21-45 DTE")

        # ── ONE SELECTOR (group C, 2026-10-28): the scanner's contract is the
        # app's on the same chain, strength and ATR; the mapping keeps the
        # alert's fields consistent with each other.
        _rows_oc = [(97, 2.00, 2.08, 300, 900), (100, 1.50, 1.56, 500, 2000),
                    (103, 1.00, 1.04, 900, 4000), (106, 0.60, 0.66, 50, 100)]
        _cd = {"error": None, "expiries": [{"expiry": "2026-11-20", "dte": 23,
                                            "calls": option_chain._chain(_rows_oc)["expiries"][0]["calls"],
                                            "puts": option_chain._chain(_rows_oc)["expiries"][0]["puts"]}]}
        for _strength in ("Strong", "Normal"):
            _mine = suggest_option("OCX", 100.0, "Bullish", 2.0, strength=_strength, chain_data=_cd)
            _app = option_chain.get_option_data("OCX", 100.0, "Bullish", _strength, OPT_MIN_DTE,
                                                PARAMS.atr_tgt_mult, 1e9, is_market_open, atr=2.0,
                                                chain_data=_cd)
            assert _mine and "error" not in _app and _mine["strike"] == _app["strike"] \
                and _mine["expiry"] == _app["expiry"], (_strength, _mine, _app)
            assert abs((_mine["ask"] - _mine["bid"]) / _mine["mid"] * 100 - _mine["spread_pct"]) < 0.11, _mine
            assert _mine["right"] == "CALL" and _mine["cost"] == round(_mine["mid"] * 100, 2), _mine
        assert suggest_option("OCX", 100.0, "Bullish", 2.0, chain_data={"error": "x", "expiries": []}) is None
        print("contract selection      : scanner and app name the same strike on one chain (Strong and Normal)")
        # THE IV SNAPSHOT RODE ON THE POST-CLOSE PASS: one row per watchlist
        # name, ATM at 100 with the call/put mean, and only in that pass (the
        # first run above was outside the window and fetched no chain).
        _ivr = [r for r in iv_snapshot.read_all(_iv_tmp) if r["kind"] == "iv"
                and r["ticker"] not in ("DDD", "DDE", "EEE")]   # the seeded departed names
        assert sorted(r["ticker"] for r in _ivr) == ["AAA", "BBB", "CCC"], _ivr
        assert all(abs(r["iv"] - 43.0) < 1e-9 and r["dte"] == 31 for r in _ivr), _ivr
        assert sorted(_chain_fetches) == ["AAA", "BBB", "CCC"], (
            f"{_chain_fetches}: the chain is fetched once per name, and only "
            f"inside the post-close pass")
        assert not _chain_calls, "record-only fetched an option chain"
        _rows3 = forward_log.read_all(_tmpl3)
        _scans3 = [r for r in _rows3 if r["kind"] == "scan"]
        assert len(_scans3) == 1 and _scans3[0]["tickers"] == ["AAA", "BBB", "CCC"], _scans3
        assert _scans3[0]["n_signals"] == 2, (
            _scans3[0], "n_signals must count what the rules PRODUCED (AAA "
            "taken + BBB skipped), not only the alert-worthy one")
        assert "AAA:Bullish" not in _saved_state, \
            "record-only must not stamp the alert cooldown — the next " \
            "session's scan has to be free to alert"
        print("record-only run         : heartbeat n_signals=2 (1 taken + 1 "
              "skipped), no alert, no trade chain, no cooldown stamp; skipped "
              "outside its window; IV snapshot written per name")
    finally:
        forward_log.LOG = _real_log3
        globals()["send_alert"] = _real_send3

    # ── GRADING READS SETTLED BARS ONLY (external review 2026-10-19) ──
    # The forming bar is dropped until the session closes; after the close
    # it is the best data there is and is kept. Dates are pinned so the
    # test does not depend on when it runs.
    import pandas as _pd5
    _f5 = _pd5.DataFrame({"Open": [10.0, 11.0, 12.0], "High": [10.5, 11.5, 12.5],
                          "Low": [9.5, 10.5, 11.5], "Close": [10.2, 11.2, 12.2]},
                         index=_pd5.DatetimeIndex(["2026-10-12", "2026-10-13", "2026-10-14"]))
    _mid = ET.localize(_dt2(2026, 10, 14, 12, 0))
    _post = ET.localize(_dt2(2026, 10, 14, 17, 0))
    _fnan = _f5.copy(); _fnan.iloc[0, _fnan.columns.get_loc("Low")] = float("nan")
    try:
        bars_for_grading(_fnan, now=_mid)
        raise AssertionError("a frame with a NaN low must be refused for grading (BACKLOG 36 group A)")
    except ValueError as _ve:
        assert "non-finite OHLC" in str(_ve), _ve
    assert [b["date"] for b in bars_for_grading(_f5, now=_mid)] == ["2026-10-12", "2026-10-13"], \
        "mid-session: today's forming bar must not reach the grader"
    assert [b["date"] for b in bars_for_grading(_f5, now=_post)] == \
        ["2026-10-12", "2026-10-13", "2026-10-14"], "after the close: today's bar is settled and kept"
    print("grading bars            : forming bar dropped mid-session, kept after the close")

    # ── THE FINGERPRINT IN FORCE IS DECLARED (external review 2026-10-19) ──
    # forward_log.KNOWN_CONFIGS is what report() reads to call two hashes one
    # dataset. If a rule changes, or a field is added to _scan_cfg(), this
    # fails until the new hash is declared there and in the pre-registration,
    # which is the order the record requires.
    _fp_now = forward_log.config_fingerprint(_scan_cfg())
    assert _fp_now == "4ddd12a3095f" and _fp_now in forward_log.KNOWN_CONFIGS, (
        _fp_now, _scan_cfg(), "undeclared config fingerprint: declare it before the first row")
    print(f"config fingerprint      : {_fp_now}, declared in forward_log.KNOWN_CONFIGS")

    # ── OUTAGE STAMP ONLY ON DELIVERY (external review 2026-10-19) ──
    _tmpl6 = _pl2.Path(_tf2.mkdtemp()) / "outage.jsonl"
    _g6 = globals()
    _saved6 = {k: _g6[k] for k in ("get_data", "get_spy_regime", "save_state", "load_state",
                                   "WATCHLIST", "FETCH_GAP_SEC", "pool_tickers", "send_alert")}
    _real_log6 = forward_log.LOG
    _state6: dict = {}
    _sent6: list = []
    try:
        forward_log.LOG = _tmpl6
        _g6["get_data"] = lambda tk: None                 # every name fails: a total outage
        _g6["get_spy_regime"] = lambda: None
        _g6["save_state"] = lambda st: _state6.update(st)
        _g6["load_state"] = lambda: dict(_state6)
        _g6["WATCHLIST"] = ["AAA", "BBB"]
        _g6["FETCH_GAP_SEC"] = 0
        _g6["pool_tickers"] = lambda wl: []
        import argparse as _ap6
        _ns6 = lambda: _ap6.Namespace(force=True, dry_run=False, record_only=False, force_pool=False)
        _g6["send_alert"] = lambda m, dry_run=False: (_sent6.append(m) or False)   # Telegram down
        run(_ns6())
        assert any("SCANNER OUTAGE" in m for m in _sent6), "the outage alert was attempted"
        assert "SCANNER:OUTAGE" not in _state6, "an UNDELIVERED outage alert must not start the cooldown"
        _sent6.clear()
        _g6["send_alert"] = lambda m, dry_run=False: (_sent6.append(m) or True)    # delivered
        run(_ns6())
        assert any("SCANNER OUTAGE" in m for m in _sent6), "with no stamp, the next run alerts again"
        assert "SCANNER:OUTAGE" in _state6, "a delivered outage alert starts the cooldown"
        print("outage cooldown         : stamped on delivery only; a failed send leaves the next run free to alert")
    finally:
        forward_log.LOG = _real_log6
        _g6.update(_saved6)

    # ── THE POOL BUCKET (BACKLOG 30): once a session, HQ only, capped, ranked ──
    _tmpl4 = _pl2.Path(_tf2.mkdtemp()) / "pool.jsonl"
    _real_log4 = forward_log.LOG
    _real_send4 = globals()["send_alert"]
    _sent4: list = []
    try:
        forward_log.LOG = _tmpl4
        globals()["send_alert"] = lambda m, dry_run=False: (_sent4.append(m) or True)
        import pandas as _pd4
        _bars4 = _pd4.bdate_range(end="2026-06-30", periods=MIN_BARS_AFTER_WARMUP + 20)
        _fetched: list = []
        def _fake_get(tk):
            _fetched.append(tk)
            fr = _pd4.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0,
                                 "Close": 100.0, "Volume": 1e6}, index=_bars4)
            fr.attrs["tk"] = tk
            return fr
        _rs = {"P1": 3.0, "P2": 9.0, "P3": -2.0, "P4": 12.0, "P5": 0.5, "P6": 7.0}
        _buckets: dict = {}
        def _fake_analyze4(df, tk, spy_regime=None, tally=None, bucket="live", rs=None):
            _buckets[tk] = bucket
            if tk in _rs:                       # six HQ signals in the pool
                if tally is not None:
                    tally["signals"] = tally.get("signals", 0) + 1
                return {"ticker": tk, "trend": "Bullish", "strength": "Strong",
                        "price": 100.0, "entry": 100.0, "stop": 98.0,
                        "target": 106.0, "rr": 3.0, "rsi": 55.0, "adx": 40.0,
                        "atr": 1.6, "filters_pass": 4, "filters_total": 4}
            return None
        _g = globals()
        _saved = {k: _g[k] for k in ("get_data", "compute", "analyze",
                                     "get_spy_regime", "save_state", "load_state",
                                     "WATCHLIST", "FETCH_GAP_SEC", "suggest_option",
                                     "pool_tickers", "relative_strength",
                                     "is_post_close_window")}
        _state4: dict = {}
        # THE PULLBACK BUCKET rides on both loops: AAA (watchlist) and P7 (pool,
        # not HQ) carry a canned pullback signal; nothing else does.
        _real_pb = pullback_signal.evaluate_pullback
        def _fake_pb(raw_df, cdf, params, spy_regime=None, tally=None):
            tk = raw_df.attrs.get("tk")
            if tk in ("AAA", "P7"):
                return {"trend": "Bullish", "price": 100.0, "entry": 100.0, "stop": 98.0,
                        "target": 106.0, "rr": 3.0, "atr": 1.6, "rsi": 40.0, "adx": 20.0,
                        "sma200": 95.0, "strength": "Normal", "regime": "Bull",
                        "ema20": 101.0, "ema50": 102.0}
            return None
        pullback_signal.evaluate_pullback = _fake_pb
        try:
            _g["get_data"] = _fake_get
            _g["compute"] = lambda df: df
            _g["analyze"] = _fake_analyze4
            _g["get_spy_regime"] = lambda: None
            _g["save_state"] = lambda st: _state4.update(st)
            _g["load_state"] = lambda: dict(_state4)
            _g["WATCHLIST"] = ["AAA"]
            _g["FETCH_GAP_SEC"] = 0
            # D8: a pool name with no contract is withheld, so the harness names one
            _opt4 = {"right": "CALL", "strike": 100.0, "expiry": "2026-12-18", "dte": 30, "mid": 2.05,
                     "bid": 2.0, "ask": 2.1, "spread_pct": 4.9, "volume": 50, "oi": 500, "cost": 205.0,
                     "pct_account": 4.1, "within_budget": True, "budget": 250.0, "dte_adequate": True,
                     "days_needed": 12, "moneyness_pct": 0.0}
            _g["suggest_option"] = lambda *a, **k: dict(_opt4)
            _pool7 = ["P1", "P2", "P3", "P4", "P5", "P6", "P7"]
            _g["pool_tickers"] = lambda wl: list(_pool7)
            _g["relative_strength"] = lambda df, bench, lookback=None: _rs.get(df.attrs.get("tk"))
            import argparse as _ap5
            _ns = lambda **kw: _ap5.Namespace(**{"force": True, "dry_run": False,
                                                 "record_only": False, "force_pool": False, **kw})
            # FIRST ALERTING RUN OF THE DAY: the pool is scanned, six HQ, five sent, by RS.
            run(_ns())
            assert _buckets.get("AAA") == "live" and all(_buckets[t] == "pool" for t in _rs), _buckets
            assert "SPY" in _fetched, "the benchmark is fetched once for the ranking"
            _pool_msgs = [m for m in _sent4 if m.startswith("🚨 POOL ALERT")]
            assert len(_pool_msgs) == POOL_ALERT_CAP == 5, (len(_pool_msgs), _sent4)
            _order = [m.splitlines()[1].split()[0] for m in _pool_msgs]
            assert _order == ["P4", "P2", "P6", "P1", "P5"], (
                f"{_order}: alerts go out in RS order and P3 (lowest) is beyond the cap")
            assert "RS +12.0" in _pool_msgs[0], _pool_msgs[0].splitlines()[0]
            assert _state4.get(POOL_STATE_KEY) == datetime.now(ET).strftime("%Y-%m-%d")
            assert "P3:Bullish" not in _state4 and "P4:Bullish" in _state4, \
                "only the alerted names get a cooldown stamp"
            _rows4 = forward_log.read_all(_tmpl4)
            _scans4 = [r for r in _rows4 if r["kind"] == "scan"]
            assert [forward_log.bucket_of(r) for r in _scans4] == ["live", "pool"], _scans4
            assert _scans4[1]["tickers"] == ["P1", "P2", "P3", "P4", "P5", "P6", "P7"] \
                and _scans4[1]["n_signals"] == 6, _scans4[1]
            assert forward_log.last_scan(_tmpl4)["tickers"] == ["AAA"], \
                "the coverage heartbeat is the LIVE scan row, not the pool's"
            _alr = [r for r in _rows4 if r["kind"] == "alert" and r["delivered"]]
            _cut = [r for r in _rows4 if r["kind"] == "alert" and not r["delivered"]]
            assert [(a["ticker"], a["header"]) for a in _cut] == [
                ("P3", "NOT SENT: beyond the pool cap of 5 (rank 6 of 6 by RS)")], (_cut, "D8: the cut is recorded")
            assert [a["ticker"] for a in _alr] == ["P4", "P2", "P6", "P1", "P5"] and \
                all(a["delivered"] and a["bucket"] == "pool" and a["header"] == "POOL ALERT"
                    and a["key"] is None for a in _alr), (
                _alr, "one alert row per delivered pool alert, in send order (no bar date on the fake)")
            _pbr = [r for r in _rows4 if r["kind"] == "signal" and r.get("bucket") == "pullback"]
            assert sorted(r["ticker"] for r in _pbr) == ["AAA", "P7"], _pbr
            assert all(r["trend"] == "Bullish" and r["decision"] == "taken" and "hq" not in r
                       and r["setup"]["regime"] == "Bull" for r in _pbr), _pbr
            assert {r["setup"]["source_bucket"] for r in _pbr} == {"live", "pool"}, _pbr
            assert not any(m.splitlines()[1].startswith(("AAA", "P7")) for m in _sent4), \
                "a pullback signal is never alerted"
            assert "AAA:Bullish" not in _state4 and "P7:Bullish" not in _state4, \
                "a pullback signal leaves no cooldown stamp"
            # SECOND RUN THE SAME DAY: no pool fetch, no pool scan row, no alert.
            _fetched.clear(); _sent4.clear()
            run(_ns())
            assert not any(t in _pool7 for t in _fetched) and "SPY" not in _fetched, _fetched
            assert not _sent4 and len([r for r in forward_log.read_all(_tmpl4)
                                       if r["kind"] == "scan"]) == 3, "live heartbeat only"
            assert len([r for r in forward_log.read_all(_tmpl4) if r.get("bucket") == "pullback" and r["kind"] == "signal"]) == 2, \
                "the same bar is not recorded twice (record_signal dedupes on the key)"
            # GRADING FETCH RULE: a pool-only name waits for the pool run or the
            # post-close pass; a live-bucket name that left the watchlist is
            # fetched in session, once, as before.
            _setup9 = {"price": 100.0, "entry": 100.0, "stop": 98.0, "target": 106.0}
            # The fixture's bars end 2026-06-30, months before any real "today":
            # with four bars after the signal and the last one that stale, the
            # resolver would VOID LV9 as a dead name (F3, 2026-10-28) on the
            # in-session run and the post-close fetch below would have nothing
            # to grade. This test is about the FETCH rule, so the dead-name
            # clock is parked for its duration; F3 has its own test in
            # forward_log.
            _nbd9 = forward_log.GRADE_NO_BARS_DAYS
            forward_log.GRADE_NO_BARS_DAYS = 10 ** 6
            forward_log.record_signal("LV9", "Bullish", _setup9, "taken", "", {"adx_min": 35},
                                      bar_date=str(_bars4[-5].date()), hq=False)
            forward_log.record_signal("P9", "Bullish", _setup9, "taken", "", {"adx_min": 35},
                                      bar_date=str(_bars4[-5].date()), hq=False, bucket="pool")
            _fetched.clear(); _sent4.clear()
            # THE LOG LINE: a deferral is reported as such, not as missing
            # data. Captured on the module logger because the warning is the
            # only place the distinction shows.
            import logging as _lg9
            _caught9: list = []
            class _Catch9(_lg9.Handler):
                def emit(self, rec):
                    _caught9.append((rec.levelno, rec.getMessage()))
            _h9 = _Catch9(); logger.addHandler(_h9)
            try:
                run(_ns())
            finally:
                logger.removeHandler(_h9)
            assert "LV9" in _fetched and "P9" not in _fetched, (
                _fetched, "in session a pool-only name waits; a departed live name is fetched")
            _defer9 = [m for lvl, m in _caught9 if "grading deferred" in m]
            assert len(_defer9) == 1 and _defer9[0].startswith(
                "forward log: grading deferred 1 pool/pullback signal(s) in 1 name(s)"), (
                _caught9, "P9's one open row is reported as deferred")
            assert not any(lvl >= _lg9.WARNING and "no data" in m for lvl, m in _caught9), (
                _caught9, "a deferral must not read as 'no data'")
            _fetched.clear()
            _g["is_post_close_window"] = lambda now=None: True
            run(_ns(force=False, record_only=True))
            forward_log.GRADE_NO_BARS_DAYS = _nbd9
            assert "P9" in _fetched and "LV9" in _fetched, (_fetched, "post-close grades both buckets")
            # --force-pool RE-RUNS IT: the five already sent sit inside the cooldown,
            # so the sixth (P3, beyond the cap the first time) is the only alert.
            _fetched.clear(); _sent4.clear()
            run(_ns(force_pool=True))
            assert sum(1 for t in _fetched if t in _pool7) == 7, _fetched
            assert [m.splitlines()[1].split()[0] for m in _sent4] == ["P3"], (
                _sent4, "forced pool scan: cooldown holds the five, the sixth goes out")
            # RECORD-ONLY NEVER SCANS THE POOL (its rows are graded there instead).
            _state4.pop(POOL_STATE_KEY, None); _fetched.clear(); _sent4.clear()
            _g["is_post_close_window"] = lambda now=None: True
            run(_ns(force=False, record_only=True))
            assert not any(t in _pool7 for t in _fetched) and not _sent4, _fetched
            assert POOL_STATE_KEY not in _state4, "record-only must not stamp the pool"
            # A DRY RUN scans and prints but leaves no stamp and records no alert:
            # cooldowns cleared first so the five are actually "sent" (printed).
            for k in list(_state4):
                if k.endswith(":Bullish"):
                    _state4.pop(k)
            _fetched.clear(); _sent4.clear()
            _n_alerts_before = len([r for r in forward_log.read_all(_tmpl4) if r["kind"] == "alert"])
            run(_ns(dry_run=True))
            assert len([m for m in _sent4 if m.startswith("🚨 POOL ALERT")]) == 5, "the dry run printed five"
            assert sum(1 for t in _fetched if t in _pool7) == 7 and POOL_STATE_KEY not in _state4
            assert len([r for r in forward_log.read_all(_tmpl4) if r["kind"] == "alert"]) == _n_alerts_before, \
                "a dry run sends nothing, so it records no alert"
            # A TELEGRAM FAILURE leaves the pool due: no stamp, no cooldown, and
            # the next alerting run scans and sends again.
            for k in list(_state4):
                if k.endswith(":Bullish"):
                    _state4.pop(k)
            globals()["send_alert"] = lambda m, dry_run=False: False
            _fetched.clear()
            run(_ns())
            assert sum(1 for t in _fetched if t in _pool7) == 7 and POOL_STATE_KEY not in _state4, \
                "undelivered pool alerts must not stamp the day"
            assert not any(k.endswith(":Bullish") for k in _state4), "no cooldown on a failed send"
            _failed_rows = [r for r in forward_log.read_all(_tmpl4) if r["kind"] == "alert" and not r["delivered"]
                            and r["header"] == "POOL ALERT"]
            assert len(_failed_rows) == 5, (len(_failed_rows), "a failed delivery is recorded as such")
            globals()["send_alert"] = lambda m, dry_run=False: (_sent4.append(m) or True)
            _fetched.clear(); _sent4.clear()
            run(_ns())
            assert sum(1 for t in _fetched if t in _pool7) == 7 and POOL_STATE_KEY in _state4 \
                and len([m for m in _sent4 if m.startswith("🚨 POOL ALERT")]) == 5, "delivered: stamped"
            # ── D8 (owner 2026-11-03): THE SECTOR CAP AND A TRADEABLE CONTRACT ──
            # V, MA, CME, JPM, GS are Financials; UNP has no contract in the gates.
            # By RS: V MA CME UNP XOM ABBV JPM GS CVX. Two Financials go, the
            # other three are held at the sector cap, UNP has no contract; none
            # of the four takes a slot, so XOM, ABBV and CVX fill the five.
            _rs8 = {"V": 9.0, "MA": 8.0, "CME": 7.0, "UNP": 6.0, "XOM": 5.0, "ABBV": 4.0,
                    "JPM": 3.0, "GS": 2.0, "CVX": 1.0}
            _rs.clear(); _rs.update(_rs8)
            _pool7[:] = list(_rs8)
            _g["suggest_option"] = lambda tk, *a, **k: None if tk == "UNP" else dict(_opt4)
            _state4.clear(); _sent4.clear()
            _tmpl4.write_text("")
            run(_ns())
            _sent8 = [m.splitlines()[1].split()[0] for m in _sent4 if m.startswith("🚨 POOL ALERT")]
            assert _sent8 == ["V", "MA", "XOM", "ABBV", "CVX"], _sent8
            _ns8 = {r["ticker"]: r["header"] for r in forward_log.read_all(_tmpl4)
                    if r["kind"] == "alert" and not r["delivered"]}
            assert _ns8["CME"].startswith("NOT SENT: sector cap: 2 Financials") and \
                _ns8["JPM"].startswith("NOT SENT: sector cap") and _ns8["GS"].startswith("NOT SENT: sector cap") \
                and _ns8["UNP"].startswith("NOT SENT: no contract") and set(_ns8) == {"CME", "JPM", "GS", "UNP"}, _ns8
            # ── D8: NO BENCHMARK, NO RS: ranked by the record's own H1' order (ADX, then R:R) ──
            _adx8 = {"V": 31.0, "MA": 45.0, "CME": 38.0, "UNP": 50.0, "XOM": 33.0, "ABBV": 41.0,
                     "JPM": 30.5, "GS": 36.0, "CVX": 44.0}
            _real_fa = _g["analyze"]
            _g["analyze"] = lambda df, tk, **k: (dict(_real_fa(df, tk, **k), adx=_adx8[tk])
                                                 if _real_fa(df, tk, **k) else None)
            _g["relative_strength"] = lambda df, bench, lookback=None: None
            _state4.clear(); _sent4.clear()
            run(_ns())
            _sent9 = [m.splitlines()[1].split()[0] for m in _sent4 if m.startswith("🚨 POOL ALERT")]
            # by ADX: UNP 50 (no contract, no slot), MA 45, CVX 44, ABBV 41, CME 38 (the
            # second Financial), GS 36 (a third: sector cap), XOM 33: five.
            assert _sent9 == ["MA", "CVX", "ABBV", "CME", "XOM"], _sent9
            assert all("ranked by ADX (RS unavailable)" in m.splitlines()[0]
                       for m in _sent4 if m.startswith("🚨 POOL ALERT")), _sent4[0].splitlines()[0]
            print("pool D8                 : at most 2 a sector, an untradeable name takes no slot, "
                  "the cut is recorded; with no SPY the five rank by ADX, not the alphabet")
        finally:
            _g.update(_saved)
            pullback_signal.evaluate_pullback = _real_pb
        print("pullback bucket         : rows under bucket=pullback for a watchlist and a pool "
              "name, never alerted, no cooldown stamp, deduped on the bar")
        print(f"pool bucket             : once a session, {POOL_ALERT_CAP} of 6 HQ sent in RS "
              f"order, rows+scan under bucket=pool, live heartbeat untouched; "
              f"second run skips, --force-pool re-runs, record-only never scans it")
    finally:
        forward_log.LOG = _real_log4
        globals()["send_alert"] = _real_send4

    # ── analyze() FETCHES NOTHING FOR A BAR THAT CANNOT SIGNAL (review 2026-10-13) ──
    _earn_calls: list = []
    _real_earn = globals()["check_earnings_blackout"]
    _real_wk = globals()["get_weekly_trend"]
    try:
        globals()["check_earnings_blackout"] = lambda t, **k: (_earn_calls.append(t), (True, "n/a"))[1]
        globals()["get_weekly_trend"] = lambda t: "Bullish"
        import pandas as _pd5
        _flat = compute(_pd5.DataFrame(
            {"Open": 100.0, "High": 100.5, "Low": 99.5, "Close": 100.0, "Volume": 1e6},
            index=_pd5.bdate_range(end="2026-06-30", periods=300)))
        assert analyze(_flat, "FLT") is None and _earn_calls == [], (
            _earn_calls, "a flat tape fails the base condition; the earnings calendar is not fetched")
        _px, _up = 100.0, []
        for _i in range(300):
            _px *= (1.004 + 0.00001 * _i) if _i % 4 != 3 else 0.992
            _up.append(_px)
        _bull = compute(_pd5.DataFrame(
            {"Open": _up, "High": [x * 1.004 for x in _up], "Low": [x * 0.996 for x in _up],
             "Close": _up, "Volume": 1e6}, index=_pd5.bdate_range(end="2026-06-30", periods=300)))
        assert sc.base_trend(_bull.iloc[-1], PARAMS) == "Bullish"
        _real_log5 = forward_log.LOG
        try:
            forward_log.LOG = _pl2.Path(_tf2.mkdtemp()) / "an.jsonl"
            analyze(_bull, "BUL")
        finally:
            forward_log.LOG = _real_log5
        assert _earn_calls == ["BUL"], (_earn_calls, "the calendar is fetched once a bar that passes")
        print("analyze() fetch order   : base condition first; earnings calendar only for a bar that passes it")
    finally:
        globals()["check_earnings_blackout"] = _real_earn
        globals()["get_weekly_trend"] = _real_wk

    # ── RELATIVE STRENGTH: settled closes, aligned by date ──
    import pandas as _pd6
    _days = _pd6.bdate_range(end="2026-06-30", periods=70)
    _stk = _pd6.DataFrame({"Close": [100.0] * 69 + [110.0]}, index=_days)
    _bch = _pd6.DataFrame({"Close": [50.0] * 70}, index=_days)
    _settled = _dt2(2026, 7, 1, 10, 0, tzinfo=ET)
    assert abs(relative_strength(_stk, _bch, now=_settled) - 10.0) < 1e-9
    _gap = _stk.drop(_days[30])                               # a missing session in the stock
    assert abs(relative_strength(_gap, _bch, now=_settled) - 10.0) < 1e-9, "aligned on common dates"
    _today = _days[-1] + _pd6.Timedelta(days=1)
    _partial = _pd6.concat([_stk, _pd6.DataFrame({"Close": [200.0]}, index=[_today])])
    _bch_partial = _pd6.concat([_bch, _pd6.DataFrame({"Close": [50.0]}, index=[_today])])
    _in_session = _today.to_pydatetime().replace(hour=10, tzinfo=ET)
    assert abs(relative_strength(_partial, _bch_partial, now=_in_session) - 10.0) < 1e-9, \
        "today's partial bar (in BOTH frames) is dropped before ranking"
    assert relative_strength(_stk.iloc[-20:], _bch, now=_settled) is None, "fewer than 64 common dates: no number"
    print("relative strength       : settled closes, common dates only, partial bar dropped")

    # ── THE GRADER IS WIRED INTO run(), AND REUSES THE SCAN'S FRAMES ──
    # results/forward_grading_preregistration.md. A taken signal on a
    # watchlist ticker is graded from the frame the scan already fetched; one
    # on a ticker that left the watchlist costs exactly one extra fetch.
    _tmpl4 = _pl2.Path(_tf2.mkdtemp()) / "grade.jsonl"
    _real_log4 = forward_log.LOG
    _real_send4 = globals()["send_alert"]
    try:
        forward_log.LOG = _tmpl4
        globals()["send_alert"] = lambda m, dry_run=False: True
        import pandas as _pd4
        _idx = _pd4.bdate_range("2026-03-02", periods=30)
        _fr4 = _pd4.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0,
                               "Close": 100.0, "Volume": 1e6}, index=_idx)
        _fr4.loc[_idx[5], ["Open", "High", "Low", "Close"]] = [107.0, 108.0, 106.5, 107.5]   # gaps through the target
        _setup4 = {"price": 100.0, "entry": 100.0, "stop": 98.0, "target": 106.0, "rr": 3.0}
        forward_log.record_signal("AAA", "Bullish", _setup4, "taken", "", {"k": 1},
                                  bar_date=str(_idx[0].date()), hq=False)
        forward_log.record_signal("GONE", "Bullish", _setup4, "taken", "", {"k": 1},
                                  bar_date=str(_idx[0].date()), hq=False)
        _fetched: list = []
        _g = globals()
        _saved = {k: _g[k] for k in ("get_data", "compute", "analyze",
                                     "get_spy_regime", "save_state", "load_state",
                                     "WATCHLIST", "FETCH_GAP_SEC", "pool_tickers", "fetch_atm_chain")}
        _iv_tmp4 = _pl2.Path(_tf2.mkdtemp()) / "iv4.jsonl"
        _real_iv_log4 = iv_snapshot.LOG
        # RECORDS the call rather than raising: the snapshot block is wrapped
        # in try/except (non-fatal by design), so a raising stub is swallowed
        # there and proves nothing. Counting calls does.
        _chain_calls4: list = []
        try:
            _g["pool_tickers"] = lambda wl: []        # the pool has its own harness
            iv_snapshot.LOG = _iv_tmp4
            _g["fetch_atm_chain"] = lambda tk, today=None: (_chain_calls4.append(tk) or None)
            _g["get_data"] = lambda tk: (_fetched.append(tk) or _fr4)
            _g["compute"] = lambda df: df
            _g["analyze"] = lambda df, tk, spy_regime=None, tally=None: None
            _g["get_spy_regime"] = lambda: None
            _g["save_state"] = lambda st: None
            _g["load_state"] = lambda: {}
            _g["WATCHLIST"] = ["AAA"]
            _g["FETCH_GAP_SEC"] = 0
            import argparse as _ap4
            run(_ap4.Namespace(force=True, dry_run=True))
        finally:
            _g.update(_saved)
            iv_snapshot.LOG = _real_iv_log4
        assert not _chain_calls4 and not _iv_tmp4.exists(), (
            f"an in-session (alerting) run must not take an IV snapshot; chain "
            f"fetched for {_chain_calls4}. The snapshot pairs a chain with the "
            f"settled close, which only the post-close pass has")
        _outs4 = {o["ticker"]: o for o in forward_log.read_all(_tmpl4)
                  if o["kind"] == "outcome"}
        assert set(_outs4) == {"AAA", "GONE"}, (
            f"run() must grade every open taken signal; graded {sorted(_outs4)}")
        assert _outs4["AAA"]["basis"] == "stop_distance" \
            and _outs4["AAA"]["detail"]["exit_rule"] == "target" \
            and _outs4["AAA"]["detail"]["gap_fill"] is True, _outs4["AAA"]
        assert _fetched == ["AAA", "GONE"], (
            f"fetches {_fetched}: the watchlist frame must be reused for AAA "
            f"and GONE fetched exactly once")
        print("grader wiring           : run() grades open signals after the "
              "scan, reusing the scan's frame; one fetch for a departed ticker")
    finally:
        forward_log.LOG = _real_log4
        globals()["send_alert"] = _real_send4

    print("\nAll self-tests passed.")
    return 0


def parse_args():
    p = argparse.ArgumentParser(description="Watchlist scanner")
    p.add_argument("--dry-run", action="store_true",
                   help="Print alerts instead of sending; don't touch state")
    p.add_argument("--force", action="store_true",
                   help="Run even when the market is closed")
    p.add_argument("--selftest", action="store_true",
                   help="Run offline checks and exit (no network, no alerts)")
    p.add_argument("--record-only", action="store_true",
                   help="Post-close pass: evaluate today's settled bar and "
                        "write its forward-log rows and scan heartbeat. Sends "
                        "no trade alert and fetches no chain. Runs only inside "
                        "the 2h after the close unless --force.")
    p.add_argument("--force-pool", action="store_true",
                   help="Scan the candidate pool (BACKLOG 30) on this run even "
                        "if it already ran today. Alerting runs only.")
    p.add_argument("--coverage", action="store_true",
                   help="Check whether the unattended system has been running "
                        "(last scan row, unchecked positions) and alert if not. "
                        "No market data; runs on every workflow run, including "
                        "the ones that cannot scan.")
    return p.parse_args()


if __name__ == "__main__":
    _args = parse_args()
    if _args.selftest:
        raise SystemExit(selftest())
    if _args.coverage:
        _st = load_state()
        prune_state(_st)
        coverage_check(_st, dry_run=_args.dry_run)
        if not _args.dry_run:
            save_state(_st)
        raise SystemExit(0)
    raise SystemExit(run(_args))
