#!/usr/bin/env python3
"""
pullback_signal.py — a long entry on weakness inside an uptrend, recorded forward
=================================================================================

BACKLOG 31, option 1. Pre-registered in results/pullback_preregistration.md
before the first row was recorded; read that first. Nothing here is tunable
without a dated amendment there.

THE RULE (one implementation, shared with the live signal)
    1. the BASE BEARISH setup of signal_core.evaluate(): close < EMA20 < EMA50,
       MACD below its signal line, 25 < RSI14 < 70, volume >= 0.70x its 20-bar
       average  -- signal_core.base_trend(), the function evaluate() itself calls;
    2. the name is in an UPTREND: the close is above its 200-session simple
       moving average, computed on the raw daily frame up to and including
       the signal bar (SMA_SESSIONS);
    3. the trade is LONG, with the live config's long-side levels exactly as
       the base bucket builds them -- signal_core.long_levels() -- and the same
       risk-sanity and R:R >= min_rr gates.

    So geometry, resolver and costs are the base bucket's; only the choice of
    BAR differs, which is the one thing under test.

WHAT THIS MODULE DOES NOT DO
    It never writes the forward log (scanner.py is the record's one writer and
    records the row under bucket="pullback"), never alerts, never fetches.
    The SPY regime is TAGGED on the row and gates nothing.

Run
---
    python pullback_signal.py --selftest      # offline, synthetic frames
"""
from __future__ import annotations

import sys

import pandas as pd

import signal_core as sc

SMA_SESSIONS = 200          # the uptrend test: close above the 200-session SMA
BUCKET = "pullback"
SETUP_KEYS = ("price", "entry", "stop", "target", "rr", "rsi", "adx", "atr",
              "strength", "sma200", "regime", "ema20", "ema50")


def _tally(tally: dict | None, key: str) -> None:
    if tally is not None:
        tally[key] = tally.get(key, 0) + 1


def sma_at_signal_bar(raw_df, cdf, sessions: int = SMA_SESSIONS) -> float | None:
    """
    The simple moving average of the RAW frame's closes over the last
    `sessions` bars up to and including the computed frame's last bar.

    The computed frame has its indicator warm-up sliced off and today's
    partial bar dropped, so it is too short for a 200-session average; the
    raw frame has the history but may still hold today's partial bar. Taking
    the raw closes dated at or before the computed frame's last bar uses the
    history and excludes the partial bar.
    """
    if raw_df is None or cdf is None or len(cdf) == 0 or len(raw_df) == 0:
        return None
    last = sc._bar_dates(cdf, None).max()
    dates = sc._bar_dates(raw_df, None)
    closes = pd.Series(raw_df["Close"].to_numpy(dtype=float), index=dates)
    closes = closes[closes.index <= last]
    if len(closes) < sessions:
        return None
    return float(closes.iloc[-sessions:].mean())


def evaluate_pullback(raw_df, cdf, params, spy_regime: dict | None = None,
                      tally: dict | None = None) -> dict | None:
    """
    The pullback signal on the last bar of `cdf` (the computed, settled
    frame the base signal just read), with `raw_df` for the 200-session
    average. Returns the setup dict for a LONG trade, or None.
    """
    if cdf is None or len(cdf) == 0:
        return None
    latest = cdf.iloc[-1]
    if sc.base_trend(latest, params) != "Bearish":
        return None
    sma = sma_at_signal_bar(raw_df, cdf)
    if sma is None:
        _tally(tally, "insufficient_history")
        return None
    price = float(latest["Close"])
    atr = float(latest["ATR"])
    if not price > sma:
        _tally(tally, "below_sma200")
        return None
    if not atr > 0:
        return None
    entry, stop, target = sc.long_levels(cdf, price, atr, params)
    # The base signal's risk-sanity gate, verbatim (signal_core.evaluate):
    # relative to price, not an absolute penny.
    risk = abs(entry - stop)
    if risk < max(0.05, price * 0.003):
        _tally(tally, "zero_risk")
        return None
    rr = round(abs(target - entry) / risk, 2)
    if rr < params.min_rr:
        _tally(tally, "rr_below_min")
        return None
    _tally(tally, "signals")
    return {
        "trend": "Bullish", "price": round(price, 2), "entry": entry,
        "stop": stop, "target": target, "rr": rr, "atr": round(atr, 2),
        "rsi": round(float(latest["RSI"]), 1),
        "adx": round(float(latest.get("ADX", 0) or 0), 1),
        "sma200": round(sma, 2), "strength": "Normal",
        "regime": str((spy_regime or {}).get("regime") or "Unknown"),
        "ema20": round(float(latest["EMA20"]), 2),
        "ema50": round(float(latest["EMA50"]), 2),
    }


# ── self-test on synthetic frames ────────────────────────────────────────

def _frame(closes, vol: float = 1e6) -> pd.DataFrame:
    idx = pd.bdate_range(end="2026-06-30", periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    o = c.shift(1).fillna(c.iloc[0])
    return pd.DataFrame({"Open": o, "High": pd.concat([o, c], axis=1).max(axis=1) * 1.004,
                         "Low": pd.concat([o, c], axis=1).min(axis=1) * 0.996,
                         "Close": c, "Volume": vol}, index=idx)


def selftest() -> int:
    from scanner import compute            # the production indicator set, offline
    print("pullback_signal.py selftest")
    print("=" * 72)
    params = sc.DEFAULTS
    assert SMA_SESSIONS == 200 and BUCKET == "pullback"
    # A long uptrend (260 bars, +0.2%/day), then a 24-bar pullback that is
    # steady enough to flip the base condition bearish but shallow enough
    # to stay above the 200-session average: alternate -0.8% / +0.3%.
    up = [100.0 * 1.002 ** i for i in range(260)]
    pb = []
    px = up[-1]
    for k in range(24):
        px *= 0.992 if k % 2 == 0 else 1.003
        pb.append(px)
    raw = _frame(up + pb)
    cdf = compute(raw)
    latest = cdf.iloc[-1]
    assert sc.base_trend(latest, params) == "Bearish", (
        float(latest["Close"]), float(latest["EMA20"]), float(latest["EMA50"]),
        float(latest["MACD"]), float(latest["Signal"]), float(latest["RSI"]))
    sma = sma_at_signal_bar(raw, cdf)
    assert sma is not None and float(latest["Close"]) > sma, (float(latest["Close"]), sma)
    assert abs(sma - raw["Close"].iloc[-200:].mean()) < 1e-9, "SMA over the raw frame's last 200 bars"
    tally: dict = {}
    s = evaluate_pullback(raw, cdf, params, {"regime": "Bull"}, tally)
    assert s and s["trend"] == "Bullish" and s["regime"] == "Bull" and tally == {"signals": 1}, (s, tally)
    assert s["stop"] < s["entry"] < s["target"] and s["rr"] >= params.min_rr, s
    e, st, tg = sc.long_levels(cdf, float(latest["Close"]), float(latest["ATR"]), params)
    assert (s["entry"], s["stop"], s["target"]) == (e, st, tg), "levels are the base signal's own"
    assert s["entry"] - s["stop"] <= 1.25 * float(latest["ATR"]) + 1e-6, "stop no wider than 1.25 ATR"
    assert set(SETUP_KEYS) <= set(s), set(SETUP_KEYS) - set(s)
    print(f"fires            : bearish base setup above SMA200 ({float(latest['Close']):.2f} > "
          f"{sma:.2f}) -> LONG, levels = long_levels(), rr {s['rr']}, regime tagged")

    # The same pullback shape BELOW the average: a long downtrend first.
    down = [200.0 * 0.998 ** i for i in range(260)]
    pb2 = []
    px = down[-1]
    for k in range(24):
        px *= 0.992 if k % 2 == 0 else 1.003
        pb2.append(px)
    raw2 = _frame(down + pb2)
    cdf2 = compute(raw2)
    assert sc.base_trend(cdf2.iloc[-1], params) == "Bearish", "the base setup is the same"
    t2: dict = {}
    assert evaluate_pullback(raw2, cdf2, params, None, t2) is None and t2 == {"below_sma200": 1}, t2
    # A bullish base setup is not a pullback.
    raw3 = _frame([100.0 * 1.003 ** i for i in range(284)])
    cdf3 = compute(raw3)
    assert sc.base_trend(cdf3.iloc[-1], params) == "Bullish"
    assert evaluate_pullback(raw3, cdf3, params) is None
    # Fewer than 200 sessions of raw history: no view, counted.
    t4: dict = {}
    assert evaluate_pullback(raw.iloc[-199:], cdf, params, None, t4) is None and t4 == {"insufficient_history": 1}
    # Today's partial bar in the raw frame does not enter the average.
    raw5 = pd.concat([raw, _frame([raw["Close"].iloc[-1] * 3.0]).set_index(
        pd.DatetimeIndex([raw.index[-1] + pd.Timedelta(days=1)]))])
    assert abs(sma_at_signal_bar(raw5, cdf) - sma) < 1e-9, "bars after the signal bar are ignored"
    # The R:R gate is the base signal's.
    import dataclasses
    strict = dataclasses.replace(params, min_rr=50.0)
    t6: dict = {}
    assert evaluate_pullback(raw, cdf, strict, None, t6) is None and t6 == {"rr_below_min": 1}, t6
    print("refuses          : below SMA200, bullish base setup, <200 sessions, R:R below min; "
          "partial bar ignored")
    print("=" * 72)
    print("All self-tests passed.")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
