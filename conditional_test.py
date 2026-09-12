"""
Is there anything beyond `side`, or did the sweep find one effect six times?

Pre-registered in results/conditional_test_preregistration.md, committed BEFORE
this file existed. The bar is fixed there and implemented in verdict() below.

WHAT RUN 1 COULD NOT ANSWER

feature_sweep found six features clearing Holm. Five are proxies for "this setup
is bullish" - side, rsi, ema20_dist, macd_hist, ema_spread - and side itself
reads CALL -3.1% against PUT -20.4%. Holm corrects for MULTIPLICITY, not for
CORRELATION, and that design tested every feature marginally and none
conditionally. It could not tell six findings from one finding seen six ways.

THIS IS NOT A SWEEP

rsi (the best of the bullish cluster) and atr_pct (the one survivor on a
different axis) are named in advance. Holm is applied across exactly those two.
No substitution is permitted after seeing the result, and nothing else is tested.

That restriction is a POWER decision made before any data. Within the CALL
stratum n = 897: re-sweeping nine features in quintiles detects 28.6% after Holm,
while run 1's largest effect was ~23%. A test that cannot see what the full
sample barely saw is theatre. Two pre-specified claims on terciles detect 16.3%.

CLAUSE 4 FIXES RUN 1'S HOLE

Run 1 required the filtered subset's expectancy to be POSITIVE and not to be
distinguishable from zero. It passed a subset at +2.66% whose 95% interval was
[-6.07, +11.39]. Here the interval must clear zero. At n = 299 that costs +8.48%,
about 3x anything observed so far - which is not a bar set to fail but what
"positive" actually requires at this sample size.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import math
import sys

import numpy as np

import adx_retest as ar
import backtest as bt
import feature_sweep as fs
import option_backtest as ob
import risk_params as rp

OOS_12 = fs.OOS_12
TESTED = ("rsi", "atr_pct")      # named in advance; no substitutions
MIN_STRATUM = 500                # clause 1
MIN_RHO = 0.6                    # clause 3 — with 3 terciles this means +/-1.0
ALPHA = 0.05
N_TERCILES = 3


def terciles(rows: list[tuple[float, float]]) -> list[dict]:
    """Equal-count terciles on (feature value, pnl). NaN already excluded."""
    rows = sorted(rows, key=lambda r: r[0])
    k = max(1, len(rows) // N_TERCILES)
    out = []
    for i in range(N_TERCILES):
        lo = i * k
        hi = len(rows) if i == N_TERCILES - 1 else (i + 1) * k
        chunk = rows[lo:hi]
        if not chunk:
            continue
        a = np.array([p for _, p in chunk], dtype=float)
        out.append({"i": i, "n": len(a),
                    "label": f"{chunk[0][0]:.2f}..{chunk[-1][0]:.2f}",
                    "expectancy": float(a.mean()),
                    "sd": float(a.std(ddof=1)) if len(a) > 1 else float("nan"),
                    "win_rate": 100.0 * float((a > 0).mean())})
    return out


def top_vs_rest(bk: list[dict], rho: float = 1.0) -> dict:
    """
    The favoured END tercile against the OTHER TWO COMBINED.

    WHICH end is chosen by the sign of the dose-response, not by which group
    looks best. rho > 0 means high values are favoured, so the top tercile is
    tested; rho < 0 means low values are, so the bottom is. That is determined by
    the gradient across all three groups, never by picking the better number.

    The first version always tested bk[-1]. atr_pct is a LOW-is-good feature
    (rho -1.00), so run 1 of this module compared its WORST group against the
    rest and asked whether it won. Recomputing the correct tail by hand gave
    t 1.01, p 0.3126 — the same verdict, but arrived at by luck rather than by
    design. Found after the run and fixed here.

    Against the OTHER TWO COMBINED, not against the opposite end: the widest
    contrast flatters any feature, and the filter a trader would actually run
    keeps one group and discards everything else.
    """
    if len(bk) < 2:
        return {"t": float("nan"), "p": 1.0, "n_top": 0, "n_rest": 0}
    high_is_good = not (rho == rho) or rho >= 0
    top = bk[-1] if high_is_good else bk[0]
    others = bk[:-1] if high_is_good else bk[1:]
    n_rest = sum(b["n"] for b in others)
    mean_rest = sum(b["expectancy"] * b["n"] for b in others) / n_rest
    var_rest = sum((b["sd"] ** 2) * (b["n"] - 1) for b in others) / (n_rest - len(others))
    se = math.sqrt(top["sd"] ** 2 / top["n"] + var_rest / n_rest)
    if not (se > 0):
        return {"t": float("nan"), "p": 1.0, "n_top": top["n"], "n_rest": n_rest}
    t = (top["expectancy"] - mean_rest) / se
    return {"t": t, "p": math.erfc(abs(t) / math.sqrt(2.0)),
            "n_top": top["n"], "n_rest": n_rest,
            "top_exp": top["expectancy"], "rest_exp": mean_rest,
            "top_sd": top["sd"]}


def analyse(trades: list[dict], side_value: float) -> dict:
    stratum = [t for t in trades
               if t.get("features", {}).get("side") == side_value]
    rows = []
    for f in TESTED:
        vals = [(t["features"].get(f, float("nan")), t["pnl_pct"])
                for t in stratum if t.get("features")]
        usable = [(v, p) for v, p in vals if v == v]
        bk = terciles(usable) if len(usable) >= N_TERCILES else []
        # rho FIRST: it decides which end the comparison and the CI read.
        rho = (ar.spearman([b["i"] for b in bk], [b["expectancy"] for b in bk])
               if len(bk) >= 2 else float("nan"))
        cmp_ = top_vs_rest(bk, rho)
        # CI on the FAVOURED group alone — clause 4's quantity, same end.
        ci = None
        if bk:
            end = bk[-1] if (not (rho == rho) or rho >= 0) else bk[0]
            if end["n"] > 1 and end["sd"] == end["sd"]:
                se = end["sd"] / math.sqrt(end["n"])
                ci = (end["expectancy"] - 1.96 * se, end["expectancy"] + 1.96 * se)
        rows.append({"feature": f, "buckets": bk, "rho": rho, "ci": ci, **cmp_})
    for r, s in zip(rows, ar.holm([r["p"] for r in rows], alpha=ALPHA)):
        r["holm"] = bool(s)
    return {"n": len(stratum), "rows": rows}


def verdict(call: dict) -> tuple[bool, list[str]]:
    """The pre-registered bar, clause for clause."""
    notes = []
    if call["n"] < MIN_STRATUM:
        return False, [f"clause 1: CALL stratum holds {call['n']} trades, under "
                       f"the {MIN_STRATUM} floor — void, not weak"]
    winners = [r for r in call["rows"] if r.get("holm")]
    if not winners:
        best = min(call["rows"], key=lambda r: r["p"])
        notes.append(
            f"clause 2: neither {' nor '.join(TESTED)} separates within CALLs — "
            f"best is {best['feature']} at p {best['p']:.4f}, needing "
            f"{ALPHA/len(TESTED):.4f}")
        return False, notes
    for r in winners:
        if not (abs(r["rho"]) >= MIN_RHO):
            notes.append(
                f"clause 3: {r['feature']} clears Holm but rho {r['rho']:+.2f} "
                f"is not strictly monotone across terciles")
            continue
        if r["ci"] is None or r["ci"][0] <= 0:
            lo, hi = r["ci"] if r["ci"] else (float("nan"), float("nan"))
            notes.append(
                f"clause 4: {r['feature']} top tercile {r['top_exp']:+.2f}% with "
                f"CI [{lo:+.2f}, {hi:+.2f}]% — does not clear zero as an "
                f"INTERVAL, which is the hole run 1 passed through")
            continue
        return True, [f"{r['feature']} separates within CALLs AND its top "
                      f"tercile clears zero: {r['top_exp']:+.2f}%, "
                      f"CI [{r['ci'][0]:+.2f}, {r['ci'][1]:+.2f}]%"]
    return False, notes


def report(call: dict, put: dict) -> int:
    print("=" * 86)
    print("CONDITIONAL TEST — is there anything beyond `side`?")
    print("=" * 86)
    print(f"  CALL stratum {call['n']}   PUT stratum {put['n']} (descriptive only)")
    print(f"  testing exactly {list(TESTED)}, named before the run, Holm across "
          f"{len(TESTED)}")
    print("=" * 86)
    for label, strat, judged in (("CALLs", call, True), ("PUTs", put, False)):
        print(f"\n  {label}" + ("" if judged else "   — NOT tested, not in the Holm family"))
        hdr = (f"    {'feature':<10}{'top n':>7}{'rest n':>8}{'top exp':>10}"
               f"{'rest exp':>10}{'t':>7}{'p':>9}{'Holm':>6}{'rho':>7}"
               f"{'top 95% CI':>20}")
        print(hdr); print("    " + "-" * (len(hdr) - 4))
        for r in strat["rows"]:
            ci = (f"[{r['ci'][0]:+.1f}, {r['ci'][1]:+.1f}]" if r["ci"] else "—")
            print(f"    {r['feature']:<10}{r.get('n_top',0):>7}{r.get('n_rest',0):>8}"
                  f"{r.get('top_exp',float('nan')):>+10.2f}"
                  f"{r.get('rest_exp',float('nan')):>+10.2f}{r['t']:>7.2f}"
                  f"{r['p']:>9.4f}{('YES' if r.get('holm') else 'no'):>6}"
                  f"{r['rho']:>7.2f}{ci:>20}")
        for r in strat["rows"]:
            if not r["buckets"]:
                continue
            print(f"\n    {r['feature']} terciles")
            for b in r["buckets"]:
                print(f"      {b['label']:<18}{b['n']:>6}{b['expectancy']:>+9.1f}"
                      f"{b['win_rate']:>8.1f}%")

    ok, why = verdict(call)
    print()
    print("=" * 86)
    print("PRE-REGISTERED VERDICT")
    print("=" * 86)
    if ok:
        for w in why:
            print(f"  PASS — {w}")
        print("\n  A SECOND AXIS EXISTS. This licenses forward paper-tracking and")
        print("  nothing else: both tranches are spent, so it is out-of-sample")
        print("  against nothing and can never be confirmed.")
    else:
        print("  NOTHING BEYOND `side`")
        for w in why:
            print(f"    - {w}")
        print("\n  Run 1 found `side` six times. That is the long/short asymmetry")
        print("  already in longshort_split.md, already diagnosed there as an")
        print("  estimate inflated by the noise that made it visible.")
        print("  STOP HERE. Conditioning on something else costs the sample that")
        print("  would make the next test possible.")
    print("=" * 86)
    return 0 if ok else 1


def selftest() -> int:
    print("conditional_test.py selftest")
    print("=" * 72)
    rng = np.random.default_rng(21)

    def mk(side, rsi, atr, pnl):
        return {"features": {"side": side, "rsi": rsi, "atr_pct": atr},
                "pnl_pct": pnl, "reason": "TIME"}

    # ── the stratum must actually be a stratum ──
    mixed = ([mk(1.0, float(i % 100), 1.0, 0.0) for i in range(600)] +
             [mk(0.0, float(i % 100), 1.0, 0.0) for i in range(400)])
    c = analyse(mixed, 1.0)
    assert c["n"] == 600, f"CALL stratum must hold only calls, got {c['n']}"
    assert analyse(mixed, 0.0)["n"] == 400
    print(f"stratification   : 600 CALLs / 400 PUTs, split cleanly")

    # ── clause 1: a thin stratum is VOID, not weak ──
    tiny = [mk(1.0, float(i), 1.0, float(rng.normal(0, 70))) for i in range(300)]
    ok, why = verdict(analyse(tiny, 1.0))
    assert not ok and any("clause 1" in w for w in why), why
    print(f"clause 1         : a {MIN_STRATUM}-trade floor voids a 300 stratum")

    # ── top vs REST, not top vs bottom ──
    bk = [{"i": 0, "n": 100, "expectancy": -30.0, "sd": 70.0},
          {"i": 1, "n": 100, "expectancy": -30.0, "sd": 70.0},
          {"i": 2, "n": 100, "expectancy": 0.0, "sd": 70.0}]
    cmp_ = top_vs_rest(bk)
    assert cmp_["n_rest"] == 200, cmp_
    assert abs(cmp_["rest_exp"] - (-30.0)) < 1e-9, cmp_["rest_exp"]
    print(f"top vs rest      : top {bk[-1]['expectancy']:+.0f} against "
          f"{cmp_['n_rest']} others at {cmp_['rest_exp']:+.0f}, not just the bottom")

    # ── the tested END follows the GRADIENT, not the position in the list ──
    # atr_pct is low-is-good. Always testing bk[-1] compared its WORST group
    # against the rest and asked whether that group won. Found after run 1.
    low_good = [{"i": 0, "n": 100, "expectancy": 0.0, "sd": 70.0},
                {"i": 1, "n": 100, "expectancy": -20.0, "sd": 70.0},
                {"i": 2, "n": 100, "expectancy": -30.0, "sd": 70.0}]
    rho_lo = ar.spearman([b["i"] for b in low_good],
                         [b["expectancy"] for b in low_good])
    assert rho_lo == -1.0, rho_lo
    c_lo = top_vs_rest(low_good, rho_lo)
    assert abs(c_lo["top_exp"] - 0.0) < 1e-9, (
        f"with rho {rho_lo:+.1f} the favoured end is the BOTTOM tercile (0.0), "
        f"got {c_lo['top_exp']:+.1f} — testing the wrong tail asks whether the "
        f"worst group beats the rest")
    assert abs(c_lo["rest_exp"] - (-25.0)) < 1e-9, c_lo["rest_exp"]
    assert c_lo["t"] > 0, "and the favoured end should read as an improvement"
    c_hi = top_vs_rest(bk, 1.0)
    assert abs(c_hi["top_exp"] - 0.0) < 1e-9, c_hi["top_exp"]
    print(f"favoured end     : rho {rho_lo:+.0f} tests the BOTTOM tercile, "
          f"rho +1 tests the top")

    # ── clause 2: pure noise inside the stratum separates nothing ──
    noise = [mk(1.0, float(rng.normal()), float(rng.normal()),
                float(rng.normal(0, 70))) for _ in range(900)]
    ok, why = verdict(analyse(noise, 1.0))
    assert not ok and any("clause 2" in w for w in why), why
    print("clause 2         : noise inside the stratum -> nothing separates")

    # ── clause 4: a REAL, monotone, significant separation that still fails ──
    # Exactly run 1's situation: the top group is positive but its interval is not.
    strong = []
    for q in range(3):
        target = (-30.0, -10.0, 3.0)[q]
        vals = rng.normal(0.0, 70.0, 300)
        vals = vals - vals.mean() + target
        for v in vals:
            strong.append(mk(1.0, float(q), 0.0, float(v)))
    c = analyse(strong, 1.0)
    r = next(x for x in c["rows"] if x["feature"] == "rsi")
    assert r["holm"] and abs(r["rho"]) >= MIN_RHO, (r["p"], r["rho"])
    assert r["top_exp"] > 0, r["top_exp"]
    ok, why = verdict(c)
    assert not ok and any("clause 4" in w for w in why), (ok, why)
    print(f"clause 4         : top tercile {r['top_exp']:+.2f}% is POSITIVE, CI "
          f"[{r['ci'][0]:+.1f}, {r['ci'][1]:+.1f}] spans zero -> FAIL")

    # ── ...and a top group whose INTERVAL clears zero must pass ──
    big = []
    for q in range(3):
        target = (-30.0, -5.0, 15.0)[q]
        vals = rng.normal(0.0, 70.0, 300)
        vals = vals - vals.mean() + target
        for v in vals:
            big.append(mk(1.0, float(q), 0.0, float(v)))
    ok_b, why_b = verdict(analyse(big, 1.0))
    assert ok_b, f"a top tercile whose CI clears zero must pass: {why_b}"
    print("bar liveness     : a top tercile at +15% with CI clear of zero PASSES")

    # ── clause 4's CI must read the FAVOURED end too, not always the top ──
    # Fixing top_vs_rest left the CI still reading bk[-1], and no test caught it:
    # every liveness fixture was high-is-good, so both ends agreed. This one is
    # low-is-good, and reverting the CI to always-top flips the verdict.
    lowgood = []
    for q, target in enumerate((15.0, -10.0, -30.0)):
        vals = rng.normal(0.0, 70.0, 300)
        vals = vals - vals.mean() + target
        for v in vals:
            lowgood.append(mk(1.0, 0.0, float(q), float(v)))
    cl = analyse(lowgood, 1.0)
    rl = next(x for x in cl["rows"] if x["feature"] == "atr_pct")
    assert rl["rho"] == -1.0, rl["rho"]
    assert rl["holm"], f"fixture must clear Holm to reach clause 4, p={rl['p']:.2e}"
    assert abs(rl["top_exp"] - 15.0) < 1.0, (
        f"the favoured end is the BOTTOM tercile at +15, got {rl['top_exp']:+.1f}")
    assert rl["ci"] is not None and rl["ci"][0] > 0, (
        f"and the CI must be read on that SAME end; got {rl['ci']} — reading "
        f"bk[-1] here returns the -30 group and fails clause 4 on a feature "
        f"that should pass")
    ok_l, why_l = verdict(cl)
    assert ok_l, f"a low-is-good feature whose favoured end clears zero must pass: {why_l}"
    print(f"clause 4 end     : low-is-good passes on its BOTTOM tercile "
          f"{rl['top_exp']:+.1f}%, CI [{rl['ci'][0]:+.1f}, {rl['ci'][1]:+.1f}]")

    # ── clause 3: with 3 terciles, |rho| >= 0.6 means EXACTLY +/-1.0 ──
    assert ar.spearman([0, 1, 2], [-30.0, -10.0, 3.0]) == 1.0
    assert abs(ar.spearman([0, 1, 2], [-30.0, 3.0, -10.0])) == 0.5, \
        ar.spearman([0, 1, 2], [-30.0, 3.0, -10.0])
    nonmono = []
    for q in range(3):
        # Must CLEAR Holm and FAIL monotonicity. A first version used
        # (-30, +20, -10): the top tercile was -10 against a rest mean of -5, so
        # it never reached clause 3 and the test passed without exercising it.
        # These give top +10 against a rest mean of -35 (highly significant) with
        # rho exactly +0.5, which is the only non-unit value three points allow.
        target = (-30.0, -40.0, 10.0)[q]
        vals = rng.normal(0.0, 70.0, 300)
        vals = vals - vals.mean() + target
        for v in vals:
            nonmono.append(mk(1.0, float(q), 0.0, float(v)))
    cn = analyse(nonmono, 1.0)
    rn = next(x for x in cn["rows"] if x["feature"] == "rsi")
    assert rn["holm"], (
        f"the clause 3 fixture must CLEAR Holm to reach clause 3 at all, "
        f"p={rn['p']:.4f} — otherwise it is stopped by clause 2 and clause 3 is "
        f"never exercised")
    assert abs(rn["rho"]) == 0.5, rn["rho"]
    ok_n, why_n = verdict(cn)
    assert not ok_n and any("clause 3" in w for w in why_n), why_n
    print(f"clause 3         : p {rn['p']:.1e} clears Holm, rho {rn['rho']:+.2f} "
          f"is not +/-1.0 -> FAIL")

    # ── ONLY the two named features are ever tested ──
    assert TESTED == ("rsi", "atr_pct"), TESTED
    c2 = analyse(noise, 1.0)
    assert [r["feature"] for r in c2["rows"]] == list(TESTED), c2["rows"]
    assert len(c2["rows"]) == 2, (
        f"{len(c2['rows'])} features tested — this is not a sweep, and Holm's "
        f"denominator is fixed at {len(TESTED)} by the pre-registration")
    # ── HOLM MUST BE APPLIED, not merely imported ──
    # With two features Holm tests at alpha/2 = 0.025 against a bare 0.05. None
    # of the fixtures above land in that window, so swapping ar.holm() for a
    # plain `p < ALPHA` left the suite green — the identical gap the feature
    # sweep had. This case is recentered to sit inside it: with Holm it fails at
    # clause 2, without Holm it passes every clause.
    band = []
    for q, target in enumerate((-0.5, 0.5, 10.5)):
        vals = rng.normal(0.0, 70.0, 300)
        vals = vals - vals.mean() + target
        for v in vals:
            band.append(mk(1.0, float(q), 0.0, float(v)))
    cb = analyse(band, 1.0)
    rb = next(x for x in cb["rows"] if x["feature"] == "rsi")
    assert ALPHA / len(TESTED) < rb["p"] < ALPHA, (
        f"fixture must sit BETWEEN the Holm threshold {ALPHA/len(TESTED)} and a "
        f"bare alpha {ALPHA} to test the correction; p = {rb['p']:.4f}")
    assert not rb["holm"], (
        f"p {rb['p']:.4f} beats a bare alpha and must still FAIL Holm across "
        f"{len(TESTED)} — if it survives, the correction is not being applied")
    assert rb["ci"] is not None and rb["ci"][0] > 0, (
        "and this fixture must otherwise PASS every later clause, or it does "
        "not prove Holm is what stopped it")
    ok_b2, why_b2 = verdict(cb)
    assert not ok_b2 and any("clause 2" in w for w in why_b2), (ok_b2, why_b2)
    print(f"holm applied     : p {rb['p']:.4f} beats alpha {ALPHA}, fails Holm at "
          f"{ALPHA/len(TESTED)}, and would otherwise have PASSED")

    print(f"no substitution  : exactly {TESTED} tested, Holm denominator {len(TESTED)}")

    # ── WIRING: the features named here must exist upstream ──
    for f in TESTED + ("side",):
        assert f in fs.FEATURES, f"{f} is not produced by the sweep's feature set"
    import pandas as pd
    n = 300
    r2 = np.random.default_rng(9)
    px = 100 * np.exp(np.cumsum(r2.normal(0.0006, 0.015, n)))
    df = bt.compute(pd.DataFrame({
        "Date": pd.bdate_range("2023-01-02", periods=n), "Open": px,
        "High": px * 1.01, "Low": px * 0.99, "Close": px,
        "Volume": np.full(n, 3e6)}))
    feats = ob.entry_features(df, len(df) - 10, rv=0.25, prem0=3.0,
                              spot0=100.0, right="CALL")
    for f in TESTED + ("side",):
        assert f in feats and feats[f] == feats[f], f"{f} missing or NaN upstream"
    assert feats["side"] == 1.0, "CALL must encode as side=1.0"
    print(f"wiring           : {TESTED} + side present upstream and finite")

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
    trades = [t for t in fs.collect(tickers, a.years, a.dte, a.iv_mult,
                                    a.spread_pct)
              if t.get("pnl_pct") is not None]
    if not trades:
        print("NOTHING MEASURED — no trades.")
        return 2
    return report(analyse(trades, 1.0), analyse(trades, 0.0))


if __name__ == "__main__":
    sys.exit(main())
