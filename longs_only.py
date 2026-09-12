"""
What would trading LONGS ONLY, in shares, actually have felt like?

A MEASUREMENT, NOT A TEST. There is no hypothesis here, no bar and no
pre-registration: every number is an accounting fact about a path that already
happened. Nothing is being searched over, so there is no multiplicity to correct
and nothing to be fooled by beyond arithmetic.

WHY LONGS ONLY IS A DEFENSIBLE CUT AND NOT CHERRY-PICKING

The short side is the single best-evidenced negative in this project. At share
level it is -0.287 R with a 95% CI of [-0.432, -0.141]; at option level -20.4%
with [-26.95, -13.85]. Both CLEAR ZERO. Dropping shorts removes a measured loss.

The long side is a different matter and must not be oversold: +0.085 R with a CI
of [-0.037, +0.206] - it spans zero, and it has decayed +0.268 -> +0.106 ->
+0.085 as the sample grew, which results/longshort_split.md diagnoses as an
estimate shrinking toward its true value. THIS IS THE REMOVAL OF A KNOWN LOSS,
NOT THE ADDITION OF A KNOWN GAIN.

WHAT R-MULTIPLES HIDE, AND WHY THIS MODULE EXISTS

Summing R-multiples assumes every signal can be taken at full size. It cannot.
Risking 1% behind a 2% stop is a FIFTY PERCENT position, and a $5,000 account
cannot hold three of those at once. The backtest's cooldown runs from the SIGNAL
bar, not the exit, so trades overlap freely and an R-sum quietly assumes
unlimited concurrent capital.

So two equity curves are reported:

    IDEALISED    every signal, full 1% risk, no capital constraint
    CONSTRAINED  positions capped at MAX_POSITION_PCT, and skipped outright when
                 the account has no room left

The GAP BETWEEN THEM is the finding. It is the same granularity problem the
spread backtest hit at $5,000, measured on the other side of the book.

DRAWDOWN IS A FACT, NOT AN INFERENCE

Expectancy has a confidence interval; a realised drawdown does not. It is what
the path did. That makes it the number worth trusting most in this file.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import math
import sys
from collections import defaultdict

import numpy as np

import backtest as bt
import risk_params as rp

OOS_12 = "GOOGL,AVGO,AMD,NFLX,CRM,ADBE,QCOM,MU,ORCL,NOW,PANW,LRCX"
BENCHMARK = "SPY"


def collect_longs(tickers: list[str], years: int) -> list[dict]:
    cfg = dict(bt.DEFAULTS, tickers=tickers, years=years)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        trades = bt.run(cfg) or []
    longs = [t for t in trades
             if t.get("trend") == "Bullish" and t.get("r") is not None]
    longs.sort(key=lambda t: str(t.get("entry_date")))
    return longs


def simulate(trades: list[dict], *, account: float, risk_pct: float,
             constrained: bool) -> dict:
    """
    Walk the trades in date order, compounding.

    constrained=False reproduces what an R-sum assumes: every signal taken at
    full risk, capital unlimited. constrained=True caps each position at
    MAX_POSITION_PCT of equity and SKIPS a signal when the already-open
    positions leave no room. The difference between the two is the point.
    """
    equity = account
    curve = [account]
    open_pos: list[dict] = []      # {"exit_date", "value"}
    taken = skipped = capped = 0
    peak_deployed = 0.0
    peak_concurrent = 0
    per_year: dict = defaultdict(lambda: {"pnl": 0.0, "n": 0})
    wins = 0

    for t in trades:
        ed = str(t.get("entry_date"))
        # retire positions that closed before this entry
        open_pos = [p for p in open_pos if p["exit_date"] > ed]
        deployed = sum(p["value"] for p in open_pos)

        risk_dollars = equity * risk_pct / 100.0
        entry, stop = float(t.get("entry", 0.0)), float(t.get("stop", 0.0))
        dist = entry - stop
        if not (dist > 0 and entry > 0):
            continue
        value = risk_dollars / dist * entry          # shares x price
        scale = 1.0

        if constrained:
            ceiling = equity * rp.MAX_POSITION_PCT / 100.0
            if value > ceiling:                      # size down, risk falls too
                scale = ceiling / value
                value = ceiling
                capped += 1
            if deployed + value > equity:            # no room at all
                skipped += 1
                continue

        pnl = float(t["r"]) * risk_dollars * scale
        equity += pnl
        taken += 1
        wins += int(pnl > 0)
        curve.append(equity)
        open_pos.append({"exit_date": str(t.get("exit_date", ed)), "value": value})
        deployed_now = sum(p["value"] for p in open_pos)
        peak_deployed = max(peak_deployed, 100.0 * deployed_now / equity)
        peak_concurrent = max(peak_concurrent, len(open_pos))
        yr = ed[:4]
        per_year[yr]["pnl"] += pnl
        per_year[yr]["n"] += 1

    arr = np.array(curve, dtype=float)
    peak = np.maximum.accumulate(arr)
    dd = (peak - arr) / peak
    years = max(len(per_year), 1)
    total = equity / account - 1.0
    cagr = (equity / account) ** (1.0 / years) - 1.0 if equity > 0 else -1.0
    return {"equity": equity, "curve": arr, "total_pct": 100.0 * total,
            "cagr_pct": 100.0 * cagr, "max_dd_pct": 100.0 * float(dd.max()),
            "taken": taken, "skipped": skipped, "capped": capped,
            "win_rate": 100.0 * wins / taken if taken else float("nan"),
            "peak_deployed_pct": peak_deployed,
            "peak_concurrent": peak_concurrent,
            "per_year": dict(per_year), "years": years}


def benchmark(ticker: str, years: int, account: float) -> dict | None:
    """The whole account in the index over the same span. No trading."""
    df = bt.download(ticker, years)
    if df is None or df.empty:
        return None
    c = df.dropna(subset=["Close"])["Close"].astype(float).to_numpy()
    if len(c) < 2:
        return None
    curve = account * c / c[0]
    peak = np.maximum.accumulate(curve)
    dd = (peak - curve) / peak
    total = curve[-1] / account - 1.0
    return {"total_pct": 100.0 * total,
            "cagr_pct": 100.0 * ((1 + total) ** (1.0 / years) - 1.0),
            "max_dd_pct": 100.0 * float(dd.max())}


def report(ideal: dict, real: dict, bench: dict | None, *, account: float,
           risk_pct: float) -> int:
    print("=" * 84)
    print("LONGS ONLY, IN SHARES — what it would actually have been")
    print("=" * 84)
    print(f"  ${account:,.0f} account, {risk_pct:.1f}% risk per trade, "
          f"position ceiling {rp.MAX_POSITION_PCT:.0f}%")
    print("  A MEASUREMENT, not a test. No bar, nothing searched over.")
    print("=" * 84)
    hdr = (f"  {'':<14}{'total %':>10}{'CAGR %':>9}{'max DD %':>10}"
           f"{'trades':>8}{'win %':>8}")
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    print(f"  {'idealised':<14}{ideal['total_pct']:>+10.1f}{ideal['cagr_pct']:>+9.1f}"
          f"{ideal['max_dd_pct']:>10.1f}{ideal['taken']:>8}{ideal['win_rate']:>8.1f}")
    print(f"  {'constrained':<14}{real['total_pct']:>+10.1f}{real['cagr_pct']:>+9.1f}"
          f"{real['max_dd_pct']:>10.1f}{real['taken']:>8}{real['win_rate']:>8.1f}")
    if bench:
        print(f"  {BENCHMARK+' buy-hold':<14}{bench['total_pct']:>+10.1f}"
              f"{bench['cagr_pct']:>+9.1f}{bench['max_dd_pct']:>10.1f}"
              f"{'—':>8}{'—':>8}")

    print(f"\n  WHAT THE CONSTRAINT COST")
    print(f"    signals skipped, no room    {real['skipped']}")
    print(f"    positions sized down        {real['capped']}")
    print(f"    peak concurrent positions   {real['peak_concurrent']}")
    print(f"    peak capital deployed       {real['peak_deployed_pct']:.0f}% of equity")
    lost = ideal["taken"] - real["taken"]
    if ideal["taken"]:
        print(f"    -> {lost} of {ideal['taken']} signals ({100.0*lost/ideal['taken']:.0f}%) "
              f"were not takeable at this account size")

    print(f"\n  BY YEAR (constrained)")
    print(f"    {'year':<8}{'trades':>8}{'P&L $':>12}")
    for yr in sorted(real["per_year"]):
        v = real["per_year"][yr]
        print(f"    {yr:<8}{v['n']:>8}{v['pnl']:>+12.0f}")

    print()
    print("=" * 84)
    print("  READING IT")
    print(f"  The long side is +0.085 R with a CI of [-0.037, +0.206] — it SPANS")
    print(f"  ZERO, and it decayed +0.268 -> +0.106 -> +0.085 as the sample grew.")
    print(f"  Dropping shorts removes a measured loss; it does not add a measured")
    print(f"  gain. Expectancy above has a confidence interval. The DRAWDOWN does")
    print(f"  not — that is what the path did.")
    if bench and real["cagr_pct"] < bench["cagr_pct"]:
        print(f"\n  This returned {real['cagr_pct']:+.1f}%/yr against {BENCHMARK} at "
              f"{bench['cagr_pct']:+.1f}%/yr,")
        print(f"  at {real['max_dd_pct']:.0f}% drawdown against {bench['max_dd_pct']:.0f}%. "
              f"Owning the index was")
        print(f"  better on both axes, with no signals to follow.")
    print("=" * 84)
    return 0


def selftest() -> int:
    print("longs_only.py selftest")
    print("=" * 72)

    def mk(date, r, entry=100.0, stop=98.0, hold_to="9999"):
        return {"trend": "Bullish", "r": r, "entry": entry, "stop": stop,
                "entry_date": date, "exit_date": hold_to, "ticker": "A"}

    # ── compounding, and 1% risk means 1% of CURRENT equity ──
    acct = 5000.0
    s = simulate([mk("2020-01-01", 1.0, hold_to="2020-01-02")],
                 account=acct, risk_pct=1.0, constrained=False)
    assert abs(s["equity"] - 5050.0) < 1e-6, s["equity"]
    s2 = simulate([mk("2020-01-01", 1.0, hold_to="2020-01-02"),
                   mk("2020-01-03", 1.0, hold_to="2020-01-04")],
                  account=acct, risk_pct=1.0, constrained=False)
    assert abs(s2["equity"] - 5050.0 * 1.01) < 1e-6, (
        f"the second trade must risk 1% of 5050, not of 5000: {s2['equity']}")
    print(f"compounding      : 1R then 1R -> ${s2['equity']:.2f}, risk follows equity")

    # ── THE POINT: 1% risk behind a 2% stop is a 50% POSITION ──
    # entry 100, stop 98 -> risk $50 buys 25 shares = $2,500 = 50% of a $5,000
    # account. Two of those is the whole account. An R-sum never sees this.
    risk_d = acct * 0.01
    value = risk_d / (100.0 - 98.0) * 100.0
    assert abs(value - 2500.0) < 1e-9, value
    assert value > acct * rp.MAX_POSITION_PCT / 100.0, (
        f"a ${value:.0f} position must exceed the {rp.MAX_POSITION_PCT}% ceiling "
        f"of ${acct * rp.MAX_POSITION_PCT/100:.0f} — if it does not, this module "
        f"has nothing to measure")
    print(f"the constraint   : 1% risk / 2% stop = ${value:,.0f} = "
          f"{100*value/acct:.0f}% of a ${acct:,.0f} account")

    # ── constrained must CAP, and capping must reduce the P&L ──
    one = [mk("2020-01-01", 1.0, hold_to="2020-01-02")]
    free = simulate(one, account=acct, risk_pct=1.0, constrained=False)
    cap = simulate(one, account=acct, risk_pct=1.0, constrained=True)
    assert cap["capped"] == 1 and free["capped"] == 0, (cap["capped"], free["capped"])
    assert cap["equity"] < free["equity"], (
        "a position sized down to the ceiling must also earn less")
    exp_scale = (acct * rp.MAX_POSITION_PCT / 100.0) / value
    assert abs((cap["equity"] - acct) - 50.0 * exp_scale) < 1e-6, cap["equity"]
    print(f"capping          : ${value:,.0f} -> ceiling, P&L scales to "
          f"{exp_scale:.2f}x, ${cap['equity']-acct:+.2f}")

    # ── overlapping positions must consume room, and force SKIPS ──
    overlap = [mk(f"2020-01-0{i}", 1.0, hold_to="2020-12-31") for i in range(1, 8)]
    con = simulate(overlap, account=acct, risk_pct=1.0, constrained=True)
    unc = simulate(overlap, account=acct, risk_pct=1.0, constrained=False)
    assert unc["taken"] == 7 and unc["skipped"] == 0, unc
    assert con["skipped"] > 0, (
        "seven simultaneous 25%-of-equity positions cannot fit in one account; "
        "if nothing is skipped, the room check is not wired")
    assert con["taken"] + con["skipped"] == 7, con
    assert con["peak_concurrent"] <= 5, con["peak_concurrent"]
    print(f"concurrency      : 7 overlapping -> {con['taken']} taken, "
          f"{con['skipped']} skipped, peak {con['peak_concurrent']} open")

    # ── ...and NON-overlapping trades must all fit ──
    seq = [mk(f"2020-0{i}-01", 1.0, hold_to=f"2020-0{i}-15") for i in range(1, 8)]
    con2 = simulate(seq, account=acct, risk_pct=1.0, constrained=True)
    assert con2["skipped"] == 0 and con2["taken"] == 7, con2
    print("sequencing       : 7 non-overlapping all fit, nothing skipped")

    # ── drawdown is measured on the PATH, not on the endpoints ──
    # Up, down hard, back up: the final equity is near the start but the
    # drawdown happened and must be reported.
    path = ([mk(f"2020-01-{i:02d}", +1.0, hold_to=f"2020-01-{i:02d}") for i in range(1, 11)]
            + [mk(f"2020-02-{i:02d}", -1.0, hold_to=f"2020-02-{i:02d}") for i in range(1, 21)]
            + [mk(f"2020-03-{i:02d}", +1.0, hold_to=f"2020-03-{i:02d}") for i in range(1, 11)])
    d = simulate(path, account=acct, risk_pct=1.0, constrained=False)
    assert d["max_dd_pct"] > 15.0, (
        f"twenty consecutive -1R after ten +1R must register a real drawdown, "
        f"got {d['max_dd_pct']:.1f}%")
    print(f"drawdown         : +10R then -20R then +10R -> "
          f"{d['max_dd_pct']:.1f}% max DD on a path ending near flat")

    # ── shorts must never appear ──
    mixed = [mk("2020-01-01", 1.0, hold_to="2020-01-02"),
             {**mk("2020-01-03", 1.0, hold_to="2020-01-04"), "trend": "Bearish"}]
    import backtest as _bt
    real_run, = (_bt.run,)
    try:
        _bt.run = lambda cfg: mixed
        got = collect_longs(["A"], 1)
    finally:
        _bt.run = real_run
    assert len(got) == 1 and all(t["trend"] == "Bullish" for t in got), got
    print("longs only       : Bearish trades filtered out before anything runs")

    # ── WIRING: the fields this module needs must exist upstream ──
    import inspect as _i
    src = _i.getsource(bt.simulate_trade)
    for f in ('"entry": float(entry)', '"stop": float(stop)', '"exit_date"'):
        assert f in src, f"backtest.simulate_trade no longer carries {f}"
    print("wiring           : entry, stop and exit_date carried by simulate_trade")

    print("=" * 72)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default=OOS_12)
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--account", type=float, default=rp.DEFAULT_ACCOUNT_SIZE)
    ap.add_argument("--risk-pct", type=float, default=rp.DEFAULT_RISK_PCT)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    print(f"  collecting long trades ...", file=sys.stderr)
    longs = collect_longs(tickers, a.years)
    print(f"    {len(longs)} long trades", file=sys.stderr)
    if not longs:
        print("NOTHING MEASURED — no long trades.")
        return 2
    ideal = simulate(longs, account=a.account, risk_pct=a.risk_pct,
                     constrained=False)
    real = simulate(longs, account=a.account, risk_pct=a.risk_pct,
                    constrained=True)
    print(f"  benchmark {BENCHMARK} ...", file=sys.stderr)
    bench = benchmark(BENCHMARK, a.years, a.account)
    return report(ideal, real, bench, account=a.account, risk_pct=a.risk_pct)


if __name__ == "__main__":
    sys.exit(main())
