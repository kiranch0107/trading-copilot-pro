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

try:
    import pandas as pd
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
    adx_min: float = 35.0
    min_rr: float = 0.5
    hq_min_rr: float = 1.0          # high-quality tier; alerts fire on this
    volume_mult: float = 1.2        # "Strong" needs volume >= avg * this
    volume_soft_mult: float = 0.70  # base condition floor
    atr_stop_mult: float = 1.0
    atr_tgt_mult: float = 4.0
    weekly_confirm: bool = True
    spy_regime_on: bool = True


DEFAULTS = SignalParams()


# ---------------------------------------------------------------------------
# Market hours / partial bar — the bug-1 fix, shared
# ---------------------------------------------------------------------------

def is_market_open(now: datetime | None = None) -> bool:
    """
    True during US regular trading hours. Caller supplies an ET-aware datetime;
    if none is given we use naive local time, which is only correct when the
    process runs in ET — so callers should pass one explicitly.
    """
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    return dtime(9, 30) <= now.time() <= dtime(16, 0)


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
    if df is None or len(df) < 2:
        return df, False
    if not is_market_open(now):
        return df, False
    return df.iloc[:-1], True


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
    if not (25 < rsi < 75):
        bits.append(f"RSI {rsi:.0f} outside range")
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
    vol_soft_ok = vol_avg > 0 and volume >= vol_avg * params.volume_soft_mult

    # ── Base conditions ──
    if price > ema20 > ema50 and macd > sig and 30 < rsi < 75 and vol_soft_ok:
        trend = "Bullish"
    elif price < ema20 < ema50 and macd < sig and 25 < rsi < 70 and vol_soft_ok:
        trend = "Bearish"
    else:
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

    adx_val = adx_value if adx_value is not None else float(latest.get("ADX", 0) or 0)
    adx_val = round(adx_val, 1)
    adx_ok = adx_val >= params.adx_min
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
    swing_low_10 = float(df["Low"].tail(10).min())
    swing_high_10 = float(df["High"].tail(10).max())

    if trend == "Bullish":
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

    rr = round(abs(target - entry) / risk, 2)
    if rr < params.min_rr:
        return {
            "blocked": True, "block_reason": "rr",
            "reason": f"R:R {rr} below the {params.min_rr:g} minimum",
            "ticker": ticker,
            "trend": trend, "strength": strength, "price": round(price, 2),
            "entry": entry, "stop": stop, "target": target, "rr": rr,
            "filters": filters, "filters_pass": n_pass, "filters_total": n_total,
            "rsi": round(rsi, 1), "adx": adx_val,
        }

    high_quality = (rr >= params.hq_min_rr and strength == "Strong" and all_pass)

    return {
        "blocked": False, "ticker": ticker, "price": round(price, 2),
        "trend": trend, "strength": strength, "entry": entry, "stop": stop,
        "target": target, "rr": rr, "rsi": round(rsi, 1), "adx": adx_val,
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

    # partial bar
    df = _frame()
    open_dt = _dt(2026, 8, 25, 12, 30)      # Tuesday midday
    closed_dt = _dt(2026, 8, 25, 17, 0)     # after the close
    d1, dropped1 = drop_partial_bar(df, now=open_dt)
    d2, dropped2 = drop_partial_bar(df, now=closed_dt)
    print(f"partial bar, market open  : dropped={dropped1}, {len(d1)} bars "
          f"(was {len(df)})")
    print(f"partial bar, after close  : dropped={dropped2}, {len(d2)} bars")
    assert dropped1 and len(d1) == len(df) - 1
    assert not dropped2 and len(d2) == len(df)
    sat = _dt(2026, 8, 22, 12, 0)
    assert not drop_partial_bar(df, now=sat)[1]
    print("partial bar, weekend      : not dropped")

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

    # REGRESSION: weekly unavailable must BLOCK, as app.py always did.
    r = evaluate(_frame(), "TEST", spy_regime={"regime": "Bull"},
                 weekly_trend=None)
    assert r["filters"]["Multi-TF Alignment"]["pass"] is False
    print(f"weekly unavailable        : blocks (not silently permissive)")

    # weekly disagreement
    r = evaluate(_frame(), "TEST", spy_regime={"regime": "Bullish"},
                 weekly_trend="Bearish")
    assert r["filters"]["Multi-TF Alignment"]["pass"] is False
    print(f"weekly disagreement       : filter fails")

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
