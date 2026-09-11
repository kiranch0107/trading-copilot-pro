"""
Is the 1x ATR stop mis-calibrated? Paired test across stop widths.

Pre-registered in results/atr_stop_preregistration.md, committed BEFORE this
file existed. The bar is fixed there and is not restated loosely here — it is
implemented in verdict() below, clause for clause.

WHAT KIND OF QUESTION THIS IS

Not "is there an anomaly". The signal has no demonstrated edge at a 1x ATR stop;
this asks whether that is a property of the SIGNAL or of the STOP. A 1x ATR stop
sits inside the instrument's own daily noise band, so trades are stopped by
ordinary range rather than by the thesis failing — and ~17.5% of trades die on
the bar they opened on, a group whose arithmetic accounts for the entire -0.048 R.

WHY THERE IS NO MONOTONIC EXPECTANCY CLAUSE

Every other study here required expectancy to grade with the swept parameter.
That is the WRONG prediction for this hypothesis. Expectancy is

    E = WR x payoff - (1 - WR)

and widening the stop raises WR while lowering payoff, so the product has an
interior optimum, not a ramp. Monotone expectancy across every width would be
evidence something is wrong, not evidence of an effect.

What the mechanism predicts monotonically is tested instead: the same-bar death
rate must FALL and the win rate must RISE as the stop widens. Those are direct
consequences of a wider stop, not composites.

PAIRED BY SIGNAL

Trades are matched on (ticker, entry_date) across arms. A signal that hits
neither stop produces an IDENTICAL trade in both, so its delta is exactly zero
and the variance concentrates in the pairs that actually differ. That is
materially more powerful than comparing two independent samples of trades.

GUARDS, FALSIFIED

Eight guards were broken one at a time and the selftest re-run; every break was
caught, each at its own assertion:

    same-bar counted as hold <= 1        -> "died on its entry bar"
    clause 5 accepts -0.05 R             -> "a -0.020 R system must not pass"
    a single pair reports a number        -> "looks measured"
    unmatched signals pair anyway         -> KeyError (not an assertion: pairing
                                             a signal one arm never produced
                                             cannot be done at all)
    monotone("falls") always True         -> the inversion assertion
    clause 2's power floor removed        -> "UNMEASURABLE"
    arm() runs BASELINE in every arm      -> "must run every pre-registered width"
    arm() sweeps the target too           -> "the target must be FIXED"

The first of those was a REAL bug, not a hypothetical: same-bar deaths were
counted as hold <= 1, which is two bars. It survived because the stats lived
inside arm() and the selftest re-implemented them in its fixture builder. The
aggregation is now summarise(), and the fixtures call it.

NO CONFIRMATION IS POSSIBLE. Both tranches are spent. Whatever this returns is
in-sample against everything and out-of-sample against nothing, so a positive
result stays a hypothesis permanently — which is why the bar's clause 5 demands
the destination (expectancy above zero) rather than merely the direction.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import math
import sys

import numpy as np

import power_check as pw
import record_recheck as rr

STOPS = (1.0, 1.25, 1.5, 2.0)
BASELINE = 1.0
TARGET_MULT = 3.0
ALPHA = 0.05


def summarise(trades: list[dict], stop_mult: float) -> dict:
    """
    Arm-level stats from a list of backtest trades.

    Split out of arm() deliberately. While this lived inside arm(), the only
    way to test it was to re-implement it in the selftest's fixture builder —
    and a re-implementation tests the fixture, not the code. It hid a real bug:
    same-bar deaths were counted as hold <= 1, which is TWO bars. backtest.py's
    own print_hold_profile() uses hold == 0, and the pre-registration says "die
    on the bar they opened on". hold is exit_i - entry_i, so same bar is zero.
    The selftest now drives THIS function, so that class of bug fails loudly.
    """
    by_signal = {}
    for t in trades:
        if t.get("r") is None:
            continue
        key = (t.get("ticker"), str(t.get("entry_date")))
        by_signal[key] = t

    rs = np.array([t["r"] for t in by_signal.values()], dtype=float)
    holds = [t.get("hold") for t in by_signal.values()]
    same_bar = sum(1 for h in holds if h is not None and h == 0)
    wins = int((rs > 0).sum())
    won = rs[rs > 0]
    lost = rs[rs <= 0]
    return {
        "stop": stop_mult, "by_signal": by_signal, "n": len(rs),
        "expectancy": float(rs.mean()) if len(rs) else float("nan"),
        "sd": float(rs.std(ddof=1)) if len(rs) > 1 else float("nan"),
        "win_rate": 100.0 * wins / len(rs) if len(rs) else float("nan"),
        "same_bar_pct": 100.0 * same_bar / len(rs) if len(rs) else float("nan"),
        "payoff": (float(won.mean()) / abs(float(lost.mean()))
                   if len(won) and len(lost) and lost.mean() != 0 else float("nan")),
    }


def arm(tickers: list[str], years: int, stop_mult: float) -> dict:
    """One stop width: run the backtest at that stop, then summarise it."""
    import backtest as bt
    cfg = dict(bt.DEFAULTS, tickers=tickers, years=years,
               atr_stop_mult=stop_mult, atr_tgt_mult=TARGET_MULT)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        trades = bt.run(cfg)
    return summarise(trades or [], stop_mult)


def pair(base: dict, other: dict) -> dict:
    """
    Paired deltas on signals present in BOTH arms.

    Signals missing from one arm are dropped rather than treated as zero: a
    setup that exists at one stop width and not another is a different fact
    from a setup whose outcome was unchanged, and conflating them would hide
    exactly the trades the hypothesis is about.
    """
    shared = set(base["by_signal"]) & set(other["by_signal"])
    deltas, identical = [], 0
    for k in shared:
        d = other["by_signal"][k]["r"] - base["by_signal"][k]["r"]
        if d == 0:
            identical += 1
        deltas.append(d)
    arr = np.array(deltas, dtype=float)
    n = len(arr)
    # The unmatched counts are attached FIRST and unconditionally. The early
    # return below used to drop them, which loses the diagnostic precisely when
    # it matters most: a comparison with almost no shared signals means the two
    # arms selected different setups, and "n < 2" alone does not say that.
    common = {"n": n,
              "only_in_base": len(set(base["by_signal"]) - shared),
              "only_in_other": len(set(other["by_signal"]) - shared)}
    if n < 2:
        return {**common, "identical": identical, "differing": n - identical,
                "mean": float("nan"), "sd": float("nan")}
    sd = float(arr.std(ddof=1))
    mean = float(arr.mean())
    se = sd / math.sqrt(n)
    return {**common, "identical": identical, "differing": n - identical,
            "mean": mean, "sd": sd, "se": se,
            "ci": (mean - 1.96 * se, mean + 1.96 * se),
            "p": math.erfc(abs(mean) / se / math.sqrt(2.0)) if se > 0 else 1.0,
            "mde": pw.mde(sd, n)}


def monotone(values: list[float], direction: str) -> bool:
    """Strictly non-increasing / non-decreasing, ignoring NaN."""
    v = [x for x in values if x == x]
    if len(v) < 2:
        return False
    if direction == "falls":
        return all(b <= a for a, b in zip(v, v[1:]))
    return all(b >= a for a, b in zip(v, v[1:]))


def verdict(arms: list[dict], pairs: dict, best_stop: float) -> tuple[bool, list[str]]:
    """The pre-registered bar, clause for clause."""
    reasons = []
    pr = pairs.get(best_stop, {})

    # 1. paired mean positive, CI clears zero
    if not (pr.get("ci") and pr["ci"][0] > 0):
        ci = pr.get("ci")
        reasons.append(
            f"clause 1: paired 95% CI "
            f"{f'[{ci[0]:+.3f}, {ci[1]:+.3f}]' if ci else '(unavailable)'} "
            f"does not clear zero")
    # 2. powered
    if pr.get("mde") is not None and abs(pr.get("mean", 0)) < pr["mde"]:
        reasons.append(
            f"clause 2: |delta| {abs(pr['mean']):.3f} is under the detectable "
            f"{pr['mde']:.3f} — UNMEASURABLE, not 'no effect'")
    # 3 & 4. the monotone predictions the mechanism actually makes
    if not monotone([a["same_bar_pct"] for a in arms], "falls"):
        reasons.append("clause 3: same-bar death rate does not fall monotonically "
                       "with stop width — the mechanism's most direct prediction")
    if not monotone([a["win_rate"] for a in arms], "rises"):
        reasons.append("clause 4: win rate does not rise monotonically with stop width")
    # 5. the destination, not the direction
    best = next((a for a in arms if a["stop"] == best_stop), None)
    if best is None or not (best["expectancy"] > 0):
        e = best["expectancy"] if best else float("nan")
        reasons.append(
            f"clause 5: expectancy at {best_stop}x is {e:+.3f} R — an improvement "
            f"to a losing system is not a tradeable result")
    return (not reasons), reasons


def report(arms: list[dict], pairs: dict) -> int:
    print("=" * 82)
    print("ATR STOP WIDTH — is the exit rule mis-calibrated?")
    print("=" * 82)
    print(f"  target fixed at {TARGET_MULT}x ATR; baseline stop {BASELINE}x")
    print(f"  paired on (ticker, entry_date); identical trades contribute delta 0")
    print("=" * 82)
    hdr = (f"  {'stop':>6} {'trades':>7} {'exp R':>8} {'win%':>7} {'same-bar%':>10} "
           f"{'payoff':>7} {'nominal BE%':>12}")
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    for a in arms:
        be = 100.0 / (1.0 + a["payoff"]) if a["payoff"] == a["payoff"] else float("nan")
        print(f"  {a['stop']:>6.2f} {a['n']:>7} {a['expectancy']:>+8.3f} "
              f"{a['win_rate']:>7.1f} {a['same_bar_pct']:>10.1f} "
              f"{a['payoff']:>7.2f} {be:>12.1f}")

    print(f"\n  PAIRED vs {BASELINE}x baseline")
    hdr2 = (f"  {'stop':>6} {'pairs':>7} {'same':>7} {'differ':>7} {'mean dR':>9} "
            f"{'95% CI':>18} {'detectable':>11} {'p':>8}")
    print(hdr2); print("  " + "-" * (len(hdr2) - 2))
    for s in STOPS:
        if s == BASELINE:
            continue
        p = pairs.get(s, {})
        if not p or p.get("mean") != p.get("mean"):
            print(f"  {s:>6.2f}   (no comparable pairs)")
            continue
        print(f"  {s:>6.2f} {p['n']:>7} {p['identical']:>7} {p['differing']:>7} "
              f"{p['mean']:>+9.3f} [{p['ci'][0]:>+7.3f},{p['ci'][1]:>+7.3f}] "
              f"{p['mde']:>11.3f} {p['p']:>8.4f}")

    # The candidate is the widest stop whose paired mean is highest — chosen by
    # the data, then judged against a bar fixed before the data existed.
    cands = [(pairs[s]["mean"], s) for s in STOPS
             if s != BASELINE and pairs.get(s, {}).get("mean") == pairs.get(s, {}).get("mean")]
    best_stop = max(cands)[1] if cands else BASELINE

    print()
    print("=" * 82)
    print("PRE-REGISTERED VERDICT")
    print("=" * 82)
    print(f"  best paired arm: {best_stop}x ATR")
    ok, why = verdict(arms, pairs, best_stop)
    if ok:
        print(f"  PASS — every clause clears at {best_stop}x.")
        print(f"  This licenses changing atr_stop_mult. It does NOT license calling")
        print(f"  it confirmed: both tranches are spent, so this is out-of-sample")
        print(f"  against nothing and remains a hypothesis.")
    else:
        print("  NOT ESTABLISHED")
        for w in why:
            print(f"    - {w}")
        print()
        print("  A null here is a result. The stop width is not the reason the")
        print("  signal is flat. Do NOT sweep more widths hoping one clears —")
        print("  there is no clean data left to confirm anything a sweep finds.")
    print("=" * 82)
    return 0 if ok else 1


def selftest() -> int:
    print("atr_stop_test.py selftest")
    print("=" * 72)

    def mk(stop, rows):
        """
        rows: (ticker, date, r, hold) -> a REAL summarise() result.

        This builds trades in backtest.py's own shape and runs the production
        aggregator over them. It deliberately does not re-compute the stats:
        the previous version did, which meant every stat assertion below was
        checking the fixture against itself.
        """
        trades = [{"ticker": t, "entry_date": d, "r": r, "hold": h}
                  for t, d, r, h in rows]
        return summarise(trades, stop)

    # ── pairing: unchanged trades contribute EXACTLY zero ──
    base = mk(1.0, [("A", "d1", -1.0, 1), ("A", "d2", 3.0, 9), ("B", "d3", -1.0, 4)])
    wide = mk(1.5, [("A", "d1", 2.0, 6), ("A", "d2", 3.0, 9), ("B", "d3", -1.5, 5)])
    p = pair(base, wide)
    assert p["n"] == 3 and p["identical"] == 1, p
    assert abs(p["mean"] - ((3.0 + 0.0 - 0.5) / 3)) < 1e-9, p["mean"]
    print(f"pairing          : 3 pairs, 1 identical (delta 0), mean {p['mean']:+.3f}")

    # ── a signal present in only one arm is DROPPED, not zeroed ──
    lopsided = mk(1.5, [("A", "d1", 2.0, 6), ("Z", "d9", 5.0, 3)])
    p2 = pair(base, lopsided)
    assert p2["n"] == 1, f"only the shared signal may pair, got {p2['n']}"
    assert p2["only_in_other"] == 1 and p2["only_in_base"] == 2, p2
    assert p2["mean"] != p2["mean"], (
        "a single pair cannot support a mean with a standard error; it must "
        "report nan rather than a number that looks measured")
    assert p2["differing"] == 1, p2
    _ = (
        "a setup that exists at one width and not the other is a different fact "
        "from one whose outcome was unchanged; zeroing it would hide exactly the "
        "trades this hypothesis is about")
    print(f"unmatched signals: dropped and counted, never treated as delta 0")

    # ── monotone helper, both directions ──
    assert monotone([17.5, 14.0, 11.0, 8.0], "falls")
    assert not monotone([17.5, 14.0, 15.0, 8.0], "falls")
    assert monotone([31.0, 33.0, 35.0], "rises")
    assert not monotone([31.0, 30.0, 35.0], "rises")
    print("monotone         : catches a single inversion in either direction")

    # ── SAME BAR MEANS ZERO BARS HELD, not one ──
    # backtest.py sets hold = exit_i - entry_i and its own print_hold_profile()
    # counts hold == 0. Counting hold <= 1 silently folded in every next-day
    # exit, inflating the rate this whole hypothesis is built on.
    sb = mk(1.0, [("A", "d1", -1.0, 0), ("A", "d2", -1.0, 1),
                  ("A", "d3", 2.0, 7)])
    assert abs(sb["same_bar_pct"] - 100.0 / 3) < 1e-9, (
        f"one of three trades died on its entry bar; got "
        f"{sb['same_bar_pct']:.1f}% — a hold of 1 is the NEXT bar, not the same one")
    assert abs(sb["win_rate"] - 100.0 / 3) < 1e-9, sb["win_rate"]
    assert abs(sb["payoff"] - 2.0) < 1e-9, sb["payoff"]
    print("same bar         : hold == 0 only; a next-day exit is not a same-bar death")

    # ── trades with no R are dropped, not counted as zero ──
    nr = summarise([{"ticker": "A", "entry_date": "d1", "r": None, "hold": 0},
                    {"ticker": "A", "entry_date": "d2", "r": 1.0, "hold": 3}], 1.0)
    assert nr["n"] == 1 and nr["expectancy"] == 1.0, nr
    print("unfilled trades  : dropped before the mean, never counted as 0 R")

    # ── THE BAR: clause 5 must sink a significant improvement that still loses ──
    arms = [mk(1.0, [("A", f"d{i}", -0.048, 5) for i in range(200)]),
            mk(1.5, [("A", f"d{i}", -0.020, 5) for i in range(200)])]
    arms[0]["same_bar_pct"], arms[1]["same_bar_pct"] = 17.5, 11.0
    arms[0]["win_rate"], arms[1]["win_rate"] = 31.0, 34.0
    arms[0]["expectancy"], arms[1]["expectancy"] = -0.048, -0.020
    pr = {1.5: {"n": 200, "identical": 50, "differing": 150, "mean": 0.028,
                "sd": 0.05, "se": 0.0035, "ci": (0.021, 0.035), "p": 1e-9,
                "mde": 0.010}}
    ok, why = verdict(arms, pr, 1.5)
    assert not ok, "a -0.020 R system must not pass"
    assert any("clause 5" in w for w in why), why
    assert not any("clause 1" in w or "clause 2" in w for w in why), (
        f"clauses 1-4 should all CLEAR here — that is the point: the improvement "
        f"is real and significant and still loses money: {why}")
    print(f"clause 5         : -0.048 -> -0.020 clears clauses 1-4 and STILL FAILS")

    # ...and the same setup with a positive destination must pass
    arms[1]["expectancy"] = +0.060
    ok2, why2 = verdict(arms, pr, 1.5)
    assert ok2, f"a positive destination with the same stats must pass: {why2}"
    print("bar liveness     : the same case at +0.060 R passes — bar is not inert")

    # ── each monotone clause fails on its own ──
    arms[0]["same_bar_pct"], arms[1]["same_bar_pct"] = 11.0, 17.5
    assert any("clause 3" in w for w in verdict(arms, pr, 1.5)[1])
    arms[0]["same_bar_pct"], arms[1]["same_bar_pct"] = 17.5, 11.0
    arms[0]["win_rate"], arms[1]["win_rate"] = 34.0, 31.0
    assert any("clause 4" in w for w in verdict(arms, pr, 1.5)[1])
    print("clauses 3 and 4  : each fails independently when its prediction inverts")

    # ── underpowered must read UNMEASURABLE, not 'no effect' ──
    thin = {1.5: dict(pr[1.5], mean=0.004, mde=0.010, ci=(0.001, 0.007))}
    arms[0]["win_rate"], arms[1]["win_rate"] = 31.0, 34.0
    w3 = verdict(arms, thin, 1.5)[1]
    assert any("UNMEASURABLE" in w for w in w3), w3
    print("clause 2         : an undetectable delta reads UNMEASURABLE")

    # ── WIRING: main() must actually run four arms at the pre-registered
    # widths, with the target FIXED. The adx_retest lesson: every selftest
    # there passed while main() called a backtest API that did not exist.
    import backtest as _bt
    for key in ("atr_stop_mult", "atr_tgt_mult", "tickers", "years"):
        assert key in _bt.DEFAULTS, (
            f"arm() builds its cfg from backtest.DEFAULTS and sets {key!r}; "
            f"a rename there would silently run the DEFAULT stop in every arm")
    assert _bt.DEFAULTS["atr_stop_mult"] == BASELINE, (
        f"BASELINE {BASELINE} must be production's stop "
        f"({_bt.DEFAULTS['atr_stop_mult']}), or the baseline arm is not the record")

    calls = []
    def _fake_run(cfg):
        calls.append((cfg["atr_stop_mult"], cfg["atr_tgt_mult"],
                      tuple(cfg["tickers"]), cfg["years"]))
        # Widening the stop turns one loss into a win, exactly as the mechanism
        # says it should; the trades are shaped like backtest.py's own.
        out = []
        for i in range(40):
            died = (i % 4 == 0) and cfg["atr_stop_mult"] < 1.5
            out.append({"ticker": "A", "entry_date": f"d{i}",
                        "r": -1.0 if died else (2.0 if i % 3 == 0 else -0.4),
                        "hold": 0 if died else 6})
        return out
    real_run, real_argv = _bt.run, sys.argv
    try:
        _bt.run = _fake_run
        sys.argv = ["atr_stop_test.py", "--tickers", "AAA,BBB", "--years", "7"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = main()
    finally:
        _bt.run, sys.argv = real_run, real_argv

    assert [c[0] for c in calls] == list(STOPS), (
        f"main() must run every pre-registered width {STOPS}, ran {[c[0] for c in calls]}")
    assert all(c[1] == TARGET_MULT for c in calls), (
        f"the target must be FIXED at {TARGET_MULT}x in every arm; sweeping both "
        f"ends at once makes the comparison uninterpretable: {calls}")
    assert all(c[2] == ("AAA", "BBB") and c[3] == 7 for c in calls), calls
    body = out.getvalue()
    assert "ATR STOP WIDTH" in body and "PRE-REGISTERED VERDICT" in body, body[:400]
    assert rc in (0, 1), rc
    print(f"main() wiring    : {len(calls)} arms at {list(STOPS)}, target pinned "
          f"at {TARGET_MULT}x, exit {rc}")

    print("=" * 72)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default=rr.OOS_12)
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    arms = []
    for s in STOPS:
        print(f"  running stop {s}x ...", file=sys.stderr)
        arms.append(arm(tickers, a.years, s))
        print(f"    {arms[-1]['n']} trades", file=sys.stderr)
    base = next(x for x in arms if x["stop"] == BASELINE)
    pairs = {x["stop"]: pair(base, x) for x in arms if x["stop"] != BASELINE}
    return report(arms, pairs)


if __name__ == "__main__":
    sys.exit(main())
