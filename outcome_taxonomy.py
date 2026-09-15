#!/usr/bin/env python3
"""
outcome_taxonomy.py — what actually happened, against what we predicted.

THE QUESTION, AND WHY THE EXISTING NULL DOES NOT COVER IT

Every test so far asked whether the setup readings PREDICT the outcome. They do
not: RSI d +0.04, ADX d -0.01, volume d +0.02 at n = 1,457 with power to detect
0.153, and random entries on the same charts did as well or better.

This asks something else. For each historical setup we predicted the target
before the stop. What actually happened -- not the verdict, the PATH?

    a trade that stopped out after travelling 2.8 R our way   -> EXIT problem
    a trade that stopped having never gone 0.3 R our way      -> ENTRY problem

Both book -1 R. Both are identical in every other number this project records.
They need opposite fixes, and one of them needs no predictive power at all.

THESE BUCKETS NEVER BLOCK A TRADE

They are a readout, not a gate. Nothing here may be consulted by scanner.py,
app.py or any path that decides whether to take a setup -- the point is to
annotate what the tool surfaces, never to suppress it.
consistency_check.check_taxonomy_never_gates() enforces that.

Pre-registered in results/outcome_taxonomy_preregistration.md, with the bucket
thresholds and what each would imply fixed before the data was looked at.

WHAT THIS CANNOT DO

Establish an edge. It describes 2,360 in-sample trades on twenty spent
tickers. A large bucket says what happened; it does not say a change would have
helped. Counting NEAR MISS trades and multiplying by a hypothetical trailing
stop is backtesting on the answer sheet.
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

import setup_cases as scs

# The buckets, in evaluation order. Order is fixed: DEAD WRONG is tested before
# NEAR MISS so a trade with rr < 1 cannot satisfy both.
#
# Thresholds come from the strategy's own geometry -- a stop at -1 R, a target
# at +rr -- and were written down before any distribution was seen.
BUCKETS = (
    ("CLEAN TARGET",
     lambda t: t["outcome"] == "win" and t["mae_r"] > -0.5,
     "reached target, never seriously threatened"),
    ("SCARE TARGET",
     lambda t: t["outcome"] == "win",
     "got there after going deep against first"),
    ("DEAD WRONG",
     lambda t: t["outcome"] == "loss" and t["mfe_r"] < 0.5,
     "stopped having barely moved our way — the ENTRY was wrong"),
    ("NEAR MISS",
     lambda t: (t["outcome"] == "loss"
                and t["mfe_r"] >= 0.5 * float((t.get("setup") or {}).get("rr", 0) or 0)),
     "stopped after getting at least halfway — the EXIT was wrong"),
    ("SHALLOW LOSS",
     lambda t: t["outcome"] == "loss",
     "moved a little our way, then stopped"),
    ("STALLED RUNNER",
     lambda t: t["outcome"] == "timeout" and t["mfe_r"] >= 1.0,
     "moved our way, ran out of time"),
    ("CHOP",
     lambda t: t["outcome"] == "timeout",
     "went nowhere either way"),
)

FAMILY = {"CLEAN TARGET": "win", "SCARE TARGET": "win",
          "DEAD WRONG": "loss", "NEAR MISS": "loss", "SHALLOW LOSS": "loss",
          "STALLED RUNNER": "timeout", "CHOP": "timeout"}


def classify(t: dict) -> str:
    """First matching bucket, in the fixed order. OTHER means unclassified."""
    for name, rule, _ in BUCKETS:
        try:
            if rule(t):
                return name
        except (KeyError, TypeError):
            return "OTHER"
    return "OTHER"


def tabulate(trades: list[dict]) -> list[dict]:
    """Per-bucket counts, share, and what those trades actually paid."""
    ts = [t for t in trades
          if t.get("r") is not None and "mfe_r" in t and "mae_r" in t]
    rows = []
    for name, _, blurb in BUCKETS + (("OTHER", None, "unclassified"),):
        grp = [t for t in ts if classify(t) == name]
        if not grp and name != "OTHER":
            rows.append({"name": name, "n": 0, "blurb": blurb})
            continue
        if not grp:
            continue
        rows.append({
            "name": name, "n": len(grp), "blurb": blurb,
            "share": len(grp) / len(ts),
            "mean_r": float(np.mean([t["r"] for t in grp])),
            "mfe": float(np.median([t["mfe_r"] for t in grp])),
            "mae": float(np.median([t["mae_r"] for t in grp])),
            "hold": float(np.median([t.get("hold", 0) for t in grp])),
            "bars_to_mfe": float(np.median([t.get("bars_to_mfe", 0) for t in grp])),
        })
    return rows


def report(rows: list[dict], total: int) -> None:
    print(f"\n{'='*92}")
    print(f"OUTCOME BUCKETS — {total} trades, classified by what the price "
          f"actually did")
    print("=" * 92)
    print(f"  {'bucket':<16}{'n':>6}{'share':>8}{'mean R':>9}"
          f"{'med MFE':>9}{'med MAE':>9}{'held':>7}{'to MFE':>8}   what it means")
    print("  " + "-" * 88)
    for fam in ("win", "loss", "timeout", None):
        for r in rows:
            if FAMILY.get(r["name"]) != fam:
                continue
            if not r["n"]:
                print(f"  {r['name']:<16}{0:>6}{'':>8}{'':>9}{'':>9}{'':>9}"
                      f"{'':>7}{'':>8}   (none)")
                continue
            print(f"  {r['name']:<16}{r['n']:>6}{r['share']*100:>7.1f}%"
                  f"{r['mean_r']:>+9.3f}{r['mfe']:>+9.2f}{r['mae']:>+9.2f}"
                  f"{r['hold']:>7.0f}{r['bars_to_mfe']:>8.0f}   {r['blurb']}")


def diagnose(rows: list[dict]) -> None:
    """What the shape implies — using the readings fixed before the run."""
    by = {r["name"]: r for r in rows}
    loss_n = sum(by[n]["n"] for n in ("DEAD WRONG", "NEAR MISS", "SHALLOW LOSS"))
    print(f"\n{'='*92}")
    print("WHAT THE SHAPE IMPLIES")
    print("  Written into the pre-registration before the counts existed, so")
    print("  they cannot be reinterpreted to suit what came back.")
    print("=" * 92)
    if not loss_n:
        print("  no losses to break down")
        return
    dw = by["DEAD WRONG"]["n"] / loss_n
    nm = by["NEAR MISS"]["n"] / loss_n
    print(f"  Of {loss_n} losses: {dw*100:.1f}% DEAD WRONG, {nm*100:.1f}% NEAR MISS")
    if nm >= 0.20:
        print(f"\n  NEAR MISS is {nm*100:.0f}% of losses — the EXIT is implicated.")
        print("  These trades went at least halfway to target and gave it all")
        print("  back. Converting them needs no predictive power: a trailing")
        print("  stop, a partial, or a break-even move acts on what already")
        print("  happened.")
        print("  THE NEXT STEP IS A PRE-REGISTERED A/B OF ONE SPECIFIC EXIT")
        print("  RULE across the whole population. Multiplying this count by a")
        print("  hypothetical payoff is backtesting on the answer sheet.")
    else:
        print(f"\n  NEAR MISS is {nm*100:.0f}% of losses — under the 20% the")
        print("  pre-registration set as the level worth acting on.")
    if dw >= 0.45:
        print(f"\n  DEAD WRONG is {dw*100:.0f}% of losses — the ENTRY is the")
        print("  problem, which matches everything already measured: the setup")
        print("  readings carry no information at n = 1,457. Exit management")
        print("  cannot fix a trade that never moved our way.")
    st = by["STALLED RUNNER"]
    if st["n"] and st["share"] >= 0.05:
        print(f"\n  STALLED RUNNER is {st['share']*100:.1f}% of all trades, "
              f"median MFE {st['mfe']:+.2f} R — max_hold is cutting trades")
        print("  that were working.")
    ch = by["CHOP"]
    if ch["n"] and ch["share"] >= 0.10:
        print(f"\n  CHOP is {ch['share']*100:.1f}% of all trades — the signal "
              f"fires where nothing happens either way.")
    print("\n  IN-SAMPLE, twenty spent tickers. A large bucket says what")
    print("  happened. It does not say a change would have helped.")
    print("=" * 92)


def which_kind_of_loss(trades: list[dict]) -> None:
    """
    Do the setup readings predict DEAD WRONG vs NEAR MISS?

    THIS IS NOT THE TEST THAT CAME BACK NULL. That one asked whether the
    readings separate a winner from a loser, and they do not. This asks whether
    they separate two KINDS of loser, and nothing measured so far touches it.

    It matters because the two need opposite fixes. If a reading says "this one
    will go nowhere" versus "this one will run and then give it back", the
    second is recoverable by exit management and the first is not — and that is
    useful even though neither is recoverable by better entry selection.

    Same machinery and the same bar as setup_population: Welch on each of the
    seven fixed readings, Holm across them.
    """
    import setup_population as sp
    import adx_retest as ar

    dw = [t for t in trades if classify(t) == "DEAD WRONG"]
    nm = [t for t in trades if classify(t) == "NEAR MISS"]
    print(f"\n{'='*92}")
    print("DOES ANY READING PREDICT *WHICH KIND* OF LOSS?")
    print(f"  DEAD WRONG n={len(dw)}   NEAR MISS n={len(nm)}")
    print("=" * 92)
    if len(dw) < 50 or len(nm) < 50:
        print(f"  Too few in one arm to test (need 50 each). No verdict.")
        return
    print(f"  detectable effect at alpha 0.05, power 0.80: "
          f"d = {sp.mde_d(len(dw), len(nm)):.3f}")
    rows = []
    for name, fn in sp.READINGS:
        a = [fn(t) for t in nm]
        b = [fn(t) for t in dw]
        a = [x for x in a if np.isfinite(x)]
        b = [x for x in b if np.isfinite(x)]
        rows.append({"name": name, **sp.welch(a, b),
                     "nm": float(np.mean(a)) if a else float("nan"),
                     "dw": float(np.mean(b)) if b else float("nan")})
    keep = ar.holm([r["p"] for r in rows], sp.ALPHA)
    print(f"  {'reading':<18}{'near miss':>11}{'dead wrong':>12}{'gap':>8}"
          f"{'d':>7}{'p':>9}{'Holm':>6}")
    print("  " + "-" * 72)
    hits = []
    for r, k in zip(rows, keep):
        print(f"  {r['name']:<18}{r['nm']:>11.2f}{r['dw']:>12.2f}"
              f"{r['gap']:>+8.2f}{r['d']:>+7.2f}{r['p']:>9.4f}"
              f"{'YES' if k else 'no':>6}")
        if k:
            hits.append(r["name"])
    if hits:
        print(f"\n  SURVIVES HOLM: {', '.join(hits)}")
        print("  A reading that tells a recoverable loss from an unrecoverable")
        print("  one is worth showing on the alert — as information, never as a")
        print("  filter. It is still in-sample and still needs tranche C.")
    else:
        print("\n  NOTHING. The readings cannot tell a trade that will go")
        print("  nowhere from one that will run and give it back, any more")
        print("  than they can tell a winner from a loser.")


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
    print("  collecting ...", file=sys.stderr)
    trades = scs.collect(tickers, a.years)
    trades = [t for t in trades if "mfe_r" in t and "mae_r" in t]
    if not trades:
        print("NOTHING TO CLASSIFY — no trade carried an excursion. Is "
              "backtest.simulate_trade still recording mfe_r/mae_r?")
        return 2
    print("=" * 92)
    print("OUTCOME TAXONOMY — what happened, against what we predicted")
    print("=" * 92)
    print(f"  {len(trades)} trades, {len(tickers)} tickers, {a.years} years")
    print(f"  pre-registered: results/outcome_taxonomy_preregistration.md")
    print(f"  THESE BUCKETS NEVER BLOCK A TRADE. They are a readout.")
    rows = tabulate(trades)
    report(rows, len(trades))
    diagnose(rows)
    which_kind_of_loss(trades)
    return 0


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------

def selftest() -> int:
    print("outcome_taxonomy.py selftest")
    print("=" * 72)

    def t(outcome, r, mfe, mae, rr=3.0, hold=5, **kw):
        d = {"outcome": outcome, "r": r, "mfe_r": mfe, "mae_r": mae,
             "hold": hold, "bars_to_mfe": 1,
             "setup": {"rr": rr, "trend": "Bullish", "rsi": 60.0, "adx": 30.0,
                       "atr": 2.0, "price": 100.0, "ema20": 99.0, "ema50": 96.0,
                       "vol_ratio": 1.2, "entry": 100.0, "stop": 98.0},
             "entry": 100.0, "stop": 98.0}
        d["setup"].update(kw)
        return d

    # ── every bucket is reachable, and the order resolves overlaps ──
    cases = [
        (t("win", 3.0, 3.1, -0.2), "CLEAN TARGET"),
        (t("win", 3.0, 3.1, -0.9), "SCARE TARGET"),
        (t("loss", -1.0, 0.2, -1.0), "DEAD WRONG"),
        (t("loss", -1.0, 2.1, -1.0), "NEAR MISS"),
        (t("loss", -1.0, 0.9, -1.0), "SHALLOW LOSS"),
        (t("timeout", 0.4, 1.4, -0.3), "STALLED RUNNER"),
        (t("timeout", 0.0, 0.3, -0.3), "CHOP"),
    ]
    for trade, want in cases:
        got = classify(trade)
        assert got == want, f"{want} classified as {got}: {trade}"
    print(f"buckets          : all {len(cases)} reachable, each exactly once")

    # ── THE ORDER MATTERS AND IS FIXED ──
    # At rr = 0.9, half the target is 0.45, which is BELOW the 0.5 dead-wrong
    # line. A trade with MFE 0.47 satisfies both rules. DEAD WRONG is tested
    # first, so the answer is deterministic rather than dependent on dict order.
    amb = t("loss", -1.0, 0.47, -1.0, rr=0.9)
    assert BUCKETS[2][1](amb) and BUCKETS[3][1](amb), (
        "the fixture must satisfy BOTH rules or it does not test the order")
    assert classify(amb) == "DEAD WRONG", (
        f"an ambiguous trade must resolve by the fixed order, got "
        f"{classify(amb)}")
    print("order            : a trade matching two rules resolves deterministically")

    # ── NOTHING FALLS THROUGH ──
    every = [c for c, _ in cases] + [amb]
    assert all(classify(x) != "OTHER" for x in every), "a case fell through"
    # and a malformed record is OTHER, not silently a real bucket
    assert classify({"outcome": "win"}) == "OTHER", (
        "a record with no excursions must be OTHER, never assigned a bucket "
        "from data it does not have")
    print("exhaustive       : every shaped record classified; malformed -> OTHER")

    # ── the table reports shares that sum to 1 and losses that agree ──
    rows = tabulate([c for c, _ in cases])
    tot = sum(r["n"] for r in rows)
    assert tot == len(cases), f"{tot} classified out of {len(cases)}"
    share = sum(r.get("share", 0.0) for r in rows)
    assert abs(share - 1.0) < 1e-9, f"shares sum to {share}"
    print(f"table            : {tot} trades, shares sum to 1.000")

    # ── mean R per bucket is the bucket's own, not the population's ──
    mixed = [t("loss", -1.0, 0.1, -1.0), t("loss", -1.0, 0.1, -1.0),
             t("win", 3.0, 3.2, -0.1)]
    by = {r["name"]: r for r in tabulate(mixed)}
    assert abs(by["DEAD WRONG"]["mean_r"] - (-1.0)) < 1e-9, by["DEAD WRONG"]
    assert abs(by["CLEAN TARGET"]["mean_r"] - 3.0) < 1e-9, by["CLEAN TARGET"]
    print("per-bucket R     : each bucket reports its own trades, not the pool")

    # ── THE BUCKETS MUST NOT BE USABLE AS A GATE ──
    # The user's constraint, pinned in code rather than left as a comment:
    # classify() takes a COMPLETED trade. It needs mfe_r and mae_r, which only
    # exist after the trade is over, so it cannot be evaluated at signal time
    # even by accident.
    live_shaped = {"outcome": "win", "setup": {"rr": 3.0}}   # no excursions yet
    assert classify(live_shaped) == "OTHER", (
        "a setup with no completed path must not receive a bucket. If it "
        "could, the taxonomy would be wireable as an entry filter, which is "
        "exactly what it must never be")
    print("never a gate     : an unfinished trade cannot be bucketed at all")

    # ── THE EXCURSIONS MUST ARRIVE, not merely be mentioned in the source ──
    # The first version of this guard grepped simulate_trade's source for the
    # key names. That tests that a string exists in the producer, which is the
    # mistake this project has now shipped four separate times — every one of
    # them a guard aimed at the producer while the break sat in the consumer.
    # Run a real trade and read what comes back.
    import backtest as bt
    df = bt._synthetic_ohlc(up=True, adx=40.0)
    px = float(df["Close"].iloc[80])
    atr = float(df["ATR"].iloc[80])
    got = bt.simulate_trade(df, 80, {"trend": "Bullish", "entry": px,
                                     "stop": px - atr, "target": px + 3 * atr,
                                     "rr": 3.0, "atr": atr}, dict(bt.DEFAULTS))
    assert got.get("filled"), "the probe trade did not fill; guard is blind"
    for k in ("mfe_r", "mae_r", "bars_to_mfe"):
        assert k in got, (
            f"backtest.simulate_trade no longer records {k!r}. Every trade "
            f"would drop out of this module and it would print nothing")
    assert got["mae_r"] <= got["r"] <= got["mfe_r"] + 1e-6, (
        f"the path contradicts the verdict: MAE {got['mae_r']:+.2f} <= R "
        f"{got['r']:+.2f} <= MFE {got['mfe_r']:+.2f} does not hold")
    assert classify(got) != "OTHER", (
        f"a real completed trade came back unclassifiable: "
        f"{ {k: got.get(k) for k in ('outcome', 'mfe_r', 'mae_r')} }")
    print(f"wiring           : real trade -> MFE {got['mfe_r']:+.2f}, MAE "
          f"{got['mae_r']:+.2f}, bucket {classify(got)}")

    print("=" * 72)
    print("All self-tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
