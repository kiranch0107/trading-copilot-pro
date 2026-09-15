#!/usr/bin/env python3
"""
exit_ab.py — three exit rules against the current one, paired by signal.

WHY

outcome_taxonomy run 1, over 2,360 trades: 356 of them -- 22.5% of all losses
-- travelled a median +1.82 R our way, peaked at bar 2, then reversed and
stopped at -1.04. They were working and the exit handed it back.

THE ARMS

Same signals, same entry bars, same fills, same initial stop and target, same
costs. ONLY the exit management differs, so an identical trade contributes a
delta of exactly zero and the comparison is paired rather than two independent
samples.

  BREAK-EVEN  stop to entry once price trades +1.0 R in our favour
  TRAIL       1.0 ATR below the high-water mark, armed at +1.0 R
  PARTIAL     half off at +1.5 R, remainder runs to the original levels

WHY THE OBVIOUS ARITHMETIC IS REFUSED

356 x 1.04 R is the number the pre-registration exists to forbid. Any rule that
rescues a near-miss also acts on the 704 winners, and SCARE TARGET is the
exposed bucket: 237 wins with a median MAE of -0.74 that still reached target.
A break-even stop that saves a near-miss scratches some of those.

The aggregates cannot settle it, because it depends on whether each winner's
drawdown came before or after +1 R. This measures both sides at once, which is
the whole reason it is an A/B and not more slicing.

Pre-registered in results/exit_ab_preregistration.md, committed before this
file existed.
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

import adx_retest as ar
import backtest as bt
import outcome_taxonomy as ot
import setup_cases as scs

ARMS = (
    ("BREAK-EVEN", {"breakeven_at": 1.0},
     "stop to entry once +1.0 R is touched"),
    ("TRAIL", {"trail_atr": 1.0, "arm_at": 1.0},
     "1.0 ATR below the high-water mark, armed at +1.0 R"),
    ("PARTIAL", {"partial_at": 1.5, "partial_frac": 0.5},
     "half off at +1.5 R, remainder to the original target"),
)


def signals(df, cfg, params) -> list[int]:
    """
    Every bar the signal fires on, walked exactly as backtest_ticker walks it.

    Enumerated ONCE and shared by every arm. The cooldown advances by a fixed
    number of bars after a fill and does not depend on how long the trade was
    held, so the sequence is identical across arms — which is what makes the
    pairing exact rather than approximate.
    """
    out = []
    i, n = 0, len(df)
    while i < n - 1:
        sig = bt.evaluate_signal(df, i, params)
        if sig:
            probe = bt.simulate_trade(df, i, sig, cfg)
            if probe.get("filled"):
                out.append((i, sig))
                i += cfg["cooldown_bars"] + 1
                continue
        i += 1
    return out


def paired(base: list[dict], arm: list[dict]) -> dict:
    """Mean delta per trade, its paired t, and where the movement came from."""
    assert len(base) == len(arm), (
        f"pairing broken: {len(base)} baseline vs {len(arm)} arm trades")
    d = [a["r"] - b["r"] for b, a in zip(base, arm)]
    n = len(d)
    if n < 2:
        return {"n": n, "mean": 0.0, "p": 1.0, "helped": 0, "hurt": 0,
                "same": n, "best": 0.0, "worst": 0.0}
    mean = float(np.mean(d))
    sd = float(np.std(d, ddof=1))
    return {"n": n, "mean": mean, "sd": sd,
            "p": ar.t_pvalue(mean, sd, n),
            "helped": sum(1 for x in d if x > 1e-9),
            "hurt": sum(1 for x in d if x < -1e-9),
            "same": sum(1 for x in d if abs(x) <= 1e-9),
            "gain": float(sum(x for x in d if x > 0)),
            "loss": float(sum(x for x in d if x < 0)),
            "best": max(d), "worst": min(d)}


def movement(base: list[dict], arm: list[dict]) -> dict[tuple, int]:
    """Which buckets trades moved between. Required output, not optional.

    A rule that converts 300 near-misses and degrades 400 winners has to show
    both numbers. A net figure hides exactly the trade-off being decided.
    """
    moves: dict[tuple, int] = {}
    for b, a in zip(base, arm):
        if abs(a["r"] - b["r"]) <= 1e-9:
            continue
        key = (ot.classify(b), "better" if a["r"] > b["r"] else "worse")
        moves[key] = moves.get(key, 0) + 1
    return moves


def report(name: str, blurb: str, st: dict, moves: dict, holm: bool,
           base_mean: float) -> str:
    print(f"\n{'='*92}")
    print(f"{name} — {blurb}")
    print("=" * 92)
    print(f"  paired delta   {st['mean']:+.4f} R per trade   "
          f"(baseline {base_mean:+.4f} -> {base_mean + st['mean']:+.4f})")
    print(f"  p {st['p']:.4f}   Holm {'SURVIVES' if holm else 'no'}   "
          f"n {st['n']}")
    print(f"  changed {st['helped'] + st['hurt']} trades: "
          f"{st['helped']} better (+{st['gain']:.1f} R total), "
          f"{st['hurt']} worse ({st['loss']:.1f} R total)")
    print(f"  unchanged {st['same']}   best single {st['best']:+.2f} R   "
          f"worst {st['worst']:+.2f} R")
    if moves:
        print(f"  where it came from, by the trade's ORIGINAL bucket:")
        for (bucket, direction), cnt in sorted(
                moves.items(), key=lambda kv: -kv[1]):
            print(f"    {bucket:<16} {direction:<7} {cnt:>5}")
    if holm and st["mean"] > 0:
        v = "PASSES"
    elif holm and st["mean"] < 0:
        v = "HARMFUL"
    else:
        v = "NOT ESTABLISHED"
    print(f"  -> {v}")
    return v


def run(tickers: list[str], years: int) -> int:
    cfg = dict(bt.DEFAULTS, tickers=tickers, years=years)
    params = bt.build_signal_params(cfg)
    base: list[dict] = []
    arms: dict[str, list[dict]] = {n: [] for n, _, _ in ARMS}

    print("  collecting ...", file=sys.stderr)
    for tk in tickers:
        raw = bt.download(tk, years)
        if raw is None:
            print(f"  ! {tk}: no data", file=sys.stderr)
            continue
        df = bt.compute(raw).copy()
        sigs = signals(df, cfg, params)
        for i, sig in sigs:
            b = bt.simulate_trade(df, i, sig, cfg)
            if not b.get("filled"):
                continue
            base.append(b)
            for name, pol, _ in ARMS:
                a = bt.simulate_trade(df, i, sig, cfg, policy=pol)
                assert a.get("filled"), (
                    f"{name} failed to fill a trade the baseline filled — the "
                    f"arms must share entries exactly or the pairing is a lie")
                arms[name].append(a)
        print(f"  {tk}: {len(sigs)} signals", file=sys.stderr)

    if not base:
        print("NOTHING TO COMPARE — no trade filled.")
        return 2
    base_mean = float(np.mean([t["r"] for t in base]))
    print("=" * 92)
    print("EXIT A/B — three rules against the current one, paired by signal")
    print("=" * 92)
    print(f"  {len(base)} trades, {len(tickers)} tickers, {years} years")
    print(f"  baseline mean R {base_mean:+.4f}")
    print(f"  pre-registered: results/exit_ab_preregistration.md")
    print(f"  Same entries and fills in every arm. Only the exit differs.")

    stats = {n: paired(base, arms[n]) for n, _, _ in ARMS}
    keep = ar.holm([stats[n]["p"] for n, _, _ in ARMS], ar.ALPHA)
    verdicts = {}
    for (name, pol, blurb), k in zip(ARMS, keep):
        verdicts[name] = report(name, blurb, stats[name],
                                movement(base, arms[name]), k, base_mean)

    print(f"\n{'='*92}")
    print("SUMMARY")
    for name, _, _ in ARMS:
        st = stats[name]
        print(f"  {name:<12} {st['mean']:+.4f} R   p {st['p']:.4f}   "
              f"{verdicts[name]}")
    passing = [n for n, v in verdicts.items() if v == "PASSES"]
    harmful = [n for n, v in verdicts.items() if v == "HARMFUL"]
    print()
    if passing:
        print(f"  {', '.join(passing)} cleared Holm with a positive delta.")
        print("  IN-SAMPLE on twenty spent tickers. That is a hypothesis for")
        print("  tranche C, not a finding, and not a reason to change the live")
        print("  exit.")
    else:
        print("  No arm passed. Managing the exit did not recover the near")
        print("  misses without giving back more elsewhere.")
    if harmful:
        print(f"  {', '.join(harmful)} is HARMFUL — it cleared Holm with a")
        print("  NEGATIVE delta. Knowing a rule costs money is a result.")
    print("=" * 92)
    return 0


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------

def selftest() -> int:
    print("exit_ab.py selftest")
    print("=" * 72)

    assert len(ARMS) == 3, (
        f"{len(ARMS)} arms — the pre-registration fixed three and the Holm "
        f"denominator with them")
    assert len({n for n, _, _ in ARMS}) == 3, "duplicate arm name"
    print(f"arms             : {len(ARMS)}, Holm denominator fixed")

    def t(r):
        return {"r": r, "outcome": "loss" if r < 0 else "win",
                "mfe_r": 2.0 if r < 0 else 3.1, "mae_r": -1.2 if r < 0 else -0.2,
                "setup": {"rr": 3.0}}

    # ── IDENTICAL TRADES MUST CONTRIBUTE EXACTLY ZERO ──
    # That is the whole point of pairing: a rule that touches 5% of trades is
    # tested on those 5%, not diluted by 95% of noise it never acted on.
    b = [t(-1.0), t(3.0), t(-1.0), t(3.0)]
    same = paired(b, [dict(x) for x in b])
    assert abs(same["mean"]) < 1e-12 and same["p"] > 0.99, same
    assert same["same"] == 4 and same["helped"] == 0 and same["hurt"] == 0, same
    print("pairing          : identical arms give delta 0.0000, p 1.00")

    # ── helped and hurt are counted SEPARATELY, never netted ──
    a = [t(0.0), t(3.0), t(-1.0), t(2.0)]      # +1, 0, 0, -1
    st = paired(b, a)
    assert st["helped"] == 1 and st["hurt"] == 1 and st["same"] == 2, st
    assert abs(st["gain"] - 1.0) < 1e-9 and abs(st["loss"] + 1.0) < 1e-9, st
    assert abs(st["mean"]) < 1e-9, (
        "this fixture nets to zero on purpose — a module that only reported "
        "the mean would call it 'no effect' while two trades moved a full R "
        "in opposite directions")
    print(f"decomposition    : nets to 0.0000 but shows 1 helped / 1 hurt")

    # ── the movement table reports the ORIGINAL bucket ──
    mv = movement(b, a)
    assert mv.get(("NEAR MISS", "better")) == 1, mv
    assert mv.get(("CLEAN TARGET", "worse")) == 1, mv
    assert sum(mv.values()) == 2, mv
    print("movement         : credits each change to the trade's own bucket")

    # ── PAIRING MUST BE REFUSED IF THE ARMS DIVERGE ──
    try:
        paired(b, a[:2])
        raise SystemExit("paired() accepted mismatched lengths")
    except AssertionError as e:
        assert "pairing broken" in str(e), e
    print("mismatch         : refuses to pair lists of different lengths")

    # ── THE POLICIES MUST REACH THE ENGINE AND DIFFER FROM EACH OTHER ──
    # Same round trip as backtest's own fixture: entry 100, stop 98, risk 2,
    # +1.5 R on bar 1 and a collapse on bar 2. Each arm books a different
    # number, so an arm silently sharing another's policy cannot hide.
    import pandas as pd
    rt = pd.DataFrame({
        "Date": pd.date_range("2024-01-01", periods=3, freq="D"),
        "Open":  [100.0, 100.0, 103.0],
        "High":  [100.0, 103.0, 103.0],
        "Low":   [100.0, 100.0,  97.0],
        "Close": [100.0, 103.0,  97.0],
    })
    rc = dict(bt.DEFAULTS, max_hold=3, slippage_bps=0.0, commission=0.0)
    tr = {"trend": "Bullish", "entry": 100.0, "stop": 98.0, "target": 120.0,
          "rr": 10.0, "atr": 2.0}
    got = {n: bt.simulate_trade(rt, 0, tr, rc, policy=p)["r"]
           for n, p, _ in ARMS}
    base_r = bt.simulate_trade(rt, 0, tr, rc)["r"]
    assert abs(base_r + 1.0) < 1e-9, base_r
    assert len(set(round(v, 6) for v in got.values())) == 3, (
        f"two arms produced the same result on a trade designed to separate "
        f"them: {got}. One is silently carrying another's policy")
    for n, v in got.items():
        assert v > base_r, f"{n} did not improve this round trip: {v}"
    print(f"arms differ      : baseline {base_r:+.2f} -> " +
          ", ".join(f"{n} {v:+.2f}" for n, v in got.items()))

    # ── and the baseline the A/B measures against is the UNPOLICED engine ──
    assert abs(bt.simulate_trade(rt, 0, tr, rc)["r"]
               - bt.simulate_trade(rt, 0, tr, rc, policy=None)["r"]) < 1e-12
    print("baseline         : policy=None is the unpoliced engine")

    print("=" * 72)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default=scs.DEFAULT_TICKERS)
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    return run(tickers, a.years)


if __name__ == "__main__":
    sys.exit(main())
