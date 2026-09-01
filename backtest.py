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

Filters intentionally SIMPLIFIED for a clean historical test — enforced by
building a SignalParams with weekly_confirm=False (no weekly-timeframe
data is fetched here, and leaving it on would BLOCK every bar, not
"simplify" the filter — see build_signal_params()):
  • ADX filter: applied (same threshold) — signal_core.evaluate() does not
    block on ADX itself (it only feeds the "high_quality" tier), so this file
    checks the filter's own pass/fail explicitly, same as the old inline gate.
  • Weekly alignment / earnings blackout: OFF — these need external calendars
    or a second data feed and would add lookahead/complexity.
  • SPY regime: OFF by default. Turn USE_REGIME on to approximate the SPY
    macro filter using SPY's own 200-SMA (computed from the same download).

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
warnings.filterwarnings("ignore")

from dataclasses import replace

import numpy as np
import pandas as pd

import signal_core as sc

try:
    import data_source
except ImportError:
    data_source = None    # optional — see download(); falls back to Yahoo-only
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

    weekly_confirm is forced OFF regardless of cfg: no weekly-timeframe data
    is fetched in this module (see the module docstring), and
    signal_core.evaluate() BLOCKS every bar when weekly_trend is None and
    weekly_confirm is True. Leaving the default on here would not "simplify"
    the filter, it would silently zero out every trade.
    """
    return replace(
        sc.DEFAULTS,
        adx_min=cfg["adx_min"],
        atr_stop_mult=cfg["atr_stop_mult"],
        atr_tgt_mult=cfg["atr_tgt_mult"],
        min_rr=cfg["min_rr"],
        volume_mult=cfg["volume_mult"],
        weekly_confirm=False,
        spy_regime_on=cfg["use_regime"],
    )


def evaluate_signal(df: pd.DataFrame, i: int, params: sc.SignalParams,
                    regime: str | None = None) -> dict | None:
    """
    Evaluate the signal at bar i using ONLY rows 0..i (no lookahead), through
    signal_core.evaluate() — see the module docstring for why this is no
    longer a hand-maintained copy.

    df.iloc[:i+1] reproduces the exact windows the old inline version passed
    by hand: signal_core internally takes df["Low"].tail(10) / df["High"].tail(20),
    which over this slice are the same rows as
    df.iloc[max(0,i-9):i+1] / df.iloc[max(0,i-19):i+1] were.

    signal_core.evaluate() does NOT block on the ADX or regime filters
    itself — they only feed the "high_quality" tier that gates live Telegram
    alerts (see scanner.py's analyze()). This backtest's own definition of
    "a trade" has always been looser than "high_quality" (no weekly/earnings/
    strength requirement — see the module docstring), so it checks the ADX
    and regime filter results directly, matching the pre-refactor behaviour
    exactly rather than silently tightening or loosening what counts as a
    signal.
    """
    window = df.iloc[: i + 1]
    spy_regime = {"regime": regime} if regime is not None else None
    r = sc.evaluate(window, "BT", params, spy_regime=spy_regime)
    if r["blocked"]:
        return None
    if not r["filters"]["ADX Trend Strength"]["pass"]:
        return None
    if not r["filters"]["Macro Regime"]["pass"]:
        return None

    return {"trend": r["trend"], "entry": r["entry"], "stop": r["stop"],
            "target": r["target"], "rr": r["rr"], "atr": r["atr"]}


# ══════════════════════════════════════════════════════════════════════
# TRADE SIMULATION — enter next open, stop-first fills, timeout m2m
# ══════════════════════════════════════════════════════════════════════
def simulate_trade(df: pd.DataFrame, signal_i: int, trade: dict,
                   cfg: dict) -> dict:
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

    if exit_i is None:                           # timeout — mark to market
        exit_i = min(entry_i + cfg["max_hold"] - 1, n - 1)
        exit_px = float(df["Close"].iloc[exit_i])
        outcome = "timeout"

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
        "r": r_multiple, "rr_planned": trade["rr"],
        "hold": exit_i - entry_i,
        "entry_date": df["Date"].iloc[entry_i] if "Date" in df.columns else entry_i,
    }


# ══════════════════════════════════════════════════════════════════════
# BACKTEST DRIVER
# ══════════════════════════════════════════════════════════════════════
def backtest_ticker(df: pd.DataFrame, cfg: dict, params: sc.SignalParams,
                    regime_series: pd.Series | None = None) -> list[dict]:
    trades = []
    i = 0
    n = len(df)
    while i < n - 1:
        regime = None
        if regime_series is not None and i < len(regime_series):
            regime = regime_series.iloc[i]
        sig = evaluate_signal(df, i, params, regime=regime)
        if sig:
            res = simulate_trade(df, i, sig, cfg)
            if res.get("filled"):
                trades.append(res)
                i += cfg["cooldown_bars"] + 1     # cooldown after a trade
                continue
        i += 1
    return trades


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


def build_regime_series(spy_df: pd.DataFrame) -> pd.Series:
    """Approximate app.py's SPY macro filter: price vs its own 200-SMA."""
    sma200 = spy_df["Close"].rolling(200, min_periods=50).mean()
    out = pd.Series("Neutral", index=spy_df.index)
    out[spy_df["Close"] > sma200] = "Bull"
    out[spy_df["Close"] < sma200] = "Bear"
    return out.reset_index(drop=True)


def _yahoo_download(ticker: str, period: str, interval: str) -> pd.DataFrame | None:
    return yf.download(ticker, period=period, interval=interval,
                       progress=False, auto_adjust=True)


def download(ticker: str, years: int) -> pd.DataFrame | None:
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
                ticker, period=f"{years}y", interval="1d",
                yahoo_fetch=_yahoo_download)
            if source not in ("yahoo", "none"):
                print(f"  ! {ticker}: Yahoo unavailable, used {source} fallback "
                     f"(not split/dividend-adjusted the way auto_adjust=True is)")
        else:
            df = _yahoo_download(ticker, f"{years}y", "1d")
        if df is None or df.empty:
            return None
        # flatten possible multiindex columns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index()
        keep = {"Date": "Date", "Open": "Open", "High": "High",
                "Low": "Low", "Close": "Close", "Volume": "Volume"}
        df = df[[c for c in keep if c in df.columns]].rename(columns=keep)
        return df
    except Exception as e:
        print(f"  ! download failed for {ticker}: {e}")
        return None


def run(cfg: dict) -> None:
    print("=" * 78)
    print("TRADING COPILOT ELITE — HISTORICAL BACKTEST (real data)")
    print("=" * 78)
    print(f"Tickers      : {', '.join(cfg['tickers'])}")
    print(f"History      : {cfg['years']} years daily")
    print(f"ADX min      : {cfg['adx_min']}   ATR stop×: {cfg['atr_stop_mult']}   "
          f"ATR tgt×: {cfg['atr_tgt_mult']}   Min R:R: {cfg['min_rr']}")
    print(f"Max hold     : {cfg['max_hold']} bars   Slippage: {cfg['slippage_bps']}bps/side   "
          f"Regime filter: {'ON (SPY 200-SMA)' if cfg['use_regime'] else 'OFF'}")
    print("=" * 78)

    params = build_signal_params(cfg)

    # Optional regime series from SPY
    regime_by_date = None
    if cfg["use_regime"]:
        spy_raw = download("SPY", cfg["years"])
        if spy_raw is not None:
            spy_c = compute(spy_raw)
            spy_c = spy_c.merge(spy_raw[["Date"]], left_index=True,
                                right_index=True, how="left")
            reg = build_regime_series(spy_raw)
            regime_by_date = dict(zip(spy_raw["Date"], reg))

    per_ticker_rows = []
    all_trades = []

    for tk in cfg["tickers"]:
        raw = download(tk, cfg["years"])
        if raw is None:
            print(f"\n{tk}: no data — skipped")
            continue
        df = compute(raw)
        # attach dates back for reporting/regime mapping
        df = df.copy()
        # re-attach Open + Date aligned by tail length
        tail = raw.tail(len(df)).reset_index(drop=True)
        for col in ["Open", "Date"]:
            if col in tail.columns:
                df[col] = tail[col].values

        # per-bar regime lookup
        reg_series = None
        if regime_by_date is not None and "Date" in df.columns:
            reg_series = df["Date"].map(regime_by_date).fillna("Neutral").reset_index(drop=True)

        trades = backtest_ticker(df, cfg, params, regime_series=reg_series)
        s = stats(trades)
        all_trades.extend(trades)

        if s["trades"] == 0:
            per_ticker_rows.append([tk, 0, "—", "—", "—", "—", "—", "—"])
        else:
            per_ticker_rows.append([
                tk, s["trades"], f"{s['win_rate']:.0f}%",
                f"{s['avg_r']:+.3f}", f"{s['total_r']:+.1f}",
                f"{s['pf']:.2f}", f"{s['max_dd']:+.1f}", f"{s['avg_hold']:.0f}",
            ])

    print("\nPER-TICKER RESULTS")
    print(tabulate(
        per_ticker_rows,
        headers=["Ticker", "Trades", "Win%", "Avg R", "Total R",
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
        "weekly_confirm must be forced off — signal_core BLOCKS every bar " \
        "otherwise, since no weekly data is fetched here"
    assert params.adx_min == 35.0 and params.atr_stop_mult == 1.25
    print("build_signal_params    : weekly_confirm forced off, tunables mapped through")

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
    full = compute(raw)
    tail = raw.tail(len(full)).reset_index(drop=True)
    for col in ("Open", "Date"):
        full[col] = tail[col].values
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
    a = p.parse_args()
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
        cooldown_bars = a.cooldown,
    )
    return cfg, a.selftest


if __name__ == "__main__":
    _cfg, _selftest = parse_args()
    if _selftest:
        sys.exit(selftest())
    sys.exit(run(_cfg) or 0)
