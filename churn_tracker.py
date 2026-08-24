#!/usr/bin/env python3
"""
churn_tracker.py — snapshot the universe, diff against last run

WHY
---
A universe that turns over 70% a week is not tradeable: you would be paying
spread and slippage to rotate constantly, and no position would ever get the
30 sessions the hold rule allows. A universe that never changes is not really
dynamic. Somewhere in between is a cadence you can actually trade.

That question is answered by calendar time, not by reasoning. Run this weekly,
same day each week, and after four or five runs the turnover column tells you
whether RS_LOOKBACK=63 produces a stable list or a treadmill.

BONUS DIAGNOSTIC
----------------
The gate count — how many of the candidate pool clear liquidity, price and the
200-SMA — turns out to be a decent breadth reading. Observed: 17/66 in the
June 2022 bear, 37/66 in June 2020, 45/66 in August 2026. A sharp drop is the
market telling you something before any signal fires. It is recorded here as
an observation, not wired into anything.

USAGE
-----
    python churn_tracker.py                  # snapshot today, diff vs last
    python churn_tracker.py --history        # show all snapshots + turnover
    python churn_tracker.py --as-of 2026-07-01   # backfill a past date
    python churn_tracker.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime

SNAPSHOT_DIR = "universe_history"


# ---------------------------------------------------------------------------
# Snapshot storage
# ---------------------------------------------------------------------------

def snapshot_path(d: date) -> str:
    return os.path.join(SNAPSHOT_DIR, f"{d.isoformat()}.json")


def load_snapshots() -> list[dict]:
    if not os.path.isdir(SNAPSHOT_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(SNAPSHOT_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(SNAPSHOT_DIR, fn)) as f:
                out.append(json.load(f))
        except Exception:
            continue
    return sorted(out, key=lambda s: s.get("as_of", ""))


def save_snapshot(snap: dict) -> str:
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    p = snapshot_path(date.fromisoformat(snap["as_of"]))
    with open(p, "w") as f:
        json.dump(snap, f, indent=2)
    return p


# ---------------------------------------------------------------------------
# Churn maths
# ---------------------------------------------------------------------------

def turnover(prev: list[str], cur: list[str]) -> dict:
    """
    Jaccard-style turnover between two universes.

    Denominator is the size of the CURRENT list, so "3 of 8 changed" reads as
    37.5% regardless of how the previous list was sized. Both lists are treated
    as sets — rank changes within the same membership are not turnover, because
    they do not cost you anything to trade.
    """
    p, c = set(prev), set(cur)
    added = sorted(c - p)
    dropped = sorted(p - c)
    held = sorted(c & p)
    denom = max(len(c), 1)
    return {
        "added": added,
        "dropped": dropped,
        "held": held,
        "turnover_pct": round(len(added) / denom * 100, 1),
        "n_added": len(added),
        "n_dropped": len(dropped),
        "n_held": len(held),
    }


def verdict(pcts: list[float]) -> str:
    if not pcts:
        return "No history yet — run weekly and check back after 4-5 snapshots."
    avg = sum(pcts) / len(pcts)
    if len(pcts) < 4:
        return (f"Mean turnover {avg:.0f}% over {len(pcts)} interval(s). "
                "Too few to judge — keep going to 4-5.")
    if avg > 50:
        return (f"Mean turnover {avg:.0f}% — high. At this rate positions rotate "
                "out before a 30-session hold can complete, and rebalancing "
                "costs will be material. A longer RS_LOOKBACK would steady it, "
                "but change it for this reason and write down why, not because "
                "a backtest preferred it.")
    if avg < 10:
        return (f"Mean turnover {avg:.0f}% — very stable. Effectively a fixed "
                "watchlist that updates rarely. Fine, but the dynamic part is "
                "not doing much work at this cadence.")
    return (f"Mean turnover {avg:.0f}% — workable. Membership changes slowly "
            "enough to hold positions, fast enough to follow leadership.")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def show_history(snaps: list[dict]) -> None:
    W = 76
    print("=" * W)
    print("UNIVERSE HISTORY")
    print("=" * W)
    if not snaps:
        print("No snapshots yet.")
        return

    print(f"\n  {'Date':<12}{'Gates':>8}{'Turnover':>10}  Universe")
    pcts = []
    for i, s in enumerate(snaps):
        tickers = s.get("tickers", [])
        gates = f"{s.get('passed_gates','?')}/{s.get('pool_size','?')}"
        if i == 0:
            tp = "     —"
        else:
            t = turnover(snaps[i - 1].get("tickers", []), tickers)
            pcts.append(t["turnover_pct"])
            tp = f"{t['turnover_pct']:>5.0f}%"
        print(f"  {s.get('as_of',''):<12}{gates:>8}{tp:>10}  "
              f"{', '.join(tickers)}")

    print("\n" + "-" * W)
    print(verdict(pcts))

    # Which names keep showing up
    counts = {}
    for s in snaps:
        for t in s.get("tickers", []):
            counts[t] = counts.get(t, 0) + 1
    persistent = sorted(counts.items(), key=lambda kv: -kv[1])[:10]
    if len(snaps) >= 3:
        print("\nMost persistent members:")
        for t, c in persistent:
            print(f"  {t:<8}{c}/{len(snaps)} snapshots")

    gates = [s.get("passed_gates") for s in snaps if s.get("passed_gates")]
    if len(gates) >= 3:
        print(f"\nGate count range: {min(gates)}-{max(gates)} of "
              f"{snaps[-1].get('pool_size','?')}")
        print("  (a sharp fall means fewer names hold their 200-SMA — broad "
              "weakness)")
    print("=" * W)


def report_new(snap: dict, prev: dict | None) -> None:
    W = 76
    print("=" * W)
    print(f"UNIVERSE SNAPSHOT — {snap['as_of']}")
    print("=" * W)
    print(f"\n  Universe : {', '.join(snap['tickers'])}")
    print(f"  Gates    : {snap.get('passed_gates','?')} of "
          f"{snap.get('pool_size','?')} candidates passed")
    if snap.get("sector_mix"):
        print(f"  Sectors  : " + ", ".join(f"{k} {v}" for k, v in
                                           sorted(snap["sector_mix"].items())))

    if prev is None:
        print("\n  First snapshot — nothing to diff against yet.")
        print("  Run again in a week to start measuring turnover.")
        print("=" * W)
        return

    t = turnover(prev.get("tickers", []), snap["tickers"])
    print(f"\n  Since {prev.get('as_of','?')}:")
    print(f"    Turnover : {t['turnover_pct']:.0f}%  "
          f"({t['n_added']} in, {t['n_dropped']} out, {t['n_held']} held)")
    if t["added"]:
        print(f"    Added    : {', '.join(t['added'])}")
    if t["dropped"]:
        print(f"    Dropped  : {', '.join(t['dropped'])}")
    if not t["added"] and not t["dropped"]:
        print("    No membership change.")
    print("=" * W)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest() -> int:
    t = turnover(["A", "B", "C", "D"], ["A", "B", "E", "F"])
    print(f"turnover        : {t['turnover_pct']:.0f}%  (expect 50)")
    assert t["turnover_pct"] == 50.0, t
    assert t["added"] == ["E", "F"] and t["dropped"] == ["C", "D"], t
    print(f"added/dropped   : {t['added']} / {t['dropped']}")

    same = turnover(["A", "B"], ["B", "A"])
    print(f"reorder only    : {same['turnover_pct']:.0f}%  (expect 0 — rank "
          f"changes are not turnover)")
    assert same["turnover_pct"] == 0.0

    empty = turnover([], ["A", "B"])
    print(f"from empty      : {empty['turnover_pct']:.0f}%  (expect 100)")
    assert empty["turnover_pct"] == 100.0

    to_empty = turnover(["A", "B"], [])
    print(f"to empty        : {to_empty['turnover_pct']:.0f}%  "
          f"(expect 0 added, 2 dropped)")
    assert to_empty["n_dropped"] == 2 and to_empty["n_added"] == 0

    print(f"\nverdict, high   : {verdict([70, 65, 75, 60])[:46]}...")
    assert "high" in verdict([70, 65, 75, 60])
    assert "stable" in verdict([5, 0, 8, 4])
    assert "workable" in verdict([25, 30, 20, 35])
    assert "Too few" in verdict([25])
    print("verdict bands   : high / stable / workable / too-few all OK")
    print("\nAll self-tests passed.")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--history", action="store_true",
                    help="show all snapshots and turnover, take none")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    snaps = load_snapshots()

    if args.history:
        show_history(snaps)
        return 0

    as_of = date.today()
    if args.as_of:
        try:
            as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()
        except ValueError:
            print("--as-of must be YYYY-MM-DD")
            return 2

    try:
        import universe as u
    except ImportError:
        print("universe.py not found — run this from the repo root.")
        return 2

    if os.path.exists(snapshot_path(as_of)):
        print(f"Snapshot for {as_of} already exists. Delete it to re-take.")
        return 1

    print(f"Selecting universe as of {as_of}...\n")
    bars = u.fetch_history(u.CANDIDATE_POOL + [u.BENCHMARK], as_of,
                           u.RS_LOOKBACK, verbose=True)
    bench = bars.get(u.BENCHMARK)
    if bench is None:
        print("No benchmark data — cannot rank.")
        return 1

    scored = []
    for t in u.CANDIDATE_POOL:
        df = bars.get(t)
        if df is None:
            continue
        sc = u.score_ticker(df, bench)
        if sc is None or not sc["above_200sma"]:
            continue
        scored.append({"ticker": t, **sc})
    scored.sort(key=lambda r: r["rs"], reverse=True)
    picked = u.apply_sector_cap(scored, u.TOP_N)

    mix = {}
    for r in picked:
        mix[r.get("sector", "Other")] = mix.get(r.get("sector", "Other"), 0) + 1

    snap = {
        "as_of": as_of.isoformat(),
        "taken_at": datetime.now().isoformat(timespec="seconds"),
        "tickers": [r["ticker"] for r in picked],
        "passed_gates": len(scored),
        "pool_size": len(u.CANDIDATE_POOL),
        "sector_mix": mix,
        "rs_lookback": u.RS_LOOKBACK,
        "top_n": u.TOP_N,
        "max_per_sector": u.MAX_PER_SECTOR,
        "detail": [{k: r[k] for k in ("ticker", "rs", "price", "dollar_vol_m")}
                   for r in picked],
    }

    prev = snaps[-1] if snaps else None
    path = save_snapshot(snap)
    report_new(snap, prev)
    print(f"\nSaved {path}")
    if prev is not None:
        print("Run --history to see the full turnover series.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
