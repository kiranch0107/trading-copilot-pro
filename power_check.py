"""
Can this test find what it is looking for? Ask BEFORE running it, not after.

THE MISTAKE THIS EXISTS TO PREVENT, found 2026-09-10 by running it late.

spread_backtest.py produced 83 non-overlapping monthly cycles and reported that
no arm's confidence interval cleared zero. That was read as "no edge". It was
not. With sd ~4.9%/cycle and n = 83, the smallest edge that test could detect at
80% power is +1.51%/cycle — about +18%/yr. An ordinary, genuinely profitable
6%/yr edge would have returned "not significant" from that test, which is the
same output it gives for an edge of exactly zero.

Two negatives from this repo, and only one of them means what it appeared to:

    backtest.py directional  1,386 trades, detectable 0.075 R, observed -0.048 R
                             -> adequately powered. The negative is real.
    spread_backtest.py       83 cycles, detectable +18%/yr, observed +3.8%/yr
                             -> underpowered. "Not significant" said nothing.

An underpowered test is worse than no test, because it produces a confident-
looking number and invites you to believe it. A "no" you cannot trust costs
exactly as much as a "yes" you cannot trust.

HOW TO USE THIS

Before running any new backtest:

    python power_check.py --sd 4.9 --n 83
        what could this test detect?

    python power_check.py --sd 4.9 --effect 0.5
        how many observations would I need to detect the edge I am hoping for?

    python power_check.py --sd 4.9 --n 83 --effect 0.5 --strict
        exit 1 if the planned test cannot detect the effect it is looking for.
        Wire this into a pre-registration so a hopeless test cannot be run and
        then interpreted.

THE PART THAT IS NOT STATISTICS

Power comes from BREADTH, and breadth is why the directional test was believable
and the spread test was not. One index, one structure, monthly, gives ~12
observations a year no matter how long you run it. Fifty tickers with daily
signals give thousands. The same property that makes a test conclusive —
many weakly-correlated observations — is also what makes a portfolio survivable.
Prefer research designs that produce many independent observations. It is better
science and better risk management, and it is the same decision.

Correlated observations do NOT count once each: overlapping windows, or fifty
tickers that all move together, carry far less information than their row count
suggests. --corr discounts for that, and it is the difference between 2,491
daily VRP observations and the ~119 independent ones vrp_check actually had.
"""
from __future__ import annotations

import argparse
import math
import sys

# Two-sided test at alpha, with the stated power.
Z_ALPHA = {0.10: 1.6449, 0.05: 1.9600, 0.01: 2.5758}
Z_POWER = {0.80: 0.8416, 0.90: 1.2816, 0.95: 1.6449}


def _z(table: dict, key: float, name: str) -> float:
    if key not in table:
        raise SystemExit(f"{name} must be one of {sorted(table)}; got {key}")
    return table[key]


def effective_n(n: int, corr: float = 0.0, block: int = 1) -> float:
    """
    How many INDEPENDENT observations n correlated ones are worth.

    Two ways to lose independence, and this repo has hit both:
      - overlapping windows (vrp_check: 2,491 daily rows, 21-session forward
        window, ~119 independent) -> pass block=21
      - cross-sectional correlation (fifty tickers that all fall together)
        -> pass corr

    The block correction is n/block. The correlation correction is the standard
    design effect for equicorrelated observations, 1 + (n-1)*corr.
    """
    if block > 1:
        n = n / block
    if corr > 0:
        n = n / (1.0 + (n - 1) * corr)
    return max(1.0, float(n))


def span_tstat(mu_per_month: float, sd_per_month: float, years: float,
               per_year: float = 12.0) -> float:
    """
    Expected t-statistic for a strategy observed over a fixed CALENDAR span.

    Deliberately takes `per_year` and deliberately ignores it in the answer,
    because the point is that it does not matter. Effect grows linearly with
    time, noise with its square root, so sampling more often shrinks the
    per-observation signal exactly as fast as it multiplies the observations.
    Over 20 years at 0.75%/month and 6% sd, monthly / weekly / daily sampling
    all give t = 1.936.

    See tstat_is_frequency_invariant() in the selftest for the proof.
    """
    scale = 12.0 / per_year
    n = years * per_year
    mu = mu_per_month * scale
    sd = sd_per_month * math.sqrt(scale)
    if sd <= 0 or n < 2:
        return float("nan")
    return mu / (sd / math.sqrt(n))


def years_needed(mu_per_month: float, sd_per_month: float, *,
                 alpha: float = 0.05, power: float = 0.80) -> float:
    """Calendar years required — the only lever that actually moves power."""
    if mu_per_month <= 0:
        raise SystemExit("mu_per_month must be positive; it is a size, not a sign")
    z = _z(Z_ALPHA, alpha, "alpha") + _z(Z_POWER, power, "power")
    months = (z * sd_per_month / mu_per_month) ** 2
    return months / 12.0


def mde(sd: float, n: float, *, alpha: float = 0.05, power: float = 0.80) -> float:
    """Smallest true effect this test would detect, in the units of sd."""
    return (_z(Z_ALPHA, alpha, "alpha") + _z(Z_POWER, power, "power")) \
        * sd / math.sqrt(n)


def required_n(sd: float, effect: float, *, alpha: float = 0.05,
               power: float = 0.80) -> int:
    """Observations needed to detect `effect` at the stated power."""
    if effect <= 0:
        raise SystemExit("--effect must be positive; it is a size, not a sign")
    z = _z(Z_ALPHA, alpha, "alpha") + _z(Z_POWER, power, "power")
    return math.ceil((z * sd / effect) ** 2)


def report(sd: float, n: int | None, effect: float | None, *, alpha: float,
           power: float, corr: float, block: int, per_year: float,
           strict: bool) -> int:
    print("=" * 72)
    print("POWER CHECK — can this test find what it is looking for?")
    print("=" * 72)
    print(f"  sd per observation : {sd:.4g}")
    print(f"  alpha / power      : {alpha} / {power}")

    eff = None
    if n is not None:
        eff = effective_n(n, corr=corr, block=block)
        print(f"  observations       : {n:,}", end="")
        if eff != float(n):
            print(f"  ->  {eff:,.0f} independent", end="")
            if block > 1:
                print(f" (overlap block {block})", end="")
            if corr > 0:
                print(f" (corr {corr})", end="")
        print()

    verdict_ok = True
    print()
    if eff is not None:
        m = mde(sd, eff, alpha=alpha, power=power)
        print(f"  SMALLEST DETECTABLE EFFECT : {m:+.4g} per observation")
        if per_year:
            print(f"                               {m * per_year:+.4g} per year "
                  f"at {per_year:g} observations/year")
        if effect is not None:
            print(f"  effect you are looking for : {effect:+.4g}")
            if effect < m:
                verdict_ok = False
                print()
                print(f"  UNDERPOWERED. This test cannot distinguish an effect of")
                print(f"  {effect:.4g} from zero. If it comes back 'not significant',")
                print(f"  that says nothing about whether the edge exists — it is")
                print(f"  the same answer the test gives for a true zero.")
            else:
                print()
                print(f"  ADEQUATE. An effect of {effect:.4g} would be detected.")

    if effect is not None:
        need = required_n(sd, abs(effect), alpha=alpha, power=power)
        raw = need
        if block > 1:
            raw = need * block
        print()
        print(f"  observations needed for {effect:+.4g} : {need:,} independent", end="")
        if raw != need:
            print(f" ({raw:,} raw at block {block})", end="")
        print()
        if per_year:
            print(f"  at {per_year:g}/year that is {raw / per_year:,.1f} years of data")

    print("=" * 72)
    if not verdict_ok:
        print("  A test that cannot find the thing is not evidence about the thing.")
        print("  Widen the sample (more instruments, more observations) or do not")
        print("  run it — do NOT run it and then read the result.")
        print("=" * 72)
    return 1 if (strict and not verdict_ok) else 0


def selftest() -> int:
    print("power_check.py selftest")
    print("=" * 66)

    # ── the arithmetic, against a case with a hand-checkable answer ──
    # z = 1.96 + 0.8416 = 2.8016; sd 1, n 100 -> 0.28016
    m = mde(1.0, 100)
    assert abs(m - 0.28016) < 1e-4, m
    # and it must round-trip
    assert required_n(1.0, m) in (100, 101), required_n(1.0, m)
    print(f"round trip       : mde(sd=1, n=100) = {m:.5f}, back to n = "
          f"{required_n(1.0, m)}")

    # ── more data must lower the bar; more noise must raise it ──
    assert mde(1.0, 400) < mde(1.0, 100), "quadrupling n must halve the MDE"
    assert abs(mde(1.0, 400) * 2 - mde(1.0, 100)) < 1e-9, "and by exactly half"
    assert mde(2.0, 100) > mde(1.0, 100), "more noise must raise the bar"
    assert mde(1.0, 100, power=0.90) > mde(1.0, 100, power=0.80), \
        "demanding more power must raise the bar"
    print("monotonicity     : 4x data halves it; noise and power raise it")

    # ── overlap and correlation must DISCOUNT, and by the right amount ──
    assert effective_n(2491, block=21) == 2491 / 21, "block must divide"
    assert abs(effective_n(2491, block=21) - 118.6) < 0.1, \
        "vrp_check's 2,491 rows are ~119 independent windows"
    assert effective_n(50, corr=0.9) < 2.0, \
        "fifty names at 0.9 correlation are worth barely more than one"
    assert effective_n(100, corr=0.0) == 100.0, "zero correlation must not discount"
    print(f"independence     : 2,491 rows at block 21 -> "
          f"{effective_n(2491, block=21):.0f}; 50 names at corr 0.9 -> "
          f"{effective_n(50, corr=0.9):.1f}")

    # ── the real cases this module was written for ──
    spread_sd, spread_n = 4.90, 83
    spread_mde = mde(spread_sd, spread_n)
    assert spread_mde * 12 > 15.0, (
        f"spread_backtest must come back badly underpowered; got "
        f"{spread_mde * 12:.1f}%/yr")
    dir_mde = mde(1.0, 1386)
    assert dir_mde < 0.08, f"the directional test was adequately powered, {dir_mde}"
    print(f"the two cases    : spread {spread_mde * 12:+.1f}%/yr detectable "
          f"(hopeless), directional {dir_mde:.3f} R (adequate)")

    # ── sampling frequency buys NOTHING, which is the load-bearing claim ──
    # Written as a test because it is counter-intuitive and because acting on
    # the opposite belief — "rebalance weekly for 4x the data points" — is a
    # way to feel better about an underpowered study without improving it.
    base = span_tstat(0.75, 6.0, 20, per_year=12)
    for per_year in (12, 52, 252, 1000):
        t = span_tstat(0.75, 6.0, 20, per_year=per_year)
        assert abs(t - base) < 1e-9, (
            f"t-stat must be invariant to sampling frequency: {per_year}/yr gave "
            f"{t:.6f} vs monthly {base:.6f}. If this ever differs, the scaling "
            f"is wrong and the module is telling people to over-sample")
    # ...while the three things that DO move it, move it.
    # t grows with the SQUARE ROOT of calendar time, which is the whole reason
    # these studies need decades. A first draft asserted "16x the time must 4x
    # the t-stat" while comparing 80 years to 20 — that is 4x the time and so
    # exactly 2x the t-stat, and the assertion failed on its own arithmetic.
    # Pin the relationship rather than a remembered multiple.
    for mult in (4, 16, 25):
        got = span_tstat(0.75, 6.0, 20 * mult) / span_tstat(0.75, 6.0, 20)
        assert abs(got - math.sqrt(mult)) < 1e-9, (
            f"{mult}x the calendar time must give exactly sqrt({mult}) = "
            f"{math.sqrt(mult):.3f}x the t-stat, got {got:.3f}")
    assert span_tstat(0.75, 3.0, 20) > span_tstat(0.75, 6.0, 20), \
        "halving volatility must raise it"
    assert span_tstat(1.50, 6.0, 20) > span_tstat(0.75, 6.0, 20), \
        "doubling the effect must raise it"
    print(f"frequency         : t = {base:.3f} at 12, 52, 252 and 1000 obs/year "
          f"— identical")

    # ── the momentum veto, pinned as a case ──
    # Cross-sectional momentum: 0.5-1.0%/month premium, 5-8% monthly sd.
    # This is why it cannot be settled with the data this project can reach.
    assert years_needed(0.75, 6.0) > 40, (
        f"a 0.75%/month effect at 6% sd needs "
        f"{years_needed(0.75, 6.0):.0f} years — if this ever reads as feasible, "
        f"re-derive it before believing a momentum result")
    assert years_needed(1.00, 5.0) < 20, \
        "the optimistic corner should be reachable, or the bar is unfalsifiable"
    print(f"momentum veto     : 0.75%/mo at 6% sd needs "
          f"{years_needed(0.75, 6.0):.0f} years; the optimistic corner "
          f"(1.0%/mo, 5% sd) needs {years_needed(1.0, 5.0):.0f}")

    # ── --strict must actually refuse, and only when it should ──
    import io, contextlib
    def _run(**kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = report(**kw)
        return rc, buf.getvalue()
    base = dict(sd=4.9, n=83, alpha=0.05, power=0.80, corr=0.0, block=1,
                per_year=12, strict=True)
    rc, out = _run(effect=0.5, **base)
    assert rc == 1 and "UNDERPOWERED" in out, (rc, out[-200:])
    rc, out = _run(effect=3.0, **base)
    assert rc == 0 and "ADEQUATE" in out, (rc, out[-200:])
    rc, _ = _run(effect=0.5, **{**base, "strict": False})
    assert rc == 0, "without --strict it must report, not refuse"
    print("strict mode      : refuses a hopeless test, passes an adequate one")

    print("=" * 66)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sd", type=float, help="stdev of ONE observation")
    ap.add_argument("--n", type=int, help="observations the test will have")
    ap.add_argument("--effect", type=float, help="the edge you hope to find")
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--power", type=float, default=0.80)
    ap.add_argument("--corr", type=float, default=0.0,
                    help="average correlation between observations")
    ap.add_argument("--block", type=int, default=1,
                    help="overlap: each observation shares data with block-1 others")
    ap.add_argument("--per-year", type=float, default=0.0,
                    help="observations per year, to annualise the answer")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if the test cannot detect --effect")
    ap.add_argument("--mu-month", type=float,
                    help="expected effect per month, for a calendar-span check")
    ap.add_argument("--sd-month", type=float,
                    help="strategy stdev per month")
    ap.add_argument("--span-years", type=float,
                    help="calendar years of data available")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if a.mu_month is not None and a.sd_month is not None:
        need = years_needed(a.mu_month, a.sd_month, alpha=a.alpha, power=a.power)
        print("=" * 72)
        print("CALENDAR-SPAN CHECK — for a strategy measured as a periodic return")
        print("=" * 72)
        print(f"  effect {a.mu_month:+.3f}%/month, sd {a.sd_month:.2f}%/month")
        print(f"  YEARS NEEDED : {need:,.0f}")
        if a.span_years:
            t = span_tstat(a.mu_month, a.sd_month, a.span_years)
            print(f"  you have     : {a.span_years:,.0f} years -> expected t = {t:.2f}")
            print()
            if need > a.span_years:
                print(f"  UNDERPOWERED by {need / a.span_years:.1f}x. Rebalancing more")
                print(f"  often does NOT help — the t-stat is invariant to sampling")
                print(f"  frequency over a fixed span. Only more calendar time, less")
                print(f"  volatility, or a bigger effect moves it.")
                print("=" * 72)
                return 1 if a.strict else 0
            print("  ADEQUATE.")
        print("=" * 72)
        return 0
    if a.sd is None or (a.n is None and a.effect is None):
        ap.error("need --sd, and at least one of --n / --effect")
    return report(a.sd, a.n, a.effect, alpha=a.alpha, power=a.power,
                  corr=a.corr, block=a.block, per_year=a.per_year,
                  strict=a.strict)


if __name__ == "__main__":
    sys.exit(main())
