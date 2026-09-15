#!/usr/bin/env python3
"""
drift_null.py — is the entry signal's edge just market drift?

THE OBSERVATION

setup_population measured every trade against the DRIFTLESS-WALK baseline: for
a walk with no drift, P(touch +k risk before -1 risk) is 1/(1+k), so a trade's
target distance alone predicts its hit rate. Longs beat that baseline by
+4.2pp (z +3.32) and shorts fell short of it by -9.5pp (z -6.57).

TWO EXPLANATIONS

  SIGNAL  the entry logic picks bars from which price is more likely to run.
  DRIFT   1/(1+k) assumes zero drift. Twenty mega-cap US equities over a
          decade-long bull market drift up, so a long entered at ANY bar beats
          a driftless baseline and a short falls short of it.

Drift explains both rows, opposite signs included, with one parameter. Nothing
measured so far separates them.

THE TEST

Enter at RANDOM bars on the same frames, with the same stop, target, exit
engine and costs, and measure the same excess. If random entries show the same
excess, the entry logic contributes nothing. If they show less, the gap is what
the signal is worth, in percentage points.

Pre-registered in results/drift_null_preregistration.md, committed before this
file existed.

WHAT IS HELD IDENTICAL

The price frames (both arms read the same compute() output per ticker), the
exit engine (backtest.simulate_trade: stop-first on ambiguous bars, next-open
fill, gapped-fill rejection, max_hold mark-to-market), slippage, commission,
max_hold, the spacing between trades, the trade count per ticker per side, and
the side itself.

WHAT IS NOT, STATED RATHER THAN BURIED

The null's R:R is always 3.00; the real signal's structural-stop validation
widens some stops and gives a spread. The excess metric subtracts each trade's
own 1/(1+R:R), which is the correction for exactly that — but a correction is
not an identical sample, so the run also reports the comparison restricted to
real trades with R:R within 0.05 of 3.00, where the geometry matches and no
correction is doing any work.
"""
from __future__ import annotations

import argparse
import math
import random
import sys

import numpy as np

import backtest as bt
import setup_cases as scs

DRAWS = 200
RR_MATCH_TOL = 0.05


def excess(trades: list[dict]) -> dict:
    """Target-hit rate against what each trade's own target distance implies."""
    ts = [t for t in trades if t.get("outcome") in ("win", "loss")]
    if not ts:
        return {"n": 0, "rate": float("nan"), "base": float("nan"),
                "excess": float("nan"), "mean_r": float("nan")}
    hits = sum(1 for t in ts if t["outcome"] == "win")
    rr = [float((t.get("setup") or {}).get("rr", 0) or 0) for t in ts]
    base = float(np.mean([1.0 / (1.0 + k) for k in rr if k > 0]))
    # R ON THE PLANNED STOP, alongside R on the fill.
    #
    # R is measured against |fill - stop|, and the fill is the next bar's open.
    # An arm that enters where price has been falling gets opens that gap
    # further down, landing near the stop, shrinking the denominator and
    # inflating R with no change in what price did. setup_cases run 2 was
    # voided by exactly this: 37 of 37 winners had filled toward the stop, and
    # re-basing on planned risk collapsed the spread 7.3x.
    #
    # So every mean R is reported twice. When the two disagree, the difference
    # was decided after the signal bar by something no entry rule can see.
    planned = [scs.fill_stats(t)["r_planned"] for t in ts]
    planned = [x for x in planned if np.isfinite(x)]
    offs = [scs.fill_stats(t)["fill_offset"] for t in ts]
    offs = [x for x in offs if np.isfinite(x)]
    return {"n": len(ts), "rate": hits / len(ts), "base": base,
            "excess": hits / len(ts) - base,
            "mean_r": float(np.mean([t["r"] for t in ts])),
            "mean_r_planned": float(np.mean(planned)) if planned else float("nan"),
            "fill_offset": float(np.median(offs)) if offs else float("nan")}


def random_entries(df, n_trades: int, trend: str, cfg: dict,
                   rng: random.Random) -> list[dict]:
    """
    n_trades entries at uniformly random bars, same geometry, same exit engine.

    UNIFORM IS THE POINT. This is the null of "the timing carries no
    information", so the bars must be chosen without reference to anything on
    them. Drawn without replacement and respecting the same minimum spacing the
    real run uses between trades, because overlapping trades on one ticker are
    not independent and the real arm is not allowed them either.
    """
    n = len(df)
    lo, hi = 1, n - 2                      # need a next bar to fill on
    if hi <= lo or n_trades <= 0:
        return []
    gap = cfg["cooldown_bars"] + 1
    pool = list(range(lo, hi))
    rng.shuffle(pool)
    chosen: list[int] = []
    for i in pool:
        if len(chosen) >= n_trades:
            break
        if all(abs(i - j) >= gap for j in chosen):
            chosen.append(i)
    out = []
    for i in sorted(chosen):
        atr = float(df["ATR"].iloc[i]) if "ATR" in df.columns else 0.0
        px = float(df["Close"].iloc[i])
        if atr <= 0 or px <= 0:
            continue
        if trend == "Bullish":
            stop = px - cfg["atr_stop_mult"] * atr
            target = px + cfg["atr_tgt_mult"] * atr
        else:
            stop = px + cfg["atr_stop_mult"] * atr
            target = px - cfg["atr_tgt_mult"] * atr
        rr = abs(target - px) / abs(px - stop)
        # signal_i rides along so the spacing rule can be checked at the sink
        # rather than trusted at the source.
        trade = {"trend": trend, "entry": px, "stop": stop, "target": target,
                 "rr": round(rr, 2), "atr": atr, "price": px, "signal_i": i}
        res = bt.simulate_trade(df, i, trade, cfg)
        if res.get("filled"):
            out.append(res)
    return out


def compare(real: list[dict], null_draws: list[dict], label: str) -> dict:
    """The real arm against the null distribution, on excess and on mean R."""
    r = excess(real)
    out = {"label": label, "real": r, "n_draws": len(null_draws)}
    for key in ("excess", "mean_r", "mean_r_planned"):
        # .get with a nan default: a caller may hand in draws that predate a
        # metric, and a KeyError here would take down a comparison whose other
        # two metrics are perfectly good.
        vals = sorted(d.get(key, float("nan")) for d in null_draws
                      if np.isfinite(d.get(key, float("nan"))))
        if len(vals) < 20 or not np.isfinite(r.get(key, float("nan"))):
            out[key] = {"verdict": "NOT ENOUGH DRAWS", "lo": float("nan"),
                        "hi": float("nan"), "mid": float("nan"), "p": 1.0}
            continue
        lo = vals[int(0.025 * len(vals))]
        hi = vals[min(len(vals) - 1, int(0.975 * len(vals)))]
        mid = float(np.median(vals))
        # Empirical p: how often the null reached the real value. No
        # distributional assumption — the null IS the distribution.
        at_or_above = sum(1 for v in vals if v >= r[key])
        p = (at_or_above + 1) / (len(vals) + 1)
        if r[key] > hi:
            v = "SIGNAL CONTRIBUTES"
        elif r[key] < lo:
            v = "SIGNAL IS NEGATIVE"
        else:
            v = "SIGNAL ADDS NOTHING"
        out[key] = {"verdict": v, "lo": lo, "hi": hi, "mid": mid, "p": p,
                    "gap": r[key] - mid}
    return out


def report(c: dict) -> None:
    r = c["real"]
    print(f"\n{'='*84}")
    print(f"{c['label']}   real: {r['n']} trades, {r['rate']*100:.1f}% hit, "
          f"baseline {r['base']*100:.1f}%, excess {r['excess']*100:+.1f}pp, "
          f"mean R {r['mean_r']:+.4f}")
    print(f"  {c['n_draws']} random draws, same frames, same exits, same costs")
    print("=" * 84)
    if np.isfinite(r.get("fill_offset", float("nan"))):
        print(f"  median fill offset {r['fill_offset']:+.3f} "
              f"(negative = the open landed toward the stop, shrinking risk)")
    for key, unit in (("excess", "pp"), ("mean_r", "R"), ("mean_r_planned", "R")):
        k = c.get(key)
        if k is None or not np.isfinite(r.get(key, float("nan"))):
            continue
        scale = 100.0 if key == "excess" else 1.0
        fmt = "+.1f" if key == "excess" else "+.4f"
        name = "R planned" if key == "mean_r_planned" else key
        print(f"  {name:<10} real {format(r[key]*scale, fmt)}{unit}"
              f"   null median {format(k['mid']*scale, fmt)}{unit}"
              f"   null 95% [{format(k['lo']*scale, fmt)}, "
              f"{format(k['hi']*scale, fmt)}]{unit}")
        print(f"  {'':<10} gap {format(k['gap']*scale, fmt)}{unit}"
              f"   empirical p {k['p']:.3f}   -> {k['verdict']}")
    # A disagreement between the two R bases is the finding, not a nuisance.
    a, b = c.get("mean_r", {}).get("verdict"), c.get("mean_r_planned", {}).get("verdict")
    if a and b and a != b:
        print(f"  !! R ON THE FILL SAYS {a}, R ON THE PLANNED STOP SAYS {b}.")
        print(f"     The difference was decided by the next bar's open, which "
              f"no entry rule can see.")


def run(tickers: list[str], years: int, draws: int, seed: int) -> int:
    cfg = dict(bt.DEFAULTS, tickers=tickers, years=years)
    params = bt.build_signal_params(cfg)
    rng = random.Random(seed)

    real: list[dict] = []
    # One list of null trades per draw, accumulated across tickers so each draw
    # is a complete alternative history rather than a per-ticker shuffle.
    null: dict[str, list[list[dict]]] = {"Bullish": [[] for _ in range(draws)],
                                         "Bearish": [[] for _ in range(draws)]}
    print("  collecting ...", file=sys.stderr)
    for tk in tickers:
        raw = bt.download(tk, years)
        if raw is None:
            print(f"  ! {tk}: no data", file=sys.stderr)
            continue
        df = bt.compute(raw).copy()
        # BOTH ARMS READ THIS FRAME. Re-downloading for the null would let a
        # cache refresh or a vendor revision land between the two and show up
        # as a difference in the result.
        rt = bt.backtest_ticker(df, cfg, params)
        real.extend(rt)
        for trend in ("Bullish", "Bearish"):
            k = sum(1 for t in rt
                    if (t.get("setup") or {}).get("trend") == trend)
            if not k:
                continue
            for d in range(draws):
                null[trend][d].extend(random_entries(df, k, trend, cfg, rng))
        print(f"  {tk}: {len(rt)} real", file=sys.stderr)

    print("=" * 84)
    print("DRIFT NULL — is the entry signal's edge just market drift?")
    print("=" * 84)
    print(f"  {len(tickers)} tickers, {years} years, {draws} random draws")
    print(f"  pre-registered: results/drift_null_preregistration.md")

    out = []
    for trend, label in (("Bullish", "LONGS"), ("Bearish", "SHORTS")):
        rt = [t for t in real if (t.get("setup") or {}).get("trend") == trend]
        if not rt:
            continue
        nd = [excess(x) for x in null[trend] if x]
        c = compare(rt, nd, label)
        report(c)
        out.append(c)

        # ── ROBUSTNESS: the same comparison where the geometry matches ──
        # The null's R:R is always 3.00. Restricting the real arm to trades
        # whose structural stop did not widen puts both arms on identical
        # geometry, so the 1/(1+R:R) correction is doing no work.
        matched = [t for t in rt
                   if abs(float((t.get("setup") or {}).get("rr", 0)) - 3.0)
                   <= RR_MATCH_TOL]
        if len(matched) >= 100:
            cm = compare(matched, nd, f"{label} (R:R-matched subset)")
            report(cm)
            main, rob = c["excess"]["verdict"], cm["excess"]["verdict"]
            if main != rob:
                print(f"\n  !! THE SUBSET DISAGREES: full sample says {main}, "
                      f"R:R-matched says {rob}.")
                print(f"     Reported as a disagreement. Neither is the "
                      f"answer, and picking the more flattering one is how a "
                      f"robustness check becomes a second shot at the result.")
            else:
                print(f"\n  robustness: {len(matched)} R:R-matched trades agree "
                      f"({rob})")

    print(f"\n{'='*84}")
    print("SUMMARY")
    for c in out:
        print(f"  {c['label']:<8} excess {c['excess']['verdict']:<20}"
              f"  mean R {c['mean_r']['verdict']}")
    print("\nIN-SAMPLE. Twenty spent tickers. A positive result here is a")
    print("hypothesis for tranche C, not a finding.")
    print("=" * 84)
    return 0


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------

def selftest() -> int:
    print("drift_null.py selftest")
    print("=" * 72)

    def tr(outcome, r, rr=3.0, trend="Bullish"):
        return {"r": r, "outcome": outcome,
                "setup": {"rr": rr, "trend": trend}}

    # ── excess is measured against 1/(1+R:R), not against zero ──
    # 25% of R:R-3.0 trades hitting target is EXACTLY the driftless rate. A
    # module that called that a 25% edge would find one in pure noise.
    ts = [tr("win", 3.0)] * 25 + [tr("loss", -1.0)] * 75
    e = excess(ts)
    assert abs(e["base"] - 0.25) < 1e-9, e
    assert abs(e["excess"]) < 1e-9, (
        f"a 25% hit rate at R:R 3.0 IS the driftless baseline; excess must be "
        f"zero, got {e['excess']:+.4f}")
    ts2 = [tr("win", 3.0)] * 35 + [tr("loss", -1.0)] * 65
    assert abs(excess(ts2)["excess"] - 0.10) < 1e-9, excess(ts2)
    # and the baseline MOVES with R:R, or near targets read as skill
    near = [tr("win", 1.0, rr=1.0)] * 50 + [tr("loss", -1.0, rr=1.0)] * 50
    assert abs(excess(near)["base"] - 0.5) < 1e-9, excess(near)
    assert abs(excess(near)["excess"]) < 1e-9, (
        "50% at R:R 1.0 is also the baseline — the target is half as far")
    # a TIMEOUT reached neither level; counting it either way invents an
    # outcome. With 25 wins / 75 losses at baseline, adding 100 timeouts must
    # not move the excess at all.
    withto = ts + [tr("timeout", 0.4) for _ in range(100)]
    assert excess(withto)["n"] == 100, excess(withto)["n"]
    assert abs(excess(withto)["excess"]) < 1e-9, (
        f"100 timeouts moved the excess to {excess(withto)['excess']:+.4f}; "
        f"a trade that reached neither level is not evidence about either")
    print("excess           : 25%@3.0 and 50%@1.0 read as zero; timeouts excluded")

    # ── THE TWO R BASES MUST DIVERGE WHEN THE FILL MOVES ──
    # Planned risk 100.5 - 98.0 = 2.5. A fill at 99.25 leaves 1.25, half of it,
    # so +6 R on the fill is +3 R on the stop the signal actually planned. If
    # these two never differ, the whole point of reporting both is lost and an
    # inflated-denominator result reads as a discovery.
    def tr2(outcome, r, fill):
        return {"r": r, "outcome": outcome,
                "entry": fill, "stop": 98.0,
                "setup": {"rr": 3.0, "trend": "Bullish",
                          "entry": 100.5, "stop": 98.0}}
    lucky = [tr2("win", 6.0, 99.25)] * 25 + [tr2("loss", -1.0, 99.25)] * 75
    e = excess(lucky)
    assert abs(e["mean_r"] - (6.0 * 0.25 - 0.75)) < 1e-9, e["mean_r"]
    assert abs(e["fill_offset"] - (-0.5)) < 1e-9, e["fill_offset"]
    assert abs(e["mean_r_planned"] - (3.0 * 0.25 - 0.5 * 0.75)) < 1e-9, (
        f"R on the planned stop must halve with a half-sized denominator: "
        f"{e['mean_r_planned']}")
    assert e["mean_r"] > e["mean_r_planned"], (
        "a fill that landed toward the stop must read LOWER on the planned "
        "basis; if the two agree here the metric is not measuring the fill")
    clean = [tr2("win", 3.0, 100.5)] * 25 + [tr2("loss", -1.0, 100.5)] * 75
    ec = excess(clean)
    assert abs(ec["fill_offset"]) < 1e-9, ec["fill_offset"]
    assert abs(ec["mean_r"] - ec["mean_r_planned"]) < 1e-9, (
        f"with the fill ON the planned entry the two bases must agree "
        f"exactly: {ec['mean_r']} vs {ec['mean_r_planned']}")
    print(f"two R bases      : fill offset {e['fill_offset']:+.2f} splits "
          f"{e['mean_r']:+.2f} R on the fill from "
          f"{e['mean_r_planned']:+.2f} R on the plan; equal when the fill is clean")

    # ── the verdicts, against a hand-built null distribution ──
    nd = [{"excess": 0.04 + 0.01 * math.sin(i), "mean_r": 0.1}
          for i in range(200)]
    # 29% hit at R:R 3.0 is excess +0.04 — the null's own median.
    inside = compare([tr("win", 3.0)] * 29 + [tr("loss", -1.0)] * 71, nd, "P")
    assert inside["excess"]["verdict"] == "SIGNAL ADDS NOTHING", inside["excess"]
    above = compare([tr("win", 3.0)] * 60 + [tr("loss", -1.0)] * 40, nd, "P")
    assert above["excess"]["verdict"] == "SIGNAL CONTRIBUTES", above["excess"]
    below = compare([tr("win", 3.0)] * 20 + [tr("loss", -1.0)] * 80, nd, "P")
    assert below["excess"]["verdict"] == "SIGNAL IS NEGATIVE", below["excess"]
    assert above["excess"]["p"] < 0.01 and below["excess"]["p"] > 0.99
    # ABOVE THE MEDIAN IS NOT ABOVE THE INTERVAL. The fixture above sits ON the
    # null median, so a break that scored `real > median` as CONTRIBUTES left
    # it green. 29.5% at R:R 3.0 is excess +0.045: past the median, inside the
    # 95% band, and still nothing.
    mid_hi = compare([tr("win", 3.0)] * 59 + [tr("loss", -1.0)] * 141, nd, "P")
    assert mid_hi["excess"]["mid"] < mid_hi["real"]["excess"] < mid_hi["excess"]["hi"], \
        f"the fixture must land between the median and the 97.5th: {mid_hi['excess']}"
    assert mid_hi["excess"]["verdict"] == "SIGNAL ADDS NOTHING", (
        f"excess {mid_hi['real']['excess']:+.4f} is above the null median "
        f"{mid_hi['excess']['mid']:+.4f} but inside its 95% band "
        f"[{mid_hi['excess']['lo']:+.4f}, {mid_hi['excess']['hi']:+.4f}]. "
        f"Beating the median is what half of pure noise does")
    print(f"verdicts         : inside / above / below resolve; above the "
          f"median but inside the band is still nothing")

    # ── compare() MUST CARRY THE PLANNED-R VERDICT, AND THE DISAGREEMENT ──
    # Dropping "mean_r_planned" from compare()'s loop left every other guard
    # green: excess() still computed it, nothing read it back. This is run 1's
    # INVERTED-Bull shape — R on the fill clears the null, R on the planned
    # stop does not, and the gap is the next bar's open.
    nd2 = [{"excess": 0.04 + 0.001 * i,
            "mean_r": 0.20 + 0.002 * (i % 25),
            "mean_r_planned": 0.34 + 0.004 * (i % 25)} for i in range(200)]
    split = compare(lucky, nd2, "SPLIT")
    assert "mean_r_planned" in split, (
        "compare() dropped the planned-R metric; an inflated-denominator "
        "result would read as a discovery with nothing to contradict it")
    assert split["mean_r"]["verdict"] == "SIGNAL CONTRIBUTES", split["mean_r"]
    assert split["mean_r_planned"]["verdict"] == "SIGNAL ADDS NOTHING", (
        f"R on the planned stop must fall inside this null: "
        f"{split['mean_r_planned']}")
    assert split["mean_r"]["verdict"] != split["mean_r_planned"]["verdict"], (
        "the fixture must make the two bases DISAGREE or it proves nothing")
    print(f"planned-R verdict: fill says {split['mean_r']['verdict']}, plan "
          f"says {split['mean_r_planned']['verdict']} — reported, not merged")

    # ── the empirical p can never be zero ──
    # (k+1)/(n+1), not k/n: 200 draws cannot establish p < 1/201, and printing
    # 0.000 would claim certainty the sample size does not have.
    assert above["excess"]["p"] >= 1.0 / (len(nd) + 1) - 1e-12, above["excess"]["p"]
    print(f"empirical p      : floor {1/(len(nd)+1):.4f} at {len(nd)} draws, "
          f"never 0")

    # ── too few draws must refuse, not guess ──
    thin = compare([tr("win", 3.0)] * 29 + [tr("loss", -1.0)] * 71,
                   nd[:5], "P")
    assert thin["excess"]["verdict"] == "NOT ENOUGH DRAWS", thin["excess"]
    print("thin null        : refuses rather than reporting a percentile of 5")

    # ── RANDOM ENTRIES: uniform, spaced, and on the real exit engine ──
    df = bt._synthetic_ohlc(up=True, adx=40.0)
    cfg = dict(bt.DEFAULTS)
    rng = random.Random(1)
    got = random_entries(df, 8, "Bullish", cfg, rng)
    assert len(got) > 0, "no random entry filled on a clean synthetic frame"
    assert all(g.get("filled") for g in got)
    assert all((g.get("setup") or {}).get("trend") == "Bullish" for g in got)
    # geometry: stop 1 ATR below, target 3 ATR above -> R:R 3.00
    for g in got:
        assert abs(float(g["setup"]["rr"]) - 3.0) < 0.01, g["setup"]["rr"]
    # IT MUST BE THE REAL EXIT ENGINE. Asserting on `filled` and the levels
    # proves only that something returned a dict with those keys — a stub that
    # booked every trade a winner passed all of it. These are fields only
    # simulate_trade() produces, and an outcome it can only reach by walking
    # the bars.
    for k in ("hold", "exit_date", "entry", "stop", "r", "outcome"):
        assert k in got[0], f"simulate_trade() produces {k!r}; this did not"
    assert all(g["outcome"] in ("win", "loss", "timeout") for g in got), got[0]
    assert all(isinstance(g["hold"], int) and g["hold"] >= 0 for g in got)

    # R MUST BE CONSISTENT WITH THE LEVELS, which a stub cannot fake: a stop
    # exit books about -1 R and nothing else. The smooth up-frame above resolves
    # every long as a timeout, so that loop ran zero times and proved nothing —
    # go long into a DOWN frame, where stops actually get hit.
    down = bt._synthetic_ohlc(up=False, adx=40.0)
    hit = random_entries(down, 12, "Bullish", cfg, random.Random(5))
    losses = [g for g in hit if g["outcome"] == "loss"]
    assert losses, (
        "no long into a downtrend hit its stop — the -1 R check below would "
        "run zero times and pass vacuously")
    for g in losses:
        assert -1.10 < g["r"] < -0.90, (
            f"a stop exit must book about -1 R, got {g['r']:+.2f}")
    wins = [g for g in hit if g["outcome"] == "win"]
    for g in wins:
        assert 2.7 < g["r"] < 3.3, (
            f"a target exit at R:R 3.0 must book about +3 R, got {g['r']:+.2f}")
    print(f"random entries   : {len(got)} filled, R:R 3.00; {len(losses)} stop "
          f"exits all at -1 R on a down frame")

    # spacing must match the real run's cooldown, or the null gets overlapping
    # trades the real arm is not allowed
    many = random_entries(df, 40, "Bullish", dict(cfg, cooldown_bars=9), rng)
    assert len(many) >= 5, f"too few to test spacing: {len(many)}"
    idx = sorted(g["setup"]["signal_i"] for g in many)
    gaps = [b - a for a, b in zip(idx, idx[1:])]
    assert min(gaps) >= 10, (
        f"two random entries sat {min(gaps)} bars apart at cooldown 9. The "
        f"real arm is not allowed overlapping trades on one ticker and the "
        f"null must not be either — correlated trades shrink its spread and "
        f"make every real result look significant")
    tight = random_entries(df, 40, "Bullish", dict(cfg, cooldown_bars=0), rng)
    tidx = sorted(g["setup"]["signal_i"] for g in tight)
    assert min(b - a for a, b in zip(tidx, tidx[1:])) >= 1
    assert len(tight) > len(many), (
        "a smaller cooldown must admit more entries, or the spacing rule is "
        "not reading the config at all")
    print(f"spacing          : min gap {min(gaps)} bars at cooldown 9, "
          f"{len(tight)} vs {len(many)} trades as cooldown falls")

    # ── DIFFERENT SEEDS MUST GIVE DIFFERENT DRAWS ──
    # A null whose draws are identical has no distribution, and every real
    # value then lands outside a zero-width interval as "SIGNAL CONTRIBUTES".
    a = random_entries(df, 6, "Bullish", cfg, random.Random(11))
    b = random_entries(df, 6, "Bullish", cfg, random.Random(12))
    assert [x["entry_date"] for x in a] != [x["entry_date"] for x in b], (
        "two seeds produced identical entries — the null has no spread and "
        "every real result would read as significant")
    print("seeds            : different draws, so the null has a distribution")

    # ── shorts are nulled against SHORT randoms, never against longs ──
    sh = random_entries(df, 6, "Bearish", cfg, random.Random(3))
    # WITHOUT THIS LENGTH CHECK the loop below runs zero times and `all()` over
    # an empty list is True, so inverting the short levels left the guard green.
    assert len(sh) > 0, (
        "no short random entry filled — the loop below would test nothing and "
        "pass vacuously")
    assert all((g.get("setup") or {}).get("trend") == "Bearish" for g in sh), sh
    for g in sh:
        assert g["setup"]["stop"] > g["setup"]["entry"] > g["setup"]["target"], (
            f"a short's stop sits ABOVE its entry and its target BELOW: {g['setup']}")
    print(f"sides            : {len(sh)} short nulls, levels inverted")

    print("=" * 72)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default=scs.DEFAULT_TICKERS)
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--draws", type=int, default=DRAWS)
    ap.add_argument("--seed", type=int, default=20260915)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    return run(tickers, a.years, a.draws, a.seed)


if __name__ == "__main__":
    sys.exit(main())
