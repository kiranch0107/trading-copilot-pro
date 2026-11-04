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

    python engine_differential.py | tee results/engine_differential_run<N>.txt   # run 2 is the current reading
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
    an index; imported lazily so the laptop run uses the production path.
    A frame with a Dividend column hands the grader the same ex-dates the
    engine reads (forward_log.attach_dividend_rows, amendment I)."""
    from scanner import bars_for_grading
    d = df
    if "Date" in d.columns:
        d = d.copy()
        d.index = pd.DatetimeIndex(pd.to_datetime(d["Date"]))
        d = d.drop(columns=["Date"])
    rows = bars_for_grading(d)
    if "Dividend" in d.columns:
        amounts = {str(ix.date()): float(v) for ix, v in d["Dividend"].items() if v == v and float(v) > 0}
        rows = fl.attach_dividend_rows(rows, amounts)
    return rows


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
    # void_floor: BOTH engines refuse the fill as inside the risk floor
    # (risk_params.min_risk; grading amendment 2026-10-29 and the research
    # engine's floor, owner 2026-10-29). One engine flooring and the other
    # not is a mismatch, like any other disagreement.
    out = {"compared": 0, "void_both": 0, "unsettled_both": 0, "void_floor": 0,
           "mismatches": []}
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
        g_floor = bool(got and got["outcome"] == "void"
                       and (got.get("detail") or {}).get("note") == "fill inside the risk floor")
        if not res.get("filled"):
            if res.get("floor"):
                if g_floor:
                    out["void_floor"] += 1
                else:
                    out["mismatches"].append((dates[i], trend, "engine floored, grader did not", got))
            elif got and got["outcome"] == "void" and got.get("reason") == "gapped_before_fill" and not g_floor:
                out["void_both"] += 1
            else:
                out["mismatches"].append((dates[i], trend, "engine unfilled", got))
            continue
        if g_floor:
            out["mismatches"].append((dates[i], trend, "grader floored, engine filled", round(res["r"], 5)))
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
    frames = cached_daily_frames()
    print("=" * 78)
    print("ENGINE DIFFERENTIAL — backtest.simulate_trade vs forward_log.resolve_signal, real bars")
    _saved = bt.ENGINE_FIXES
    bt.set_engine_fixes(bt.GRADER_FIXES)   # the rules the grader shares (amendments H and I)
    print(f"Engine fixes : {bt.describe_engine()}  (the grader's: amendments H and I)")
    print("=" * 78)
    try:
        return _run_frames(frames, per_frame, seed)
    finally:
        bt.set_engine_fixes(_saved)


def _run_frames(frames, per_frame: int, seed: int) -> int:
    import bar_cache
    if not frames:
        print("  no daily frames in .bar_cache; run a backtest first (they freeze the bars)")
        return 2
    rng = random.Random(seed)
    tot = {"compared": 0, "void_both": 0, "unsettled_both": 0, "void_floor": 0}
    mism = []
    for key, df, meta in frames:
        r = compare_frame(df, per_frame, rng)
        for k in tot:
            tot[k] += r[k]
        mism += [(key,) + m for m in r["mismatches"]]
        print(f"  {key:<28} rows {len(df):>5}  compared {r['compared']:>3}  void {r['void_both']:>2}  "
              f"floor {r['void_floor']:>2}  mismatches {len(r['mismatches'])}")
    print(f"\n  frames {len(frames)}   fingerprint {bar_cache.fingerprint([m for _, _, m in frames])}")
    print(f"  compared {tot['compared']}   void in both {tot['void_both']}   unsettled in both {tot['unsettled_both']}")
    print(f"  void at the risk floor in both engines (2026-10-29): {tot['void_floor']}")
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
    _saved_fixes = bt.ENGINE_FIXES
    bt.set_engine_fixes(bt.GRADER_FIXES)   # the grader's rules: amendments H and I
    try:
        return _selftest_body()
    finally:
        bt.set_engine_fixes(_saved_fixes)


def _selftest_body() -> int:
    rng = random.Random(1)
    tot = {"compared": 0, "void_both": 0, "unsettled_both": 0, "void_floor": 0}
    mism = []
    for s in range(12):
        r = compare_frame(_synthetic(s), 50, rng)
        for k in tot:
            tot[k] += r[k]
        mism += r["mismatches"]
    assert tot["compared"] >= 400 and tot["void_both"] >= 1, tot
    # AMENDMENT I on data: the same frames with an ex-date every ~13 bars of
    # 1 % of price, through both engines; they must still agree, and the
    # dividends must have reached both.
    _dtot, _dm = 0, []
    for s in range(6):
        _fd = _synthetic(s)
        _fd["Dividend"] = [float(_fd["Close"].iloc[k]) * 0.01 if k % 13 == 7 else 0.0 for k in range(len(_fd))]
        r = compare_frame(_fd, 50, random.Random(10 + s))
        _dtot += r["compared"]
        _dm += r["mismatches"]
    assert _dtot >= 200 and not _dm, _dm[:3]
    _fd0 = _synthetic(0)
    _fd0["Dividend"] = [5.0 if k == 30 else 0.0 for k in range(len(_fd0))]
    _rows = bars_rows(_fd0)
    assert sum(b["dividend"] for b in _rows) == 5.0, "the grader must see the frame's ex-dates"
    assert not mism, mism[:3]
    # THE RISK FLOOR (2026-10-29): the seeded geometries land at least one
    # fill inside it; both engines refuse it and the comparator counts it as
    # void_floor. An engine that floors alone is a mismatch (tested below).
    assert tot["void_floor"] >= 1, tot
    print(f"agreement        : {tot['compared']} random setups on 12 synthetic frames, "
          f"{tot['void_both']} gapped fills void in both, {tot['void_floor']} void at the risk "
          f"floor in both, 0 mismatches")
    def _nofloor(row, bars, today=None):
        g = fl.resolve_signal(row, bars, today=today)
        if g and g["outcome"] == "void" and (g.get("detail") or {}).get("note") == "fill inside the risk floor":
            return {"outcome": "loss", "r": -1.0, "exit_price": 0.0, "detail": {}}
        return g
    r3 = compare_frame(_synthetic(0), 400, random.Random(1), resolve=_nofloor)
    assert any(m[2] == "engine floored, grader did not" for m in r3["mismatches"]), \
        "a grader without the floor must be reported against the engine's floor"
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
    # AMENDMENT H ON ONE BAR (owner 2026-11-04): a bar that opens beyond the
    # target and trades through the stop. The grader and the engine with the
    # grader's rules book the win at the open; the committed research engine
    # (no fixes, every committed result) still books -1 R, until the research
    # default is flipped with its re-run (BACKLOG 47).
    _f = pd.DataFrame({"Date": pd.bdate_range("2026-03-02", periods=24),
                       "Open": [100.0] * 24, "High": [100.5] * 24, "Low": [99.5] * 24, "Close": [100.0] * 24,
                       "Volume": 1e6})
    _f.loc[2, ["Open", "High", "Low", "Close"]] = [112.0, 113.0, 94.0, 95.0]
    _d = _f.set_index(pd.DatetimeIndex(_f["Date"])).drop(columns=["Date"])
    _setup = {"trend": "Bullish", "entry": 100.0, "stop": 98.0, "target": 110.0, "rr": 5.0}
    _cfg = {"slippage_bps": fl.GRADE_SLIPPAGE_BPS, "commission": 0.0, "max_hold": fl.GRADE_MAX_HOLD}
    _row = {"trend": "Bullish", "bar_date": "2026-03-02", "setup": {"price": 100.0, "stop": 98.0, "target": 110.0}}
    _g = fl.resolve_signal(_row, bars_rows(_d), today=dt.date(2026, 6, 1))
    _e1 = bt.simulate_trade(_d, 0, _setup, _cfg)
    assert _g["outcome"] == "win" and _e1["outcome"] == "win" and abs(_g["r"] - _e1["r"]) <= TOL_R, (_g, _e1)
    bt.set_engine_fixes("")
    try:
        _e0 = bt.simulate_trade(_d, 0, _setup, _cfg)
    finally:
        bt.set_engine_fixes(bt.GRADER_FIXES)
    assert _e0["outcome"] == "loss" and _e0["r"] < -0.9, _e0
    print(f"amendment H      : open 112 past target 110, low 94: the grader and the engine with its rules "
          f"book {_g['r']:+.2f} R; the committed research engine {_e0['r']:+.2f} R; dividends agree on "
          f"{_dtot} setups")
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
