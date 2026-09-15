#!/usr/bin/env python3
"""
setup_population.py — the stage-1 comparison, run on every trade instead of 80.

WHAT THIS IS FOR

setup_cases.py shows hand-chosen cases so a human can eyeball them. That is
what it is for and it does it honestly, but 80 cases out of 2,360 trades cannot
settle anything: its longs came back flat across every reading, and its shorts
produced four effects at Cohen's d ~ 1.0 with none surviving Holm, on twelve
winners and eleven losers.

This runs the same comparison — REACHED ITS TARGET vs HIT ITS STOP — across the
whole population, so a null is a real null rather than an underpowered one, and
an effect that only existed in 23 cases has somewhere to die.

Pre-registered in results/setup_population_preregistration.md, committed before
this file existed.

WHAT IT IS NOT

Confirmation. Every ticker in the default set is spent. This has real
statistical power and zero confirmatory value: it generates a hypothesis with a
trustworthy n. Tranche C is unspent and is still the only thing that could test
whatever comes out of here.

THE THIRD CLAUSE, WHICH IS THE ONE THAT MATTERS

Two clauses are the usual ones: survive Holm, and show a monotone gradient
across terciles. The third is economic, and it exists because this population
runs at mean R -0.012 — break-even to slightly negative.

A reading can separate a 28% target rate from a 33% one, clear Holm at
n = 1,700, and still leave every bucket losing money. Statistical separation on
a zero-edge system is not an edge. So the favoured tercile's mean R must be
POSITIVE, or the reading is recorded as REAL BUT UNPROFITABLE and goes no
further.
"""
from __future__ import annotations

import argparse
import math
import sys

import numpy as np

import adx_retest as ar
import power_check as pc
import setup_cases as scs

ALPHA = ar.ALPHA
POWER = ar.POWER

# THE READINGS, FIXED. The Holm denominator is len(READINGS) and must not move
# with what the data turns out to look like.
#
# `stop %` is NOT here: 75 of 80 stage-1 cases had it equal to `ATR %`, because
# the stop IS 1.0 x ATR. Including it would count one reading as two pieces of
# evidence and inflate the denominator with a duplicate.
#
# `gates passed` is NOT here: evaluate_signal() returns nothing unless every
# gate passes, so it is 4 on every trade that reaches this module. A reading
# with one possible value cannot discriminate and is not a null result.
READINGS = (
    ("planned R:R",     lambda t: float(t["setup"].get("rr", 0))),
    ("RSI",             lambda t: float(t["setup"].get("rsi", 0))),
    ("ADX",             lambda t: float(t["setup"].get("adx", 0))),
    ("ATR %",           lambda t: scs.derived(t)["atr_pct"]),
    ("volume x",        lambda t: float(t["setup"].get("vol_ratio", 0))),
    ("price-EMA20 ATR", lambda t: scs.derived(t)["px_vs_ema20_atr"]),
    ("EMA20-EMA50 ATR", lambda t: scs.derived(t)["ema20_vs_ema50_atr"]),
)


def sides(trades: list[dict]) -> dict[str, list[dict]]:
    """
    Longs and shorts, timeouts dropped.

    SPLIT BEFORE COMPARING. Pooled, RSI runs 57.7-75.0 on longs and 25.2-47.4
    on shorts, so its mean averages two opposite conditions and measures the
    sample's long/short mix rather than the setup. A timeout neither reached
    the target nor hit the stop; assigning it to a side invents an outcome the
    trade did not have.
    """
    out: dict[str, list[dict]] = {"LONGS": [], "SHORTS": []}
    for t in trades:
        if t.get("outcome") not in ("win", "loss"):
            continue
        trend = (t.get("setup") or {}).get("trend")
        if trend == "Bullish":
            out["LONGS"].append(t)
        elif trend == "Bearish":
            out["SHORTS"].append(t)
    return out


def welch(a: list[float], b: list[float]) -> dict:
    """Two-sample Welch t and a two-sided p, plus Cohen's d.

    Normal approximation for p, as everywhere else in this project (see
    adx_retest.t_pvalue) — n here is in the hundreds, where it is accurate.
    """
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return {"t": 0.0, "p": 1.0, "d": 0.0, "gap": 0.0}
    va, vb = float(np.var(a, ddof=1)), float(np.var(b, ddof=1))
    se = math.sqrt(va / na + vb / nb)
    gap = float(np.mean(a) - np.mean(b))
    if se <= 0:
        return {"t": 0.0, "p": 1.0, "d": 0.0, "gap": gap}
    t = gap / se
    pooled = math.sqrt((va + vb) / 2.0)
    return {"t": t, "p": math.erfc(abs(t) / math.sqrt(2.0)),
            "d": gap / pooled if pooled > 0 else 0.0, "gap": gap}


def mech_baseline(ts: list[dict]) -> float:
    """
    P(touch target before stop) for a DRIFTLESS walk: 1/(1+R:R).

    THIS IS THE NUMBER TO BEAT, and without it a bucket readout is theatre.
    Run 1's only surviving reading was `planned R:R`, whose terciles hit their
    targets 47.8% / 30.3% / 29.0% of the time — against a baseline of roughly
    41% / 28% / 24%. Trades with nearer targets reach them more often. That is
    arithmetic, not a property of the setup, and a tool that reports the raw
    rate is reporting the target distance back to the user in disguise.

    The informational content is the EXCESS: how far the observed rate sits
    above what the target distance alone implies. For the R:R reading itself
    the excess is near zero by construction, which is exactly the tautology
    made visible instead of hidden.
    """
    vals = [1.0 / (1.0 + float(t["setup"].get("rr", 0)))
            for t in ts if float(t["setup"].get("rr", 0) or 0) > 0]
    return float(np.mean(vals)) if vals else float("nan")


def mean_ci(vals: list[float], z: float = 1.96) -> tuple[float, float, float]:
    """Mean and its 95% interval. A bucket estimate without one invites
    over-reading: 30% on 40 trades and 30% on 400 are different claims."""
    n = len(vals)
    if n < 2:
        return (float("nan"), float("nan"), float("nan"))
    m = float(np.mean(vals))
    half = z * float(np.std(vals, ddof=1)) / math.sqrt(n)
    return (m, m - half, m + half)


def two_prop_z(k1: int, n1: int, k2: int, n2: int) -> float:
    """Unpooled z for two rates. Used on EXCESS rates, where the baselines are
    observed rather than estimated, so the variance is the binomial one."""
    if n1 < 2 or n2 < 2:
        return 0.0
    p1, p2 = k1 / n1, k2 / n2
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    return (p1 - p2) / se if se > 0 else 0.0


def terciles(sub: list[dict], getter, expect_sign: float) -> dict:
    """
    Sorted into three by the reading: target rate and mean R in each.

    THIS CLAUSE CAN ONLY SINK A READING, NEVER RESCUE ONE. A difference of
    means with no gradient behind it is what a lucky tail looks like; a real
    effect should show more of itself as the reading increases.

    `expect_sign` comes from the sign of the mean gap, so the direction is
    decided by clause 1 and not chosen afterwards to suit the buckets.
    """
    vals = [(getter(t), t) for t in sub]
    vals = [(v, t) for v, t in vals if np.isfinite(v)]
    n = len(vals)
    if n < 6:
        return {"ok": False, "reason": f"only {n} trades", "buckets": []}
    vals.sort(key=lambda vt: vt[0])
    k = n // 3
    cut = [vals[:k], vals[k:2 * k], vals[2 * k:]]
    buckets = []
    for grp in cut:
        ts = [t for _, t in grp]
        hit = sum(1 for t in ts if t["outcome"] == "win")
        base = mech_baseline(ts)
        m, lo, hi = mean_ci([t["r"] for t in ts])
        buckets.append({
            "lo": grp[0][0], "hi": grp[-1][0], "n": len(ts), "hits": hit,
            "rate": hit / len(ts),
            "base": base,
            "excess": hit / len(ts) - base if np.isfinite(base) else float("nan"),
            "mean_r": m, "r_lo": lo, "r_hi": hi,
            "rr": float(np.mean([float(t["setup"].get("rr", 0)) for t in ts])),
        })
    rho = ar.spearman([0.0, 1.0, 2.0], [b["rate"] for b in buckets])
    monotone = abs(rho) == 1.0 and (rho > 0) == (expect_sign > 0)
    # The favoured bucket is the END the mean gap points at, named before its
    # mean R is read.
    fav = buckets[-1] if expect_sign > 0 else buckets[0]
    # THE FAVOURED BUCKET MUST ALSO BE THE BEST ONE. See the amendment in the
    # pre-registration: run 1 passed `planned R:R` because its favoured bucket
    # had a positive mean R (+0.044) while being the WORST of the three (+0.153
    # and +0.117 above it). A rule preferring that bucket raises the hit rate
    # and cuts the return. "Makes money" was the wrong question; "beats the
    # alternatives" is the one a filter has to answer.
    best = max(buckets, key=lambda b: b["mean_r"])
    return {"ok": True, "buckets": buckets, "rho": rho,
            "monotone": monotone, "favoured": fav,
            "fav_is_best": fav is best,
            "best_mean_r": best["mean_r"]}


def assess(sub: list[dict], label: str) -> dict:
    """Every reading on one side, Holm-corrected within that side."""
    w = [t for t in sub if t["outcome"] == "win"]
    l = [t for t in sub if t["outcome"] == "loss"]
    rows = []
    for name, fn in READINGS:
        a = [fn(t) for t in w]
        b = [fn(t) for t in l]
        a = [x for x in a if np.isfinite(x)]
        b = [x for x in b if np.isfinite(x)]
        st = welch(a, b)
        rows.append({"name": name, "fn": fn,
                     "mean_w": float(np.mean(a)) if a else float("nan"),
                     "mean_l": float(np.mean(b)) if b else float("nan"),
                     **st})
    keep = ar.holm([r["p"] for r in rows], ALPHA)
    for r, k in zip(rows, keep):
        r["holm"] = k
        tc = terciles(sub, r["fn"], r["gap"])
        r["terciles"] = tc
        if not k:
            r["verdict"] = "NOT ESTABLISHED"
        elif not tc.get("monotone"):
            r["verdict"] = "NO DOSE-RESPONSE"
        elif tc["favoured"]["mean_r"] <= 0:
            r["verdict"] = "REAL BUT UNPROFITABLE"
        elif not tc.get("fav_is_best"):
            r["verdict"] = "NOT THE BEST BUCKET"
        else:
            r["verdict"] = "TRADEABLE"

    # ── THE SECOND LENS: is the bucket INFORMATIVE? ──
    #
    # A filter asks "trade this bucket, skip the others". A readout asks "given
    # that this trade exists, what should I expect". They are different
    # questions and a reading can answer one and not the other.
    #
    # The informational test is on EXCESS hit rate, never the raw rate: the raw
    # rate is mostly 1/(1+R:R), which the user can compute from the target
    # distance without a tool. A reading is informative only if knowing the
    # bucket moves the expectation BEYOND what the target distance already
    # implies.
    exc_p = []
    for r in rows:
        tc = r["terciles"]
        if not tc.get("ok"):
            exc_p.append(1.0)
            continue
        bs = [b for b in tc["buckets"] if np.isfinite(b["excess"])]
        if len(bs) < 2:
            exc_p.append(1.0)
            continue
        top = max(bs, key=lambda b: b["excess"])
        bot = min(bs, key=lambda b: b["excess"])
        z = two_prop_z(top["hits"], top["n"], bot["hits"], bot["n"])
        # The baselines differ between the two buckets, so the quantity being
        # tested is the excess spread, not the raw rate spread.
        adj = (top["excess"] - bot["excess"])
        raw = (top["rate"] - bot["rate"])
        z = z * (adj / raw) if raw not in (0.0,) else 0.0
        r["excess_spread"] = adj
        exc_p.append(math.erfc(abs(z) / math.sqrt(2.0)))
    keep_i = ar.holm(exc_p, ALPHA)
    for r, p_, k_ in zip(rows, exc_p, keep_i):
        r["info_p"] = p_
        r["informative"] = k_
    return {"label": label, "n_win": len(w), "n_loss": len(l), "rows": rows}


def mde_d(n_a: int, n_b: int, *, alpha: float = ALPHA,
          power: float = POWER) -> float:
    """Smallest Cohen's d this comparison could detect, at the stated power."""
    if n_a < 2 or n_b < 2:
        return float("inf")
    z = pc._z(pc.Z_ALPHA, alpha, "alpha") + pc._z(pc.Z_POWER, power, "power")
    return z * math.sqrt(1.0 / n_a + 1.0 / n_b)


def report(res: dict) -> None:
    n_w, n_l = res["n_win"], res["n_loss"]
    n = n_w + n_l
    print(f"\n{'='*84}")
    rate = f"   {n_w/n*100:.1f}% target rate" if n else ""
    print(f"{res['label']}   {n_w} reached target, {n_l} hit stop{rate}")
    print(f"  detectable effect at alpha {ALPHA}, power {POWER}: "
          f"d = {mde_d(n_w, n_l):.3f}")
    print("=" * 84)
    print(f"  {'reading':<18}{'target':>9}{'stopped':>9}{'gap':>8}{'d':>7}"
          f"{'p':>9}{'Holm':>6}   filter verdict / informative")
    print("  " + "-" * 82)
    for r in res["rows"]:
        tc = r["terciles"]
        info = (f"INFORMATIVE (p {r.get('info_p', 1):.4f})"
                if r.get("informative") else "not informative")
        print(f"  {r['name']:<18}{r['mean_w']:>9.2f}{r['mean_l']:>9.2f}"
              f"{r['gap']:>+8.2f}{r['d']:>+7.2f}"
              f"{r['p']:>9.4f}{'YES' if r['holm'] else 'no':>6}   "
              f"{r['verdict']}  |  {info}")
        if not tc.get("ok"):
            continue
        # hit rate, what the TARGET DISTANCE alone implies, and the excess.
        # The third column is the only one carrying information the user could
        # not get from the setup's own R:R.
        print(f"      {'bucket':<22}{'n':>5}{'hit':>7}{'base':>7}{'excess':>8}"
              f"{'mean R':>9}{'95% CI':>18}")
        for b in tc["buckets"]:
            rng = f"[{b['lo']:.2f}..{b['hi']:.2f}]"
            print(f"      {rng:<22}{b['n']:>5}{b['rate']*100:>6.1f}%"
                  f"{b['base']*100:>6.1f}%{b['excess']*100:>+7.1f}%"
                  f"{b['mean_r']:>+9.3f}"
                  f"   [{b['r_lo']:+.3f}, {b['r_hi']:+.3f}]")
        print(f"      rho {tc['rho']:+.1f} on hit rate; favoured bucket is "
              f"{'the best' if tc.get('fav_is_best') else 'NOT the best'} "
              f"by mean R (best {tc['best_mean_r']:+.3f})")


def verdict(all_res: list[dict]) -> str:
    cands = [(r["label"], row["name"]) for r in all_res
             for row in r["rows"] if row["verdict"] == "TRADEABLE"]
    unprof = [(r["label"], row["name"]) for r in all_res
              for row in r["rows"] if row["verdict"] == "REAL BUT UNPROFITABLE"]
    notbest = [(r["label"], row["name"]) for r in all_res
               for row in r["rows"] if row["verdict"] == "NOT THE BEST BUCKET"]
    infos = [(r["label"], row["name"], row.get("excess_spread", 0.0))
             for r in all_res for row in r["rows"] if row.get("informative")]
    print(f"\n{'='*84}")
    print("LENS 1 — FILTER: trade this bucket, skip the others")
    if cands:
        print("  TRADEABLE, cleared all four clauses:")
        for side, name in cands:
            print(f"    {side}: {name}")
    else:
        print("  Nothing tradeable. No reading cleared all four clauses.")
    if notbest:
        print("  NOT THE BEST BUCKET — separates the outcomes, but the bucket")
        print("  it points at earns less than the ones it would have you skip:")
        for side, name in notbest:
            print(f"    {side}: {name}")
    if unprof:
        print("  REAL BUT UNPROFITABLE — separates the outcomes, does not pay:")
        for side, name in unprof:
            print(f"    {side}: {name}")

    print("\nLENS 2 — READOUT: given this trade, what should I expect")
    print("  Tested on EXCESS hit rate, never the raw rate. The raw rate is")
    print("  mostly 1/(1+R:R), which the setup already tells you.")
    if infos:
        print("  INFORMATIVE — knowing the bucket moves the expectation beyond")
        print("  what the target distance alone implies:")
        for side, name, sp in sorted(infos, key=lambda x: -abs(x[2])):
            print(f"    {side}: {name}   excess spread {sp*100:+.1f} pp")
    else:
        print("  Nothing informative. Every reading's bucket differences are")
        print("  explained by the target distance the setup already carries.")
    print("\nIN-SAMPLE. Every ticker here is spent. This has power and no")
    print("confirmatory value. Tranche C is unspent and is the only test left.")
    print("=" * 84)
    return "CANDIDATES" if cands else "NULL"


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------

def selftest() -> int:
    print("setup_population.py selftest")
    print("=" * 72)

    def mk(outcome, r, trend="Bullish", **kw):
        s = {"trend": trend, "price": 100.0, "entry": 100.0, "stop": 98.0,
             "target": 106.0, "rr": 3.0, "rsi": 60.0, "adx": 30.0, "atr": 2.0,
             "ema20": 99.0, "ema50": 96.0, "vol_ratio": 1.2,
             "filters_pass": 4, "filters_total": 4}
        s.update(kw)
        return {"ticker": "AAA", "r": r, "outcome": outcome,
                "entry": s["entry"], "stop": s["stop"], "setup": s}

    # ── the Holm denominator is the reading count and must not drift ──
    assert len(READINGS) == 7, (
        f"{len(READINGS)} readings — the pre-registration fixed seven and the "
        f"Holm denominator with them. Changing this after the fact is how a "
        f"correction stops correcting")
    names = [n for n, _ in READINGS]
    assert len(names) == len(set(names)), f"duplicate reading: {names}"
    assert "stop %" not in names, (
        "stop % duplicates ATR % — the stop IS 1.0 x ATR, and 75 of 80 stage-1 "
        "cases had them equal. One reading must not be counted as two")
    assert "gates passed" not in names, (
        "gates passed cannot vary: evaluate_signal() returns nothing unless "
        "every gate passes. A constant is not a null result")
    print(f"readings         : {len(READINGS)}, no duplicate, no constant")

    # ── sides split, timeouts dropped ──
    mixed = [mk("win", 3.0), mk("loss", -1.0),
             mk("win", 3.0, trend="Bearish"), mk("loss", -1.0, trend="Bearish"),
             mk("timeout", 0.3), mk("timeout", 0.2, trend="Bearish")]
    sp = sides(mixed)
    assert len(sp["LONGS"]) == 2 and len(sp["SHORTS"]) == 2, sp
    assert all(t["outcome"] != "timeout"
               for v in sp.values() for t in v), (
        "a timeout neither reached the target nor hit the stop; counting it as "
        "either invents an outcome the trade did not have")
    print("sides            : longs/shorts split, timeouts dropped")

    # ── welch: a real difference is found, an identical pair is not ──
    hi = [10.0, 11.0, 9.0, 10.5, 9.5] * 8
    lo = [5.0, 6.0, 4.0, 5.5, 4.5] * 8
    w = welch(hi, lo)
    assert w["p"] < 0.001 and w["d"] > 2.0, w
    same = welch(hi, list(hi))
    assert same["p"] > 0.99 and abs(same["gap"]) < 1e-9, same
    print(f"welch            : d {w['d']:.1f} p {w['p']:.1e}; identical -> p 1.0")

    # ── MDE shrinks with n, and says so honestly at tiny n ──
    assert mde_d(12, 11) > mde_d(300, 700) > mde_d(3000, 7000), (
        "the detectable effect must fall as n rises")
    assert abs(mde_d(850, 850) - 0.136) < 0.005, mde_d(850, 850)
    print(f"power            : d_mde {mde_d(12,11):.2f} at n=12/11, "
          f"{mde_d(300,700):.3f} at n=300/700")

    # ── DOSE-RESPONSE CAN ONLY SINK, NEVER RESCUE ──
    # A reading whose means differ but whose buckets show no gradient must not
    # pass. Built so the top and bottom terciles hit at the same rate and the
    # middle is high: the mean gap is real, the gradient is not.
    hump = ([mk("loss", -1.0, rsi=10.0 + i) for i in range(6)] +
            [mk("win", 3.0, rsi=30.0 + i) for i in range(6)] +
            [mk("loss", -1.0, rsi=50.0 + i) for i in range(6)])
    tc = terciles(hump, dict(READINGS)["RSI"], +1.0)
    assert tc["ok"] and not tc["monotone"], tc
    print(f"dose-response    : hump-shaped buckets rejected (rho {tc['rho']:+.1f})")

    # ── a genuine gradient passes, in the direction the mean gap implies ──
    ramp = ([mk("loss", -1.0, rsi=10.0 + i) for i in range(5)] +
            [mk("win", 3.0, rsi=15.0)] +
            [mk("loss", -1.0, rsi=30.0 + i) for i in range(3)] +
            [mk("win", 3.0, rsi=35.0 + i) for i in range(3)] +
            [mk("win", 3.0, rsi=50.0 + i) for i in range(5)] +
            [mk("loss", -1.0, rsi=55.0)])
    tc = terciles(ramp, dict(READINGS)["RSI"], +1.0)
    assert tc["ok"] and tc["monotone"] and tc["rho"] == 1.0, tc
    assert tc["favoured"] is tc["buckets"][-1], (
        "with a positive gap the favoured bucket is the TOP tercile")
    tc_neg = terciles(ramp, dict(READINGS)["RSI"], -1.0)
    assert not tc_neg["monotone"], (
        "a rising gradient must not satisfy a reading whose mean gap points "
        "down — the direction is decided by clause 1, not chosen afterwards")
    assert tc_neg["favoured"] is tc_neg["buckets"][0]
    print("direction        : gradient must match the sign of the mean gap")

    # ── THE ECONOMIC CLAUSE ──
    # A reading that separates the outcomes cleanly but leaves the favoured
    # bucket losing money is REAL BUT UNPROFITABLE, never a candidate.
    # Payoff +0.4 R per win against -1.0 R per loss: a 40% target rate still
    # loses. The separation is real; the bucket is not tradeable.
    poor = []
    for i in range(40):
        poor.append(mk("loss", -1.0, rsi=10.0 + i * 0.1))
    for i in range(40):
        poor.append(mk("win", 0.4, rsi=50.0 + i * 0.1) if i % 5 == 0
                    else mk("loss", -1.0, rsi=50.0 + i * 0.1))
    res = assess(poor, "PROBE")
    rsi_row = [r for r in res["rows"] if r["name"] == "RSI"][0]
    assert rsi_row["holm"], f"the fixture must clear Holm or it tests nothing: {rsi_row}"
    assert rsi_row["terciles"]["favoured"]["mean_r"] <= 0, rsi_row["terciles"]
    assert rsi_row["verdict"] == "REAL BUT UNPROFITABLE", (
        f"a reading whose favoured bucket loses money is not a candidate, got "
        f"{rsi_row['verdict']}. The population runs at mean R -0.012; a clean "
        f"split of a zero-edge system is still zero edge")
    print("economic clause  : clears Holm, loses money -> REAL BUT UNPROFITABLE")

    # ── and the same shape, paid properly, IS a candidate ──
    rich = []
    for i in range(40):
        rich.append(mk("loss", -1.0, rsi=10.0 + i * 0.1))
    for i in range(40):
        rich.append(mk("win", 3.0, rsi=50.0 + i * 0.1) if i % 2 == 0
                    else mk("loss", -1.0, rsi=50.0 + i * 0.1))
    res = assess(rich, "PROBE")
    rsi_row = [r for r in res["rows"] if r["name"] == "RSI"][0]
    assert rsi_row["verdict"] == "TRADEABLE", rsi_row
    assert rsi_row["terciles"]["favoured"]["mean_r"] > 0
    print("tradeable path   : same split, paid at 3 R -> TRADEABLE")

    # ── THE FAVOURED BUCKET MUST BE THE BEST ONE ──
    # Run 1's shape exactly: `planned R:R` cleared Holm, showed a monotone
    # gradient, and its favoured bucket had a POSITIVE mean R of +0.044 while
    # the two buckets it would have you skip earned +0.153 and +0.117. Under
    # the original clause that was a candidate. Following it would have raised
    # the hit rate and cut the return.
    #
    # Built here as: the low bucket hits often for small change, the high
    # bucket hits rarely for real money.
    shaped = []
    # low bucket mean R = 0.6(+0.9) + 0.4(-1.0) = +0.14: profitable, and the
    # worst of the three. That is run 1's shape, where the favoured bucket paid
    # +0.044 against +0.153 and +0.117 in the ones it would have you skip.
    for i in range(90):                       # low: 60% hit at +0.9 R
        shaped.append(mk("win", 0.9, rsi=10.0 + i * 0.1) if i % 5 < 3
                      else mk("loss", -1.0, rsi=10.0 + i * 0.1))
    for i in range(90):                       # mid: 40% hit at +3 R
        shaped.append(mk("win", 3.0, rsi=40.0 + i * 0.1) if i % 5 < 2
                      else mk("loss", -1.0, rsi=40.0 + i * 0.1))
    for i in range(90):                       # high: 30% hit at +5 R
        shaped.append(mk("win", 5.0, rsi=70.0 + i * 0.1) if i % 10 < 3
                      else mk("loss", -1.0, rsi=70.0 + i * 0.1))
    res = assess(shaped, "SHAPED")
    row = [r for r in res["rows"] if r["name"] == "RSI"][0]
    fav = row["terciles"]["favoured"]
    assert row["holm"], f"the fixture must clear Holm: p={row['p']}"
    assert fav["mean_r"] > 0, (
        "the favoured bucket must be PROFITABLE, or the old clause would have "
        "rejected this and the new one would go untested")
    assert not row["terciles"]["fav_is_best"], row["terciles"]
    assert row["verdict"] == "NOT THE BEST BUCKET", (
        f"a bucket that pays {fav['mean_r']:+.3f} while the ones it would have "
        f"you skip pay {row['terciles']['best_mean_r']:+.3f} is not tradeable. "
        f"Got {row['verdict']}")
    print(f"best-bucket clause: favoured pays {fav['mean_r']:+.2f}, best pays "
          f"{row['terciles']['best_mean_r']:+.2f} -> NOT THE BEST BUCKET")

    # ── A PURE TAUTOLOGY MUST NOT READ AS INFORMATIVE ──
    # Every bucket hits at exactly 1/(1+R:R) — the driftless-walk rate. The raw
    # hit rates differ enormously (50% vs 25% vs 12.5%) and mean nothing: they
    # are the target distance restated. Excess is zero everywhere, so the
    # readout must say so rather than present the gradient as a finding.
    taut = []
    # Equal group sizes so the terciles land exactly on the three R:R groups;
    # unequal ones make buckets straddle groups and manufacture a small excess
    # out of nothing but the boundary.
    for rr_, hits, n_ in ((1.0, 30, 60), (3.0, 15, 60), (7.0, 8, 60)):
        for i in range(n_):
            won = i < hits
            taut.append(mk("win" if won else "loss",
                           rr_ if won else -1.0, rr=rr_,
                           rsi=10.0 * rr_ + i * 0.01))
    res = assess(taut, "TAUT")
    row = [r for r in res["rows"] if r["name"] == "RSI"][0]
    exc = [b["excess"] for b in row["terciles"]["buckets"]]
    rates = [b["rate"] for b in row["terciles"]["buckets"]]
    assert max(rates) - min(rates) > 0.3, (
        f"the fixture must have a big RAW spread or it proves nothing: {rates}")
    assert max(abs(e) for e in exc) < 0.02, (
        f"excess must be ~0 when the rate IS the baseline: {exc}")
    assert not row["informative"], (
        f"raw rates {[round(r,3) for r in rates]} differ by "
        f"{max(rates)-min(rates):.0%} and carry no information — every one of "
        f"them is 1/(1+R:R). Reporting that gradient is reporting the target "
        f"distance back to the user in disguise")
    print(f"tautology         : raw spread {max(rates)-min(rates):.0%}, excess "
          f"{max(abs(e) for e in exc):.1%} -> not informative")

    # ── AND A REAL EDGE ON TOP OF THE BASELINE DOES READ AS INFORMATIVE ──
    # Three groups, all at R:R 3.0, so the baseline is 25% everywhere and the
    # buckets align with the terciles. Only the hit rate varies, which is the
    # one thing target distance cannot explain.
    edge = []
    for base_rsi, hits in ((20.0, 8), (40.0, 18), (60.0, 33)):
        for i in range(60):
            won = i < hits
            edge.append(mk("win" if won else "loss", 3.0 if won else -1.0,
                           rr=3.0, rsi=base_rsi + i * 0.01))
    res = assess(edge, "EDGE")
    row = [r for r in res["rows"] if r["name"] == "RSI"][0]
    bks = row["terciles"]["buckets"]
    assert all(abs(b["base"] - 0.25) < 1e-9 for b in bks), \
        f"the fixture must hold R:R constant so the baseline cannot move: {bks}"
    assert row["informative"], (
        f"three buckets at the SAME R:R hitting "
        f"{[round(b['rate'],3) for b in bks]} cannot be explained by target "
        f"distance; that is information. p={row.get('info_p')}")
    _hits = "/".join(f"{b['rate']*100:.0f}%" for b in bks)
    print(f"real edge         : R:R held at 3.0, hit {_hits} -> INFORMATIVE "
          f"(p {row['info_p']:.1e})")

    # ── HOLM MUST ACTUALLY BIND ──
    # A reading whose raw p sits between alpha/7 (0.0071) and alpha (0.05) is
    # the ONLY thing that can tell Holm apart from a raw threshold. The null
    # fixture below cannot: with 600 random trades it usually produces no raw
    # p under 0.05 at all, so swapping Holm for `p < ALPHA` changed nothing and
    # the guard stayed green. This is the third module in this project where
    # exactly that gap appeared. Build the band deliberately.
    n_band = 60
    base = [50.0 + (i % 11) - 5 for i in range(n_band)]
    sd_b = float(np.std(base, ddof=1))
    want_t = 2.326                      # two-sided p ~ 0.020
    gap_b = want_t * sd_b * math.sqrt(2.0 / n_band)
    band = ([mk("loss", -1.0, rsi=v, adx=30.0, vol_ratio=1.2) for v in base] +
            [mk("win", 3.0, rsi=v + gap_b, adx=30.0, vol_ratio=1.2)
             for v in base])
    res = assess(band, "BAND")
    row = [r for r in res["rows"] if r["name"] == "RSI"][0]
    assert ALPHA / len(READINGS) < row["p"] < ALPHA, (
        f"the fixture must land between alpha/7 and alpha or it cannot tell "
        f"Holm from a raw threshold; p = {row['p']:.4f}")
    assert not row["holm"], (
        f"p = {row['p']:.4f} is under {ALPHA} but over {ALPHA}/{len(READINGS)} "
        f"= {ALPHA/len(READINGS):.4f}. Holm must reject it. Accepting it is "
        f"taking the best of seven at face value")
    assert row["verdict"] == "NOT ESTABLISHED", row["verdict"]
    print(f"holm binds       : p {row['p']:.4f} < {ALPHA} but rejected at "
          f"{ALPHA}/{len(READINGS)}")

    # ── AND THE DOSE-RESPONSE CLAUSE MUST BIND INSIDE assess() ──
    # terciles() is tested directly above, but that proves nothing about
    # whether assess() consults it. Both fixtures used there are monotone, so
    # deleting the clause from assess() changed no verdict and that guard was
    # green too. This reading clears Holm easily and its buckets run
    # 0% / 90% / 50% — a real shift of means with no gradient behind it.
    humped = ([mk("loss", -1.0, rsi=10.0 + i * 0.4) for i in range(20)] +
              [mk("win", 3.0, rsi=30.0 + i * 0.4) for i in range(18)] +
              [mk("loss", -1.0, rsi=38.0 + i * 0.4) for i in range(2)] +
              [mk("win", 3.0, rsi=50.0 + i * 0.4) for i in range(10)] +
              [mk("loss", -1.0, rsi=54.0 + i * 0.4) for i in range(10)])
    res = assess(humped, "HUMP")
    row = [r for r in res["rows"] if r["name"] == "RSI"][0]
    assert row["holm"], f"the fixture must clear Holm or it tests nothing: {row['p']}"
    assert not row["terciles"]["monotone"], row["terciles"]["rho"]
    assert row["terciles"]["favoured"]["mean_r"] > 0, (
        "the favoured bucket must be PROFITABLE, or the economic clause would "
        "reject this and the dose-response clause would go untested")
    assert row["verdict"] == "NO DOSE-RESPONSE", (
        f"a reading that clears Holm and pays, with buckets at "
        f"{[round(b['rate'],2) for b in row['terciles']['buckets']]}, must be "
        f"rejected for having no gradient. Got {row['verdict']}")
    _rates = "/".join(f"{b['rate']*100:.0f}%"
                      for b in row["terciles"]["buckets"])
    print(f"dose binds       : buckets {_rates} -> NO DOSE-RESPONSE")

    # ── mean_ci must return an INTERVAL, not a point ──
    # Every bucket line prints a 95% CI. Nothing asserted the interval had
    # width, so collapsing it to the point estimate left the suite green while
    # the readout claimed a precision it does not have.
    m, lo, hi = mean_ci([1.0, 2.0, 3.0, 4.0, 5.0] * 8)
    assert abs(m - 3.0) < 1e-9, m
    assert hi - lo > 0.1, (
        f"the CI collapsed to a point: [{lo}, {hi}]. A bucket estimate without "
        f"an interval invites over-reading — 30% on 40 trades and 30% on 400 "
        f"are different claims")
    _, lo40, hi40 = mean_ci([1.0, 2.0, 3.0, 4.0, 5.0] * 8)
    _, lo8, hi8 = mean_ci([1.0, 2.0, 3.0, 4.0, 5.0] * 2)
    assert (hi8 - lo8) > (hi40 - lo40) * 1.5, (
        "the interval must widen as n falls; it did not")
    assert not np.isfinite(mean_ci([1.0])[1]), "n=1 has no interval"
    print(f"bucket CI        : width {hi-lo:.2f} at n=40, {hi8-lo8:.2f} at n=10")

    # ── HOLM MUST BIND ON THE INFORMATIONAL LENS TOO ──
    # The edge fixture lands at p = 8.5e-08 and the tautology at p = 1.0, so
    # neither can tell Holm from a raw threshold. This is the SECOND lens in
    # this module with that hole and the fourth instance in the project.
    # Three buckets at constant R:R hitting 20% / 30% / 40%: top vs bottom
    # gives z = 2.45, p = 0.014 — under alpha, over alpha/7.
    band2 = []
    for base_rsi, hits in ((20.0, 12), (40.0, 18), (60.0, 24)):
        for i in range(60):
            won = i < hits
            band2.append(mk("win" if won else "loss", 3.0 if won else -1.0,
                            rr=3.0, rsi=base_rsi + i * 0.01))
    res = assess(band2, "BAND2")
    row = [r for r in res["rows"] if r["name"] == "RSI"][0]
    assert ALPHA / len(READINGS) < row["info_p"] < ALPHA, (
        f"the fixture must land between alpha/7 and alpha or it cannot tell "
        f"Holm from a raw threshold; info_p = {row['info_p']:.4f}")
    assert not row["informative"], (
        f"info_p = {row['info_p']:.4f} is under {ALPHA} but over "
        f"{ALPHA}/{len(READINGS)}. Holm must reject it — this is the best of "
        f"seven readings, not one hypothesis")
    print(f"info holm binds  : info_p {row['info_p']:.4f} < {ALPHA} but "
          f"rejected at {ALPHA}/{len(READINGS)}")

    # ── a null population yields nothing ──
    import random
    rng = random.Random(7)
    null = [mk("win" if rng.random() < 0.3 else "loss",
               3.0 if rng.random() < 0.3 else -1.0,
               rsi=rng.uniform(30, 70), adx=rng.uniform(25, 50),
               vol_ratio=rng.uniform(0.7, 2.0)) for _ in range(600)]
    res = assess(null, "NULL")
    assert all(r["verdict"] != "TRADEABLE" for r in res["rows"]), (
        f"noise produced a tradeable reading: "
        f"{[(r['name'], r['verdict'], r['p']) for r in res['rows']]}")
    assert not any(r.get("informative") for r in res["rows"]), (
        f"noise read as informative: "
        f"{[(r['name'], r.get('info_p')) for r in res['rows'] if r.get('informative')]}")
    print("null population  : 600 random trades, nothing tradeable or informative")

    # ── WIRING: the readings must arrive from a real signal ──
    # Checking that setup_cases produces these names would test a string. Run
    # the production path and read what comes back.
    import backtest as bt
    df = bt._synthetic_ohlc(up=True, adx=40.0)
    sig = bt.evaluate_signal(df, len(df) - 1, bt.build_signal_params(bt.DEFAULTS))
    assert sig is not None, "the synthetic bar no longer signals; guard is blind"
    probe = {"ticker": "BT", "r": 1.0, "outcome": "win",
             "entry": sig["entry"], "stop": sig["stop"], "setup": sig}
    for name, fn in READINGS:
        v = fn(probe)
        assert np.isfinite(v) and v != 0, (
            f"reading {name!r} is {v} on a live signal — the value did not "
            f"survive the handoff and would score as a silent zero")
    print("wiring           : all 7 readings real on a live signal")

    print("=" * 72)
    print("All self-tests passed.")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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
    if not trades:
        print("NOTHING TO SCORE — no trades carried a setup.")
        return 2

    sp = sides(trades)
    n_to = sum(1 for t in trades if t.get("outcome") == "timeout")
    print("=" * 84)
    print("SETUP READINGS ACROSS THE POPULATION — target vs stop, every trade")
    print("=" * 84)
    print(f"  {len(trades)} trades, {len(tickers)} tickers, {a.years} years")
    print(f"  {len(sp['LONGS'])} long, {len(sp['SHORTS'])} short, "
          f"{n_to} timeouts excluded")
    print(f"  overall mean R {float(np.mean([t['r'] for t in trades])):+.4f}")
    print(f"  pre-registered: results/setup_population_preregistration.md")

    out = [assess(sp[k], k) for k in ("LONGS", "SHORTS") if sp[k]]
    for res in out:
        report(res)
    verdict(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
