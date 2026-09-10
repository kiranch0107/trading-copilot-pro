"""
Always-on defined-risk short premium on SPY / QQQ — does it survive?

vrp_check.py established the premise: implied vol runs +3.63 vol points above
subsequent realised (95% HAC CI [+2.40, +4.85], 83.7% of days, positive in all
eleven years). See results/vrp_measurement.md.

That measurement licensed a direction and nothing more. It said the premium
EXISTS, is BROAD, and is THICK. It never said a seller SURVIVES — its five worst
stretches are one Feb 2020 event sampled five times, so the left tail rests on
n ~= 1. This module measures the thing the premium measurement could not:
what an always-on, defined-risk seller actually ends up with, path included.

WHAT IS REAL HERE AND WHAT IS MODELLED — read this before believing a number
-----------------------------------------------------------------------------
REAL (observed data, not assumptions):
  - SPY / QQQ daily price paths, 10 years. So the FREQUENCY of max-loss events
    and the SEQUENCE of drawdowns are real, not simulated. That is the binding
    unmeasured constraint, and it is the half this backtest gets right.
  - Implied vol at entry is the observed ^VIX close, NOT a modelled multiple of
    realised. option_backtest.py assumes iv_mult = 1.15; this reads the market's
    actual number. For QQQ the observed ^VXN is used where available.

MODELLED (and therefore where this can be wrong):
  - SKEW. Real index puts trade on a skew: the further out of the money, the
    higher the implied vol. Its effect on a credit spread is NOT one-directional,
    which the first draft of this file got wrong and its own selftest caught.

    A put credit spread is net SHORT vega — the short leg sits closer to the
    money and carries the larger vega — so raising implied vol on both strikes
    RAISES the credit. Skew raises the far (long) strike more, which pushes the
    other way. Which effect wins depends on moneyness, and it changes sign:

        2% OTM, 10 wide:  credit 2.545 -> 2.436 at skew 20   (skew COSTS)
        5% OTM, 10 wide:  credit 0.932 -> 0.988              (skew PAYS)
        8% OTM, 10 wide:  credit 0.207 -> 0.295              (skew PAYS more)

    and for an iron condor it flips the opposite way, because the call wing's
    skew has the opposite sign. So skew cannot be treated as a conservative
    haircut. It is a SWEEP AXIS (--skew / --skew-sweep), and the bar must hold
    at the LEAST FAVOURABLE skew tested, whichever direction that turns out to
    be for the arm in question. That rule cannot be gamed by picking a skew.

    What skew does NOT distort is the other side of the trade: exits settle on
    REAL SPY/QQQ prices, so a bigger modelled credit is never free — if the
    downside that skew is pricing actually arrives, the settlement pays for it.
  - Fills. Modelled as a fraction of the bid-ask ceiling in risk_params, paid on
    entry and on exit, both legs.
  - European exercise and no early assignment on the short leg.

PRE-REGISTERED, BEFORE THE FIRST RUN — 2026-09-10
--------------------------------------------------
Written before any result was seen, because this repo has been burned by
deciding the bar afterwards (the ADX-35 sweep, the discovery-set long side).

    PASS, and this is worth trading, if ALL of:
      1. mean monthly return > 0 and its 95% CI clears zero.  Cycles do not
         overlap, so the ordinary interval is correct here — unlike vrp_check,
         where overlapping windows needed a HAC correction.
      2. max peak-to-trough drawdown <= MAX_DRAWDOWN_PCT of the account.
      3. still passes 1 and 2 at the LEAST FAVOURABLE skew in the swept
         range, not merely at the skew that happens to flatter it.
      4. the single worst cycle loses <= MAX_POSITION_PCT of the account —
         i.e. the position sizing actually holds under a Feb 2020 repeat.
      5. ADDED 2026-09-10 AFTER THE FIRST RUN, and it is a TIGHTENING, declared
         as an addition rather than folded in silently: the arm must beat
         holding the same dollars in the underlying over the same cycles.

         The first run passed no arm, so nothing was rescued by adding this and
         nothing already-passing was re-judged. It exists because the first run
         showed put spreads positive and iron condors ruinous — and the thing
         that separates them is not volatility, it is DELTA. A short put spread
         is net long delta in a decade when the index tripled. A positive mean
         is what long delta produces whether or not any premium was harvested.
         Without this clause the bar could be cleared by a leveraged long
         wearing the language of a volatility strategy.

    FAIL otherwise. A structure that clears 1 and 2 but fails 3 has no edge; it
    has an unmodelled cost. A structure that clears 1-3 but fails 4 is not a bad
    strategy, it is a bad SIZE, and the honest response is to shrink it and
    re-run rather than to accept the drawdown.

Clause 4 is the one the VRP measurement could not reach, and it is the reason
this module exists.
"""
from __future__ import annotations

import argparse
import math
import sys

import numpy as np
import pandas as pd

import backtest as bt
import risk_params as rp
from option_backtest import bs_price

# ── the structures ──
PUT_SPREAD = "put_spread"
IRON_CONDOR = "iron_condor"

# ── pre-registered thresholds ──
MAX_DRAWDOWN_PCT = 25.0
# Vol points added per 1.0 of downward log-moneyness. SPX 30-day skew is
# commonly quoted near 2 vol points per 10% OTM, i.e. ~20 points per 1.0 log
# unit. Deliberately at the pessimistic end of the usual range.
REALISTIC_SKEW = 20.0

TRADING_DAYS = 252
RISK_FREE = 0.04

VOL_INDEX = {"SPY": "^VIX", "QQQ": "^VXN"}


def implied_at(base_vol: float, spot: float, strike: float, skew: float) -> float:
    """
    Implied vol for one strike, given the at-the-money vol and a skew slope.

    Linear in log-moneyness: a strike BELOW spot carries MORE vol. With
    skew = 0 this returns base_vol for every strike, which is flat
    Black-Scholes and the optimistic case for a put spread.
    """
    m = math.log(strike / spot)
    return max(0.01, base_vol - skew / 100.0 * m)


def _leg(spot, strike, t, base_vol, right, skew):
    return bs_price(spot, strike, t, implied_at(base_vol, spot, strike, skew),
                    right, r=RISK_FREE)


def build(structure: str, spot: float, base_vol: float, t: float,
          width: float, otm_pct: float, skew: float) -> dict:
    """
    Strike selection and entry credit for one cycle.

    otm_pct places the SHORT strike; width is the distance to the long wing.
    Strikes are rounded to whole dollars, which is what SPY/QQQ actually list.
    """
    legs = []
    short_put = round(spot * (1.0 - otm_pct / 100.0))
    long_put = short_put - width
    legs.append(("PUT", short_put, -1))
    legs.append(("PUT", long_put, +1))
    if structure == IRON_CONDOR:
        short_call = round(spot * (1.0 + otm_pct / 100.0))
        long_call = short_call + width
        legs.append(("CALL", short_call, -1))
        legs.append(("CALL", long_call, +1))

    credit = 0.0
    for right, strike, qty in legs:
        px = _leg(spot, strike, t, base_vol, right, skew)
        credit -= qty * px          # short legs (-1) add to credit
    return {"legs": legs, "credit": credit,
            "max_loss": width - credit if structure == PUT_SPREAD
            else width - credit}


def settle(legs, spot_at_exit: float) -> float:
    """Intrinsic value owed at expiry, per share. Positive = we owe."""
    owed = 0.0
    for right, strike, qty in legs:
        intr = (max(0.0, spot_at_exit - strike) if right == "CALL"
                else max(0.0, strike - spot_at_exit))
        owed -= qty * intr
    return owed


def run(ticker: str, *, structure: str, width: float, otm_pct: float,
        dte: int, skew: float, years: int, cost_frac: float,
        account: float, vol_series: pd.Series | None = None,
        bars: pd.DataFrame | None = None) -> dict | None:
    """
    One always-on arm: enter a new cycle every `dte` sessions, hold to expiry.

    No signal, no timing, no filter — that is the point. vrp_check found the
    edge in the SIDE, not in the moment, so this measures the side.
    """
    df = bars if bars is not None else bt.download(ticker, years)
    if df is None or df.empty:
        return None
    df = df.dropna(subset=["Close"]).copy()
    df["Date"] = pd.to_datetime(df["Date"]).dt.normalize()
    df = df.set_index("Date").sort_index()

    if vol_series is None:
        vix = bt.download(VOL_INDEX.get(ticker, "^VIX"), years)
        if vix is None or vix.empty:
            return None
        vix = vix.dropna(subset=["Close"]).copy()
        vix["Date"] = pd.to_datetime(vix["Date"]).dt.normalize()
        vol_series = vix.set_index("Date").sort_index()["Close"]

    closes = df["Close"]
    vols = vol_series.reindex(closes.index).ffill() / 100.0
    t = dte / 365.0

    cycles = []
    i = 0
    while i + dte < len(closes):
        spot = float(closes.iloc[i])
        base_vol = float(vols.iloc[i]) if not pd.isna(vols.iloc[i]) else float("nan")
        if not np.isfinite(base_vol) or base_vol <= 0:
            i += dte
            continue

        pos = build(structure, spot, base_vol, t, width, otm_pct, skew)
        exit_spot = float(closes.iloc[i + dte])
        owed = settle(pos["legs"], exit_spot)

        n_legs = len(pos["legs"])
        # Crossing the spread on the way in and on the way out, every leg.
        cost = cost_frac * rp.MAX_OPTION_SPREAD_PCT / 100.0 * pos["credit"] * n_legs
        pnl = (pos["credit"] - owed - cost) * 100.0     # one contract, 100 shares

        cycles.append({
            "entry": closes.index[i], "exit": closes.index[i + dte],
            "spot": spot, "exit_spot": exit_spot, "iv": base_vol * 100.0,
            "credit": pos["credit"], "owed": owed, "pnl": pnl,
            "max_loss": pos["max_loss"] * 100.0,
        })
        i += dte

    if not cycles:
        return None
    return summarise(cycles, account=account, ticker=ticker,
                     structure=structure, width=width)


def benchmark(cycles: list[dict], *, account: float) -> dict:
    """
    The comparison the strategy has to beat: the SAME dollars, in the index.

    A short put spread is net LONG delta. Over a decade in which the index
    roughly tripled, a positive mean is exactly what a long-delta position
    produces whether or not any volatility premium was harvested. So the
    strategy's own numbers cannot tell you which one you were paid for — this
    can. It holds the capital the spread ties up (its max loss) in the
    underlying, entering and exiting on the same dates, and reports what that
    would have done.

    If the spread does not beat this, the premium was not what paid you.
    """
    pnl = []
    for c in cycles:
        dollars = c["max_loss"]
        shares = dollars / c["spot"]
        pnl.append(shares * (c["exit_spot"] - c["spot"]))
    pnl = np.array(pnl, dtype=float)
    equity = account + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[account], equity]))
    dd = (peak - np.concatenate([[account], equity])) / peak
    ret = pnl / account * 100.0
    n = len(ret)
    sd = float(ret.std(ddof=1)) if n > 1 else float("nan")
    mean = float(ret.mean())
    half = 1.96 * sd / math.sqrt(n) if n > 1 else float("nan")
    return {"mean_pct": mean, "ci": (mean - half, mean + half),
            "total_pct": float(equity[-1] - account) / account * 100.0,
            "max_dd_pct": float(dd.max() * 100.0),
            "win_rate": float((pnl > 0).mean() * 100.0)}


def summarise(cycles: list[dict], *, account: float, ticker: str,
              structure: str, width: float) -> dict:
    pnl = np.array([c["pnl"] for c in cycles], dtype=float)
    equity = account + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[account], equity]))
    dd = (peak - np.concatenate([[account], equity])) / peak
    n = len(pnl)
    ret_pct = pnl / account * 100.0
    sd = float(ret_pct.std(ddof=1)) if n > 1 else float("nan")
    half = 1.96 * sd / math.sqrt(n) if n > 1 else float("nan")
    mean = float(ret_pct.mean())
    worst_i = int(np.argmin(pnl))

    return {
        "ticker": ticker, "structure": structure, "width": width,
        "cycles": n,
        "mean_pct": mean, "ci": (mean - half, mean + half),
        "total_pct": float(equity[-1] - account) / account * 100.0,
        "win_rate": float((pnl > 0).mean() * 100.0),
        "max_dd_pct": float(dd.max() * 100.0),
        "worst_pct": float(pnl.min() / account * 100.0),
        "worst_when": cycles[worst_i]["entry"].date(),
        "max_loss_pct": float(max(c["max_loss"] for c in cycles) / account * 100.0),
        "avg_credit": float(np.mean([c["credit"] for c in cycles])),
        "benchmark": benchmark(cycles, account=account),
        "rows": cycles,
    }


def verdict(r: dict, account: float) -> tuple[bool, list[str]]:
    """The pre-registered bar, applied. Returns (passed, reasons)."""
    reasons = []
    if not r["ci"][0] > 0:
        reasons.append(f"95% CI [{r['ci'][0]:+.2f}, {r['ci'][1]:+.2f}]% "
                       f"does not clear zero")
    if r["max_dd_pct"] > MAX_DRAWDOWN_PCT:
        reasons.append(f"max drawdown {r['max_dd_pct']:.1f}% exceeds "
                       f"the {MAX_DRAWDOWN_PCT:.0f}% bar")
    b = r.get("benchmark")
    if b is not None and r["mean_pct"] <= b["mean_pct"]:
        reasons.append(f"mean {r['mean_pct']:+.2f}%/cycle does not beat simply "
                       f"holding the same dollars in {r['ticker']} "
                       f"({b['mean_pct']:+.2f}%) — whatever paid, it was not "
                       f"the volatility premium")
    if abs(r["worst_pct"]) > rp.MAX_POSITION_PCT:
        reasons.append(f"worst cycle {r['worst_pct']:+.1f}% breaches "
                       f"MAX_POSITION_PCT ({rp.MAX_POSITION_PCT:.0f}%) — "
                       f"a bad SIZE, not necessarily a bad structure")
    return (not reasons), reasons


def verdict_across_skews(by_skew: dict, account: float) -> tuple[bool, list[str]]:
    """
    Clause 3, enforced: an arm passes only if it passes at EVERY skew tested.

    Judging each skew separately and reporting the good one is how a sweep
    becomes a search for a flattering assumption. Skew's effect changes sign
    with moneyness (see the module docstring), so there is no single
    conservative direction to haircut toward — the only rule that cannot be
    gamed is that the worst case in the swept range must clear the bar.
    """
    failures = []
    for skew in sorted(by_skew):
        ok, why = verdict(by_skew[skew], account)
        if not ok:
            failures.append(f"at skew {skew:.0f}: " + "; ".join(why))
    return (not failures), failures


def selftest() -> int:
    """Offline. No network: every input is constructed."""
    print("spread_backtest.py selftest")
    print("=" * 70)

    # ── skew does what its name says, in the direction that matters ──
    spot, vol = 650.0, 0.16
    assert implied_at(vol, spot, 600, 20.0) > vol, "a lower strike must carry MORE vol"
    assert implied_at(vol, spot, 700, 20.0) < vol, "a higher strike must carry LESS vol"
    assert implied_at(vol, spot, 600, 0.0) == vol, "zero skew must be flat"

    # ── skew's effect on the credit CHANGES SIGN with moneyness ──
    # The first draft asserted skew always shrinks the put-spread credit, on the
    # reasoning that the long wing is the far, dearer strike. The model refuted
    # it immediately. A put credit spread is net SHORT vega — the short leg is
    # nearer the money and has the larger vega — so lifting vol on both strikes
    # RAISES the credit, and only near the money does the far leg's extra skew
    # win. Pinning the sign flip is a stronger test than the wrong one it
    # replaces, because it fails if either mechanism is mis-wired.
    near = [build(PUT_SPREAD, spot, vol, 30 / 365, 10, 2.0, k)["credit"]
            for k in (0.0, REALISTIC_SKEW)]
    far = [build(PUT_SPREAD, spot, vol, 30 / 365, 10, 8.0, k)["credit"]
           for k in (0.0, REALISTIC_SKEW)]
    assert near[1] < near[0], (
        f"near the money the long wing's skew should dominate and SHRINK the "
        f"credit, got {near[0]:.3f} -> {near[1]:.3f}")
    assert far[1] > far[0], (
        f"far out of the money the short leg's vega should dominate and GROW "
        f"the credit, got {far[0]:.3f} -> {far[1]:.3f}")
    print(f"skew sign flip   : 2% OTM {near[0]:.3f}->{near[1]:.3f} (costs), "
          f"8% OTM {far[0]:.3f}->{far[1]:.3f} (pays)")

    # ── settlement pays what it should at the boundaries ──
    legs = build(PUT_SPREAD, spot, vol, 30 / 365, 10, 5.0, 0.0)["legs"]
    short_k = legs[0][1]
    assert settle(legs, spot * 1.10) == 0.0, "well above the short strike: expires worthless"
    assert abs(settle(legs, short_k - 50) - 10.0) < 1e-9, \
        "far below the long wing: owes exactly the width"
    mid = settle(legs, short_k - 4)
    assert 0 < mid < 10, f"between the strikes must be partial, got {mid}"
    print(f"settlement       : 0 above, width below, {mid:.1f} partial in between")

    # ── an iron condor collects more than a put spread, and caps the same ──
    ps = build(PUT_SPREAD, spot, vol, 30 / 365, 10, 5.0, 0.0)
    ic = build(IRON_CONDOR, spot, vol, 30 / 365, 10, 5.0, 0.0)
    assert ic["credit"] > ps["credit"], "two sides must collect more than one"
    ic_legs = ic["legs"]
    assert settle(ic_legs, spot * 1.50) <= 10.0 + 1e-9, \
        "a condor's upside loss must still cap at the width"
    print(f"condor           : credit {ps['credit']:.2f} -> {ic['credit']:.2f}, "
          f"loss still capped at the width")

    # ── the bar applies, and every clause can fail ──
    acct = 5000.0
    good = {"ci": (0.4, 1.2), "max_dd_pct": 12.0, "worst_pct": -18.0}
    ok, why = verdict(good, acct)
    assert ok and not why, why
    for bad, expect in (
        ({"ci": (-0.3, 1.5), "max_dd_pct": 12.0, "worst_pct": -18.0}, "CI"),
        ({"ci": (0.4, 1.2), "max_dd_pct": 44.0, "worst_pct": -18.0}, "drawdown"),
        ({"ci": (0.4, 1.2), "max_dd_pct": 12.0, "worst_pct": -37.0}, "MAX_POSITION_PCT"),
    ):
        ok, why = verdict(bad, acct)
        assert not ok, (bad, "should have failed")
        assert any(expect in w for w in why), (bad, why)
    allbad = verdict({"ci": (-1.0, 1.0), "max_dd_pct": 90.0, "worst_pct": -99.0}, acct)
    assert allbad[0] is False and len(allbad[1]) == 3, \
        "an all-bad result must trip every clause, or one of them is inert"
    print("pre-registered bar: passes clean, fails CI / drawdown / size, all three")

    # ── clause 5: the benchmark's ARITHMETIC, on a path with a known answer ──
    # A first draft asserted the index must beat the spread on a rising ramp.
    # It does not, and the model said so: only the at-risk dollars are deployed,
    # so a fat credit can out-earn them. Testing the economics of a synthetic
    # path was the wrong test. Test what the clause actually depends on — that
    # the benchmark computes the right number, and that the verdict reads it.
    known = [{"spot": 100.0, "exit_spot": 110.0, "max_loss": 1000.0},
             {"spot": 110.0, "exit_spot": 99.0, "max_loss": 1000.0}]
    b = benchmark(known, account=5000.0)
    # +10% then -10% on $1,000 deployed = +$100 then -$100, on a $5,000 account.
    assert abs(b["mean_pct"] - 0.0) < 1e-9, f"mean should be 0.0%, got {b['mean_pct']}"
    assert abs(b["win_rate"] - 50.0) < 1e-9, b["win_rate"]
    up = benchmark([{"spot": 100.0, "exit_spot": 120.0, "max_loss": 1000.0}],
                   account=5000.0)
    assert abs(up["mean_pct"] - 4.0) < 1e-9, (
        f"+20% on $1,000 of a $5,000 account is +4.0%, got {up['mean_pct']}")
    print(f"benchmark maths  : +20% on the at-risk dollars -> "
          f"{up['mean_pct']:+.1f}% of account")

    # The clause fires on a strategy that loses to the index, and only then.
    beaten = {"ci": (0.4, 1.2), "max_dd_pct": 12.0, "worst_pct": -18.0,
              "mean_pct": 0.50, "ticker": "SPY",
              "benchmark": {"mean_pct": 0.90}}
    ok, why = verdict(beaten, acct)
    assert not ok and any("volatility premium" in w for w in why), why
    winning = dict(beaten, benchmark={"mean_pct": 0.10})
    ok, why = verdict(winning, acct)
    assert ok, f"an arm that beats the index must not be sunk by clause 5: {why}"
    print("clause 5         : sinks an arm the index beats, spares one it does not")

    # WIRING: run() must actually attach a benchmark, or the clause is inert.
    _idx = pd.bdate_range("2020-01-02", periods=300)
    _flat = pd.DataFrame({"Date": _idx, "Close": np.full(len(_idx), 400.0)})
    _vols = pd.Series(np.full(len(_idx), 20.0), index=_idx)
    wired = run("SPY", structure=PUT_SPREAD, width=10, otm_pct=5.0, dte=30,
                skew=0.0, years=1, cost_frac=0.0, account=5000.0,
                vol_series=_vols, bars=_flat)
    assert "benchmark" in wired and "mean_pct" in wired["benchmark"], \
        "run() dropped the benchmark — clause 5 would never fire on real data"
    assert abs(wired["benchmark"]["mean_pct"]) < 1e-9, \
        "a perfectly flat path must give the index-holder exactly zero"
    print("clause 5 wiring  : run() attaches it; flat path gives the holder 0.00%")

    # ── clause 3: one bad skew must sink the arm, however good the others ──
    passing = {"ci": (0.4, 1.2), "max_dd_pct": 12.0, "worst_pct": -18.0}
    failing = {"ci": (0.4, 1.2), "max_dd_pct": 44.0, "worst_pct": -18.0}
    ok, why = verdict_across_skews({0.0: passing, 20.0: passing}, acct)
    assert ok and not why, why
    ok, why = verdict_across_skews({0.0: passing, 10.0: passing,
                                    20.0: failing, 30.0: passing}, acct)
    assert not ok, "three good skews must not rescue one bad one"
    assert len(why) == 1 and "at skew 20" in why[0], why
    print("clause 3         : one failing skew sinks the arm, and is named")

    # ── the engine end to end, on a constructed path with a KNOWN outcome ──
    idx = pd.bdate_range("2020-01-02", periods=300)
    flatpx = pd.DataFrame({"Date": idx, "Close": np.full(len(idx), 400.0)})
    vols = pd.Series(np.full(len(idx), 20.0), index=idx)
    r = run("SPY", structure=PUT_SPREAD, width=10, otm_pct=5.0, dte=30,
            skew=0.0, years=1, cost_frac=0.0, account=5000.0,
            vol_series=vols, bars=flatpx)
    assert r is not None and r["cycles"] >= 8, r
    assert r["win_rate"] == 100.0, (
        f"a perfectly flat path can never touch a 5% OTM short strike, so every "
        f"cycle must win; got {r['win_rate']:.1f}%")
    assert r["mean_pct"] > 0, "and the mean must be the credit, positive"
    print(f"engine           : flat path -> {r['cycles']} cycles, "
          f"{r['win_rate']:.0f}% win, {r['mean_pct']:+.2f}%/cycle")

    # A crash path must produce a full-width loss, and the sizing clause must see it.
    crash = np.full(len(idx), 400.0)
    crash[40:] = 250.0
    crashpx = pd.DataFrame({"Date": idx, "Close": crash})
    rc = run("SPY", structure=PUT_SPREAD, width=10, otm_pct=5.0, dte=30,
             skew=0.0, years=1, cost_frac=0.0, account=5000.0,
             vol_series=vols, bars=crashpx)
    assert rc["worst_pct"] < 0, "a 37% gap down must lose"
    assert rc["max_dd_pct"] > 0, "and must register a drawdown"
    print(f"engine           : crash path -> worst {rc['worst_pct']:+.1f}%, "
          f"max dd {rc['max_dd_pct']:.1f}%")

    # LIVENESS: costs must actually subtract.
    free = run("SPY", structure=PUT_SPREAD, width=10, otm_pct=5.0, dte=30,
               skew=0.0, years=1, cost_frac=0.0, account=5000.0,
               vol_series=vols, bars=flatpx)
    paid = run("SPY", structure=PUT_SPREAD, width=10, otm_pct=5.0, dte=30,
               skew=0.0, years=1, cost_frac=1.0, account=5000.0,
               vol_series=vols, bars=flatpx)
    assert paid["mean_pct"] < free["mean_pct"], (
        "paying the bid-ask must reduce the return — if it does not, the cost "
        "model is wired to nothing")
    print(f"cost model       : {free['mean_pct']:+.3f}% free -> "
          f"{paid['mean_pct']:+.3f}% after crossing")

    print("=" * 70)
    print("All self-tests passed.")
    return 0


def report(by_arm: dict, account: float, skews: list[float]) -> int:
    print("=" * 78)
    print("ALWAYS-ON DEFINED RISK — does the seller survive?")
    print("=" * 78)
    print(f"Account   : ${account:,.0f}")
    print(f"Implied   : observed ^VIX / ^VXN close, not a modelled multiple")
    print(f"Skews     : {', '.join(f'{k:.0f}' for k in skews)} "
          f"vol points per 1.0 log-moneyness")
    print(f"Exits     : real SPY / QQQ settlement prices")
    print("=" * 78)

    for skew in skews:
        print(f"\n  SKEW {skew:.0f}")
        hdr = (f"  {'arm':<24} {'cyc':>4} {'mean%':>7} {'95% CI':>16} {'tot%':>8} "
               f"{'win%':>6} {'maxDD%':>7} {'worst%':>7} {'hold%':>8}")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for arm in sorted(by_arm):
            r = by_arm[arm].get(skew)
            if r is None:
                continue
            print(f"  {arm:<24} {r['cycles']:>4} {r['mean_pct']:>+7.2f} "
                  f"[{r['ci'][0]:>+6.2f},{r['ci'][1]:>+6.2f}] "
                  f"{r['total_pct']:>+8.1f} {r['win_rate']:>6.1f} "
                  f"{r['max_dd_pct']:>7.1f} {r['worst_pct']:>+7.1f} "
                  f"{r['benchmark']['mean_pct']:>+8.2f}")

    print()
    print("=" * 78)
    print("PRE-REGISTERED VERDICT")
    print("=" * 78)
    print("  hold% = mean cycle return from putting the SAME dollars the spread")
    print("          ties up into the underlying instead. Clause 5: the arm must")
    print("          beat it, or the volatility premium is not what paid.")
    print()
    print(f"  bar: CI clears zero AND maxDD <= {MAX_DRAWDOWN_PCT:.0f}% AND worst")
    print(f"       cycle <= {rp.MAX_POSITION_PCT:.0f}% of account — at EVERY skew tested,")
    print(f"       because skew's sign depends on moneyness and cannot be")
    print(f"       haircut in one direction.")
    print()
    any_pass = False
    for arm in sorted(by_arm):
        ok, why = verdict_across_skews(by_arm[arm], account)
        if ok:
            any_pass = True
            print(f"  PASS  {arm}")
        else:
            print(f"  FAIL  {arm}")
            for w in why:
                print(f"          - {w}")
    print("=" * 78)
    if not any_pass:
        print("  NO ARM CLEARED THE BAR. That is a result, not a prompt to widen")
        print("  the grid until one does.")
        print("=" * 78)
    return 0 if any_pass else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default="SPY,QQQ")
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--dte", type=int, default=30)
    ap.add_argument("--otm", type=float, default=5.0,
                    help="short strike, %% out of the money")
    ap.add_argument("--widths", default="5,10")
    ap.add_argument("--structures", default=f"{PUT_SPREAD},{IRON_CONDOR}")
    ap.add_argument("--skew", type=float, default=REALISTIC_SKEW,
                    help="vol points per 1.0 log-moneyness; 0 = flat BS "
                         "(optimistic for put spreads)")
    ap.add_argument("--skew-sweep", action="store_true",
                    help="run the whole grid at several skews and show the "
                         "sensitivity, since skew is the unmeasured input")
    ap.add_argument("--cost-frac", type=float, default=0.5,
                    help="fraction of MAX_OPTION_SPREAD_PCT actually paid")
    ap.add_argument("--account", type=float, default=rp.DEFAULT_ACCOUNT_SIZE)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    widths = [float(w) for w in a.widths.split(",") if w.strip()]
    structures = [s.strip() for s in a.structures.split(",") if s.strip()]
    skews = [0.0, 10.0, REALISTIC_SKEW, 30.0] if a.skew_sweep else [a.skew]

    by_arm: dict = {}
    for skew in skews:
        for tk in tickers:
            for st in structures:
                for w in widths:
                    r = run(tk, structure=st, width=w, otm_pct=a.otm,
                            dte=a.dte, skew=skew, years=a.years,
                            cost_frac=a.cost_frac, account=a.account)
                    if r is None:
                        print(f"  ! no data for {tk} — skipped", file=sys.stderr)
                        continue
                    arm = f"{tk} {st} w{w:.0f} otm{a.otm:.0f}"
                    by_arm.setdefault(arm, {})[skew] = r
    if not by_arm:
        print("NOTHING MEASURED — do not read anything into this run.",
              file=sys.stderr)
        return 2
    return report(by_arm, a.account, skews)


if __name__ == "__main__":
    sys.exit(main())
