#!/usr/bin/env python3
"""
universe.py — rules-based universe selection

THE PROBLEM THIS FIXES
----------------------
A hand-picked watchlist has survivorship bias baked in. NVDA, META and MSFT
are on the list BECAUSE they already went up. Backtesting on them asks "would
this strategy have worked on the stocks I already know did well" — which is
hindsight leaking into the result, and it inflates every number.

This module picks the universe by rules applied at a point in time, using only
data available on that date. Run as of 2019 it would pick 2019's leaders, not
today's. That makes future backtests honest rather than flattering.

WHAT THIS IS AND IS NOT
-----------------------
This REMOVES A BIAS. It does not add an edge, and it is not a filter tuned to
improve returns. Ranking by relative strength is a universe-construction rule,
not a signal — the entry logic still has to earn its own keep, and the
591-trade out-of-sample test says it has not yet.

Resist tuning TOP_N or the lookback to improve backtest numbers. That converts
a bias fix back into the same parameter search that produced the false
positive. Pick defaults once, on reasoning rather than results, and leave them.

USAGE
-----
    python universe.py                      # today's universe, printed
    python universe.py --as-of 2024-06-01   # what it would have picked then
    python universe.py --selftest           # verify ranking math, no network
    python universe.py --json out.json

    from universe import select_universe
    tickers = select_universe()             # -> ["AVGO", "AMD", ...]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import date, datetime, timedelta

try:
    import pandas as pd
except ImportError:
    raise SystemExit("Missing pandas. Run: pip install pandas yfinance")


# ---------------------------------------------------------------------------
# PARAMETERS — chosen once, on reasoning. Do not tune these against results.
# ---------------------------------------------------------------------------
TOP_N = 8                  # how many names to trade
RS_LOOKBACK = 63           # ~3 months of sessions; long enough to be a trend,
                           # short enough to still be current
MIN_DOLLAR_VOLUME = 50e6   # liquidity floor: options need a real underlying
MIN_PRICE = 20.0           # sub-$20 names have poor option chains
MIN_HISTORY = 200          # sessions required, so the 200-SMA is meaningful
BENCHMARK = "SPY"
MAX_PER_SECTOR = 2         # risk control, not a performance knob — eight names
                           # that are all healthcare are not eight independent
                           # bets. Capping concentration is the point; do not
                           # tune this against backtest results.

# Static sector labels for the candidate pool. Static is fine here: a company's
# sector is stable on the timescale that matters, so this introduces no
# lookahead the way a price-derived label would.
SECTORS = {
    "AAPL": "Tech", "MSFT": "Tech", "GOOGL": "Tech", "AMZN": "ConsDisc",
    "META": "Tech", "NVDA": "Semis", "AVGO": "Semis", "ORCL": "Tech",
    "CRM": "Tech", "ADBE": "Tech", "AMD": "Semis", "QCOM": "Semis",
    "TXN": "Semis", "INTC": "Semis", "MU": "Semis", "AMAT": "Semis",
    "LRCX": "Semis", "KLAC": "Semis", "NOW": "Tech", "PANW": "Tech",
    "SNPS": "Tech", "CDNS": "Tech", "INTU": "Tech", "IBM": "Tech",
    "CSCO": "Tech",
    "NFLX": "Comms", "DIS": "Comms", "TSLA": "ConsDisc", "HD": "ConsDisc",
    "LOW": "ConsDisc", "NKE": "ConsDisc", "SBUX": "ConsDisc",
    "MCD": "ConsDisc", "COST": "Staples", "TGT": "ConsDisc",
    "WMT": "Staples", "PG": "Staples", "KO": "Staples", "PEP": "Staples",
    "JPM": "Financials", "BAC": "Financials", "GS": "Financials",
    "MS": "Financials", "V": "Financials", "MA": "Financials",
    "AXP": "Financials", "BLK": "Financials",
    "CAT": "Industrials", "DE": "Industrials", "HON": "Industrials",
    "GE": "Industrials", "BA": "Industrials", "UNP": "Industrials",
    "UPS": "Industrials",
    "UNH": "Health", "JNJ": "Health", "LLY": "Health", "ABBV": "Health",
    "MRK": "Health", "PFE": "Health", "TMO": "Health", "ABT": "Health",
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy", "SLB": "Energy",
}

# The candidate pool. This is the one judgement call that cannot be fully
# mechanised without a survivorship-free historical index membership feed
# (which yfinance does not provide). It is deliberately BROAD — the ranking
# does the selecting, not this list. Widening it is safe; hand-picking
# winners into it reintroduces exactly the bias this module exists to remove.
CANDIDATE_POOL = [
    # mega/large-cap tech
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "AVGO", "ORCL",
    "CRM", "ADBE", "AMD", "QCOM", "TXN", "INTC", "MU", "AMAT", "LRCX",
    "KLAC", "NOW", "PANW", "SNPS", "CDNS", "INTU", "IBM", "CSCO",
    # comms / consumer
    "NFLX", "DIS", "TSLA", "HD", "LOW", "NKE", "SBUX", "MCD", "COST",
    "TGT", "WMT", "PG", "KO", "PEP",
    # financials / industrials / health / energy
    "JPM", "BAC", "GS", "MS", "V", "MA", "AXP", "BLK",
    "CAT", "DE", "HON", "GE", "BA", "UNP", "UPS",
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT",
    "XOM", "CVX", "COP", "SLB",
]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def sessions_to_calendar_days(sessions: int) -> int:
    """
    Calendar days needed to contain `sessions` trading days, with slack for
    holidays. ~5 sessions per 7 calendar days, plus a 45-day cushion.

    This is the arithmetic that broke the first version: the window was sized
    from RS_LOOKBACK alone (167 days, ~119 sessions) while MIN_HISTORY demanded
    200, so every ticker including the benchmark was silently dropped for short
    history and the ranker reported "no SPY data".
    """
    return int(sessions * 7 / 5) + 45


def fetch_history(tickers: list[str], as_of: date,
                  lookback_sessions: int, verbose: bool = False) -> dict:
    """
    Daily bars ending on or before `as_of`.

    Everything downstream slices to `as_of`, so a backtest run for a past date
    never sees a bar that had not printed yet. That is the whole point — a
    universe built with future data would be worthless.

    The window must cover the LONGEST requirement, not just the RS lookback:
    the 200-SMA gate needs MIN_HISTORY sessions of its own.

    NOT routed through data_source.py's fallback (unlike app.py, scanner.py,
    exit_monitor.py and backtest.py). This fetches ~67 tickers in ONE batched
    yf.download() call; data_source.fetch_daily() is a per-ticker interface,
    and per-ticker fallback within a batch failure would need this function
    restructured, not just wrapped. If Yahoo is down, ranking simply fails
    and load_universe() falls back to the last committed snapshot or the
    static list (see its own docstring) — a real gap, left as a known
    follow-up rather than silently claimed as covered.
    """
    import yfinance as yf
    need_sessions = max(MIN_HISTORY, lookback_sessions + 1)
    start = as_of - timedelta(days=sessions_to_calendar_days(need_sessions))
    end = as_of + timedelta(days=1)
    if verbose:
        print(f"  window: {start} -> {as_of}  "
              f"(need {need_sessions} sessions)")

    out, stats = {}, {"missing": [], "short": [], "ok": 0}
    try:
        raw = yf.download(tickers, start=start, end=end, interval="1d",
                          progress=False, group_by="ticker", auto_adjust=False)
    except Exception as e:
        print(f"  ! batch download failed: {e}", file=sys.stderr)
        return out

    if raw is None or raw.empty:
        print("  ! download returned nothing at all — check connectivity",
              file=sys.stderr)
        return out

    for t in tickers:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if t not in raw.columns.get_level_values(0):
                    stats["missing"].append(t)
                    continue
                df = raw[t].copy()
            else:
                df = raw.copy()
            df = df.dropna(subset=["Close", "Volume"])
            if df.empty:
                stats["missing"].append(t)
                continue
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            df = df[df.index <= pd.Timestamp(as_of)]
            if len(df) < need_sessions:
                stats["short"].append((t, len(df)))
                continue
            out[t] = df
            stats["ok"] += 1
        except Exception:
            stats["missing"].append(t)

    if verbose or not out:
        print(f"  fetched: {stats['ok']} usable, "
              f"{len(stats['short'])} too short, {len(stats['missing'])} no data")
        if stats["short"]:
            worst = sorted(stats["short"], key=lambda x: x[1])[:5]
            detail = ", ".join(f"{t}={n}" for t, n in worst)
            print(f"  short-history examples (sessions returned): {detail}")
            print(f"  -> the fetch window is not covering {need_sessions} sessions")
    return out


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def score_ticker(df: pd.DataFrame, bench: pd.DataFrame,
                 lookback: int = RS_LOOKBACK) -> dict | None:
    """
    Relative strength against the benchmark over `lookback` sessions, plus the
    liquidity and trend gates.

    RS is a simple return spread, not a ratio — a ratio behaves badly when the
    benchmark return is near zero, which happens often enough to matter.
    """
    if len(df) < max(lookback + 1, MIN_HISTORY) or len(bench) < lookback + 1:
        return None

    close = df["Close"]
    price = float(close.iloc[-1])
    if price < MIN_PRICE:
        return None

    dollar_vol = float((close * df["Volume"]).tail(20).mean())
    if not math.isfinite(dollar_vol) or dollar_vol < MIN_DOLLAR_VOLUME:
        return None

    t_ret = price / float(close.iloc[-lookback - 1]) - 1
    b_ret = float(bench["Close"].iloc[-1]) / float(bench["Close"].iloc[-lookback - 1]) - 1
    rs = t_ret - b_ret

    sma200 = float(close.tail(200).mean())
    above_200 = price > sma200

    return {
        "price": round(price, 2),
        "dollar_vol_m": round(dollar_vol / 1e6, 1),
        "return": round(t_ret * 100, 2),
        "bench_return": round(b_ret * 100, 2),
        "rs": round(rs * 100, 2),
        "sma200": round(sma200, 2),
        "above_200sma": above_200,
    }


def select_universe(as_of: date | None = None, top_n: int = TOP_N,
                    pool: list[str] | None = None,
                    require_uptrend: bool = True,
                    max_per_sector: int = MAX_PER_SECTOR,
                    verbose: bool = False) -> list[str]:
    """
    Return the top `top_n` tickers by relative strength as of `as_of`.

    require_uptrend drops names below their own 200-SMA. This is a trend
    definition, not an optimisation: a "relative strength leader" in a
    structural downtrend is just the least-bad name in a falling group, which
    is not what the entry logic is built for.
    """
    as_of = as_of or date.today()
    pool = pool or CANDIDATE_POOL
    bars = fetch_history(pool + [BENCHMARK], as_of, RS_LOOKBACK,
                         verbose=verbose)
    bench = bars.get(BENCHMARK)
    if bench is None:
        print(f"\n  ! no {BENCHMARK} data — cannot rank.", file=sys.stderr)
        print("    Every candidate is scored RELATIVE to the benchmark, so "
              "without it\n    nothing can be ranked. See the fetch counts "
              "above for the cause.", file=sys.stderr)
        return []

    rows = []
    for t in pool:
        df = bars.get(t)
        if df is None:
            continue
        sc = score_ticker(df, bench)
        if sc is None:
            continue
        if require_uptrend and not sc["above_200sma"]:
            continue
        rows.append({"ticker": t, **sc})

    rows.sort(key=lambda r: r["rs"], reverse=True)
    picked = apply_sector_cap(rows, top_n, max_per_sector)
    if verbose:
        _print_table(rows, as_of, top_n, picked)
    return [r["ticker"] for r in picked]


def apply_sector_cap(rows: list[dict], top_n: int,
                     max_per_sector: int = MAX_PER_SECTOR) -> list[dict]:
    """
    Walk the RS ranking in order, skipping any name whose sector is already
    full. Ranking still decides WHO gets in; the cap only decides how many of
    a kind.

    If the cap cannot fill top_n (a small pool, or most sectors exhausted),
    the shortfall is returned as-is rather than backfilled past the cap — a
    smaller, genuinely diversified universe beats a full but concentrated one.
    """
    if max_per_sector <= 0:
        return rows[:top_n]
    counts, picked = {}, []
    for r in rows:
        sec = SECTORS.get(r["ticker"], "Other")
        if counts.get(sec, 0) >= max_per_sector:
            continue
        counts[sec] = counts.get(sec, 0) + 1
        picked.append({**r, "sector": sec})
        if len(picked) >= top_n:
            break
    return picked


def _print_table(rows: list[dict], as_of: date, top_n: int,
                 picked: list[dict]) -> None:
    W = 82
    chosen = {r["ticker"] for r in picked}
    print("=" * W)
    print(f"UNIVERSE SELECTION — as of {as_of}")
    print("=" * W)
    print(f"Ranked by {RS_LOOKBACK}-session return vs {BENCHMARK}. "
          f"Liquidity floor ${MIN_DOLLAR_VOLUME/1e6:.0f}M/day.")
    print(f"Sector cap: max {MAX_PER_SECTOR} per sector.")
    print(f"{len(rows)} of {len(CANDIDATE_POOL)} candidates passed the gates.\n")
    print(f"  {'#':>3} {'Ticker':<8}{'Sector':<12}{'Price':>9}{'Ret%':>8}"
          f"{'RS%':>8}{'$Vol M':>9}  Note")
    counts = {}
    for i, r in enumerate(rows[:max(top_n * 3, 20)], 1):
        sec = SECTORS.get(r["ticker"], "Other")
        if r["ticker"] in chosen:
            mark, note = "*", ""
            counts[sec] = counts.get(sec, 0) + 1
        elif counts.get(sec, 0) >= MAX_PER_SECTOR:
            mark, note = " ", f"skipped — {sec} full"
        else:
            mark, note = " ", "below cut"
        print(f"  {i:>3}{mark}{r['ticker']:<8}{sec:<12}{r['price']:>9.2f}"
              f"{r['return']:>8.2f}{r['rs']:>8.2f}{r['dollar_vol_m']:>9.1f}  {note}")

    print(f"\n  * = selected ({len(picked)} of {top_n} requested). "
          f"Benchmark over the same window: "
          f"{rows[0]['bench_return'] if rows else 0:.2f}%")
    by_sec = {}
    for r in picked:
        by_sec[r.get("sector", "Other")] = by_sec.get(r.get("sector", "Other"), 0) + 1
    print(f"  Sector mix: " + ", ".join(f"{k} {v}" for k, v in sorted(by_sec.items())))
    print("\n" + "-" * W)
    print("This picks the universe. It does not make the entry signal work —")
    print("that is a separate question the out-of-sample test answered no to.")
    print("-" * W)


# ---------------------------------------------------------------------------
# Shared snapshot loader — used by app.py AND scanner.py
# ---------------------------------------------------------------------------

HISTORY_DIR = "universe_history"
MAX_AGE_DAYS = 14
ENV_TOGGLE = "USE_DYNAMIC_UNIVERSE"
DEFAULT_DYNAMIC = True     # the code default; env var overrides either way


def dynamic_enabled() -> bool:
    """
    One switch, read by every consumer.

    app.py and scanner.py must never disagree about which universe is live: if
    the scanner alerts on RS leaders while the app scans the fixed baseline,
    you get Telegram alerts for tickers the app will not show you.

    The DEFAULT lives here in code rather than in an env var, because the two
    consumers run in different places (Streamlit Cloud and GitHub Actions) and
    keeping an env var in sync across both is exactly the kind of manual step
    that drifts. Set USE_DYNAMIC_UNIVERSE=0 to force the static baseline, or
    =1 to force dynamic; unset means DEFAULT_DYNAMIC.
    """
    raw = os.environ.get(ENV_TOGGLE, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return DEFAULT_DYNAMIC


def latest_snapshot(history_dir: str = HISTORY_DIR) -> tuple[dict | None, str]:
    """Newest JSON in history_dir, written by the weekly scheduled job."""
    try:
        if not os.path.isdir(history_dir):
            return None, f"no {history_dir}/ directory"
        files = sorted(f for f in os.listdir(history_dir) if f.endswith(".json"))
        if not files:
            return None, f"{history_dir}/ is empty"
        with open(os.path.join(history_dir, files[-1])) as f:
            snap = json.load(f)
        if not snap.get("tickers"):
            return None, f"{files[-1]} has no tickers"
        return snap, files[-1]
    except Exception as e:
        return None, f"unreadable ({e})"


def load_universe(fallback: list[str],
                  history_dir: str = HISTORY_DIR,
                  max_age_days: int = MAX_AGE_DAYS,
                  allow_live: bool = True,
                  today: date | None = None) -> tuple[list[str], str, int, str]:
    """
    Resolve the universe to scan. Returns (tickers, status, age_days, source)
    where source is one of: snapshot | snapshot-stale | live | fallback.

    Reading the committed snapshot rather than re-ranking is deliberate. It
    costs zero Yahoo calls, works when Yahoo is rate-limiting, and — the real
    reason — guarantees the list being traded is EXACTLY the one recorded in
    the churn history. A live re-rank would mean the turnover series describes
    a universe slightly different from the one that generated the trades.

    A week-old snapshot is the intended cadence, not staleness: the universe
    is rebalanced weekly by design.

    allow_live=False is for headless callers (scanner.py) that must not spend
    ~67 Yahoo calls ranking a universe while they still have a watchlist to
    scan through the same rate budget.
    """
    today = today or datetime.now().date()
    snap, label = latest_snapshot(history_dir)

    if snap is not None:
        try:
            as_of = datetime.strptime(snap["as_of"], "%Y-%m-%d").date()
            age = (today - as_of).days
        except Exception:
            age = -1
        tickers = list(snap["tickers"])
        if 0 <= age <= max_age_days:
            return tickers, f"snapshot {snap.get('as_of', label)}", age, "snapshot"
        return (tickers,
                f"snapshot {snap.get('as_of', label)} is {age}d old — the weekly "
                f"job may not be running", age, "snapshot-stale")

    if allow_live:
        try:
            picked = select_universe()
            if picked:
                return picked, "ranked live (no snapshot)", 0, "live"
        except Exception as e:
            label = f"{label}; live ranking failed ({e})"

    return list(fallback), f"{label} — using fallback list", -1, "fallback"


# ---------------------------------------------------------------------------
# Self-test — synthetic data, no network
# ---------------------------------------------------------------------------

def selftest() -> int:
    import numpy as np
    idx = pd.bdate_range("2024-01-02", periods=300)

    def make(drift, price0=100.0, vol=5e6):
        c = price0 * np.exp(np.cumsum(np.full(len(idx), drift)))
        return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99,
                             "Close": c, "Volume": np.full(len(idx), vol)},
                            index=idx)

    bench = make(0.0004)                       # benchmark drifts up gently
    strong = make(0.0015)                      # clear outperformer
    weak = make(-0.0005)                       # underperformer, downtrend
    illiquid = make(0.0020, vol=100)           # great RS, no liquidity
    cheap = make(0.0020, price0=5.0)           # great RS, price below floor

    s_strong = score_ticker(strong, bench)
    s_weak = score_ticker(weak, bench)
    assert s_strong and s_weak, "scoring returned None on valid input"
    print(f"strong RS      : {s_strong['rs']:+.2f}%  (expect positive)")
    print(f"weak RS        : {s_weak['rs']:+.2f}%  (expect negative)")
    assert s_strong["rs"] > 0 > s_weak["rs"]
    assert s_strong["rs"] > s_weak["rs"]

    print(f"strong trend   : {'above' if s_strong['above_200sma'] else 'below'} 200SMA")
    assert s_strong["above_200sma"] is True
    assert s_weak["above_200sma"] is False
    print(f"weak trend     : below 200SMA -> dropped when require_uptrend=True")

    assert score_ticker(illiquid, bench) is None
    print("illiquid       : rejected by dollar-volume floor")
    assert score_ticker(cheap, bench) is None
    print("sub-$20        : rejected by price floor")

    short = make(0.001).tail(50)
    assert score_ticker(short, bench) is None
    print("short history  : rejected (needs 200 sessions for the SMA)")

    # Ranking order end to end, with fetch stubbed out
    pool = ["STRONG", "MID", "WEAK"]
    fake = {"STRONG": strong, "MID": make(0.0008), "WEAK": weak, BENCHMARK: bench}
    global fetch_history
    real = fetch_history
    fetch_history = lambda t, a, l, verbose=False: fake
    try:
        # cap off here: these synthetic tickers all fall in "Other", so the
        # sector cap would clamp them to 2 and mask the ranking check.
        picked = select_universe(as_of=date(2025, 2, 1), top_n=2, pool=pool,
                                 max_per_sector=0)
        print(f"\nranked top 2   : {picked}  (expect ['STRONG', 'MID'])")
        assert picked == ["STRONG", "MID"], picked
        keep_weak = select_universe(as_of=date(2025, 2, 1), top_n=3, pool=pool,
                                    require_uptrend=False, max_per_sector=0)
        print(f"uptrend off    : {keep_weak}  (WEAK now included, ranked last)")
        assert keep_weak[-1] == "WEAK"
    finally:
        fetch_history = real

    # The bug the first version shipped with: the fetch window was sized from
    # RS_LOOKBACK only and could not contain MIN_HISTORY sessions, so every
    # ticker was dropped for short history. Assert the window is big enough.
    need = max(MIN_HISTORY, RS_LOOKBACK + 1)
    days = sessions_to_calendar_days(need)
    approx_sessions = int(days * 5 / 7)
    print(f"\nfetch window   : {days} calendar days ~ {approx_sessions} sessions")
    print(f"required       : {need} sessions")
    assert approx_sessions >= need, (
        f"fetch window holds ~{approx_sessions} sessions but {need} are required")
    print("window covers requirement: OK")

    # Sector cap: three Semis ranked 1-3 must not all be selected
    fake_rows = [
        {"ticker": "NVDA", "rs": 40.0}, {"ticker": "AMD", "rs": 35.0},
        {"ticker": "MU", "rs": 30.0},   {"ticker": "JPM", "rs": 25.0},
        {"ticker": "TMO", "rs": 20.0},  {"ticker": "ABT", "rs": 15.0},
    ]
    capped = [r["ticker"] for r in apply_sector_cap(fake_rows, top_n=4,
                                                    max_per_sector=2)]
    print(f"\nsector cap     : {capped}")
    print("                 (MU dropped — Semis already had NVDA and AMD)")
    assert capped == ["NVDA", "AMD", "JPM", "TMO"], capped
    uncapped = [r["ticker"] for r in apply_sector_cap(fake_rows, top_n=4,
                                                      max_per_sector=0)]
    assert uncapped == ["NVDA", "AMD", "MU", "JPM"], uncapped
    print(f"cap disabled   : {uncapped}  (pure RS order)")
    assert all(t in SECTORS for t in CANDIDATE_POOL), \
        [t for t in CANDIDATE_POOL if t not in SECTORS]
    print(f"sector coverage: all {len(CANDIDATE_POOL)} candidates labelled")

    # Shared loader — the path both app.py and scanner.py depend on
    import tempfile, json as _json
    d = tempfile.mkdtemp()
    fb = ["NVDA", "META", "MSFT"]

    tk, st_, age, src = load_universe(fb, history_dir=d, allow_live=False)
    print(f"\nempty dir      : {src} -> {tk}")
    assert src == "fallback" and tk == fb

    _json.dump({"as_of": "2026-08-21", "tickers": ["TMO", "PANW"]},
               open(os.path.join(d, "2026-08-21.json"), "w"))
    tk, st_, age, src = load_universe(fb, history_dir=d, allow_live=False,
                                      today=date(2026, 8, 24))
    print(f"fresh snapshot : {src} ({age}d) -> {tk}")
    assert src == "snapshot" and tk == ["TMO", "PANW"] and age == 3

    tk, st_, age, src = load_universe(fb, history_dir=d, allow_live=False,
                                      today=date(2026, 10, 1))
    print(f"stale snapshot : {src} ({age}d) — still scanned, with a warning")
    assert src == "snapshot-stale" and tk == ["TMO", "PANW"]

    _json.dump({"as_of": "2026-08-24", "tickers": ["AAA", "BBB"]},
               open(os.path.join(d, "2026-08-24.json"), "w"))
    tk, _, _, src = load_universe(fb, history_dir=d, allow_live=False,
                                  today=date(2026, 8, 24))
    print(f"two snapshots  : newest wins -> {tk}")
    assert tk == ["AAA", "BBB"]

    open(os.path.join(d, "2026-08-31.json"), "w").write("{not json")
    tk, _, _, src = load_universe(fb, history_dir=d, allow_live=False,
                                  today=date(2026, 8, 31))
    print(f"corrupt newest : {src} -> {tk}")
    assert src == "fallback" and tk == fb

    for var, want in [("1", True), ("true", True), ("ON", True),
                      ("0", False), ("false", False), ("OFF", False)]:
        os.environ[ENV_TOGGLE] = var
        assert dynamic_enabled() is want, (var, want)
    os.environ.pop(ENV_TOGGLE, None)
    assert dynamic_enabled() is DEFAULT_DYNAMIC
    print(f"env toggle     : 1/true/ON -> on, 0/false/OFF -> off, "
          f"unset -> {DEFAULT_DYNAMIC}")

    print("\nAll self-tests passed.")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--top", type=int, default=TOP_N)
    ap.add_argument("--max-per-sector", type=int, default=MAX_PER_SECTOR,
                    help=f"sector concentration cap (default {MAX_PER_SECTOR}, 0=off)")
    ap.add_argument("--no-uptrend-filter", action="store_true",
                    help="keep names below their 200-SMA")
    ap.add_argument("--json", default=None, help="write the selected list here")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    as_of = date.today()
    if args.as_of:
        try:
            as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()
        except ValueError:
            print("--as-of must be YYYY-MM-DD")
            return 2

    print(f"Fetching {len(CANDIDATE_POOL)} candidates + {BENCHMARK}...\n")
    picked = select_universe(as_of=as_of, top_n=args.top,
                             require_uptrend=not args.no_uptrend_filter,
                             max_per_sector=args.max_per_sector,
                             verbose=True)
    if not picked:
        print("\nNothing passed the gates.")
        return 1

    print(f"\nWATCHLIST = {json.dumps(picked)}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"as_of": str(as_of), "tickers": picked,
                       "rs_lookback": RS_LOOKBACK, "top_n": args.top}, f, indent=2)
        print(f"Written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
