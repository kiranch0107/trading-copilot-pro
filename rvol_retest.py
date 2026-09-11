"""
Does relative volume improve the signal? And is the gate already live any good?

THIS IS NOT A NEW HYPOTHESIS. signal_core.py line 71 carries

    volume_mult: float = 1.2     # "Strong" needs volume >= avg * this

which IS a relative-volume gate at 1.2, wired into the strength tag, shipped,
and never validated. This is the same situation adx_retest.py found: a filter
running in production on the strength of nobody having checked. ADX turned out
to be HURTING — its dose-response ran backwards at rho -0.70 — so the prior here
is not neutral.

MECHANISM FIRST, per BACKLOG item 13, because "high volume means conviction" is
a description and not a mechanism:

    Volume proxies information arrival. A price move accompanied by unusual
    volume is more likely to reflect participants acting on new information;
    a move on thin volume is more likely to be noise or a liquidity artefact.
    If that holds, the signal's edge should concentrate where volume is high.

    Who is on the other side? Whoever sold into an informed, high-volume move
    without the information. That is a real story, and it is also the one that
    predicts a DOSE-RESPONSE: the edge should grow with relative volume rather
    than appear in one bucket. That prediction is what makes this falsifiable.

WHY THIS COSTS ALMOST NOTHING TO RUN

The data already exists. compute() has produced VOL_AVG20 since it was written,
so RVOL = Volume / VOL_AVG20 is available on every historical bar, and
backtest.simulate_trade() now carries it on each trade. There is no need to
accumulate anything forward: 3,406 trades across 12 tickers and 10 years can be
bucketed today.

It also runs the backtest ONCE. adx_retest had to re-run per threshold because
ADX is a gate that changes which trades exist. RVOL is an ATTRIBUTE of a trade,
so the buckets partition one sample instead of being five overlapping ones.

NO LOOKAHEAD. RVOL is read at the SIGNAL bar, which has closed by the time the
decision is made — entry is the next bar's open. Reading it at the entry bar
would be lookahead, and backtest.py's selftest pins the distinction.

TRANCHE DISCIPLINE. This runs on the ALREADY-CONTAMINATED 12-ticker set, so it
spends nothing. Tranche A is spent only to CONFIRM a pass, never to look first —
the likely outcome is null, and burning a clean tranche on a null is how a
project runs out of the one asset that can settle a positive finding.

PRE-REGISTERED, BEFORE THE FIRST RUN — 2026-09-11

    RVOL improves the signal only if ALL of:
      1. at least one bucket is significant AFTER Holm correction across the
         buckets tested
      2. that bucket clears both its own power threshold and the MIN_TRADES
         floor
      3. expectancy is monotone non-decreasing in RVOL (Spearman rho >= the
         adx_retest bar). A single hot bucket with dips either side is the
         ADX-35 shape and must not be adopted.

    AND, judged separately because it is the question that affects live code:
      4. THE LIVE GATE. Trades at RVOL >= 1.2 must out-earn trades below it by
         more than the pair can detect. If they do not, volume_mult is not
         earning its place, exactly as ADX was not.

    Clause 4 is a SINGLE pre-specified comparison fixed at the value already in
    production, so it carries no multiplicity of its own.
"""
from __future__ import annotations

import argparse
import math
import sys

import numpy as np

import adx_retest as ar          # Holm, Spearman, power gate, sample floor
import power_check as pw
import signal_core as sc

N_BUCKETS = 5
LIVE_GATE = sc.DEFAULTS.volume_mult if hasattr(sc, "DEFAULTS") else 1.2
DEFAULT_TICKERS = ar.DEFAULT_TICKERS
DEFAULT_YEARS = ar.DEFAULT_YEARS


def bucket_by_rvol(trades: list[dict], n_buckets: int = N_BUCKETS) -> dict:
    """
    Split trades into equal-count RVOL buckets and summarise each.

    Equal-count rather than equal-width: RVOL is right-skewed, so fixed width
    would put almost everything in the first bucket and a handful of outliers
    in the last, and the last would then fail the sample floor for a reason
    that has nothing to do with the hypothesis.
    """
    rows = [t for t in trades
            if t.get("rvol") is not None and np.isfinite(t["rvol"])
            and t.get("r") is not None]
    if len(rows) < n_buckets * 2:
        return {}
    rows.sort(key=lambda t: t["rvol"])
    chunks = np.array_split(np.arange(len(rows)), n_buckets)

    labels, means, sds, ns, edges = [], [], [], [], []
    for k, idx in enumerate(chunks):
        if len(idx) == 0:
            # Equal-count splitting cannot produce this. An empty bucket means
            # the splitting strategy was changed to something width-based, and
            # the caller should hear that rather than an IndexError three lines
            # down or a silent nan mean.
            raise ValueError(
                f"bucket {k + 1} of {n_buckets} is empty — equal-count "
                f"splitting cannot do that, so the bucketing has been changed "
                f"to a width-based scheme that leaves gaps on skewed RVOL")
        sub = [rows[i] for i in idx]
        rs = np.array([t["r"] for t in sub], dtype=float)
        labels.append(float(np.mean([t["rvol"] for t in sub])))
        edges.append((sub[0]["rvol"], sub[-1]["rvol"]))
        means.append(float(rs.mean()))
        sds.append(float(rs.std(ddof=1)))
        ns.append(len(rs))
    return {"labels": labels, "means": means, "sds": sds, "ns": ns,
            "edges": edges, "n_total": len(rows)}


def live_gate(trades: list[dict], gate: float = LIVE_GATE) -> dict | None:
    """
    The single pre-specified comparison: RVOL >= gate versus RVOL < gate.

    Fixed at the value ALREADY IN PRODUCTION, so there is no threshold search
    and no multiplicity to correct. This is the clause that can change live
    code.
    """
    hi = np.array([t["r"] for t in trades
                   if t.get("rvol") is not None and t["rvol"] >= gate
                   and t.get("r") is not None], dtype=float)
    lo = np.array([t["r"] for t in trades
                   if t.get("rvol") is not None and t["rvol"] < gate
                   and t.get("r") is not None], dtype=float)
    if len(hi) < 2 or len(lo) < 2:
        return None
    diff = float(hi.mean() - lo.mean())
    # Welch standard error of the difference. The CI and the p-value MUST come
    # from this same number.
    #
    # The first version did not. It built the CI from `se` but computed p from
    # a pooled sd at min(n_hi, n_lo), which on a 897 / 2,509 split threw away
    # the larger group's precision entirely. The two then disagreed in the live
    # output: 95% CI [+0.015, +0.280], which clears zero, printed beside
    # p = 0.0676, which does not. Both described one quantity and were free to
    # contradict each other — the same defect as the "spread exceeded 15% —
    # tightest was 13.3%" message, in statistical clothing.
    se = math.sqrt(hi.var(ddof=1) / len(hi) + lo.var(ddof=1) / len(lo))
    z = abs(diff) / se if se > 0 else 0.0
    p = math.erfc(z / math.sqrt(2.0))
    # The detection threshold is a different question — "what could a sample
    # this size have found" — so it legitimately uses a pooled sd and the
    # smaller group, which is the binding constraint on power.
    pooled = math.sqrt((hi.var(ddof=1) + lo.var(ddof=1)) / 2)
    return {"gate": gate, "n_hi": len(hi), "n_lo": len(lo),
            "mean_hi": float(hi.mean()), "mean_lo": float(lo.mean()),
            "diff": diff, "se": se, "p": p,
            "detectable": pw.mde(pooled, min(len(hi), len(lo))),
            "ci": (diff - 1.96 * se, diff + 1.96 * se)}


def report(b: dict, gate: dict | None) -> int:
    print("=" * 80)
    print("RELATIVE VOLUME — does it improve the signal, and is the live gate earning it?")
    print("=" * 80)
    print(f"  trades with RVOL : {b['n_total']:,}")
    print(f"  buckets          : {len(b['labels'])}, equal-count")
    print(f"  live gate        : volume_mult = {LIVE_GATE:g} (signal_core)")
    print("=" * 80)

    res = ar.judge(b["labels"], b["means"], b["sds"], b["ns"])
    hdr = (f"  {'bucket':>6} {'RVOL range':>16} {'mean':>6} {'trades':>7} "
           f"{'exp R':>8} {'p':>8} {'detectable':>11} {'Holm':>6} {'n>=floor':>9}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for i, r in enumerate(res["rows"]):
        lo, hi = b["edges"][i]
        print(f"  {i + 1:>6} {f'{lo:.2f}-{hi:.2f}':>16} {r['level']:>6.2f} "
              f"{r['n']:>7} {r['mean']:>+8.3f} {r['p']:>8.4f} "
              f"{r['mde']:>11.3f} {'yes' if r['holm'] else 'no':>6} "
              f"{'yes' if r['big_enough'] else 'NO':>9}")

    print()
    print(f"  dose-response (Spearman rho, RVOL vs expectancy): {res['rho']:+.2f}"
          f"   bar >= {ar.MIN_RHO}")

    if gate:
        print()
        print("  " + "=" * 76)
        print(f"  CLAUSE 4 — THE LIVE GATE, RVOL >= {gate['gate']:g}")
        print("  " + "=" * 76)
        print(f"    RVOL >= {gate['gate']:g} : {gate['n_hi']:>6,} trades, "
              f"{gate['mean_hi']:+.3f} R")
        print(f"    RVOL <  {gate['gate']:g} : {gate['n_lo']:>6,} trades, "
              f"{gate['mean_lo']:+.3f} R")
        print(f"    difference     : {gate['diff']:+.3f} R   "
              f"95% CI [{gate['ci'][0]:+.3f}, {gate['ci'][1]:+.3f}]")
        print(f"    detectable     : {gate['detectable']:.3f} R   p = {gate['p']:.4f}")

    print()
    print("=" * 80)
    print("PRE-REGISTERED VERDICT")
    print("=" * 80)
    ok = res["passed"]
    if ok:
        best = max(res["winners"], key=lambda r: r["mean"])
        print(f"  CLAUSES 1-3 PASS — RVOL {best['level']:.2f} survives correction at "
              f"n = {best['n']}, and the sweep grades.")
    else:
        print("  CLAUSES 1-3: NOT ESTABLISHED")
        for w in res["reasons"]:
            print(f"    - {w}")

    gate_ok = False
    if gate:
        clears = gate["ci"][0] > 0 and abs(gate["diff"]) >= gate["detectable"]
        gate_ok = bool(clears)
        print()
        if clears:
            print(f"  CLAUSE 4 PASS — the live volume_mult = {gate['gate']:g} gate "
                  f"earns its place: +{gate['diff']:.3f} R.")
        else:
            print(f"  CLAUSE 4: NOT ESTABLISHED — volume_mult = {gate['gate']:g} is "
                  f"not measurably better than its complement.")
            if gate["diff"] < 0:
                print(f"    And the sign is NEGATIVE ({gate['diff']:+.3f} R): the "
                      f"high-volume half did WORSE. That is the ADX shape, where a")
                print(f"    live filter was costing money rather than saving it.")
    print("=" * 80)
    if not ok:
        print("  A null here is a result. Do NOT re-cut the buckets, add thresholds,")
        print("  or spend tranche A hoping for a different answer — tranche A exists")
        print("  to CONFIRM a pass, not to look for one.")
        print("=" * 80)
    return 0 if (ok and gate_ok) else 1


def selftest() -> int:
    print("rvol_retest.py selftest")
    print("=" * 70)

    def mk(rvol, r):
        return {"rvol": rvol, "r": r}

    # ── equal-count bucketing, not equal-width ──
    # RVOL is right-skewed; a few huge values must not leave the top bucket with
    # three trades while the bottom holds everything.
    # The fixture matters. A first version used a tight cluster plus three far
    # outliers; under equal-WIDTH that leaves buckets EMPTY, so the function
    # crashed with IndexError and this assertion was never reached — the guard
    # looked like it caught the change and had not been exercised at all.
    # This fixture is skewed but spans every width-bin, so equal-width produces
    # buckets that are unequal and NON-empty, which is what the assertion is for.
    skewed = []
    for lo, count in ((0.50, 100), (1.00, 50), (1.50, 20), (2.00, 10), (2.50, 5)):
        skewed += [mk(lo + 0.4 * (i / max(count - 1, 1)), 0.0)
                   for i in range(count)]
    b = bucket_by_rvol(skewed, 5)
    counts = b["ns"]
    assert max(counts) - min(counts) <= 1, (
        f"equal-count buckets must be within one of each other, got {counts} — "
        f"equal-WIDTH on this skewed input gives roughly [100, 50, 20, 10, 5], "
        f"so the top bucket would fail the sample floor for a reason that has "
        f"nothing to do with the hypothesis")
    print(f"bucketing        : skewed 100/50/20/10/5 -> counts {counts}, "
          f"equal by design")

    # and an empty bucket must be a NAMED failure, not an IndexError
    try:
        bucket_by_rvol.__wrapped__  # noqa: B018
    except AttributeError:
        pass
    import numpy as _np
    _saved = _np.array_split
    try:
        _np.array_split = lambda a, n: [a[:len(a)]] + [a[:0]] * (n - 1)
        raised = None
        try:
            bucket_by_rvol(skewed, 5)
        except ValueError as e:
            raised = str(e)
    finally:
        _np.array_split = _saved
    assert raised and "empty" in raised, (
        f"an empty bucket must raise a named ValueError, got {raised!r} — "
        f"otherwise it surfaces as an IndexError or a silent nan mean")
    print("empty bucket     : named ValueError, not an IndexError")

    # ── a planted dose-response must be FOUND ──
    rng = np.random.default_rng(3)
    planted = []
    for i in range(4000):
        rv = float(rng.uniform(0.3, 3.0))
        planted.append(mk(rv, 0.25 * (rv - 1.5) + float(rng.normal(0, 1.0))))
    pb = bucket_by_rvol(planted, 5)
    res = ar.judge(pb["labels"], pb["means"], pb["sds"], pb["ns"])
    assert res["rho"] > 0.9, f"a planted linear effect must grade, rho {res['rho']}"
    assert pb["means"][0] < pb["means"][-1]
    print(f"finds a real one : planted +0.25/RVOL -> rho {res['rho']:+.2f}, "
          f"{pb['means'][0]:+.2f} to {pb['means'][-1]:+.2f} R")

    # ── pure noise must NOT pass ──
    noise = [mk(float(rng.uniform(0.3, 3.0)), float(rng.normal(0, 1.0)))
             for _ in range(4000)]
    nb = bucket_by_rvol(noise, 5)
    assert not ar.judge(nb["labels"], nb["means"], nb["sds"], nb["ns"])["passed"], \
        "pure noise must not clear the bar"
    print("rejects noise    : 4,000 random trades -> NOT ESTABLISHED")

    # ── the live gate compares the right two halves ──
    split = [mk(2.0, 1.0)] * 500 + [mk(0.5, -1.0)] * 500
    g = live_gate(split, gate=1.2)
    assert g["n_hi"] == 500 and g["n_lo"] == 500, g
    assert abs(g["diff"] - 2.0) < 1e-9, g["diff"]
    print(f"live gate split  : {g['n_hi']}/{g['n_lo']} at 1.2, diff {g['diff']:+.1f} R")

    # ── THE CI AND THE p MUST AGREE. This is the bug the live run exposed. ──
    # A 95% CI that clears zero means p < 0.05, always. If the two come from
    # different standard errors they can contradict, and the first version did
    # exactly that in production output.
    #
    # The FIRST version of this guard was itself decorative. It drew random
    # samples at a fixed shift, and every one landed far from the boundary, so
    # CI and p agreed under the buggy formula too and the assertion never fired.
    # Reverting the bug did not fail the suite. A guard that cannot reach the
    # contradiction zone is not a guard.
    #
    # So the fixture is now CONSTRUCTED to sit in that zone, and pinned to the
    # observed live case: 897 vs 2,509 trades, sd ~1.6, difference +0.147 R.
    # There the Welch p is 0.030 (CI clears) while the pooled-at-min(n) p is
    # 0.068 (CI appears not to) — the exact contradiction that was printed.
    def _exact(n, mean, sd, seed):
        """A sample whose mean and sd are exactly as asked, not approximately."""
        x = np.random.default_rng(seed).normal(0, 1, n)
        x = (x - x.mean()) / x.std(ddof=1)
        return mean + sd * x

    for n_hi, n_lo, diff_target in ((897, 2509, 0.147),    # the live case
                                    (897, 2509, 0.120),
                                    (897, 2509, 0.175),
                                    (500, 3000, 0.130),
                                    (2000, 2000, 0.000)):
        sample = ([mk(2.0, float(v)) for v in _exact(n_hi, diff_target, 1.6, 1)] +
                  [mk(0.5, float(v)) for v in _exact(n_lo, 0.0, 1.6, 2)])
        g2 = live_gate(sample, gate=1.2)
        clears = g2["ci"][0] > 0 or g2["ci"][1] < 0
        assert clears == (g2["p"] < 0.05), (
            f"CI and p disagree at n={n_hi}/{n_lo}, diff {g2['diff']:+.3f}: CI "
            f"[{g2['ci'][0]:+.3f}, {g2['ci'][1]:+.3f}] "
            f"{'clears' if clears else 'straddles'} zero but p = {g2['p']:.4f}. "
            f"They describe one quantity and must come from one standard error")
    # and the live case must actually BE in the contradiction zone, or this
    # whole block is testing nothing again.
    live = ([mk(2.0, float(v)) for v in _exact(897, 0.147, 1.6, 1)] +
            [mk(0.5, float(v)) for v in _exact(2509, 0.0, 1.6, 2)])
    gl = live_gate(live, gate=1.2)
    assert gl["ci"][0] > 0 and gl["p"] < 0.05, (
        f"the pinned live case must clear zero under the CORRECT formula, "
        f"got CI {gl['ci']} p {gl['p']:.4f}")
    _pl = 1.6
    _buggy = ar.t_pvalue(gl["diff"], _pl * math.sqrt(2), 897)
    assert _buggy > 0.05, (
        f"the pinned case must be one where the BUGGY formula says p = "
        f"{_buggy:.4f} > 0.05 while the CI clears — otherwise this fixture "
        f"cannot catch the regression it exists for")
    print(f"CI / p agree     : 5 constructed splits; the live 897/2509 case "
          f"(p {gl['p']:.3f} correct vs {_buggy:.3f} buggy) is pinned")

    # and it must detect the SIGN, since a negative diff is the ADX finding
    flipped = [mk(2.0, -1.0)] * 500 + [mk(0.5, 1.0)] * 500
    gf = live_gate(flipped, gate=1.2)
    assert gf["diff"] < 0, "a worse high-volume half must report a NEGATIVE diff"
    print(f"live gate sign   : high-volume-worse reports {gf['diff']:+.1f} R")

    # ── trades without RVOL are dropped, not treated as zero ──
    mixed = [mk(1.0, 1.0), mk(None, 5.0), {"r": 5.0}, mk(float("nan"), 5.0)]
    kept = bucket_by_rvol(mixed * 30, 2)
    assert kept["n_total"] == 30, (
        f"only the 30 trades WITH rvol may be counted, got {kept['n_total']} — "
        f"a missing RVOL is unknown, not 0.0, and treating it as 0 would stuff "
        f"the bottom bucket with trades that have no volume data")
    print("missing RVOL     : dropped and counted, never coerced to 0.0")

    # ── WIRING: main()'s real path, with backtest.run stubbed ──
    # Everything above exercises bucket_by_rvol() and live_gate(). None of it
    # would notice that main() calls a signature that does not exist — which is
    # precisely what happened in adx_retest.py, where the recommended command
    # crashed on an AttributeError while every selftest passed.
    import contextlib, io, sys as _sys
    import backtest as _bt

    assert hasattr(_bt, "run"), "backtest.run must exist"
    calls = []
    def _fake_run(cfg):
        calls.append(cfg["adx_min"])
        g = np.random.default_rng(11)
        return [{"r": float(g.normal(0, 1)), "rvol": float(g.uniform(0.3, 3.0)),
                 "trend": "Bullish", "hold": 5} for _ in range(3000)]

    real_run, real_argv = _bt.run, _sys.argv
    try:
        _bt.run = _fake_run
        _sys.argv = ["rvol_retest.py", "--tickers", "AAPL"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = main()
    finally:
        _bt.run, _sys.argv = real_run, real_argv

    assert calls == [0.0], (
        f"main() must run the UNFILTERED baseline (adx_min 0), got {calls} — "
        f"filtering first would shrink the sample for a gate already shown not "
        f"to help")
    assert rc in (0, 1), f"main() must return a verdict code, got {rc}"
    body = out.getvalue()
    assert "RELATIVE VOLUME" in body, "main() must print the report"
    assert "THE LIVE GATE" in body, (
        "clause 4 must appear — it is the only clause that can change live code")
    print(f"main() wiring    : ran at adx_min {calls[0]:g}, printed the report "
          f"and clause 4, exit {rc}")

    print("=" * 70)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default=DEFAULT_TICKERS)
    ap.add_argument("--years", type=int, default=DEFAULT_YEARS)
    ap.add_argument("--buckets", type=int, default=N_BUCKETS)
    ap.add_argument("--gate", type=float, default=LIVE_GATE)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    import contextlib
    import io

    import backtest as bt

    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    # adx_min = 0: the unfiltered baseline, which is the largest sample and the
    # one ADX 0 showed to be the least-bad arm. Filtering first would shrink the
    # sample for a gate already shown not to help.
    cfg = dict(bt.DEFAULTS, tickers=tickers, years=a.years, adx_min=0.0)
    print(f"Running the backtest once over {len(tickers)} tickers / {a.years}y — "
          f"RVOL is a trade attribute, so one run covers every bucket.",
          file=sys.stderr)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        trades = bt.run(cfg)
    if not trades:
        print("NOTHING MEASURED — do not read anything into this run.",
              file=sys.stderr)
        return 2
    have = sum(1 for t in trades if t.get("rvol") is not None)
    print(f"  {len(trades):,} trades, {have:,} carry RVOL", file=sys.stderr)

    b = bucket_by_rvol(trades, a.buckets)
    if not b:
        print("  ! too few trades with RVOL to bucket", file=sys.stderr)
        return 2
    return report(b, live_gate(trades, a.gate))


if __name__ == "__main__":
    sys.exit(main())
