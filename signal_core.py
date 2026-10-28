#!/usr/bin/env python3
"""
signal_core.py — the single implementation of "does this signal fire"

WHY THIS EXISTS
---------------
app.py and scanner.py each had their own analyze(). They disagreed in two ways
that produced real, confusing behaviour on 2026-08-25: the scanner alerted on
TGT, ABT and TMO at 12:30 ET, and the app said "no signal" on all three an hour
later.

  1. The scanner read df.iloc[-1] directly, so mid-session it analysed TODAY'S
     PARTIAL BAR — a Close that is really just the current price and a Volume
     that is only what has traded so far. app.py had fixed this (bug #7) by
     dropping the partial bar; the scanner never got the fix.

  2. The scanner applied four gates (trend stack, MACD, ADX, R:R). app.py
     applied those PLUS volume-vs-average, weekly multi-timeframe alignment,
     earnings blackout, and macro regime. The scanner was structurally looser,
     so setups cleared it that the app rejected.

Syncing constants does not fix duplicated logic — that was tried, and ADX/ATR
were matched days before this happened. The only durable fix is one
implementation. This module is it. Both callers import evaluate() and neither
keeps a private copy.

DESIGN
------
Pure and network-free. Anything that needs a fetch (weekly trend, earnings
dates, SPY regime) is passed IN by the caller, because app.py fetches through
Streamlit caches and scanner.py fetches through its own rate limiter, and this
module should not care which. That also makes every branch testable offline.

THE HONEST CAVEAT
-----------------
A signal firing here means "this matches the pattern that was specified". The
591-trade out-of-sample validation found no measurable edge in that pattern
(-0.014 R per trade, PF 0.98, 95% CI [-0.124, +0.096]). Nothing in this file
changes that. It makes two systems agree; it does not make them right.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, replace
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

try:
    import pandas as pd
    import numpy as np
except ImportError:
    raise SystemExit("Missing pandas. Run: pip install pandas")


# ---------------------------------------------------------------------------
# Parameters — ONE set of defaults, imported by both callers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SignalParams:
    """
    Every tunable the signal reads. Defaults here are the frozen baseline from
    the Aug 2026 sweep — kept because it is the best-DEFINED hypothesis tested,
    not because it is profitable.

    app.py builds one of these from its sidebar; scanner.py uses DEFAULTS. If
    you change a default, both move together — which is the entire point.
    """
    adx_min: float = 30.0       # 35 until 2026-10-28 (BACKLOG 33, amendment F: an intake choice)
    min_rr: float = 0.5
    hq_min_rr: float = 1.0          # high-quality tier; alerts fire on this
    volume_mult: float = 1.2        # "Strong" needs volume >= avg * this
    volume_soft_mult: float = 0.70  # base condition floor
    atr_stop_mult: float = 1.25
    atr_tgt_mult: float = 4.0
    # OFF since 2026-09-02, on measurement. Over 15 years x 12 tickers the
    # weekly gate rejected 5 bars out of the 13,748 that reached the filters
    # (0.04%) — it is checked after the ADX gate, and a name that trending is
    # essentially never on the wrong side of its 20-week EMA. Against that it
    # FAILS CLOSED: evaluate() treats a missing weekly trend as blocking (see
    # below), so every rate-limited Yahoo call killed live alerts outright.
    # It was also never in the validated config — the frozen OOS run had it
    # off. app.py still exposes it as a sidebar checkbox; this is the default,
    # not a removal.
    weekly_confirm: bool = False
    spy_regime_on: bool = True


DEFAULTS = SignalParams()

# The frame contract. Every caller's compute() must produce all of these
# before calling evaluate(); evaluate() raises if any is absent rather than
# quietly scoring a signal it cannot actually assess.
REQUIRED_COLUMNS = ("Close", "High", "Low", "Volume", "EMA20", "EMA50",
                    "RSI", "MACD", "Signal", "ATR", "VOL_AVG20")


# ---------------------------------------------------------------------------
# Market hours / partial bar — the bug-1 fix, shared
# ---------------------------------------------------------------------------

# NYSE closures and 1:00pm ET early closes. app.py, scanner.py and
# exit_monitor.py each had their own copy of this calendar; this is the one
# they should all read. A holiday-blind version reports the market OPEN on
# Thanksgiving, and drop_partial_bar() then DISCARDS the last completed bar
# as if it were in progress — silently analysing day-stale data.
MARKET_HOLIDAYS = frozenset({
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27",
    "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
})
MARKET_HALF_DAYS = frozenset({
    "2025-07-03", "2025-11-28", "2025-12-24", "2026-11-27", "2026-12-24",
    "2027-11-26",
})


# The market calendar is expressed in Eastern Time, so the default clock has to
# be too. This module is the CANONICAL one — app.py, scanner.py and
# exit_monitor.py all carry their own copy of is_market_open() and all three
# already default to ET — and it was the only one defaulting to naive local
# time. On a UTC host (GitHub Actions runners, this project's own containers)
# that is wrong in BOTH directions: 13:30-16:00 UTC is pre-open in ET but reads
# as open, and 16:00-20:00 UTC is the ET afternoon session but reads as closed.
# drop_partial_bar() trusts this answer, so the first case discards the last
# COMPLETED bar and the second analyses an in-progress one.
#
# No caller relies on the naive behaviour today — scanner.py passes
# datetime.now(ET) explicitly and app.py uses its own copy — so this closes a
# trap rather than changing a result. A naive datetime passed in is still
# honoured as-is, because a caller that supplies one has made a choice.
_ET = ZoneInfo("America/New_York")


def is_market_open(now: datetime | None = None) -> bool:
    """
    True during US regular trading hours, accounting for holidays and 1:00pm
    early closes.

    `now` defaults to the current time IN EASTERN TIME, not the host's local
    clock, because the calendar above is an ET calendar. Pass an explicit
    datetime to ask about another moment.
    """
    now = now if now is not None else datetime.now(_ET)
    if now.weekday() >= 5:
        return False
    day = now.strftime("%Y-%m-%d")
    if day in MARKET_HOLIDAYS:
        return False
    close = dtime(13, 0) if day in MARKET_HALF_DAYS else dtime(16, 0)
    return dtime(9, 30) <= now.time() <= close


# ───────────────────────────────────────────────────────────────────────────
# THE ONE PLACE THAT DECIDES WHETHER A BAR HAS SETTLED
# ───────────────────────────────────────────────────────────────────────────
# This defect was found and fixed SIX separate times, in six modules, under four
# different function names — signal_core.drop_partial_bar, app.py's own copy,
# backtest._drop_todays_bar, universe.drop_unsettled,
# exit_monitor.drop_unsettled_bars — and each fix was local. There was no single
# answer to "is this frame safe to read?", so every new data path either
# re-invented the guard or forgot it. Forgetting it cost, in order: alerts that
# appeared and vanished with the clock, an unreproducible backtest cache, a
# universe snapshot that depended on the time of day, and live option positions
# closed on an intraday dip that recovered.
#
# TWO POLICIES, because the difference between the call sites is real:
#
#   DROP_UNTIL_CLOSE  live paths (app, scanner, exit_monitor). Today's bar is
#                     unusable until today's session has ENDED — before the open
#                     it is a stub, during the session it is in progress — and is
#                     the freshest real data once the close has passed.
#
#   DROP_ALWAYS       reproducible paths (backtest, universe). Never read a bar
#                     dated today, settled or not, because a frame whose last row
#                     was written today cannot be cached or fingerprinted: two
#                     fetches four minutes apart returned identical row counts
#                     and different content for all 13 series.
#
# IDENTIFICATION IS ALWAYS BY DATE, never "the last row". The old
# drop_partial_bar() dropped iloc[-1] while the market was open, which left
# today's bar in place BEFORE the open — Yahoo emits that row well ahead of
# 09:30. backtest._drop_todays_bar()'s docstring had named that exact gap
# ("every live path leaves it in place") and only fixed it for the backtest.
DROP_UNTIL_CLOSE = "until_close"
DROP_ALWAYS = "always"


def session_closed(now: datetime | None = None) -> bool:
    """
    True when today's regular session is over — or when today is not a trading
    day at all, since then nothing is forming.

    This is the question drop_unsettled(DROP_UNTIL_CLOSE) needs, and it is NOT
    `not is_market_open()`: that is also true before the open, which is when the
    stub bar problem lives.
    """
    now = now if now is not None else datetime.now(_ET)
    if now.weekday() >= 5:
        return True
    day = now.strftime("%Y-%m-%d")
    if day in MARKET_HOLIDAYS:
        return True
    close = dtime(13, 0) if day in MARKET_HALF_DAYS else dtime(16, 0)
    return now.time() > close


def _bar_dates(df: pd.DataFrame, date_col: str | None):
    """Normalised bar dates, from `date_col` if given else the index."""
    src = df[date_col] if date_col and date_col in df.columns else df.index
    idx = pd.to_datetime(src, errors="coerce")
    try:
        idx = idx.tz_localize(None)
    except (TypeError, AttributeError):
        try:
            idx = idx.tz_convert(None)
        except (TypeError, AttributeError):
            pass
    return pd.DatetimeIndex(idx).normalize()


def validate_bars(df: pd.DataFrame, date_col: str | None = None) -> str | None:
    """
    None when a daily OHLC frame is usable, else one reason why not. BACKLOG
    36 group A, the one validator every live path runs: scanner.get_data()
    refuses the ticker with the reason, bars_for_grading() refuses the grade.
    A refusal, never a fill with zero: a NaN close that reached evaluate()
    would fail every comparison silently and read as "no signal"; a NaN low
    in the grader would never touch a stop.
    """
    cols = ("Open", "High", "Low", "Close")
    if df is None or len(df) == 0:
        return "empty frame"
    missing = [c for c in cols if c not in df.columns]
    if missing:
        return f"missing columns {', '.join(missing)}"
    try:
        vals = df[list(cols)].to_numpy(dtype=float)
    except (TypeError, ValueError):
        return "non-numeric OHLC"
    if not np.isfinite(vals).all():
        return "non-finite OHLC"
    if (vals <= 0).any():
        return "non-positive price"
    if (vals[:, 2] > vals[:, 1]).any():
        return "low above high"
    dates = _bar_dates(df, date_col)
    if dates.isna().any():
        return "undated bar"
    if dates.duplicated().any():
        return "duplicate bar dates"
    if not dates.is_monotonic_increasing:
        return "bars out of order"
    return None


def drop_unsettled(df: pd.DataFrame,
                   *,
                   policy: str = DROP_UNTIL_CLOSE,
                   now: datetime | None = None,
                   date_col: str | None = None) -> tuple[pd.DataFrame, int]:
    """
    Remove bars dated today (ET) or later, per `policy`. Returns (df, n_dropped).

    `date_col` names a date COLUMN (backtest frames carry "Date"); omit it for a
    DatetimeIndex. A frame with neither is returned untouched rather than
    guessed at — silently trimming the wrong row is the failure this exists to
    stop.
    """
    if policy not in (DROP_UNTIL_CLOSE, DROP_ALWAYS):
        raise ValueError(f"unknown policy {policy!r}; use DROP_UNTIL_CLOSE or "
                         f"DROP_ALWAYS")
    if df is None or len(df) == 0:
        return df, 0
    now = now if now is not None else datetime.now(_ET)

    has_col = bool(date_col) and date_col in getattr(df, "columns", ())
    if not has_col and not isinstance(getattr(df, "index", None), pd.DatetimeIndex):
        try:
            pd.to_datetime(df.index, errors="raise")
        except Exception:
            return df, 0        # undated frame — do not guess which row is today

    dates = _bar_dates(df, date_col if has_col else None)
    today = pd.Timestamp(now.date())
    if policy == DROP_UNTIL_CLOSE and session_closed(now):
        # Today's bar is settled; it is the best data there is. A bar dated
        # AFTER today is never data, whatever the clock says (BACKLOG 36
        # group A): a provider glitch or a wrong container clock must not
        # put a bar from the future at the end of the frame.
        keep = ~(dates > today)
    else:
        keep = dates < today
    dropped = int((~keep).sum())
    if not dropped:
        return df, 0
    return df[keep], dropped


def drop_partial_bar(df: pd.DataFrame,
                     now: datetime | None = None) -> tuple[pd.DataFrame, bool]:
    """
    While the market is OPEN the final daily bar is INCOMPLETE: its Close is
    the live price and its Volume only what has traded so far today.

    US equity volume is U-shaped — a stock has roughly 15% of its daily volume
    by 10:00 ET and does not cross 70% until about 14:40 ET. Comparing that
    partial volume against a 20-day average of FULL days means a perfectly
    normal stock fails the volume floor all morning and passes every afternoon.
    The filter measures the clock, not the market.

    That is exactly what produced the 12:30 alerts that the app then rejected.

    Returns (df, dropped_flag).
    """
    #
    # KEPT for its callers' (df, bool) contract; the decision now lives in
    # drop_unsettled(). Its old body was `df.iloc[:-1] if is_market_open(now)`,
    # which identified the wrong row (the last one, not the one dated today) and
    # asked the wrong question (open now, rather than has today closed), so it
    # left the stub bar in place before the open.
    if df is None or len(df) < 2:
        return df, False
    out, n = drop_unsettled(df, policy=DROP_UNTIL_CLOSE, now=now)
    return out, bool(n)


# ---------------------------------------------------------------------------
# The signal
# ---------------------------------------------------------------------------

def _base_reason(price, ema20, ema50, macd, sig, rsi, volume, vol_avg,
                 params) -> str:
    """Which base condition failed — so the UI can say more than 'no signal'."""
    bits = []
    if not (price > ema20 > ema50 or price < ema20 < ema50):
        bits.append("EMA stack not aligned")
    if not (macd > sig or macd < sig):
        bits.append("MACD flat")
    elif (price > ema20 > ema50) and macd <= sig:
        bits.append("MACD not confirming the uptrend")
    elif (price < ema20 < ema50) and macd >= sig:
        bits.append("MACD not confirming the downtrend")
    # Must mirror the ACTUAL bands used below: 30-75 for a bullish stack,
    # 25-70 for a bearish one. A single 25-75 test here reported "RSI fine"
    # for an RSI of 27 in an uptrend, which is exactly the bar that failed.
    if price > ema20 > ema50 and not (30 < rsi < 75):
        bits.append(f"RSI {rsi:.0f} outside the 30-75 band for a long")
    elif price < ema20 < ema50 and not (25 < rsi < 70):
        bits.append(f"RSI {rsi:.0f} outside the 25-70 band for a short")
    if vol_avg > 0 and volume < vol_avg * params.volume_soft_mult:
        bits.append(f"volume {volume/vol_avg:.2f}x average "
                    f"(needs {params.volume_soft_mult:g}x)")
    elif vol_avg <= 0:
        bits.append("no volume average available")
    return "; ".join(bits) if bits else "base conditions not met"


def _norm_trend(v: str | None) -> str | None:
    """
    Normalise trend vocabulary.

    app.py's get_spy_regime() emits "Bull"/"Bear"/"Neutral"/"Unknown"; other
    callers naturally write "Bullish"/"Bearish". An earlier version of this
    module compared raw strings, so "Bear" never matched "Bearish" and the
    regime filter passed EVERY time — a filter that looks active and does
    nothing is worse than no filter at all. Normalising here means a caller
    cannot break the gate by choosing different words.
    """
    if not v:
        return None
    t = str(v).strip().lower()
    if t.startswith("bull"):
        return "Bullish"
    if t.startswith("bear"):
        return "Bearish"
    return None


def _check_regime(trend: str, spy_regime: dict) -> tuple[bool, str]:
    """Counter-regime gate. Unknown/Neutral does not block — it is not a view."""
    raw = spy_regime.get("regime", "Unknown")
    reg = _norm_trend(raw)
    if reg is None:
        return True, f"Regime {raw} — no filter applied"
    if trend == "Bullish" and reg == "Bearish":
        return False, "Counter-regime: long setup in a bearish SPY market"
    if trend == "Bearish" and reg == "Bullish":
        return False, "Counter-regime: short setup in a bullish SPY market"
    return True, f"Regime aligned: {trend} in {raw} market"



def base_trend(latest, params) -> str | None:
    """
    The BASE condition, and nothing else: "Bullish", "Bearish" or None for
    one computed bar. Extracted from evaluate() so a second signal (the
    pullback bucket, results/pullback_preregistration.md) reads the very
    same test rather than a copy of it. evaluate() calls this.
    """
    price = float(latest["Close"])
    ema20 = float(latest["EMA20"])
    ema50 = float(latest["EMA50"])
    rsi = float(latest["RSI"])
    macd = float(latest["MACD"])
    sig = float(latest["Signal"])
    volume = float(latest.get("Volume", 0) or 0)
    vol_avg = float(latest.get("VOL_AVG20", 0) or 0)
    vol_soft_ok = vol_avg > 0 and volume >= vol_avg * params.volume_soft_mult
    if price > ema20 > ema50 and macd > sig and 30 < rsi < 75 and vol_soft_ok:
        return "Bullish"
    if price < ema20 < ema50 and macd < sig and 25 < rsi < 70 and vol_soft_ok:
        return "Bearish"
    return None


def long_levels(df, price: float, atr: float, params) -> tuple[float, float, float]:
    """
    The live LONG-side entry, stop and target for the last bar of `df`.
    Extracted from evaluate() (which calls it) so the pullback bucket builds
    its levels with the base signal's own arithmetic: only the choice of bar
    differs between the two buckets.
    """
    swing_low_10 = float(df["Low"].tail(10).min())
    entry = round(price, 2)
    atr_stop = price - (atr * params.atr_stop_mult)
    structural_stop = swing_low_10 - (atr * 0.10)
    # A 10-bar low ABOVE price (gap down) is meaningless as a stop; using it
    # jams risk to a cent and produces phantom R:R in the hundreds.
    stop = max(structural_stop, atr_stop) if structural_stop < price else atr_stop
    stop = round(min(stop, entry - 0.01), 2)

    raw_target = price + (atr * params.atr_tgt_mult)
    resistance = float(df["High"].tail(20).max())
    # Cap only at MEANINGFUL resistance — at least 1 ATR away. In a steady
    # uptrend the 20-bar high is the latest bar's high, and capping against
    # it crushes the target to just above entry.
    if resistance >= entry + atr:
        target = round(min(raw_target, resistance * 0.995), 2)
    else:
        target = round(raw_target, 2)
    target = round(max(target, entry + 0.02), 2)
    return entry, stop, target


def gate_adx(adx_raw: float, adx_min: float) -> bool:
    """ADX >= adx_min, compared UNROUNDED (amendment 2026-10-28 E). The
    rounded value is for display only: 34.96 used to pass a 35 threshold."""
    return float(adx_raw) >= float(adx_min)


def gate_rr(rr_raw: float, rr_min: float) -> bool:
    """R:R >= rr_min on the cent-rounded levels, compared UNROUNDED
    (amendment 2026-10-28 E): 0.996 used to pass a 1.0 threshold."""
    return float(rr_raw) >= float(rr_min)


def evaluate(df: pd.DataFrame,
             ticker: str,
             params: SignalParams = DEFAULTS,
             *,
             adx_value: float | None = None,
             weekly_trend: str | None = None,
             earnings: tuple[bool, str] = (True, "Earnings check not supplied"),
             spy_regime: dict | None = None) -> dict:
    """
    Evaluate one ticker. `df` must already have indicators computed AND the
    partial bar dropped — call drop_partial_bar() first.

    Injected inputs (all optional, all network-dependent in the caller):
      adx_value    — precomputed ADX; falls back to df["ADX"].iloc[-1]
      weekly_trend — "Bullish"/"Bearish"/None from the weekly timeframe
      earnings     — (ok, detail) from the caller's earnings check
      spy_regime   — dict with a "regime" key, or None

    Returns a dict with "blocked" True/False. When blocked, "block_reason" is
    one of: base | zero_risk | rr — and the diagnostic fields for that reason
    are populated so the UI can explain the rejection.
    """
    # A missing indicator column must be an ERROR, not a silent rejection.
    #
    # scanner.py's compute() shipped without VOL_AVG20 while app.py's had it.
    # evaluate() read it with latest.get("VOL_AVG20", 0), got 0, and every
    # ticker failed the volume floor — so the scanner reported "no signal"
    # forever and looked like a quiet market rather than a broken pipeline.
    # A caller that hands us an incomplete frame is misconfigured; say so.
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"{ticker}: evaluate() requires {', '.join(missing)} but the frame "
            f"does not have {'them' if len(missing) > 1 else 'it'}. The caller's "
            f"compute() must produce every column in "
            f"signal_core.REQUIRED_COLUMNS before calling evaluate()."
        )

    latest = df.iloc[-1]
    price = float(latest["Close"])
    ema20 = float(latest["EMA20"])
    ema50 = float(latest["EMA50"])
    rsi = float(latest["RSI"])
    macd = float(latest["MACD"])
    sig = float(latest["Signal"])
    atr = float(latest["ATR"])
    volume = float(latest.get("Volume", 0) or 0)
    vol_avg = float(latest.get("VOL_AVG20", 0) or 0)

    vol_ok = vol_avg > 0 and volume >= vol_avg * params.volume_mult

    # ── Base conditions ── (base_trend() holds the soft volume floor too)
    trend = base_trend(latest, params)
    if trend is None:
        return {
            "blocked": True, "block_reason": "base",
            "reason": _base_reason(price, ema20, ema50, macd, sig, rsi,
                                   volume, vol_avg, params),
            "ticker": ticker,
            "price": round(price, 2), "ema20": round(ema20, 2),
            "ema50": round(ema50, 2), "rsi": round(rsi, 1),
            "macd": round(macd, 4), "signal_line": round(sig, 4),
            "vol_ratio": round(volume / vol_avg, 2) if vol_avg else 0,
            "filters": {},
        }

    strength = "Strong" if (
        ((rsi > 60 and trend == "Bullish") or (rsi < 40 and trend == "Bearish"))
        and vol_ok
    ) else "Normal"

    # ── Four enhancement filters ──
    filters: dict[str, dict] = {}

    adx_raw = float(adx_value if adx_value is not None else (latest.get("ADX", 0) or 0))
    adx_val = round(adx_raw, 1)                 # display; the gate reads adx_raw
    adx_ok = gate_adx(adx_raw, params.adx_min)
    filters["ADX Trend Strength"] = {
        "pass": adx_ok,
        "detail": f"ADX {adx_val} {'>=' if adx_ok else '<'} {params.adx_min:g} threshold"}

    if not params.weekly_confirm:
        mtf_ok, mtf_detail = True, "Weekly confirmation disabled"
    elif weekly_trend is None:
        # BLOCKING when unavailable, matching app.py's original behaviour. An
        # earlier version of this module treated None as non-blocking, which
        # silently loosened the app every time the weekly fetch failed.
        mtf_ok, mtf_detail = False, "Weekly data unavailable"
    elif _norm_trend(weekly_trend) == trend:
        mtf_ok, mtf_detail = True, f"Weekly {weekly_trend} aligned"
    else:
        mtf_ok = False
        mtf_detail = f"Daily {trend} vs Weekly {weekly_trend} — misaligned"
    filters["Multi-TF Alignment"] = {"pass": mtf_ok, "detail": mtf_detail}

    earnings_ok, earnings_detail = earnings
    filters["Earnings Blackout"] = {"pass": earnings_ok, "detail": earnings_detail}

    if params.spy_regime_on and spy_regime:
        regime_ok, regime_detail = _check_regime(trend, spy_regime)
    else:
        regime_ok, regime_detail = True, "Regime filter disabled"
    filters["Macro Regime"] = {"pass": regime_ok, "detail": regime_detail}

    n_pass = sum(1 for f in filters.values() if f["pass"])
    n_total = len(filters)
    all_pass = (n_pass == n_total)

    # ── Entry / stop / target ──
    # All three anchor to the SAME reference (current price) so they are
    # internally coherent. Structure informs the stop; the resistance/support
    # cap applies only when the level is genuinely beyond entry.
    swing_high_10 = float(df["High"].tail(10).max())

    if trend == "Bullish":
        entry, stop, target = long_levels(df, price, atr, params)   # the 10-bar low lives there
    else:
        entry = round(price, 2)
        atr_stop = price + (atr * params.atr_stop_mult)
        structural_stop = swing_high_10 + (atr * 0.10)
        stop = min(structural_stop, atr_stop) if structural_stop > price else atr_stop
        stop = round(max(stop, entry + 0.01), 2)

        raw_target = price - (atr * params.atr_tgt_mult)
        support = float(df["Low"].tail(20).min())
        if support <= entry - atr:
            target = round(max(raw_target, support * 1.005), 2)
        else:
            target = round(raw_target, 2)
        target = round(min(target, entry - 0.02), 2)

    # ── Risk sanity gate ──
    # Relative to price, not an absolute penny: a $0.01 stop on a $100 stock is
    # 0.01% and would be taken out by any tick.
    risk = abs(entry - stop)
    min_risk = max(0.05, price * 0.003)
    if risk < min_risk:
        return {
            "blocked": True, "block_reason": "zero_risk",
            "reason": (f"stop too close to entry (risk ${abs(entry-stop):.2f} "
                       f"< minimum ${max(0.05, price*0.003):.2f})"),
            "ticker": ticker,
            "trend": trend, "price": round(price, 2), "entry": entry,
            "stop": stop, "risk": round(risk, 2), "min_risk": round(min_risk, 2),
            "filters": filters, "filters_pass": n_pass, "filters_total": n_total,
        }

    rr_raw = abs(target - entry) / risk          # the levels are cent-rounded; the ratio is not
    rr = round(rr_raw, 2)                        # display; the gates read rr_raw
    if not gate_rr(rr_raw, params.min_rr):
        return {
            "blocked": True, "block_reason": "rr",
            "reason": f"R:R {rr} below the {params.min_rr:g} minimum",
            "ticker": ticker,
            "trend": trend, "strength": strength, "price": round(price, 2),
            "entry": entry, "stop": stop, "target": target, "rr": rr,
            "filters": filters, "filters_pass": n_pass, "filters_total": n_total,
            "rsi": round(rsi, 1), "adx": adx_val, "rr_raw": rr_raw, "adx_raw": adx_raw,
        }

    high_quality = (gate_rr(rr_raw, params.hq_min_rr) and strength == "Strong" and all_pass)

    return {
        "blocked": False, "ticker": ticker, "price": round(price, 2),
        "trend": trend, "strength": strength, "entry": entry, "stop": stop,
        "target": target, "rr": rr, "rsi": round(rsi, 1), "adx": adx_val,
        "rr_raw": rr_raw, "adx_raw": adx_raw,
        "atr": round(atr, 2), "ema20": round(ema20, 2), "ema50": round(ema50, 2),
        "volume": volume, "vol_avg": vol_avg,
        "vol_ratio": round(volume / vol_avg, 2) if vol_avg else 0,
        "filters": filters, "filters_pass": n_pass, "filters_total": n_total,
        "all_pass": all_pass, "high_quality": high_quality,
    }


def params_fingerprint(p: SignalParams) -> str:
    """Stable string of every tunable — for cache keys and run logs."""
    return "_".join(f"{k}{v}" for k, v in sorted(asdict(p).items()))


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _frame(n=60, price=100.0, up=True, vol=2_000_000, vol_avg=1_000_000,
           rsi=65.0, adx=40.0, atr=2.0):
    import numpy as np
    idx = pd.bdate_range("2026-01-02", periods=n)
    close = np.linspace(price * 0.9, price, n) if up else np.linspace(price * 1.1, price, n)
    df = pd.DataFrame({
        "Open": close, "Close": close,
        "High": close + atr * 0.5, "Low": close - atr * 0.5,
        "Volume": [vol_avg] * (n - 1) + [vol],
        "EMA20": close * (0.98 if up else 1.02),
        "EMA50": close * (0.96 if up else 1.04),
        "RSI": rsi, "MACD": 1.0 if up else -1.0, "Signal": 0.5 if up else -0.5,
        "ATR": atr, "ADX": adx, "VOL_AVG20": vol_avg,
    }, index=idx)
    return df


def selftest() -> int:
    from datetime import datetime as _dt

    # ── unsettled bars: one decision, two policies ──
    # The fixture must END on the reference day, or the test asserts nothing: the
    # old version dropped iloc[-1] regardless of dates, so it passed on ANY frame
    # and could not tell a today-dated bar from a month-old one.
    _ref = _dt(2026, 8, 25)                  # a Tuesday
    _n = 60
    _idx = pd.bdate_range(end=pd.Timestamp(_ref.date()), periods=_n)
    df = _frame(n=_n).set_axis(_idx)
    assert _bar_dates(df, None).max() == pd.Timestamp(_ref.date()), \
        "fixture must carry a bar dated on the reference day"

    pre_open = _dt(2026, 8, 25, 7, 0)        # Yahoo already emits today's row
    mid = _dt(2026, 8, 25, 12, 30)
    after = _dt(2026, 8, 25, 17, 0)

    d0, dropped0 = drop_partial_bar(df, now=pre_open)
    d1, dropped1 = drop_partial_bar(df, now=mid)
    d2, dropped2 = drop_partial_bar(df, now=after)
    print(f"unsettled, pre-open       : dropped={dropped0}, {len(d0)} bars")
    print(f"unsettled, market open    : dropped={dropped1}, {len(d1)} bars "
          f"(was {len(df)})")
    print(f"unsettled, after close    : dropped={dropped2}, {len(d2)} bars")
    # THE REGRESSION THIS PINS. The old rule asked `is_market_open()`, which is
    # False before the open, so today's stub bar was used for every pre-market
    # scan. backtest._drop_todays_bar() had documented that gap and fixed it for
    # itself only.
    assert dropped0 and len(d0) == len(df) - 1, \
        "today's bar must be dropped BEFORE the open too — it is a stub there, " \
        "not settled data"
    assert dropped1 and len(d1) == len(df) - 1
    assert not dropped2 and len(d2) == len(df), \
        "after the close today's bar is settled and is the freshest real data"

    sat = _dt(2026, 8, 29, 12, 0)             # the Saturday AFTER the last bar
    assert not drop_partial_bar(df, now=sat)[1], \
        "a Saturday reference day has no session in progress"
    # A Saturday BEFORE the frame's last bars makes those bars future-dated,
    # and a future-dated bar is never data (BACKLOG 36 group A).
    assert drop_partial_bar(df, now=_dt(2026, 8, 22, 12, 0))[1], \
        "bars dated after the reference day must go, whatever the weekday"

    # DROP_ALWAYS is the reproducible policy: the close does not rescue the bar.
    _a1, _n1 = drop_unsettled(df, policy=DROP_ALWAYS, now=after)
    assert _n1 == 1 and len(_a1) == len(df) - 1, \
        "DROP_ALWAYS must drop a today-dated bar even after it settles — that " \
        "is the whole difference from the live policy"
    _u1, _n2 = drop_unsettled(df, policy=DROP_UNTIL_CLOSE, now=after)
    assert _n2 == 0, "and DROP_UNTIL_CLOSE must not, or the two are one policy"
    print("policies differ           : after the close ALWAYS drops, "
          "UNTIL_CLOSE keeps")

    # A date COLUMN works the same as a DatetimeIndex (backtest frames use one).
    _col = df.reset_index().rename(columns={"index": "Date"})
    _c1, _cn = drop_unsettled(_col, policy=DROP_ALWAYS, now=after,
                              date_col="Date")
    assert _cn == 1 and len(_c1) == len(_col) - 1, \
        "a date column must be honoured, or backtest frames silently keep the bar"
    # An undated frame must be left alone rather than guessed at.
    _bad = df.reset_index(drop=True)
    assert drop_unsettled(_bad, policy=DROP_ALWAYS, now=after)[1] == 0, \
        "an undated frame must be returned untouched, not trimmed by position"
    print("frame shapes              : index or Date column; undated untouched")

    # A bar dated AFTER today is never data, whatever the clock (BACKLOG 36
    # group A): after the close UNTIL_CLOSE keeps today's bar and still drops
    # tomorrow's; ALWAYS drops both.
    _fut = pd.concat([df, df.iloc[[-1]].set_index(df.index[-1:] + pd.Timedelta(days=1))])
    _f1, _fn1 = drop_unsettled(_fut, policy=DROP_UNTIL_CLOSE, now=after)
    assert _fn1 == 1 and _f1.index[-1] == df.index[-1], \
        "a future-dated bar must be dropped after the close, today's kept"
    assert drop_unsettled(_fut, policy=DROP_ALWAYS, now=after)[1] == 2
    assert drop_unsettled(_fut, policy=DROP_UNTIL_CLOSE, now=mid)[1] == 2, \
        "mid-session both today's stub and the future bar go"
    print("future bars               : dropped whatever the clock or policy")

    # validate_bars: one reason per defect, None when clean.
    assert validate_bars(df) is None, validate_bars(df)
    _v = df.copy(); _v.iloc[3, _v.columns.get_loc("Close")] = float("nan")
    assert validate_bars(_v) == "non-finite OHLC", validate_bars(_v)
    _v = df.copy(); _v.iloc[3, _v.columns.get_loc("Low")] = -1.0
    assert validate_bars(_v) == "non-positive price", validate_bars(_v)
    _v = df.copy(); _v.iloc[3, _v.columns.get_loc("Low")] = float(_v["High"].iloc[3]) + 1.0
    assert validate_bars(_v) == "low above high", validate_bars(_v)
    _v = pd.concat([df, df.iloc[[2]]])
    assert validate_bars(_v) == "duplicate bar dates", validate_bars(_v)
    _v = df.iloc[::-1]
    assert validate_bars(_v) == "bars out of order", validate_bars(_v)
    assert validate_bars(df.drop(columns=["Low"])) == "missing columns Low"
    assert validate_bars(_col, date_col="Date") is None, "a Date column is read like the index"
    print("validate_bars             : finite, positive, low<=high, dated, unique, ordered")

    # session_closed() is the question, and it is NOT `not is_market_open()`.
    assert not session_closed(pre_open) and not session_closed(mid)
    assert session_closed(after) and session_closed(sat)
    assert not is_market_open(pre_open), \
        "pre-open is 'market closed' but NOT 'session closed' — conflating the " \
        "two is what kept the stub bar"
    print("session_closed            : pre-open is not 'closed', post-close is")

    # clean bullish signal
    r = evaluate(_frame(), "TEST", spy_regime={"regime": "Bullish"},
                 weekly_trend="Bullish")
    print(f"\nclean bullish             : blocked={r['blocked']}, "
          f"trend={r.get('trend')}, rr={r.get('rr')}, hq={r.get('high_quality')}")
    assert not r["blocked"] and r["trend"] == "Bullish"

    # ADX below threshold blocks the filter but not the signal
    r = evaluate(_frame(adx=20), "TEST", spy_regime={"regime": "Bullish"},
                 weekly_trend="Bullish")
    assert r["filters"]["ADX Trend Strength"]["pass"] is False
    assert r["high_quality"] is False
    print(f"low ADX                   : filter fails, high_quality="
          f"{r['high_quality']}")

    # UNROUNDED GATES (amendment 2026-10-28 E): 34.96 is below 35, 0.996 is
    # below 1.0; the displayed values are still rounded. The 35 gate is
    # pinned explicitly here: DEFAULTS moved to 30 (amendment F, below).
    _g35 = replace(DEFAULTS, adx_min=35.0)
    r = evaluate(_frame(), "TEST", _g35, adx_value=34.96, spy_regime={"regime": "Bullish"},
                 weekly_trend="Bullish")
    assert r["filters"]["ADX Trend Strength"]["pass"] is False and r["high_quality"] is False \
        and r["adx"] == 35.0 and abs(r["adx_raw"] - 34.96) < 1e-12, (
        r["filters"]["ADX Trend Strength"], r["adx"], "34.96 must fail a 35 gate while displaying 35.0")
    r = evaluate(_frame(), "TEST", _g35, adx_value=35.0, spy_regime={"regime": "Bullish"},
                 weekly_trend="Bullish")
    assert r["filters"]["ADX Trend Strength"]["pass"] is True
    # THE LIVE GATE IS 30 (amendment 2026-10-28 F, BACKLOG 33): an intake
    # choice, declared; 29.96 fails it and 30.0 passes it, unrounded.
    assert DEFAULTS.adx_min == 30.0, DEFAULTS.adx_min
    _r29 = evaluate(_frame(), "TEST", adx_value=29.96, spy_regime={"regime": "Bullish"}, weekly_trend="Bullish")
    _r30 = evaluate(_frame(), "TEST", adx_value=30.0, spy_regime={"regime": "Bullish"}, weekly_trend="Bullish")
    assert _r29["filters"]["ADX Trend Strength"]["pass"] is False and _r30["filters"]["ADX Trend Strength"]["pass"] is True, \
        "the live ADX leg is 30, compared unrounded"
    _r34 = evaluate(_frame(), "TEST", adx_value=34.0, spy_regime={"regime": "Bullish"}, weekly_trend="Bullish")
    assert _r34["filters"]["ADX Trend Strength"]["pass"] is True, "the [30, 35) band is admitted"
    assert gate_rr(0.996, 1.0) is False and gate_rr(1.0, 1.0) is True and gate_rr(0.4999, 0.5) is False
    assert gate_adx(34.96, 35) is False and gate_adx(35.0, 35) is True
    assert "rr_raw" in r and abs(round(r["rr_raw"], 2) - r["rr"]) < 1e-9, "rr is the rounded display of rr_raw"
    print("unrounded gates           : 34.96 fails ADX 35, 0.996 fails R:R 1.0; display stays rounded")

    # THE BUG THAT CAUSED THIS: low volume must block at the base condition.
    # The scanner had no volume gate at all, so it fired where the app did not.
    r = evaluate(_frame(vol=100_000), "TEST")
    print(f"low volume                : blocked={r['blocked']}, "
          f"reason={r.get('block_reason')}  <- scanner used to MISS this")
    assert r["blocked"] and r["block_reason"] == "base"

    # earnings blackout
    r = evaluate(_frame(), "TEST", earnings=(False, "Earnings in 2 days"),
                 spy_regime={"regime": "Bullish"}, weekly_trend="Bullish")
    assert r["filters"]["Earnings Blackout"]["pass"] is False
    assert r["high_quality"] is False
    print(f"earnings blackout         : filter fails, high_quality=False")

    # REGRESSION: app.py emits "Bull"/"Bear", not "Bullish"/"Bearish".
    # Raw string comparison made the regime filter pass every time.
    for word in ("Bear", "Bearish", "bear", "BEARISH"):
        r = evaluate(_frame(), "TEST", spy_regime={"regime": word},
                     weekly_trend="Bullish")
        assert r["filters"]["Macro Regime"]["pass"] is False, word
    print(f"regime conflict           : blocks on Bear/Bearish/bear/BEARISH")

    for word in ("Neutral", "Unknown", ""):
        r = evaluate(_frame(), "TEST", spy_regime={"regime": word},
                     weekly_trend="Bullish")
        assert r["filters"]["Macro Regime"]["pass"] is True, word
    print(f"regime neutral/unknown    : does not block")

    # The weekly filter is OFF by default (see SignalParams), so these tests
    # pin it on explicitly. Testing it through DEFAULTS would have quietly
    # stopped testing anything the moment the default flipped.
    wk_on = replace(DEFAULTS, weekly_confirm=True)

    # REGRESSION: weekly unavailable must BLOCK when the filter is ON. Failing
    # closed is deliberate — a dead fetch must not silently loosen the system.
    # It is also the reason the filter now defaults off: it made every
    # rate-limited Yahoo call an outage.
    r = evaluate(_frame(), "TEST", wk_on, spy_regime={"regime": "Bull"},
                 weekly_trend=None)
    assert r["filters"]["Multi-TF Alignment"]["pass"] is False
    print(f"weekly unavailable        : blocks (not silently permissive)")

    # weekly disagreement
    r = evaluate(_frame(), "TEST", wk_on, spy_regime={"regime": "Bullish"},
                 weekly_trend="Bearish")
    assert r["filters"]["Multi-TF Alignment"]["pass"] is False
    print(f"weekly disagreement       : filter fails")

    # ...and with the filter at its DEFAULT, none of that applies: the weekly
    # value is inert and cannot block. If this fails, the default has moved
    # back and the live scanner is fetching weekly bars again.
    assert DEFAULTS.weekly_confirm is False, \
        "weekly_confirm must default OFF — it rejected 5 bars in 13,748 and " \
        "fails closed on a network error"
    for _wk in (None, "Bearish", "Bullish"):
        r = evaluate(_frame(), "TEST", spy_regime={"regime": "Bull"},
                     weekly_trend=_wk)
        assert r["filters"]["Multi-TF Alignment"]["pass"] is True, _wk
    print(f"weekly at default (OFF)   : inert, blocks nothing")

    # bearish path
    r = evaluate(_frame(up=False, rsi=35), "TEST",
                 spy_regime={"regime": "Bearish"}, weekly_trend="Bearish")
    print(f"clean bearish             : blocked={r['blocked']}, "
          f"trend={r.get('trend')}")
    assert not r["blocked"] and r["trend"] == "Bearish"
    assert r["stop"] > r["entry"] > r["target"]

    # params flow through
    strict = replace(DEFAULTS, adx_min=99)
    r = evaluate(_frame(), "TEST", strict, spy_regime={"regime": "Bullish"},
                 weekly_trend="Bullish")
    assert r["filters"]["ADX Trend Strength"]["pass"] is False
    print(f"params respected          : adx_min=99 fails a 40 ADX")

    assert params_fingerprint(DEFAULTS) == params_fingerprint(SignalParams())
    print(f"\nfingerprint               : {params_fingerprint(DEFAULTS)[:52]}...")
    # REGRESSION: the UI reads r["all_pass"] in three places. An earlier
    # version of this module renamed it to "all_filters_pass", which raised
    # KeyError the moment a signal fired in the Swing Trade tab.
    r = evaluate(_frame(), "TEST", spy_regime={"regime": "Bull"},
                 weekly_trend="Bullish")
    for k in ("all_pass", "filters", "filters_pass", "filters_total", "trend",
              "strength", "entry", "stop", "target", "rr", "rsi", "adx",
              "atr", "price", "high_quality"):
        assert k in r, f"missing key the UI depends on: {k}"
    print(f"\nresult contract           : all {15} UI keys present")

    # blocked results explain themselves
    r = evaluate(_frame(vol=100_000), "TEST")
    assert r["reason"] and "volume" in r["reason"]
    print(f"blocked reason            : {r['reason']}")

    print("\nAll self-tests passed.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
