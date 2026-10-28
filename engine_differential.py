#!/usr/bin/env python3
"""
engine_differential.py — the research engine and the forward grader must agree, on real bars
============================================================================================

Two engines price a trade in this repo: backtest.simulate_trade() (every
committed result in results/) and forward_log.resolve_signal() (every outcome
in the forward record). Their rules are pinned to each other in prose and
in two selftests; this module pins them to each other on DATA. For every
daily frame frozen in .bar_cache it draws random signal bars with random
long and short geometries, runs both engines with the same costs and hold,
and reports every disagreement in outcome or R. The geometries are random,
not a strategy's, so this measures the engines, never a signal.

Run on the laptop (reads the cache only; fetches nothing):

    python engine_differential.py | tee results/engine_differential_run1.txt
    python engine_differential.py --selftest        # offline, synthetic frames
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import sys

import numpy as np
import pandas as pd

import backtest as bt
import forward_log as fl

TOL_R = 5e-5               # resolve_signal() records R to four decimals
PER_FRAME = 40             # random setups per cached frame
SEED = 7


def bars_rows(df: pd.DataFrame) -> list:
    """scanner.bars_for_grading() on a frame carrying Date as a column or
    an index; imported lazily so the laptop run uses the production path."""
    from scanner import bars_for_grading
    d = df
    if "Date" in d.columns:
        d = d.copy()
        d.index = pd.DatetimeIndex(pd.to_datetime(d["Date"]))
        d = d.drop(columns=["Date"])
    return bars_for_grading(d)


def compare_frame(df: pd.DataFrame, n_setups: int, rng: random.Random,
                  resolve=fl.resolve_signal) -> dict:
    """Random setups on one frame through both engines. Returns counts and
    the list of mismatches."""
    d = df.copy()
    if "Date" in d.columns:
        d.index = pd.DatetimeIndex(pd.to_datetime(d["Date"]))
        d = d.drop(columns=["Date"])
    n = len(d)
    bars = bars_rows(d)
    dates = [str(x.date()) for x in d.index]
    cfg = {"slippage_bps": fl.GRADE_SLIPPAGE_BPS, "commission": 0.0, "max_hold": fl.GRADE_MAX_HOLD}
    # "Today" is the day after the frame's last bar: the grader's dead-name
    # rule (forward_log F3, 2026-10-28) voids a window whose last bar is
    # GRADE_NO_BARS_DAYS stale, so a far-future clock would void every
    # end-of-frame setup the engine still reports as settled.
    far = d.index[-1].date() + dt.timedelta(days=1)
    out = {"compared": 0, "void_both": 0, "unsettled_both": 0, "mismatches": []}
    hi, lo, cl = d["High"].to_numpy(float), d["Low"].to_numpy(float), d["Close"].to_numpy(float)
    for _ in range(n_setups):
        i = rng.randint(10, n - 2)
        trend = rng.choice(["Bullish", "Bearish"])
        px = float(cl[i])
        atr = float(np.mean(hi[i - 9:i + 1] - lo[i - 9:i + 1])) or px * 0.01
        k, m = rng.uniform(0.5, 2.5), rng.uniform(0.8, 5.0)
        stop, target = (px - k * atr, px + m * atr) if trend == "Bullish" else (px + k * atr, px - m * atr)
        res = bt.simulate_trade(d, i, {"trend": trend, "entry": px, "stop": stop,
                                       "target": target, "rr": m / k}, cfg)
        row = {"trend": trend, "bar_date": dates[i], "setup": {"price": px, "stop": stop, "target": target}}
        got = resolve(row, bars, today=far)
        if not res.get("filled"):
            if got and got["outcome"] == "void" and got.get("reason") == "gapped_before_fill":
                out["void_both"] += 1
            else:
                out["mismatches"].append((dates[i], trend, "engine unfilled", got))
            continue
        if got is None:
            if i + 1 + fl.GRADE_MAX_HOLD > n:
                out["unsettled_both"] += 1
            else:
                out["mismatches"].append((dates[i], trend, "grader unsettled, engine settled", res["outcome"]))
            continue
        exp = res["outcome"]
        if exp == "timeout":
            exp = "win" if res["r"] > fl.GRADE_DUST_R else ("loss" if res["r"] < -fl.GRADE_DUST_R else "breakeven")
        out["compared"] += 1
        if got["outcome"] != exp or abs(float(got["r"]) - float(res["r"])) > TOL_R:
            out["mismatches"].append((dates[i], trend, exp, round(res["r"], 5), got["outcome"], got["r"]))
    return out


def cached_daily_frames() -> list[tuple[str, pd.DataFrame, dict]]:
    import bar_cache
    frames = []
    for key, meta in sorted(bar_cache._load_manifest().items()):
        if meta.get("interval") != "1d":
            continue
        df, m = bar_cache.load(meta["ticker"], "1d", int(meta["years"]), meta.get("variant", ""))
        if df is not None and len(df) > 60:
            frames.append((key, df, m))
    return frames


def run(per_frame: int = PER_FRAME, seed: int = SEED) -> int:
    import bar_cache
    frames = cached_daily_frames()
    print("=" * 78)
    print("ENGINE DIFFERENTIAL — backtest.simulate_trade vs forward_log.resolve_signal, real bars")
    print("=" * 78)
    if not frames:
        print("  no daily frames in .bar_cache; run a backtest first (they freeze the bars)")
        return 2
    rng = random.Random(seed)
    tot = {"compared": 0, "void_both": 0, "unsettled_both": 0}
    mism = []
    for key, df, meta in frames:
        r = compare_frame(df, per_frame, rng)
        for k in tot:
            tot[k] += r[k]
        mism += [(key,) + m for m in r["mismatches"]]
        print(f"  {key:<28} rows {len(df):>5}  compared {r['compared']:>3}  void {r['void_both']:>2}  "
              f"mismatches {len(r['mismatches'])}")
    print(f"\n  frames {len(frames)}   fingerprint {bar_cache.fingerprint([m for _, _, m in frames])}")
    print(f"  compared {tot['compared']}   void in both {tot['void_both']}   unsettled in both {tot['unsettled_both']}")
    print(f"  mismatches {len(mism)} at {TOL_R} R")
    for m in mism[:20]:
        print(f"    {m}")
    print("  VERDICT: " + ("the engines AGREE" if not mism else "the engines DISAGREE — read the rows above"))
    print("=" * 78)
    return 0 if not mism else 1


def _synthetic(seed: int, n: int = 90) -> pd.DataFrame:
    g = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(g.normal(0.0005, 0.02, n)))
    opn = np.concatenate([[100.0], close[:-1]]) * (1 + g.normal(0, 0.008, n))
    hi = np.maximum(opn, close) * (1 + np.abs(g.normal(0, 0.006, n)))
    lo = np.minimum(opn, close) * (1 - np.abs(g.normal(0, 0.006, n)))
    return pd.DataFrame({"Date": pd.bdate_range("2026-01-02", periods=n), "Open": opn, "High": hi,
                         "Low": lo, "Close": close, "Volume": 1e6})


def selftest() -> int:
    print("engine_differential.py selftest")
    print("=" * 72)
    rng = random.Random(1)
    tot = {"compared": 0, "void_both": 0, "unsettled_both": 0}
    mism = []
    for s in range(12):
        r = compare_frame(_synthetic(s), 50, rng)
        for k in tot:
            tot[k] += r[k]
        mism += r["mismatches"]
    assert tot["compared"] >= 400 and tot["void_both"] >= 1, tot
    assert not mism, mism[:3]
    print(f"agreement        : {tot['compared']} random setups on 12 synthetic frames, "
          f"{tot['void_both']} gapped fills void in both, 0 mismatches")
    # The comparator can fail: a grader that misreports R by one cent in R is caught.
    def _skewed(row, bars, today=None):
        g = fl.resolve_signal(row, bars, today=today)
        if g and g.get("r") is not None:
            g = dict(g, r=round(float(g["r"]) + 0.01, 4))
        return g
    r = compare_frame(_synthetic(3), 30, random.Random(2), resolve=_skewed)
    assert r["mismatches"], "a grader off by 0.01 R must be reported"
    def _mislabel(row, bars, today=None):
        g = fl.resolve_signal(row, bars, today=today)
        if g and g.get("outcome") == "loss":
            g = dict(g, outcome="win")
        return g
    r2 = compare_frame(_synthetic(4), 30, random.Random(2), resolve=_mislabel)
    assert r2["mismatches"], "a relabelled outcome must be reported"
    print("comparator       : an R off by 0.01 and a relabelled outcome are both reported")
    assert TOL_R == 5e-5, "tolerance is the grader's four-decimal rounding, nothing looser"
    print("=" * 72)
    print("All self-tests passed.")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    ap = argparse.ArgumentParser(description="engine differential on the cached daily frames")
    ap.add_argument("--per-frame", type=int, default=PER_FRAME)
    ap.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args()
    return run(a.per_frame, a.seed)


if __name__ == "__main__":
    sys.exit(main())
