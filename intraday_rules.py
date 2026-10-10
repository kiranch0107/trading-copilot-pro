#!/usr/bin/env python3
"""
intraday_rules.py — six chart-reading rules, codified, on 60 days of 5-minute bars
==================================================================================

Phase 1 of results/day_trading_screen.md, pre-registered in
results/intraday_rules_preregistration.md. Every definition below is that
document's, in code. Read it first; nothing here is tunable without a dated
amendment there.

WHAT THIS ANSWERS
    Does any objective version of trendlines, patterns, Fibonacci, volume or
    RSI carry an edge intraday, net of 5 bps a side, that random entries with
    the same exits do not?

WHAT IS HELD FIXED (the pre-registration's table)
    5-minute regular-hours bars, 60 days, raw, frozen in .bar_cache; 21
    names; one trade per rule per name per session; next-bar-open entry;
    stop first, then target, else FLAT AT THE SESSION'S LAST BAR; gapped
    levels fill at the open; 5 bps a side; EMA9/20, RSI14, ATR14, VWAP,
    ±3-bar pivots, a 6-bar opening range, RVOL over the prior 20 sessions.

THE BENCHMARK
    drift_null's method: random entry bars, same side, same stop and target
    distances, same engine and costs. Twenty fixed-seed draws.

THE BAR (per rule)
    mean R >= +0.15, session-clustered 95% CI above zero, and >= +0.15 R
    over the random null. Rule 5 additionally: the named Fibonacci zone beats
    both control zones by >= +0.15 R.

Run
---
    python intraday_rules.py                 # fetch (or read the cache) and report
    python intraday_rules.py --selftest      # offline, synthetic sessions
"""
from __future__ import annotations

import argparse
import math
import random
import sys

import numpy as np
import pandas as pd

import backtest as bt

try:
    import bar_cache
except Exception:                                   # noqa: BLE001
    bar_cache = None

# ── fixed by the pre-registration ───────────────────────────────────────
SWEEP_7 = ["TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "META", "SPY"]
OOS_12 = ["GOOGL", "AVGO", "AMD", "NFLX", "CRM", "ADBE", "QCOM", "MU",
          "ORCL", "NOW", "PANW", "LRCX"]
UNIVERSE = sorted(set(SWEEP_7 + OOS_12 + ["QQQ"]))          # 20 symbols (SPY is in SWEEP_7)
PERIOD = "60d"
INTERVAL = "5m"
SLIPPAGE_BPS = 5.0
OR_BARS = 6                      # 09:30-10:00
PIVOT_W = 3                      # ±3 bars
RVOL_LOOKBACK = 20               # sessions
RVOL_MIN = 1.5                   # rule 1
VWAP_DEV_ATR = 1.0               # rule 2
DIV_MIN_BARS = 10                # rule 4
FIB_ZONES = {"named": (0.500, 0.618), "control_low": (0.300, 0.418),
             "control_high": (0.700, 0.818)}
BAR_MEAN_R = 0.15
BAR_OVER_NULL = 0.15
NULL_DRAWS = 20
SEED_BASE = 20260
RULES = ("orb", "vwap_reclaim", "ema_pullback", "rsi_divergence",
         "fib_pullback", "trendline_break")


# ── bars ────────────────────────────────────────────────────────────────

def _yahoo_intraday(ticker: str) -> pd.DataFrame | None:
    import yfinance as yf
    df = yf.download(ticker, period=PERIOD, interval=INTERVAL, auto_adjust=False,
                     progress=False, prepost=False, threads=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    df = df.rename(columns={df.columns[0]: "Date"})
    return df[["Date", "Open", "High", "Low", "Close", "Volume"]]


def fetch_intraday(ticker: str) -> pd.DataFrame | None:
    """60 days of 5-minute bars, frozen under their own cache key."""
    if bar_cache is None:
        return _yahoo_intraday(ticker)
    df, _meta, _status = bar_cache.get_or_fetch(
        ticker, INTERVAL, 0, lambda: _yahoo_intraday(ticker), variant=f"{PERIOD}-raw")
    return df


# ── features ────────────────────────────────────────────────────────────

def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """
    One feature set for every rule. Adds: session (date), bar_in_session,
    session_last (index of the session's last bar), EMA9, EMA20, RSI14,
    ATR14, VWAP, pivot_high / pivot_low flags, or_high / or_low (opening
    range), rvol (first-30-minute volume over the prior-20-session median).
    """
    import ta
    d = df.copy().reset_index(drop=True)
    ts = pd.to_datetime(d["Date"])
    try:
        ts = ts.dt.tz_convert("America/New_York")
    except (TypeError, AttributeError):
        pass
    d["session"] = ts.dt.date.astype(str)
    d["bar_in_session"] = d.groupby("session").cumcount()
    d["session_last"] = d.groupby("session")["bar_in_session"].transform("max") + \
        d.index - d["bar_in_session"]
    d["EMA9"] = ta.trend.ema_indicator(d["Close"], 9)
    d["EMA20"] = ta.trend.ema_indicator(d["Close"], 20)
    d["RSI"] = ta.momentum.rsi(d["Close"], 14)
    d["ATR"] = ta.volatility.average_true_range(d["High"], d["Low"], d["Close"], 14)
    tp = (d["High"] + d["Low"] + d["Close"]) / 3.0
    pv = (tp * d["Volume"]).groupby(d["session"]).cumsum()
    vv = d["Volume"].groupby(d["session"]).cumsum().replace(0, np.nan)
    d["VWAP"] = pv / vv
    # A pivot high is STRICTLY above the w bars to its left and at least equal
    # to the w bars to its right, so a tie at the extreme flags the earlier bar
    # once rather than every tied bar. Mirrored for lows.
    w = PIVOT_W
    hi, lo = d["High"], d["Low"]
    left_hi = hi.shift(1).rolling(w).max()
    right_hi = hi[::-1].shift(1).rolling(w).max()[::-1]
    left_lo = lo.shift(1).rolling(w).min()
    right_lo = lo[::-1].shift(1).rolling(w).min()[::-1]
    d["pivot_high"] = (hi > left_hi) & (hi >= right_hi)
    d["pivot_low"] = (lo < left_lo) & (lo <= right_lo)
    # pivots are only KNOWN w bars later; the rules read them with that lag
    first = d.groupby("session").head(OR_BARS)
    orr = first.groupby("session").agg(or_high=("High", "max"), or_low=("Low", "min"),
                                       or_vol=("Volume", "sum"))
    d = d.join(orr, on="session")
    med = orr["or_vol"].shift(1).rolling(RVOL_LOOKBACK, min_periods=5).median()
    d["rvol"] = d["session"].map(orr["or_vol"] / med)
    return d


def _known_pivots(d: pd.DataFrame, i: int, kind: str, lo_idx: int) -> list[int]:
    """Pivot indices of `kind` ("high"/"low") confirmed by bar i (i.e. at most
    i - PIVOT_W), at or after lo_idx."""
    col = "pivot_high" if kind == "high" else "pivot_low"
    s = d[col].iloc[lo_idx:max(lo_idx, i - PIVOT_W + 1)]
    return [int(k) for k in s.index[s.values]]


def _session_bounds(d: pd.DataFrame, i: int) -> tuple[int, int]:
    last = int(d["session_last"].iloc[i])
    first = last - int(d["bar_in_session"].iloc[last])
    return first, last


# ── the six detectors ───────────────────────────────────────────────────
# Each returns at most ONE setup per session for its rule, scanning bars in
# order and firing on the first qualifying bar. A setup is
# {"rule", "signal_i", "trend", "entry", "stop", "target", "rr", "feature"}.

def _setup(rule, i, trend, entry, stop, target, feature=None):
    risk = abs(entry - stop)
    if risk <= 0 or not all(map(math.isfinite, (entry, stop, target))):
        return None
    if (trend == "Bullish" and not (stop < entry < target)) or \
       (trend == "Bearish" and not (target < entry < stop)):
        return None
    return {"rule": rule, "signal_i": int(i), "trend": trend, "entry": float(entry),
            "stop": float(stop), "target": float(target),
            "rr": round(abs(target - entry) / risk, 3), "feature": feature}


def detect_orb(d, first, last):
    rv = d["rvol"].iloc[first]
    if not (rv == rv and rv >= RVOL_MIN):
        return None
    oh, ol = d["or_high"].iloc[first], d["or_low"].iloc[first]
    for i in range(first + OR_BARS, last - 1):
        c = d["Close"].iloc[i]
        if c > oh:
            return _setup("orb", i, "Bullish", c, ol, c + 2 * (c - ol), feature=float(rv))
        if c < ol:
            return _setup("orb", i, "Bearish", c, oh, c - 2 * (oh - c), feature=float(rv))
    return None


def detect_vwap_reclaim(d, first, last):
    dev_lo = dev_hi = None       # index of the extreme since a ≥1 ATR deviation
    for i in range(first + 1, last - 1):
        c, v, a = d["Close"].iloc[i], d["VWAP"].iloc[i], d["ATR"].iloc[i]
        if not (a == a and a > 0 and v == v):
            continue
        if c <= v - VWAP_DEV_ATR * a:
            dev_lo = i if dev_lo is None or d["Low"].iloc[i] < d["Low"].iloc[dev_lo] else dev_lo
        elif dev_lo is not None and c > v:
            ext = float(d["Low"].iloc[dev_lo:i + 1].min())
            return _setup("vwap_reclaim", i, "Bullish", c, ext, v + a, feature=float((v - ext) / a))
        if c >= v + VWAP_DEV_ATR * a:
            dev_hi = i if dev_hi is None or d["High"].iloc[i] > d["High"].iloc[dev_hi] else dev_hi
        elif dev_hi is not None and c < v:
            ext = float(d["High"].iloc[dev_hi:i + 1].max())
            return _setup("vwap_reclaim", i, "Bearish", c, ext, v - a, feature=float((ext - v) / a))
    return None


def _trend(d, i, lo_idx):
    ph = _known_pivots(d, i, "high", lo_idx)
    pl = _known_pivots(d, i, "low", lo_idx)
    if len(ph) >= 2 and len(pl) >= 2:
        H, L = d["High"].values, d["Low"].values
        if H[ph[-1]] > H[ph[-2]] and L[pl[-1]] > L[pl[-2]]:
            return "up", pl[-1], ph[-1]
        if H[ph[-1]] < H[ph[-2]] and L[pl[-1]] < L[pl[-2]]:
            return "down", pl[-1], ph[-1]
    return None, None, None


def detect_ema_pullback(d, first, last):
    for i in range(first + OR_BARS, last - 1):
        tr, last_pl, last_ph = _trend(d, i, first)
        if tr is None:
            continue
        c, e9, e20 = d["Close"].iloc[i], d["EMA9"].iloc[i], d["EMA20"].iloc[i]
        if tr == "up" and d["Low"].iloc[i] <= e20 and c > e9:
            stop = float(d["Low"].iloc[last_pl])
            s = _setup("ema_pullback", i, "Bullish", c, stop, c + 2 * (c - stop))
            if s: return s
        if tr == "down" and d["High"].iloc[i] >= e20 and c < e9:
            stop = float(d["High"].iloc[last_ph])
            s = _setup("ema_pullback", i, "Bearish", c, stop, c - 2 * (stop - c))
            if s: return s
    return None


def detect_rsi_divergence(d, first, last):
    L, H, R = d["Low"].values, d["High"].values, d["RSI"].values
    for i in range(first + OR_BARS, last - 1):
        pl = _known_pivots(d, i, "low", first)
        if len(pl) >= 2 and pl[-1] - pl[-2] >= DIV_MIN_BARS and pl[-1] >= first:
            a, b = pl[-2], pl[-1]
            if L[b] < L[a] and R[b] == R[b] and R[a] == R[a] and R[b] > R[a] and b == i - PIVOT_W:
                c = d["Close"].iloc[i]
                s = _setup("rsi_divergence", i, "Bullish", c, float(L[b]), c + 2 * (c - L[b]),
                           feature=float(R[b] - R[a]))
                if s: return s
        ph = _known_pivots(d, i, "high", first)
        if len(ph) >= 2 and ph[-1] - ph[-2] >= DIV_MIN_BARS and ph[-1] >= first:
            a, b = ph[-2], ph[-1]
            if H[b] > H[a] and R[b] == R[b] and R[a] == R[a] and R[b] < R[a] and b == i - PIVOT_W:
                c = d["Close"].iloc[i]
                s = _setup("rsi_divergence", i, "Bearish", c, float(H[b]), c - 2 * (H[b] - c),
                           feature=float(R[a] - R[b]))
                if s: return s
    return None


def fib_zone(retrace: float) -> str | None:
    for name, (lo, hi) in FIB_ZONES.items():
        if lo <= retrace <= hi:
            return name
    return None


def detect_fib_pullback(d, first, last):
    L, H, C = d["Low"].values, d["High"].values, d["Close"].values
    for i in range(first + OR_BARS, last - 1):
        pl = _known_pivots(d, i, "low", first)
        ph = _known_pivots(d, i, "high", first)
        if pl and ph and ph[-1] > pl[-1] and H[ph[-1]] > L[pl[-1]]:        # bull impulse
            lo, hi = L[pl[-1]], H[ph[-1]]
            retr = (hi - C[i - 1]) / (hi - lo)
            z = fib_zone(retr)
            if z and C[i] > C[i - 1] and C[i] < hi:
                return _setup("fib_pullback", i, "Bullish", C[i], float(lo), float(hi), feature=z)
        if pl and ph and pl[-1] > ph[-1] and L[pl[-1]] < H[ph[-1]]:        # bear impulse
            hi, lo = H[ph[-1]], L[pl[-1]]
            retr = (C[i - 1] - lo) / (hi - lo)
            z = fib_zone(retr)
            if z and C[i] < C[i - 1] and C[i] > lo:
                return _setup("fib_pullback", i, "Bearish", C[i], float(hi), float(lo), feature=z)
    return None


def _regress(xs, ys):
    n = len(xs); mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None, None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return b, my - b * mx


def detect_trendline_break(d, first, last):
    L, H, C = d["Low"].values, d["High"].values, d["Close"].values
    for i in range(first + OR_BARS, last - 1):
        pl = _known_pivots(d, i, "low", first)
        ph = _known_pivots(d, i, "high", first)
        if len(pl) >= 3:
            xs = pl[-3:]; b, a = _regress(xs, [L[x] for x in xs])
            if b is not None and b > 0 and C[i] < a + b * i:
                stop = float(H[ph[-1]]) if ph else float(max(H[xs[-1]:i + 1]))
                s = _setup("trendline_break", i, "Bearish", C[i], stop, C[i] - 2 * (stop - C[i]),
                           feature=float(b))
                if s: return s
        if len(ph) >= 3:
            xs = ph[-3:]; b, a = _regress(xs, [H[x] for x in xs])
            if b is not None and b < 0 and C[i] > a + b * i:
                stop = float(L[pl[-1]]) if pl else float(min(L[xs[-1]:i + 1]))
                s = _setup("trendline_break", i, "Bullish", C[i], stop, C[i] + 2 * (C[i] - stop),
                           feature=float(b))
                if s: return s
    return None


DETECTORS = {"orb": detect_orb, "vwap_reclaim": detect_vwap_reclaim,
             "ema_pullback": detect_ema_pullback, "rsi_divergence": detect_rsi_divergence,
             "fib_pullback": detect_fib_pullback, "trendline_break": detect_trendline_break}


# ── simulation ──────────────────────────────────────────────────────────

def simulate(d: pd.DataFrame, setup: dict, tally: dict | None = None) -> dict | None:
    """One setup through backtest.simulate_trade, flat at the session's last bar."""
    i = setup["signal_i"]
    _first, last = _session_bounds(d, i)
    if i + 1 > last:
        return None
    cfg = {"slippage_bps": SLIPPAGE_BPS, "commission": 0.0, "max_hold": last - i}
    r = bt.simulate_trade(d, i, {"trend": setup["trend"], "entry": setup["entry"],
                                 "stop": setup["stop"], "target": setup["target"],
                                 "rr": setup["rr"]}, cfg, tally=tally)
    if not r.get("filled"):
        return None
    r.update({"rule": setup["rule"], "session": d["session"].iloc[i],
              "feature": setup.get("feature"), "signal_i": i,
              "exit_i": i + 1 + int(r["hold"])})          # entry is i+1; hold = exit - entry
    return r


def run_rules(d: pd.DataFrame, ticker: str, tally: dict | None = None) -> list[dict]:
    """Every rule on every session of one prepared frame; one position per
    name at a time across rules (a setup inside an open trade is skipped)."""
    out = []
    sessions = d.groupby("session").indices
    busy_until = -1
    for sess in sorted(sessions):
        idx = sessions[sess]
        first, last = int(idx[0]), int(idx[-1])
        if last - first < OR_BARS + 4:
            continue
        setups = [s for s in (f(d, first, last) for f in DETECTORS.values()) if s]
        for s in sorted(setups, key=lambda s: s["signal_i"]):
            if s["signal_i"] <= busy_until:
                if tally is not None:
                    tally["skipped: position open"] = tally.get("skipped: position open", 0) + 1
                continue
            r = simulate(d, s, tally)
            if r:
                r["ticker"] = ticker
                out.append(r)
                busy_until = r["exit_i"]
    return out


def random_null(d: pd.DataFrame, trades: list[dict], draw: int) -> list[dict]:
    """drift_null's method: same count, side, stop and target DISTANCES per
    (rule, session); the entry bar is random inside the session."""
    rng = random.Random(SEED_BASE + draw)
    out = []
    for t in trades:
        first, last = _session_bounds(d, t["signal_i"])
        lo, hi = first + OR_BARS, last - 4
        if hi <= lo:
            continue
        j = rng.randint(lo, hi)
        c = float(d["Close"].iloc[j])
        s = t["setup"]
        risk = abs(s["entry"] - s["stop"]); reward = abs(s["target"] - s["entry"])
        if t["trend"] == "Bullish":
            stop, target = c - risk, c + reward
        else:
            stop, target = c + risk, c - reward
        n = _setup(t["rule"], j, t["trend"], c, stop, target)
        if n is None:
            continue
        r = simulate(d, n)
        if r:
            out.append(r)
    return out


# ── statistics ──────────────────────────────────────────────────────────

def clustered(trades: list[dict]) -> dict:
    """Mean R with a session-clustered 95% CI: one cluster per session."""
    if not trades:
        return {"n": 0, "sessions": 0, "mean": float("nan"), "lo": float("nan"), "hi": float("nan")}
    rs = np.array([t["r"] for t in trades], dtype=float)
    by = {}
    for t in trades:
        by.setdefault(t["session"], []).append(t["r"])
    means = np.array([np.mean(v) for v in by.values()])
    S = len(means)
    se = float(means.std(ddof=1) / math.sqrt(S)) if S > 1 else float("nan")
    m = float(rs.mean())
    return {"n": len(rs), "sessions": S, "mean": m, "se": se,
            "lo": m - 1.96 * se if S > 1 else float("nan"),
            "hi": m + 1.96 * se if S > 1 else float("nan")}


def verdict(real: dict, null_mean: float, extra_ok: bool = True) -> tuple[bool, list[str]]:
    notes = []
    c1 = real["n"] > 0 and real["mean"] >= BAR_MEAN_R
    c2 = real["n"] > 0 and real["lo"] == real["lo"] and real["lo"] > 0
    c3 = real["n"] > 0 and null_mean == null_mean and real["mean"] - null_mean >= BAR_OVER_NULL
    notes.append(f"mean R {real['mean']:+.3f} {'>=' if c1 else '<'} {BAR_MEAN_R:+.2f}")
    notes.append(f"clustered CI lower {real['lo']:+.3f} {'>' if c2 else '<='} 0 "
                 f"({real['sessions']} session clusters)")
    notes.append(f"over random null {real['mean'] - null_mean:+.3f} "
                 f"{'>=' if c3 else '<'} {BAR_OVER_NULL:+.2f}")
    if not extra_ok:
        notes.append("rule-specific clause failed")
    return bool(c1 and c2 and c3 and extra_ok), notes


def terciles(trades: list[dict], key=lambda t: t["feature"]) -> list[tuple[str, int, float]]:
    xs = sorted((t for t in trades if isinstance(key(t), (int, float))), key=key)
    if len(xs) < 6:
        return []
    k = len(xs) // 3
    parts = [xs[:k], xs[k:2 * k], xs[2 * k:]]
    return [(f"T{i + 1} [{key(p[0]):.2f}..{key(p[-1]):.2f}]", len(p),
             float(np.mean([t["r"] for t in p]))) for i, p in enumerate(parts)]


# ── report ──────────────────────────────────────────────────────────────

def analyse(all_trades: list[dict], frames: dict, fingerprint: str | None = None) -> int:
    print("=" * 78)
    print("SIX INTRADAY RULES ON 5-MINUTE BARS — results/intraday_rules_preregistration.md")
    print("=" * 78)
    print(f"  names {len(frames)}   sessions {len({t['session'] for t in all_trades}) if all_trades else 0}"
          f"   costs {SLIPPAGE_BPS:g} bps/side   flat at the close   fingerprint {fingerprint or 'n/a'}")
    worst_clear = []
    for rule in RULES:
        real = [t for t in all_trades if t["rule"] == rule]
        st = clustered(real)
        nulls = []
        for draw in range(NULL_DRAWS):
            draw_trades = []
            for tk, d in frames.items():
                draw_trades += random_null(d, [t for t in real if t["ticker"] == tk], draw)
            if draw_trades:
                nulls.append(float(np.mean([t["r"] for t in draw_trades])))
        null_mean = float(np.mean(nulls)) if nulls else float("nan")
        pct = (100.0 * sum(1 for x in nulls if x < st["mean"]) / len(nulls)) if nulls and real else float("nan")
        print(f"\n  {rule}")
        print(f"    setups {st['n']:>5}   mean R {st['mean']:+.3f}   95% CI [{st['lo']:+.3f}, {st['hi']:+.3f}]"
              f"   wins {sum(1 for t in real if t['outcome'] == 'win')}   timeouts {sum(1 for t in real if t['outcome'] == 'timeout')}")
        print(f"    random null {null_mean:+.3f} over {len(nulls)} draws; real at percentile {pct:.0f}")
        extra_ok = True
        if rule == "orb" and real:
            for lab, n, m in terciles(real):
                print(f"    RVOL {lab:<22} n {n:>4}  mean R {m:+.3f}")
        if rule == "fib_pullback" and real:
            zones = {}
            for t in real:
                zones.setdefault(t["feature"], []).append(t["r"])
            for z in ("named", "control_low", "control_high"):
                v = zones.get(z, [])
                print(f"    zone {z:<13} n {len(v):>4}  mean R {np.mean(v) if v else float('nan'):+.3f}")
            nm = np.mean(zones.get("named", [float('nan')]))
            extra_ok = all(nm - np.mean(zones.get(z, [float('nan')])) >= BAR_OVER_NULL
                           for z in ("control_low", "control_high")) if zones.get("named") else False
        ok, notes = verdict(st, null_mean, extra_ok)
        for n in notes:
            print(f"      - {n}")
        print(f"    VERDICT: {'CLEARS (one pre-stated confirmation window owed)' if ok else 'does not clear'}")
        worst_clear.append(ok)
    print("\n" + "=" * 78)
    if any(worst_clear):
        print("  At least one rule clears its bar on this window. Per the pre-registration")
        print("  it gets ONE confirmation window: the next 60 days, same code, same bar.")
    else:
        print("  No rule clears. On these names, over this window, net of costs, none of the")
        print("  six objective versions of the proposed tools beat random entry by the bar.")
    print("=" * 78)
    return 0


def run(tickers: list[str]) -> int:
    frames, trades, tally = {}, [], {}
    metas = []
    for tk in tickers:
        raw = fetch_intraday(tk)
        if raw is None or len(raw) < 200:
            print(f"  ! {tk}: no intraday bars", file=sys.stderr)
            continue
        d = prepare(raw)
        frames[tk] = d
        trades += run_rules(d, tk, tally)
        print(f"  {tk}: {len(d)} bars, {d['session'].nunique()} sessions, "
              f"{sum(1 for t in trades if t['ticker'] == tk)} trades", file=sys.stderr)
        if bar_cache is not None:
            _df, meta = bar_cache.load(tk, INTERVAL, 0, f"{PERIOD}-raw")
            if meta:
                metas.append(meta)
    fp = bar_cache.fingerprint(metas) if (bar_cache is not None and metas) else None
    if tally:
        print(f"  engine tally: {tally}", file=sys.stderr)
    return analyse(trades, frames, fp)


# ── self-test on synthetic sessions ─────────────────────────────────────

def _session_frame(closes: list[float], day: str, vol: float = 1e5, spread: float = 0.2,
                   vols: list[float] | None = None) -> pd.DataFrame:
    n = len(closes)
    ts = pd.date_range(f"{day} 09:30", periods=n, freq="5min", tz="America/New_York")
    c = np.array(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"Date": ts, "Open": o, "High": np.maximum(o, c) + spread,
                         "Low": np.minimum(o, c) - spread, "Close": c,
                         "Volume": vols if vols is not None else [vol] * n})


def _flat_days(n_days: int, start_day: int = 1, level: float = 100.0, bars: int = 78,
               vol: float = 1e5) -> list[pd.DataFrame]:
    out = []
    for k in range(n_days):
        day = f"2026-09-{start_day + k:02d}"
        rng = random.Random(k)
        closes = [level + 0.05 * math.sin(i / 4.0) + rng.uniform(-0.03, 0.03) for i in range(bars)]
        out.append(_session_frame(closes, day, vol=vol))
    return out


def selftest() -> int:
    print("intraday_rules.py selftest")
    print("=" * 72)
    # 25 quiet sessions so RVOL has a lookback, then one shaped session per rule.
    days = _flat_days(25, 1)
    # Rule 1: ORB with RVOL 3x, breakout at bar 8, runs to target.
    orb_closes = [100.0 + 0.02 * i for i in range(6)] + [100.5, 101.2, 101.6, 102.0, 102.6, 103.2] + \
                 [103.3] * 66
    orb = _session_frame(orb_closes, "2026-09-26", vol=1e5,
                         vols=[3e5] * 6 + [1e5] * 72)
    # Rule 2: VWAP reclaim — a deep dip then a close back above VWAP, then up.
    # The reclaim bar must close above VWAP but BELOW VWAP + 1 ATR, or the
    # target is already behind the entry and the setup is (correctly) void.
    vw = [100.0] * 10 + [99.0, 98.2, 97.6, 97.4] + [98.0, 98.9, 99.6, 100.0, 100.4, 100.9, 101.5] + [101.5] * 57
    vwap_day = _session_frame(vw, "2026-09-27", spread=0.3)
    # Rule 4: RSI divergence — two pivot lows 14 bars apart, lower low, then rally.
    rsi = ([100 - 0.4 * i for i in range(8)] + [96.8 + 0.5 * i for i in range(6)] +
           [99.8 - 0.55 * i for i in range(8)] + [95.6 + 0.6 * i for i in range(12)] + [102.8] * 44)
    rsi_day = _session_frame(rsi, "2026-09-28", spread=0.1)
    # Rule 5: Fibonacci — impulse 100 -> 110, retrace to the 55% level, bounce.
    fib = ([100 + 1.0 * i for i in range(11)] + [110 - 0.9 * i for i in range(7)] +
           [104.6, 105.4, 106.5, 107.8, 109.0, 109.6] + [109.6] * 54)
    fib_day = _session_frame(fib, "2026-09-29", spread=0.1)
    # Rule 6: trendline — three rising pivot lows then a break below the line.
    # Three rising pivot lows (at 100.0, 101.0, 102.0) with rising highs, then
    # a close below the regression line through those lows.
    tl = []
    for k in range(3):
        tl += [100 + k * 1.0 + x for x in (0.8, 0.4, 0.0, 0.3, 0.9, 1.4, 1.8, 1.5)]
    tl += [103.0, 102.6, 102.2, 101.6, 101.0, 100.2] + [100.0] * 48
    tl_day = _session_frame(tl, "2026-09-30", spread=0.1)
    # Rule 3: EMA pullback — rising pivots, a touch of EMA20 with close above EMA9.
    # Four rising swings (peak 1.6 so no two pivot highs tie), then a pullback
    # whose LOW reaches EMA20 while the CLOSE holds above EMA9 — a wick, which
    # the wider spread on this day supplies.
    ema = []
    for k in range(4):
        ema += [100 + k * 0.9 + x for x in (0.6, 0.3, 0.0, 0.35, 0.8, 1.2, 1.6, 1.3)]
    ema += [103.9, 103.7, 103.7, 104.0, 104.5, 105.2, 106.0, 106.8] + [107.0] * 38
    ema_day = _session_frame(ema, "2026-10-01", spread=0.8)

    # The SAME breakout shape on ordinary volume: RVOL ~1.0, so rule 1 must
    # refuse it — the volume leg is the mechanism, not decoration.
    orb_quiet = _session_frame(orb_closes, "2026-10-02", vol=1e5)
    raw = pd.concat(days + [orb, vwap_day, rsi_day, fib_day, tl_day, ema_day, orb_quiet],
                    ignore_index=True)
    d = prepare(raw)
    assert d["session"].nunique() == 32 and d["bar_in_session"].max() == 77
    assert (d.groupby("session")["session_last"].nunique() == 1).all(), "session_last is per session"
    assert abs(d["rvol"].iloc[d.index[d["session"] == "2026-09-26"][0]] - 3.0) < 0.05, "RVOL 3x at the open"
    print("features         : sessions, opening range, RVOL, VWAP, pivots, EMA/RSI/ATR computed")

    # DETECTOR BY DETECTOR, on its shaped session, with the expected side.
    # (Driven directly: in run_rules() another rule firing earlier in the
    # same session takes the one position, which is the one-position rule
    # doing its job, not the detector failing.)
    def _bounds(day):
        idx = d.index[d["session"] == day]
        return int(idx[0]), int(idx[-1])
    shaped = (("orb", "2026-09-26", "Bullish"), ("vwap_reclaim", "2026-09-27", "Bullish"),
              ("rsi_divergence", "2026-09-28", "Bullish"), ("fib_pullback", "2026-09-29", "Bullish"),
              ("trendline_break", "2026-09-30", "Bearish"), ("ema_pullback", "2026-10-01", "Bullish"))
    for rule, day, side in shaped:
        st = DETECTORS[rule](d, *_bounds(day))
        assert st is not None and st["trend"] == side, (rule, day, st)
        assert st["rr"] > 0 and (st["stop"] < st["entry"]) == (side == "Bullish"), st
    o = detect_orb(d, *_bounds("2026-09-26"))
    assert o["feature"] > 2.9 and simulate(d, o)["outcome"] == "win", o
    assert detect_fib_pullback(d, *_bounds("2026-09-29"))["feature"] == "named"
    assert detect_orb(d, *_bounds("2026-09-10")) is None, "RVOL 1.0 on a quiet day: no ORB setup"
    assert detect_orb(d, *_bounds("2026-10-02")) is None, (
        "the same breakout on ordinary volume must NOT fire: RVOL is the rule's mechanism")
    print("detectors        : each of the six fires on its shaped session, right side; "
          "ORB refuses a quiet open")

    tally: dict = {}
    trades = run_rules(d, "SYN", tally)
    by_rule = {}
    for t in trades:
        by_rule.setdefault(t["rule"], []).append(t)
    assert trades and all("exit_i" in t and t["exit_i"] <= int(d["session_last"].iloc[t["signal_i"]])
                          for t in trades), "every trade exits inside its own session"
    per_day = {}
    for t in trades:
        per_day[t["session"]] = per_day.get(t["session"], 0) + 1
    assert tally.get("skipped: position open", 0) >= 1, tally
    # No two trades on one day overlap in time.
    for day in per_day:
        ts = sorted((t["signal_i"], t["exit_i"]) for t in trades if t["session"] == day)
        assert all(b[0] > a[1] for a, b in zip(ts, ts[1:])), (day, ts)
    print(f"run_rules        : {len(trades)} trades over {len(per_day)} sessions, one position "
          f"at a time ({tally.get('skipped: position open', 0)} setups skipped while open)")

    # Flat at the close: a trade that never hits a level exits on the session's last bar.
    quiet = d.index[d["session"] == "2026-09-10"]
    s = _setup("orb", int(quiet[20]), "Bullish", 100.0, 90.0, 130.0)
    r = simulate(d, s)
    assert r and r["outcome"] == "timeout" and r["exit_i"] == int(quiet[-1]), (r["exit_i"], int(quiet[-1]))
    s2 = _setup("orb", int(quiet[-1]), "Bullish", 100.0, 90.0, 130.0)
    assert simulate(d, s2) is None, "a signal on the session's last bar cannot fill next-open"
    print("flat at the close: exits on the session's last bar, never the next day")

    # One position per name: a second setup inside an open trade is skipped.
    assert tally.get("skipped: position open", 0) >= 1 or len(trades) <= 6 * 31, tally
    # Zones are disjoint and the named one is where the ratios say.
    assert fib_zone(0.55) == "named" and fib_zone(0.35) == "control_low" and fib_zone(0.75) == "control_high"
    assert fib_zone(0.45) is None and fib_zone(0.65) is None, "gaps between zones keep them disjoint"
    zs = list(FIB_ZONES.values())
    assert all(abs((h - l) - 0.118) < 1e-9 for l, h in zs), "equal width"
    print("fib zones        : named 50-61.8, controls 30-41.8 / 70-81.8, equal width, disjoint")

    # Random null: fixed seeds reproduce; different draws differ; same geometry.
    n1 = random_null(d, trades, 0); n2 = random_null(d, trades, 0); n3 = random_null(d, trades, 1)
    assert [t["signal_i"] for t in n1] == [t["signal_i"] for t in n2], "seed 0 must reproduce"
    assert [t["signal_i"] for t in n1] != [t["signal_i"] for t in n3], "draws must differ"
    for t, n in zip(trades, n1):
        assert n["trend"] == t["trend"] and n["session"] == t["session"]
        assert abs(abs(n["setup"]["entry"] - n["setup"]["stop"]) - abs(t["setup"]["entry"] - t["setup"]["stop"])) < 1e-6
    print("random null      : fixed seeds reproduce, draws differ, side and geometry preserved")

    # Clustered CI: trades on one day are one cluster.
    fake = [{"r": 1.0, "session": "a"}] * 50 + [{"r": -1.0, "session": "b"}] * 50
    st = clustered(fake)
    assert st["n"] == 100 and st["sessions"] == 2 and abs(st["mean"]) < 1e-9
    assert st["hi"] > 1.0, "two clusters of 50 must not look like 100 observations"
    ok, _ = verdict({"n": 100, "sessions": 60, "mean": 0.30, "lo": 0.05, "hi": 0.55}, 0.05)
    assert ok
    ok2, _ = verdict({"n": 100, "sessions": 60, "mean": 0.30, "lo": 0.05, "hi": 0.55}, 0.20)
    assert not ok2, "beating zero is not the bar; beating random entry by 0.15 is"
    ok3, _ = verdict({"n": 100, "sessions": 60, "mean": 0.30, "lo": -0.01, "hi": 0.61}, 0.0)
    assert not ok3, "a CI touching zero does not clear"
    print("bar              : mean, clustered CI, and margin over the null all required")
    print("=" * 72)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="six intraday rules on 5-minute bars")
    ap.add_argument("--tickers", default=",".join(UNIVERSE))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    return run([t.strip().upper() for t in a.tickers.split(",") if t.strip()])


if __name__ == "__main__":
    sys.exit(main())
