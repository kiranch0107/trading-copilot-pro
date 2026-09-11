#!/usr/bin/env python3
"""
consistency_check.py — enforce the invariants that span modules

WHY THIS EXISTS
---------------
This project's recurring failure mode is not bad logic — it is the SAME logic
living in two places and drifting. signal_core.py exists because app.py and
scanner.py each had their own analyze(). backtest.py was rewritten to call
signal_core because it had a hand-maintained copy. Both fixes were correct and
both were invisible to CI, because nothing checks that two files still agree.

Every check here is one that a human already had to find by hand, at least
once, after it had already produced a wrong number or a wrong alert. They are
cheap, offline, and deterministic — the point is that the NEXT drift is caught
by a machine in 30 seconds instead of by you, months later, from a Telegram
alert the app disagrees with.

WHAT THIS CANNOT CHECK
----------------------
app.py cannot be imported here: it is a Streamlit script whose module body
renders the whole UI and fetches live data. Its checks are therefore
SOURCE-LEVEL (does the text still derive its defaults from signal_core?) not
behavioural. Same for the two backtest callers, whose signal paths need
network. Source-level is weaker than a real call — it proves the wiring is
declared, not that it runs — but it is what is available offline, and it
would have caught every drift this file was written in response to.

USAGE
    python consistency_check.py            # run every check
    python consistency_check.py --selftest # same thing (CI entry point)
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime
from pathlib import Path

# How much runway the hardcoded market calendar must still have. This is a
# FORCING FUNCTION, not a style rule: when the calendar runs out,
# is_market_open() reports the market OPEN on a holiday, and drop_partial_bar()
# then discards the last COMPLETED bar as though it were still forming — so
# every module silently analyses day-stale data. signal_core.py's own comment
# describes exactly this failure. Failing CI ~6 weeks out is the cheap warning.
CALENDAR_MIN_RUNWAY_DAYS = 45

# The four Python modules plus the inline copy in the scanner workflow. Each
# entry is (path, holidays-anchor, half-days-anchor).
CALENDAR_SOURCES = [
    ("signal_core.py",                "MARKET_HOLIDAYS = frozenset({", "MARKET_HALF_DAYS = frozenset({"),
    ("app.py",                        "MARKET_HOLIDAYS = {",           "MARKET_HALF_DAYS = {"),
    ("scanner.py",                    "MARKET_HOLIDAYS = {",           "MARKET_HALF_DAYS = {"),
    ("exit_monitor.py",               "MARKET_HOLIDAYS = {",           "MARKET_HALF_DAYS = {"),
    (".github/workflows/scanner.yml", "HOLIDAYS = {",                  "HALF_DAYS = {"),
]

# Sidebar tunables in app.py that MUST be sourced from signal_core.DEFAULTS
# rather than written as literals. Three of these had already drifted (see the
# comment block at their definition in app.py) before this check existed.
APP_DERIVED_DEFAULTS = [
    ("ADX_MIN",        "_D.adx_min"),
    ("MIN_RR",         "_D.min_rr"),
    ("HQ_MIN_RR",      "_D.hq_min_rr"),
    ("VOLUME_MULT",    "_D.volume_mult"),
    ("ATR_STOP_MULT",  "_D.atr_stop_mult"),
    ("ATR_TGT_MULT",   "_D.atr_tgt_mult"),
    ("WEEKLY_CONFIRM", "_D.weekly_confirm"),
    ("SPY_REGIME",     "_D.spy_regime_on"),
]

_DATE_RE = re.compile(r'"(20\d\d-\d\d-\d\d)"')


def _dates_after(path: str, anchor: str) -> list[str]:
    """Pull the quoted YYYY-MM-DD set that follows `anchor` in `path`."""
    txt = Path(path).read_text()
    i = txt.find(anchor)
    if i < 0:
        raise AssertionError(f"{path}: anchor {anchor!r} not found — the "
                             f"calendar was renamed or moved; update "
                             f"CALENDAR_SOURCES in consistency_check.py")
    block = txt[i:].split("}", 1)[0]
    return sorted(set(_DATE_RE.findall(block)))


# ---------------------------------------------------------------------------
# 1. The market calendar is duplicated 5x — it must at least stay identical
# ---------------------------------------------------------------------------

def check_calendars_identical() -> None:
    ref_path, ref_hol, ref_half = CALENDAR_SOURCES[0]
    hol_ref = _dates_after(ref_path, ref_hol)
    half_ref = _dates_after(ref_path, ref_half)
    print(f"  reference: {ref_path} — {len(hol_ref)} holidays, "
          f"{len(half_ref)} half-days")

    for path, hol_anchor, half_anchor in CALENDAR_SOURCES[1:]:
        hol = _dates_after(path, hol_anchor)
        half = _dates_after(path, half_anchor)
        if hol != hol_ref:
            raise AssertionError(
                f"MARKET_HOLIDAYS in {path} has DRIFTED from {ref_path}.\n"
                f"  only in {path}: {sorted(set(hol) - set(hol_ref))}\n"
                f"  only in {ref_path}: {sorted(set(hol_ref) - set(hol))}\n"
                f"  All {len(CALENDAR_SOURCES)} copies must match, or the app, "
                f"the scanner and the exit monitor will disagree about whether "
                f"the market is open.")
        if half != half_ref:
            raise AssertionError(
                f"MARKET_HALF_DAYS in {path} has DRIFTED from {ref_path}.\n"
                f"  only in {path}: {sorted(set(half) - set(half_ref))}\n"
                f"  only in {ref_path}: {sorted(set(half_ref) - set(half))}")
        print(f"  {path:38} matches")


def check_calendar_runway(today: date | None = None) -> None:
    today = today or date.today()
    hol = _dates_after(*CALENDAR_SOURCES[0][:2])
    last = datetime.strptime(hol[-1], "%Y-%m-%d").date()
    runway = (last - today).days
    print(f"  calendar ends {last} — {runway} days of runway "
          f"(minimum {CALENDAR_MIN_RUNWAY_DAYS})")
    if runway < CALENDAR_MIN_RUNWAY_DAYS:
        raise AssertionError(
            f"The hardcoded market calendar ends {last}, only {runway} days "
            f"away.\n"
            f"  Past that date is_market_open() returns True on market "
            f"holidays, and drop_partial_bar() then DISCARDS the last "
            f"COMPLETED bar as if it were still forming — so every module "
            f"silently analyses day-stale data.\n"
            f"  Add next year's NYSE holidays and 1:00pm early closes to all "
            f"{len(CALENDAR_SOURCES)} copies listed in CALENDAR_SOURCES.")


# ---------------------------------------------------------------------------
# 2. compute() exists in three files — the indicators must be identical
# ---------------------------------------------------------------------------

def _synthetic_bars(n: int = 320):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(11)
    rets = rng.normal(loc=0.0008, scale=0.013, size=n)
    close = 100.0 * np.cumprod(1.0 + rets)
    return pd.DataFrame({
        "Open":   close,
        "High":   close * (1.0 + np.abs(rng.normal(0, 0.004, n))),
        "Low":    close * (1.0 - np.abs(rng.normal(0, 0.004, n))),
        "Close":  close,
        "Volume": np.full(n, 2_000_000.0),
    })


def check_compute_agrees() -> None:
    """
    scanner.compute() vs backtest.compute() on identical bars.

    app.py's compute() is a third copy that cannot be imported here (Streamlit
    script), so it is checked at source level below instead.
    """
    import numpy as np
    import scanner
    import backtest

    raw = _synthetic_bars()
    a = scanner.compute(raw.copy()).reset_index(drop=True)
    b = backtest.compute(raw.copy()).reset_index(drop=True)

    cols = ["EMA20", "EMA50", "MACD", "Signal", "RSI", "ATR", "ADX", "VOL_AVG20"]
    if len(a) != len(b):
        raise AssertionError(
            f"scanner.compute() returned {len(a)} bars but backtest.compute() "
            f"returned {len(b)} — the warm-up trim or dropna rules have "
            f"drifted apart.")
    for c in cols:
        if not np.allclose(a[c].to_numpy(), b[c].to_numpy(), rtol=1e-9, atol=1e-9):
            worst = float(np.nanmax(np.abs(a[c].to_numpy() - b[c].to_numpy())))
            raise AssertionError(
                f"{c} differs between scanner.compute() and "
                f"backtest.compute() (max abs diff {worst:.3g}). The live "
                f"signal and the backtest would be computing different "
                f"indicators from the same prices.")
    print(f"  scanner.compute() == backtest.compute() on {len(a)} bars "
          f"({len(cols)} indicators)")


def check_app_compute_source() -> None:
    """app.py's compute() must still build the same indicator set."""
    txt = Path("app.py").read_text()
    i = txt.find("def compute(")
    if i < 0:
        raise AssertionError("app.py: compute() not found")
    body = txt[i:i + 1400]
    required = ["EMA20", "EMA50", "MACD", "Signal", "RSI", "ATR", "ADX",
                "VOL_AVG20"]
    missing = [c for c in required if f'"{c}"' not in body]
    if missing:
        raise AssertionError(
            f"app.py's compute() no longer sets {missing} — it has drifted "
            f"from scanner.compute()/backtest.compute(). signal_core.evaluate() "
            f"raises on a frame missing any REQUIRED_COLUMNS, so this is a "
            f"live break, not a style issue.")
    print(f"  app.py compute() sets all {len(required)} indicator columns")


# ---------------------------------------------------------------------------
# 3. app.py's sidebar defaults must come from signal_core, not literals
# ---------------------------------------------------------------------------

def check_app_defaults_derived() -> None:
    txt = Path("app.py").read_text()
    if "_D = signal_core.DEFAULTS" not in txt:
        raise AssertionError(
            "app.py no longer binds `_D = signal_core.DEFAULTS`. The sidebar "
            "defaults must be sourced from the shared dataclass — hardcoding "
            "them is how atr_stop_mult, hq_min_rr and volume_mult silently "
            "drifted away from the values scanner.py runs on.")

    for name, expected in APP_DERIVED_DEFAULTS:
        m = re.search(rf"^{name}\s*=.*$", txt, re.MULTILINE)
        if not m:
            raise AssertionError(f"app.py: {name} assignment not found")
        if expected not in m.group(0):
            raise AssertionError(
                f"app.py: {name} default is not derived from "
                f"signal_core.DEFAULTS (expected `{expected}` in its "
                f"definition, got:\n    {m.group(0).strip()}\n"
                f"  A literal here drifts away from what scanner.py runs on "
                f"and nothing notices until the two disagree on a live trade.")
    print(f"  all {len(APP_DERIVED_DEFAULTS)} signal tunables derive from "
          f"signal_core.DEFAULTS")


# ---------------------------------------------------------------------------
# 4. backtest.evaluate_signal()'s callers must pass SignalParams, not a dict
# ---------------------------------------------------------------------------

def check_backtest_callers() -> None:
    """
    backtest.evaluate_signal() took a cfg dict until it was rewritten to
    delegate to signal_core.evaluate(); it now takes a SignalParams. Two
    callers were missed and broke with
        AttributeError: 'dict' object has no attribute 'volume_mult'
    Neither is in CI and neither has a selftest, so it went unnoticed.
    """
    import backtest as bt
    import signal_core as sc

    # Behavioural half: the contract itself still holds.
    params = bt.build_signal_params(dict(bt.DEFAULTS))
    assert isinstance(params, sc.SignalParams)
    df = bt._synthetic_ohlc(adx=40.0)
    bt.evaluate_signal(df, len(df) - 1, params)          # must not raise

    try:
        bt.evaluate_signal(df, len(df) - 1, dict(bt.DEFAULTS))
    except AttributeError:
        pass                                              # expected
    else:
        raise AssertionError(
            "backtest.evaluate_signal() accepted a plain dict. If the "
            "signature has gone back to taking cfg, update this check AND "
            "universe_backtest.py / option_backtest.py together.")
    print("  backtest.evaluate_signal() takes SignalParams (dict rejected)")

    # Source half: the callers actually build one. They cannot be exercised
    # offline (their signal paths need network), so this checks the wiring.
    for path in ("universe_backtest.py", "option_backtest.py"):
        txt = Path(path).read_text()
        if "build_signal_params" not in txt:
            raise AssertionError(
                f"{path} calls bt.evaluate_signal() but never builds a "
                f"SignalParams via bt.build_signal_params(). It will raise "
                f"AttributeError: 'dict' object has no attribute "
                f"'volume_mult' the moment it runs.")
        for bad in re.findall(r"evaluate_signal\([^)]*\)", txt):
            if re.search(r",\s*(cfg|sig_cfg)\s*\)", bad):
                raise AssertionError(
                    f"{path} passes a cfg dict to evaluate_signal(): {bad}")
        print(f"  {path:24} builds SignalParams before evaluate_signal()")


# ---------------------------------------------------------------------------
# 6. The three injected inputs to signal_core.evaluate() must be ONE rule
# ---------------------------------------------------------------------------

def check_market_context_shared() -> None:
    """
    signal_core.evaluate() is a single implementation, but it takes
    weekly_trend and spy_regime as INJECTED values — and app.py and scanner.py
    each used to compute those their own way:
        weekly   app: EMA10w vs EMA20w crossover   scanner: price vs EMA20w
        regime   app: 3-state, ADX>=20 gated       scanner: 2-state, ungated
    Opposite verdicts on ordinary pullbacks, on a BLOCKING filter. Both now
    delegate to market_context, so this checks the delegation is still in place
    rather than quietly reimplemented.
    """
    import market_context as mc

    for path in ("app.py", "scanner.py"):
        txt = Path(path).read_text()
        if "import market_context" not in txt:
            raise AssertionError(
                f"{path} no longer imports market_context — the weekly trend "
                f"and SPY regime must come from the shared module or the two "
                f"callers will feed signal_core different values again.")
        for fn in ("get_weekly_trend", "get_spy_regime"):
            i = txt.index(f"def {fn}(")
            body = txt[i:i + 1600]
            if f"market_context.{fn}" not in body:
                raise AssertionError(
                    f"{path}: {fn}() no longer delegates to "
                    f"market_context.{fn}(). Reimplementing it here is how "
                    f"app.py and scanner.py diverged in the first place.")
        print(f"  {path:12} delegates weekly trend + SPY regime to market_context")

    # The regime rule must still match the one the OOS test actually validated.
    import numpy as np
    import pandas as pd
    import backtest as bt
    for series in (np.linspace(300, 500, 260), np.linspace(500, 300, 260)):
        want = bt.build_regime_series(pd.DataFrame({"Close": series})).iloc[-1]
        got = mc.spy_regime_from_bars(
            pd.DataFrame({"Close": series, "High": series + 1,
                          "Low": series - 1}))["regime"]
        if got != want:
            raise AssertionError(
                f"live SPY regime {got!r} != backtest.build_regime_series() "
                f"{want!r}. The 591-trade OOS test ran with use_regime=True, "
                f"so live must use the rule the backtest validated.")
    print("  live SPY regime == backtest.build_regime_series() (the validated rule)")

    # ADX must not have been reintroduced as a gate. Uses market_context's own
    # verified low-ADX fixture (ADX ~11) — an earlier fixture here measured
    # ADX 26, above the old threshold, so it passed whether or not the gate
    # existed and this guard was silently useless.
    r = mc.spy_regime_from_bars(mc._choppy_above_sma200())
    assert r["adx"] is not None and r["adx"] < 20, (
        f"guard fixture must have ADX < 20 to be meaningful, got {r['adx']}")
    if r["regime"] != "Bull":
        raise AssertionError(
            f"a low-ADX tape returned regime {r['regime']!r}. ADX is reported "
            f"only — gating on it (app.py's old ADX>=20 -> 'Neutral') disables "
            f"the regime filter in choppy markets, which the backtest never did.")
    print("  SPY ADX is reported, not gating (matches the backtest)")


# ---------------------------------------------------------------------------
# 7. The live universe must not spend reserved (held-out) data
# ---------------------------------------------------------------------------

def check_universe_not_spending_reserved() -> None:
    """
    data_reservation.py protects held-out tickers so a future out-of-sample
    test is actually out-of-sample. But nothing connected it to universe.py,
    and on 2026-09-02 every one of the 66 names in CANDIDATE_POOL was either
    contaminated or reserved — so the dynamic ranker could not pick a ticker
    WITHOUT spending research capital, and the scanner had been alerting on
    six Tranche B names for weeks.

    Live trading on a reserved name spends it: you see the outcome, you form a
    view, and the tranche is no longer clean. This makes that impossible to do
    by accident again.
    """
    import data_reservation as dr
    import universe as uni

    r = dr.check_clean(uni.CANDIDATE_POOL)
    reserved = {t for v in r["reserved"].values() for t in v}
    if reserved:
        raise AssertionError(
            f"universe.CANDIDATE_POOL contains {len(reserved)} RESERVED "
            f"ticker(s): {', '.join(sorted(reserved))}.\n"
            f"  The dynamic universe feeds scanner.py, so these would be "
            f"traded live — which spends a held-out tranche you cannot get "
            f"back.\n"
            f"  Either remove them from the pool, or claim the tranche "
            f"deliberately:  python data_reservation.py --spend <X> "
            f"--purpose '...'")
    print(f"  CANDIDATE_POOL ({len(uni.CANDIDATE_POOL)}) holds no "
          f"reserved-but-unspent tickers")

    # The pool must still be broad enough for the ranker to mean anything.
    if len(uni.CANDIDATE_POOL) < 40:
        raise AssertionError(
            f"CANDIDATE_POOL is down to {len(uni.CANDIDATE_POOL)} names. "
            f"Ranking by relative strength stops being a selection at that "
            f"size — widen it with names outside the reservation tranches.")
    labelled = [t for t in uni.CANDIDATE_POOL if t not in uni.SECTORS]
    if labelled:
        raise AssertionError(
            f"unlabelled tickers in CANDIDATE_POOL: {labelled} — the sector "
            f"cap silently treats them as one bucket ('Other').")
    print(f"  every pool ticker has a sector label; sector cap is meaningful")


# ---------------------------------------------------------------------------
# 5. A ticker that could not be scanned must not look like a ticker with no
#    setup
# ---------------------------------------------------------------------------

def check_scan_failures_surfaced() -> None:
    """
    On 2026-09-02 the live app showed 0 high-quality / 0 all-filters /
    0 partial and "Partial / failed signals (0 tickers)" for an 8-name
    watchlist, minutes after its own logs recorded a YFRateLimitError. Every
    failure path in the scan did a bare `return None`, and the caller dropped
    them all identically — so a total data outage and a quiet market rendered
    as the same screen.

    Source-level, because importing app.py executes the Streamlit script and
    makes live Yahoo calls (same reason CI compiles it instead of running it).
    What is pinned here is the CONTRACT: failures travel back to the UI.
    """
    txt = Path("app.py").read_text()

    if "return r if r and not r.get(\"blocked\") else None" in txt:
        raise AssertionError(
            "_scan_one_ticker() is back to collapsing every outcome into "
            "None. A rate-limited fetch then renders identically to 'no "
            "setup today' and a data outage becomes invisible.")

    for needed, why in (
        ('return None, "no data"',
         "a missing frame must report WHY, not just vanish"),
        ('return None, "not enough history"',
         "a short history must be distinguishable from a rejected setup"),
        ("skipped.append((tk, reason))",
         "the scan must collect per-ticker skip reasons"),
        ("skipped.append((tk, type(e).__name__))",
         "an exception is a failure to look, and must be surfaced too"),
        ("all_setups, scan_skipped = run_watchlist_scan(",
         "the caller must unpack the skip list, not discard it"),
        ("No ticker could be scanned",
         "an all-tickers-failed scan must say so instead of showing zeros"),
    ):
        if needed not in txt:
            raise AssertionError(f"app.py: missing {needed!r} — {why}")

    # scanner.py runs unattended, so its failures can only ever be seen in the
    # run log. They must at least get there.
    stxt = Path("scanner.py").read_text()
    for needed in ('failed.append(f"{tk} (no data)")',
                   "Failed tickers:"):
        if needed not in stxt:
            raise AssertionError(
                f"scanner.py: missing {needed!r} — an unattended scan that "
                f"silently fetches nothing looks exactly like a scan that "
                f"found nothing.")
    print("  app.py scan reports unscannable tickers separately from no-setup")
    print("  scanner.py logs failed tickers rather than dropping them")


# ---------------------------------------------------------------------------
# 6. The backtest and the live paths must price bars the same way
# ---------------------------------------------------------------------------

def check_adjustment_matches_live() -> None:
    """
    backtest.py fetched auto_adjust=True while app.py, scanner.py and
    exit_monitor.py all fetched False. So the backtest measured a DIFFERENT
    price series from the one live signals fire on, and nothing caught it for
    weeks — it was carried as a known-open note instead of a failing test.

    Measured 2026-09-02, both arms refetched twelve minutes apart:
        adjusted  -0.018 R, 593 trades, 8 of 13 series rewrote 2337-2893 rows
        raw       -0.021 R, 585 trades, ZERO rows rewritten
    Performance is a tie; reproducibility is not. Raw also matches live.

    This check fails if either side drifts again.
    """
    import backtest as bt

    assert bt.AUTO_ADJUST is False, (
        "backtest.AUTO_ADJUST is True. That series is NOT reproducible — a "
        "refetch rewrites the full history of every dividend payer — and it "
        "no longer matches the live paths. --adjusted-prices exists for a "
        "deliberate comparison; the default must stay raw.")
    print("  backtest defaults to auto_adjust=False (raw, reproducible)")

    for path in ("app.py", "scanner.py", "exit_monitor.py"):
        txt = Path(path).read_text()
        if "auto_adjust=True" in txt:
            raise AssertionError(
                f"{path} fetches auto_adjust=True somewhere. Live paths use "
                f"raw bars; a mismatch means the backtest measures a series "
                f"nothing trades on.")
        if "auto_adjust=False" not in txt:
            raise AssertionError(
                f"{path} no longer sets auto_adjust explicitly. yfinance's "
                f"default has changed before; leaving it implicit is how the "
                f"backtest/live split went unnoticed in the first place.")
    print("  app.py / scanner.py / exit_monitor.py all fetch auto_adjust=False")


# ---------------------------------------------------------------------------
# 7. oos_validate.py must be able to parse backtest.py's ACTUAL output
# ---------------------------------------------------------------------------

def check_oos_parses_backtest_output() -> None:
    """
    oos_validate.py reads backtest.py's stdout with regexes. That makes the
    table layout a CONTRACT between two modules, and it broke: a Bars column
    was added to backtest.py's per-ticker table and the parser was not
    updated, so every real OOS run died with "Could not parse backtest
    output".

    Its selftest did not catch it, because that parses a hardcoded SAMPLE
    string — a stale copy of a format owned by another module. A test pinning
    its own copy of the thing under test cannot notice the thing changing.

    So this runs backtest.run() for real, on synthetic bars, and feeds the
    actual stdout through the actual parser. No network: the downloader is
    replaced, and the cache is bypassed so the developer's real .bar_cache is
    untouched.
    """
    import io
    import contextlib
    import numpy as np
    import pandas as pd
    import backtest as bt
    import oos_validate as oo

    def _synthetic(ticker, years, interval="1d"):
        n = 700 if interval == "1d" else 160
        rng = np.random.default_rng(abs(hash(ticker)) % 997)
        close = 100 * np.exp(np.cumsum(rng.normal(0.0012, 0.013, n)))
        idx = (pd.bdate_range("2020-01-02", periods=n) if interval == "1d"
               else pd.date_range("2020-01-06", periods=n, freq="W-MON"))
        return pd.DataFrame({
            "Date": idx, "Open": close * 0.999, "High": close * 1.012,
            "Low": close * 0.988, "Close": close,
            "Volume": rng.integers(5_000_000, 20_000_000, n).astype(float)})

    real_dl, real_cache = bt._download_uncached, bt.CACHE_ENABLED
    bt._download_uncached, bt.CACHE_ENABLED = _synthetic, False
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bt.run(dict(bt.DEFAULTS, tickers=["AAA", "BBB", "CCC"],
                        years=2, adx_min=15, use_regime=False))
        out = buf.getvalue()
    finally:
        bt._download_uncached, bt.CACHE_ENABLED = real_dl, real_cache

    per_ticker, agg = oo.parse_output(out)

    if not per_ticker:
        raise AssertionError(
            "oos_validate.parse_output() found ZERO per-ticker rows in "
            "backtest.py's real output. The table layout has changed and "
            "PER_TICKER_RE no longer matches it — every OOS run will exit 3 "
            "with 'Could not parse backtest output'.")
    if "expectancy" not in agg or "total_trades" not in agg:
        raise AssertionError(
            f"the aggregate block did not parse (got keys {sorted(agg)}). "
            f"AGG_PATTERNS and backtest.py's summary have drifted.")

    # The parsed rows must agree with the run, not merely be non-empty: a
    # regex that matched the wrong columns would still produce rows.
    assert sum(t["trades"] for t in per_ticker) == agg["total_trades"], (
        f"per-ticker trades {sum(t['trades'] for t in per_ticker)} != "
        f"aggregate {agg['total_trades']} — the parser is reading the wrong "
        f"columns, which is worse than failing outright")
    for t in per_ticker:
        assert t["bars"] and t["bars"] > t["trades"], (
            f"{t['ticker']}: bars={t['bars']} is not a plausible bar count — "
            f"the Bars column is being read as something else")
    print(f"  oos_validate parses backtest output "
          f"({len(per_ticker)} rows, {agg['total_trades']} trades, columns agree)")


# ---------------------------------------------------------------------------
# 8. The unattended workflows must not depend on Streamlit
# ---------------------------------------------------------------------------

def check_unattended_modules_are_streamlit_free() -> None:
    """
    scanner.py and exit_monitor.py run on GitHub Actions, whose workflows
    install a SHORT list of packages:

        scanner.yml       yfinance pandas ta requests pytz
        exit-monitor.yml  yfinance pandas pytz requests

    No Streamlit. gh_sync.py imports Streamlit at module scope, and
    journal_store.py imports gh_sync — so the day either unattended module
    imports journal_store (to log an alert, say, which is a very natural thing
    to want), both workflows die with ModuleNotFoundError and the alerts stop
    arriving. Silently, because nobody reads a green-until-it-isn't cron log.

    A review claimed this had already happened. It has not — exit_monitor.py
    imports neither — but nothing prevented it. This does.

    Import-graph based rather than a grep for "streamlit", because the danger
    is the TRANSITIVE edge, which no grep of these two files would ever show.
    """
    import ast

    repo = Path(".")

    def direct_imports(mod: str) -> set[str]:
        try:
            tree = ast.parse((repo / f"{mod}.py").read_text())
        except FileNotFoundError:
            return set()
        out = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                out.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                out.add(node.module.split(".")[0])
        return out

    local = {p.stem for p in repo.glob("*.py")}

    for entry in ("scanner", "exit_monitor"):
        seen, stack, path_to = set(), [entry], {entry: [entry]}
        while stack:
            mod = stack.pop()
            if mod in seen:
                continue
            seen.add(mod)
            for dep in sorted(direct_imports(mod)):
                if dep == "streamlit":
                    chain = " -> ".join(path_to[mod] + ["streamlit"])
                    raise AssertionError(
                        f"{entry}.py transitively imports Streamlit:\n"
                        f"    {chain}\n"
                        f"Its GitHub Actions workflow does not install "
                        f"Streamlit, so this run will die with "
                        f"ModuleNotFoundError and the alerts will stop "
                        f"arriving without anyone being told.")
                if dep in local and dep not in seen:
                    path_to[dep] = path_to[mod] + [dep]
                    stack.append(dep)
        print(f"  {entry}.py imports no Streamlit "
              f"(checked {len(seen)} modules transitively)")


# ---------------------------------------------------------------------------
# 9. Position sizing constants live in exactly one place
# ---------------------------------------------------------------------------

def check_sizing_constants_shared() -> None:
    """
    app.py and scanner.py each hardcoded ACCOUNT_SIZE and RISK_PCT. A review
    read the 1.0 vs 5.0 gap as a 5x position-sizing bug. It was not — they are
    different quantities (stop-distance risk vs max option premium) that
    happened to share a name — but one identifier meaning two things across
    two modules is how the earlier signal divergences started, and the account
    size really was duplicated.

    Both now read risk_params.py. This check keeps them there.
    """
    import risk_params

    assert risk_params.option_budget(1500, 5.0) == 75.0

    for path, forbidden in (
        ("app.py", ["ACCOUNT_SIZE = 1500"]),
        ("scanner.py", ["ACCOUNT_SIZE = 1500", "RISK_PCT     = 5.0",
                        "RISK_PCT = 5.0"]),
    ):
        txt = Path(path).read_text()
        for lit in forbidden:
            if lit in txt:
                raise AssertionError(
                    f"{path} hardcodes '{lit}' again. Sizing constants belong "
                    f"in risk_params.py so app.py and scanner.py cannot drift.")
        if "risk_params" not in txt:
            raise AssertionError(
                f"{path} no longer reads risk_params.py — the two files can "
                f"drift apart again with nothing to notice.")

    # scanner's percentage must keep the name that says what it governs.
    stxt = Path("scanner.py").read_text()
    assert "OPTION_BUDGET_PCT" in stxt, (
        "scanner.py's premium cap must not be called RISK_PCT again — that "
        "name means stop-distance risk in app.py, and one identifier meaning "
        "two things is the bug this check exists to prevent.")
    print("  app.py + scanner.py share risk_params.py; premium cap is named apart")


# ---------------------------------------------------------------------------
# 10. Every credential the workflows supply must reach the app too
# ---------------------------------------------------------------------------

def check_app_can_reach_fallback_key() -> None:
    """
    data_source.py reads TIINGO_API_KEY from os.environ and nothing else, and
    it must stay that way — scanner.py and exit_monitor.py import it and run
    where Streamlit does not exist.

    scanner.yml and exit-monitor.yml both pass that key in as an env var, so
    the fallback works unattended. app.py had NO equivalent: the key lives in
    st.secrets there, and nothing copied it across. The second price source —
    the entire point of which is "Yahoo is unavailable" — could never activate
    in the app, which is the one place a human is sitting there watching it
    fail.

    Source-level because importing app.py runs the Streamlit script and makes
    live calls (same reason CI compiles it instead).
    """
    import data_source

    app = Path("app.py").read_text()
    key = data_source.TIINGO_KEY_ENV

    if "_bridge_secrets_to_env" not in app:
        raise AssertionError(
            "app.py no longer bridges st.secrets into os.environ. "
            f"data_source reads {key} from the environment only, so the "
            f"fallback silently cannot activate in the app.")
    if key not in app:
        raise AssertionError(
            f"app.py does not bridge {key}. Yahoo throttling will stop the "
            f"app dead with a fallback that is configured but unreachable.")

    # Whatever the workflows bother to pass, the app should be able to see.
    for wf in ("scanner.yml", "exit-monitor.yml"):
        txt = Path(".github/workflows") / wf
        if not txt.exists():
            continue
        if key in txt.read_text() and key not in app:
            raise AssertionError(
                f"{wf} passes {key} but app.py cannot reach it — the "
                f"unattended paths get a fallback and the interactive one "
                f"does not.")
    print(f"  app.py bridges {key} from secrets; unattended paths use env")
    print("  data_source stays env-only (Streamlit-free for the workflows)")


# ---------------------------------------------------------------------------
# 11. Paper trades must never be blended into the live record, and every
#     logging path must check position size
# ---------------------------------------------------------------------------

def check_journal_and_sizing_guards() -> None:
    """
    Both halves of this were found by reading a week of real trades, not code.

    MODE: paper vs live lived only in the free-text `notes` field. Nothing
    parsed it, so the dashboard reported all six closed trades as one record —
    66.7% win rate, PF 2.28, +1.55R — when the five real ones were 80% /
    PF 7.08 / +$316 and the one paper trade was 0% / -$448. The blend
    described neither. A convention held in prose is not a convention.

    SIZE: no logging path checked the cost of a position. calc_position_size()
    computed the right answer and no caller asked it. A paper trade was logged
    at $625 on a $1,500 account — 42% of capital in a single long option,
    where the premium is the whole maximum loss.
    """
    import journal_store as js
    import risk_params as rp

    # ── the stats function must be able to separate the two records ──
    J = [{"outcome": "WIN",  "actual_rr": 0.5, "pnl_usd": 100.0,
          "closed": "2026-01-01", "mode": "live"},
         {"outcome": "LOSS", "actual_rr": -0.7, "pnl_usd": -400.0,
          "closed": "2026-01-02", "mode": "paper"}]
    live = js.journal_stats(J, mode="live")
    both = js.journal_stats(J)

    # Presence FIRST, with .get() throughout. Indexing a missing key raises
    # KeyError, which selftest() does not catch — the run aborted with a
    # traceback instead of reporting the failure, so a removed dollar metric
    # looked like a crash rather than the specific regression it is.
    for key in ("net_usd", "profit_factor_usd", "equity_curve_usd"):
        if key not in (live or {}):
            raise AssertionError(
                f"journal_stats no longer reports {key}. The R figures are a "
                f"return on premium and can point the opposite way from the "
                f"money — dollars are the only figure that is simply true.")

    if not live or live.get("net_usd") != 100.0:
        raise AssertionError(
            "journal_stats(mode='live') no longer isolates real trades. "
            "Blending paper into the track record is how a losing week reads "
            "as a winning one.")
    if both.get("net_usd") != -300.0:
        raise AssertionError("the blended total is wrong")
    if live.get("net_usd", 0) <= both.get("net_usd", 0):
        raise AssertionError(
            "live and blended must be distinguishable on this fixture, or the "
            "check has stopped discriminating")

    app = Path("app.py").read_text()
    if "journal_stats(journal, mode=_mode)" not in app:
        raise AssertionError(
            "app.py's dashboard no longer filters by mode — it is back to "
            "showing one blended headline for real and simulated trades.")
    if "net_usd" not in app:
        raise AssertionError(
            "app.py no longer displays dollar P&L. pnl_usd was stored from the "
            "first trade and rendered nowhere, so the only visible numbers "
            "were R-based ones that disagreed with the account.")

    # ── every logging path must run the size gate ──
    if "def size_gate(" not in app:
        raise AssertionError("app.py lost size_gate()")
    n_modes = app.count("mode=_q_mode") + app.count("mode=_o_mode") + \
              app.count("mode=_chk_mode")
    n_gates = app.count("size_gate(")
    n_srcs = app.count("source=_q_src") + app.count("source=_o_src") + \
             app.count("source=_chk_src")
    if n_modes < 3:
        raise AssertionError(
            f"only {n_modes} of the position-logging paths pass a mode. Any "
            f"path that does not will silently record a paper trade as live.")
    if "def source_selector(" not in app:
        raise AssertionError("app.py lost source_selector()")
    if n_srcs < 3:
        raise AssertionError(
            f"only {n_srcs} of the position-logging paths pass a source. "
            f"open_option_position() defaults to 'discretionary', so an "
            f"unwired path silently records every trade as a judgement call "
            f"— and the journal can then say nothing about whether the "
            f"SIGNAL works, which is what the 30-trade run is for.")
    if n_gates < 4:      # 1 definition + 3 call sites
        raise AssertionError(
            f"size_gate is referenced {n_gates} times; expected the definition "
            f"plus all 3 logging paths. An ungated path is how $625 went into "
            f"a $1,500 account with nothing objecting.")

    # ── the thresholds themselves still bite ──
    if rp.check_option_cost(6.25, 1, account_size=1500)["level"] != "block":
        raise AssertionError(
            "a $625 contract on a $1,500 account no longer blocks — that is "
            "42% of capital in one long option.")
    if rp.check_option_cost(0.0, 1)["level"] != "invalid":
        raise AssertionError(
            "a zero entry premium must be rejected: a percentage stop on a $0 "
            "entry can never fire, so the monitor would watch it forever.")
    print(f"  journal separates paper from live and reports dollars")
    print(f"  all {n_modes} logging paths pass mode, source, and the size gate")


# ---------------------------------------------------------------------------
# 12. Every production module must at least import
# ---------------------------------------------------------------------------

# app.py is excluded on purpose: importing it executes the whole Streamlit
# script body, which renders the UI and makes live Yahoo calls. CI compiles it
# instead (see tests.yml).
def check_no_stale_tranche_notes() -> None:
    """
    A SPENT tranche must not carry a note calling itself held-out.

    THE BUG THIS PINS, 2026-09-11. The lock file's `spent` flag said tranche B
    was consumed on 2026-09-02 (live alerts had been firing on its names), while
    its `note` field still read "Second held-out set. Do not touch until A is
    spent." A session read the note, not the flag, and planned a confirmation
    run on 48 tickers believing 32 of them were clean. The power estimate built
    on that was wrong by a factor of three.

    Two fields describing one fact, free to disagree — the same defect as the
    "spread exceeded 15%, tightest was 13.3%" message and the CI/p mismatch in
    rvol_retest. The flag is the truth; the note is prose that nobody updates.
    """
    import json
    import os
    path = "data_reservation.lock.json"
    if not os.path.exists(path):
        print("  no reservation lock — nothing to check")
        return
    lock = json.load(open(path, encoding="utf-8"))
    claims = ("held-out", "held out", "do not touch", "unspent", "pristine")
    bad = []
    for name, tr in lock.get("reserved", {}).items():
        if not tr.get("spent"):
            continue
        note = (tr.get("note") or "").lower()
        # A corrected note may QUOTE the old claim; the marker is what counts.
        if "spent" in note.split(".")[0]:
            continue
        if any(c in note for c in claims):
            bad.append((name, tr.get("note")))
    if bad:
        raise AssertionError(
            "A SPENT tranche still describes itself as held-out:\n" +
            "\n".join(f"    {n}: {note}" for n, note in bad) +
            "\n  The `spent` flag is the truth and the note is stale prose. "
            "Reading the note instead of the flag is how a confirmation run "
            "got planned on 32 contaminated tickers.")
    n_spent = sum(1 for t in lock.get("reserved", {}).values() if t.get("spent"))
    print(f"  no spent tranche claims to be held-out ({n_spent} spent)")


def check_backlog_coverage_table_current() -> None:
    """
    BACKLOG's test-coverage table must match reality.

    THE PATTERN THIS PINS, which has now bitten three times in two days:

      - the reservation lock said tranche B was "held-out" in `note` while its
        `spent` flag said otherwise, and a confirmation run was planned on 32
        contaminated tickers
      - BACKLOG item 5's table listed option_chain.py and option_backtest.py as
        untested for weeks after both were given tests and wired into CI
      - item 10's body still named option_backtest.py as untested inside an item
        already marked CLOSED

    Every instance is the same shape: prose describing a fact, sitting beside the
    fact, free to drift. Prose does not update itself, so the table is checked
    against the filesystem instead of trusted.
    """
    import os
    import re
    if not os.path.exists("BACKLOG.md"):
        print("  no BACKLOG.md — nothing to check")
        return
    doc = open("BACKLOG.md", encoding="utf-8").read()
    wf = (open(".github/workflows/tests.yml", encoding="utf-8").read()
          if os.path.exists(".github/workflows/tests.yml") else "")

    rows = re.findall(r"^\|\s*`([\w.]+\.py)`\s*\|[^|]*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$",
                      doc, re.M)
    if not rows:
        print("  no coverage table found in BACKLOG.md")
        return

    def truthy(cell: str) -> bool:
        return "yes" in cell.lower()

    bad = []
    for mod, claim_test, claim_ci in rows:
        if not os.path.exists(mod):
            bad.append(f"{mod}: named in the table but the file does not exist")
            continue
        src = open(mod, encoding="utf-8").read()
        real_test = bool(re.search(r"def selftest\s*\(", src)) or "--selftest" in src
        real_ci = f"python {mod}" in wf
        if truthy(claim_test) != real_test:
            bad.append(f"{mod}: table says selftest={claim_test.strip()!r}, "
                       f"actually {real_test}")
        if truthy(claim_ci) != real_ci:
            bad.append(f"{mod}: table says CI={claim_ci.strip()!r}, "
                       f"actually {real_ci}")
    if bad:
        raise AssertionError(
            "BACKLOG.md's coverage table has drifted from the repository:\n" +
            "\n".join(f"    {b}" for b in bad) +
            "\n  The table is prose; the filesystem is the fact. Update the "
            "table, or stop claiming coverage it cannot see.")
    print(f"  BACKLOG coverage table matches the repo ({len(rows)} modules)")


IMPORTABLE_MODULES = [
    "signal_core", "data_source", "rate_limit", "market_context", "gh_sync",
    "journal_store", "bar_cache", "risk_params", "notify",
    "option_chain", "universe", "scanner", "exit_monitor", "backtest",
    "oos_validate", "data_reservation", "excursion_analysis", "churn_tracker",
    "universe_backtest", "option_backtest", "liquidity_check", "vrp_check",
    "spread_backtest", "power_check", "adx_retest", "pead_study",
    "rvol_retest", "record_recheck", "atr_stop_test",
]


def check_cost_gates_shared() -> None:
    """
    The option bid-ask ceiling had FOUR copies: 15.0 in scanner.py, 0.15 twice
    in option_chain.py, 15.0 in app.py's contract check, plus a 10% warning in
    app.py that agreed with none of them. The measured option win rate had two:
    0.238 in app.py and a re-typed 23.8 in risk_params' own selftest.

    That matters more than the usual duplication, because the ceiling is
    DERIVED from the win rate — and the 26.5% this docstring used to quote was
    itself a TP+200 breakeven against a win rate measured at TP+100. At the
    measured basis 15% needs 44.1% and the 8% ceiling needs 38.9%, against 23.8%.
    Two numbers that must move together, spread across five files, will not.

    Both now live in risk_params.py. This check keeps them there.
    """
    import risk_params

    # CORRECTED 2026-09-10. This used to assert
    #
    #     spread_breakeven_wr(cap) < wr < spread_breakeven_wr(cap + 2)
    #
    # i.e. "the ceiling sits where the arithmetic turns". That passed only
    # because spread_breakeven_wr defaulted to TP +200% while OPT_WIN_RATE was
    # measured at TP +100%. The invariant was enforcing the NUMBER while the
    # BASIS underneath it was wrong — so it went green on an unsound comparison,
    # the same shape as guarding the market calendar's dates but not its
    # semantics. What must be pinned is the basis.
    wr = risk_params.OPT_WIN_RATE * 100
    cap = risk_params.MAX_OPTION_SPREAD_PCT

    import inspect
    sig = inspect.signature(risk_params.spread_breakeven_wr).parameters
    assert sig["tp_pct"].default == risk_params.OPT_WIN_RATE_TP_PCT, (
        f"spread_breakeven_wr() defaults to TP {sig['tp_pct'].default}% but "
        f"OPT_WIN_RATE was measured at {risk_params.OPT_WIN_RATE_TP_PCT}%. Every "
        f"caller using the defaults would compare a win rate from one payoff "
        f"against the breakeven of another.")
    assert sig["sl_pct"].default == risk_params.OPT_WIN_RATE_SL_PCT, \
        "the SL basis drifted from the one OPT_WIN_RATE was measured at"

    # And the honest conclusion must stay stated: at the measured basis the
    # structure is negative at every spread, so the ceiling is a loss cap. If a
    # future re-measurement flips this, the comment block in risk_params.py is
    # stale and the ceiling can finally be derived rather than capped.
    at_gate = risk_params.spread_breakeven_wr(cap)
    assert at_gate > wr, (
        f"breakeven at the {cap:g}% ceiling is now {at_gate:.1f}%, BELOW the "
        f"measured {wr:.1f}% — the structure clears breakeven for the first "
        f"time. Re-derive MAX_OPTION_SPREAD_PCT and rewrite the block in "
        f"risk_params.py that says no spread breaks even.")

    # The docs must not re-acquire the old claim, and must keep stating the
    # corrected one. Absence alone is too weak (the block could be deleted
    # wholesale) and presence alone too weak (the false claim could sit beside
    # it), so both are asserted.
    rtxt = Path("risk_params.py").read_text()
    assert "is where the arithmetic turns" not in rtxt, (
        "risk_params.py asserts the spread ceiling sits where the arithmetic "
        "turns. No such point exists at the measured basis — breakeven exceeds "
        "the measured win rate even at ZERO spread, so the ceiling is a loss "
        "cap. Re-read the corrected block before changing this.")
    assert "NO SPREAD AT WHICH THIS CONFIGURATION BREAKS EVEN" in rtxt, (
        "risk_params.py no longer records that the measured option structure is "
        "negative at every spread. That is the finding the ceiling's purpose "
        "rests on; if a re-measurement changed it, update this check on purpose.")

    # A literal ceiling anywhere else is a copy waiting to drift.
    #
    # WIDENED 2026-09-10, after a live QQQI check reported "every spread exceeded
    # 15% of mid — tightest was 13.3%". Both halves cannot be true. The gate was
    # 8% and rejected it correctly; the MESSAGE carried a hardcoded 15. This
    # check was scanning three files for the literals 15.0 / 0.15 and so missed
    # FOUR live copies:
    #
    #   option_chain.py  the rejection message itself — "15%" in an f-string,
    #                    which is not "15.0", so the pattern skipped it
    #   liquidity_check.py  its own MAX_SPREAD_PCT = 15.0 — file not scanned
    #   exit_monitor.py     a bare `> 15` — file not scanned, and not "15.0"
    #   app.py           a caption reading "≤ 15% of mid" — text, not a literal
    #
    # So the invariant guarding against copies of this number had four copies it
    # could not see. It now scans every module and matches a bare 15 near the
    # word spread, prose included.
    stale = re.compile(r"(?<![\w.])(?:15(?:\.0)?|0\.15)\s*%?(?![\w.])")
    for path in ("app.py", "scanner.py", "option_chain.py",
                 "liquidity_check.py", "exit_monitor.py"):
        txt = Path(path).read_text()
        assert "risk_params" in txt, (
            f"{path} no longer reads risk_params.py — the spread ceiling can "
            f"drift away from the win rate it is derived from.")
        for i, line in enumerate(txt.splitlines(), 1):
            code = line.split("#", 1)[0]
            if "spread" not in code.lower():
                continue
            if stale.search(code):
                raise AssertionError(
                    f"{path}:{i} carries its own spread ceiling: {line.strip()!r}. "
                    f"Read risk_params.MAX_OPTION_SPREAD_PCT instead — this is "
                    f"the fourth copy of that number, and they disagreed.")

    atxt = Path("app.py").read_text()
    assert "OPT_WIN_RATE = 0.238" not in atxt, (
        "app.py hardcodes the option win rate again. It is the input to the "
        "spread ceiling; a second copy lets the gate and the number it is "
        "derived from move apart silently.")
    assert "risk_params.OPT_WIN_RATE" in atxt, (
        "app.py's expected-value line must read risk_params.OPT_WIN_RATE.")

    print(f"  spread ceiling {cap:g}% derived from OPT_WIN_RATE {wr:.1f}%, "
          f"one copy of each")


# ---------------------------------------------------------------------------
# 17. Every production module must import
# ---------------------------------------------------------------------------

def check_modules_import() -> None:
    import importlib
    for name in IMPORTABLE_MODULES:
        try:
            importlib.import_module(name)
        except Exception as e:
            raise AssertionError(
                f"import {name} failed: {type(e).__name__}: {e}") from e
    print(f"  all {len(IMPORTABLE_MODULES)} importable modules import cleanly")


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 18. compute() must carry Open/Date with their own indicator row
# ---------------------------------------------------------------------------

def check_compute_preserves_alignment() -> None:
    """
    Every bar's Open and Date must still belong to the bar whose indicators sit
    beside them after compute().

    THE BUG THIS PINS. backtest.run(), option_backtest and universe_backtest all
    re-attached Open/Date with `raw.tail(len(df))`, which assumes compute() drops
    rows only from the FRONT. It does not: dropna() removes a row wherever any
    indicator is NaN, and VOL_AVG20 is a 20-bar rolling mean, so ONE missing
    Volume anywhere deletes 20 INTERIOR rows. Tail-alignment then paired earlier
    indicator rows with an Open and Date from 20 sessions later.

    That reached the numbers, not just the reporting: simulate_trade() takes the
    fill from df["Open"], and the SPY-regime join keys on df["Date"]. It was
    silent, and option_backtest is where OPT_WIN_RATE is measured — the input the
    option spread ceiling is derived from.

    The fixture injects an interior NaN deliberately, and asserts that the old
    method would have DISAGREED. Without that second assertion this test passes
    on a clean frame where both methods happen to agree — a dead fixture of
    exactly the kind the falsification pass keeps finding here.
    """
    import numpy as np
    import pandas as pd
    import backtest as bt

    n = 400
    rng = np.random.default_rng(0)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    raw = pd.DataFrame({
        "Date": pd.bdate_range("2024-01-02", periods=n),
        "Open": close.values, "High": close.values + 1,
        "Low": close.values - 1, "Close": close.values,
        "Volume": np.full(n, 1_000_000.0),
    })
    raw.loc[n // 2, "Volume"] = np.nan      # one bad bar, 20 interior rows lost

    out = bt.compute(raw)
    assert len(out) > 50, f"fixture degenerate: compute() kept only {len(out)} rows"

    # Ground truth: re-run compute()'s own row filter while KEEPING the index.
    probe = raw.copy()
    c, h, l = probe["Close"], probe["High"], probe["Low"]
    import ta
    probe["EMA20"] = ta.trend.ema_indicator(c, window=20)
    probe["EMA50"] = ta.trend.ema_indicator(c, window=50)
    _m = ta.trend.MACD(c)
    probe["MACD"], probe["Signal"] = _m.macd(), _m.macd_signal()
    probe["RSI"] = ta.momentum.rsi(c, window=14)
    probe["ATR"] = ta.volatility.average_true_range(h, l, c, window=14)
    probe["ADX"] = ta.trend.adx(h, l, c, window=14)
    probe["VOL_AVG20"] = probe["Volume"].rolling(20).mean()
    probe = probe.dropna(subset=["EMA20", "EMA50", "MACD", "Signal", "RSI",
                                 "ATR", "ADX", "VOL_AVG20"])
    if len(probe) > bt.WARMUP_BARS + bt.MIN_BARS_AFTER:
        probe = probe.iloc[bt.WARMUP_BARS:]
    truth = raw.loc[probe.index, ["Date", "Open"]].reset_index(drop=True)

    assert len(truth) == len(out), (
        f"compute() kept {len(out)} rows, the reference filter kept {len(truth)} "
        f"— compute()'s row filter changed; update this check deliberately.")

    bad_dates = int((pd.to_datetime(out["Date"]).values != truth["Date"].values).sum())
    assert bad_dates == 0, (
        f"{bad_dates} of {len(out)} rows carry a Date that belongs to a "
        f"different bar. Open/Date must travel THROUGH compute() with their own "
        f"row — never be re-attached positionally afterwards.")
    assert np.allclose(out["Open"].values, truth["Open"].values), (
        "Open is misaligned with its indicator row — simulate_trade() would "
        "fill at a price from the wrong bar.")

    # LIVENESS: the discarded method must actually differ on this fixture, or
    # the two assertions above prove nothing.
    stale = raw.tail(len(out)).reset_index(drop=True)
    drift = int((pd.to_datetime(stale["Date"]).values != truth["Date"].values).sum())
    assert drift > 0, (
        "fixture is DEAD: raw.tail(len(df)) agrees with the truth here, so this "
        "check would pass even with the bug restored. The interior NaN is not "
        "dropping rows — re-derive the fixture.")

    # And no module may reintroduce it.
    for path in ("backtest.py", "option_backtest.py", "universe_backtest.py"):
        txt = Path(path).read_text()
        for i, line in enumerate(txt.splitlines(), 1):
            code = line.split("#", 1)[0]
            if ".tail(len(" in code:
                raise AssertionError(
                    f"{path}:{i} re-attaches columns by tail position: "
                    f"{line.strip()!r}. compute() already carries Open/Date on "
                    f"the correct row; tail-alignment silently corrupts both "
                    f"whenever an interior row is dropped.")
    print(f"  compute() keeps Open/Date on their own row "
          f"({len(out)} rows; tail-alignment would have broken {drift})")
    print("  no module re-attaches Open/Date by tail position")


# ---------------------------------------------------------------------------
# 19. the four is_market_open() copies must BEHAVE the same, not just share dates
# ---------------------------------------------------------------------------

def check_market_hours_agree() -> None:
    """
    check_calendars_identical() pins the calendar DATA across five files. Nothing
    pinned the LOGIC that reads it, and four modules implement it:
    signal_core.py (canonical), app.py, scanner.py and exit_monitor.py.

    They had already drifted in one respect that mattered: signal_core's default
    clock was naive `datetime.now()` while the other three defaulted to ET. On a
    UTC host that inverts the answer for most of the session, and
    drop_partial_bar() trusts it — so the canonical module was the one carrying
    the trap.

    Guarding dates but not semantics is how the option spread ceiling stayed
    green on an unsound comparison for a day. Same lesson, different file.

    app.py cannot be imported here (it renders Streamlit and fetches prices on
    import), so it is checked statically for the two properties that matter.
    """
    import importlib
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ET = ZoneInfo("America/New_York")
    mods = {name: importlib.import_module(name)
            for name in ("signal_core", "scanner", "exit_monitor")}

    # Boundaries and the cases the calendar exists for. Each is (dt, why).
    cases = [
        (datetime(2026, 9, 9, 10, 0, tzinfo=ET),  "ordinary Wednesday, mid-session"),
        (datetime(2026, 9, 9, 9, 29, tzinfo=ET),  "one minute before the open"),
        (datetime(2026, 9, 9, 9, 30, tzinfo=ET),  "the open itself"),
        (datetime(2026, 9, 9, 16, 1, tzinfo=ET),  "one minute after the close"),
        (datetime(2026, 9, 12, 11, 0, tzinfo=ET), "Saturday"),
        (datetime(2026, 11, 26, 11, 0, tzinfo=ET), "Thanksgiving — a full closure"),
        (datetime(2026, 11, 27, 12, 0, tzinfo=ET), "half-day, before 1:00pm"),
        (datetime(2026, 11, 27, 14, 0, tzinfo=ET), "half-day, after the 1:00pm close"),
        (datetime(2026, 7, 3, 11, 0, tzinfo=ET),  "observed Independence Day"),
    ]

    for dt, why in cases:
        answers = {n: bool(m.is_market_open(dt)) for n, m in mods.items()}
        if len(set(answers.values())) != 1:
            raise AssertionError(
                f"is_market_open() disagrees on {dt:%Y-%m-%d %H:%M} ({why}): "
                f"{answers}. Four copies of this function decide whether the "
                f"last daily bar is complete; they cannot differ.")

    # LIVENESS: the cases must actually exercise both answers, or agreement is
    # vacuous (four functions returning True always would pass).
    verdicts = {bool(mods["signal_core"].is_market_open(dt)) for dt, _ in cases}
    assert verdicts == {True, False}, (
        f"the sampled timestamps only ever produce {verdicts} — this check "
        f"would pass on a function that ignores its argument. Re-derive them.")

    # Holidays and half-days must be the REASON for the closures above, not a
    # coincidence of the clock.
    assert not mods["signal_core"].is_market_open(
        datetime(2026, 11, 26, 11, 0, tzinfo=ET)), "Thanksgiving must be closed"
    assert mods["signal_core"].is_market_open(
        datetime(2026, 11, 25, 11, 0, tzinfo=ET)), \
        "the day BEFORE Thanksgiving is a normal session — if this fails the "\
        "holiday test above proves nothing about holidays"

    # No copy may default to the host's local clock.
    import inspect
    for name, m in mods.items():
        src = inspect.getsource(m.is_market_open)
        assert "datetime.now()" not in src.replace(" ", ""), (
            f"{name}.is_market_open() defaults to naive datetime.now(). The "
            f"calendar is an ET calendar; on a UTC host this is wrong in both "
            f"directions and drop_partial_bar() trusts the answer.")

    atxt = Path("app.py").read_text()
    i = atxt.find("def is_market_open(")
    assert i > 0, "app.py no longer defines is_market_open — update this check"
    body = atxt[i:i + 900]
    assert "America/New_York" in body, (
        "app.py's is_market_open() no longer resolves an Eastern-time clock.")
    assert "MARKET_HOLIDAYS" in body and "MARKET_HALF_DAYS" in body, (
        "app.py's is_market_open() stopped consulting the holiday/half-day "
        "calendar — it would report the market open on Thanksgiving.")

    print(f"  {len(cases)} timestamps, 3 importable copies agree "
          f"(open and closed both exercised)")
    print("  no copy defaults to the host's local clock; app.py checked statically")


# ---------------------------------------------------------------------------
# 20. the reservation lock must agree with itself
# ---------------------------------------------------------------------------

def check_reservation_self_consistent() -> None:
    """
    data_reservation.py is the only thing standing between this project and
    re-testing data it has already seen. Two holes made it advisory rather than
    binding, and both are the same shape as the option-gate basis bug: the guard
    enforced a value while the meaning underneath went unchecked.

    1. reservation_hash() covers the tranche DEFINITIONS in the module, and
       RESERVED has no `spent` key. So editing "spent": true -> false directly in
       the lock JSON left the stored hash matching the code hash exactly, and
       status() then printed "Clean shots remaining: 2 (A, B)" four lines above
       its own ledger entry recording that B had been spent by live trading.
       lock_state_hash() now covers the mutable half.

    2. spend() is append-only with no un-spend path, so a ledger entry for a
       tranche whose flag reads False cannot have come from the tool. The lock
       carried exactly that for tranche A. reconcile() reports it, and an
       annulment is how the legitimate case gets DECLARED instead of effected by
       editing a flag.

    This check fails CI if either ever regresses, so "Tranche A is unspent" is a
    statement the repo can defend rather than one it merely repeats.
    """
    import data_reservation as dr

    lock = dr.load_lock()
    if lock is None:
        raise AssertionError(
            "no data_reservation.lock.json — the reservation is unenforced. "
            "Run: python data_reservation.py --init")

    problems = dr.reconcile(lock)
    assert not problems, (
        "the reservation lock disagrees with its own ledger:\n    "
        + "\n    ".join(problems))

    stored = lock.get("state_hash")
    assert stored is not None, (
        "the lock has no state_hash, so edits to the spend flags or the ledger "
        "are undetectable. Any write through save_lock() adds it.")
    assert stored == dr.lock_state_hash(lock), (
        f"the spend state has been edited outside the tool: stored "
        f"{stored}, recomputed {dr.lock_state_hash(lock)}. spend() never "
        f"un-spends, so treat every 'available' tranche as unverified.")

    # A spent tranche must block a NEW hypothesis, and allow only the run it was
    # claimed for. Spending used to make a tranche clean again.
    spent = [(n, tr) for n, tr in lock["reserved"].items() if tr.get("spent")]
    for name, tr in spent:
        sample = tr["tickers"][:3]
        r = dr.check_clean(sample, purpose="an unrelated new hypothesis")
        assert r["clean"] is False and name in r["spent"], (
            f"tranche {name} is SPENT but check_clean() reports {sample} as "
            f"clean for a new hypothesis. One clean shot has to mean one.")
        claimed = (tr.get("spent_on") or "").strip()
        if claimed:
            ok = dr.check_clean(sample, purpose=claimed)
            assert ok["clean"] is True, (
                f"tranche {name} no longer validates for the purpose it was "
                f"actually claimed for ({claimed!r}) — the gate is now too "
                f"tight to run the test the tranche was spent on.")

    # An unspent tranche must still block outright.
    unspent = [(n, tr) for n, tr in lock["reserved"].items()
               if not tr.get("spent")]
    for name, tr in unspent:
        r = dr.check_clean(tr["tickers"][:3], purpose="anything")
        assert r["clean"] is False and name in r["reserved"], (
            f"tranche {name} is UNSPENT and must block any test until it is "
            f"claimed deliberately")

    avail = [n for n, tr in lock["reserved"].items()
             if not tr.get("spent")
             and not any(e.get("tranche") == n for e in lock.get("ledger", []))
             or (not tr.get("spent") and tr.get("annulled"))]
    print(f"  lock agrees with its ledger; state_hash {stored} verified")
    print(f"  {len(spent)} spent tranche(s) block new hypotheses, "
          f"{len(unspent)} unspent block outright")
    print(f"  clean shots defensible: {', '.join(sorted(avail)) or 'none'}")


# ---------------------------------------------------------------------------
# 21. the weekly filter cannot be switched on while live and backtest disagree
# ---------------------------------------------------------------------------

def check_weekly_rule_parity() -> None:
    """
    The live weekly trend and the backtested one are different rules.

    market_context.weekly_trend_from_bars() reads close.iloc[-1] — the CURRENT
    weekly bar, which does not close until Friday. backtest.build_weekly_trend_map()
    shifts each week's verdict so it is only usable from the following week,
    because using it mid-week leaks Thursday and Friday into a Wednesday
    decision; its own docstring calls that "the whole difficulty here".

    That divergence is harmless today only because
    signal_core.DEFAULTS.weekly_confirm is False, so the live verdict gates
    nothing. The danger is the flip: turning the filter on would put a rule into
    production that no backtest has measured, while the backtest that justified
    it measured a LAGGED version. Nothing connected those two facts.

    This check is the connection. While the filter is off it simply records the
    divergence. If it is ever turned on, CI fails and says what has to happen
    first.
    """
    import inspect
    import signal_core as sc
    import market_context as mc

    live_src = inspect.getsource(mc.weekly_trend_from_bars)
    reads_current_week = "iloc[-1]" in live_src.replace(" ", "")

    if not sc.DEFAULTS.weekly_confirm:
        assert reads_current_week, (
            "market_context.weekly_trend_from_bars() no longer reads the current "
            "weekly bar. If it was changed to lag like the backtest, that is the "
            "fix this check was waiting for — update this check and re-measure "
            "the filter before enabling it.")
        # The divergence must stay documented where a reader will meet it.
        assert "does not CLOSE until" in inspect.getdoc(mc.weekly_trend_from_bars), (
            "the docstring no longer warns that this reads an unfinished week. "
            "That warning is the only thing standing between a reader and a "
            "rule the backtest never measured.")
        print("  weekly filter OFF; live reads the unfinished week, backtest "
              "lags it — divergence recorded, gating nothing")
        return

    raise AssertionError(
        "signal_core.DEFAULTS.weekly_confirm is now TRUE, but\n"
        "  market_context.weekly_trend_from_bars() still reads the CURRENT,\n"
        "  unfinished weekly bar while backtest.build_weekly_trend_map() uses\n"
        "  the previous week's closed verdict.\n"
        "  Enabling the filter now ships a rule no backtest has measured.\n"
        "  Either lag the live rule to match the backtest, or re-measure the\n"
        "  filter with the live (unlagged) definition and record the result —\n"
        "  then update this check deliberately.")


# ---------------------------------------------------------------------------
# 22. one decision about whether a bar has settled
# ---------------------------------------------------------------------------

def check_unsettled_bar_single_source() -> None:
    """
    Every module that reads a price frame must route "has this bar settled?"
    through signal_core.drop_unsettled().

    This defect was found and fixed SIX times — signal_core, app.py,
    backtest (_drop_todays_bar), universe (drop_unsettled),
    exit_monitor (drop_unsettled_bars), and scanner via signal_core — under four
    names, each fix local. The cost of forgetting it, in order: alerts that
    appeared and vanished with the clock, an uncacheable backtest, a universe
    snapshot that depended on the time of day, and live option positions closed
    on an intraday dip that recovered.

    The copies had also DRIFTED, which is the part a per-module fix cannot catch:
    the live ones asked `is_market_open()` and dropped iloc[-1], so they kept
    today's bar BEFORE the open (Yahoo emits it well ahead of 09:30) and
    identified the wrong row. backtest._drop_todays_bar()'s docstring named that
    exact gap — "every live path leaves it in place" — and fixed it only for
    itself.

    So this does not check that each module drops a bar. It checks there is ONE
    implementation and five delegations, and that the two policies stay distinct.
    """
    import signal_core as sc

    # The two policies must actually differ, or routing everything through one
    # function has quietly collapsed them.
    import pandas as pd
    from datetime import datetime
    ref = datetime(2026, 8, 25, 17, 0, tzinfo=sc._ET)      # after the close
    idx = pd.bdate_range(end=pd.Timestamp("2026-08-25"), periods=5)
    probe = pd.DataFrame({"Close": [1, 2, 3, 4, 5.0]}, index=idx)
    _, n_always = sc.drop_unsettled(probe, policy=sc.DROP_ALWAYS, now=ref)
    _, n_live = sc.drop_unsettled(probe, policy=sc.DROP_UNTIL_CLOSE, now=ref)
    assert n_always == 1 and n_live == 0, (
        f"the two policies no longer differ after the close "
        f"(ALWAYS dropped {n_always}, UNTIL_CLOSE dropped {n_live}). "
        f"Reproducible paths need the bar gone even once it settles; live paths "
        f"need it kept. Collapsing them silently changes one of the two.")

    # And the pre-open case, which is the regression the consolidation fixed.
    pre = datetime(2026, 8, 25, 7, 0, tzinfo=sc._ET)
    _, n_pre = sc.drop_unsettled(probe, policy=sc.DROP_UNTIL_CLOSE, now=pre)
    assert n_pre == 1, (
        "DROP_UNTIL_CLOSE must drop today's bar BEFORE the open. It is a stub "
        "there, not settled data, and `not is_market_open()` is true then — "
        "which is exactly how the old live rule kept it.")

    # Five delegating call sites, one implementation.
    delegators = {
        "app.py": "drop_partial_bar",
        "backtest.py": "_drop_todays_bar",
        "universe.py": "drop_unsettled",
        "exit_monitor.py": "drop_unsettled_bars",
    }
    for path, fname in delegators.items():
        txt = Path(path).read_text()
        i = txt.find(f"def {fname}(")
        assert i > 0, f"{path} no longer defines {fname}() — update this check"
        body = txt[i:i + 3000]
        end = body.find("\ndef ", 1)
        if end > 0:
            body = body[:end]
        # Skip the `def` line itself. universe.py's delegator is also CALLED
        # drop_unsettled, so a bare-name search was satisfied by its own
        # signature and the check passed with the delegation removed. Found by
        # falsification; the fix is to require a QUALIFIED call below.
        body = body.split("\n", 1)[1] if "\n" in body else ""
        # COMMENT-STRIPPED, and this mattered: the first version of this check
        # searched the raw body, which every delegator satisfied with the comment
        # "ROUTED THROUGH signal_core.drop_unsettled()". The guard passed on
        # prose while the code underneath had been replaced by a hand-rolled date
        # compare. Found by falsification, which is the only reason it is not
        # still passing.
        code = "\n".join(l.split("#", 1)[0] for l in body.splitlines())
        qualified = ("sc.drop_unsettled(" in code
                     or "signal_core.drop_unsettled(" in code)
        assert qualified, (
            f"{path}:{fname}() no longer delegates to "
            f"signal_core.drop_unsettled(). That is the sixth copy of this "
            f"decision starting over; the last five drifted. The call must be "
            f"QUALIFIED (sc.drop_unsettled / signal_core.drop_unsettled) — a "
            f"bare name matched universe.py's own identically-named wrapper.")
        assert "iloc[:-1]" not in code.replace(" ", ""), (
            f"{path}:{fname}() identifies the unsettled bar by POSITION again. "
            f"It must be identified by DATE — iloc[-1] is the wrong row before "
            f"the open, when today's stub is present but is not the only bar.")

    # scanner reaches it through signal_core rather than its own copy.
    stxt = Path("scanner.py").read_text()
    assert "def drop_partial_bar" not in stxt and "def drop_unsettled" not in stxt, (
        "scanner.py has grown its own unsettled-bar function. It calls "
        "signal_core's; a local copy is how the other five drifted.")

    # signal_core is the only implementation: nothing else may compare bar dates
    # against today by hand.
    for path in ("app.py", "scanner.py", "universe.py", "exit_monitor.py"):
        txt = Path(path).read_text()
        for i, line in enumerate(txt.splitlines(), 1):
            code = line.split("#", 1)[0].replace(" ", "")
            # Any "<pd.Timestamp(...date())" shape, whatever the variable is
            # called. The first version pinned the variable name `df.index` and a
            # rename walked straight past it.
            if "<pd.Timestamp" in code and (".date()" in code or "today" in code):
                raise AssertionError(
                    f"{path}:{i} compares bar dates against today directly: "
                    f"{line.strip()!r}. Call signal_core.drop_unsettled() — "
                    f"this is how six copies of one decision happened.")

    print(f"  one implementation in signal_core, {len(delegators)} delegations "
          f"+ scanner via signal_core")
    print("  policies distinct (ALWAYS drops a settled today-bar, UNTIL_CLOSE "
          "keeps it) and pre-open covered")


def check_duplicated_constants_agree() -> None:
    """
    A CONSTANT defined in more than one module must hold the same value.

    This repo has already paid for this once: the option bid-ask ceiling had five
    copies that disagreed, and the measured option win rate had two. Those were
    found by hand, then single-sourced. Nothing stops the next one.

    Four constants are currently duplicated and all four agree:

        MIN_RHO       adx_retest, pead_study          the dose-response floor
        ALPHA         adx_retest, atr_stop_test, pead_study
        RISK_FREE     option_backtest, spread_backtest
        TRADING_DAYS  option_backtest, spread_backtest, vrp_check

    Duplication is not itself the failure — a study module pinning its own
    pre-registered alpha is reasonable, and forcing them into one file would
    couple modules that should stay independent. The failure is duplication that
    DRIFTS, so this checks agreement rather than forbidding the copies.
    """
    import re
    import pathlib
    seen: dict[str, list[tuple[str, str]]] = {}
    for f in sorted(pathlib.Path(".").glob("*.py")):
        if f.name == "consistency_check.py":
            continue
        for m in re.finditer(r"^([A-Z][A-Z0-9_]{2,})\s*=\s*([-+]?\d+\.?\d*)\s*$",
                             f.read_text(), re.M):
            seen.setdefault(m.group(1), []).append((f.name, m.group(2)))

    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    assert dupes, (
        "no duplicated numeric constants found at all — either every copy was "
        "single-sourced (say so here) or this check's regex stopped matching")

    for name, where in sorted(dupes.items()):
        values = {val for _, val in where}
        assert len(values) == 1, (
            f"{name} disagrees across modules: "
            + ", ".join(f"{f}={v}" for f, v in where)
            + " — a constant with two values is the bug this check exists for")

    total = sum(len(v) for v in dupes.values())
    print(f"  {len(dupes)} constants duplicated across {total} definitions, all agreeing")


CHECKS = [
    ("market calendars identical across 5 copies", check_calendars_identical),
    ("market calendar has runway left",            check_calendar_runway),
    ("compute() agrees across modules",            check_compute_agrees),
    ("app.py compute() indicator set intact",      check_app_compute_source),
    ("app.py defaults derive from signal_core",    check_app_defaults_derived),
    ("backtest.evaluate_signal callers correct",   check_backtest_callers),
    ("weekly trend + SPY regime are one rule",     check_market_context_shared),
    ("live universe spends no reserved data",     check_universe_not_spending_reserved),
    ("scan failures are surfaced, not swallowed",  check_scan_failures_surfaced),
    ("backtest prices match the live paths",       check_adjustment_matches_live),
    ("oos_validate parses backtest.py's output",   check_oos_parses_backtest_output),
    ("unattended modules are Streamlit-free",      check_unattended_modules_are_streamlit_free),
    ("sizing constants live in one place",         check_sizing_constants_shared),
    ("app can reach the price fallback key",       check_app_can_reach_fallback_key),
    ("paper/live split + sizing gates",            check_journal_and_sizing_guards),
    ("option cost gates share one source",         check_cost_gates_shared),
    ("compute() keeps Open/Date on their row",     check_compute_preserves_alignment),
    ("is_market_open agrees across 4 copies",      check_market_hours_agree),
    ("reservation lock agrees with itself",        check_reservation_self_consistent),
    ("weekly filter off while rules diverge",      check_weekly_rule_parity),
    ("unsettled-bar decision has one source",      check_unsettled_bar_single_source),
    ("no spent tranche claims to be held-out",     check_no_stale_tranche_notes),
    ("BACKLOG coverage table is current",          check_backlog_coverage_table_current),
    ("duplicated constants agree",                 check_duplicated_constants_agree),
    ("every production module imports",            check_modules_import),
]


def selftest() -> int:
    failures = []
    for title, fn in CHECKS:
        print(f"\n{title}")
        try:
            fn()
        except AssertionError as e:
            failures.append((title, str(e)))
            print(f"  FAIL: {e}")

    print("\n" + "=" * 70)
    if failures:
        print(f"{len(failures)} of {len(CHECKS)} consistency checks FAILED:")
        for title, _ in failures:
            print(f"  - {title}")
        return 1
    print(f"All {len(CHECKS)} cross-module consistency checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(selftest())
