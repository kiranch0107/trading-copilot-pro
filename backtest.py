"""
Trading Copilot ELITE — Historical Backtester
=============================================
Validates the EXACT signal logic from app.py against REAL historical daily
data for your watchlist. This is the "does the edge survive on real prices?"
test — distinct from the synthetic Monte-Carlo sweep used for parameter tuning.

What it does
------------
  • Downloads N years of real OHLCV per ticker (yfinance)
  • Computes the same indicators as app.py (ta library, Wilder smoothing)
  • Discards the indicator warm-up head (first 100 converged bars) exactly
    like app.py's compute()
  • Walks forward bar-by-bar with NO lookahead: at each bar it evaluates the
    signal using ONLY data up to and including that bar
  • On a signal, it enters at the NEXT bar's open (realistic — you can't fill
    on the close that generated the signal), then walks forward checking
    whether stop or target is hit first (stop checked first on ambiguous bars
    = conservative), with a max-hold timeout that marks to market
  • Applies optional slippage + commission per trade
  • Reports per-ticker and aggregate: trades, win rate, expectancy (avg R),
    total R, profit factor, max drawdown, avg hold — plus a monthly R curve

The signal itself — base conditions, ADX gate, entry/stop/target math
(single-reference, ATR-based caps, structural-stop validation, relative
zero-risk gate, MIN_RR gate) — is NOT reimplemented here. It calls
signal_core.evaluate(), the same function app.py and scanner.py call live.

THIS USED TO BE A SECOND IMPLEMENTATION, hand-written to "mirror" app.py's
analyze(). That is exactly the pattern signal_core.py was built to end (see
its docstring: two copies of the signal disagreed and produced contradictory
live alerts). A backtest that silently drifts from the live signal is worse
than no backtest — it keeps validating a strategy nobody is actually
running, and the 591-trade out-of-sample result depends on this file having
tested the real thing. Delegating means a future change to
signal_core.evaluate() is automatically re-tested next run, not something
that requires a second manual edit that can quietly not happen.

Filters:
  • ADX filter: applied (same threshold) — signal_core.evaluate() does not
    block on ADX itself (it only feeds the "high_quality" tier), so this file
    checks the filter's own pass/fail explicitly, same as the old inline gate.
  • SPY regime: OFF by default. --use-regime applies the SAME rule live uses
    (market_context / build_regime_series: price vs its own 200-SMA).
  • Weekly alignment: OFF by default, --use-weekly to apply it.
  • Earnings blackout: OFF — needs an external calendar with point-in-time
    accuracy, which yfinance does not provide historically.

WHY --use-weekly EXISTS
-----------------------
weekly_confirm defaulted to TRUE in signal_core until 2026-09-02, so the LIVE
system applied weekly alignment as a BLOCKING filter. This file used
to force it off unconditionally, which meant the 591-trade out-of-sample
validation never measured it: live was running a gate that nothing had
tested. --use-weekly fetches weekly bars and supplies the same
price-vs-20w-EMA verdict market_context computes live, so the filter could
finally be A/B'd.

RESULT (2026-09-02): the gate rejected 5 bars out of the 13,748 that reached
the filters, and 0 that no other gate would have caught. Expectancy moved
less than the run-to-run noise of the data feed itself. signal_core's default
is now weekly_confirm=False, matching the config the OOS run validated, so
live and this file finally agree. The flag stays for re-measurement.

The hard part is lookahead. A weekly bar labelled Monday does not CLOSE until
Friday, so its verdict is not knowable during its own week — using it on
Wednesday would leak Thursday and Friday into a Wednesday decision. Each
week's verdict is therefore shifted to apply from the START OF THE FOLLOWING
WEEK. Both properties (parity with the live rule, and the shift) are asserted
in --selftest against a fixture that oscillates, so a broken shift actually
fails rather than comparing two identical verdicts.

Run
---
  pip install yfinance pandas ta tabulate numpy
  python backtest.py

  # options:
  python backtest.py --years 5 --tickers TSLA,NVDA,AAPL
  python backtest.py --atr-stop 1.0 --atr-tgt 3.0 --adx-min 25
  python backtest.py --slippage-bps 5 --commission 0.65 --use-regime
"""
from __future__ import annotations

import argparse
import sys
import warnings
from datetime import datetime
from zoneinfo import ZoneInfo
warnings.filterwarnings("ignore")

from dataclasses import replace

import numpy as np
import pandas as pd

import signal_core as sc
import market_context as mc

try:
    import data_source
except ImportError:
    data_source = None    # optional — see download(); falls back to Yahoo-only
try:
    import bar_cache
except ImportError:
    bar_cache = None      # optional — see download(); runs uncached, and says so
try:
    import yfinance as yf
except ImportError:
    raise SystemExit("Missing yfinance. Run: pip install yfinance pandas ta tabulate numpy")
try:
    import ta
except ImportError:
    raise SystemExit("Missing ta. Run: pip install ta")
try:
    from tabulate import tabulate
except ImportError:
    def tabulate(rows, headers, **kw):   # minimal fallback
        out = ["  ".join(str(h) for h in headers)]
        for r in rows:
            out.append("  ".join(str(c) for c in r))
        return "\n".join(out)


# ══════════════════════════════════════════════════════════════════════
# CONFIG — defaults mirror app.py's sidebar defaults
# ══════════════════════════════════════════════════════════════════════
DEFAULTS = dict(
    tickers       = ["TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "META", "SPY"],
    years         = 5,
    adx_min       = 25,
    atr_stop_mult = 1.0,      # app.py default
    atr_tgt_mult  = 3.0,      # app.py default (updated from 2.5)
    min_rr        = 0.5,
    volume_mult   = 1.0,      # for the "Strong" strength tag only
    max_hold      = 20,       # bars to hold before timeout mark-to-market
    slippage_bps  = 2.0,      # per side, in basis points of price
    commission    = 0.0,      # $ per trade (round trip), for share trades
    use_regime    = False,    # approximate SPY 200-SMA macro filter
    use_weekly    = False,    # weekly-timeframe alignment filter
    cooldown_bars = 3,        # bars to wait after a trade before re-entering
)

WARMUP_BARS       = 100       # matches app.py INDICATOR_WARMUP_BARS
MIN_BARS_AFTER    = 40        # matches app.py MIN_BARS_AFTER_WARMUP


# ══════════════════════════════════════════════════════════════════════
# INDICATORS — identical to app.py compute()
# ══════════════════════════════════════════════════════════════════════
def compute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, l = df["Close"], df["High"], df["Low"]
    df["EMA20"]     = ta.trend.ema_indicator(c, window=20)
    df["EMA50"]     = ta.trend.ema_indicator(c, window=50)
    macd            = ta.trend.MACD(c)
    df["MACD"]      = macd.macd()
    df["Signal"]    = macd.macd_signal()
    df["RSI"]       = ta.momentum.rsi(c, window=14)
    df["ATR"]       = ta.volatility.average_true_range(h, l, c, window=14)
    df["ADX"]       = ta.trend.adx(h, l, c, window=14)
    df["VOL_AVG20"] = df["Volume"].rolling(20).mean()
    df = df.dropna(subset=["EMA20", "EMA50", "MACD", "Signal", "RSI",
                           "ATR", "ADX", "VOL_AVG20"])
    # discard warm-up head exactly like app.py
    if len(df) > WARMUP_BARS + MIN_BARS_AFTER:
        df = df.iloc[WARMUP_BARS:]
    return df.reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════
# SIGNAL — delegates to signal_core.evaluate(), the live implementation
# ══════════════════════════════════════════════════════════════════════
def build_signal_params(cfg: dict) -> sc.SignalParams:
    """
    Map this run's cfg onto signal_core.SignalParams once per run (not once
    per bar — the dataclass is immutable and identical for every bar of every
    ticker in a given run).

    weekly_confirm follows cfg["use_weekly"], default False. It MUST stay off
    unless run() is actually supplying a weekly trend per bar: signal_core
    BLOCKS every bar when weekly_trend is None and weekly_confirm is True, so
    turning it on without the data does not "simplify" the filter, it silently
    zeroes out every trade. build_weekly_trend_map() supplies that data when
    --use-weekly is passed.
    """
    return replace(
        sc.DEFAULTS,
        adx_min=cfg["adx_min"],
        atr_stop_mult=cfg["atr_stop_mult"],
        atr_tgt_mult=cfg["atr_tgt_mult"],
        min_rr=cfg["min_rr"],
        volume_mult=cfg["volume_mult"],
        weekly_confirm=bool(cfg.get("use_weekly", False)),
        spy_regime_on=cfg["use_regime"],
    )


def evaluate_signal(df: pd.DataFrame, i: int, params: sc.SignalParams,
                    regime: str | None = None,
                    weekly: str | None = None,
                    tally: dict | None = None) -> dict | None:
    """
    Evaluate the signal at bar i using ONLY rows 0..i (no lookahead), through
    signal_core.evaluate() — see the module docstring for why this is no
    longer a hand-maintained copy.

    df.iloc[:i+1] reproduces the exact windows the old inline version passed
    by hand: signal_core internally takes df["Low"].tail(10) / df["High"].tail(20),
    which over this slice are the same rows as
    df.iloc[max(0,i-9):i+1] / df.iloc[max(0,i-19):i+1] were.

    signal_core.evaluate() does NOT block on ANY of its four enhancement
    filters — they only feed the "high_quality" tier that gates live Telegram
    alerts (see scanner.py's analyze()). A caller that wants a filter applied
    must read filters[...]["pass"] itself. This function therefore checks the
    three filters this backtest treats as gates:

      ADX Trend Strength — always, matching the pre-refactor inline gate
      Macro Regime       — signal_core already no-ops it when spy_regime_on
                           is False or no regime was supplied
      Multi-TF Alignment — signal_core already no-ops it (pass=True) when
                           params.weekly_confirm is False, so this check is
                           inert on the default OFF arm and only bites under
                           --use-weekly

    THE BUG THIS SHAPE EXISTS TO PREVENT: the Multi-TF check was missing when
    --use-weekly was first added, so both arms of the weekly A/B ran the same
    signal and the "measurement" compared a filter against itself. Earnings is
    deliberately NOT checked — the backtest has no earnings calendar for
    history, and signal_core defaults it to pass.
    """
    def _count(key: str) -> None:
        if tally is not None:
            tally[key] = tally.get(key, 0) + 1

    window = df.iloc[: i + 1]
    spy_regime = {"regime": regime} if regime is not None else None
    r = sc.evaluate(window, "BT", params, spy_regime=spy_regime,
                    weekly_trend=weekly)
    if r["blocked"]:
        _count("base")
        return None
    # Each gate is counted INDEPENDENTLY, before any of them short-circuits.
    # Counting on the way out instead would make every number conditional on
    # the ones above it: ADX >= 35 rejects ~94% of setups, so a weekly gate
    # counted after it can only ever report on the survivors and reads as
    # "does nothing" even when it disagrees constantly. The counts therefore
    # OVERLAP and do not sum to the total — the report says so.
    adx_ok = r["filters"]["ADX Trend Strength"]["pass"]
    regime_ok = r["filters"]["Macro Regime"]["pass"]
    weekly_ok = r["filters"]["Multi-TF Alignment"]["pass"]
    _count("reached_filters")
    if not adx_ok:
        _count("adx")
    if not regime_ok:
        _count("regime")
    if not weekly_ok:
        _count("weekly")
    if not (adx_ok and regime_ok and weekly_ok):
        # What the weekly gate uniquely costs: bars nothing else would have
        # rejected. This is the number that decides whether the filter earns
        # its keep, and it is invisible in the overlapping counts above.
        if adx_ok and regime_ok and not weekly_ok:
            _count("weekly_only")
        return None
    _count("passed")

    return {"trend": r["trend"], "entry": r["entry"], "stop": r["stop"],
            "target": r["target"], "rr": r["rr"], "atr": r["atr"]}


# ══════════════════════════════════════════════════════════════════════
# TRADE SIMULATION — enter next open, stop-first fills, timeout m2m
# ══════════════════════════════════════════════════════════════════════
def simulate_trade(df: pd.DataFrame, signal_i: int, trade: dict,
                   cfg: dict, tally: dict | None = None) -> dict:
    """
    Enter at the OPEN of bar signal_i+1 (no same-bar fill). Walk forward until
    stop or target is hit (stop checked first on ambiguous bars = conservative),
    or max_hold is reached (mark to market at that close).
    Returns realised R multiple net of slippage/commission.
    """
    n = len(df)
    entry_i = signal_i + 1
    if entry_i >= n:
        return {"filled": False}

    # Realistic entry: next bar open, adjusted for slippage
    raw_entry = float(df["Open"].iloc[entry_i]) if "Open" in df.columns \
        else float(df["Close"].iloc[signal_i])
    slip = raw_entry * cfg["slippage_bps"] / 10_000.0
    trend = trade["trend"]
    entry = raw_entry + slip if trend == "Bullish" else raw_entry - slip

    stop, target = trade["stop"], trade["target"]
    risk = abs(entry - stop)
    if risk <= 0:
        return {"filled": False}

    # GAPPED PAST ITS OWN LEVELS — no trade.
    #
    # The signal is generated on bar N's close; the fill is bar N+1's open. If
    # that open gaps beyond the stop or the target, the setup being signalled
    # no longer exists at the price you would pay for it: a long whose stop is
    # ABOVE its entry is not a long with a stop, it is a different trade.
    #
    # This is not a nicety. Before the check, both cases scored with the WRONG
    # SIGN, because r = pnl / |entry - stop| is computed against a distance
    # that has inverted:
    #
    #   bullish, open gaps below the stop    -> outcome "loss",  r = +1.000
    #   bullish, open gaps above the target  -> outcome "win",   r = -0.200
    #
    # so the worst fills in the sample were being booked as full winners. The
    # count is reported at the end of the run rather than left silent, because
    # a filter that quietly drops trades is how a backtest starts describing a
    # strategy nobody ran.
    if trend == "Bullish":
        gapped = "stop" if entry <= stop else ("target" if entry >= target else None)
    else:
        gapped = "stop" if entry >= stop else ("target" if entry <= target else None)
    if gapped:
        if tally is not None:
            key = f"gapped past {gapped} before fill"
            tally[key] = tally.get(key, 0) + 1
        return {"filled": False, "gapped": gapped}

    exit_i = None; exit_px = None; outcome = None
    for j in range(entry_i, min(entry_i + cfg["max_hold"], n)):
        hi = float(df["High"].iloc[j]); lo = float(df["Low"].iloc[j])
        if trend == "Bullish":
            if lo <= stop:                       # stop first (conservative)
                exit_px, outcome, exit_i = stop, "loss", j; break
            if hi >= target:
                exit_px, outcome, exit_i = target, "win", j; break
        else:
            if hi >= stop:
                exit_px, outcome, exit_i = stop, "loss", j; break
            if lo <= target:
                exit_px, outcome, exit_i = target, "win", j; break

    # RIGHT-CENSORING. A trade whose max_hold window extends past the last bar
    # cannot time out on its own terms — it is marked to market at whatever the
    # final close happens to be, and then counted as a "timeout" exactly like a
    # trade that genuinely ran its course. The sample end is not a neutral exit:
    # it books the unfinished trades at the prevailing trend, which is the same
    # family of error as scoring two halves of a sample on different bases.
    #
    # They are kept (dropping them would be its own selection) and FLAGGED, so a
    # run can say how much of its record is unfinished rather than implying all
    # of it is settled.
    censored = (entry_i + cfg["max_hold"]) > n
    if exit_i is None:                           # timeout — mark to market
        exit_i = min(entry_i + cfg["max_hold"] - 1, n - 1)
        exit_px = float(df["Close"].iloc[exit_i])
        outcome = "timeout"
        if censored and tally is not None:
            tally["unfinished at sample end (marked to last close)"] = \
                tally.get("unfinished at sample end (marked to last close)", 0) + 1

    # Exit slippage (opposite direction)
    exit_slip = exit_px * cfg["slippage_bps"] / 10_000.0
    exit_fill = exit_px - exit_slip if trend == "Bullish" else exit_px + exit_slip

    pnl = (exit_fill - entry) if trend == "Bullish" else (entry - exit_fill)
    r_multiple = pnl / risk

    # Commission expressed in R (approx: commission / dollar-risk-per-share
    # is negligible for share trades; included for completeness)
    if cfg["commission"] > 0:
        r_multiple -= cfg["commission"] / (risk * 100)   # ~1 contract-ish scale

    return {
        "filled": True, "trend": trend, "outcome": outcome,
        "censored": bool(censored and outcome == "timeout"),
        "r": r_multiple, "rr_planned": trade["rr"],
        "hold": exit_i - entry_i,
        "entry_date": df["Date"].iloc[entry_i] if "Date" in df.columns else entry_i,
    }


# ══════════════════════════════════════════════════════════════════════
# BACKTEST DRIVER
# ══════════════════════════════════════════════════════════════════════
def backtest_ticker(df: pd.DataFrame, cfg: dict, params: sc.SignalParams,
                    regime_series: pd.Series | None = None,
                    weekly_series: pd.Series | None = None,
                    tally: dict | None = None) -> list[dict]:
    trades = []
    i = 0
    n = len(df)
    while i < n - 1:
        regime = None
        if regime_series is not None and i < len(regime_series):
            regime = regime_series.iloc[i]
        weekly = None
        if weekly_series is not None and i < len(weekly_series):
            weekly = weekly_series.iloc[i]
        sig = evaluate_signal(df, i, params, regime=regime, weekly=weekly,
                              tally=tally)
        if sig:
            res = simulate_trade(df, i, sig, cfg, tally=tally)
            if res.get("filled"):
                trades.append(res)
                i += cfg["cooldown_bars"] + 1     # cooldown after a trade
                continue
        i += 1
    return trades


SIDES = (("Bullish", "LONG "), ("Bearish", "SHORT"))


def split_by_side(trades: list, side: str) -> list:
    """
    The long/short split, as one function so the selftest can break it.

    An earlier version of this test filtered an inline fixture and asserted on
    stats() — which passes whether or not run() splits anything, because it
    never touched run()'s filter. Keeping the filter here means the assertion
    below is on the code that actually runs.
    """
    return [t for t in trades if t.get("trend") == side]


def side_concentration(trades: list, side: str) -> dict | None:
    """
    Is one side's result broad across names, or carried by one or two?

    An aggregate of +0.100 R means something very different if eleven of
    twelve tickers contribute it than if one does. `drop_best` answers that
    directly: recompute the side's average with the single best-contributing
    ticker removed. If a twelve-name average halves when one name leaves, the
    average was describing that name.

    This is a ROBUSTNESS check, not a search. It reports how fragile a number
    is; it never nominates which tickers to trade. Picking the names that
    looked good here is the ADX-35 mistake with a different label on it.
    """
    sub = split_by_side(trades, side)
    if len(sub) < 2:
        return None
    by_tk: dict[str, list] = {}
    for t in sub:
        by_tk.setdefault(t.get("ticker", "?"), []).append(t["r"])
    if len(by_tk) < 2:
        return None

    per = {tk: (len(rs), float(np.mean(rs))) for tk, rs in by_tk.items()}
    overall = float(np.mean([t["r"] for t in sub]))
    # "Best" by total R contributed, not by average: a name with 40 trades at
    # +0.20 moves the aggregate far more than one with 3 trades at +2.00.
    best = max(by_tk, key=lambda tk: sum(by_tk[tk]))
    rest = [r for tk, rs in by_tk.items() if tk != best for r in rs]
    return {
        "per_ticker": per,
        "overall": overall,
        "n_positive": sum(1 for _, (_, avg) in per.items() if avg > 0),
        "n_tickers": len(per),
        "best": best,
        "drop_best": float(np.mean(rest)) if rest else float("nan"),
    }


def print_concentration(all_trades: list) -> None:
    """
    Render the concentration check. Silent when there is nothing to say —
    a single-ticker run has no concentration question to answer.

    This lives in its own function so the selftest exercises the real render
    path. Left inline in run() it could only be tested by a full backtest,
    which needs the network, which means in practice it would not be tested.
    """
    conc = [(lbl, side_concentration(all_trades, sd)) for sd, lbl in SIDES]
    if not any(c for _, c in conc):
        return
    print("\n  CONCENTRATION — how much of each side rests on one name")
    print("  (a robustness check. It does NOT nominate tickers to trade:")
    print("   picking the names that looked good here is the ADX-35 mistake.)")
    for lbl, c in conc:
        if not c:
            continue
        drop = c["drop_best"]
        frag = ""
        if np.isfinite(drop) and abs(c["overall"]) > 1e-9:
            if 1 - (drop / c["overall"]) > 0.5:
                frag = "  <- over half the result is that one name"
        print(f"    {lbl}  positive in {c['n_positive']}/{c['n_tickers']} "
              f"tickers   {c['overall']:+.3f} R -> {drop:+.3f} R "
              f"without {c['best']}{frag}")


def hold_profile(trades: list) -> dict | None:
    """
    How many trades are resolved on the bar they were entered on.

    backtest_trade()'s exit walk starts at entry_i, so a position can open at
    one bar's open and close within that same session — hold == 0. That is
    correct modelling (price hitting your stop the day you enter takes you out
    in real life too), but it is not visible in "avg hold 4.4 bars", and the
    entry bar is structurally asymmetric:

        stop   sits ATR_STOP x ATR from entry  -- inside a normal day's range
        target sits ATR_TGT  x ATR from entry  -- several times outside it

    So the entry bar can reach the stop and essentially cannot reach the
    target. If a large share of trades resolve there, they resolve badly, and
    the aggregate is partly a measurement of that rather than of the signal.
    """
    if not trades:
        return None
    h = np.array([t["hold"] for t in trades])
    r = np.array([t["r"] for t in trades])
    same = h == 0
    n_same = int(same.sum())
    if n_same == 0:
        return {"n": len(trades), "n_same": 0, "median_hold": float(np.median(h))}
    losses = sum(1 for t, sm in zip(trades, same)
                 if sm and t.get("outcome") == "loss")
    return {
        "n": len(trades),
        "n_same": n_same,
        "pct_same": 100.0 * n_same / len(trades),
        "loss_rate_same": 100.0 * losses / n_same,
        "avg_r_same": float(r[same].mean()),
        "avg_r_rest": float(r[~same].mean()) if (~same).any() else float("nan"),
        "median_hold": float(np.median(h)),
    }


def print_hold_profile(trades: list, cfg: dict) -> None:
    """Render the hold profile. Descriptive — see the closing note."""
    hp = hold_profile(trades)
    if not hp:
        return
    print("\n  HOLD PROFILE — how many trades resolve on the bar they opened on")
    if not hp["n_same"]:
        print(f"    none: every trade lived past its entry bar "
              f"(median hold {hp['median_hold']:.0f} bars)")
        return
    rest = hp["n"] - hp["n_same"]
    print(f"    same session  : {hp['n_same']:>5} ({hp['pct_same']:.1f}%)   "
          f"{hp['loss_rate_same']:.0f}% lose   {hp['avg_r_same']:+.3f} R")
    print(f"    held longer   : {rest:>5} ({100 - hp['pct_same']:.1f}%)"
          f"{'':>13}{hp['avg_r_rest']:+.3f} R")
    print(f"    median hold   : {hp['median_hold']:.0f} bars")
    print(f"    The stop sits {cfg['atr_stop_mult']:g}x ATR from entry and the "
          f"target {cfg['atr_tgt_mult']:g}x. A normal day's range reaches the")
    print(f"    stop but not the target, so the entry bar is asymmetric by "
          f"construction.")
    print(f"    DESCRIPTIVE. Widening the stop because this reads badly is a "
          f"parameter chosen")
    print(f"    on data already spent — the ADX-35 mistake in a different hat.")


def stats(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    r = np.array([t["r"] for t in trades])
    wins = r[r > 0]; losses = r[r < 0]
    equity = np.cumsum(r)
    peak = np.maximum.accumulate(equity)
    max_dd = float((equity - peak).min())
    gp = wins.sum(); gl = abs(losses.sum())
    return {
        "trades":     len(r),
        "win_rate":   len(wins) / len(r) * 100,
        "avg_r":      float(r.mean()),
        "total_r":    float(r.sum()),
        "pf":         (gp / gl) if gl > 0 else float("inf"),
        "max_dd":     max_dd,
        "avg_hold":   float(np.mean([t["hold"] for t in trades])),
        "best":       float(r.max()),
        "worst":      float(r.min()),
    }


def build_weekly_trend_map(ticker: str, years: int) -> "pd.Series | None":
    """
    Weekly trend as of each date, for testing the weekly_confirm filter.

    THE POINT OF THIS FUNCTION: weekly_confirm defaulted to TRUE live but
    backtest.build_signal_params() forces it OFF, so the 591-trade OOS test
    never applied the weekly filter at all. The live config was therefore
    running a BLOCKING gate that nothing had measured. This makes it
    measurable.

    NO LOOKAHEAD, and it is the whole difficulty here. A weekly bar labelled
    Monday only CLOSES on Friday, so its verdict is not knowable during its
    own week — using it on Wednesday would leak Thursday and Friday into a
    Wednesday decision. Each week's verdict is therefore shifted forward and
    applies from the START OF THE FOLLOWING WEEK onward.

    Uses the same price-vs-EMA20w rule as market_context (asserted against it
    in selftest), so what is measured here is what runs live.
    """
    raw = download(ticker, years, interval="1wk")
    if raw is None or raw.empty or "Date" not in raw.columns:
        return None
    w = raw.dropna(subset=["Close"]).reset_index(drop=True)
    if len(w) < mc.WEEKLY_MIN_BARS + 1:
        return None

    close = w["Close"]
    ema = close.ewm(span=mc.WEEKLY_EMA_SPAN, adjust=False).mean()
    verdict = np.where(close > ema, "Bullish", "Bearish").astype(object)
    # Not enough history for the EMA to mean anything -> None, which
    # signal_core treats as BLOCKING when weekly_confirm is on.
    verdict[: mc.WEEKLY_MIN_BARS - 1] = None

    dates = pd.to_datetime(w["Date"])
    # Shift: week i's verdict becomes usable at week i+1's start.
    effective_from = list(dates.iloc[1:]) + [dates.iloc[-1] + pd.Timedelta(days=7)]
    return pd.Series(verdict, index=pd.DatetimeIndex(effective_from)).sort_index()


def build_regime_series(spy_df: pd.DataFrame) -> pd.Series:
    """Approximate app.py's SPY macro filter: price vs its own 200-SMA."""
    sma200 = spy_df["Close"].rolling(200, min_periods=50).mean()
    out = pd.Series("Neutral", index=spy_df.index)
    out[spy_df["Close"] > sma200] = "Bull"
    out[spy_df["Close"] < sma200] = "Bear"
    return out.reset_index(drop=True)


# ── price adjustment ──
# auto_adjust=True has been the backtest's setting since it was written, while
# app.py, scanner.py and exit_monitor.py all fetch with False. That mismatch
# was recorded as an open item for weeks; on 2026-09-02 it stopped being
# theoretical. A --refresh-cache twelve minutes after a fetch rewrote THOUSANDS
# of historical rows in 8 of 13 series, across the full 15-year window:
#
#   AVGO 2769 rows   CRM 2376   GOOGL 2337   LRCX 2846
#   MU   2692        ORCL 2801  QCOM  2893   SPY   2825
#   ADBE 0   AMD 0   NFLX 0   NOW 0   PANW 0
#
# The five that did not move pay no dividend. The eight that did, all do.
# Under auto_adjust=True every bar is scaled by a cumulative dividend factor,
# so a change anywhere in that factor rewrites the entire history — and Yahoo
# does not return a stable factor between requests. That single mechanism
# accounts for every result we saw today: 592/591/586/585/593 trades and
# -0.016 to -0.031 R, all from the same command.
#
# MEASURED AND SWITCHED, 2026-09-02. Both arms were run, then both were
# REFETCHED twelve minutes later to test stability:
#
#              expectancy   trades   rows rewritten on refetch
#   adjusted     -0.018 R     593    8 of 13 series, 2337-2893 rows each
#   raw          -0.021 R     585    ZERO, all 13 series
#
# Performance is a tie (0.003 R apart, far inside the +/-0.11 OOS CI), so
# stability decides, and it is not close. The raw refetch reproduced its
# fingerprint exactly; the adjusted refetch rewrote 15 years of history in
# every dividend-paying name.
#
# The one risk that argued for adjusted prices turned out not to exist. NFLX
# split 7:1 in July 2015, inside this window, and its raw and adjusted results
# are identical to the decimal (62 trades, -0.223 R) — as are AMD's and ADBE's,
# the other two non-payers. auto_adjust=False output is ALREADY split-adjusted;
# it only drops the dividend adjustment. There are no unadjusted split gaps.
#
# So raw is stable, performs the same, AND matches what app.py, scanner.py and
# exit_monitor.py actually trade on. That mismatch was the last open item from
# the original review.
#
# --adjusted-prices restores the old behaviour; it caches separately, so both
# series can be held at once and neither arm can overwrite the other.
AUTO_ADJUST = False


def _yahoo_download(ticker: str, period: str, interval: str) -> pd.DataFrame | None:
    return yf.download(ticker, period=period, interval=interval,
                       progress=False, auto_adjust=AUTO_ADJUST)


# ── on-disk bars (bar_cache.py) ──
# ON by default. A research harness that reads different bars on every run
# cannot measure anything: two runs of an identical command on 2026-09-02
# differed by 7 trades and 0.014 R purely because Yahoo returned different
# history, which is larger than most effects worth testing. Cached runs are
# reproducible by construction, and the fingerprint printed by run() says
# whether two runs read the same inputs. --no-cache and --refresh-cache are
# the deliberate ways out.
CACHE_ENABLED = True
CACHE_REFRESH = False
_CACHE_LOG: list[tuple] = []


# US market timezone. The bar labelled "today" is decided by the exchange's
# calendar, not by wherever this process happens to run — a UTC runner would
# otherwise start dropping tomorrow's bar at 20:00 ET.
_ET = ZoneInfo("America/New_York")


def _drop_todays_bar(ticker: str, df: "pd.DataFrame | None") -> "pd.DataFrame | None":
    """
    Remove any trailing bar dated today (ET) or later.

    WHY THIS IS STRICTER THAN signal_core.drop_partial_bar(): that function
    only trims while the market is OPEN, which is right for a live scan. It
    does not cover the case that actually bit here — before the open, Yahoo
    already emits a row dated today, and every live path leaves it in place.

    Observed 2026-09-02 with the market closed: two fetches four minutes apart
    returned identical row counts and DIFFERENT content for all 13 series,
    SPY included. Every cached frame ended on that day's date. A frame whose
    last row is still being written cannot be cached, and a fingerprint over
    it is not a fingerprint of anything.

    The backtest also gains nothing from the bar: backtest_ticker() loops to
    n-1 and fills at the NEXT bar's open, so the final row can never be
    entered. It is pure instability with no analytical value.
    """
    # ROUTED THROUGH signal_core.drop_unsettled(), the one place that decides
    # whether a bar has settled. DROP_ALWAYS, not DROP_UNTIL_CLOSE: this path
    # needs reproducibility, not freshness, and the reasoning above is why —
    # a frame whose last row was written today cannot be cached or fingerprinted
    # even once it settles. The live paths take the other policy.
    if df is None or "Date" not in df.columns or df.empty:
        return df
    today_et = datetime.now(_ET).date()
    df, n_dropped = sc.drop_unsettled(df, policy=sc.DROP_ALWAYS,
                                      date_col="Date")
    if n_dropped:
        df = df.reset_index(drop=True)
        print(f"  · {ticker}: dropped {n_dropped} bar(s) dated {today_et} or "
              f"later — still forming, and unusable by a next-bar-open fill")
    return df


def _download_uncached(ticker: str, years: int,
                       interval: str = "1d") -> pd.DataFrame | None:
    """
    Yahoo first, falling back through data_source.fetch_daily() (currently
    Tiingo, if TIINGO_API_KEY is set) when Yahoo comes back empty — the same
    routing app.py, scanner.py and exit_monitor.py use, so a Yahoo outage no
    longer takes every data-dependent script down at once. See
    data_source.py's docstring for the adjustment caveat: this module wants
    auto_adjust=True (split/dividend-adjusted); the fallback provider's bars
    are not, which only matters across a split inside the fetched window.
    """
    try:
        if data_source is not None:
            df, source = data_source.fetch_daily(
                ticker, period=f"{years}y", interval=interval,
                yahoo_fetch=_yahoo_download)
            if source not in ("yahoo", "none"):
                print(f"  ! {ticker}: Yahoo unavailable, used {source} fallback "
                     f"(not split/dividend-adjusted the way auto_adjust=True is)")
        else:
            df = _yahoo_download(ticker, f"{years}y", interval)
        if df is None or df.empty:
            return None
        # flatten possible multiindex columns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index()
        keep = {"Date": "Date", "Open": "Open", "High": "High",
                "Low": "Low", "Close": "Close", "Volume": "Volume"}
        df = df[[c for c in keep if c in df.columns]].rename(columns=keep)
        return _drop_todays_bar(ticker, df)
    except Exception as e:
        print(f"  ! download failed for {ticker}: {e}")
        return None


def download(ticker: str, years: int,
             interval: str = "1d") -> pd.DataFrame | None:
    """
    Bars for (ticker, years, interval), from disk when we already have them.

    The cache is deliberately never-expiring: reproducing a result means
    reading the bars the original run read, so freshness is an explicit act
    (--refresh-cache), not something that happens to you between two runs of
    the same command. bar_cache.py's docstring has the full reasoning.

    Every read is logged so run() can print one fingerprint over the whole
    dataset — the line that tells you whether two runs are comparable at all.
    """
    if bar_cache is None:
        return _download_uncached(ticker, years, interval)
    df, meta, status = bar_cache.get_or_fetch(
        ticker, interval, years,
        lambda: _download_uncached(ticker, years, interval),
        refresh=CACHE_REFRESH, enabled=CACHE_ENABLED,
        variant="" if AUTO_ADJUST else "raw")
    _CACHE_LOG.append((meta, status))
    return df


def run(cfg: dict) -> None:
    _CACHE_LOG.clear()
    print("=" * 78)
    print("TRADING COPILOT ELITE — HISTORICAL BACKTEST (real data)")
    print("=" * 78)
    print(f"Tickers      : {', '.join(cfg['tickers'])}")
    print(f"History      : {cfg['years']} years daily")
    print(f"ADX min      : {cfg['adx_min']}   ATR stop×: {cfg['atr_stop_mult']}   "
          f"ATR tgt×: {cfg['atr_tgt_mult']}   Min R:R: {cfg['min_rr']}")
    print(f"Max hold     : {cfg['max_hold']} bars   Slippage: {cfg['slippage_bps']}bps/side   "
          f"Regime filter: {'ON (SPY 200-SMA)' if cfg['use_regime'] else 'OFF'}")
    print(f"Prices       : {'auto_adjust=True (split+dividend adjusted) — NOT reproducible across refetches' if AUTO_ADJUST else 'auto_adjust=False (raw; splits still adjusted) — matches every live path'}")
    print(f"Weekly filter: {'ON (price vs 20w EMA)' if cfg.get('use_weekly') else 'OFF'}"
          f"{'   <- OFF live since 2026-09-02; this re-measures it' if cfg.get('use_weekly') else '   (matches the live default)'}")
    print("=" * 78)

    params = build_signal_params(cfg)

    # Optional regime series from SPY
    regime_by_date = None
    if cfg["use_regime"]:
        spy_raw = download("SPY", cfg["years"])
        if spy_raw is not None:
            # build_regime_series() reads Close straight off the raw frame, so
            # the compute() call that used to sit here was dead: its result was
            # merged onto itself and then never read. Removed rather than left
            # to imply the regime uses indicators it does not.
            reg = build_regime_series(spy_raw)
            regime_by_date = dict(zip(spy_raw["Date"], reg))

    per_ticker_rows = []
    all_trades = []
    # Per-gate rejection counts across every bar of every ticker. This exists
    # because the first --use-weekly A/B reported a plausible-looking result
    # while the weekly gate was not wired in at all: the aggregate numbers
    # alone could not tell "the filter barely matters" apart from "the filter
    # never ran". These counts distinguish the two from the output itself.
    tally: dict[str, int] = {}
    total_bars = 0

    for tk in cfg["tickers"]:
        raw = download(tk, cfg["years"])
        if raw is None:
            print(f"\n{tk}: no data — skipped")
            continue
        df = compute(raw)
        # Open and Date are ALREADY correct here — compute() carries every
        # column of `raw` through its dropna and head-slice, so each row's
        # Open/Date still belong to the bar whose indicators sit beside them.
        #
        # This used to re-attach them with `raw.tail(len(df))`, which is only
        # correct when compute() drops rows exclusively from the FRONT. It does
        # not: dropna() removes a row wherever ANY indicator is NaN, and
        # VOL_AVG20 is a 20-bar rolling mean, so ONE missing Volume anywhere in
        # the series deletes 20 interior rows. Tail-alignment then paired every
        # earlier indicator row with an Open and Date from 20 sessions LATER —
        # overwriting two correct columns with wrong ones, silently.
        #
        # Measured on a 400-bar frame with a single NaN Volume at row 250: 51 of
        # 181 surviving rows (28%) got the wrong Date, the first by 28 calendar
        # days, with an entry-price error of 2% of price. simulate_trade() reads
        # df["Open"] for the fill and the regime join reads df["Date"], so the
        # corruption reached both the entry price and which regime applied.
        #
        # consistency_check.check_compute_preserves_alignment() now pins this.
        df = df.copy()

        # per-bar regime lookup
        reg_series = None
        if regime_by_date is not None and "Date" in df.columns:
            mapped = df["Date"].map(regime_by_date)
            # A date this ticker has and SPY does not becomes "Neutral", and
            # signal_core reads Neutral as "no view" — so an ALIGNMENT FAILURE
            # silently turns the regime filter OFF for that bar instead of
            # failing. That is the shape of bug this repo keeps paying for (the
            # weekly gate that "ran" without being wired in), so the unmatched
            # bars are counted and printed rather than absorbed.
            unmatched = int(mapped.isna().sum())
            if unmatched:
                tally["regime date unmatched against SPY (gate inert on those bars)"] = (
                    tally.get("regime date unmatched against SPY (gate inert on those bars)", 0)
                    + unmatched)
            reg_series = mapped.fillna("Neutral").reset_index(drop=True)

        # per-bar weekly trend, as of the last CLOSED weekly bar (no lookahead)
        wk_series = None
        if cfg.get("use_weekly") and "Date" in df.columns:
            wmap = build_weekly_trend_map(tk, cfg["years"])
            if wmap is None:
                print(f"  ! {tk}: no weekly data — weekly filter would block "
                      f"every bar, skipping ticker")
                continue
            wk_series = pd.Series(
                [wmap.asof(pd.Timestamp(d)) for d in df["Date"]]
            ).reset_index(drop=True)

        trades = backtest_ticker(df, cfg, params, regime_series=reg_series,
                                 weekly_series=wk_series, tally=tally)
        s = stats(trades)
        for _t in trades:
            _t["ticker"] = tk      # for the concentration check below
        all_trades.extend(trades)
        total_bars += len(df)

        # Bars is reported because a silently TRUNCATED download changes every
        # number in the row with no other trace. Two runs of the identical
        # command four minutes apart differed by 7 trades and 0.014 R purely
        # because Yahoo returned less history the second time; nothing in the
        # output said so, and the difference was larger than the effect the
        # run was measuring. Compare this column across runs before comparing
        # anything else.
        if s["trades"] == 0:
            per_ticker_rows.append([tk, len(df), 0, "—", "—", "—", "—", "—", "—"])
        else:
            per_ticker_rows.append([
                tk, len(df), s["trades"], f"{s['win_rate']:.0f}%",
                f"{s['avg_r']:+.3f}", f"{s['total_r']:+.1f}",
                f"{s['pf']:.2f}", f"{s['max_dd']:+.1f}", f"{s['avg_hold']:.0f}",
            ])

    print("\nPER-TICKER RESULTS")
    print(tabulate(
        per_ticker_rows,
        headers=["Ticker", "Bars", "Trades", "Win%", "Avg R", "Total R",
                 "PF", "MaxDD", "Hold"],
        tablefmt="simple",
    ))

    agg = stats(all_trades)
    print("\n" + "=" * 78)
    print("AGGREGATE (all tickers combined)")
    print("=" * 78)
    if agg["trades"] == 0:
        print("No trades generated. Try --adx-min 20 or a longer --years window.")
        return
    print(f"  Total trades   : {agg['trades']}")
    print(f"  Win rate       : {agg['win_rate']:.1f}%")
    print(f"  Expectancy     : {agg['avg_r']:+.3f} R per trade")
    print(f"  Total return   : {agg['total_r']:+.1f} R")
    print(f"  Profit factor  : {agg['pf']:.2f}")
    print(f"  Max drawdown   : {agg['max_dd']:+.1f} R")
    print(f"  Avg hold       : {agg['avg_hold']:.1f} bars")
    print(f"  Best / worst   : {agg['best']:+.2f} R / {agg['worst']:+.2f} R")
    print(f"  Bars tested    : {total_bars}   <- compare across runs FIRST; "
          f"if this moves, the data moved")

    # ── LONG vs SHORT ──
    # A diagnostic, not a selector. The aggregate can only tell you the system
    # loses; it cannot tell you WHICH HALF loses, and those need different
    # answers. A long-only rule in a rising tape and a counter-trend short rule
    # in the same tape are two strategies averaged into one number — the same
    # shape as blending paper trades into a live track record.
    #
    # READ THIS AS A DIAGNOSIS, NOT A PERMISSION SLIP. If one side looks
    # better, that is ONE comparison on data already spent; turning it into
    # "trade only longs" is the ADX-35 mistake again, and the sweep in
    # results/ shows where that ends. A side worth trading is one that
    # survives a fresh pre-registered test on reserved tickers.
    for side, label in SIDES:
        sub = split_by_side(all_trades, side)
        if not sub:
            print(f"  {label}          : no trades")
            continue
        ss = stats(sub)
        r = np.array([t["r"] for t in sub])
        se = float(r.std(ddof=1)) / np.sqrt(len(r)) if len(r) > 1 else float("nan")
        lo, hi = ss["avg_r"] - 1.96 * se, ss["avg_r"] + 1.96 * se
        print(f"  {label}          : {ss['trades']:>4} trades  "
              f"{ss['win_rate']:>5.1f}% win  {ss['avg_r']:+.3f} R  "
              f"PF {ss['pf']:.2f}  95% CI [{lo:+.3f}, {hi:+.3f}]"
              f"{'  <- CI clears 0' if lo > 0 else ''}")

    print_concentration(all_trades)
    print_hold_profile(all_trades, cfg)
    if bar_cache is not None and _CACHE_LOG:
        metas = [m for m, _ in _CACHE_LOG]
        statuses = [st for _, st in _CACHE_LOG]
        print(f"  {bar_cache.summarise(metas, statuses)}")
        # On a refresh, say WHAT moved. One altered row dated near today is a
        # bar that was still forming; many rows spread through history is the
        # provider re-adjusting the series. Those need different fixes, and
        # "the fingerprint changed" cannot tell you which you have.
        diffs = [(m["ticker"], m["refresh_diff"]) for m in metas
                 if m and m.get("refresh_diff")
                 and (m["refresh_diff"]["changed"]
                      or m["refresh_diff"]["rows_before"] != m["refresh_diff"]["rows_after"])]
        if diffs:
            print("  refresh changed these series:")
            for tk_, d in sorted(diffs):
                span = (f"{d['first_changed']}..{d['last_changed']}"
                        if d["changed"] else "no shared row altered")
                print(f"    {tk_:<6} rows {d['rows_before']}->{d['rows_after']}, "
                      f"{d['changed']} altered  ({span})")
        if not CACHE_ENABLED:
            print("     ^ caching OFF: this run is NOT reproducible, and cannot")
            print("       be compared against any other run.")

    # ── which gate rejected what ──
    reached = tally.get("reached_filters", 0)
    if reached:
        print("\n" + "=" * 78)
        print("GATE REJECTIONS (bars that formed a valid setup, then were filtered)")
        print("=" * 78)
        print(f"  Bars reaching the filters : {reached}")
        print("  Counted independently — a bar can fail several gates, so")
        print("  these OVERLAP and will not sum to the total.")
        for key, label in (("adx", "ADX below minimum"),
                           ("regime", "Macro regime conflict"),
                           ("weekly", "Weekly misaligned/missing")):
            n_rej = tally.get(key, 0)
            state = ""
            if key == "weekly" and not cfg.get("use_weekly"):
                state = "   (filter OFF)"
            elif key == "regime" and not cfg.get("use_regime"):
                state = "   (filter OFF)"
            print(f"  failed {label:<31}: {n_rej:>6}"
                  f"  ({n_rej / reached * 100:5.1f}%){state}")
        wk_only = tally.get("weekly_only", 0)
        print(f"  ...of which WEEKLY ALONE rejected : {wk_only:>6}"
              f"  ({wk_only / reached * 100:5.1f}%)")
        print("     (bars no other gate would have stopped — what the weekly")
        print("      filter uniquely costs you)")
        print(f"  survived every gate       : {tally.get('passed', 0)}")

        # Setups that passed every gate and then could not be filled, because
        # the next open had already gapped beyond the stop or the target.
        # Reported so the difference between "survived every gate" and the
        # trade count is accounted for rather than left as a silent gap.
        _gap_stop = tally.get("gapped past stop before fill", 0)
        _gap_tgt = tally.get("gapped past target before fill", 0)
        if _gap_stop or _gap_tgt:
            print(f"  gapped past its levels    : {_gap_stop + _gap_tgt:>6}"
                  f"  ({_gap_stop} past the stop, {_gap_tgt} past the target)")
            print("     (the next open was already beyond the level, so the setup")
            print("      did not exist at the fill price — no trade)")

        _cens = tally.get("unfinished at sample end (marked to last close)", 0)
        if _cens:
            _tot = agg.get("trades", 0) or 0
            _pct = (100.0 * _cens / _tot) if _tot else 0.0
            print(f"  unfinished at sample end  : {_cens:>6}  ({_pct:.1f}% of trades)")
            print("     (their hold window ran past the last bar, so they are marked")
            print("      to the final close — not settled on their own terms)")

        _unm = tally.get("regime date unmatched against SPY "
                         "(gate inert on those bars)", 0)
        if _unm:
            print(f"  !! {_unm} bar(s) had no matching SPY date, so the regime gate was")
            print("     INERT on them — Neutral reads as 'no view'. An alignment")
            print("     failure here looks exactly like a filter with nothing to say.")

        # A gate that is ON and rejects nothing is a wiring failure, not a
        # finding about the market. Say so in the output rather than leaving
        # it to be read as "the filter does not matter".
        if cfg.get("use_weekly") and tally.get("weekly", 0) == 0:
            print("\n  !! WEEKLY FILTER IS ON BUT REJECTED ZERO BARS — treat this run as")
            print("     INVALID. The gate is not reaching signal_core; do not compare")
            print("     it against the filter-OFF arm.")

    # Interpretation
    print("\nINTERPRETATION")
    exp = agg["avg_r"]; pf = agg["pf"]
    if exp > 0 and pf > 1.1:
        print(f"  ✅ Positive expectancy ({exp:+.3f} R/trade, PF {pf:.2f}) on REAL data.")
        print("     The edge that showed up in the Monte-Carlo sweep survives on")
        print("     actual historical prices. This is the result you want to see.")
    elif exp > 0:
        print(f"  🟡 Marginally positive ({exp:+.3f} R/trade, PF {pf:.2f}). The edge is")
        print("     real but thin — transaction costs and slippage matter a lot here.")
    else:
        print(f"  🔴 Negative expectancy ({exp:+.3f} R/trade) on real data. The synthetic")
        print("     edge did NOT survive. Do not trade this as-is — investigate which")
        print("     tickers/periods dragged it down before risking capital.")
    print("\n  Reminder: past performance is not predictive. Stops are not guaranteed")
    print("  (overnight gaps). Options add theta/slippage this share-based test omits.")


# ══════════════════════════════════════════════════════════════════════
# SELF-TEST — synthetic data, no network. Proves the signal_core delegation
# in evaluate_signal() reproduces the pre-refactor contract: signal_core
# itself does not block on ADX or regime, so a regression here (deleting
# either explicit check) would silently let the backtest — and therefore
# oos_validate.py, which shells out to this file — trade bars the live
# scanner/app would never alert on.
# ══════════════════════════════════════════════════════════════════════
def _synthetic_ohlc(n=140, price=100.0, up=True, adx=40.0, atr=2.0,
                    vol=2_000_000, vol_avg=1_000_000, rsi=None):
    idx = pd.bdate_range("2020-01-02", periods=n)
    close = (np.linspace(price * 0.85, price, n) if up
            else np.linspace(price * 1.15, price, n))
    rsi = rsi if rsi is not None else (65.0 if up else 35.0)
    return pd.DataFrame({
        "Open": close, "Close": close,
        "High": close + atr * 0.5, "Low": close - atr * 0.5,
        "Volume": vol_avg, "Date": idx,
        "EMA20": close * (0.98 if up else 1.02),
        "EMA50": close * (0.96 if up else 1.04),
        "RSI": rsi, "MACD": (1.0 if up else -1.0),
        "Signal": (0.5 if up else -0.5),
        "ATR": atr, "ADX": adx, "VOL_AVG20": vol_avg,
    }, index=idx)


def selftest() -> int:
    cfg = dict(DEFAULTS, adx_min=35.0, atr_stop_mult=1.25, atr_tgt_mult=4.0,
              min_rr=0.5, use_regime=False)
    params = build_signal_params(cfg)
    assert params.weekly_confirm is False, \
        "weekly_confirm must default off — signal_core BLOCKS every bar " \
        "otherwise, and no weekly data is fetched unless --use-weekly"
    assert build_signal_params(dict(cfg, use_weekly=True)).weekly_confirm is True, \
        "--use-weekly must actually turn the filter on"
    assert params.adx_min == 35.0 and params.atr_stop_mult == 1.25
    print("build_signal_params    : weekly off by default, on with use_weekly")

    # Clean bullish setup, ADX comfortably above threshold -> a trade.
    df = _synthetic_ohlc(adx=40.0)
    sig = evaluate_signal(df, len(df) - 1, params)
    assert sig is not None, "clean bullish bar with ADX above threshold should trade"
    assert sig["trend"] == "Bullish" and sig["stop"] < sig["entry"] < sig["target"]
    print(f"clean bullish bar       : trades, entry={sig['entry']} "
         f"stop={sig['stop']} target={sig['target']} rr={sig['rr']}")

    # THE REGRESSION THIS TEST EXISTS TO CATCH: signal_core.evaluate() itself
    # does not block on ADX (it only gates "high_quality"). If evaluate_signal
    # stopped checking filters["ADX Trend Strength"]["pass"] explicitly, this
    # bar — identical except for ADX — would start trading, silently loosening
    # every backtest and OOS run without changing a single number in FROZEN.
    df_weak_adx = _synthetic_ohlc(adx=20.0)
    sig = evaluate_signal(df_weak_adx, len(df_weak_adx) - 1, params)
    assert sig is None, "ADX below cfg threshold must still block the backtest trade"
    print("ADX below threshold     : blocked (matches the pre-refactor gate)")

    # Regime: off by default -> trades even with a hypothetically hostile regime
    # value, because evaluate_signal() only passes spy_regime when regime is
    # not None, and backtest_ticker() only ever supplies one when use_regime.
    df = _synthetic_ohlc(adx=40.0)
    sig = evaluate_signal(df, len(df) - 1, params, regime=None)
    assert sig is not None
    print("regime unset             : trades (use_regime=False path)")

    # Regime ON: bullish setup in a "Bear" tape is blocked; in a "Bull" tape
    # it trades. Matches the original inline `regime == "Bear"/"Bull"` checks.
    cfg_regime = dict(cfg, use_regime=True)
    params_regime = build_signal_params(cfg_regime)
    sig = evaluate_signal(df, len(df) - 1, params_regime, regime="Bear")
    assert sig is None, "long setup in a Bear tape must block when use_regime=True"
    print("regime ON, conflicting   : blocked")
    sig = evaluate_signal(df, len(df) - 1, params_regime, regime="Bull")
    assert sig is not None, "long setup in a Bull tape must trade when use_regime=True"
    print("regime ON, aligned       : trades")

    # Bearish path, and the returned dict has exactly what simulate_trade() reads.
    df_bear = _synthetic_ohlc(up=False, adx=40.0)
    sig = evaluate_signal(df_bear, len(df_bear) - 1, params)
    assert sig is not None and sig["trend"] == "Bearish"
    assert sig["stop"] > sig["entry"] > sig["target"]
    for k in ("trend", "entry", "stop", "target", "rr", "atr"):
        assert k in sig, f"simulate_trade() reads '{k}' — missing from evaluate_signal()"
    print(f"clean bearish bar        : trades, keys present for simulate_trade()")

    # ── the weekly filter must actually GATE, not just get reported ──
    # THE REGRESSION THIS TEST EXISTS TO CATCH: --use-weekly turns on
    # params.weekly_confirm and run() feeds a per-bar weekly trend, but
    # signal_core.evaluate() only RECORDS the verdict in
    # filters["Multi-TF Alignment"] — it does not block on it. The first
    # version of evaluate_signal() never read that key, so both arms of the
    # weekly A/B ran an identical signal and produced identical trades; the
    # measurement compared the filter against itself and looked like "the
    # weekly filter does nothing", which was a statement about the wiring,
    # not about the market.
    #
    # The fixture is a Bullish setup. weekly="Bearish" is the disagreement
    # case; weekly=None is the fetch-failed case, which signal_core treats as
    # BLOCKING on purpose (see its own selftest) so a dead Yahoo call cannot
    # silently loosen the system.
    df_wk = _synthetic_ohlc(adx=40.0)
    params_wk = build_signal_params(dict(cfg, use_weekly=True))

    assert evaluate_signal(df_wk, len(df_wk) - 1, params_wk,
                           weekly="Bullish") is not None, \
        "weekly agreeing with the daily trend must still trade"
    assert evaluate_signal(df_wk, len(df_wk) - 1, params_wk,
                           weekly="Bearish") is None, \
        "weekly DISAGREEING must block — otherwise --use-weekly measures nothing"
    assert evaluate_signal(df_wk, len(df_wk) - 1, params_wk,
                           weekly=None) is None, \
        "weekly unavailable must block, matching signal_core and the live app"
    print("weekly filter ON        : aligned trades, misaligned and missing block")

    # And the mirror image: with the filter OFF the weekly value is inert, so
    # the default arm of the A/B is byte-identical to every run before
    # --use-weekly existed. If this ever fails, the fix above has moved the
    # BASELINE, which would invalidate the OOS lock rather than test it.
    for _wk in ("Bullish", "Bearish", None):
        assert evaluate_signal(df_wk, len(df_wk) - 1, params,
                               weekly=_wk) is not None, \
            f"weekly={_wk!r} must not affect the run when weekly_confirm is off"
    print("weekly filter OFF       : weekly value inert, baseline unmoved")

    # The tally that makes the above visible in a REAL run's output. Without
    # it the aggregate numbers cannot distinguish "the filter barely matters"
    # from "the filter never ran" — which is precisely how the first weekly
    # A/B produced a confident-looking result from a dead gate.
    _tal: dict[str, int] = {}
    evaluate_signal(df_wk, len(df_wk) - 1, params_wk, weekly="Bearish", tally=_tal)
    assert _tal.get("weekly") == 1, \
        f"a weekly rejection must be counted, got {_tal!r}"
    evaluate_signal(df_wk, len(df_wk) - 1, params_wk, weekly="Bullish", tally=_tal)
    assert _tal.get("passed") == 1 and _tal.get("reached_filters") == 2, \
        f"tally must count survivors and bars reaching the filters, got {_tal!r}"
    print("gate tally              : counts weekly rejections and survivors")

    # ── today's bar must never reach the backtest ──
    # Yahoo emits a row dated today even before the open, and every value in
    # it moves until the close. signal_core.drop_partial_bar() does not cover
    # that case (it only trims while the market is OPEN), and backtest.py
    # never called it at all — so two fetches four minutes apart returned
    # identical row counts and different content for all 13 series.
    _today = datetime.now(_ET).date()
    _idx = pd.bdate_range(end=pd.Timestamp(_today), periods=6)
    _c = np.linspace(100, 105, len(_idx))
    _raw = pd.DataFrame({"Date": _idx, "Open": _c, "High": _c, "Low": _c,
                         "Close": _c, "Volume": np.full(len(_idx), 1e6)})
    assert pd.to_datetime(_raw["Date"]).dt.date.max() == _today, \
        "fixture must actually contain a today-dated bar, or this tests nothing"
    _trimmed = _drop_todays_bar("TEST", _raw)
    assert len(_trimmed) == len(_raw) - 1, \
        f"today's bar must be dropped, kept {len(_trimmed)} of {len(_raw)}"
    assert pd.to_datetime(_trimmed["Date"]).dt.date.max() < _today
    # ...and a frame that stops before today is left completely alone.
    _old = _raw.iloc[:-1].copy()
    assert len(_drop_todays_bar("TEST", _old)) == len(_old), \
        "a frame with no today-dated bar must pass through untouched"
    print(f"today's bar             : dropped ({_today}), older bars untouched")

    # ── the long/short split reports each side separately ──
    # A diagnostic that silently mixed the sides would be worse than none: it
    # would look like a breakdown and be an average.
    _mixed = [{"r": 1.0, "trend": "Bullish", "hold": 5},
              {"r": 1.0, "trend": "Bullish", "hold": 5},
              {"r": -1.0, "trend": "Bearish", "hold": 5},
              {"r": -1.0, "trend": "Bearish", "hold": 5}]
    # Walk SIDES exactly as run() does, so a swapped label is caught too: the
    # fixture's bullish trades win and its bearish trades lose, so whichever
    # row prints "LONG " must be the +1.00 R one.
    _by_label = {lbl.strip(): stats(split_by_side(_mixed, side))
                 for side, lbl in SIDES}
    assert set(_by_label) == {"LONG", "SHORT"}, _by_label
    _long, _short = _by_label["LONG"], _by_label["SHORT"]
    assert _long["avg_r"] == 1.0, \
        "the row labelled LONG is not the bullish side — the labels are " \
        f"attached to the wrong trends: {_by_label}"
    _all = stats(_mixed)
    assert _long["avg_r"] == 1.0 and _short["avg_r"] == -1.0, (_long, _short)
    assert _all["avg_r"] == 0.0, _all
    assert _long["avg_r"] != _all["avg_r"], \
        "the split must be distinguishable from the aggregate on a fixture " \
        "built to make them differ, or it is not testing anything"
    assert _long["trades"] + _short["trades"] == _all["trades"]
    print(f"long/short split        : +1.00 R long, -1.00 R short, "
          f"0.00 R blended — sides do not leak")

    # ── the concentration check must actually detect concentration ──
    # Fixture: four names contribute nothing, one name carries everything.
    # Built so a check that ignores the per-ticker split cannot pass it.
    _conc_rows = []
    for _tk in ("A", "B", "C", "D"):
        _conc_rows += [{"r": 0.0, "trend": "Bullish", "hold": 3, "ticker": _tk}
                       for _ in range(5)]
    _conc_rows += [{"r": 2.0, "trend": "Bullish", "hold": 3, "ticker": "HOG"}
                   for _ in range(5)]
    _c = side_concentration(_conc_rows, "Bullish")
    assert _c is not None, \
        "the check returned nothing on a 5-ticker fixture — it is not " \
        "splitting by ticker at all"
    assert _c["best"] == "HOG", _c["best"]
    assert abs(_c["overall"] - 0.4) < 1e-9, _c["overall"]
    assert abs(_c["drop_best"] - 0.0) < 1e-9, (
        f"drop_best={_c['drop_best']:.3f}, expected 0.000. The four other "
        f"names contribute nothing, so removing HOG must leave exactly 0 — "
        f"a non-zero value means HOG is still in the leave-one-out set.")
    assert _c["n_positive"] == 1 and _c["n_tickers"] == 5, _c
    print(f"concentration           : {_c['overall']:+.2f} R -> "
          f"{_c['drop_best']:+.2f} R without {_c['best']} — one name detected")

    # And it must NOT cry concentration when the result is genuinely broad.
    _broad = [{"r": 1.0, "trend": "Bullish", "hold": 3, "ticker": t}
              for t in ("A", "B", "C", "D", "E") for _ in range(5)]
    _b = side_concentration(_broad, "Bullish")
    assert abs(_b["overall"] - 1.0) < 1e-9 and abs(_b["drop_best"] - 1.0) < 1e-9, _b
    assert _b["n_positive"] == 5, _b
    print(f"broad result            : {_b['overall']:+.2f} R unchanged when the "
          f"best name leaves — no false alarm")

    # "Best" must mean biggest CONTRIBUTION, not biggest single trade. STEADY
    # totals +5.0 over ten trades; SPIKE totals +3.0 in one. Removing STEADY is
    # what actually moves the aggregate, so STEADY is the name the check must
    # name. A fixture where both rules agree would not test this.
    _tie = ([{"r": 0.5, "trend": "Bullish", "hold": 3, "ticker": "STEADY"}
             for _ in range(10)]
            + [{"r": 3.0, "trend": "Bullish", "hold": 3, "ticker": "SPIKE"}])
    _t = side_concentration(_tie, "Bullish")
    assert sum(r["r"] for r in _tie if r["ticker"] == "STEADY") > \
        sum(r["r"] for r in _tie if r["ticker"] == "SPIKE"), \
        "fixture is broken: STEADY must out-total SPIKE or it tests nothing"
    assert max(r["r"] for r in _tie if r["ticker"] == "SPIKE") > \
        max(r["r"] for r in _tie if r["ticker"] == "STEADY"), \
        "fixture is broken: SPIKE must have the larger single trade"
    assert _t["best"] == "STEADY", (
        f"best={_t['best']} — 'best' must be the biggest total contribution, "
        f"not the biggest single trade: a lone +3.00 R outlier moves a "
        f"12-trade average far less than ten steady +0.50s")
    print(f"best = contribution     : STEADY (+5.0 total) over SPIKE "
          f"(+3.0 in one trade)")

    # ── the RENDER path, not just the arithmetic ──
    import io as _io, contextlib as _ctx

    def _render_conc(rows):
        buf = _io.StringIO()
        with _ctx.redirect_stdout(buf):
            print_concentration(rows)
        return buf.getvalue()

    _fragile = _render_conc(_conc_rows)                 # HOG carries everything
    assert "over half the result is that one name" in _fragile, (
        "HOG carries 100% of this fixture's result and the fragility flag did "
        "not fire. Rendered:\n" + _fragile)
    assert "without HOG" in _fragile, (
        "the render must name which ticker was removed. Rendered:\n" + _fragile)
    _solid = _render_conc(_broad)                       # five equal names
    assert "over half the result" not in _solid, \
        "a broad, five-name result was flagged as carried by one ticker"
    # One ticker means there is no concentration question — say nothing.
    _single = [{"r": 1.0, "trend": "Bullish", "hold": 3, "ticker": "ONE"}
               for _ in range(5)]
    assert _render_conc(_single) == "", \
        "a single-ticker run must print nothing rather than compare a name " \
        "against itself"
    print("concentration render    : flags the fragile case, quiet on the "
          "broad and single-name ones")

    # ── hold profile: same-session exits must be counted, and counted apart ──
    # Fixture: three trades die on their entry bar, five live longer, and the
    # two groups have DIFFERENT average R. A profile that pooled them would
    # report one number and match neither.
    # There are losses in BOTH groups on purpose. A fixture whose only losses
    # were same-session would score identically whether the loss rate counted
    # that group or every trade, and would pass a broken implementation.
    def _t(r, hold, outcome):
        return {"r": r, "trend": "Bullish", "hold": hold, "ticker": "A",
                "outcome": outcome}
    _hp_rows = ([_t(-1.0, 0, "loss"), _t(-1.0, 0, "loss"), _t(+1.0, 0, "win")]
                + [_t(-1.0, 4, "loss")] * 3 + [_t(+2.0, 4, "win")] * 2)
    _hp = hold_profile(_hp_rows)
    assert _hp["n_same"] == 3 and _hp["n"] == 8, _hp
    assert abs(_hp["pct_same"] - 37.5) < 1e-9, _hp["pct_same"]
    _all_losses = sum(1 for t in _hp_rows if t["outcome"] == "loss")
    assert _all_losses != _hp["n_same"], \
        "fixture is broken: losses must NOT all be same-session, or a loss " \
        "rate computed over every trade would score the same and pass"
    assert abs(_hp["loss_rate_same"] - 200.0 / 3) < 1e-9, (
        f"loss_rate_same={_hp['loss_rate_same']:.1f}, expected 66.7 — 2 of "
        f"the 3 same-session trades lost. {_all_losses} of 8 lost overall, so "
        f"a rate taken over all trades would read "
        f"{100 * _all_losses / _hp['n_same']:.1f}.")
    assert abs(_hp["avg_r_same"] + 1 / 3) < 1e-9, (
        f"avg_r_same={_hp['avg_r_same']:.3f}, expected -0.333 (the three "
        f"same-session trades). Getting {_hp['avg_r_rest']:.3f} or a blend "
        f"means the two groups are being averaged together, which is the one "
        f"thing this section exists to avoid.")
    assert abs(_hp["avg_r_rest"] - 0.2) < 1e-9, (
        f"avg_r_rest={_hp['avg_r_rest']:.3f}, expected +0.200 (the five "
        f"longer-held trades)")
    assert _hp["avg_r_same"] != _hp["avg_r_rest"], \
        "the fixture must make the two groups differ or it tests nothing"
    print(f"hold profile            : {_hp['pct_same']:.1f}% same-session at "
          f"{_hp['avg_r_same']:+.2f} R vs {_hp['avg_r_rest']:+.2f} R — split, "
          f"not pooled")

    # hold == 1 is NOT the same session. An off-by-one here would silently
    # inflate the headline number this whole section exists to report.
    _off = [{"r": -1.0, "trend": "Bullish", "hold": 1, "ticker": "A",
             "outcome": "loss"} for _ in range(4)]
    assert hold_profile(_off)["n_same"] == 0, \
        "hold == 1 means the trade survived its entry bar and exited on the " \
        "next one — counting it as same-session overstates the finding"

    _b2 = _io.StringIO()
    with _ctx.redirect_stdout(_b2):
        print_hold_profile(_hp_rows, dict(DEFAULTS))
    _txt = _b2.getvalue()
    assert "same session" in _txt and "37.5%" in _txt, _txt
    assert "DESCRIPTIVE" in _txt, \
        "the note that this is not a licence to retune the stop must survive"
    _b3 = _io.StringIO()
    try:
        with _ctx.redirect_stdout(_b3):
            print_hold_profile(_off, dict(DEFAULTS))
    except Exception as _e:
        raise AssertionError(
            f"a run with no same-session exits must render, not raise: "
            f"{type(_e).__name__}: {_e}") from None
    assert "none: every trade lived past its entry bar" in _b3.getvalue(), \
        "with no same-session exits the profile must say so, not print 0%"
    print("hold profile render     : states the case, and the no-cases case")

    # ── an unfinished trade at the sample end must say so ──
    # Both directions are asserted. A test that only checks the censored case
    # passes just as well with the flag hardwired to True, which is the dead
    # fixture this repo keeps finding.
    _ccfg = dict(cfg, slippage_bps=0.0, commission=0.0, max_hold=10)

    def _flat(n_bars):
        _c = np.full(n_bars, 100.0)
        return pd.DataFrame({
            "Date": pd.bdate_range("2024-01-01", periods=n_bars),
            "Open": _c, "High": _c + 0.1, "Low": _c - 0.1, "Close": _c})

    _sig = {"trend": "Bullish", "entry": 100.0, "stop": 95.0,
            "target": 115.0, "rr": 3.0}

    # 4 bars, hold window 10 -> cannot finish: censored.
    _ct: dict = {}
    _short = simulate_trade(_flat(4), 0, _sig, _ccfg, tally=_ct)
    assert _short["filled"] and _short["outcome"] == "timeout", _short
    assert _short["censored"] is True, (
        "a trade whose hold window runs past the last bar is NOT settled on its "
        "own terms and must be flagged as unfinished")
    assert _ct.get("unfinished at sample end (marked to last close)") == 1, _ct

    # 40 bars, same window -> finishes: must NOT be flagged.
    _lt: dict = {}
    _long = simulate_trade(_flat(40), 0, _sig, _ccfg, tally=_lt)
    assert _long["filled"] and _long["outcome"] == "timeout", _long
    assert _long["censored"] is False, (
        "a trade that ran its full hold window inside the sample is settled — "
        "flagging it as unfinished would overstate how much of the record is "
        "provisional")
    assert "unfinished at sample end (marked to last close)" not in _lt, _lt
    print("right-censoring         : unfinished trades flagged, settled ones not")

    # ── a fill that gapped past its own levels must not become a trade ──
    # Before this check both cases scored with the WRONG SIGN, so the test
    # asserts the sign explicitly rather than only that a trade was dropped.
    _gcfg = dict(cfg, slippage_bps=0.0, commission=0.0)

    def _gap(open_px, low, high, trend, stop, target, tally=None):
        _df = pd.DataFrame({
            "Date": pd.bdate_range("2024-01-01", periods=3),
            "Open": [100.0, open_px, open_px], "High": [100.0, high, high],
            "Low": [100.0, low, low], "Close": [100.0, open_px, open_px]})
        return simulate_trade(_df, 0, {"trend": trend, "entry": 100.0,
                                       "stop": stop, "target": target,
                                       "rr": 3.0}, _gcfg, tally=tally)

    # The normal path still fills and still loses 1R — the guard must not be
    # so wide that it swallows ordinary stop-outs.
    for _side, _a in (("bullish", (99.0, 97.0, 99.5, "Bullish", 98.0, 106.0)),
                      ("bearish", (101.0, 100.5, 103.0, "Bearish", 102.0, 94.0))):
        _o = _gap(*_a)
        assert _o.get("filled"), (
            f"an ORDINARY {_side} stop-out was rejected as a gapped fill "
            f"({_o}). The guard is too wide — it must reject only fills on "
            f"the wrong side of their own levels, not every losing trade.")
        assert abs(_o["r"] + 1.0) < 1e-9, (
            f"ordinary {_side} stop-out scored {_o['r']:+.3f}, expected "
            f"-1.000")

    _gt: dict = {}
    for _lbl, _args, _want in (
            ("bullish gapped below its stop",
             (96.0, 95.0, 96.5, "Bullish", 98.0, 106.0), "stop"),
            ("bullish gapped above its target",
             (108.0, 107.5, 109.0, "Bullish", 98.0, 106.0), "target"),
            ("bearish gapped above its stop",
             (104.0, 103.5, 105.0, "Bearish", 102.0, 94.0), "stop"),
            ("bearish gapped below its target",
             (92.0, 91.0, 92.5, "Bearish", 102.0, 94.0), "target")):
        _r = _gap(*_args, tally=_gt)
        assert not _r.get("filled"), (
            f"{_lbl}: filled with r={_r.get('r'):+.3f}. A fill on the wrong "
            f"side of its own {_want} inverts r = pnl / |entry - stop| and "
            f"books the worst fills in the sample as winners.")
        assert _r["gapped"] == _want, (_lbl, _r)
    assert sum(_gt.values()) == 4 and len(_gt) == 2, _gt
    print(f"gapped fills            : 4 rejected (2 past stop, 2 past target), "
          f"counted not silent; ordinary stop-outs still fill at -1.00 R")

    # ── the on-disk bar cache actually caches ──
    # A harness that refetches on every run cannot measure anything smaller
    # than its data feed's own drift, which here was 7 trades and 0.014 R
    # between two runs of the same command. These assertions are what make
    # "reproducible" a property rather than an intention.
    if bar_cache is not None:
        import tempfile as _tf, shutil as _sh
        from pathlib import Path as _P
        _real = (bar_cache.CACHE_DIR, bar_cache.MANIFEST)
        _tmp = _P(_tf.mkdtemp(prefix="bt_cache_selftest_"))
        bar_cache.CACHE_DIR, bar_cache.MANIFEST = _tmp, _tmp / "manifest.json"
        _real_unc = globals()["_download_uncached"]
        _fetches = []
        def _counting(t, y, interval="1d"):
            _fetches.append(t)
            n = 120
            idx = pd.bdate_range("2024-01-01", periods=n)
            c = np.linspace(100, 130, n)
            return pd.DataFrame({"Date": idx, "Open": c, "High": c * 1.01,
                                 "Low": c * 0.99, "Close": c,
                                 "Volume": np.full(n, 1e6)})
        globals()["_download_uncached"] = _counting
        try:
            d1 = download("ZZZ", 5)
            d2 = download("ZZZ", 5)
            assert d1 is not None and d2 is not None
            assert len(_fetches) == 1, \
                f"second download must hit the cache, got {len(_fetches)} fetches"
            assert d1.equals(d2), "cached bars must come back identical"
            print(f"bar cache               : 2 downloads, 1 fetch, identical bars")

            # The adjusted and raw arms must not share a cache entry. If they
            # did, running one after the other would silently compare a price
            # series against itself — the same non-measurement as the weekly
            # filter, but harder to spot, because both frames look plausible.
            # The flip below goes raw -> adjusted because raw is now the
            # DEFAULT; flipping to the default would be a no-op and would test
            # nothing, which is how this assertion first went green by
            # accident when the default changed under it.
            global AUTO_ADJUST
            assert AUTO_ADJUST is False, \
                "raw is the default since 2026-09-02 — if this fails the " \
                "default moved back to adjusted, which is not reproducible"
            n_before = len(_fetches)
            AUTO_ADJUST = True
            download("ZZZ", 5)
            assert len(_fetches) == n_before + 1, \
                "--adjusted-prices must fetch its OWN series, not reuse the " \
                "raw one — otherwise the A/B compares a series to itself"
            AUTO_ADJUST = False
            download("ZZZ", 5)
            assert len(_fetches) == n_before + 1, \
                "switching back must hit the ORIGINAL raw entry"
            print(f"raw vs adjusted         : separate cache entries, no collision")

            # --no-cache must genuinely bypass, or the escape hatch is a lie.
            global CACHE_ENABLED
            CACHE_ENABLED = False
            n_pre = len(_fetches)
            download("ZZZ", 5)
            assert len(_fetches) == n_pre + 1, \
                "--no-cache must actually refetch"
            CACHE_ENABLED = True
            print(f"--no-cache              : bypasses the cache, refetches")
        finally:
            globals()["_download_uncached"] = _real_unc
            _sh.rmtree(_tmp, ignore_errors=True)
            bar_cache.CACHE_DIR, bar_cache.MANIFEST = _real
            CACHE_ENABLED = True
            AUTO_ADJUST = False

    # ── weekly trend map: the filter the OOS test never measured ──
    # Built so weekly_confirm can finally be A/B'd. Two things must hold: it
    # must agree with the LIVE rule (market_context), and it must not peek.
    #
    # The fixture OSCILLATES on purpose. An earlier version used a monotonic
    # uptrend, where every week's verdict is "Bullish" — so the lookahead
    # assertion below compared "Bullish" to "Bullish" and passed even with the
    # no-lookahead shift deleted. A fixture that cannot distinguish the bug
    # from the fix tests nothing; the assert on _flip below now guarantees
    # this one can.
    import market_context as mc_
    wk_idx = pd.bdate_range("2024-01-01", periods=120, freq="W-MON")
    _t = np.arange(120)
    wk_close = 100 + 12 * np.sin(2 * np.pi * _t / 7) + 0.05 * _t
    wk_raw = pd.DataFrame({"Date": wk_idx, "Close": wk_close,
                           "Open": wk_close, "High": wk_close + 1,
                           "Low": wk_close - 1, "Volume": 1e6})

    _real_download = globals()["download"]
    globals()["download"] = lambda t, y, interval="1d": wk_raw
    try:
        wmap = build_weekly_trend_map("TEST", 5)
    finally:
        globals()["download"] = _real_download
    assert wmap is not None and len(wmap) == len(wk_raw)

    # PARITY: the vectorised verdict must equal market_context's function on
    # the same bars. If these diverge, the backtest measures a filter the live
    # system does not apply — the whole point of this file.
    for k in (40, 70, 119):
        expanding = mc_.weekly_trend_from_bars(wk_raw.iloc[:k + 1])
        assert wmap.iloc[k] == expanding, (
            f"week {k}: backtest says {wmap.iloc[k]!r}, market_context says "
            f"{expanding!r} — the backtest would be testing a different rule")
    print(f"weekly map parity      : matches market_context on sampled weeks")

    # NO LOOKAHEAD: week k's verdict must not be readable until week k+1 has
    # STARTED. A weekly bar labelled Monday only closes on Friday, so reading
    # it mid-week leaks Thursday and Friday into a Wednesday decision.
    _flip = next(k for k in range(mc_.WEEKLY_MIN_BARS, len(wk_raw) - 2)
                 if wmap.iloc[k] != wmap.iloc[k + 1])
    assert wmap.iloc[_flip] != wmap.iloc[_flip + 1], "fixture must discriminate"
    wednesday = pd.Timestamp(wk_idx[_flip + 1]) + pd.Timedelta(days=2)
    assert wmap.asof(wednesday) == wmap.iloc[_flip], (
        f"mid-week lookup returned {wmap.asof(wednesday)!r}; the week "
        f"beginning {wk_idx[_flip + 1].date()} has NOT closed yet, so its "
        f"verdict must not be visible — this leaks future bars")
    print(f"weekly map lookahead   : week k readable only from week k+1 "
          f"(checked at a week where the verdict flips)")

    # End-to-end: real ta-computed indicators through compute() ->
    # backtest_ticker() -> simulate_trade(), no exceptions, sane R-multiples.
    # A pure monotonic price line pegs RSI near 100 (all gains, no losses),
    # which fails the 30-75 band forever — a random walk with positive drift
    # gives real pullbacks, so RSI/MACD/ADX behave like actual market data.
    # adx_min is relaxed here on purpose: this block exercises the pipeline,
    # it is not re-asserting the ADX gate (already covered above).
    n = 260
    idx = pd.bdate_range("2020-01-02", periods=n)
    rng = np.random.default_rng(7)
    rets = rng.normal(loc=0.0015, scale=0.012, size=n)
    close = 100.0 * np.cumprod(1.0 + rets)
    high = close * (1.0 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1.0 - np.abs(rng.normal(0, 0.004, n)))
    raw = pd.DataFrame({
        "Date": idx, "Open": close, "High": high, "Low": low,
        "Close": close, "Volume": np.full(n, 2_000_000.0),
    })
    full = compute(raw)   # Open/Date already aligned; see run() for why
    cfg_e2e = dict(cfg, adx_min=10.0)
    params_e2e = build_signal_params(cfg_e2e)
    trades = backtest_ticker(full, cfg_e2e, params_e2e)
    assert len(trades) >= 1, \
        "a seeded random walk with positive drift should produce at least " \
        "one trade end to end — if this starts failing, check compute() " \
        "or evaluate_signal() before assuming the seed is unlucky"
    print(f"end-to-end synthetic run : {len(trades)} trade(s), no exceptions")
    for t in trades:
        assert t["filled"] and np.isfinite(t["r"])

    print("\nAll self-tests passed.")
    return 0


def parse_args() -> tuple[dict, bool]:
    p = argparse.ArgumentParser(description="Trading Copilot historical backtest")
    p.add_argument("--selftest", action="store_true",
                   help="verify the signal_core delegation on synthetic data, "
                        "no network, then exit")
    p.add_argument("--tickers", type=str, default=",".join(DEFAULTS["tickers"]))
    p.add_argument("--years", type=int, default=DEFAULTS["years"])
    p.add_argument("--adx-min", type=float, default=DEFAULTS["adx_min"])
    p.add_argument("--atr-stop", type=float, default=DEFAULTS["atr_stop_mult"])
    p.add_argument("--atr-tgt", type=float, default=DEFAULTS["atr_tgt_mult"])
    p.add_argument("--min-rr", type=float, default=DEFAULTS["min_rr"])
    p.add_argument("--max-hold", type=int, default=DEFAULTS["max_hold"])
    p.add_argument("--slippage-bps", type=float, default=DEFAULTS["slippage_bps"])
    p.add_argument("--commission", type=float, default=DEFAULTS["commission"])
    p.add_argument("--cooldown", type=int, default=DEFAULTS["cooldown_bars"])
    p.add_argument("--use-regime", action="store_true", default=DEFAULTS["use_regime"])
    p.add_argument("--adjusted-prices", action="store_true",
                   help="fetch with auto_adjust=True (split+dividend "
                        "adjusted). This is what the ORIGINAL OOS baseline "
                        "was measured on, and it is NOT reproducible: a "
                        "refetch rewrites the whole history of every "
                        "dividend payer. Cached separately from the raw "
                        "series, so the two can be compared.")
    p.add_argument("--raw-prices", action="store_true",
                   help="fetch with auto_adjust=False. This is now the "
                        "default and the flag is a no-op, kept so existing "
                        "commands keep working.")
    p.add_argument("--no-cache", action="store_true",
                   help="bypass bar_cache.py and fetch live. Makes the run "
                        "NON-reproducible — two runs minutes apart can differ "
                        "by more than the effect you are measuring.")
    p.add_argument("--refresh-cache", action="store_true",
                   help="refetch every series and replace what is cached. "
                        "Do this deliberately, between experiments — never "
                        "in the middle of an A/B, or the arms stop being "
                        "comparable.")
    p.add_argument("--use-weekly", action="store_true", default=DEFAULTS["use_weekly"],
                   help="apply the weekly-alignment filter (fetches weekly bars). "
                        "The live config runs this ON but it has never been measured.")
    a = p.parse_args()

    global CACHE_ENABLED, CACHE_REFRESH, AUTO_ADJUST
    CACHE_ENABLED = not a.no_cache
    CACHE_REFRESH = a.refresh_cache
    AUTO_ADJUST = bool(a.adjusted_prices)

    cfg = dict(
        tickers       = [t.strip().upper() for t in a.tickers.split(",") if t.strip()],
        years         = a.years,
        adx_min       = a.adx_min,
        atr_stop_mult = a.atr_stop,
        atr_tgt_mult  = a.atr_tgt,
        min_rr        = a.min_rr,
        volume_mult   = DEFAULTS["volume_mult"],
        max_hold      = a.max_hold,
        slippage_bps  = a.slippage_bps,
        commission    = a.commission,
        use_regime    = a.use_regime,
        use_weekly    = a.use_weekly,
        cooldown_bars = a.cooldown,
    )
    return cfg, a.selftest


if __name__ == "__main__":
    _cfg, _selftest = parse_args()
    if _selftest:
        sys.exit(selftest())
    sys.exit(run(_cfg) or 0)
