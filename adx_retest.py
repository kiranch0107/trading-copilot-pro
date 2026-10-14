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

AMENDMENT 2026-10-14 — THE LIVE CONFIG, LONGS ONLY, AND THE MARGINAL BAND

The 2026-09-10 run measured the RECORD config (ADX gate on bt.DEFAULTS: 1.0 /
3.0, regime off, both sides) before the gap-fill fix (BACKLOG 21). The owner's
question is about the config the scanner runs: "existing parameters, ADX 30
instead of 35". Three flags, default off so the old command means what it did:

    --config live    every shared key from signal_core.DEFAULTS (1.25 / 4.0,
                     volume 1.2, regime on) via backtest.CFG_TO_PARAMS, so the
                     cfg cannot drift from the scanner by hand
    --longs-only     drop Bearish trades, as the direction gate does live
    --band LO,HI     the MARGINAL trades a threshold change adds or removes:
                     signal-bar ADX in [LO, HI) from the ungated (ADX 0) run

The band is the honest unit for "30 instead of 35": the ADX-30 set is the
ADX-35 set plus exactly the [30, 35) trades, so the aggregate at 30 can only
move by what that band carries. Nested aggregates hide it; the band shows it.
Bands are cut from ONE ungated run by the signal bar's ADX (carried on each
trade's setup), so the 3-bar cooldown is the ungated run's, not the gated
run's: a gated re-run would admit a few bars the ungated run's cooldown
consumed. Stated, not corrected. Pre-registered, with the bar the band must
clear, in results/adx_live_preregistration.md.
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
CI_Z = 1.96


def live_cfg(tickers: list[str], years: int, adx_min: float) -> dict:
    """
    The scanner's configuration as a backtest cfg.

    Every key backtest.build_signal_params() reads is taken from
    signal_core.DEFAULTS through backtest.CFG_TO_PARAMS, so this is the live
    signal by construction and not a hand copy of it (the drift BACKLOG 23 is
    about). Only adx_min is then overridden, which is the one thing under test.
    Everything else (max_hold 20, cooldown 3, slippage 2 bps) is bt.DEFAULTS.
    """
    import backtest as bt
    import signal_core as sc
    cfg = dict(bt.DEFAULTS, tickers=list(tickers), years=years)
    for key, field in bt.CFG_TO_PARAMS.items():
        cfg[key] = getattr(sc.DEFAULTS, field)
    cfg["adx_min"] = adx_min
    return cfg


def signal_adx(trade: dict) -> float | None:
    """The signal bar's ADX, carried on the trade's setup by evaluate_signal()."""
    setup = trade.get("setup") or {}
    v = setup.get("adx")
    return float(v) if v is not None else None


def bands(trades: list[dict], levels: list[float]) -> list[dict]:
    """
    Partition one run's trades by signal-bar ADX into [l0, l1), [l1, l2), ...,
    [l_last, inf). A trade with no ADX reading is dropped and counted.
    Each band carries n, mean, sd, p, a CI, the MDE and the two floors.
    """
    edges = sorted(set(float(x) for x in levels))
    out = []
    missing = 0
    for i, lo in enumerate(edges):
        hi = edges[i + 1] if i + 1 < len(edges) else math.inf
        rs = []
        for t in trades:
            a = signal_adx(t)
            if a is None:
                continue
            if lo <= a < hi and t.get("r") is not None:
                rs.append(float(t["r"]))
        n = len(rs)
        if n >= 2:
            arr = np.array(rs, dtype=float)
            mean, sd = float(arr.mean()), float(arr.std(ddof=1))
        else:
            mean, sd = (float(rs[0]) if rs else 0.0), 0.0
        se = sd / math.sqrt(n) if n > 1 and sd > 0 else 0.0
        mde = pw.mde(sd, n, alpha=ALPHA, power=POWER) if n > 1 and sd > 0 else math.inf
        out.append({"lo": lo, "hi": hi, "n": n, "mean": mean, "sd": sd,
                    "p": t_pvalue(mean, sd, n), "ci_lo": mean - CI_Z * se,
                    "ci_hi": mean + CI_Z * se, "mde": mde,
                    "powered": abs(mean) >= mde, "big_enough": n >= MIN_TRADES})
    missing = sum(1 for t in trades if signal_adx(t) is None)
    for b in out:
        b["missing"] = missing
    return out


def judge_band(band: dict) -> dict:
    """
    The pre-registered bar for "add this band to the alerted set"
    (results/adx_live_preregistration.md): the band's mean R must be positive,
    significant at ALPHA on its own single test, detectable at POWER, and
    carried by at least MIN_TRADES trades. Anything else is NOT ESTABLISHED.
    """
    reasons = []
    if band["n"] < MIN_TRADES:
        reasons.append(f"n = {band['n']} is under the {MIN_TRADES}-trade floor")
    if band["mean"] <= 0:
        reasons.append(f"mean R {band['mean']:+.3f} is not positive")
    elif band["p"] > ALPHA:
        reasons.append(f"p = {band['p']:.3f} > {ALPHA}: not distinguishable from zero")
    if not band["powered"]:
        reasons.append(f"|mean| {abs(band['mean']):.3f} is under the detectable "
                       f"{band['mde']:.3f} R at {POWER:.0%} power — INCONCLUSIVE, "
                       f"which is not 'no edge'")
    return {"passed": not reasons, "reasons": reasons}


def report_bands(rows: list[dict], question: tuple[float, float] | None,
                 side: str) -> None:
    print()
    print(f"  MARGINAL BANDS — signal-bar ADX, from the ungated run ({side})")
    hdr = (f"  {'band':>14} {'trades':>7} {'exp R':>8} {'sd':>6} {'p':>8} "
           f"{'95% CI':>18} {'detectable':>11} {'powered':>8} {'n>=floor':>9}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for b in rows:
        hi = "inf" if b["hi"] == math.inf else f"{b['hi']:.0f}"
        print(f"  {'[' + format(b['lo'], '.0f') + ', ' + hi + ')':>14} {b['n']:>7} "
              f"{b['mean']:>+8.3f} {b['sd']:>6.2f} {b['p']:>8.4f} "
              f"{'[' + format(b['ci_lo'], '+.3f') + ', ' + format(b['ci_hi'], '+.3f') + ']':>18} "
              f"{b['mde']:>11.3f} {'yes' if b['powered'] else 'NO':>8} "
              f"{'yes' if b['big_enough'] else 'NO':>9}")
    if rows and rows[0]["missing"]:
        print(f"  ({rows[0]['missing']} trade(s) carried no ADX reading and sit in no band)")
    if question is None:
        return
    lo, hi = question
    hit = [b for b in rows if b["lo"] == lo and b["hi"] == hi]
    print()
    if not hit:
        print(f"  band [{lo:.0f}, {hi:.0f}) is not one of the cuts above — pass both "
              f"edges in --levels")
        return
    v = judge_band(hit[0])
    tag = f"band [{lo:.0f}, {hi:.0f})"
    if v["passed"]:
        print(f"  {tag}: SUPPORTED — positive, significant, detectable, n = {hit[0]['n']}")
    else:
        print(f"  {tag}: NOT ESTABLISHED")
        for r in v["reasons"]:
            print(f"    - {r}")
        print("  Lowering the threshold to admit this band is then a choice about")
        print("  how many alerts to receive, not a measured improvement.")


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

    # ── THE LIVE CONFIG IS signal_core.DEFAULTS, by construction ──
    # build_signal_params() is the only reader of the cfg; if live_cfg() maps
    # every shared key, the params it yields ARE the scanner's. A missed key
    # (regime, volume) would quietly test a config nobody runs — BACKLOG 23.
    import signal_core as _sc
    _lc = live_cfg(["AAPL"], 10, 35.0)
    assert _bt.build_signal_params(_lc) == _sc.DEFAULTS, (
        _bt.build_signal_params(_lc), "live_cfg(35) must build the scanner's params exactly")
    assert _bt.build_signal_params(live_cfg(["AAPL"], 10, 30.0)) == \
        _sc.DEFAULTS.__class__(**{**_sc.DEFAULTS.__dict__, "adx_min": 30.0}), \
        "live_cfg(30) differs from the scanner in adx_min and nothing else"
    assert _bt.build_signal_params(dict(_bt.DEFAULTS, tickers=["AAPL"], years=10)) \
        != _sc.DEFAULTS, "the record config is NOT the live config (or this test is vacuous)"
    assert _lc["max_hold"] == _bt.DEFAULTS["max_hold"] and _lc["years"] == 10
    print("live_cfg         : builds signal_core.DEFAULTS exactly; record differs")

    # ── BANDS: half-open on the signal bar's ADX, boundary on the low edge ──
    def _tr(adx, r, trend="Bullish"):
        return {"r": r, "trend": trend, "setup": {"adx": adx}}
    _fx = [_tr(29.9, 1.0), _tr(30.0, 0.5), _tr(34.9, -0.5), _tr(35.0, 2.0),
           _tr(40.0, 1.0), {"r": 0.3, "trend": "Bullish", "setup": {}}]
    _bd = bands(_fx, [0.0, 30.0, 35.0])
    assert [(b["lo"], b["hi"], b["n"]) for b in _bd] == \
        [(0.0, 30.0, 1), (30.0, 35.0, 2), (35.0, math.inf, 2)], _bd
    assert _bd[1]["mean"] == 0.0 and _bd[2]["mean"] == 1.5, _bd
    assert _bd[0]["missing"] == 1, "a trade with no ADX reading is counted, not binned"
    assert bands(_fx, [35.0, 30.0, 0.0]) == _bd, "edges are sorted, order of --levels is free"
    print("bands            : [30,35) takes 30.0 and 34.9, not 29.9 or 35.0; missing counted")

    # ── judge_band: the pre-registered bar, and it can say yes ──
    _good = bands([_tr(32.0, 0.6 + 0.01 * (i % 5)) for i in range(200)], [30.0, 35.0])[0]
    assert judge_band(_good)["passed"], judge_band(_good)
    _thin = bands([_tr(32.0, 0.6 + 0.01 * (i % 5)) for i in range(20)], [30.0, 35.0])[0]
    _vt = judge_band(_thin)
    assert not _vt["passed"] and any(str(MIN_TRADES) in r for r in _vt["reasons"]), _vt
    _null = bands([_tr(32.0, 0.02 * (i % 7 - 3)) for i in range(400)], [30.0, 35.0])[0]
    _vn = judge_band(_null)
    assert not _vn["passed"] and any("INCONCLUSIVE" in r for r in _vn["reasons"]), _vn
    _neg = bands([_tr(32.0, -0.5 - 0.01 * (i % 5)) for i in range(200)], [30.0, 35.0])[0]
    assert any("not positive" in r for r in judge_band(_neg)["reasons"])
    print("judge_band       : a real band passes; thin, null and negative bands do not")

    # ── main() wiring for the three flags: live cfg reaches run(), shorts are
    # dropped, the fingerprint line survives the swallowed stdout, the band is
    # judged. A flag that parsed but never reached run() would pass every
    # unit test above.
    seen_cfgs = []
    def _fake_run2(cfg):
        seen_cfgs.append(dict(cfg))
        print("  data: v2-deadbeefcafe0000  (1 series, 1 cached / 0 fetched, oldest 1d)")
        n = max(3, int(800 - cfg["adx_min"] * 18))
        return [{"r": 0.01 * (i % 7 - 3), "trend": "Bullish" if i % 3 else "Bearish",
                 "setup": {"adx": 20.0 + (i % 30)}} for i in range(n)]
    try:
        _bt.run = _fake_run2
        _sys.argv = ["adx_retest.py", "--levels", "0,30,35", "--tickers", "AAPL",
                     "--config", "live", "--longs-only", "--band", "30,35"]
        out2 = io.StringIO()
        with contextlib.redirect_stdout(out2), contextlib.redirect_stderr(io.StringIO()):
            rc2 = main()
    finally:
        _bt.run, _sys.argv = real_run, real_argv
    assert [c["adx_min"] for c in seen_cfgs] == [0.0, 30.0, 35.0], seen_cfgs
    assert all(c["use_regime"] is True and c["atr_stop_mult"] == 1.25
               and c["volume_mult"] == 1.2 for c in seen_cfgs), (
        seen_cfgs[0], "--config live must reach backtest.run()")
    _txt = out2.getvalue()
    assert "data: v2-deadbeefcafe0000" in _txt, "the fingerprint line must survive"
    assert "config: live" in _txt and "longs only" in _txt, _txt
    assert "band [30, 35)" in _txt, _txt
    assert "[30, 35)" in _txt and "[35, inf)" in _txt, _txt
    # 800 fake trades at ADX 0, a third Bearish: the ADX 0 row must show 2/3.
    _row0 = next(ln for ln in _txt.splitlines() if ln.strip().startswith("0 "))
    assert int(_row0.split()[1]) == 533, (_row0, "--longs-only must drop the Bearish third")
    assert rc2 in (0, 1)
    print("flag wiring      : live cfg reached run(), shorts dropped, fingerprint kept, band judged")

    # ── the flags are OFF by default: the 2026-09-10 command still means record, both sides ──
    try:
        _bt.run = _fake_run2; seen_cfgs.clear()
        _sys.argv = ["adx_retest.py", "--levels", "25", "--tickers", "AAPL"]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            main()
    finally:
        _bt.run, _sys.argv = real_run, real_argv
    assert seen_cfgs[0]["use_regime"] is False and seen_cfgs[0]["atr_stop_mult"] == 1.0, \
        "default must still be the record config"
    print("defaults         : no flag -> record config, both sides (the old command is unchanged)")

    # ── --band edges must be levels, or the band would be cut from nothing ──
    try:
        _bt.run = _fake_run2
        _sys.argv = ["adx_retest.py", "--levels", "0,35", "--tickers", "AAPL", "--band", "30,35"]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc3 = main()
    finally:
        _bt.run, _sys.argv = real_run, real_argv
    assert rc3 == 2, "a --band edge missing from --levels must refuse to run"
    print("band guard       : --band 30,35 without 30 in --levels is refused")

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
    ap.add_argument("--config", choices=("record", "live"), default="record",
                    help="record: bt.DEFAULTS geometry (the 2026-09-10 run); "
                         "live: signal_core.DEFAULTS, what the scanner runs")
    ap.add_argument("--longs-only", action="store_true",
                    help="drop Bearish trades, as the live direction gate does")
    ap.add_argument("--band", default=None, metavar="LO,HI",
                    help="judge the marginal band [LO, HI) of signal-bar ADX, "
                         "cut from the lowest level's run (both must be levels)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    import contextlib
    import io

    import backtest as bt

    levels = [float(x) for x in a.levels.split(",") if x.strip()]
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]

    question = None
    if a.band:
        parts = [float(x) for x in a.band.split(",")]
        if len(parts) != 2 or parts[0] >= parts[1]:
            print(f"  ! --band wants LO,HI with LO < HI, got {a.band!r}", file=sys.stderr)
            return 2
        question = (parts[0], parts[1])
        if any(x not in levels for x in question):
            print(f"  ! --band edges {a.band} must both be in --levels "
                  f"{','.join(str(x) for x in levels)}", file=sys.stderr)
            return 2

    side = "longs only" if a.longs_only else "both sides"
    means, sds, ns, kept = [], [], [], []
    lowest_trades, lowest_level, data_line = None, None, None
    for lv in levels:
        if a.config == "live":
            cfg = live_cfg(tickers, a.years, lv)
        else:
            cfg = dict(bt.DEFAULTS, tickers=tickers, years=a.years, adx_min=lv)
        # run() prints a full report per level; we want the trades, not five
        # reports. Errors still surface — only stdout is swallowed. The one
        # line kept is the bar-cache fingerprint, so the run says what it read.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            trades = bt.run(cfg)
        if trades is None:
            print("  ! backtest.run() returned None — it is expected to return "
                  "the trade list. Re-check that change before trusting a sweep.",
                  file=sys.stderr)
            return 2
        if data_line is None:
            data_line = next((ln.strip() for ln in buf.getvalue().splitlines()
                              if ln.strip().startswith("data:")), None)
        if a.longs_only:
            trades = bt.split_by_side(trades, "Bullish")
        if lowest_level is None or lv < lowest_level:
            lowest_level, lowest_trades = lv, trades
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
    print(f"  config: {a.config} — ADX gate swept; stop {cfg['atr_stop_mult']} / "
          f"target {cfg['atr_tgt_mult']} ATR, volume {cfg['volume_mult']}x, regime "
          f"{'on' if cfg['use_regime'] else 'off'}, hold {cfg['max_hold']}, {side}")
    print(f"  {data_line or 'data: no fingerprint line found in backtest output'}")
    rc = report(judge(kept, means, sds, ns))
    if lowest_trades is not None:
        report_bands(bands(lowest_trades, kept), question, side)
        if lowest_level and lowest_level > 0:
            print(f"  (bands cut from the ADX {lowest_level:g} run, not an ungated one: "
                  f"the lowest band is truncated at that gate)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
