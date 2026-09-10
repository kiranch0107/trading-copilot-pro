"""
Post-earnings announcement drift — does it survive on data we can actually get?

THE MECHANISM, WRITTEN FIRST, BECAUSE THAT IS THE NEW RULE

Every prior test in this repo that started from a chart pattern found nothing.
The one that started from a REASON — vrp_check, "people overpay for crash
insurance" — found something real. So the reason comes first here:

    When a company reports earnings far from expectations, the price does not
    fully adjust that day. Analysts revise slowly, index and mandate-bound
    funds cannot react immediately, and attention is finite. The information
    diffuses over weeks rather than instantly, so prices keep drifting in the
    direction of the surprise.

    Who is on the other side and why do they lose? Sellers into a positive
    surprise who anchor on the pre-announcement price, and holders who under-
    react because updating is costly. This is a limits-to-attention story with
    forty years of literature behind it, not a pattern found by sweeping.

    It is also HEAVILY researched, which cuts the other way: the effect has
    decayed markedly since the 1990s. The bar below is set at the decayed
    modern size, not the textbook one.

THE LOOKAHEAD TRAP, AND WHY THE TIMESTAMP IS LOAD-BEARING

yfinance returns an announcement TIME, not just a date, and it decides
everything:

    16:00:00-04:00  after the close  -> first tradeable price is D+1's open
    08:00:00-04:00  before the open  -> first tradeable price is D's open

Every row in the AAPL sample is 16:00. A study that enters at day D's open or
close for those events is trading on information that was not public, and would
manufacture the entire effect out of nothing. entry_index() below is the only
place that decision is made, and its selftest checks both cases plus the
boundary.

SUE, NOT RAW SURPRISE

The classic measure is standardised unexpected earnings: the surprise divided by
the dispersion of that company's own past surprises. A 5% beat means something
different for a company that always lands within 1% than for one that swings
20%. Critically, the dispersion is computed from PRIOR quarters only — using the
full-sample stdev would leak the future into the sort.

WHAT THIS CANNOT FIX

Survivorship. The universe is companies that exist and are listed TODAY, so
firms that were delisted are absent. For a long-short SUE spread this is milder
than for a long-only test — both legs are drawn from survivors — but it is not
zero: if delisted firms were disproportionately negative-surprise, the short leg
is missing its worst names and the spread is OVERSTATED. Recorded, not fixable
with this data source.

PRE-REGISTERED, BEFORE THE FIRST RUN — 2026-09-10

    PEAD is REAL and worth pursuing only if ALL of:
      1. the top-minus-bottom SUE quintile spread is positive, and significant
         with standard errors CLUSTERED BY ANNOUNCEMENT QUARTER — events cluster
         in earnings season and share market moves, so unclustered SEs would
         overstate significance the same way the iid interval did in vrp_check
      2. adequately powered on its own event count (power_check)
      3. monotone across quintiles: Spearman rho >= MIN_RHO. A real diffusion
         effect grades with the size of the surprise. A spike in the top
         quintile alone is a lucky bucket, the same failure the ADX sweep had.
      4. the spread survives a realistic round-trip cost

    Anything else is INCONCLUSIVE or FAIL.
"""
from __future__ import annotations

import argparse
import math
import sys

import numpy as np
import pandas as pd

import power_check as pw

HOLD_SESSIONS = 60          # the classic PEAD window
MIN_PRIOR_QUARTERS = 4      # before SUE can be standardised
N_QUINTILES = 5
MIN_RHO = 0.6
MIN_EVENTS = 400
ROUND_TRIP_COST_PCT = 0.10  # shares, both sides; near-zero commission era
BENCHMARK = "SPY"
ALPHA = 0.05


def entry_index(bars: pd.DataFrame, announced: pd.Timestamp) -> int | None:
    """
    First index whose OPEN is tradeable on public information.

    THE decision in this module. An after-close announcement (>= 16:00 ET) is
    not tradeable until the next session's open. A before-open announcement
    (< 09:30 ET) is tradeable at that same session's open. Anything in between
    is intraday: treated as after-close, the conservative choice, because
    entering at that day's open would precede the news.
    """
    ts = pd.Timestamp(announced)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("America/New_York")
    day = ts.normalize().tz_localize(None) if ts.tzinfo else ts.normalize()

    before_open = (ts.hour < 9) or (ts.hour == 9 and ts.minute < 30)
    dates = bars["Date"].values

    if before_open:
        idx = np.searchsorted(dates, np.datetime64(day), side="left")
    else:
        idx = np.searchsorted(dates, np.datetime64(day), side="right")
    return int(idx) if idx < len(dates) else None


def sue_series(surprises: pd.Series, min_prior: int = MIN_PRIOR_QUARTERS) -> pd.Series:
    """
    Standardised unexpected earnings, using ONLY prior quarters for the scale.

    surprises must be ordered oldest-first. The divisor for row i is the stdev
    of rows [0, i), so no future information enters the sort. Rows without
    enough history return NaN and are dropped rather than back-filled.
    """
    out = []
    for i in range(len(surprises)):
        prior = surprises.iloc[:i].dropna()
        if len(prior) < min_prior:
            out.append(np.nan)
            continue
        sd = float(prior.std(ddof=1))
        out.append(np.nan if sd <= 0 else float(surprises.iloc[i]) / sd)
    return pd.Series(out, index=surprises.index, dtype=float)


def cluster_se(values: np.ndarray, clusters: np.ndarray) -> float:
    """
    Cluster-robust standard error of the MEAN.

    Earnings land in a handful of weeks each quarter, so events in one season
    share the market's moves over their whole holding window. Treating them as
    independent overstates significance — the same error the iid interval made
    in vrp_check, in a different disguise. This sums residuals WITHIN each
    cluster before squaring, which is what makes shared moves count once.
    """
    v = np.asarray(values, dtype=float)
    n = len(v)
    if n < 2:
        return float("nan")
    resid = v - v.mean()
    total = 0.0
    for g in np.unique(clusters):
        total += float(resid[clusters == g].sum()) ** 2
    if total <= 0:
        return float(v.std(ddof=1) / math.sqrt(n))
    return math.sqrt(total) / n


def quintile_stats(events: pd.DataFrame, n_q: int = N_QUINTILES) -> dict:
    """Sort by SUE, measure abnormal return per bucket, and the top-minus-bottom."""
    ev = events.dropna(subset=["sue", "abnormal"]).copy()
    if len(ev) < n_q * 2:
        return {}
    ev["bucket"] = pd.qcut(ev["sue"], n_q, labels=False, duplicates="drop")

    rows = []
    for b in sorted(ev["bucket"].dropna().unique()):
        sub = ev[ev["bucket"] == b]
        rows.append({"bucket": int(b), "n": len(sub),
                     "mean": float(sub["abnormal"].mean()),
                     "sue": float(sub["sue"].mean())})

    top = ev[ev["bucket"] == ev["bucket"].max()]
    bot = ev[ev["bucket"] == ev["bucket"].min()]
    # Long the top bucket, short the bottom. Each leg's events keep their own
    # cohort so the spread's SE is clustered too.
    paired = np.concatenate([top["abnormal"].to_numpy(),
                             -bot["abnormal"].to_numpy()])
    coh = np.concatenate([top["cohort"].to_numpy(), bot["cohort"].to_numpy()])
    spread = float(paired.mean())
    se = cluster_se(paired, coh)
    se_naive = float(paired.std(ddof=1) / math.sqrt(len(paired)))

    return {"rows": rows, "spread": spread, "se": se, "se_naive": se_naive,
            "n": len(paired), "n_cohorts": int(len(np.unique(coh))),
            "sd": float(paired.std(ddof=1))}


def verdict(q: dict) -> tuple[bool, list[str]]:
    """The pre-registered bar, applied."""
    reasons = []
    if not q:
        return False, ["too few events to form quintiles"]

    net = q["spread"] - ROUND_TRIP_COST_PCT
    lo = q["spread"] - 1.96 * q["se"]
    if not lo > 0:
        reasons.append(f"clustered 95% CI lower bound {lo:+.2f}% does not clear "
                       f"zero (spread {q['spread']:+.2f}%, SE {q['se']:.3f})")
    if q["n"] < MIN_EVENTS:
        reasons.append(f"{q['n']} events, under the {MIN_EVENTS} floor")
    detect = pw.mde(q["sd"], q["n"])
    if abs(q["spread"]) < detect:
        reasons.append(f"spread {q['spread']:+.2f}% is below what {q['n']} events "
                       f"can detect ({detect:.2f}%) — INCONCLUSIVE, not 'no effect'")
    means = [r["mean"] for r in q["rows"]]
    rho = _spearman(list(range(len(means))), means)
    q["rho"] = rho
    if rho < MIN_RHO:
        reasons.append(f"no dose-response: Spearman rho {rho:+.2f} < {MIN_RHO} — "
                       f"a lucky bucket, not a diffusion effect")
    if net <= 0:
        reasons.append(f"net of {ROUND_TRIP_COST_PCT:.2f}% round-trip cost the "
                       f"spread is {net:+.2f}%")
    return (not reasons), reasons


def _spearman(x, y) -> float:
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos + 1.0
        return r
    rx, ry = rank(list(x)), rank(list(y))
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else 0.0


def collect(tickers: list[str], years: int) -> pd.DataFrame:
    """
    Build the event table. Network-bound; every modelling choice above is
    already made, so this only joins announcements to prices.
    """
    import backtest as bt
    import yfinance as yf

    bench = bt.download(BENCHMARK, years)
    if bench is None or bench.empty:
        print(f"  ! no {BENCHMARK} bars — cannot market-adjust", file=sys.stderr)
        return pd.DataFrame()
    bench = bench.dropna(subset=["Close"]).copy()
    bench["Date"] = pd.to_datetime(bench["Date"]).dt.normalize()
    bench = bench.set_index("Date").sort_index()["Close"]

    rows = []
    for tk in tickers:
        bars = bt.download(tk, years)
        if bars is None or bars.empty:
            print(f"  ! {tk}: no bars", file=sys.stderr)
            continue
        bars = bars.dropna(subset=["Open", "Close"]).copy()
        bars["Date"] = pd.to_datetime(bars["Date"]).dt.normalize()
        bars = bars.sort_values("Date").reset_index(drop=True)

        try:
            ed = yf.Ticker(tk).earnings_dates
        except Exception as e:                       # noqa: BLE001
            print(f"  ! {tk}: earnings fetch failed ({e})", file=sys.stderr)
            continue
        if ed is None or ed.empty or "Surprise(%)" not in ed.columns:
            print(f"  ! {tk}: no surprise column", file=sys.stderr)
            continue

        ed = ed.sort_index()                          # oldest first
        ed = ed[ed["Surprise(%)"].notna()]
        if len(ed) <= MIN_PRIOR_QUARTERS:
            print(f"  ! {tk}: only {len(ed)} usable quarters", file=sys.stderr)
            continue
        sues = sue_series(ed["Surprise(%)"])

        used = 0
        for announced, sue in zip(ed.index, sues):
            if not np.isfinite(sue):
                continue
            i = entry_index(bars, announced)
            if i is None or i + HOLD_SESSIONS >= len(bars):
                continue
            entry = float(bars["Open"].iloc[i])
            exit_ = float(bars["Close"].iloc[i + HOLD_SESSIONS])
            if entry <= 0:
                continue
            raw = (exit_ - entry) / entry * 100.0

            d0, d1 = bars["Date"].iloc[i], bars["Date"].iloc[i + HOLD_SESSIONS]
            try:
                b0 = float(bench.asof(d0))
                b1 = float(bench.asof(d1))
            except Exception:                         # noqa: BLE001
                continue
            if not (np.isfinite(b0) and np.isfinite(b1) and b0 > 0):
                continue
            mkt = (b1 - b0) / b0 * 100.0

            ts = pd.Timestamp(announced)
            rows.append({
                "ticker": tk, "announced": ts, "sue": float(sue),
                "raw": raw, "market": mkt, "abnormal": raw - mkt,
                "cohort": f"{ts.year}Q{(ts.month - 1) // 3 + 1}",
            })
            used += 1
        print(f"  {tk:<6} {used:>3} events", file=sys.stderr)

    return pd.DataFrame(rows)


def report(events: pd.DataFrame) -> int:
    q = quintile_stats(events)
    print("=" * 78)
    print("POST-EARNINGS ANNOUNCEMENT DRIFT")
    print("=" * 78)
    print(f"  events          : {len(events):,} across "
          f"{events['ticker'].nunique() if len(events) else 0} tickers")
    print(f"  hold            : {HOLD_SESSIONS} sessions from the first tradeable open")
    print(f"  return          : market-adjusted vs {BENCHMARK}")
    print(f"  sort            : SUE, scaled by PRIOR quarters only")
    print("=" * 78)
    if not q:
        print("  too few events to form quintiles — nothing measured")
        return 2

    print(f"\n  {'quintile':>9} {'events':>7} {'mean SUE':>9} {'abnormal %':>11}")
    print("  " + "-" * 40)
    for r in q["rows"]:
        print(f"  {r['bucket'] + 1:>9} {r['n']:>7} {r['sue']:>+9.2f} "
              f"{r['mean']:>+11.2f}")

    ok, why = verdict(q)
    lo = q["spread"] - 1.96 * q["se"]
    hi = q["spread"] + 1.96 * q["se"]
    lo_n = q["spread"] - 1.96 * q["se_naive"]
    hi_n = q["spread"] + 1.96 * q["se_naive"]
    print()
    print(f"  TOP MINUS BOTTOM : {q['spread']:+.2f}%  over {HOLD_SESSIONS} sessions")
    print(f"  95% CI clustered : [{lo:+.2f}, {hi:+.2f}]   "
          f"({q['n_cohorts']} earnings-season cohorts)")
    print(f"  95% CI unclustered: [{lo_n:+.2f}, {hi_n:+.2f}]   "
          f"(WRONG here: events cluster in season)")
    print(f"  dose-response    : Spearman rho {q.get('rho', float('nan')):+.2f}"
          f"   bar >= {MIN_RHO}")
    print(f"  net of costs     : {q['spread'] - ROUND_TRIP_COST_PCT:+.2f}%")
    print()
    print("=" * 78)
    if ok:
        print("  PASS — the drift is present, graded, clustered-significant, and")
        print("  survives costs. Worth building a strategy against.")
    else:
        print("  NOT ESTABLISHED")
        for w in why:
            print(f"    - {w}")
        print()
        print("  Survivorship note: the universe is companies listed TODAY. If")
        print("  delisted firms skewed negative-surprise, the short leg is missing")
        print("  its worst names and this spread is if anything OVERSTATED.")
    print("=" * 78)
    return 0 if ok else 1


def selftest() -> int:
    print("pead_study.py selftest")
    print("=" * 70)

    bars = pd.DataFrame({
        "Date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04",
                                "2024-01-05", "2024-01-08"]),
        "Open": [100.0, 101.0, 102.0, 103.0, 104.0],
        "Close": [100.5, 101.5, 102.5, 103.5, 104.5]})

    # ── THE trap: after-close must not trade that day's open ──
    amc = pd.Timestamp("2024-01-03 16:00:00-05:00")
    bmo = pd.Timestamp("2024-01-03 08:00:00-05:00")
    i_amc, i_bmo = entry_index(bars, amc), entry_index(bars, bmo)
    assert bars["Date"].iloc[i_bmo] == pd.Timestamp("2024-01-03"), \
        "a before-open report IS tradeable at that day's open"
    assert bars["Date"].iloc[i_amc] == pd.Timestamp("2024-01-04"), (
        f"an AFTER-CLOSE report must wait for the NEXT open, got "
        f"{bars['Date'].iloc[i_amc].date()} — entering on the announcement day "
        f"trades on information that was not public and fabricates the effect")
    assert i_amc == i_bmo + 1, "the two cases must differ by exactly one session"
    # the boundary: 09:29 is before the open, 09:31 is not
    early = entry_index(bars, pd.Timestamp("2024-01-03 09:29:00-05:00"))
    late = entry_index(bars, pd.Timestamp("2024-01-03 09:31:00-05:00"))
    assert early == i_bmo and late == i_amc, (early, late)
    print(f"entry timing     : BMO -> {bars['Date'].iloc[i_bmo].date()}, "
          f"AMC -> {bars['Date'].iloc[i_amc].date()}, 09:30 boundary held")

    # ── SUE must not see the future ──
    s = pd.Series([1.0, -1.0, 2.0, -2.0, 10.0, 0.0])
    sue = sue_series(s, min_prior=4)
    assert sue.iloc[:4].isna().all(), "no SUE before enough prior quarters"
    assert np.isfinite(sue.iloc[4]), "the 5th has four priors and must compute"
    # the huge 5th surprise must NOT shrink its own denominator
    scale_at_4 = float(s.iloc[:4].std(ddof=1))
    assert abs(sue.iloc[4] - s.iloc[4] / scale_at_4) < 1e-9, (
        "SUE at i must divide by the stdev of rows BEFORE i — using the full "
        "sample would leak the future into the sort")
    print(f"SUE lookahead    : first 4 NaN; row 5 = {sue.iloc[4]:.2f}, scaled by "
          f"priors only")

    # ── clustering must widen when events share a cohort ──
    rng = np.random.default_rng(0)
    shared = np.concatenate([rng.normal(0, 1, 50) + 3.0,
                             rng.normal(0, 1, 50) - 3.0])
    one_per = np.arange(100)
    two_big = np.array([0] * 50 + [1] * 50)
    se_indep = cluster_se(shared, one_per)
    se_clust = cluster_se(shared, two_big)
    assert se_clust > 2 * se_indep, (
        f"two big cohorts carrying opposite means must widen the SE far beyond "
        f"100 singleton clusters ({se_clust:.3f} vs {se_indep:.3f})")
    print(f"clustering       : 100 singletons SE {se_indep:.3f} -> "
          f"2 cohorts SE {se_clust:.3f}")

    # ── the bar: every clause fails on its own, and a real effect passes ──
    def _q(spread, se, n, sd, means):
        return {"spread": spread, "se": se, "se_naive": se / 3, "n": n, "sd": sd,
                "n_cohorts": 20,
                "rows": [{"bucket": i, "n": n // 5, "mean": m, "sue": i - 2.0}
                         for i, m in enumerate(means)]}
    graded = [-1.5, -0.5, 0.2, 1.0, 2.0]
    good = _q(3.5, 0.5, 1200, 15.0, graded)
    ok, why = verdict(good)
    assert ok, f"a graded, clustered-significant, cost-surviving effect must pass: {why}"
    for bad, expect in (
        (_q(3.5, 3.0, 1200, 15.0, graded), "does not clear"),
        (_q(3.5, 0.5, 100, 15.0, graded), "floor"),
        (_q(3.5, 0.5, 1200, 15.0, [2.0, -0.5, 0.2, 1.0, -1.5]), "dose-response"),
        (_q(0.05, 0.001, 1200, 0.2, graded), "round-trip"),
    ):
        ok2, why2 = verdict(bad)
        assert not ok2, (expect, "should have failed")
        assert any(expect in w for w in why2), (expect, why2)
    print("pre-registered bar: passes a graded effect; fails CI / floor / "
          "dose-response / costs")

    # LIVENESS: an all-bad result must trip more than one clause.
    dead = verdict(_q(0.01, 5.0, 50, 30.0, [2.0, 1.0, 0.0, -1.0, -2.0]))
    assert dead[0] is False and len(dead[1]) >= 3, dead
    print(f"bar liveness     : an all-bad result trips {len(dead[1])} clauses")

    # ── quintile_stats end to end, with a KNOWN answer ──
    n = 500
    rs = np.random.default_rng(7)
    sue_v = rs.normal(0, 1, n)
    ev = pd.DataFrame({"sue": sue_v,
                       "abnormal": 2.0 * sue_v + rs.normal(0, 1, n),
                       "cohort": [f"2024Q{i % 4 + 1}" for i in range(n)]})
    q = quintile_stats(ev)
    assert q["spread"] > 0, "a planted positive relationship must show up"
    assert q["rows"][0]["mean"] < q["rows"][-1]["mean"], "and must be graded"
    assert q["se"] > 0
    print(f"engine           : planted +2.0/SUE -> spread {q['spread']:+.2f}%, "
          f"graded {q['rows'][0]['mean']:+.1f} to {q['rows'][-1]['mean']:+.1f}")

    print("=" * 70)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default=(
        "AAPL,MSFT,NVDA,AMZN,META,GOOGL,TSLA,AMD,ORCL,CRM,ADBE,NFLX,"
        "AVGO,QCOM,TXN,INTC,MU,AMAT,NOW,PANW,LRCX,KLAC,CSCO,IBM,"
        "JPM,BAC,WFC,GS,MS,V,MA,AXP,UNH,JNJ,PFE,MRK,ABBV,LLY,"
        "WMT,COST,TGT,HD,LOW,NKE,SBUX,MCD,PG,KO,PEP,XOM"))
    ap.add_argument("--years", type=int, default=8)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    print(f"Collecting {len(tickers)} tickers — this makes one earnings call each "
          f"and will take a few minutes.", file=sys.stderr)
    events = collect(tickers, a.years)
    if events.empty:
        print("NOTHING MEASURED — do not read anything into this run.",
              file=sys.stderr)
        return 2
    return report(events)


if __name__ == "__main__":
    sys.exit(main())
