"""
Does the EMA20 thesis-break exit earn its place, or cost money?

Pre-registered in results/thesis_exit_preregistration.md, committed BEFORE this
file existed. The bar is fixed there and implemented in verdict() below.

WHY THIS ONE IS DIFFERENT FROM EVERY OTHER STUDY HERE

exit_monitor.py runs this rule on LIVE positions, unattended, on a schedule. It
closes real contracts. The other studies asked whether some hypothesis was worth
trading; this asks whether a rule already spending money is earning its place.

option_decompose found it fires on 42.6% of trades - 596 of 1398, the largest
bucket in the system - contributing -14.7 of the -9.2% total.

THE AMBIGUITY

THESIS exits average -34.4%. A full stop averages -59.3%. So a thesis-break lands
24.9 points ABOVE a stop. Either the rule cuts losers early (it earns its place)
or it closes trades that would have recovered (it destroys value). One number
cannot separate those. Two arms can.

WHY THE PAIRING SHOULD BE PERFECT, AND WHY THAT IS STILL TESTED

run_ticker advances `i += cooldown_bars + 1` from the SIGNAL bar, not from the
exit, so entry dates do not depend on how a trade ended. Every signal should
appear in both arms and the untouched trades should pair at delta exactly zero.

That is an assumption about someone else's loop, so clause 1 counts rather than
trusts. The ATR stop test lost 9.2% of its pairs to exactly this kind of
divergence and only discovered it by counting.

CLAUSE 4 IS REDUNDANT, AND THE MODULE SAYS SO

The pre-registration lists five clauses. Implementing them showed clause 4 (CI
clears zero) cannot fire: mde uses z = 2.802 for alpha 0.05 AND power 0.80, while
the CI half-width uses 1.96, so anything passing clause 3 has already cleared
zero. It is kept as a guard against a future edit that weakens clause 3, and the
selftest asserts the inequality that makes it redundant rather than pretending it
fires. Found before any data existed.

THE DEFAULT ACTION IS KEEP

Declared in the pre-registration before any number existed: an UNMEASURABLE
result, or a CI spanning zero, means the rule STAYS. Changing an unattended live
exit rule on a difference the data cannot resolve is churn dressed as research.
Only a positive delta whose CI clears zero licenses touching exit_monitor.py.

WHAT A PASS CANNOT MEAN

The system's directional edge is -5.08%, CI [-11.69, +1.52]. No exit rule repairs
an absent edge. This test can only make a losing system lose less.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import math
import sys

import numpy as np

import backtest as bt
import option_backtest as ob
import power_check as pw
import risk_params as rp

OOS_12 = "GOOGL,AVGO,AMD,NFLX,CRM,ADBE,QCOM,MU,ORCL,NOW,PANW,LRCX"
MAX_UNMATCHED_PCT = 1.0     # clause 1
ALPHA = 0.05


def arm(tickers: list[str], years: int, use_thesis: bool,
        dte: int = 30, iv_mult: float = 1.15, spread_pct: float = 5.0) -> dict:
    """One arm. Returns trades keyed by (ticker, entry_date)."""
    cfg = dict(years=years, dte=dte, tp=rp.OPT_WIN_RATE_TP_PCT,
               sl=rp.OPT_WIN_RATE_SL_PCT, dte_exit=7, use_thesis=use_thesis,
               iv_mult=iv_mult, spread_pct=spread_pct,
               cooldown_bars=bt.DEFAULTS["cooldown_bars"])
    sig_cfg = dict(bt.DEFAULTS)
    sig_cfg.update(tickers=tickers, years=years)

    by_signal = {}
    for tk in tickers:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            trades = ob.run_ticker(tk, cfg, sig_cfg) or []
        for t in trades:
            if t.get("pnl_pct") is None:
                continue
            # Key on the SIGNAL's identity. This used to be
            #     t.get("entry_date", t.get("spot0"))
            # and option_backtest had no entry_date at all, so the fallback ran
            # every time and two trades sharing an entry spot collided — one was
            # silently dropped (1397 pairs against the decomposition's 1398).
            # A .get() default is a guess unless the key is known to exist.
            key = (tk, t["entry_date"])
            assert key not in by_signal, (
                f"two trades on the same signal {key} — the key is not unique "
                f"and pairing would silently drop one")
            by_signal[key] = t
    return {"use_thesis": use_thesis, "by_signal": by_signal,
            "n": len(by_signal)}


def pair(on: dict, off: dict) -> dict:
    """Paired deltas, OFF minus ON. Positive means removing the rule helped."""
    a, b = on["by_signal"], off["by_signal"]
    shared = set(a) & set(b)
    deltas, touched, untouched_nonzero = [], 0, 0
    for k in shared:
        d = b[k]["pnl_pct"] - a[k]["pnl_pct"]
        deltas.append(d)
        if a[k]["reason"] == "THESIS":
            touched += 1
        elif d != 0.0:
            untouched_nonzero += 1
    arr = np.array(deltas, dtype=float)
    n = len(arr)
    common = {"n": n, "shared": len(shared),
              "only_on": len(set(a) - shared), "only_off": len(set(b) - shared),
              "touched": touched, "untouched_nonzero": untouched_nonzero,
              "unmatched_pct": (100.0 * (len(set(a) | set(b)) - len(shared))
                                / max(len(set(a) | set(b)), 1))}
    if n < 2:
        return {**common, "mean": float("nan"), "sd": float("nan")}
    sd = float(arr.std(ddof=1))
    mean = float(arr.mean())
    se = sd / math.sqrt(n)
    return {**common, "mean": mean, "sd": sd, "se": se,
            "ci": (mean - 1.96 * se, mean + 1.96 * se),
            "mde": pw.mde(sd, n),
            "p": math.erfc(abs(mean) / se / math.sqrt(2.0)) if se > 0 else 1.0}


def verdict(pr: dict) -> tuple[str, list[str]]:
    """
    The pre-registered bar. Returns (action, reasons).

    Action is one of KEEP / CHANGE / VOID — never a bare pass or fail, because
    the decision this informs is 'what should exit_monitor.py do', not 'is there
    an effect'.
    """
    notes = []

    # clause 1 — pairing complete, or the design assumption is void
    if pr["unmatched_pct"] > MAX_UNMATCHED_PCT:
        return "VOID", [
            f"clause 1: {pr['unmatched_pct']:.1f}% of signals failed to match "
            f"across arms (ceiling {MAX_UNMATCHED_PCT}%) — entry dates moved "
            f"with the exit rule, so this is not a clean comparison"]

    # clause 2 — the effect must be confined to the trades the rule touched
    if pr["untouched_nonzero"]:
        return "VOID", [
            f"clause 2: {pr['untouched_nonzero']} trades that did NOT exit on "
            f"THESIS still moved — something other than the rule changed"]

    # clause 3 — measurable
    if pr.get("mde") is None or pr["mean"] != pr["mean"]:
        return "KEEP", ["no comparable pairs — nothing measured"]
    if abs(pr["mean"]) < pr["mde"]:
        notes.append(
            f"clause 3: |delta| {abs(pr['mean']):.2f}% is under the detectable "
            f"{pr['mde']:.2f}% — UNMEASURABLE, not 'no difference'")
        return "KEEP", notes

    # clause 4 — CI clears zero
    lo, hi = pr["ci"]
    if lo <= 0.0 <= hi:
        notes.append(
            f"clause 4: 95% CI [{lo:+.2f}, {hi:+.2f}]% spans zero")
        return "KEEP", notes

    # clause 5 — the declared decision
    if pr["mean"] < 0:
        notes.append(
            f"the rule HELPS: removing it costs {pr['mean']:+.2f}% per trade, "
            f"CI [{lo:+.2f}, {hi:+.2f}]%")
        return "KEEP", notes
    notes.append(
        f"removing the rule GAINS {pr['mean']:+.2f}% per trade, "
        f"CI [{lo:+.2f}, {hi:+.2f}]%")
    return "CHANGE", notes


def report(on: dict, off: dict, pr: dict) -> int:
    print("=" * 78)
    print("THESIS EXIT — does the EMA20 break rule earn its place?")
    print("=" * 78)
    print(f"  delta is OFF minus ON: positive means REMOVING the rule helped")
    print(f"  the default action is KEEP — see the pre-registration")
    print("=" * 78)

    def mean_pnl(d):
        v = [t["pnl_pct"] for t in d["by_signal"].values()]
        return float(np.mean(v)) if v else float("nan")

    print(f"  {'arm':<22}{'trades':>8}{'mean %':>10}")
    print("  " + "-" * 40)
    print(f"  {'thesis ON (live rule)':<22}{on['n']:>8}{mean_pnl(on):>+10.2f}")
    print(f"  {'thesis OFF':<22}{off['n']:>8}{mean_pnl(off):>+10.2f}")

    print(f"\n  PAIRED")
    print(f"    pairs                  {pr['shared']}")
    print(f"    unmatched              {pr['unmatched_pct']:.2f}%  "
          f"(only ON {pr['only_on']}, only OFF {pr['only_off']})")
    print(f"    touched by the rule    {pr['touched']}")
    print(f"    untouched but moved    {pr['untouched_nonzero']}  (must be 0)")
    if pr["mean"] == pr["mean"]:
        print(f"    mean delta             {pr['mean']:+.2f}%")
        print(f"    95% CI                 [{pr['ci'][0]:+.2f}, {pr['ci'][1]:+.2f}]%")
        print(f"    smallest detectable    {pr['mde']:.2f}%")
        print(f"    p                      {pr['p']:.4f}")

    action, notes = verdict(pr)
    print()
    print("=" * 78)
    print(f"  PRE-REGISTERED VERDICT: {action}")
    for nline in notes:
        print(f"    - {nline}")
    if action == "KEEP":
        print("\n  exit_monitor.py is unchanged. That is the declared default, not")
        print("  an absence of a result.")
    elif action == "CHANGE":
        print("\n  This licenses changing the THESIS rule in exit_monitor.py, and")
        print("  nothing more. It does not make the system profitable: the")
        print("  directional edge is -5.08%, CI [-11.69, +1.52].")
    else:
        print("\n  VOID means the comparison was not clean. Do not read the delta.")
    print("=" * 78)
    return 0 if action in ("KEEP", "CHANGE") else 2


def selftest() -> int:
    print("thesis_test.py selftest")
    print("=" * 72)

    def mk(reason, pnl):
        return {"reason": reason, "pnl_pct": pnl}

    # The field arm() keys on must actually exist upstream. It did not: the old
    # key used a .get() fallback that ran on every trade because the record had
    # no entry_date, and nothing noticed until a trade count came back one short.
    probe = ob._result("TIME", 3.0, 2.0, 4, 26, -33.3, spot0=100.0,
                       spot_exit=101.0, strike=100.0, iv=0.2, right="CALL",
                       dte0=30, entry_date="2024-01-05")
    assert probe.get("entry_date") == "2024-01-05", (
        "option_backtest._result must carry entry_date; arm() keys pairs on it "
        "and a missing field silently collapses trades that share an entry spot")
    print("signal identity  : _result carries entry_date, so pairs key on the signal")

    def arms(pairs):
        """pairs: {key: (on_reason, on_pnl, off_pnl)}"""
        on = {"by_signal": {k: mk(r, a) for k, (r, a, b) in pairs.items()},
              "n": len(pairs), "use_thesis": True}
        off = {"by_signal": {k: mk("STOP", b) for k, (r, a, b) in pairs.items()},
               "n": len(pairs), "use_thesis": False}
        return on, off

    # ── untouched trades must pair at EXACTLY zero ──
    on, off = arms({"a": ("STOP", -50.0, -50.0), "b": ("TARGET", 100.0, 100.0),
                    "c": ("THESIS", -34.0, -50.0)})
    p = pair(on, off)
    assert p["n"] == 3 and p["touched"] == 1, p
    assert p["untouched_nonzero"] == 0, p
    assert abs(p["mean"] - (-16.0 / 3)) < 1e-9, p["mean"]
    print(f"pairing          : 3 pairs, 1 touched, mean {p['mean']:+.2f}%")

    # ── clause 2: an untouched trade that MOVED voids the run ──
    bad_on, bad_off = arms({"a": ("STOP", -50.0, -40.0), "c": ("THESIS", -34.0, -50.0)})
    pb = pair(bad_on, bad_off)
    assert pb["untouched_nonzero"] == 1, pb
    act, why = verdict(pb)
    assert act == "VOID" and any("clause 2" in w for w in why), (act, why)
    print("clause 2         : an untouched trade that moves reads VOID")

    # ── clause 1: unmatched signals void the run ──
    lop_on = {"by_signal": {f"k{i}": mk("STOP", -50.0) for i in range(100)},
              "n": 100, "use_thesis": True}
    lop_off = {"by_signal": {f"k{i}": mk("STOP", -50.0) for i in range(90)},
               "n": 90, "use_thesis": False}
    pl = pair(lop_on, lop_off)
    assert pl["unmatched_pct"] > MAX_UNMATCHED_PCT, pl["unmatched_pct"]
    assert verdict(pl)[0] == "VOID", verdict(pl)
    print(f"clause 1         : {pl['unmatched_pct']:.0f}% unmatched reads VOID")

    # ── clause 3: an undetectable delta must KEEP, and say UNMEASURABLE ──
    rng = np.random.default_rng(11)
    tiny = {f"k{i}": ("THESIS", 0.0, float(rng.normal(0.2, 60.0)))
            for i in range(1398)}
    on_t, off_t = arms(tiny)
    pt = pair(on_t, off_t)
    act, why = verdict(pt)
    assert act == "KEEP", (act, why)
    assert any("UNMEASURABLE" in w for w in why), why
    print(f"clause 3         : delta {pt['mean']:+.2f}% under detectable "
          f"{pt['mde']:.2f}% -> KEEP, UNMEASURABLE")

    # ── clause 5: a big POSITIVE delta licenses CHANGE ──
    big = {f"k{i}": ("THESIS", -34.0, float(rng.normal(-14.0, 40.0)))
           for i in range(1398)}
    on_b, off_b = arms(big)
    pbig = pair(on_b, off_b)
    act, why = verdict(pbig)
    assert act == "CHANGE" and pbig["mean"] > 0, (act, pbig["mean"])
    print(f"clause 5 CHANGE  : delta {pbig['mean']:+.2f}% -> {act}")

    # ── ...and a big NEGATIVE delta must KEEP, never CHANGE ──
    worse = {f"k{i}": ("THESIS", -34.0, float(rng.normal(-54.0, 40.0)))
             for i in range(1398)}
    on_w, off_w = arms(worse)
    pw_ = pair(on_w, off_w)
    act, why = verdict(pw_)
    assert act == "KEEP" and pw_["mean"] < 0, (act, pw_["mean"])
    assert any("HELPS" in w for w in why), why
    print(f"clause 5 KEEP    : delta {pw_['mean']:+.2f}% -> {act}, the rule helps")

    # ── CLAUSE 4 CANNOT FIRE, and that is worth asserting rather than hiding ──
    # Trying to build the case exposed it: mde uses z = 2.802 (alpha 0.05 AND
    # power 0.80) while the CI half-width uses 1.96. Since 2.802 > 1.96, any
    # delta that clears clause 3 has ALREADY cleared zero, so clause 4 is
    # implied by clause 3 and is unreachable as written.
    #
    # It is kept as a guard against a future edit that loosens clause 3, not
    # removed - but the suite must not pretend it fires. Assert the relationship
    # that makes it redundant, then prove the branch still works if it ever
    # becomes reachable.
    n = 400
    vals = rng.normal(0.0, 30.0, n)
    vals = vals - vals.mean() + 3.2
    edge = {f"k{i}": ("THESIS", 0.0, float(v)) for i, v in enumerate(vals)}
    pe = pair(*arms(edge))
    half = 1.96 * pe["se"]
    assert pe["mde"] > half, (
        f"mde {pe['mde']:.3f} must exceed the CI half-width {half:.3f} — if it "
        f"does not, clause 3 no longer implies clause 4 and the redundancy "
        f"documented here has silently stopped holding")
    assert abs(pe["mean"]) < pe["mde"], "this fixture is stopped by clause 3 first"
    assert verdict(pe)[0] == "KEEP", verdict(pe)
    print(f"clause 3 implies 4: mde {pe['mde']:.2f}% > CI half-width {half:.2f}%, "
          f"so clause 4 is unreachable")

    # ...and the clause 4 BRANCH still works when handed a state that reaches it
    reachable = {"n": 400, "shared": 400, "only_on": 0, "only_off": 0,
                 "touched": 400, "untouched_nonzero": 0, "unmatched_pct": 0.0,
                 "mean": 3.0, "sd": 30.0, "se": 1.9,
                 "ci": (-0.7, 6.7), "mde": 2.0, "p": 0.11}
    act, why = verdict(reachable)
    assert act == "KEEP" and any("clause 4" in w for w in why), (act, why)
    print("clause 4 branch  : still KEEPs when a CI spanning zero reaches it")

    # ── WIRING: arm() must build cfg the way run_ticker actually needs ──
    # The option_decompose lesson, one week old: nine guards passed while main()
    # could not run, because none of them called the entry point.
    import pandas as pd
    idx = pd.bdate_range("2021-01-04", periods=400)
    r2 = np.random.default_rng(5)
    px = 100.0 * np.exp(np.cumsum(r2.normal(0.0007, 0.016, len(idx))))
    fake = pd.DataFrame({"Date": idx, "Open": px, "High": px * 1.012,
                         "Low": px * 0.988, "Close": px,
                         "Volume": np.full(len(idx), 3e6)})
    # Wrap run_ticker to record the ARM each call belongs to. Counting downloads
    # is not enough and the falsification proved it: running both arms with
    # use_thesis=True still makes four downloads, so a comparison against itself
    # passed a guard that only counted. Record the flag, not the traffic.
    real_dl, real_argv, real_rt = bt.download, sys.argv, ob.run_ticker
    seen, flags = [], []
    def _spy(tk, cfg, sig_cfg, coverage=None):
        flags.append(cfg["use_thesis"])
        return real_rt(tk, cfg, sig_cfg, coverage)
    try:
        ob.run_ticker = _spy
        bt.download = lambda tk, years, *a, **k: (seen.append((tk, years)), fake.copy())[1]
        sys.argv = ["thesis_test.py", "--tickers", "AAA,BBB", "--years", "3"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = main()
    finally:
        bt.download, sys.argv, ob.run_ticker = real_dl, real_argv, real_rt
    assert seen, "main() never reached bt.download"
    assert set(flags) == {True, False}, (
        f"the two arms must differ in use_thesis; saw {set(flags)} — both arms "
        f"on the same setting is a comparison against itself, and it produces "
        f"the identical download count, so counting cannot catch it")
    assert flags.count(True) == flags.count(False) == 2, flags
    assert {t for t, _ in seen} == {"AAA", "BBB"}, seen
    assert all(y == 3 for _, y in seen), seen
    assert len(seen) == 4, (
        f"two arms x two tickers = four downloads, got {len(seen)} — if this is "
        f"2, only one arm ran and the comparison is against itself")
    body = out.getvalue()
    assert "THESIS EXIT" in body and "PRE-REGISTERED VERDICT" in body, body[:300]
    assert rc in (0, 2), rc
    print(f"main() wiring    : {len(seen)} downloads (2 arms x 2 tickers), exit {rc}")

    print("=" * 72)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default=OOS_12)
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--dte", type=int, default=30)
    ap.add_argument("--iv-mult", type=float, default=1.15)
    ap.add_argument("--spread-pct", type=float, default=5.0)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    print("  arm A: thesis ON  ...", file=sys.stderr)
    on = arm(tickers, a.years, True, a.dte, a.iv_mult, a.spread_pct)
    print(f"    {on['n']} trades", file=sys.stderr)
    print("  arm B: thesis OFF ...", file=sys.stderr)
    off = arm(tickers, a.years, False, a.dte, a.iv_mult, a.spread_pct)
    print(f"    {off['n']} trades", file=sys.stderr)
    if not on["n"] or not off["n"]:
        print("NOTHING MEASURED — an arm produced no trades.")
        return 2
    return report(on, off, pair(on, off))


if __name__ == "__main__":
    sys.exit(main())
