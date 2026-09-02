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
    if df is None or "Date" not in df.columns or df.empty:
        return df
    today_et = datetime.now(_ET).date()
    dates = pd.to_datetime(df["Date"]).dt.date
    keep_mask = dates < today_et
    n_dropped = int((~keep_mask).sum())
    if n_dropped:
        df = df[keep_mask].reset_index(drop=True)
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
            spy_c = compute(spy_raw)
            spy_c = spy_c.merge(spy_raw[["Date"]], left_index=True,
                                right_index=True, how="left")
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
