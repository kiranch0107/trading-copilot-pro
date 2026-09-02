#!/usr/bin/env python3
"""
market_context.py — ONE implementation of the weekly trend and the SPY regime

WHY THIS EXISTS
---------------
signal_core.evaluate() takes weekly_trend, earnings and spy_regime as INJECTED
inputs, so signal_core itself is genuinely a single implementation. But app.py
and scanner.py each computed those inputs their own way, and two of them had
diverged into different rules entirely:

    get_weekly_trend   app: EMA10w vs EMA20w crossover, 1y, >=20 bars
                   scanner: price vs EMA20w,            2y, >=30 bars
    get_spy_regime     app: 3-state, Bull/Bear only when ADX >= 20, else Neutral
                   scanner: 2-state, price vs 200-SMA, no ADX anywhere

"Is the 10-week EMA above the 20-week EMA" and "is price above the 20-week EMA"
are different questions that disagree constantly, and weekly alignment is a
BLOCKING filter. So the app and the scanner could reach opposite verdicts on
the same ticker at the same moment — the exact failure signal_core.py was
written to end, just moved one level upstream into its inputs.

WHICH RULE IS CORRECT — WHAT THE EVIDENCE ACTUALLY SAYS
-------------------------------------------------------
SPY REGIME: settled by the backtest. backtest.build_regime_series() is
    sma200 = Close.rolling(200).mean()
    Bull if Close > sma200, Bear if Close < sma200, else Neutral
2-state, no ADX. oos_validate.py's FROZEN config sets use_regime=True, so this
rule WAS part of the 591-trade out-of-sample test. app.py's ADX>=20 gate was
never tested by anything. The validated rule wins: no ADX gate.

  Consequence, and it is not cosmetic: app.py used to return "Neutral" whenever
  ADX < 20, and signal_core treats Neutral as "no view" — the regime filter did
  not block at all. Removing the gate makes the app STRICTER; it will now block
  counter-regime setups it previously allowed through in choppy tape. That is
  what the backtest tested, so that is what live should do. ADX is still
  computed and reported, it just no longer decides the verdict.

WEEKLY TREND: the backtest is SILENT. backtest.build_signal_params() forces
weekly_confirm=False (it fetches no weekly data), so the 591-trade OOS result
tested NEITHER rule. There is no evidence for the crossover version or the
price version.

  Two things follow, and both are stated plainly rather than papered over:
  1. This module picks price vs EMA20w — the scanner's rule — on CONSISTENCY,
     not evidence: every other trend test in this system is price against a
     moving average (daily base condition is price > EMA20 > EMA50; the regime
     is price vs 200-SMA). The EMA10w/EMA20w crossover was the odd one out.
  2. weekly_confirm defaults to TRUE live, so the live system applies a
     BLOCKING filter that its own validation never tested. That is a real
     config-vs-validation gap, separate from which rule is used. Either
     measure the filter (add weekly bars to backtest.py) or turn it off to
     match the validated config. It is recorded in oos_validate.lock.json's
     live_divergence_note.

DESIGN
------
Pure functions take bars and return a verdict; thin wrappers take an injected
fetch callable. Same shape as data_source.py and signal_core.py, and it means
the decision rules are testable offline — these two functions gate live signals
and had no tests at all before this module existed.
"""

from __future__ import annotations

import logging

try:
    import pandas as pd
except ImportError:
    raise SystemExit("Missing pandas. Run: pip install pandas")

logger = logging.getLogger(__name__)

WEEKLY_EMA_SPAN = 20      # 20-week EMA, the reference the weekly rule tests against
WEEKLY_MIN_BARS = 30      # scanner's floor; a 20-span EMA on 20 bars is barely converged
WEEKLY_PERIOD   = "2y"    # ~104 weekly bars

REGIME_SMA_WINDOW = 200
REGIME_MIN_BARS   = 200   # a 200-SMA needs 200 bars; fewer means "no view", not "Neutral tape"
REGIME_PERIOD     = "2y"

# Kept ONLY as a reported diagnostic. It used to gate the regime verdict in
# app.py (ADX < 20 => "Neutral" => filter disabled). The backtest never applied
# that gate, so it does not decide anything here. Do not reintroduce it as a
# gate without backtesting it first.
SPY_ADX_REPORT_WINDOW = 14


# ---------------------------------------------------------------------------
# Pure decision rules
# ---------------------------------------------------------------------------

def weekly_trend_from_bars(df: pd.DataFrame,
                           min_bars: int = WEEKLY_MIN_BARS) -> str | None:
    """
    "Bullish" / "Bearish" from weekly bars, or None when there is not enough
    history to judge.

    Rule: last weekly close vs the 20-week EMA. See the module docstring for
    why this rule and not the EMA10w/EMA20w crossover — it is a consistency
    argument, NOT a backtested one.

    None matters: signal_core treats an unavailable weekly trend as BLOCKING
    when weekly_confirm is on, which is deliberate (see its docstring) — an
    unknown must not silently loosen the filter.
    """
    if df is None or getattr(df, "empty", True):
        return None
    close = df["Close"].dropna()
    if len(close) < min_bars:
        return None
    ema = close.ewm(span=WEEKLY_EMA_SPAN, adjust=False).mean()
    price_w, ema_w = float(close.iloc[-1]), float(ema.iloc[-1])
    if not (pd.notna(price_w) and pd.notna(ema_w)):
        return None
    return "Bullish" if price_w > ema_w else "Bearish"


def spy_regime_from_bars(df: pd.DataFrame,
                         min_bars: int = REGIME_MIN_BARS) -> dict:
    """
    Macro regime from SPY daily bars.

    Rule: price vs its own 200-SMA, two-state — identical to
    backtest.build_regime_series(), which is the version the 591-trade
    out-of-sample test actually ran with (oos_validate FROZEN use_regime=True).

    Returns the same dict shape app.py's UI already renders:
        {regime, price, sma200, adx, reasoning}
    regime is "Bull" | "Bear" | "Unknown". "Bull"/"Bear" match the vocabulary
    backtest.build_regime_series() emits; signal_core._norm_trend() maps both
    those and "Bullish"/"Bearish" onto the same thing, so either is safe — but
    matching the backtest exactly is one less translation to get wrong.

    "Unknown" (not "Neutral") when there is too little data: signal_core treats
    an unrecognised regime as "no view, do not filter", and that is the honest
    reading of missing data. It is NOT the same statement as "the tape is
    choppy", which is what the old ADX gate was claiming.
    """
    out = {"regime": "Unknown", "price": None, "sma200": None,
           "adx": None, "reasoning": "SPY data unavailable"}
    if df is None or getattr(df, "empty", True):
        return out

    d = df.dropna(subset=["Close"])
    if len(d) < min_bars:
        out["reasoning"] = (f"only {len(d)} SPY bars, need {min_bars} for a "
                            f"{REGIME_SMA_WINDOW}-SMA")
        return out

    price = float(d["Close"].iloc[-1])
    sma200 = float(d["Close"].rolling(REGIME_SMA_WINDOW).mean().iloc[-1])
    if not (pd.notna(price) and pd.notna(sma200)):
        return out

    # Reported only — never a gate. See module docstring.
    adx_val = None
    try:
        if {"High", "Low"} <= set(d.columns):
            import ta
            adx_series = ta.trend.adx(d["High"], d["Low"], d["Close"],
                                      window=SPY_ADX_REPORT_WINDOW).dropna()
            if len(adx_series):
                adx_val = round(float(adx_series.iloc[-1]), 1)
    except Exception as e:                      # diagnostics must never break the gate
        logger.debug("SPY ADX unavailable (%s)", e)

    regime = "Bull" if price > sma200 else "Bear"
    where = "above" if regime == "Bull" else "below"
    adx_note = f" (ADX {adx_val:.0f}, reported only)" if adx_val is not None else ""
    return {"regime": regime,
            "price": round(price, 2),
            "sma200": round(sma200, 2),
            "adx": adx_val,
            "reasoning": f"SPY ${price:.0f} {where} 200-SMA ${sma200:.0f}{adx_note}"}


# ---------------------------------------------------------------------------
# Fetch wrappers — the caller injects how bars are fetched
# ---------------------------------------------------------------------------

def get_weekly_trend(ticker: str, fetch) -> str | None:
    """
    `fetch(ticker, period, interval)` returns a bar frame or None. app.py
    injects its cached/rate-limited yfinance call; scanner.py injects its
    gapped one. Returns None on any failure — which BLOCKS when
    weekly_confirm is on, rather than guessing.
    """
    try:
        df = fetch(ticker, WEEKLY_PERIOD, "1wk")
        if df is not None and isinstance(getattr(df, "columns", None), pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return weekly_trend_from_bars(df)
    except Exception as e:
        logger.debug("Weekly trend unavailable for %s (%s)", ticker, e)
        return None


def get_spy_regime(fetch, ticker: str = "SPY") -> dict:
    """Macro regime, with the fetch injected. Always returns the dict shape."""
    try:
        df = fetch(ticker, REGIME_PERIOD, "1d")
        if df is not None and isinstance(getattr(df, "columns", None), pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return spy_regime_from_bars(df)
    except Exception as e:
        logger.warning("SPY regime unavailable (%s)", e)
        return {"regime": "Unknown", "price": None, "sma200": None,
                "adx": None, "reasoning": f"SPY fetch failed ({e})"}


# ---------------------------------------------------------------------------
# Self-test — synthetic bars, no network
# ---------------------------------------------------------------------------

def _weekly(closes):
    return pd.DataFrame({"Close": list(closes)})


def _spy(closes, hi_pad=1.0, lo_pad=1.0):
    c = pd.Series(list(closes), dtype="float64")
    return pd.DataFrame({"Close": c, "High": c + hi_pad, "Low": c - lo_pad})


def _choppy_above_sma200():
    """
    A genuinely LOW-ADX tape (ADX ~11) whose price still sits ~7% above its
    own 200-SMA: fast oscillation with a mild upward drift. This is the exact
    condition app.py's old rule mishandled — it required ADX >= 20 to commit to
    Bull/Bear and returned "Neutral" otherwise, which signal_core reads as "no
    view" and does not filter on. So in tape like this the app applied NO
    regime filter while the scanner still blocked counter-regime setups.

    An earlier version of this fixture was a random walk that actually measured
    ADX 26 — above the threshold, so it never exercised the gate at all and the
    guard below passed whether or not the gate existed. Verified: this one is
    genuinely under 20.
    """
    import numpy as np
    import pandas as pd
    t = np.arange(300)
    rng = np.random.default_rng(4)
    c = 400 + 15 * np.sin(2 * np.pi * t / 9) + 0.12 * t + rng.normal(0, 1.5, 300)
    return pd.DataFrame({"Close": c, "High": c + 1.2, "Low": c - 1.2})


def selftest() -> int:
    import numpy as np

    # ── weekly trend ──
    up = _weekly(np.linspace(50, 100, 60))
    down = _weekly(np.linspace(100, 50, 60))
    print(f"weekly, rising          : {weekly_trend_from_bars(up)}")
    print(f"weekly, falling         : {weekly_trend_from_bars(down)}")
    assert weekly_trend_from_bars(up) == "Bullish"
    assert weekly_trend_from_bars(down) == "Bearish"

    short = _weekly(np.linspace(50, 100, WEEKLY_MIN_BARS - 1))
    assert weekly_trend_from_bars(short) is None
    print(f"weekly, too few bars    : None (blocks when weekly_confirm is on)")
    assert weekly_trend_from_bars(None) is None
    assert weekly_trend_from_bars(pd.DataFrame()) is None
    print(f"weekly, no data         : None")

    # THE DIVERGENCE THIS MODULE EXISTS TO SETTLE, made concrete.
    # An ordinary 2-bar pullback inside an uptrend: price drops below the
    # 20-week EMA while the 10-week EMA is still above it. The two rules
    # return OPPOSITE verdicts on the same bars — and weekly alignment is a
    # BLOCKING filter, so before this module the app and the scanner could
    # reach opposite conclusions on the same ticker at the same moment.
    pullback = _weekly(list(np.linspace(50, 100, 55)) + [92.0, 84.0])
    close = pullback["Close"]
    e10 = float(close.ewm(span=10, adjust=False).mean().iloc[-1])
    e20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    price_w = float(close.iloc[-1])
    old_rule = "Bullish" if e10 > e20 else "Bearish"      # app.py's crossover
    got = weekly_trend_from_bars(pullback)                 # price vs EMA20w
    print(f"weekly, 2-bar pullback  : price {price_w:.0f}, EMA10w {e10:.1f}, "
          f"EMA20w {e20:.1f}")
    print(f"    price-vs-EMA20w rule : {got}   <- what this module now returns")
    print(f"    old crossover rule   : {old_rule}   <- what app.py used to return")
    assert old_rule != got, (
        "this case is supposed to demonstrate the two rules disagreeing; if "
        "they now agree the fixture stopped exercising the divergence")
    assert got == "Bearish"

    # ── SPY regime: the rule the 591-trade OOS test actually ran ──
    bull = spy_regime_from_bars(_spy(np.linspace(300, 500, 260)))
    bear = spy_regime_from_bars(_spy(np.linspace(500, 300, 260)))
    print(f"\nregime, price > 200SMA  : {bull['regime']} — {bull['reasoning']}")
    print(f"regime, price < 200SMA  : {bear['regime']} — {bear['reasoning']}")
    assert bull["regime"] == "Bull"
    assert bear["regime"] == "Bear"

    # REGRESSION: a flat/choppy tape has low ADX. app.py used to call that
    # "Neutral", which signal_core reads as "no view" and does NOT filter on.
    # The backtest had no such gate, so a low-ADX tape must still return a
    # directional verdict here or live diverges from what was validated.
    r = spy_regime_from_bars(_choppy_above_sma200())
    print(f"regime, LOW ADX ({r['adx']})   : {r['regime']} "
          f"<- app.py returned Neutral here and applied NO filter")
    assert r["adx"] is not None and r["adx"] < 20, (
        f"fixture must actually have ADX < 20 to exercise the old gate, "
        f"got {r['adx']}")
    assert r["regime"] == "Bull", "low ADX must not suppress the regime verdict"

    thin = spy_regime_from_bars(_spy(np.linspace(300, 500, 50)))
    assert thin["regime"] == "Unknown"
    print(f"regime, too few bars    : {thin['regime']} — {thin['reasoning']}")
    assert spy_regime_from_bars(None)["regime"] == "Unknown"
    print(f"regime, no data         : Unknown")

    # ── vocabulary must survive signal_core's normaliser ──
    import signal_core as sc
    assert sc._norm_trend(bull["regime"]) == "Bullish"
    assert sc._norm_trend(bear["regime"]) == "Bearish"
    assert sc._norm_trend("Unknown") is None
    print(f"\nvocabulary              : Bull->Bullish, Bear->Bearish, "
          f"Unknown->None (no filter)")

    # ── matches backtest.build_regime_series(), the validated implementation ──
    import backtest as bt
    for series in (np.linspace(300, 500, 260), np.linspace(500, 300, 260)):
        spy_df = pd.DataFrame({"Close": series})
        want = bt.build_regime_series(spy_df).iloc[-1]
        got = spy_regime_from_bars(_spy(series))["regime"]
        assert got == want, f"live regime {got!r} != backtest regime {want!r}"
    print(f"backtest parity         : live regime == "
          f"backtest.build_regime_series() on both directions")

    # ── injected fetch wrappers ──
    assert get_weekly_trend("X", lambda t, p, i: up) == "Bullish"
    assert get_weekly_trend("X", lambda t, p, i: None) is None
    def _boom(t, p, i): raise RuntimeError("rate limited")
    assert get_weekly_trend("X", _boom) is None
    assert get_spy_regime(_boom)["regime"] == "Unknown"
    print(f"fetch wrappers          : inject, degrade to None/Unknown on error")

    print("\nAll self-tests passed.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
