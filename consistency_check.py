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
# 6. Every production module must at least import
# ---------------------------------------------------------------------------

# app.py is excluded on purpose: importing it executes the whole Streamlit
# script body, which renders the UI and makes live Yahoo calls. CI compiles it
# instead (see tests.yml).
IMPORTABLE_MODULES = [
    "signal_core", "data_source", "rate_limit", "market_context", "gh_sync",
    "journal_store", "bar_cache",
    "option_chain", "universe", "scanner", "exit_monitor", "backtest",
    "oos_validate", "data_reservation", "excursion_analysis", "churn_tracker",
    "universe_backtest", "option_backtest", "liquidity_check",
]


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
