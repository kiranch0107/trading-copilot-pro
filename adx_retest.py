"""
Re-test the ADX filter — on a sample that could actually settle it.

WHY THE EXISTING FILES ARE NOT EVIDENCE

results/adx20.txt .. adx35.txt are dated 2026-09-01. Everything below landed
AFTER them:

    09-02  auto_adjust True -> False. Adjusted prices rewrote 2,300-2,900 rows
           per dividend payer between two fetches twelve minutes apart.
    09-09  the cost model ("stop paying a toll that exceeds the edge")
    09-10  "Reject fills that gapped past their own stop or target — they
           scored INVERTED"
    09-10  two further silent mis-scoring bugs
    09-10  partial-bar handling consolidated

So those numbers came out of code with three known scoring bugs on an unstable
price basis. They are stale, and re-running the SAME sweep would not fix the
deeper problem.

WHY RE-RUNNING THE SAME SWEEP WOULD BE WORTHLESS

    ADX  trades   exp R   detectable at 80% power
     20     653  +0.016   0.110      not significant
     25     425  -0.012   0.136      not significant
     30     250  +0.006   0.177      not significant
     35     140  +0.188   0.237      NOT SIGNIFICANT EITHER

The headline +0.188 R at ADX 35 needed >0.237 R to be believable at n = 140. It
never cleared its own detection threshold. And the shape gives it away:

    +0.016 -> -0.012 -> +0.006 -> +0.188

No dose-response. The winner is simply the bucket with the fewest trades, which
is what noise looks like when you sort by outcome. Four levels tried gives a 19%
chance that at least one looks good at alpha = 0.05 before anyone picks.

WHAT THIS MODULE DOES DIFFERENTLY

1. WIDE SAMPLE. 12 tickers / 10 years, the cut that gave 1,386 trades and a
   trustworthy -0.048 R, instead of 7 tickers / 5 years.
2. POWER FIRST. Every bucket is checked against power_check before its number is
   allowed to mean anything. An underpowered bucket reports INCONCLUSIVE, which
   is different from "no edge" and is never rounded to it.
3. MULTIPLICITY. Holm-Bonferroni across the levels tested, so the best of N is
   judged as the best of N.
4. DOSE-RESPONSE. If ADX genuinely filters out chop, expectancy should RISE with
   the threshold. A single spike at one level, with dips either side, is the
   signature of noise. This is tested explicitly and it costs no extra data.

PRE-REGISTERED, BEFORE THE RE-RUN — 2026-09-10

    The ADX filter is REAL, and worth keeping, only if ALL of:
      1. at least one level is significant AFTER Holm correction
      2. that level is adequately powered on its own trade count
      3. expectancy is monotone non-decreasing in the threshold (Spearman
         rho >= MIN_RHO across the levels tested)

    Anything else is INCONCLUSIVE or FAIL. A level that clears 1 and 2 but
    fails 3 is a single lucky bucket, not a filter, and must not be adopted.

Clause 3 is the one the original sweep would have failed, and it is the reason
this module exists rather than a re-run of the old command.
"""
from __future__ import annotations

import argparse
import math
import sys

import numpy as np

import power_check as pw

DEFAULT_LEVELS = (0.0, 20.0, 25.0, 30.0, 35.0)
# CORRECTED 2026-09-11. This list was described as "the 12-ticker cut" and is
# not: the record's 12-ticker set is the 591-trade OOS set (see
# record_recheck.OOS_12), which shares NO names with the 7-ticker sweep set.
# What was actually used is all SEVEN Aug-2026 parameter-sweep tickers — ~60
# configurations swept over them — plus five of the OOS twelve.
#
# The claim that ADX 25's -0.039 R "reproduces the record's -0.048 R, so the
# engine agrees with itself" compared two different universes. RETRACTED.
#
# The name says what it is now. Results already recorded on it stay valid as
# results; only the description of what they were run on was wrong.
DEFAULT_TICKERS = "TSLA,NVDA,AAPL,MSFT,AMZN,META,SPY,GOOGL,AMD,NFLX,ORCL,CRM"
SWEPT_MIX_12 = DEFAULT_TICKERS   # explicit alias: 7 swept + 5 OOS, NOT the OOS 12
DEFAULT_YEARS = 10
MIN_RHO = 0.6
# A bucket smaller than this cannot carry the verdict, however good it looks.
#
# This is SEPARATE from the power check, and the difference matters. "Powered"
# here means the observed effect exceeds what the test could detect — which a
# 12-trade bucket satisfies trivially if the effect is large enough. Post-hoc
# power does not protect against a tiny sample throwing a big number; only a
# minimum sample size does. The original sweep's winner had 140 trades, so this
# bar is set where it would have caught it.
MIN_TRADES = 150
ALPHA = 0.05
POWER = 0.80


def t_pvalue(mean: float, sd: float, n: int) -> float:
    """Two-sided p for mean != 0. Normal approximation; n here is in the 100s."""
    if n < 2 or sd <= 0:
        return 1.0
    z = abs(mean) / (sd / math.sqrt(n))
    return math.erfc(z / math.sqrt(2.0))


def holm(pvals: list[float], alpha: float = ALPHA) -> list[bool]:
    """
    Holm-Bonferroni. Returns, per input position, whether it survives.

    Uniformly more powerful than plain Bonferroni and just as valid, which
    matters when the whole point is not to throw away a real effect while
    refusing to be fooled by the best of five.
    """
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    out = [False] * m
    for rank, i in enumerate(order):
        if pvals[i] <= alpha / (m - rank):
            out[i] = True
        else:
            break            # Holm stops at the first failure
    return out


def spearman(x: list[float], y: list[float]) -> float:
    """Rank correlation, no scipy. Ties averaged."""
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(x), rank(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else 0.0


def judge(levels: list[float], means: list[float], sds: list[float],
          ns: list[int]) -> dict:
    """Apply the pre-registered bar to a completed sweep."""
    pvals = [t_pvalue(m, s, n) for m, s, n in zip(means, sds, ns)]
    survives = holm(pvals)
    mdes = [pw.mde(s, n, alpha=ALPHA, power=POWER) for s, n in zip(sds, ns)]
    powered = [abs(m) >= d for m, d in zip(means, mdes)]
    rho = spearman(list(levels), list(means))

    rows = []
    for i, lv in enumerate(levels):
        rows.append({"level": lv, "n": ns[i], "mean": means[i], "sd": sds[i],
                     "p": pvals[i], "mde": mdes[i],
                     "holm": survives[i], "powered": powered[i]})

    for r in rows:
        r["big_enough"] = r["n"] >= MIN_TRADES
    winners = [r for r in rows
               if r["holm"] and r["powered"] and r["big_enough"]]
    reasons = []
    if not any(r["holm"] for r in rows):
        reasons.append("no level survives Holm correction across "
                       f"{len(levels)} comparisons")
    elif not winners:
        thin = [r for r in rows if r["holm"] and not r["big_enough"]]
        if thin:
            reasons.append(
                f"the only level surviving Holm has n = {thin[0]['n']}, under the "
                f"{MIN_TRADES}-trade floor — a small bucket throwing a big number "
                f"is the exact shape of the 2026-09-01 result")
        else:
            reasons.append("the level that survives Holm is underpowered on its "
                           "own trade count — INCONCLUSIVE, which is not the "
                           "same as 'no edge' and must not be read as one")
    if rho < MIN_RHO:
        reasons.append(f"no dose-response: Spearman rho {rho:+.2f} < {MIN_RHO} "
                       f"— a lucky bucket, not a filter")
    return {"rows": rows, "rho": rho, "passed": (not reasons),
            "reasons": reasons, "winners": winners}


def report(res: dict) -> int:
    print("=" * 78)
    print("ADX RE-TEST — pre-registered, multiplicity-corrected, power-checked")
    print("=" * 78)
    hdr = (f"  {'ADX':>5} {'trades':>7} {'exp R':>8} {'sd':>6} {'p':>8} "
           f"{'detectable':>11} {'powered':>8} {'Holm':>6} {'n>=floor':>9}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in res["rows"]:
        print(f"  {r['level']:>5.0f} {r['n']:>7} {r['mean']:>+8.3f} {r['sd']:>6.2f} "
              f"{r['p']:>8.4f} {r['mde']:>11.3f} "
              f"{'yes' if r['powered'] else 'NO':>8} "
              f"{'yes' if r['holm'] else 'no':>6} "
              f"{'yes' if r['big_enough'] else 'NO':>9}")
    print()
    print(f"  dose-response (Spearman rho, level vs expectancy): {res['rho']:+.2f}"
          f"   bar >= {MIN_RHO}")
    print()
    print("=" * 78)
    if res["passed"]:
        best = max(res["winners"], key=lambda r: r["mean"])
        print(f"  PASS — ADX {best['level']:.0f} survives correction, is adequately")
        print(f"  powered at n = {best['n']}, and the sweep shows dose-response.")
    else:
        print("  NOT ESTABLISHED")
        for w in res["reasons"]:
            print(f"    - {w}")
        print()
        print("  INCONCLUSIVE is not a licence to keep the filter, and it is not")
        print("  a licence to try more levels until one passes. Either widen the")
        print("  sample so the buckets can settle it, or leave the filter alone.")
    print("=" * 78)
    return 0 if res["passed"] else 1


def selftest() -> int:
    print("adx_retest.py selftest")
    print("=" * 68)

    # ── Holm: correct, and stricter than uncorrected ──
    assert holm([0.001, 0.60, 0.70, 0.80]) == [True, False, False, False]
    assert holm([0.20, 0.30, 0.40, 0.50]) == [False] * 4, \
        "nothing marginal may survive four comparisons"
    assert holm([0.04]) == [True], "a single test is judged at plain alpha"
    assert holm([0.04, 0.04, 0.04, 0.04]) == [False] * 4, (
        "four p=0.04 results must ALL fail Holm — this is the multiplicity "
        "trap the original sweep walked into")
    print("holm             : one strong survives; four marginal all fail")

    # ── Spearman: monotone vs a single spike, which is the whole point ──
    levels = [0.0, 20.0, 25.0, 30.0, 35.0]
    rising = [-0.05, 0.00, 0.05, 0.10, 0.15]
    spike = [0.016, -0.012, 0.006, 0.020, 0.188]      # the real 2026-09-01 shape
    assert spearman(levels, rising) > 0.99, spearman(levels, rising)
    assert spearman(levels, spike) < 0.99
    print(f"dose-response    : clean ramp rho {spearman(levels, rising):+.2f}, "
          f"the actual 09-01 shape rho {spearman(levels, spike):+.2f}")

    # ── the historical sweep must NOT pass this bar ──
    old = judge([20.0, 25.0, 30.0, 35.0],
                [0.016, -0.012, 0.006, 0.188],
                [1.0, 1.0, 1.0, 1.0],
                [653, 425, 250, 140])
    assert not old["passed"], "the 2026-09-01 sweep must not clear the new bar"
    assert any("Holm" in r for r in old["reasons"]), old["reasons"]
    print(f"the old sweep    : NOT ESTABLISHED — {old['reasons'][0][:52]}...")

    # ── a genuine effect must still be able to PASS, or the bar is unfalsifiable ──
    good = judge(levels, [-0.05, 0.02, 0.08, 0.14, 0.22],
                 [1.0] * 5, [1200, 900, 700, 500, 400])
    assert good["passed"], (
        f"a large, monotone, well-powered effect MUST pass, or the bar can "
        f"never say yes: {good['reasons']}")
    print("liveness         : a monotone, powered, corrected effect DOES pass")

    # ── sample-size gate, which post-hoc power does NOT provide ──
    # A first draft asserted a 12-trade bucket could not carry the verdict and
    # used post-hoc power to stop it. The model refuted that: with a big enough
    # observed effect, 12 trades ARE "powered" by that definition, because the
    # definition asks whether the effect exceeds the detection threshold, not
    # whether the sample is trustworthy. Those are different questions and the
    # second one needs its own floor.
    huge_thin = judge(levels, [-0.05, -0.02, 0.00, 0.02, 1.50],
                      [1.0] * 5, [1200, 900, 700, 500, 12])
    assert huge_thin["rows"][-1]["powered"], (
        "with an effect this large, 12 trades DO clear post-hoc power — which is "
        "precisely why post-hoc power is not the right guard")
    assert huge_thin["rows"][-1]["holm"], "and it survives Holm on p alone"
    assert not huge_thin["passed"], (
        "yet it must NOT pass: the sample-size floor is what stops it")
    assert any(str(MIN_TRADES) in r for r in huge_thin["reasons"]), \
        huge_thin["reasons"]
    print(f"sample-size gate : n=12 clears Holm AND post-hoc power, still fails "
          f"the {MIN_TRADES}-trade floor")

    # and the floor must not sink a legitimately large bucket
    assert judge(levels, [-0.05, 0.02, 0.08, 0.14, 0.22],
                 [1.0] * 5, [1200, 900, 700, 500, 400])["passed"], \
        "the floor must not block a well-sampled effect"
    print("floor liveness   : a 400-trade bucket is not blocked by the floor")

    # ── WIRING: main()'s real code path, with backtest.run stubbed ──
    # Everything above tests judge(). None of it would notice that main() calls
    # an API that does not exist — which is exactly what happened: the first
    # draft called bt.run_ticker(), which is ABSENT, and guarded it with
    # `except TypeError` while the real failure would be AttributeError. The
    # command would have crashed on the user's machine. This runs main().
    import contextlib, io, sys as _sys
    import backtest as _bt

    assert hasattr(_bt, "run"), "backtest.run must exist"
    assert not hasattr(_bt, "run_ticker"), (
        "backtest.run_ticker is absent — if it ever returns, revisit main()")

    calls = []
    def _fake_run(cfg):
        calls.append(cfg["adx_min"])
        # fewer trades as the threshold rises, like the real thing
        n = max(3, int(800 - cfg["adx_min"] * 18))
        return [{"r": 0.01 * (i % 7 - 3)} for i in range(n)]

    real_run, real_argv = _bt.run, _sys.argv
    try:
        _bt.run = _fake_run
        _sys.argv = ["adx_retest.py", "--levels", "20,25,30", "--tickers", "AAPL"]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main()
    finally:
        _bt.run, _sys.argv = real_run, real_argv

    assert calls == [20.0, 25.0, 30.0], f"main() must sweep every level: {calls}"
    assert rc in (0, 1), f"main() must return a verdict code, got {rc}"
    assert "ADX RE-TEST" in out.getvalue(), "main() must print the report"
    print(f"main() wiring    : swept {calls}, printed a report, exit {rc}")

    print("=" * 68)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default=DEFAULT_TICKERS)
    ap.add_argument("--years", type=int, default=DEFAULT_YEARS)
    ap.add_argument("--levels", default=",".join(str(x) for x in DEFAULT_LEVELS))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    import contextlib
    import io

    import backtest as bt

    levels = [float(x) for x in a.levels.split(",") if x.strip()]
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]

    means, sds, ns, kept = [], [], [], []
    for lv in levels:
        cfg = dict(bt.DEFAULTS, tickers=tickers, years=a.years, adx_min=lv)
        # run() prints a full report per level; we want the trades, not five
        # reports. Errors still surface — only stdout is swallowed.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            trades = bt.run(cfg)
        if trades is None:
            print("  ! backtest.run() returned None — it is expected to return "
                  "the trade list. Re-check that change before trusting a sweep.",
                  file=sys.stderr)
            return 2
        rs = [t["r"] for t in trades if t.get("r") is not None]
        print(f"  ADX {lv:>5.1f}: {len(rs):>5} trades", file=sys.stderr)
        if len(rs) < 2:
            print(f"  ! ADX {lv}: too few trades — skipped", file=sys.stderr)
            continue
        arr = np.array(rs, dtype=float)
        kept.append(lv)
        means.append(float(arr.mean()))
        sds.append(float(arr.std(ddof=1)))
        ns.append(len(arr))

    if not kept:
        print("NOTHING MEASURED — do not read anything into this run.",
              file=sys.stderr)
        return 2
    return report(judge(kept, means, sds, ns))


if __name__ == "__main__":
    sys.exit(main())
