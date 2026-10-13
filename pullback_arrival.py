#!/usr/bin/env python3
"""
pullback_arrival.py — how often the pullback rule fires, counted on real history. NO OUTCOMES.
=============================================================================================

BACKLOG 31. The pullback bucket (results/pullback_preregistration.md) is a
FORWARD test: its R is read once, at 349 settled episodes, and never from
history, because every reserved tranche is spent. What history CAN say
without touching that is the ARRIVAL RATE: how many bars a day the rule
fires on across the pool, how many episodes that collapses to, how that
splits by SPY regime, and therefore how long the forward reading will take.
That is a plumbing and planning check, on the real frames the scanner
reads, and this module is built so it cannot be anything else:

    - it never simulates a trade, never reads a bar after the signal bar,
      never computes an R, a win, a loss or an exit (its selftest and
      consistency_check.check_pullback_arrival_outcome_free() grep for it);
    - it prints counts, dates, regimes and tickers, nothing priced after
      the signal.

Run on the laptop (Yahoo access; frozen in .bar_cache under its own key):

    python pullback_arrival.py | tee results/pullback_arrival_run1.txt
    python pullback_arrival.py --selftest          # offline
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict

import pandas as pd

import signal_core as sc
import pullback_signal as pbs

YEARS = 3                 # enough raw history for 100 indicator + 200 SMA warm-ups and a 1y window
WINDOW_SESSIONS = 252     # the last trading year, the window reported
EPISODES_REQUIRED = 349   # the pre-registration's reading gate, for the estimate


def regime_series(spy: pd.DataFrame) -> dict:
    """date -> Bull/Bear/Neutral, backtest.build_regime_series()'s rule
    (close against its 200-SMA), keyed by the bar's date."""
    import backtest as bt
    reg = bt.build_regime_series(spy)
    dates = sc._bar_dates(spy, None)
    return {str(d.date()): r for d, r in zip(dates, reg.tolist())}


def _with_index(df: pd.DataFrame) -> pd.DataFrame:
    """A cached frame carries Date as a column; the scanner's frames carry
    it as the index. Give this one the scanner's shape."""
    if "Date" in df.columns:
        d = df.copy()
        d.index = pd.DatetimeIndex(pd.to_datetime(d["Date"]))
        return d.drop(columns=["Date"])
    return df


def arrivals(ticker: str, raw: pd.DataFrame, compute, params, regimes: dict,
             window: int = WINDOW_SESSIONS) -> list[dict]:
    """
    Every bar in the last `window` sessions on which the pullback rule fires,
    as {"ticker", "date", "regime", "rr"}, plus the refusal tallies. The
    computed frame is built once (indicators are causal) and the rule is
    evaluated on each prefix, exactly as the scanner sees one bar at a time.
    Nothing after a signal bar is ever read for that bar.
    """
    raw = _with_index(raw)
    cdf = compute(raw)
    out, tally = [], Counter()
    start = max(0, len(cdf) - window)
    for i in range(start, len(cdf)):
        prefix = cdf.iloc[:i + 1]
        t: dict = {}
        sig = pbs.evaluate_pullback(raw, prefix, params, None, t)
        tally.update(t)
        if sig:
            d = str(sc._bar_dates(prefix, None)[-1].date())
            out.append({"ticker": ticker, "date": d, "regime": regimes.get(d, "Unknown"),
                        "rr": sig["rr"]})
    return out, tally


def episodes(rows: list[dict]) -> list[dict]:
    """Rows on consecutive SESSIONS for one ticker collapse to the first:
    the record's own unit. Consecutive here means adjacent in that ticker's
    own bar sequence, which is what the sorted dates give."""
    by: dict = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r["ticker"], r["date"])):
        by[r["ticker"]].append(r)
    out = []
    for tk, rs in by.items():
        prev = None
        for r in rs:
            d = pd.Timestamp(r["date"])
            if prev is None or (d - prev).days > 4:        # a gap of more than a long weekend
                out.append(r)
            prev = d
    return out


def report(all_rows: list[dict], tallies: Counter, names: int, sessions: int,
           fingerprint: str | None) -> int:
    eps = episodes(all_rows)
    print("=" * 78)
    print("PULLBACK ARRIVAL — counts only; no result, no R, nothing read after a signal bar")
    print("=" * 78)
    print(f"  names {names}   sessions in window {sessions}   fingerprint {fingerprint or 'n/a'}")
    print(f"  refusals: {dict(tallies)}")
    print(f"  signal bars {len(all_rows)}   episodes {len(eps)}   "
          f"= {len(all_rows) / max(1, sessions):.2f} bars/day, {len(eps) / max(1, sessions):.2f} episodes/day")
    by_reg = Counter(r["regime"] for r in eps)
    print("  episodes by SPY regime: " + ", ".join(f"{k} {v}" for k, v in sorted(by_reg.items())))
    by_month = Counter(r["date"][:7] for r in eps)
    print("  episodes by month: " + ", ".join(f"{k} {v}" for k, v in sorted(by_month.items())))
    top = Counter(r["ticker"] for r in eps).most_common(10)
    print("  most frequent names: " + ", ".join(f"{t} {n}" for t, n in top))
    rate = len(eps) / max(1, sessions)
    if rate > 0:
        print(f"  at this rate {EPISODES_REQUIRED} episodes take about {EPISODES_REQUIRED / rate:.0f} "
              f"sessions ({EPISODES_REQUIRED / rate / 21:.1f} months)")
    else:
        print("  the rule did not fire in the window: the forward bucket will record nothing")
    print("=" * 78)
    return 0


def run(tickers: list[str], years: int = YEARS) -> int:
    import backtest as bt
    import bar_cache
    from scanner import compute
    params = sc.DEFAULTS
    spy = bt.download("SPY", years)
    if spy is None or spy.empty:
        print("SPY unavailable; no regime, no run.")
        return 2
    regimes = regime_series(_with_index(spy))
    rows, tallies, metas, failed = [], Counter(), [], []
    sessions = 0
    for tk in tickers:
        try:
            raw = bt.download(tk, years)
            if raw is None or len(raw) < 320:
                failed.append(tk)
                continue
            r, t = arrivals(tk, raw, compute, params, regimes)
            rows += r
            tallies.update(t)
            sessions = max(sessions, min(WINDOW_SESSIONS, len(raw)))
            _df, meta = bar_cache.load(tk, "1d", years, "raw" if not bt.AUTO_ADJUST else "")
            if meta:
                metas.append(meta)
            print(f"  {tk}: {len(r)} signal bars", flush=True)
        except Exception as e:                           # noqa: BLE001
            print(f"  ! {tk}: FAILED: {type(e).__name__}: {e}", flush=True)
            failed.append(tk)
    if failed:
        print(f"  ! {len(failed)} name(s) produced nothing: {', '.join(failed)}")
    fp = bar_cache.fingerprint(metas) if metas else None
    return report(rows, tallies, len(tickers) - len(failed), sessions, fp)


def _universe() -> list[str]:
    import universe as u
    wl, _s, _a, _src = u.load_universe(["NVDA", "META", "MSFT"], allow_live=False)
    return sorted(set(u.CANDIDATE_POOL) | set(wl))


def selftest() -> int:
    from pathlib import Path
    from scanner import compute
    print("pullback_arrival.py selftest")
    print("=" * 72)
    src = Path(__file__).read_text(encoding="utf-8")
    body = src.split('"""', 2)[2]                       # everything after the module docstring
    # The words are assembled so this list is not itself a mention of them.
    for parts in (("simulate", "_trade"), ("resolve", "_signal"), ("out", "come"),
                  ("exit", "_price"), ("mean", "_r"), ("win", "_rate")):
        word = "".join(parts)
        assert word not in body, f"counts-only module mentions {word!r}"
    print("counts only      : no engine call, no result word, no R anywhere in the code")
    params = sc.DEFAULTS
    # A 300-bar uptrend then a 24-bar shallow pullback (pullback_signal's own
    # fixture shape): the rule fires on a run of consecutive bars = ONE episode.
    up = [100.0 * 1.002 ** i for i in range(300)]
    pb, px = [], up[-1]
    for k in range(24):
        px *= 0.992 if k % 2 == 0 else 1.003
        pb.append(px)
    raw = pbs._frame(up + pb).reset_index().rename(columns={"index": "Date"})
    spy = pbs._frame([100.0 * 1.001 ** i for i in range(324)]).reset_index().rename(columns={"index": "Date"})
    regimes = regime_series(_with_index(spy))
    assert set(regimes.values()) <= {"Bull", "Bear", "Neutral"} and regimes[max(regimes)] == "Bull"
    rows, tally = arrivals("SYN", raw, compute, params, regimes, window=60)
    assert rows and all(r["ticker"] == "SYN" and r["regime"] == "Bull" and r["rr"] >= params.min_rr for r in rows), rows[:2]
    assert tally.get("signals") == len(rows) and "below_sma200" not in tally, tally
    eps = episodes(rows)
    assert len(eps) == 1 and eps[0]["date"] == rows[0]["date"], (len(eps), "consecutive bars are one episode")
    assert set(rows[0].keys()) == {"ticker", "date", "regime", "rr"}, \
        "nothing priced after the signal leaves this module"
    # Below the average: refused and counted, no rows.
    down = [200.0 * 0.998 ** i for i in range(300)]
    pb2, px = [], down[-1]
    for k in range(24):
        px *= 0.992 if k % 2 == 0 else 1.003
        pb2.append(px)
    raw2 = pbs._frame(down + pb2).reset_index().rename(columns={"index": "Date"})
    rows2, tally2 = arrivals("DWN", raw2, compute, params, regimes, window=60)
    assert not rows2 and tally2.get("below_sma200", 0) >= 1, tally2
    # Consecutive sessions collapse to one episode; a gap starts another;
    # a weekend is not a gap; two tickers never merge. Explicit rows, so the
    # collapse itself is what is tested, whatever the shaped fixture fires.
    def _row(tk, day):
        return {"ticker": tk, "date": day, "regime": "Bull", "rr": 3.0}
    run1 = [_row("T", d) for d in ("2026-09-03", "2026-09-04", "2026-09-07", "2026-09-08")]   # Fri -> Mon
    run2 = [_row("T", d) for d in ("2026-09-21", "2026-09-22")]
    other = [_row("U", "2026-09-04")]
    eps2 = episodes(run1 + run2 + other)
    assert [(e["ticker"], e["date"]) for e in sorted(eps2, key=lambda e: (e["ticker"], e["date"]))] == \
        [("T", "2026-09-03"), ("T", "2026-09-21"), ("U", "2026-09-04")], eps2
    print(f"arrivals         : {len(rows)} signal bars -> 1 episode on the shaped pullback; refused and "
          f"counted below the SMA; separated runs are separate episodes")
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        report(rows, tally, 1, 60, "v2-test")
    txt = buf.getvalue()
    assert "episodes 1" in txt and "mean R" not in txt and " wins" not in txt and "loss" not in txt, txt
    print("report           : counts, regimes, months, names, time-to-349; no result line")
    print("=" * 72)
    print("All self-tests passed.")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    ap = argparse.ArgumentParser(description="pullback arrival rate on real history, counts only")
    ap.add_argument("--years", type=int, default=YEARS)
    ap.add_argument("--tickers", nargs="*", default=None, help="default: the candidate pool plus the watchlist")
    a = ap.parse_args()
    return run(a.tickers or _universe(), a.years)


if __name__ == "__main__":
    sys.exit(main())
