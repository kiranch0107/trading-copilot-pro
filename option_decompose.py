"""
Where does the option loss actually come from — time, direction, or the spread?

option_backtest.py measures OPT_WIN_RATE = 24.9% at TP +100% / SL -50%, against
a breakeven of 33.3%. That is -12.6% of premium per trade. It does not say WHY,
and the remedies are opposite: if the signal is directionally right and carry is
eating it, the structure is wrong; if the signal is directionally wrong, no
structure saves it and an entry filter is the only lever left.

THE DECOMPOSITION IS EXACT, NOT ATTRIBUTED

Premium is f(spot, time_left, iv) with iv fixed for the life of the trade (see
the model note below). Going from (S0, t0) to (Se, t1) in two steps:

    carry     = f(S0, t1, iv) - f(S0, t0, iv)      time, at the entry spot
    direction = f(Se, t1, iv) - f(S0, t1, iv)      spot, at the exit's time left

Those two compose exactly — the intermediate term cancels — so

    carry + direction + spread = total

with NO residual and no cross-term to argue about. selftest() asserts the
reconciliation to a tenth of a basis point on every fixture, which is the whole
guard: an attribution that does not add up is a story, not a measurement.

Order matters and is declared: carry is taken at the ENTRY spot and direction at
the EXIT's remaining time. Taking them the other way round moves the cross-term
from one bucket to the other. Neither is more correct; the choice is fixed here
so it cannot be chosen after seeing the answer.

WHAT THIS CANNOT SEE — the model has no volatility path

option_backtest computes iv ONCE at entry and reprices every forward bar at that
same iv. You therefore always sell your volatility back at exactly the price you
bought it. Measured on an ATM 30-DTE call held 10 days, the flat-spot decay is
-19.5% / -19.4% / -19.2% at iv_mult 1.00 / 1.15 / 1.30 — the multiplier only
DE-LEVERS the percentage swings, it never charges for overpaying.

So "carry" here is theta alone. A real long option also pays or collects the
change in implied vol over the holding period, and that term is absent from
OPT_WIN_RATE entirely. Its sign is unmeasured, so this module does not guess at
it; it reports what the model contains and names what it does not.

NO BAR, NO PRE-REGISTRATION

This is accounting on trades that already exist, not a hypothesis test. There is
no effect to detect, no threshold to clear, and nothing to be fooled by beyond
arithmetic — which the reconciliation guard covers. A power check would be
theatre here and is deliberately not performed.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import sys

import numpy as np

import backtest as bt
import option_backtest as ob
import risk_params as rp

REQUIRED = ("spot0", "spot_exit", "strike", "iv", "right", "dte0",
            "held", "entry_prem", "exit_prem", "pnl_pct")


def decompose(t: dict, spread_pct: float) -> dict | None:
    """One trade's P&L split into carry, direction and spread. Percent of premium."""
    if any(t.get(k) is None for k in REQUIRED):
        return None
    S0, Se = float(t["spot0"]), float(t["spot_exit"])
    K, iv, right = float(t["strike"]), float(t["iv"]), t["right"]
    t0 = float(t["dte0"]) / ob.TRADING_DAYS
    t1 = max(float(t["dte0"]) - float(t["held"]), 0.0) / ob.TRADING_DAYS

    mid0 = ob.bs_price(S0, K, t0, iv, right)          # pre-spread entry mid
    if mid0 <= 0:
        return None
    frozen = ob.bs_price(S0, K, t1, iv, right)        # spot never moved
    actual = ob.bs_price(Se, K, t1, iv, right)        # where it really went

    entry_fill = float(t["entry_prem"])               # mid + half the spread
    carry_pct = (frozen - mid0) / entry_fill * 100.0
    dirn_pct = (actual - frozen) / entry_fill * 100.0

    # THE NULL, because direction > 0 is NOT evidence the signal was right.
    # A long option is convex: the same 3% move is +62% if it goes your way and
    # only -42% if it does not. A coin-flip signal therefore earns POSITIVE mean
    # direction from convexity alone. The informative quantity is direction
    # against what the OPPOSITE side would have earned on the identical path —
    # same spot, same days held, same iv, call swapped for put. Convexity is
    # present in both and cancels; only directional edge survives the difference.
    #
    # BOTH SIDES ARE DIVIDED BY THE SAME DENOMINATOR — the premium actually
    # paid. Dividing each by its own premium leaves a bias, because at the money
    # a call and a put are not worth the same: put-call parity gives
    # C - P = S - K*exp(-rT). Measured, that bias was -2.41% on symmetric moves,
    # and the guard below is what found it.
    #
    # With one denominator the cancellation is exact. Parity at fixed t1 gives
    # dC(x) - dP(x) = S0*x, so
    #
    #     edge = (signed spot move) / premium paid
    #
    # which is zero on symmetric moves by construction, and is exactly the right
    # quantity: the underlying's move, levered by what the option cost.
    other = "PUT" if right == "CALL" else "CALL"
    m_frozen = ob.bs_price(S0, K, t1, iv, other)
    m_actual = ob.bs_price(Se, K, t1, iv, other)
    mirror_pct = (m_actual - m_frozen) / entry_fill * 100.0
    # Both crossings, expressed against the same denominator as the P&L.
    spread_pct_cost = ((mid0 - entry_fill) + (actual * (1 - spread_pct / 200) - actual)) \
        / entry_fill * 100.0
    return {**t, "carry_pct": carry_pct, "direction_pct": dirn_pct,
            "spread_cost_pct": spread_pct_cost, "mirror_pct": mirror_pct,
            "edge_pct": dirn_pct - mirror_pct,
            "recon_pct": carry_pct + dirn_pct + spread_pct_cost}


def summarise(rows: list[dict]) -> dict:
    p = np.array([r["pnl_pct"] for r in rows], dtype=float)
    c = np.array([r["carry_pct"] for r in rows], dtype=float)
    d = np.array([r["direction_pct"] for r in rows], dtype=float)
    s = np.array([r["spread_cost_pct"] for r in rows], dtype=float)
    m = np.array([r["mirror_pct"] for r in rows], dtype=float)
    e = np.array([r["edge_pct"] for r in rows], dtype=float)
    ok = np.isfinite(e)
    return {"n": len(p), "total": float(p.mean()), "carry": float(c.mean()),
            "direction": float(d.mean()), "spread": float(s.mean()),
            "mirror": float(m[ok].mean()) if ok.any() else float("nan"),
            "edge": float(e[ok].mean()) if ok.any() else float("nan"),
            "edge_sd": float(e[ok].std(ddof=1)) if ok.sum() > 1 else float("nan"),
            "edge_n": int(ok.sum()),
            "dir_positive_pct": float((d > 0).mean() * 100.0),
            "dir_median": float(np.median(d))}


def report(rows: list[dict]) -> int:
    agg = summarise(rows)
    be = rp.OPT_WIN_RATE_SL_PCT / (rp.OPT_WIN_RATE_TP_PCT + rp.OPT_WIN_RATE_SL_PCT)
    print("=" * 78)
    print("OPTION P&L DECOMPOSITION — time, direction, or the spread?")
    print("=" * 78)
    print(f"  {agg['n']} trades   TP +{rp.OPT_WIN_RATE_TP_PCT:.0f}% / "
          f"SL -{rp.OPT_WIN_RATE_SL_PCT:.0f}%   breakeven win rate {be:.1%}")
    print("=" * 78)
    print(f"  {'component':<26}{'mean % of premium':>20}")
    print("  " + "-" * 46)
    print(f"  {'carry (theta)':<26}{agg['carry']:>+19.2f}%")
    print(f"  {'direction (spot move)':<26}{agg['direction']:>+19.2f}%")
    print(f"  {'spread (both crossings)':<26}{agg['spread']:>+19.2f}%")
    print("  " + "-" * 46)
    print(f"  {'= total':<26}{agg['carry']+agg['direction']+agg['spread']:>+19.2f}%")
    print(f"  {'(reported P&L)':<26}{agg['total']:>+19.2f}%")

    print(f"\n  direction positive on {agg['dir_positive_pct']:.1f}% of trades, "
          f"median {agg['dir_median']:+.2f}%")

    import power_check as pw
    print(f"\n  THE NULL — direction is convex, so positive is not evidence")
    print(f"  {'signal side (direction)':<34}{agg['direction']:>+10.2f}%")
    print(f"  {'opposite side, same paths (mirror)':<34}{agg['mirror']:>+10.2f}%")
    print("  " + "-" * 44)
    print(f"  {'= directional edge':<34}{agg['edge']:>+10.2f}%")
    if agg["edge_n"] > 1 and agg["edge_sd"] == agg["edge_sd"]:
        det = pw.mde(agg["edge_sd"], agg["edge_n"])
        se = agg["edge_sd"] / (agg["edge_n"] ** 0.5)
        print(f"  {'95% CI':<34}[{agg['edge']-1.96*se:+.2f}, {agg['edge']+1.96*se:+.2f}]%")
        print(f"  {'smallest detectable':<34}{det:>+10.2f}%")
        if abs(agg["edge"]) < det:
            print(f"  -> UNMEASURABLE at this sample, not 'no edge'")

    print(f"\n  BY EXIT REASON")
    hdr = f"  {'reason':<10}{'n':>6}{'share':>8}{'total':>9}{'carry':>9}{'direction':>11}{'spread':>9}"
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    for reason in ("TARGET", "STOP", "TIME", "THESIS", "EXPIRY"):
        sub = [r for r in rows if r["reason"] == reason]
        if not sub:
            continue
        a = summarise(sub)
        print(f"  {reason:<10}{a['n']:>6}{100*a['n']/len(rows):>7.1f}%"
              f"{a['total']:>+9.1f}{a['carry']:>+9.1f}{a['direction']:>+11.1f}"
              f"{a['spread']:>+9.1f}")

    print()
    print("=" * 78)
    print("  READING IT")
    # Read the EDGE, never the raw direction. A coin flip earns positive
    # direction from convexity alone; only the mirror difference is informative.
    if agg["edge"] > 0:
        print(f"  Directional EDGE is positive ({agg['edge']:+.2f}%): the signal beat its own")
        print(f"  mirror on identical paths, so the loss is carry and costs. That is a")
        print(f"  STRUCTURE problem — an entry filter does not fix a theta bill.")
    else:
        print(f"  Directional EDGE is negative ({agg['edge']:+.2f}%): the signal did not beat")
        print(f"  its own mirror. No structure repairs that; an entry filter is the only")
        print(f"  lever left, and the share backtest already refuted two candidates.")
    print(f"  Raw direction ({agg['direction']:+.2f}%) is NOT the number to read — a long")
    print(f"  option is convex, so a coin flip earns positive direction by itself.")
    print()
    print("  NOT MEASURED HERE: the volatility path. option_backtest prices entry and")
    print("  exit at the SAME iv, so the premium you overpay is refunded at exit. Carry")
    print("  above is theta only, and OPT_WIN_RATE inherits that omission.")
    print("=" * 78)
    return 0


def selftest() -> int:
    print("option_decompose.py selftest")
    print("=" * 72)
    sp = 5.0

    def mk(S0, Se, held, right="CALL", K=100.0, iv=0.20, dte0=30):
        t0, t1 = dte0 / ob.TRADING_DAYS, (dte0 - held) / ob.TRADING_DAYS
        mid0 = ob.bs_price(S0, K, t0, iv, right)
        entry = mid0 * (1 + sp / 200)
        exitp = ob.bs_price(Se, K, t1, iv, right) * (1 - sp / 200)
        return {"reason": "TIME", "entry_prem": entry, "exit_prem": exitp,
                "held": held, "dte_left": dte0 - held,
                "pnl_pct": (exitp - entry) / entry * 100.0,
                "spot0": S0, "spot_exit": Se, "strike": K, "iv": iv,
                "right": right, "dte0": dte0}

    # ── THE GUARD: the parts must add up to the whole ──
    cases = [(100.0, 100.0, 10), (100.0, 103.0, 10), (100.0, 97.0, 5),
             (100.0, 110.0, 25), (100.0, 100.0, 30)]
    for S0, Se, held in cases:
        t = mk(S0, Se, held)
        d = decompose(t, sp)
        assert d is not None, (S0, Se, held)
        assert abs(d["recon_pct"] - t["pnl_pct"]) < 0.001, (
            f"carry {d['carry_pct']:+.4f} + direction {d['direction_pct']:+.4f} + "
            f"spread {d['spread_cost_pct']:+.4f} = {d['recon_pct']:+.4f} but the "
            f"trade returned {t['pnl_pct']:+.4f} — an attribution that does not "
            f"reconcile is a story, not a measurement")
    print(f"reconciliation   : {len(cases)} cases, parts == whole to <0.001%")

    # ── carry must be NEGATIVE for a long option and grow with time held ──
    c10 = decompose(mk(100.0, 100.0, 10), sp)["carry_pct"]
    c25 = decompose(mk(100.0, 100.0, 25), sp)["carry_pct"]
    assert c10 < 0 and c25 < c10, (
        f"a long option must bleed theta, and bleed more over 25 days than 10: "
        f"{c10:+.2f} then {c25:+.2f}")
    print(f"carry            : {c10:+.1f}% at 10 days, {c25:+.1f}% at 25 — bleeds, and more")

    # ── direction must carry the SIGN of the move, and only the move ──
    up = decompose(mk(100.0, 103.0, 10), sp)
    flat = decompose(mk(100.0, 100.0, 10), sp)
    down = decompose(mk(100.0, 97.0, 10), sp)
    assert up["direction_pct"] > 0 > down["direction_pct"], (up, down)
    assert abs(flat["direction_pct"]) < 1e-9, (
        f"spot did not move, so direction must be EXACTLY zero, got "
        f"{flat['direction_pct']:+.6f} — anything else is carry leaking in")
    print(f"direction        : {down['direction_pct']:+.1f} / {flat['direction_pct']:+.1f} / "
          f"{up['direction_pct']:+.1f}% for -3% / flat / +3% spot")

    # ── a PUT must read the same move with the opposite sign ──
    putd = decompose(mk(100.0, 97.0, 10, right="PUT"), sp)
    assert putd["direction_pct"] > 0, (
        f"a put gains when spot falls; got {putd['direction_pct']:+.2f}")
    assert putd["carry_pct"] < 0, "and still bleeds theta"
    print(f"puts             : -3% spot reads {putd['direction_pct']:+.1f}% direction, "
          f"{putd['carry_pct']:+.1f}% carry")

    # ── carry and direction must be INDEPENDENT of each other ──
    # Same holding period, different moves: carry must be IDENTICAL, because the
    # split is defined at the entry spot. If carry moves with the spot, the
    # decomposition is attributing the move twice.
    carries = {decompose(mk(100.0, Se, 10), sp)["carry_pct"] for Se in (95.0, 100.0, 105.0)}
    assert max(carries) - min(carries) < 1e-9, (
        f"carry is defined at the ENTRY spot and must not vary with where the "
        f"trade ended: {sorted(carries)}")
    print("independence     : carry identical across -5% / flat / +5% exits")

    # ── THE NULL must cancel convexity: a coin flip must read ~zero EDGE ──
    # Symmetric moves of equal size. Raw direction is positive (convexity);
    # the mirror difference must not be.
    coin = [decompose(mk(100.0, 100.0 * (1 + m), 10), sp)
            for m in (+0.03, -0.03, +0.05, -0.05, +0.01, -0.01)]
    raw = float(np.mean([c["direction_pct"] for c in coin]))
    edge = float(np.mean([c["edge_pct"] for c in coin]))
    assert raw > 0, (
        f"symmetric moves must still show POSITIVE raw direction — that is the "
        f"convexity this null exists to remove; got {raw:+.2f}")
    assert abs(edge) < 1e-9, (
        f"and the mirrored edge must cancel it to zero on symmetric moves, got "
        f"{edge:+.6f} — if it does not, the null is not neutral and every result "
        f"it reports is biased by exactly this much")
    print(f"the null         : symmetric moves -> raw {raw:+.1f}% but edge {edge:+.3f}%")

    # ...and a signal that is genuinely right must show POSITIVE edge
    good = [decompose(mk(100.0, 103.0, 10), sp), decompose(mk(100.0, 104.0, 10), sp)]
    assert float(np.mean([g["edge_pct"] for g in good])) > 0, (
        "a signal whose calls all went up must beat its own mirror")
    print("null liveness    : an all-correct signal reads positive edge")

    # ── a trade missing its entry state is DROPPED, never guessed at ──
    bare = {"reason": "STOP", "entry_prem": 3.0, "exit_prem": 1.5, "held": 4,
            "dte_left": 26, "pnl_pct": -50.0}
    assert decompose(bare, sp) is None, (
        "a trade recorded before the entry state was carried cannot be "
        "decomposed and must be dropped, not defaulted to zero")
    print("missing state    : dropped, never defaulted")

    # ── WIRING: CALL the upstream builder and read the dict it returns ──
    # The first version of this guard grepped inspect.getsource(ob._result) for
    # each field name. That is a test of the source TEXT, not of the record:
    # renaming the dict key "spot0" to "spot_0" leaves the PARAMETER named spot0
    # in the source, so the substring check passed while the field was gone.
    # Found by deliberately breaking it. Build a real record instead.
    rec = ob._result("TIME", 3.0, 2.0, 4, 26, -33.3,
                     spot0=100.0, spot_exit=101.0, strike=100.0,
                     iv=0.2, right="CALL", dte0=30)
    missing = [f for f in REQUIRED if f not in rec]
    assert not missing, (
        f"option_backtest._result no longer returns {missing} — decompose() "
        f"would drop every trade and this module would silently measure nothing")
    assert decompose(rec, sp) is not None, (
        "a record straight from _result must be decomposable; if it is not, the "
        "two modules have drifted apart on field names or types")
    print(f"wiring           : _result returns all {len(REQUIRED)} fields, and a "
          f"record from it decomposes")

    # ── WIRING, THE PART THAT ACTUALLY BROKE: run main() end to end ──
    # The guard above checks the record SHAPE. It said nothing about whether
    # main() can build the cfg dicts run_ticker needs — and it could not. The
    # first version passed sig_cfg = {} and omitted cfg["years"], so the very
    # first ticker raised KeyError: 'adx_min' inside bt.build_signal_params()
    # while this entire suite passed. Fake only the DOWNLOAD, so every other
    # line on the path is the real one.
    import pandas as pd
    idx = pd.bdate_range("2021-01-04", periods=400)
    rng = np.random.default_rng(7)
    px = 100.0 * np.exp(np.cumsum(rng.normal(0.0007, 0.016, len(idx))))
    fake = pd.DataFrame({"Date": idx, "Open": px, "High": px * 1.012,
                         "Low": px * 0.988, "Close": px,
                         "Volume": np.full(len(idx), 3_000_000.0)})
    real_dl, real_argv = bt.download, sys.argv
    calls = []
    try:
        bt.download = lambda tk, years, *a, **k: (calls.append((tk, years)), fake.copy())[1]
        sys.argv = ["option_decompose.py", "--tickers", "AAA,BBB", "--years", "3"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = main()
    finally:
        bt.download, sys.argv = real_dl, real_argv

    assert calls, "main() never reached bt.download — it failed before any data"
    assert [c[0] for c in calls] == ["AAA", "BBB"], calls
    assert all(c[1] == 3 for c in calls), (
        f"cfg['years'] must reach download; got {calls} — omitting it was one of "
        f"the two bugs this guard exists for")
    body = out.getvalue()
    assert rc in (0, 2), rc
    if rc == 0:
        assert "DECOMPOSITION" in body and "directional edge" in body.lower(), body[:400]
        assert "carry" in body and "mirror" in body, body[:400]
    print(f"main() wiring    : ran end to end on {len(calls)} tickers through the "
          f"real run_ticker, exit {rc}")

    print("=" * 72)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default="GOOGL,AVGO,AMD,NFLX,CRM,ADBE,QCOM,MU,ORCL,NOW,PANW,LRCX")
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--dte", type=int, default=30)
    ap.add_argument("--iv-mult", type=float, default=1.15)
    ap.add_argument("--spread-pct", type=float, default=5.0)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    # Both dicts are built exactly as option_backtest.main() builds them. The
    # first version passed sig_cfg = {} and omitted cfg["years"], and crashed on
    # the first ticker with KeyError: 'adx_min' — run_ticker feeds sig_cfg to
    # bt.build_signal_params(), which reads seven keys out of it. Every selftest
    # passed, because none of them called main(). That is the adx_retest failure
    # repeated one function over, which is why the wiring test below now runs
    # main() end to end against a faked download.
    cfg = dict(years=a.years, dte=a.dte,
               tp=rp.OPT_WIN_RATE_TP_PCT, sl=rp.OPT_WIN_RATE_SL_PCT,
               dte_exit=7, use_thesis=True, iv_mult=a.iv_mult,
               spread_pct=a.spread_pct, cooldown_bars=bt.DEFAULTS["cooldown_bars"])
    sig_cfg = dict(bt.DEFAULTS)
    sig_cfg.update(tickers=tickers, years=a.years)
    trades = []
    for tk in tickers:
        print(f"  {tk} ...", file=sys.stderr)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            trades.extend(ob.run_ticker(tk, cfg, sig_cfg) or [])
    rows = [d for d in (decompose(t, a.spread_pct) for t in trades) if d]
    dropped = len(trades) - len(rows)
    if not rows:
        print("NOTHING MEASURED — no decomposable trades.")
        return 2
    if dropped:
        print(f"  {dropped} of {len(trades)} trades lacked entry state and were dropped")
    return report(rows)


if __name__ == "__main__":
    sys.exit(main())
