"""
Does anything observable AT ENTRY separate the trades that reach TP from the rest?

Pre-registered in results/feature_sweep_preregistration.md, committed BEFORE this
file existed. The bar is fixed there and implemented in verdict() below.

THE ARITHMETIC THE FILTER MUST BEAT, fixed before any feature was chosen

OPT_WIN_RATE is 24.9% against a 33.3% breakeven at TP +100% / SL -50%: an
8.4-point shortfall, -12.6% of premium per trade. Closing it requires

    keep 80%  ->  the dropped bucket must win -15.5%   impossible
    keep 60%  ->                                9.8%
    keep 50%  ->                               14.8%

so a usable filter DISCARDS AT LEAST 40% of signals and the discarded bucket
wins at well under half the base rate - about 15 win-rate points of separation.
A feature that shifts win rate three or four points cannot work here whatever its
p-value, which is why clause 4 is a floor on the RESULT and not on significance.

POWER, AND THE ONE GOOD PROPERTY OF THIS DESIGN

Five equal-count buckets of ~279 at an sd near 70% detect 16.6% uncorrected and
21.6% after Holm across ten features - 14.4 win-rate points against a requirement
of 15. The margin is 1.04x.

That is thin, and it is also the point: THIS SWEEP CAN ONLY DETECT EFFECTS BIG
ENOUGH TO BE USEFUL. Anything it misses would not have closed the gap. A null is
decisive about the only effects worth having, while saying nothing about small
ones - and it should be read that way rather than as "no relationship exists".

DOSE-RESPONSE CAN ONLY SINK A FEATURE

Holm is applied to the PRIMARY test per feature. The Spearman clause is
falsifiability, never a second shot on goal: a feature that fails it is refuted,
and a feature that passes it gains nothing it had not already earned. Running
both and accepting either would be two tests dressed as one.

NOTHING FOUND HERE CAN EVER BE CONFIRMED

Both tranches are spent. A surviving feature is in-sample against everything and
out-of-sample against nothing, permanently. A PASS licenses forward paper
tracking and nothing else. That is stated in the pre-registration because the
temptation afterwards is to treat a survivor as a discovery.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import math
import sys

import numpy as np

import adx_retest as ar          # holm(), spearman() — one implementation
import backtest as bt
import option_backtest as ob
import risk_params as rp

OOS_12 = "GOOGL,AVGO,AMD,NFLX,CRM,ADBE,QCOM,MU,ORCL,NOW,PANW,LRCX"
FEATURES = ("side", "adx", "rsi", "atr_pct", "rvol",
            "ema20_dist", "ema_spread", "macd_hist", "rv", "prem_pct")
N_BUCKETS = 5
MIN_BUCKET = 150          # clause 1
MIN_RHO = 0.6             # clause 3
MIN_DISCARD_PCT = 20.0    # clause 5
ALPHA = 0.05


def collect(tickers: list[str], years: int, dte: int = 30,
            iv_mult: float = 1.15, spread_pct: float = 5.0) -> list[dict]:
    cfg = dict(years=years, dte=dte, tp=rp.OPT_WIN_RATE_TP_PCT,
               sl=rp.OPT_WIN_RATE_SL_PCT, dte_exit=7, use_thesis=True,
               iv_mult=iv_mult, spread_pct=spread_pct,
               cooldown_bars=bt.DEFAULTS["cooldown_bars"])
    sig_cfg = dict(bt.DEFAULTS)
    sig_cfg.update(tickers=tickers, years=years)
    out = []
    for tk in tickers:
        print(f"  {tk} ...", file=sys.stderr)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out.extend(ob.run_ticker(tk, cfg, sig_cfg) or [])
    return out


def bucket(trades: list[dict], feat: str, n_buckets: int = N_BUCKETS) -> list[dict]:
    """
    Equal-count buckets on one feature. Binary features get two groups.

    Trades whose feature is NaN are DROPPED and counted, never pooled into a
    bucket — a missing observation is not a low one.
    """
    rows = [(t["features"].get(feat, float("nan")), t["pnl_pct"])
            for t in trades if t.get("features")]
    usable = [(v, p) for v, p in rows if v == v]
    dropped = len(rows) - len(usable)
    if not usable:
        return []
    vals = sorted({v for v, _ in usable})
    if len(vals) <= 2:                       # binary, e.g. side
        groups = [[p for v, p in usable if v == x] for x in vals]
        labels = [f"{x:g}" for x in vals]
    else:
        usable.sort(key=lambda r: r[0])
        k = max(1, len(usable) // n_buckets)
        groups, labels = [], []
        for i in range(n_buckets):
            lo = i * k
            hi = len(usable) if i == n_buckets - 1 else (i + 1) * k
            chunk = usable[lo:hi]
            if not chunk:
                continue
            groups.append([p for _, p in chunk])
            labels.append(f"{chunk[0][0]:.2f}..{chunk[-1][0]:.2f}")
    out = []
    for i, (g, lab) in enumerate(zip(groups, labels)):
        a = np.array(g, dtype=float)
        out.append({"i": i, "label": lab, "n": len(a),
                    "expectancy": float(a.mean()),
                    "sd": float(a.std(ddof=1)) if len(a) > 1 else float("nan"),
                    "win_rate": 100.0 * float((a > 0).mean()),
                    "dropped_nan": dropped})
    return out


def welch(a: dict, b: dict) -> tuple[float, float]:
    """Welch t and two-sided p for two buckets. One SE, so t and p agree."""
    va, vb = a["sd"] ** 2 / a["n"], b["sd"] ** 2 / b["n"]
    se = math.sqrt(va + vb)
    if not (se > 0):
        return float("nan"), 1.0
    t = (b["expectancy"] - a["expectancy"]) / se
    return t, math.erfc(abs(t) / math.sqrt(2.0))


def analyse(trades: list[dict]) -> list[dict]:
    rows = []
    for f in FEATURES:
        bk = bucket(trades, f)
        if len(bk) < 2:
            rows.append({"feature": f, "buckets": bk, "p": 1.0,
                         "rho": float("nan"), "t": float("nan"),
                         "thin": True})
            continue
        t, p = welch(bk[0], bk[-1])
        rho = ar.spearman([b["i"] for b in bk], [b["expectancy"] for b in bk])
        rows.append({"feature": f, "buckets": bk, "t": t, "p": p, "rho": rho,
                     "thin": any(b["n"] < MIN_BUCKET for b in bk)})
    survives = ar.holm([r["p"] for r in rows], alpha=ALPHA)
    for r, s in zip(rows, survives):
        r["holm"] = bool(s)
    return rows


def best_filter(trades: list[dict], row: dict) -> dict | None:
    """
    Apply the candidate as a filter: keep the better half of its buckets.

    The split point is the one the DATA picks — everything above the bucket
    where expectancy turns positive. That is deliberately generous to the
    feature, because clause 4 then judges the result rather than the method.
    """
    bk = row["buckets"]
    if len(bk) < 2:
        return None
    keep_from = min(range(len(bk)), key=lambda i: -bk[i]["expectancy"])
    order = sorted(range(len(bk)), key=lambda i: -bk[i]["expectancy"])
    best = None
    for take in range(1, len(bk)):
        idx = set(order[:take])
        kept_n = sum(bk[i]["n"] for i in idx)
        total = sum(b["n"] for b in bk)
        exp = sum(bk[i]["expectancy"] * bk[i]["n"] for i in idx) / kept_n
        disc = 100.0 * (total - kept_n) / total
        if best is None or exp > best["expectancy"]:
            best = {"keep_buckets": sorted(idx), "kept_n": kept_n,
                    "expectancy": exp, "discard_pct": disc}
    return best


def verdict(rows: list[dict], trades: list[dict]) -> tuple[bool, list[str]]:
    """The pre-registered bar, clause for clause."""
    reasons = []
    thin = [r["feature"] for r in rows if r["thin"]]
    if thin:
        reasons.append(
            f"clause 1: buckets under {MIN_BUCKET} trades in {', '.join(thin)} — "
            f"a thin bucket with a big observed effect is not evidence")
    winners = [r for r in rows if r.get("holm")]
    if not winners:
        best = min(rows, key=lambda r: r["p"])
        reasons.append(
            f"clause 2: no feature survives Holm across {len(rows)} — best is "
            f"{best['feature']} at p {best['p']:.4f}, needing "
            f"{ALPHA/len(rows):.4f}")
        return False, reasons
    graded = [r for r in winners if abs(r["rho"]) >= MIN_RHO]
    if not graded:
        reasons.append(
            f"clause 3: {', '.join(r['feature'] for r in winners)} cleared Holm "
            f"but no dose-response (|rho| < {MIN_RHO}) — one freak bucket, "
            f"not a pattern")
        return False, reasons
    for r in graded:
        f = best_filter(trades, r)
        if f is None:
            continue
        if f["discard_pct"] < MIN_DISCARD_PCT:
            reasons.append(
                f"clause 5: {r['feature']} discards only {f['discard_pct']:.0f}% "
                f"— under {MIN_DISCARD_PCT:.0f}% no filter closes an 8.4-point gap")
            continue
        if f["expectancy"] <= 0:
            reasons.append(
                f"clause 4: {r['feature']} filtered to {f['expectancy']:+.2f}% — "
                f"an improvement to a losing system is not a tradeable result")
            continue
        return True, [f"{r['feature']} survives every clause: filtered "
                      f"expectancy {f['expectancy']:+.2f}% keeping "
                      f"{100 - f['discard_pct']:.0f}% of signals"]
    return False, reasons


def report(rows: list[dict], trades: list[dict]) -> int:
    be = rp.OPT_WIN_RATE_SL_PCT / (rp.OPT_WIN_RATE_TP_PCT + rp.OPT_WIN_RATE_SL_PCT)
    allp = np.array([t["pnl_pct"] for t in trades], dtype=float)
    print("=" * 88)
    print("ENTRY-FEATURE SWEEP — does anything at entry separate TP from SL?")
    print("=" * 88)
    print(f"  {len(trades)} trades   TP +{rp.OPT_WIN_RATE_TP_PCT:.0f}% / "
          f"SL -{rp.OPT_WIN_RATE_SL_PCT:.0f}%   breakeven {be:.1%}")
    print(f"  base expectancy {allp.mean():+.2f}%   base win rate "
          f"{100*(allp>0).mean():.1f}%")
    print("=" * 88)
    hdr = (f"  {'feature':<12}{'buckets':>8}{'lo exp':>9}{'hi exp':>9}"
           f"{'t':>7}{'p':>9}{'Holm':>6}{'rho':>7}{'min n':>7}")
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    for r in sorted(rows, key=lambda x: x["p"]):
        bk = r["buckets"]
        if len(bk) < 2:
            print(f"  {r['feature']:<12}{'(unusable)':>8}")
            continue
        print(f"  {r['feature']:<12}{len(bk):>8}{bk[0]['expectancy']:>+9.1f}"
              f"{bk[-1]['expectancy']:>+9.1f}{r['t']:>7.2f}{r['p']:>9.4f}"
              f"{('YES' if r['holm'] else 'no'):>6}{r['rho']:>7.2f}"
              f"{min(b['n'] for b in bk):>7}")

    print(f"\n  BUCKET DETAIL for the three smallest p")
    for r in sorted(rows, key=lambda x: x["p"])[:3]:
        print(f"\n  {r['feature']}")
        print(f"    {'bucket':<18}{'n':>6}{'exp %':>9}{'win %':>8}")
        for b in r["buckets"]:
            print(f"    {b['label']:<18}{b['n']:>6}{b['expectancy']:>+9.1f}"
                  f"{b['win_rate']:>8.1f}")

    ok, why = verdict(rows, trades)
    print()
    print("=" * 88)
    print("PRE-REGISTERED VERDICT")
    print("=" * 88)
    if ok:
        for w in why:
            print(f"  PASS — {w}")
        print("\n  This licenses FORWARD PAPER TRACKING of the filtered subset and")
        print("  nothing else. Both tranches are spent, so it is out-of-sample")
        print("  against nothing and can never be confirmed.")
    else:
        print("  NO FEATURE CLEARS THE BAR")
        for w in why:
            print(f"    - {w}")
        print("\n  A null here is decisive about the effects that MATTER: the design")
        print("  detects ~14 win-rate points and a usable filter needs ~15. It says")
        print("  nothing about small effects, which would not have closed the gap.")
        print("  Do NOT add features — each one costs power and makes the next")
        print("  harder to find.")
    print("=" * 88)
    return 0 if ok else 1


def selftest() -> int:
    print("feature_sweep.py selftest")
    print("=" * 72)

    def mk(fv, pnl, feat="adx"):
        return {"features": {feat: fv}, "pnl_pct": pnl, "reason": "TIME"}

    # ── equal-count bucketing, and NaN dropped not pooled ──
    tr = [mk(float(i), float(i)) for i in range(100)] + [mk(float("nan"), 5.0)]
    bk = bucket(tr, "adx")
    assert len(bk) == 5 and all(b["n"] == 20 for b in bk), [b["n"] for b in bk]
    assert bk[0]["dropped_nan"] == 1, bk[0]["dropped_nan"]
    assert bk[0]["expectancy"] < bk[-1]["expectancy"]
    print(f"bucketing        : 5 x 20, 1 NaN dropped not pooled")

    # ── a binary feature gets TWO groups, never five ──
    bs = [mk(1.0, 10.0, "side") for _ in range(60)] + \
         [mk(0.0, -10.0, "side") for _ in range(40)]
    bb = bucket(bs, "side")
    assert len(bb) == 2 and {b["n"] for b in bb} == {60, 40}, bb
    print(f"binary feature   : side -> 2 groups of {[b['n'] for b in bb]}")

    # ── Holm must come from adx_retest, not a second copy ──
    assert ar.holm([0.001, 0.5]) == [True, False], ar.holm([0.001, 0.5])
    assert ar.holm([0.03, 0.04]) == [False, False], (
        "two p just under 0.05 must BOTH fail Holm at alpha/2 — that is the "
        "whole point of correcting for the sweep")
    print("holm             : shared with adx_retest, and 0.03/0.04 both fail")

    # ── clause 2: nothing survives Holm -> fail, naming the best ──
    rng = np.random.default_rng(4)
    noise = [mk(float(rng.normal()), float(rng.normal(0, 70))) for _ in range(1400)]
    rows = analyse(noise)
    ok, why = verdict(rows, noise)
    assert not ok and any("clause 2" in w for w in why), why
    print("clause 2         : pure noise -> no feature survives Holm")

    # ── HOLM MUST ACTUALLY BE APPLIED, not merely imported ──
    # The noise fixture above cannot prove this: it produced no feature with a
    # raw p under 0.05, so swapping Holm for a bare `p < ALPHA` left the suite
    # green. Found by breaking it. This case is built to land BETWEEN the two
    # thresholds — nominally significant, killed by the correction.
    # Each bucket is RECENTERED to an exact target mean rather than left to the
    # draw. A first attempt specified the means and let the RNG land where it
    # liked; it came back 1 sigma high at p 0.0007, below the Holm threshold, so
    # the test would have passed or failed on the seed.
    weak, per = [], 280
    for q in range(5):
        noise = rng.normal(0.0, 70.0, per)
        vals = noise - noise.mean() + (-7.1 + 3.55 * q)   # exact bucket mean
        for v in vals:
            weak.append({"features": {f: (float(q) if f == "adx" else 0.0)
                                      for f in FEATURES},
                         "pnl_pct": float(v), "reason": "TIME"})
    rows_w = analyse(weak)
    a_w = next(r for r in rows_w if r["feature"] == "adx")
    assert a_w["p"] < ALPHA, (
        f"fixture must be nominally significant to test the correction, "
        f"p = {a_w['p']:.4f}")
    assert a_w["p"] > ALPHA / len(FEATURES), (
        f"...and must NOT clear the Holm threshold {ALPHA/len(FEATURES):.4f}, "
        f"or it tests nothing; p = {a_w['p']:.4f}")
    assert not a_w["holm"], (
        f"p {a_w['p']:.4f} beats a bare alpha of {ALPHA} but must FAIL Holm "
        f"across {len(FEATURES)} features — if it survives, the sweep is "
        f"accepting the best of ten as though only one had been tried")
    ok_w, _ = verdict(rows_w, weak)
    assert not ok_w
    print(f"holm applied     : p {a_w['p']:.4f} beats alpha {ALPHA} and still "
          f"fails Holm at {ALPHA/len(FEATURES):.4f}")

    # ── clause 4: a REAL, significant, dose-responsive effect that still loses ──
    # Every clause but 4 clears and the verdict must still be FAIL.
    strong = []
    for i in range(1400):
        q = i % 5
        strong.append({"features": {f: (float(q) if f == "adx" else 0.0)
                                    for f in FEATURES},
                       # EVERY bucket negative: -80, -65, -50, -35, -20. The
                       # gradient is real and significant, and the best bucket a
                       # filter can reach is still -20%. The first version used
                       # -60 + 20q, whose top bucket was +20 — so filtering to it
                       # legitimately passed, and the fixture tested nothing.
                       "pnl_pct": float(rng.normal(-80 + 15 * q, 25)),
                       "reason": "TIME"})
    rows = analyse(strong)
    adx = next(r for r in rows if r["feature"] == "adx")
    assert adx["holm"] and abs(adx["rho"]) >= MIN_RHO, (adx["p"], adx["rho"])
    ok, why = verdict(rows, strong)
    assert not ok and any("clause 4" in w for w in why), (ok, why)
    print(f"clause 4         : real effect (p {adx['p']:.1e}, rho {adx['rho']:+.2f}) "
          f"still FAILS — filtered expectancy stays negative")

    # ── ...and the same shape shifted positive must PASS. The bar is not inert. ──
    good = []
    for i in range(1400):
        q = i % 5
        good.append({"features": {f: (float(q) if f == "adx" else 0.0)
                                  for f in FEATURES},
                     "pnl_pct": float(rng.normal(-40 + 30 * q, 25)),
                     "reason": "TIME"})
    rows_g = analyse(good)
    ok_g, why_g = verdict(rows_g, good)
    assert ok_g, f"a filter reaching positive expectancy must pass: {why_g}"
    print(f"bar liveness     : the same shape reaching positive expectancy PASSES")

    # ── clause 1: a thin bucket is named, not silently accepted ──
    # EVERY feature must be populated. A first version set only "adx", so the
    # other nine produced empty buckets and hit the separate `len(bk) < 2`
    # branch that marks thin unconditionally — clause 1 then fired for the wrong
    # reason and the test passed even with the bucket-size check deleted.
    # Found by breaking it.
    thin = [{"features": {f: float(i) for f in FEATURES},
             "pnl_pct": float(rng.normal(0, 70)), "reason": "TIME"}
            for i in range(200)]
    rows_t = analyse(thin)
    ok_t, why_t = verdict(rows_t, thin)
    assert not ok_t and any("clause 1" in w for w in why_t), why_t
    print(f"clause 1         : buckets of 40 named as thin (floor {MIN_BUCKET})")

    # ── CLAUSE 3 was never tested until falsification found it ──
    # Deleting the dose-response filter left the suite green, because no fixture
    # produced a feature that CLEARS Holm and FAILS rho. This one does: the
    # top-vs-bottom gap is large and highly significant, and the middle buckets
    # are scrambled, so it is one freak bucket rather than a pattern.
    freak, per = [], 280
    for q, target in enumerate((-60.0, 40.0, -50.0, 35.0, -20.0)):
        noise = rng.normal(0.0, 40.0, per)
        for v in noise - noise.mean() + target:
            freak.append({"features": {f: (float(q) if f == "adx" else 0.0)
                                       for f in FEATURES},
                          "pnl_pct": float(v), "reason": "TIME"})
    rows_f = analyse(freak)
    a_f = next(r for r in rows_f if r["feature"] == "adx")
    assert a_f["holm"], f"fixture must clear Holm to test clause 3, p={a_f['p']:.2e}"
    assert abs(a_f["rho"]) < MIN_RHO, (
        f"...and must FAIL dose-response, rho={a_f['rho']:+.2f}")
    ok_f, why_f = verdict(rows_f, freak)
    assert not ok_f and any("clause 3" in w for w in why_f), (ok_f, why_f)
    print(f"clause 3         : p {a_f['p']:.1e} clears Holm, rho {a_f['rho']:+.2f} "
          f"sinks it — a freak bucket is not a pattern")

    # ── a MISSING column must read NaN, never 0.0 ──
    # Also untested until falsification: zeroing a missing feature upstream left
    # every guard green, because the clean frame has every column. A zero is a
    # measurement; a missing column is not.
    import pandas as _pd
    bare = _pd.DataFrame({"Close": [100.0] * 50, "Volume": [3e6] * 50})
    nf = ob.entry_features(bare, 40, rv=0.25, prem0=3.0,
                           spot0=100.0, right="CALL")
    for f in ("adx", "rsi", "ema_spread", "macd_hist"):
        assert nf[f] != nf[f], (
            f"{f} came back {nf[f]!r} on a frame with no indicator columns; a "
            f"missing column must be NaN so the trade is DROPPED, not bucketed "
            f"as though zero had been observed")
    assert nf["side"] == 1.0 and nf["rv"] == 25.0, nf
    print("missing columns  : NaN, not 0.0 — dropped rather than bucketed")

    # ── WIRING: the features this module names must EXIST upstream ──
    import pandas as pd
    n = 300
    r2 = np.random.default_rng(9)
    px = 100 * np.exp(np.cumsum(r2.normal(0.0006, 0.015, n)))
    df = bt.compute(pd.DataFrame({
        "Date": pd.bdate_range("2023-01-02", periods=n), "Open": px,
        "High": px * 1.01, "Low": px * 0.99, "Close": px,
        "Volume": np.full(n, 3e6)}))
    # bt.compute() DROPS the indicator warmup, so the computed frame is much
    # shorter than the raw one — index off its own length, never the input's.
    assert len(df) > 20, f"computed frame too short to test: {len(df)}"
    probe_i = len(df) - 10
    feats = ob.entry_features(df, probe_i, rv=0.25, prem0=3.0, spot0=100.0,
                              right="CALL")
    missing = [f for f in FEATURES if f not in feats]
    assert not missing, (
        f"option_backtest.entry_features does not produce {missing} — every "
        f"bucket would be empty and the sweep would silently measure nothing")
    bad = [f for f in FEATURES if feats[f] != feats[f]]
    assert not bad, f"features came back NaN on a clean frame: {bad}"
    print(f"wiring           : all {len(FEATURES)} features present and finite")

    # ── NO LOOKAHEAD: features must read the SIGNAL bar, not the entry bar ──
    df2 = df.copy()
    df2.loc[df2.index[probe_i + 1:], ["ADX", "RSI", "ATR", "EMA20", "EMA50",
                                      "MACD", "Signal", "Volume", "Close"]] = 1e9
    after = ob.entry_features(df2, probe_i, rv=0.25, prem0=3.0, spot0=100.0,
                              right="CALL")
    assert after == feats, (
        "corrupting every bar AFTER the signal changed the features — "
        "entry_features is reading past signal_i, which is lookahead")
    print("no lookahead     : bars after the signal cannot change the features")

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
    trades = [t for t in collect(tickers, a.years, a.dte, a.iv_mult, a.spread_pct)
              if t.get("pnl_pct") is not None]
    if not trades:
        print("NOTHING MEASURED — no trades.")
        return 2
    return report(analyse(trades), trades)


if __name__ == "__main__":
    sys.exit(main())
