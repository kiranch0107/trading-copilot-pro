#!/usr/bin/env python3
"""
vrp_check.py — does the volatility risk premium exist in this data?
====================================================================
THE QUESTION, AND WHY IT COMES FIRST

option_backtest.py prices every entry at `iv = realised_vol * 1.15`, with the
comment "trade above realised vol; ignoring that would make every entry look
cheap". That 1.15 is an ASSUMPTION, and it is the entire thesis behind selling
premium rather than buying it. Nothing in this repo has ever measured it.

Before building spread machinery — two legs, assignment, margin — measure the
premise. If implied does not exceed subsequent realised by enough to survive
costs, the short-premium thesis is dead and the build is not worth starting.

WHAT IS MEASURED

VIX is the market's 30-day implied volatility for SPX, quoted in annualised
percentage points. For each day t:

    implied[t]   = VIX close on day t
    realised[t]  = annualised stdev of SPY log returns over the NEXT
                   FORWARD_SESSIONS trading days
    premium[t]   = implied[t] - realised[t]

A positive premium means options were priced above what the market then did —
the seller's edge. A negative one means the buyer was right.

THREE THINGS THIS GETS RIGHT ON PURPOSE

1. HORIZONS MATCH. VIX is a 30-CALENDAR-day measure, which is 21 trading days.
   Comparing it against a 20- or 30-SESSION realised window would be a basis
   error — the same shape as comparing a win rate measured at TP+100 against a
   breakeven computed at TP+200, which this repo has already paid for twice.

2. THE UNEVALUABLE TAIL IS DROPPED, NOT SILENTLY TRUNCATED. The last
   FORWARD_SESSIONS days have no forward window yet. They are removed and
   COUNTED, because a forward-looking measure that quietly scores its final rows
   against a partial window is how a backtest starts describing something nobody
   ran.

3. IT LOOKS FORWARD DELIBERATELY, WHICH IS NOT LOOKAHEAD. Every other measure in
   this repo must use only what was knowable at the time. This one compares a
   price formed at t against what happened after t — that IS the question. It is
   a measurement, not a signal, and nothing here may be used to trade.

PRE-REGISTERED, BEFORE THE FIRST RUN — 2026-09-10
--------------------------------------------------
Written down first because this repo has been burned by deciding the bar after
seeing the number (the ADX-35 sweep, the discovery-set long side). The bar:

    PASS, and the spread build is worth starting, if ALL of:
      - mean premium > 0 and the 95% CI clears zero
      - at least 70% of days show implied > subsequent realised
      - mean premium >= MIN_EDGE_VOL_POINTS, so there is headroom for costs

    FAIL otherwise, and short premium is not the answer either.

MIN_EDGE_VOL_POINTS is a JUDGMENT, not a derivation, and it is stated here so it
cannot be revised after the fact. A credit spread pays bid-ask on TWO legs, and
this repo's own ceiling is 8% of mid on ONE. Two vol points of average edge is
the least that could plausibly survive that. If the result lands between 0 and 2,
that is a FAIL by this rule and an honest "too thin to trade", not a pass.

WHAT A PASS WOULD AND WOULD NOT LICENSE

Would: building and testing a defined-risk short structure on INDEX options.

Would NOT: selling premium on single names. The VRP is best documented on index
options; single names carry earnings and idiosyncratic jump risk that the index
diversifies away. A positive SPY result is evidence about SPY, and this repo's
own history — the long side that held up on 7 tickers and decayed on 12 — is the
argument for not generalising a result past what it measured.

Run
---
    python vrp_check.py                 # 10 years
    python vrp_check.py --years 15
    python vrp_check.py --selftest      # offline, no network
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

import backtest as bt

# VIX is a 30-CALENDAR-day measure. 21 trading days is the matching horizon;
# using 20 or 30 sessions would compare two different windows.
FORWARD_SESSIONS = 21
TRADING_DAYS = 252

# See the pre-registration above. Stated before the first run.
MIN_EDGE_VOL_POINTS = 2.0
MIN_PCT_POSITIVE = 70.0

VIX_TICKER = "^VIX"
UNDERLYING = "SPY"


def forward_realised_vol(closes: pd.Series,
                         sessions: int = FORWARD_SESSIONS) -> pd.Series:
    """
    Annualised realised vol over the NEXT `sessions` days, aligned to day t.

    Deliberately forward-looking — see the module docstring. The final
    `sessions` entries are NaN because their window does not exist yet; the
    caller must drop and count them rather than treat them as zero.
    """
    rets = np.log(closes / closes.shift(1))
    # shift(-1) so the window starts the day AFTER t: an option sold at t's
    # close is exposed to t+1 onward, not to t's own return.
    fwd = rets.shift(-1).rolling(sessions).std().shift(-(sessions - 1))
    return fwd * np.sqrt(TRADING_DAYS) * 100.0


def measure(years: int = 10) -> dict | None:
    spy = bt.download(UNDERLYING, years)
    vix = bt.download(VIX_TICKER, years)
    missing = [t for t, d in ((UNDERLYING, spy), (VIX_TICKER, vix)) if d is None]
    if missing:
        print(f"  ! no data for {', '.join(missing)} — cannot measure",
              file=sys.stderr)
        return None

    spy = spy.dropna(subset=["Close"]).copy()
    vix = vix.dropna(subset=["Close"]).copy()
    for d in (spy, vix):
        d["Date"] = pd.to_datetime(d["Date"]).dt.normalize()

    spy = spy.set_index("Date").sort_index()
    vix = vix.set_index("Date").sort_index()

    realised = forward_realised_vol(spy["Close"])
    frame = pd.DataFrame({"implied": vix["Close"], "realised": realised}).dropna(
        subset=["implied"])

    total = len(frame)
    unevaluable = int(frame["realised"].isna().sum())
    frame = frame.dropna(subset=["realised"])
    frame["premium"] = frame["implied"] - frame["realised"]

    if frame.empty:
        print("  ! no overlapping days — cannot measure", file=sys.stderr)
        return None

    p = frame["premium"]
    n = len(p)
    sd = float(p.std(ddof=1))
    mean = float(p.mean())
    half = 1.96 * sd / np.sqrt(n)

    return {
        "n": n, "unevaluable": unevaluable, "rows_seen": total,
        "mean": mean, "median": float(p.median()), "sd": sd,
        "ci": (mean - half, mean + half),
        "pct_positive": float((p > 0).mean() * 100.0),
        "p05": float(p.quantile(0.05)), "p95": float(p.quantile(0.95)),
        "worst": float(p.min()), "best": float(p.max()),
        "frame": frame,
    }


def verdict(r: dict) -> tuple[bool, list[str]]:
    """The pre-registered bar, applied. Returns (passed, reasons)."""
    reasons = []
    ci_clears = r["ci"][0] > 0
    if not ci_clears:
        reasons.append(f"95% CI [{r['ci'][0]:+.2f}, {r['ci'][1]:+.2f}] does not clear zero")
    if r["pct_positive"] < MIN_PCT_POSITIVE:
        reasons.append(f"{r['pct_positive']:.1f}% of days positive, "
                       f"below the {MIN_PCT_POSITIVE:.0f}% bar")
    if r["mean"] < MIN_EDGE_VOL_POINTS:
        reasons.append(f"mean {r['mean']:+.2f} vol points is under the "
                       f"{MIN_EDGE_VOL_POINTS:.1f}-point bar — too thin to pay "
                       f"two legs of bid-ask")
    return (not reasons), reasons


def report(r: dict, years: int) -> int:
    W = 78
    print("=" * W)
    print("VOLATILITY RISK PREMIUM — is the 1.15x assumption real?")
    print("=" * W)
    print(f"Implied   : {VIX_TICKER} close (30-day, annualised %)")
    print(f"Realised  : {UNDERLYING} forward {FORWARD_SESSIONS}-session vol, "
          f"annualised %")
    print(f"History   : {years} years")
    print(f"Days      : {r['n']:,} measured, {r['unevaluable']} dropped with no "
          f"forward window yet")
    print("=" * W)

    print(f"\n  mean premium   : {r['mean']:+.2f} vol points")
    print(f"  median         : {r['median']:+.2f}")
    print(f"  95% CI         : [{r['ci'][0]:+.2f}, {r['ci'][1]:+.2f}]")
    print(f"  days positive  : {r['pct_positive']:.1f}%")
    print(f"  5th / 95th pct : {r['p05']:+.2f} / {r['p95']:+.2f}")
    print(f"  worst / best   : {r['worst']:+.2f} / {r['best']:+.2f}")

    # The tail is the whole risk in short premium, so it gets its own line
    # rather than being left inside a percentile.
    f = r["frame"]
    by_year = f.groupby(f.index.year)["premium"].agg(["mean", "count"])
    print(f"\n  BY YEAR — a premium that only exists in calm years is not one")
    for yr, row in by_year.iterrows():
        bar = "+" if row["mean"] > 0 else "-"
        print(f"    {yr}  {row['mean']:+6.2f}  ({int(row['count'])} days) {bar}")

    print(f"\n  WORST 5 STRETCHES (implied minus realised, most negative)")
    for dt, row in f.nsmallest(5, "premium").iterrows():
        print(f"    {dt.date()}  implied {row['implied']:5.1f}  "
              f"realised {row['realised']:5.1f}  -> {row['premium']:+6.2f}")

    passed, reasons = verdict(r)
    print("\n" + "=" * W)
    print("PRE-REGISTERED VERDICT")
    print("=" * W)
    print(f"  bar: CI clears zero AND >={MIN_PCT_POSITIVE:.0f}% days positive "
          f"AND mean >= {MIN_EDGE_VOL_POINTS:.1f} vol points")
    if passed:
        print("\n  PASS — the premium is real, persistent and thick enough to")
        print("  be worth testing a defined-risk short structure against.")
        print("\n  It licenses INDEX options only. Single names carry earnings")
        print("  and jump risk the index diversifies away, and this repo's own")
        print("  history is the argument against generalising past what was")
        print("  measured.")
    else:
        print("\n  FAIL — short premium is not the answer either. Reasons:")
        for why in reasons:
            print(f"    - {why}")
        print("\n  Do not build the spread machinery on this.")
    print("=" * W)
    return 0 if passed else 1


def selftest() -> int:
    """Offline. Constructs series with a KNOWN premium and checks it is found."""
    print("vrp_check.py selftest")
    print("=" * 66)

    # ── forward vol is forward, and aligned to the right day ──
    rng = np.random.default_rng(0)
    n = 200
    idx = pd.bdate_range("2024-01-02", periods=n)
    # A series that is calm, then violent, so the forward window must PICK UP
    # the violence BEFORE it happens in the index.
    rets = np.concatenate([rng.normal(0, 0.002, n // 2),
                           rng.normal(0, 0.020, n - n // 2)])
    closes = pd.Series(100 * np.exp(np.cumsum(rets)), index=idx)
    fwd = forward_realised_vol(closes, sessions=21)

    calm_day = n // 2 - 40          # well before the regime change
    edge_day = n // 2 - 5           # its forward window straddles the change
    assert fwd.iloc[calm_day] < fwd.iloc[edge_day], (
        f"forward vol must RISE before the volatility does "
        f"({fwd.iloc[calm_day]:.1f} vs {fwd.iloc[edge_day]:.1f}) — if it does "
        f"not, the window is aligned backwards and the whole measure is a "
        f"lagging indicator wearing a forward name")
    assert fwd.iloc[-1] != fwd.iloc[-1] or pd.isna(fwd.iloc[-1]), \
        "the final rows have no forward window and must be NaN"
    assert int(fwd.isna().sum()) >= 21, \
        "at least one full forward window must be unevaluable at the tail"
    print(f"forward window   : rises before the vol does, "
          f"{int(fwd.isna().sum())} tail rows unevaluable")

    # ── the verdict applies the bar it pre-registered ──
    good = {"mean": 3.0, "ci": (2.1, 3.9), "pct_positive": 80.0}
    ok, why = verdict(good)
    assert ok and not why, why
    for bad, expect in (
        ({"mean": 3.0, "ci": (-0.4, 6.4), "pct_positive": 80.0}, "CI"),
        ({"mean": 3.0, "ci": (2.1, 3.9), "pct_positive": 55.0}, "% of days"),
        ({"mean": 1.2, "ci": (0.8, 1.6), "pct_positive": 80.0}, "vol points"),
    ):
        ok, why = verdict(bad)
        assert not ok, (bad, "should have failed")
        assert any(expect.split()[0] in w for w in why), (bad, why)
    print("pre-registered bar: passes a clean case, fails CI / breadth / thinness")

    # LIVENESS: a bar that nothing can fail is not a bar.
    assert verdict({"mean": 0.0, "ci": (-1.0, 1.0), "pct_positive": 0.0})[0] is False
    assert len(verdict({"mean": 0.0, "ci": (-1.0, 1.0), "pct_positive": 0.0})[1]) == 3, \
        "an all-bad input must trip every clause, or some clause is inert"
    print("bar liveness     : an all-bad result trips all three clauses")

    print("=" * 66)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    r = measure(a.years)
    if r is None:
        print("\nNOTHING MEASURED — do not read anything into this run.",
              file=sys.stderr)
        return 2
    return report(r, a.years)


if __name__ == "__main__":
    sys.exit(main())
