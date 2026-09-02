"""
universe_backtest.py — does the dynamic RS universe rule actually help?
========================================================================
The live scanner trades whatever universe.select_universe() ranked highest,
rebalanced weekly. That rule has never been measured. This measures it.

THE MISTAKE THIS FILE EXISTS TO AVOID
--------------------------------------
The obvious way to backtest a dynamic universe is to take today's snapshot —
TMO, TGT, PANW, ABT, NOW, DE, MU, BAC — and run the signal over those eight
names for 15 years. That number would be worthless, and worse, it would be
POSITIVE, which makes it the most dangerous number in this project.

Those eight are in the list precisely BECAUSE the RS ranking picked them in
August 2026, which means they outperformed over the preceding months. Running
them backward through history asks "how did recent winners do?" and the answer
is always "well". It is survivorship bias in its purest form, and after seven
honest no-edge results it would be the first encouraging figure you have seen
— arriving exactly when you most want one.

What this file does instead: at every rebalance date it calls
select_universe(as_of=that_date), which fetches only bars up to that date and
ranks using only what was knowable then. The universe on 2019-03-04 is
whatever the rule would have chosen on 2019-03-04, knowing nothing after it.
That is the only version that measures the rule rather than hindsight.

WHAT IT COMPARES
-----------------
Three arms over the same window, same engine, same trade management:
  dynamic — the RS rule, re-selected at each rebalance
  static  — NVDA/META/MSFT, the Aug 2026 sweep baseline
  frozen  — the twelve tickers in oos_validate.lock.json

The comparison is the point. Dynamic beating a fixed list is the only result
that would justify the extra machinery; dynamic merely being positive proves
nothing, since a rising market lifts any long-only rule.

COST WARNING
-------------
Each rebalance ranks the full candidate pool through yfinance. Weekly over 15
years is ~780 rebalances, and Yahoo will rate-limit long before that. The
default is a MONTHLY rebalance over 5 years (~60 selections), which is the
honest compromise: it under-samples the weekly rule slightly, and that
difference is reported rather than hidden. Use --rebalance weekly --years 15
only with a cache and patience.

Run
---
    pip install yfinance pandas numpy ta
    python universe_backtest.py                      # 5y, monthly, all 3 arms
    python universe_backtest.py --years 10
    python universe_backtest.py --arms dynamic,static
    python universe_backtest.py --rebalance weekly --years 3
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from datetime import date, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

import backtest as bt
import universe as uni

# Pause between rebalance selections. Each one pulls the whole candidate pool,
# so this is the main lever on whether Yahoo rate-limits the run.
SELECT_GAP_SEC = 2.0

STATIC_ARM = ["NVDA", "META", "MSFT"]


def frozen_arm() -> list[str]:
    """The OOS lock's universe, read rather than retyped so the two cannot drift."""
    p = Path(__file__).with_name("oos_validate.lock.json")
    if not p.exists():
        return []
    return list(json.loads(p.read_text())["frozen"]["tickers"])


def rebalance_dates(start: date, end: date, freq: str) -> list[date]:
    step = 7 if freq == "weekly" else 28
    out, d = [], start
    while d <= end:
        out.append(d)
        d += timedelta(days=step)
    return out


def build_membership(dates: list[date], top_n: int,
                     verbose: bool) -> dict[date, list[str]]:
    """
    The point-in-time universe at each rebalance.

    select_universe(as_of=d) fetches bars ending on or before d and ranks with
    only those. Nothing after d can influence the membership on d. This loop is
    the slow part of the run and the reason the whole file exists.
    """
    membership: dict[date, list[str]] = {}
    for k, d in enumerate(dates, 1):
        try:
            picked = uni.select_universe(as_of=d, top_n=top_n, verbose=False)
        except Exception as e:
            print(f"  ! {d}: selection failed ({e}) — skipping this rebalance",
                  file=sys.stderr)
            picked = []
        membership[d] = picked
        if verbose or k % 10 == 0 or k == len(dates):
            print(f"  [{k:3}/{len(dates)}] {d}: "
                  f"{', '.join(picked) if picked else '(none)'}")
        time.sleep(SELECT_GAP_SEC)
    return membership


def held_on(membership: dict[date, list[str]], when: pd.Timestamp) -> set[str]:
    """Universe in force on `when` — the most recent rebalance at or before it."""
    d = when.date() if hasattr(when, "date") else when
    prior = [rd for rd in membership if rd <= d]
    return set(membership[max(prior)]) if prior else set()


def run_arm(name: str, tickers: list[str], years: int, cfg: dict,
            membership: dict[date, list[str]] | None) -> dict:
    """
    Backtest one arm. When `membership` is given, a signal is only traded if
    its ticker was in the universe on that bar's date — which is what makes the
    dynamic arm a test of the RULE and not of a hand-picked list.
    """
    all_trades: list[dict] = []
    per_ticker: list[dict] = []

    # backtest.evaluate_signal() takes a signal_core.SignalParams, not the cfg
    # dict — it delegates to signal_core.evaluate() rather than reimplementing
    # the signal. Build it once per arm; cfg still carries the simulation-only
    # settings (slippage, max_hold, cooldown) that simulate_trade() reads.
    params = bt.build_signal_params(cfg)

    for tk in sorted(tickers):
        raw = bt.download(tk, years)
        if raw is None:
            continue
        df = bt.compute(raw)
        if len(df) < bt.MIN_BARS_AFTER:
            continue
        dates = pd.to_datetime(raw.tail(len(df))["Date"]) \
            if "Date" in raw.columns else pd.to_datetime(df.index)

        trades = []
        i, n = 0, len(df)
        while i < n - 1:
            if membership is not None:
                if tk not in held_on(membership, dates.iloc[i]):
                    i += 1
                    continue
            sig = bt.evaluate_signal(df, i, params)
            if sig:
                res = bt.simulate_trade(df, i, sig, cfg)
                if res.get("filled"):
                    res["ticker"] = tk
                    trades.append(res)
                    i += cfg["cooldown_bars"] + 1
                    continue
            i += 1

        if trades:
            r = np.array([t["r"] for t in trades])
            per_ticker.append({"ticker": tk, "trades": len(r),
                               "avg_r": round(float(r.mean()), 3),
                               "total_r": round(float(r.sum()), 1)})
            all_trades += trades

    s = bt.stats(all_trades)
    pos = sum(1 for p in per_ticker if p["avg_r"] > 0)
    ci_low = ci_high = None
    if len(all_trades) > 1:
        r = np.array([t["r"] for t in all_trades])
        se = float(r.std(ddof=1)) / np.sqrt(len(r))
        ci_low, ci_high = float(r.mean() - 1.96*se), float(r.mean() + 1.96*se)
    s.update({"ci_low": ci_low, "ci_high": ci_high, "arm": name, "per_ticker": per_ticker,
              "tickers_traded": len(per_ticker),
              "tickers_positive": pos,
              "tickers_positive_frac":
                  round(pos / len(per_ticker), 3) if per_ticker else 0.0})
    return s


def main() -> int:
    p = argparse.ArgumentParser(
        description="Point-in-time backtest of the dynamic RS universe rule")
    p.add_argument("--years", type=int, default=5)
    p.add_argument("--rebalance", choices=["weekly", "monthly"],
                   default="monthly")
    p.add_argument("--top-n", type=int, default=uni.TOP_N)
    p.add_argument("--arms", type=str, default="dynamic,static,frozen")
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args()

    arms = [x.strip() for x in a.arms.split(",") if x.strip()]
    end = date.today()
    start = end - timedelta(days=365 * a.years)

    print("=" * 78)
    print("DYNAMIC UNIVERSE BACKTEST — point-in-time membership")
    print("=" * 78)
    print(f"Window     : {start} -> {end}  ({a.years}y)")
    print(f"Rebalance  : {a.rebalance}  top_n={a.top_n}  "
          f"pool={len(uni.CANDIDATE_POOL)} candidates")
    print(f"Arms       : {', '.join(arms)}")
    print(f"Engine     : backtest.py (no-lookahead proven, next-bar-open "
          f"fills, stop-first conservative)")
    print("Membership : select_universe(as_of=d) at each rebalance — uses only")
    print("             bars up to d, so today's winners are NOT projected back.")
    if a.rebalance == "monthly":
        print("NOTE       : live rebalances WEEKLY. Monthly here under-samples")
        print("             the rule to stay inside Yahoo's rate budget.")
    print("=" * 78)

    cfg = dict(bt.DEFAULTS)
    membership = None
    dyn_tickers: set[str] = set()

    if "dynamic" in arms:
        dates = rebalance_dates(start, end, a.rebalance)
        print(f"\nBuilding point-in-time membership ({len(dates)} selections, "
              f"~{len(dates) * SELECT_GAP_SEC / 60:.0f} min)...")
        membership = build_membership(dates, a.top_n, a.verbose)
        for v in membership.values():
            dyn_tickers |= set(v)
        churn = (sum(len(set(membership[dates[i]]) ^ set(membership[dates[i - 1]]))
                     for i in range(1, len(dates))) / max(1, len(dates) - 1))
        print(f"\n  Distinct tickers ever held : {len(dyn_tickers)}")
        print(f"  Avg membership change      : {churn:.1f} names per rebalance")
        if not dyn_tickers:
            print("  ! no memberships resolved — dynamic arm cannot run",
                  file=sys.stderr)
            arms = [x for x in arms if x != "dynamic"]

    results = []
    for arm in arms:
        if arm == "dynamic":
            print(f"\nRunning dynamic arm over {len(dyn_tickers)} tickers...")
            results.append(run_arm("dynamic", sorted(dyn_tickers), a.years,
                                   cfg, membership))
        elif arm == "static":
            print(f"\nRunning static arm ({', '.join(STATIC_ARM)})...")
            results.append(run_arm("static", STATIC_ARM, a.years, cfg, None))
        elif arm == "frozen":
            fz = frozen_arm()
            print(f"\nRunning frozen arm ({len(fz)} tickers from the OOS lock)...")
            results.append(run_arm("frozen", fz, a.years, cfg, None))

    print("\n" + "=" * 78)
    print("COMPARISON")
    print("=" * 78)
    print(f"{'Arm':10} {'Trades':>7} {'Win%':>6} {'Avg R':>8} {'Total R':>9} "
          f"{'PF':>6} {'MaxDD':>8} {'95% CI':>18}  Tickers+")
    for s in results:
        if not s.get("trades"):
            print(f"{s['arm']:10} {'0':>7}  (no trades)")
            continue
        ci = (f"[{s['ci_low']:+.3f}, {s['ci_high']:+.3f}]"
              if s.get('ci_low') is not None else 'n/a')
        print(f"{s['arm']:10} {s['trades']:>7} "
              f"{s['win_rate']:>5.1f}% {s['avg_r']:>8.3f} "
              f"{s['total_r']:>9.1f} {s['pf']:>6.2f} "
              f"{s['max_dd']:>8.1f} {ci:>18}  "
              f"{s['tickers_positive']}/{s['tickers_traded']}")

    print("\n" + "=" * 78)
    print("HOW TO READ THIS")
    print("=" * 78)
    print("  The dynamic arm being POSITIVE proves nothing on its own — a")
    print("  long-only rule in a rising market is positive by construction.")
    print("  The only result that justifies the ranking machinery is dynamic")
    print("  BEATING the fixed arms on expectancy AND on breadth.")
    print()
    print("  If dynamic wins on total R but not on avg R, it is trading more,")
    print("  not trading better.")
    print()
    print("  If breadth stays under half in every arm, the signal still does")
    print("  not predict and the universe choice is rearranging deck chairs.")
    print()
    print("  Do NOT now sweep top_n / RS_LOOKBACK / sector cap looking for a")
    print("  better number. Across this many candidates one combination will")
    print("  always look good in sample and will not survive out of it.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
