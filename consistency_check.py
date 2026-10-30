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

# THE TWO PERSONAL ACCESS TOKENS EXPIRE IN THE SAME WEEK (BACKLOG, scheduler
# item, "FORCING FUNCTION — TWO PATs"). When the dispatch token dies every
# cron-job.org dispatch is refused and the scanner falls back to GitHub's own
# schedule, which fires 40-110 minutes late against a two-hour post-close
# window; when the app token dies every app write fails (the 2026-10-13
# incident). Nothing enforced the rotation the way the calendar check above
# enforces the holiday list, so this does: it fails TOKEN_MIN_RUNWAY_DAYS
# before the earlier expiry. Rotating a token = replacing the secret AND
# moving its date here, in one commit that says so.
TOKEN_EXPIRY = {
    "app PAT (Streamlit secret GITHUB_TOKEN; Contents read/write)": "2027-01-05",
    "cron-job.org dispatch PAT (workflow_dispatch on scanner.yml)":  "2027-01-06",
}
TOKEN_MIN_RUNWAY_DAYS = 21

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


def check_token_runway(today: date | None = None) -> None:
    today = today or date.today()
    for name, exp in TOKEN_EXPIRY.items():
        d = datetime.strptime(exp, "%Y-%m-%d").date()
        runway = (d - today).days
        print(f"  {name}: expires {exp} — {runway} days of runway "
              f"(minimum {TOKEN_MIN_RUNWAY_DAYS})")
        if runway < TOKEN_MIN_RUNWAY_DAYS:
            raise AssertionError(
                f"The {name} expires {exp}, {runway} days away.\n"
                f"  Rotate it now (both tokens are rotated in one sitting; see "
                f"BACKLOG 'FORCING FUNCTION — TWO PATs'), then move its date in "
                f"TOKEN_EXPIRY to the new expiry in the same commit. A dead "
                f"dispatch token leaves the scanner on GitHub's late schedule; a "
                f"dead app token makes every app write fail.")


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


def check_earnings_gate_shared() -> None:
    """
    The earnings blackout is ONE implementation, and it reads EVERY date.

    THE BUG THIS PINS. app.py and scanner.py each carried their own copy. The
    window semantics agreed, so a constants check would have passed — but the
    PARSING did not: app read only the first element of the calendar's
    "Earnings Date", scanner iterated all of them. yfinance returns that field
    as a RANGE of two estimates for one event more often than not, so on a
    ticker where the blackout matched the second estimate the app said CLEAR
    and the scanner said BLOCKED, on the same ticker at the same moment.

    Checked three ways, because a source grep alone is what let the last
    duplicated rule sit here for months:
      1. both callers delegate (source — app.py cannot be imported, it is a
         Streamlit script that renders UI and fetches on import);
      2. neither carries the date-window arithmetic any more;
      3. the shared rule is DRIVEN, through scanner's real call path, on the
         exact two-estimate case the two used to disagree on.
    """
    import datetime as _dt
    import market_context as mc

    for path in ("app.py", "scanner.py"):
        txt = Path(path).read_text()
        i = txt.index("def check_earnings_blackout(")
        body = txt[i:i + 2500]
        if "market_context.get_earnings_blackout" not in body:
            raise AssertionError(
                f"{path}: check_earnings_blackout() no longer delegates to "
                f"market_context.get_earnings_blackout(). Two copies of this "
                f"gate is how the app and the scanner reached opposite "
                f"verdicts on the same ticker.")
        # A reimplementation would have to do the window arithmetic itself.
        for tell in ("POST_EARNINGS_DAYS <=", "<= EARNINGS_DAYS",
                     "<= EARNINGS_BLACKOUT_DAYS"):
            if tell in body:
                raise AssertionError(
                    f"{path}: check_earnings_blackout() computes the blackout "
                    f"window itself ({tell!r}). The window lives in "
                    f"market_context.earnings_blackout_from_dates() and "
                    f"nowhere else.")
        print(f"  {path:12} delegates the earnings gate, carries no window of its own")

    # ── DRIVEN, not grepped: the two-estimate range both callers now share ──
    import scanner as sc
    _today = _dt.date(2026, 9, 16)
    # The EARLIER date is long past and clears; the LATER one is inside the
    # window. calendar_dates() sorts, so a "first date only" reimplementation
    # reads the harmless one — which is precisely how app.py used to clear a
    # ticker the scanner blocked. A fixture ordered the other way would be
    # rescued by the sort and would pass on the bug it exists to catch.
    _range = {"Earnings Date": ["2026-08-20", "2026-09-18"]}
    _real_cal, _real_now = sc._earnings_calendar, sc.datetime
    try:
        sc._earnings_calendar = lambda t: _range

        class _Clock:                      # freeze scanner's ET clock
            @staticmethod
            def now(tz=None):
                return _dt.datetime(2026, 9, 16, 12, 0)
        sc.datetime = _Clock
        ok, why = sc.check_earnings_blackout("ZZ")
    finally:
        sc._earnings_calendar, sc.datetime = _real_cal, _real_now

    if ok:
        raise AssertionError(
            f"the earnings gate cleared a ticker whose SECOND calendar estimate "
            f"(2026-09-18) is 2 days out, inside the 3-day blackout: {why!r}. "
            f"Reading only the first date is exactly what app.py used to do.")
    # And the fixture must depend on that second date, or it proves nothing.
    if not mc.earnings_blackout_from_dates([_dt.date(2026, 8, 20)], _today)[0]:
        raise AssertionError(
            "the fixture's EARLIER date already blocks, so this check would "
            "pass even on the single-date bug it exists to catch")
    print(f"  driven: blocks on the later estimate too — {why}")

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
        # An import inside `try: ... except ImportError:` is OPTIONAL: the
        # module runs without it (option_chain.py's Streamlit cache
        # decorator, group C 2026-10-28). Everything else is a hard edge.
        guarded: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try) and any(
                    (isinstance(h.type, ast.Name) and h.type.id in ("ImportError", "ModuleNotFoundError"))
                    or (isinstance(h.type, ast.Tuple) and any(
                        isinstance(e, ast.Name) and e.id in ("ImportError", "ModuleNotFoundError")
                        for e in h.type.elts))
                    for h in node.handlers):
                for inner in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                    if isinstance(inner, (ast.Import, ast.ImportFrom)):
                        guarded.add(id(inner))
        out = set()
        for node in ast.walk(tree):
            if id(node) in guarded:
                continue
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
    # ONE implementation of the rule, imported rather than copied. Two rules
    # describing one fact are free to disagree, which is the defect this very
    # check exists to catch — and amend_note() enforcing a stricter rule than
    # this one would refuse to sync a note the check considers fine.
    import data_reservation as _dr
    bad = []
    for name, tr in lock.get("reserved", {}).items():
        if _dr.note_claims_held_out(tr.get("note"), bool(tr.get("spent"))):
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


def check_capital_never_gates_alerts() -> None:
    """
    Capital may be computed and shown. It must never suppress an alert.

    Asked for explicitly: "I don't want to filter trades based on cap — alert
    irrespective of cap." The scanner today does the right thing: an
    over-budget contract gets a warning line appended and the alert still goes
    out. This stops that drifting, because "skip the ones we can't afford"
    looks like a kindness in a diff.

    Encoded structurally rather than by promise: no branch in scanner.py may
    both TEST a capital fact and RETURN or CONTINUE out of the alert path. The
    scanner is free to read ACCOUNT_SIZE, size a contract and warn about the
    budget — it may not act on any of it by staying silent.
    """
    import ast as _ast
    capital = {"within_budget", "budget", "ACCOUNT_SIZE", "account_size",
               "pct_account", "MAX_POSITION_PCT", "cost", "equity"}
    src = Path("scanner.py").read_text()
    tree = _ast.parse(src)
    bad = []
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.If):
            continue
        names = {n.id for n in _ast.walk(node.test) if isinstance(n, _ast.Name)}
        names |= {n.attr for n in _ast.walk(node.test)
                  if isinstance(n, _ast.Attribute)}
        names |= {k.value for k in _ast.walk(node.test)
                  if isinstance(k, _ast.Constant) and isinstance(k.value, str)}
        if not (names & capital):
            continue
        for stmt in _ast.walk(node):
            # `return None` is Return(value=Constant(None)); only a BARE
            # `return` has value=None. Checking the latter alone missed the
            # scanner's actual suppression pattern entirely — analyze() signals
            # "no alert" by returning None, and the first version of this check
            # sailed past a planted `if ACCOUNT_SIZE < X: return None`.
            leaves = isinstance(stmt, _ast.Continue) or (
                isinstance(stmt, _ast.Return) and (
                    stmt.value is None
                    or (isinstance(stmt.value, _ast.Constant)
                        and stmt.value.value is None)))
            if leaves:
                bad.append((node.lineno, sorted(names & capital)))
                break
    if bad:
        raise AssertionError(
            f"scanner.py branches on capital and then leaves the alert path:\n"
            + "\n".join(f"    line {ln}: tests {ns} then returns/continues"
                         for ln, ns in bad)
            + "\n  Capital may be computed and SHOWN, never used to stay "
              "silent. An over-budget contract gets a warning and the alert "
              "still goes out.")
    print(f"  capital informs alerts, never suppresses them "
          f"({len(capital)} symbols checked)")


def check_forward_log_has_no_test_data() -> None:
    """
    The committed forward log must contain no fixture rows.

    THE BUG THIS PINS. scanner.analyze() writes to forward_log.LOG, and the
    scanner's own selftest drove analyze() before redirecting that path — so
    every run appended two fabricated "ZZ" rows to the real log. Twenty two
    reached a commit, and CI would have added more on every push.

    A research record that quietly accumulates test fixtures is worse than no
    record: it looks like evidence. This is cheap and catches the whole family
    — any fixture ticker, from any module, however it got there.
    """
    import json
    import os
    path = "forward_log.jsonl"
    if not os.path.exists(path):
        print("  forward log not started yet — nothing to check")
        return
    fixtures = {"ZZ", "AAA", "BBB", "CCC", "XXX", "TEST", "BT"}
    bad = []
    for i, line in enumerate(open(path, encoding="utf-8"), start=1):
        line = line.strip()
        if not line:
            continue
        tk = (json.loads(line).get("ticker") or "").upper()
        if tk in fixtures:
            bad.append((i, tk))
    if bad:
        raise AssertionError(
            f"{len(bad)} fixture row(s) in the committed forward log: "
            f"{', '.join(f'row {i} {t}' for i, t in bad[:5])}"
            f"{' ...' if len(bad) > 5 else ''}.\n"
            f"  A selftest is writing to the production log. Redirect "
            f"forward_log.LOG for the WHOLE test, not the one block that "
            f"mentions it — anything driving analyze() writes as a side "
            f"effect.")
    import forward_log as _fl
    problems = _fl.verify()
    if problems:
        raise AssertionError(
            f"the forward log chain is broken:\n    " +
            "\n    ".join(problems[:4]))
    n = len(_fl.read_all())
    print(f"  forward log: {n} rows, no fixtures, chain intact")


def check_taxonomy_never_gates() -> None:
    """
    The outcome buckets annotate. They never decide whether a trade is taken.

    Asked for explicitly: "I do not want you to restrict any trade because it
    falls in bucket A vs B". This pins that, because the drift from "show the
    bucket" to "skip the bad bucket" is one line and looks like an improvement
    in the diff.

    Two independent reasons it cannot happen, and both are checked:

      1. No live path imports outcome_taxonomy at all.
      2. classify() needs mfe_r and mae_r, which exist only AFTER a trade is
         over. A setup at signal time has no path, so there is nothing to
         classify even if someone wired it in.

    The second is the real guarantee; the first stops the attempt earlier.
    """
    import ast as _ast
    live = ("scanner.py", "app.py", "exit_monitor.py", "signal_core.py")
    offenders = []
    for f in live:
        path = Path(f)
        if not path.exists():
            continue
        tree = _ast.parse(path.read_text())
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                if any(a.name == "outcome_taxonomy" for a in node.names):
                    offenders.append(f)
            elif isinstance(node, _ast.ImportFrom):
                if node.module == "outcome_taxonomy":
                    offenders.append(f)
    if offenders:
        raise AssertionError(
            f"live path(s) import outcome_taxonomy: {', '.join(sorted(set(offenders)))}.\n"
            f"  The buckets are a readout, never a gate. Annotating an alert "
            f"is fine and needs no import here; skipping a setup because of "
            f"its bucket is the thing that must not happen.")
    import outcome_taxonomy as _ot
    unfinished = {"outcome": "win", "setup": {"rr": 3.0}}
    assert _ot.classify(unfinished) == "OTHER", (
        "a setup with no completed path received a bucket. classify() must "
        "depend on mfe_r/mae_r so it is unusable at signal time by "
        "construction, not merely by convention")
    print(f"  outcome buckets annotate only ({len(live)} live paths clean, "
          f"unfinished trades unclassifiable)")


def check_line_endings_preserved() -> None:
    """
    Files that ship CRLF must keep it.

    app.py and scanner.py are CRLF. A ten-line edit made with
    pathlib.write_text() rewrote both to LF and landed as a 5,906-line diff on
    app.py alone — every line "changed", git blame destroyed, and the actual
    change invisible inside the noise. That is how a real mistake gets merged:
    not hidden deliberately, just buried.

    Pinned rather than normalised, because a repo-wide conversion is its own
    5,906-line diff and would do the same damage once more.
    """
    crlf = {"app.py", "scanner.py"}
    wrong = []
    for f in sorted(crlf):
        path = Path(f)
        if not path.exists():
            continue
        data = path.read_bytes()
        if b"\r\n" not in data:
            wrong.append(f)
    if wrong:
        raise AssertionError(
            f"{', '.join(wrong)} shipped CRLF and now has LF line endings.\n"
            f"  Every line reads as changed, which buries the real edit. Use "
            f"newline='' or write bytes when editing these files.")
    print(f"  {len(crlf)} CRLF files keep their line endings")


def check_direction_gate_is_live_only() -> None:
    """
    Every LIVE path consults risk_params.direction_blocked(); no research path
    does.

    BOTH HALVES MATTER AND FAIL DIFFERENTLY.

    If a live path stops consulting it, the scanner resumes sending short
    alerts that drift_null measured as worse than random entry — all 200 random
    draws beat the real short signal, at -0.131 R per trade. That is money.

    If a RESEARCH path starts consulting it, every historical number in
    results/ silently changes meaning: 2,360 trades become 1,457, the recorded
    mean R stops being the mean R that was recorded, and no new run can be
    compared with an old one. The short side is still measured. It is just not
    traded. That is the whole design, and a gate that leaked into backtest.py
    would look like a tidy-up in the diff.
    """
    # AST, NOT GREP. A text search for "direction_blocked(" matches the call
    # sitting inside a comment, so commenting the gate out — the single most
    # likely way it gets disabled, in a hurry, to chase a short — left the
    # check green. Falsification caught exactly that. A commented call is not
    # in the tree.
    import ast as _ast

    def _calls_gate(path: Path) -> bool:
        tree = _ast.parse(path.read_text())
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Call):
                continue
            fn = node.func
            name = (fn.attr if isinstance(fn, _ast.Attribute)
                    else fn.id if isinstance(fn, _ast.Name) else None)
            if name == "direction_blocked":
                return True
        return False

    live = {"scanner.py", "app.py"}
    research = {"backtest.py", "signal_core.py", "setup_population.py",
                "drift_null.py", "setup_cases.py", "option_backtest.py",
                "universe_backtest.py", "longs_only.py", "inverted_arm.py"}
    missing, leaked = [], []
    for f in sorted(live):
        if not _calls_gate(Path(f)):
            missing.append(f)
    for f in sorted(research):
        path = Path(f)
        if path.exists() and _calls_gate(path):
            leaked.append(f)
    if missing:
        raise AssertionError(
            f"live path(s) no longer consult the direction gate: "
            f"{', '.join(missing)}.\n"
            f"  Short setups measured WORSE THAN RANDOM entry (drift_null: all "
            f"200 random draws beat the real signal, -0.131 R per trade). "
            f"Without this call the scanner sends them again.")
    if leaked:
        raise AssertionError(
            f"research module(s) now apply the LIVE direction gate: "
            f"{', '.join(leaked)}.\n"
            f"  Every recorded result changes meaning — 2,360 trades become "
            f"1,457 and no past number stays comparable. The short side is "
            f"measured, not traded.")
    import risk_params as _rp
    assert _rp.direction_blocked("Bearish"), "the gate must block shorts"
    assert _rp.direction_blocked("Bullish") is None, "longs must pass"
    assert _rp.direction_blocked(None) is None, "an absent trend is not a short"
    print(f"  direction gate: {len(live)} live paths apply it, "
          f"{len(research)} research paths do not")


def check_selftests_run_in_ci() -> None:
    """
    Every module with a --selftest flag must be invoked by tests.yml.

    A selftest that CI never runs is a test suite you only find out about when
    you happen to run it by hand. setup_population.py shipped with twelve
    guards and no CI step: the workflow names each module explicitly, so a new
    one is silently absent rather than picked up. Nothing in this suite tied
    the two lists together, which is why the omission survived a green run of
    everything else.

    app.py is exempt and says so in the workflow — importing it renders the
    Streamlit UI and makes live calls, so CI compiles and lints it instead.
    """
    import glob
    yml = Path(".github/workflows/tests.yml").read_text()
    missing = []
    for f in sorted(glob.glob("*.py")):
        if '"--selftest"' not in Path(f).read_text():
            continue
        if f not in yml:
            missing.append(f)
    if missing:
        raise AssertionError(
            f"{len(missing)} module(s) expose --selftest but tests.yml never "
            f"runs them: {', '.join(missing)}.\n"
            f"  Their guards do not protect anything on CI. Add a step to "
            f"  .github/workflows/tests.yml for each.")
    n = sum(1 for f in glob.glob("*.py") if '"--selftest"' in Path(f).read_text())
    print(f"  all {n} --selftest modules are invoked by tests.yml")


IMPORTABLE_MODULES = [
    "signal_core", "data_source", "rate_limit", "market_context", "gh_sync",
    "journal_store", "bar_cache", "risk_params", "notify",
    "option_chain", "universe", "scanner", "exit_monitor", "backtest",
    "oos_validate", "data_reservation", "excursion_analysis", "churn_tracker",
    "universe_backtest", "option_backtest", "liquidity_check", "vrp_check",
    "spread_backtest", "power_check", "adx_retest", "pead_study",
    "rvol_retest", "record_recheck", "atr_stop_test", "option_decompose",
    "thesis_test", "feature_sweep", "conditional_test", "longs_only",
    "setup_cases", "setup_population", "drift_null", "inverted_arm",
    "outcome_taxonomy", "exit_ab", "forward_log",
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
    beside them after compute(), the execution timeline must be complete, and
    the warm-up must be a fixed head.

    THE BUGS THIS PINS. (1) backtest.run(), option_backtest and
    universe_backtest re-attached Open/Date with `raw.tail(len(df))`, which
    assumed compute() drops rows only from the FRONT; it dropped interior rows
    too, so earlier indicator rows were paired with an Open and Date from 20
    sessions later. (2) Those interior rows were DROPPED: one missing Volume
    deleted twenty sessions from the execution timeline, and a stop touched in
    one of them was never seen (group B, 2026-10-28). (3) The warm-up was
    discarded only when more than WARMUP_BARS + MIN_BARS_AFTER rows survived,
    so 189 raw rows kept 140 and 190 kept 41 (group B).

    compute() now keeps every session from the first complete-indicator row
    plus WARMUP_BARS, carries SIGNAL_OK per row, and reads no signal where it
    is False. The fixture injects an interior NaN deliberately and asserts
    that the discarded dropna filter would have DISAGREED, so the check
    cannot pass on a clean frame.
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
    raw.loc[n // 2, "Volume"] = np.nan      # one bad bar: 20 signal bars blank, 0 sessions lost

    out = bt.compute(raw)
    assert len(out) > 50, f"fixture degenerate: compute() kept only {len(out)} rows"
    assert "SIGNAL_OK" in out.columns, "compute() must carry SIGNAL_OK"

    # Ground truth: the indicators on the raw frame, index kept.
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
    ok = probe[bt.INDICATOR_COLUMNS].notna().all(axis=1)
    first = int(np.flatnonzero(ok.to_numpy())[0])
    probe = probe.iloc[first + bt.WARMUP_BARS:]
    truth = raw.loc[probe.index, ["Date", "Open"]].reset_index(drop=True)

    assert len(truth) == len(out), (
        f"compute() kept {len(out)} rows, the reference filter kept {len(truth)} "
        f"— compute()'s row filter changed; update this check deliberately.")
    assert out["SIGNAL_OK"].tolist() == ok.iloc[first + bt.WARMUP_BARS:].tolist(), \
        "SIGNAL_OK must be exactly 'every indicator present on this row'"
    n_blank = int((~out["SIGNAL_OK"]).sum())
    assert n_blank == 20, (n_blank, "one missing Volume blanks exactly twenty signal bars")

    bad_dates = int((pd.to_datetime(out["Date"]).values != truth["Date"].values).sum())
    assert bad_dates == 0, (
        f"{bad_dates} of {len(out)} rows carry a Date that belongs to a "
        f"different bar. Open/Date must travel THROUGH compute() with their own "
        f"row — never be re-attached positionally afterwards.")
    assert np.allclose(out["Open"].values, truth["Open"].values), (
        "Open is misaligned with its indicator row — simulate_trade() would "
        "fill at a price from the wrong bar.")

    # LIVENESS: the discarded dropna filter must actually differ on this
    # fixture, or the assertions above prove nothing about interior rows.
    dropped = probe.dropna(subset=bt.INDICATOR_COLUMNS)
    assert len(probe) - len(dropped) == 20, (
        "fixture is DEAD: the interior NaN is not blanking rows, so this check "
        "would pass with the old dropna filter restored. Re-derive the fixture.")
    # The gap sessions are ON the timeline, dated and priced.
    gap_dates = pd.to_datetime(out.loc[~out["SIGNAL_OK"], "Date"])
    assert gap_dates.iloc[0] == raw["Date"].iloc[n // 2], "the gap starts at the bad bar itself"
    assert np.isfinite(out.loc[~out["SIGNAL_OK"], ["Open", "High", "Low", "Close"]].to_numpy()).all()

    # The warm-up is a FIXED head: one more raw bar is one more retained row.
    lens = [len(bt.compute(raw.iloc[:k])) for k in (first + bt.WARMUP_BARS + 38,
                                                     first + bt.WARMUP_BARS + 39,
                                                     first + bt.WARMUP_BARS + 40,
                                                     first + bt.WARMUP_BARS + 41)]
    assert lens == [38, 39, 40, 41], (lens, "a conditional warm-up slice is back")

    # And no module may reintroduce tail-alignment.
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
    print(f"  compute() keeps Open/Date on their own row, every session on the timeline "
          f"({len(out)} rows, {n_blank} signal-blank), warm-up a fixed head")
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


def check_forward_log_single_writer() -> None:
    """
    Exactly ONE module appends to the forward log, and the workflow commits it.

    THE CONSTRAINT. forward_log.append() takes `seq = len(rows) + 1` and
    `prev = rows[-1]["hash"]`, so two processes appending from the same starting
    file produce rows with the SAME seq and the SAME prev. Git merges both and
    the result does not verify — permanently, because the log is append-only and
    the hashes are computed over the seq, so it cannot be renumbered to repair.

    scanner.py is the writer (GitHub Actions, commits after each scan). app.py
    runs on Streamlit Cloud, a different machine, so it must never append —
    which is why record_outcome() still has no caller. That is a decision about
    who owns the chain, not a wiring gap; BACKLOG 17 carries it.

    AND THE COMMIT MUST EXIST. The log lives on an ephemeral runner. Without the
    workflow committing it, every row is destroyed with the container — which is
    exactly the state this repo was in from the day the log was added until
    2026-09-16: a tamper-evident record with no data and no anchor.
    """
    writers = []
    for p in sorted(Path(".").glob("*.py")):
        if p.name in ("forward_log.py", "consistency_check.py"):
            continue
        txt = p.read_text()
        # attach_outcomes() is a WRITE too — it is how the scanner appends
        # outcome rows (BACKLOG 17). Leaving it out would let a second module
        # start writing through the one entry point this check did not name.
        # A forward_log.append() call WITH an explicit path writes some other
        # chain in the same format (daytrade_paper.jsonl); without one it
        # writes the forward log, and that is a writer.
        import re as _re
        bare_append = _re.search(r"forward_log\.append\((?=\s*[^)\s])(?![^)]*path=)", txt)
        if bare_append or any(f"forward_log.{fn}" in txt for fn in
                              ("record_signal", "record_outcome", "attach_outcomes",
                               "record_scan", "grade_open_signals", "record_alert")):
            writers.append(p.name)
    if writers != ["scanner.py"]:
        raise AssertionError(
            f"the forward log must have exactly one writer and it must be "
            f"scanner.py; found {writers or 'none'}. Two writers produce rows "
            f"with the same seq and prev, and the merged chain does not verify "
            f"— permanently, because an append-only log cannot be renumbered.")
    print(f"  one writer: {writers[0]}")

    wf = Path(".github/workflows/scanner.yml").read_text()
    if "forward_log.jsonl" not in wf:
        raise AssertionError(
            "scanner.yml does not commit forward_log.jsonl. The runner is "
            "ephemeral, so every signal the log records is destroyed with the "
            "container — and a hash chain cannot detect its own tail being "
            "cut, so git is its only anchor. .gitignore says exactly this.")
    if "git add" not in wf or "forward_log.jsonl" not in wf.split("git add", 1)[1][:120]:
        raise AssertionError(
            "scanner.yml mentions forward_log.jsonl but does not `git add` it "
            "— a file that is never staged is never committed.")
    print("  scanner.yml stages and commits forward_log.jsonl (its only anchor)")


def check_unattended_workflows_pinned_and_alarmed() -> None:
    """
    The two unattended workflows install the versions CI tests, and the
    scanner's silence alarm runs on EVERY run.

    THE TWO BUGS THIS PINS (BACKLOG 22, 27). scanner.yml and exit-monitor.yml
    ran `pip install yfinance pandas ...` UNPINNED, and on 2026-10-02 installed
    yfinance 1.7.0 / pandas 3.0.6 while tests.yml ran against requirements.txt's
    1.6.0 / 3.0.5. The live money path ran versions no selftest had seen.
    `-c requirements.txt` installs only the named packages at the pinned
    versions, so pins have one home.

    And from 09-16 to 10-07 GitHub's scheduler delivered the scanner in
    session on 11 of 48 slots; each late run skipped at the market-hours guard
    and reported success, and nothing could say the scanner had gone quiet.
    `scanner.py --coverage` is the alarm. It is only an alarm if it runs on the
    runs that CANNOT scan, which is why its step must be `if: always()` --
    a conditional step is the silence this exists to end.
    """
    for wf in ("scanner.yml", "exit-monitor.yml"):
        txt = Path(".github/workflows", wf).read_text()
        installs = [l.strip() for l in txt.splitlines()
                    if l.strip().startswith("run: pip install")]
        assert installs, f"{wf}: no pip install line found"
        for line in installs:
            if "-r requirements.txt" in line:
                continue
            assert "-c requirements.txt" in line, (
                f"{wf} installs unpinned: `{line}`. Production ran yfinance "
                f"1.7.0 against a tested 1.6.0 this way. Use "
                f"`pip install -c requirements.txt <pkgs>` so the versions CI "
                f"tests are the versions money runs on.")
        print(f"  {wf:18} installs pinned to requirements.txt")

    txt = Path(".github/workflows/scanner.yml").read_text()
    i = txt.find("name: Check scan coverage")
    assert i > 0, ("scanner.yml has no 'Check scan coverage' step. Without it a "
                   "late run skips, reports success, and nothing says the "
                   "scanner went quiet -- which it did for three trading days.")
    block = txt[i:i + 900]
    assert "if: always()" in block, (
        "the coverage step is conditional. It must be `if: always()` -- it "
        "only earns its place on the runs that cannot scan.")
    assert "scanner.py --coverage" in block, "the coverage step does not run --coverage"
    src = Path("scanner.py").read_text()
    assert '"--coverage"' in src and "def coverage_check" in src, \
        "scanner.py no longer provides --coverage"
    print("  scanner.yml        runs the silence alarm on every run (if: always())")


def check_research_config_divergence_declared() -> None:
    """
    backtest.DEFAULTS (the research config) and signal_core.DEFAULTS (the live
    config) differ, and every difference is written down in ONE place.

    THE FINDING (BACKLOG 23, review H1). The 1,386-trade record and every study
    built on it use `dict(bt.DEFAULTS, ...)`: ADX 25, 1.0 / 3.0 ATR, volume 1.0,
    regime off. The scanner runs signal_core.DEFAULTS: ADX 30 (35 until
    2026-10-28), 1.25 / 4.0, volume 1.2, regime on. For a year the research dict's comments said
    "app.py default" — false, and nothing read them. check_duplicated_constants
    matches `NAME = value` at module level and cannot see inside a dict literal,
    so this is the check that can.

    The rule is not "they must agree" — making them agree would move every
    committed result without a re-run. The rule is that the divergence is
    DECLARED in backtest.RESEARCH_VS_LIVE, completely and exactly: a shared key
    that differs must be listed with both values as they are; a listed key must
    actually differ. Either config can change, but not silently.
    """
    import backtest as bt
    import signal_core as sc

    # The mapping is real: build_signal_params() reads exactly these keys.
    params = bt.build_signal_params(dict(bt.DEFAULTS))
    for key, field in bt.CFG_TO_PARAMS.items():
        assert getattr(params, field) == bt.DEFAULTS[key], (
            f"backtest.CFG_TO_PARAMS maps cfg[{key!r}] -> SignalParams.{field}, "
            f"but build_signal_params(DEFAULTS) gives {field}="
            f"{getattr(params, field)!r} against DEFAULTS[{key!r}]="
            f"{bt.DEFAULTS[key]!r}. The mapping and the function disagree.")

    differing = {key for key, field in bt.CFG_TO_PARAMS.items()
                 if bt.DEFAULTS[key] != getattr(sc.DEFAULTS, field)}
    declared = set(bt.RESEARCH_VS_LIVE)

    undeclared = differing - declared
    assert not undeclared, (
        "research and live configs differ on "
        + ", ".join(f"{k} (research {bt.DEFAULTS[k]!r}, live "
                    f"{getattr(sc.DEFAULTS, bt.CFG_TO_PARAMS[k])!r})"
                    for k in sorted(undeclared))
        + " and backtest.RESEARCH_VS_LIVE does not say so. Declare it with a "
          "reason, or make them agree AND re-run every result built on "
          "bt.DEFAULTS — a silent divergence is how the record came to "
          "describe a configuration the scanner does not run.")
    stale = declared - differing
    assert not stale, (
        f"backtest.RESEARCH_VS_LIVE lists {sorted(stale)} as diverging, but "
        f"the two configs now agree there. A stale declaration is the same "
        f"drift as the stale comments it replaced; remove the entry.")
    for key, (research, live, why) in bt.RESEARCH_VS_LIVE.items():
        field = bt.CFG_TO_PARAMS[key]
        assert research == bt.DEFAULTS[key] and live == getattr(sc.DEFAULTS, field), (
            f"RESEARCH_VS_LIVE[{key!r}] declares research={research!r}, "
            f"live={live!r}, but the configs hold research="
            f"{bt.DEFAULTS[key]!r}, live={getattr(sc.DEFAULTS, field)!r}. "
            f"The declaration must state the values as they ARE.")
        assert isinstance(why, str) and len(why.strip()) >= 20, (
            f"RESEARCH_VS_LIVE[{key!r}] has no reason; a divergence without a "
            f"stated reason is a divergence nobody chose")

    src = Path("backtest.py").read_text()
    assert "app.py default" not in src, (
        "backtest.py still says a research value is an app.py default. It is "
        "not: app.py derives from signal_core.DEFAULTS.")
    print(f"  {len(differing)} of {len(bt.CFG_TO_PARAMS)} shared keys diverge "
          f"(research vs live), all declared in backtest.RESEARCH_VS_LIVE")


def check_post_close_record_wired() -> None:
    """
    The post-close record-only run exists in scanner.py AND scanner.yml.

    WHY IT EXISTS. During the session the scanner evaluates YESTERDAY's bar
    (drop_partial_bar drops today's in-progress bar), so a signal that forms
    at today's close is first recorded by tomorrow's 11:07 scan — and if
    tomorrow's dispatches all miss, by the day after. `--record-only` runs in
    the two hours after the close, when today's bar is settled, and writes the
    signal rows and the `scan` heartbeat for it. It sends no trade alert and
    fetches no chain: the market is shut, so the next session's first scan
    alerts (the forward log dedupes on bar date; alert cooldown does not read
    the log). The log gets the bar on the day it closed.

    Half-wired is the failure mode: a flag with no workflow step is a feature
    that never runs; a workflow step with no flag is a run that scans in the
    alerting mode after hours.
    """
    src = Path("scanner.py").read_text()
    assert '"--record-only"' in src and "def is_post_close_window" in src, \
        "scanner.py no longer provides --record-only / is_post_close_window"
    wf = Path(".github/workflows/scanner.yml").read_text()
    assert "postclose=" in wf, (
        "scanner.yml's market-hours guard does not emit a `postclose` output, "
        "so no step can know it is in the record-only window")
    i = wf.find("name: Record the closed bar")
    assert i > 0, "scanner.yml has no 'Record the closed bar' step"
    block = wf[i:i + 900]
    assert "scanner.py --record-only" in block, \
        "the post-close step does not run `scanner.py --record-only`"
    assert "outputs.postclose == 'true'" in block, \
        "the post-close step is not gated on the guard's postclose output"
    j = wf.find("name: Run scanner")
    assert j > 0 and "outputs.open == 'true'" in wf[j:j + 400], \
        "the alerting scan step must stay gated on the market being OPEN"
    # A manual record_only dispatch runs the post-close pass and ONLY that:
    # the alerting step must exclude it, or an evening dispatch would send
    # trade alerts on a closed market.
    import re as _re
    _if = _re.search(r"name: Record the closed bar\n\s+if: ([^\n]+)", wf)
    assert _if and "inputs.record_only == true" in _if.group(1), \
        "the post-close step's `if:` must honour a manual record_only dispatch"
    assert "inputs.record_only != true" in wf[j:j + 400], \
        "the alerting scan must not run on a record_only dispatch"
    print("  scanner.yml        runs --record-only in the post-close window or on a "
          "record_only dispatch; alerting scan stays session-only")


def check_hq_tier_annotates_record() -> None:
    """
    The high-quality tier TAGS the forward record; it does not GATE it.

    OWNER DECISION 2026-10-08, pre-registered in
    results/forward_record_preregistration.md (BACKLOG 24). Two of the tier's
    legs -- ADX >= 35 and volume >= 1.2x -- were tested in this repo and found
    non-predictive, and for three weeks the record's `taken` set was gated on
    them: 19 base signals, 0 taken. A long base signal is now `taken` with
    `hq` on the row saying what the gate would have selected. Alerts still
    fire on the tier; that is what is SENT, and this check pins that it no
    longer decides what is RECORDED.

    Driven through scanner.analyze() against a temporary log, from outside
    the module, so the scanner's own selftest cannot be edited away with the
    behaviour.
    """
    import tempfile
    import pandas as pd
    import scanner
    import signal_core as sc
    import forward_log

    frame = pd.DataFrame(
        {"Close": [100.0] * (scanner.MIN_BARS_AFTER_WARMUP + 5)},
        index=pd.bdate_range("2026-01-05", periods=scanner.MIN_BARS_AFTER_WARMUP + 5))

    def canned(trend, hq):
        r = {"blocked": False, "ticker": "CK", "trend": trend,
             "strength": "Strong" if hq else "Normal", "price": 100.0,
             "entry": 100.0, "stop": 98.0, "target": 106.0,
             "rr": 3.0 if hq else 0.8, "rsi": 65.0, "adx": 30.0, "atr": 2.0,
             "high_quality": hq, "filters_pass": 4, "filters_total": 4,
             "filters": {"ADX Trend Strength": {"pass": hq}}}
        return r

    real_log, real_eval = forward_log.LOG, sc.evaluate
    real_wk, real_earn = scanner.get_weekly_trend, scanner.check_earnings_blackout
    try:
        forward_log.LOG = Path(tempfile.mkdtemp()) / "ck.jsonl"
        scanner.get_weekly_trend = lambda t: "Bullish"
        scanner.check_earnings_blackout = lambda t, **k: (True, "n/a")
        sc.evaluate = lambda *a, **k: canned("Bullish", False)
        base = scanner.analyze(frame, "CKA")
        sc.evaluate = lambda *a, **k: canned("Bullish", True)
        hq = scanner.analyze(frame, "CKB")
        sc.evaluate = lambda *a, **k: canned("Bearish", True)
        short = scanner.analyze(frame, "CKC")
        rows = {r["ticker"]: r for r in forward_log.read_all() if r.get("kind") == "signal"}
    finally:
        forward_log.LOG, sc.evaluate = real_log, real_eval
        scanner.get_weekly_trend, scanner.check_earnings_blackout = real_wk, real_earn

    a = rows.get("CKA") or {}
    assert a.get("decision") == "taken" and a.get("hq") is False, (
        f"a long base signal below the HQ tier was recorded as "
        f"{a.get('decision')!r} hq={a.get('hq')!r}; it must be taken with "
        f"hq=False. The tier gating the record is what starved it (BACKLOG 24, "
        f"results/forward_record_preregistration.md)")
    assert a.get("setup", {}).get("hq_fail"), \
        "a below-tier row must say WHICH legs failed (setup.hq_fail is empty)"
    assert base is None, \
        "a below-tier signal must not produce an alert payload; alerts stay on the tier"
    b = rows.get("CKB") or {}
    assert b.get("decision") == "taken" and b.get("hq") is True and hq is not None, \
        f"an HQ long must be taken, hq=True, and alerted: {b.get('decision')!r}/{b.get('hq')!r}/{hq!r}"
    c = rows.get("CKC") or {}
    assert c.get("decision") == "skipped" and short is None, \
        "the direction gate (a measured decision) must still skip shorts"
    print("  HQ tier tags the record (taken, hq=False below the tier), gates only the alert")


def check_outcome_bases_never_pooled() -> None:
    """
    The two outcome bases coexist on one signal and are never summed.

    results/forward_grading_preregistration.md. A journal trade yields an R on
    the option PREMIUM; the mechanical grader yields an R on the underlying's
    STOP DISTANCE. Different denominators -- the error risk_params.py already
    paid for once. So: record_outcome() allows one row per (ref_seq, basis)
    and refuses a second on the same basis; summary() reports every count and
    mean under its basis and has no pooled mean; report() prints them apart;
    the grader is wired into scanner.py, the log's one writer; and
    resolve_signal() is pure stdlib so it cannot fetch anything itself.
    """
    import tempfile
    import forward_log as fl
    import inspect

    tmp = Path(tempfile.mkdtemp()) / "bases.jsonl"
    setup = {"price": 100.0, "entry": 100.0, "stop": 98.0, "target": 106.0, "rr": 3.0}
    sig = fl.record_signal("CKX", "Bullish", setup, "taken", "", {"k": 1},
                           bar_date="2026-09-01", path=tmp, bucket="pool", hq=False)
    assert fl.HEADLINE_BUCKET == "pool", (
        "the headline population is the POOL (forward_record_preregistration.md, "
        "amendment 2026-10-28); changing it is a new amendment, dated, before its first row")
    fl.record_outcome(sig["seq"], "loss", -1.3, "stop_distance", exit_price=97.4,
                      detail={"exit_rule": "stop"}, path=tmp)
    try:
        fl.record_outcome(sig["seq"], "win", 2.0, "stop_distance", path=tmp)
    except ValueError as e:
        assert "stop_distance" in str(e), e
    else:
        raise AssertionError(
            "a second stop_distance outcome for one signal was accepted; "
            "a result could be revised after the fact")
    fl.record_outcome(sig["seq"], "win", 0.4, "premium", trade_id="CKT1",
                      path=tmp)
    sm = fl.summary(tmp)
    assert "mean_r" not in sm and "settled" not in sm, (
        f"summary() still carries a pooled key ({[k for k in ('mean_r', 'settled') if k in sm]}); "
        f"premium and stop-distance R must never be summed")
    bb = sm["by_basis"]
    assert bb["premium"]["settled"] == 1 and bb["stop_distance"]["settled"] == 1
    assert abs(bb["premium"]["mean_r"] - 0.4) < 1e-9 and abs(bb["stop_distance"]["mean_r"] + 1.3) < 1e-9, bb

    src = inspect.getsource(fl.resolve_signal)
    for banned in ("yfinance", "requests", "pandas", "urllib", "data_source"):
        assert banned not in src, f"resolve_signal() must be pure: found {banned!r}"
    sc_src = Path("scanner.py").read_text()
    assert "forward_log.grade_open_signals(" in sc_src, \
        "scanner.py does not call the grader; the record has intake and no grading"
    print("  one outcome per (ref_seq, basis); summary/report split by basis; "
          "grader pure and wired into scanner.py")


def check_signal_key_round_trip() -> None:
    """
    A trade logged as a system signal names WHICH signal, and the outcome
    lands on exactly that row (BACKLOG 28, piece 2).

    THE CHAIN, end to end, each link in a different module: the scanner's
    alert carries the signal bar's date; the app's three logging sites hand
    forward_log.signal_key(underlying, direction, bar) to
    journal_store.open_option_position(); close_position() carries it to the
    journal row; attach_outcomes() matches a keyed row exactly and refuses a
    key that names a skipped or missing signal. One link dropped and the
    match falls back to the five-day heuristic, which is refused whenever
    two signals qualify -- the common case now that base signals are
    recorded on consecutive bars.
    """
    import tempfile
    import forward_log as fl
    import journal_store as js

    import re
    app = Path("app.py").read_text()
    # Call sites only: `signal_key=(...` or `signal_key=_name`. A docstring's
    # "signal_key=..." does not count.
    sites = len(re.findall(r"signal_key=(\(|_)", app))
    assert "import forward_log" in app and sites >= 3, (
        f"app.py passes signal_key= at {sites} site(s); the scan-result quick "
        f"log, the manual form and the contract checker must all pass it")
    assert "forward_log.signal_key(" in app, \
        "app.py must build the key with forward_log.signal_key(), not its own format"
    assert "def signal_bar_input" in app and "sig_bar_" in app, \
        "the manual forms have no signal-bar date input"
    sc_src = Path("scanner.py").read_text()
    assert "Signal bar:" in sc_src and '"signal_key":' in sc_src, \
        "the alert does not tell the owner the signal bar's date"
    # The key format is ONE function. journal_store must not re-derive it:
    # no definition of its own, and no f-string that pipes a ticker into a
    # "TICKER|Trend|date" shape.
    js_src = Path("journal_store.py").read_text()
    assert "def signal_key" not in js_src and not re.search(r'f"\{[^}]*\}\|', js_src), \
        "journal_store builds a key itself; the format lives in forward_log only"

    # Behavioural: position -> journal row -> exact attachment.
    mem = {"pos": [], "jrn": []}
    saved = {k: getattr(js, k) for k in ("load_positions", "save_positions",
                                         "load_journal", "save_journal")}
    try:
        js.load_positions = lambda: list(mem["pos"])
        js.save_positions = lambda d: mem.__setitem__("pos", list(d))
        js.load_journal = lambda: list(mem["jrn"])
        js.save_journal = lambda d: mem.__setitem__("jrn", list(d))
        key = fl.signal_key("NVDA", "Bullish", "2026-09-10")
        pos = js.open_option_position("NVDA", "CALL", 250.0, "2026-11-20", 1, 3.0,
                                      {"tp_pct": 200, "sl_pct": 50, "dte_exit": 7,
                                       "max_hold_bars": 20, "invalidate_ema": True},
                                      source="signal", signal_key=key)
        js.close_position(pos["id"], 4.5, "WIN")
    finally:
        for k, v in saved.items():
            setattr(js, k, v)
    row = mem["jrn"][0]
    assert row["signal_key"] == key and row["source"] == "signal", row
    tmp = Path(tempfile.mkdtemp()) / "key.jsonl"
    setup = {"price": 100.0, "entry": 100.0, "stop": 98.0, "target": 106.0, "rr": 3.0}
    fl.record_signal("NVDA", "Bullish", setup, "taken", "", {"k": 1},
                     bar_date="2026-09-09", path=tmp, hq=False)
    fl.record_signal("NVDA", "Bullish", setup, "taken", "", {"k": 1},
                     bar_date="2026-09-10", path=tmp, hq=False)
    row["date"] = "2026-09-11 10:00 ET"           # inside both candidates' windows
    rep = fl.attach_outcomes([row], path=tmp)
    assert rep["attached"] == 1 and rep["ambiguous"] == 0, (
        rep, "two candidates and a key: the key must decide, not ambiguity")
    out = [x for x in fl.read_all(tmp) if x["kind"] == "outcome"][0]
    assert out["ref_seq"] == 2 and out["basis"] == "premium", out
    print("  alert -> app -> position -> journal -> exact attachment: key carried end to end")


def check_option_iv_single_source() -> None:
    """
    The option engine's IV multiplier has one home, every committed option
    result says which value it used, and the recorded sensitivity table is
    the committed sweep, digit for digit (BACKLOG 25).

    option_backtest.py carried `default=1.15` in its argparse for a year with
    the comment "trade above realised vol" and nothing measured it. The number
    now lives in risk_params.OPT_IV_MULT with its caveat; the engine must read
    it from there, not restate it. And a result whose header does not say
    `IV: <x>x realised` is a number with no stated assumption.
    """
    import re
    import risk_params as rp
    src = Path("option_backtest.py").read_text()
    assert "default=risk_params.OPT_IV_MULT" in src, (
        "option_backtest.py's --iv-mult default is not risk_params.OPT_IV_MULT; "
        "a second copy of the constant is how 1.15 went unmeasured for a year")
    assert not re.search(r"default=1\.15\b", src), \
        "option_backtest.py still hard-codes 1.15 somewhere"

    files = sorted(Path("results").glob("option_*"))
    assert files, "no option results on file"
    for f in files:
        txt = f.read_text(encoding="utf-8")
        if f.name.endswith("preregistration.md"):
            continue
        assert re.search(r"IV: [0-9.]+x realised", txt), (
            f"{f.name} carries no 'IV: <x>x realised' line; an option result "
            f"must state the assumption it was priced under")

    # The sensitivity table in risk_params is the committed sweep: the files
    # OPT_SWEEP_SOURCE names (run 2, 2026-10-28, the no-look-ahead code).
    for mult, wr in rp.OPT_WIN_RATE_BY_IV_MULT.items():
        f = Path(rp.OPT_SWEEP_SOURCE.format(mult=mult))
        assert f.exists(), f"risk_params lists {mult} but {f.name} is not on file"
        txt = f.read_text(encoding="utf-8")
        assert "look-ahead" not in txt[:600] and "SUPERSEDED" not in txt[:600], (
            f"{f.name} carries a caveat (look-ahead or SUPERSEDED); the table must be "
            f"read from a re-run on the fixed code, never from a caveated file")
        m = re.search(r"^\+100% / -50%\s+\d+\s+([0-9.]+)%", txt, re.M)
        assert m, f"{f.name}: no +100%/-50% row"
        assert abs(float(m.group(1)) - wr) < 1e-9, (
            f"OPT_WIN_RATE_BY_IV_MULT[{mult}] = {wr} but {f.name} measured "
            f"{m.group(1)}% -- the table must be the file")
        hdr = re.search(r"IV: ([0-9.]+)x realised", txt)
        assert hdr and abs(float(hdr.group(1)) - mult) < 1e-9, \
            f"{f.name} was not run at {mult}x"
    # And the per-TP table is the 1.15x file's rows, digit for digit: win
    # rate, average win, average loss. OPT_WIN_RATE is that file's +100 row.
    f115 = Path(rp.OPT_SWEEP_SOURCE.format(mult=rp.OPT_IV_MULT))
    txt = f115.read_text(encoding="utf-8")
    for tp, (wr, aw, al) in rp.OPT_SWEEP_BY_TP.items():
        m = re.search(rf"^\+{tp:g}% / -50%\s+\d+\s+([0-9.]+)%\s+\+([0-9.]+)%\s+-([0-9.]+)%", txt, re.M)
        assert m, f"{f115.name}: no +{tp:g}%/-50% row"
        got = tuple(float(x) for x in m.groups())
        assert got == (wr, aw, al), (
            f"OPT_SWEEP_BY_TP[{tp}] = {(wr, aw, al)} but {f115.name} measured {got}; "
            f"the table must be the file")
    assert abs(rp.OPT_SWEEP_BY_TP[rp.OPT_WIN_RATE_TP_PCT][0] / 100 - rp.OPT_WIN_RATE) < 1e-9, (
        f"OPT_WIN_RATE {rp.OPT_WIN_RATE} is not the sweep's +{rp.OPT_WIN_RATE_TP_PCT:g} row")
    print(f"  OPT_IV_MULT single-sourced; {len(files)} option result(s) state their "
          f"IV; sensitivity table matches the committed sweep")


def check_iv_snapshot_single_writer_and_committed() -> None:
    """
    The IV snapshot file has one writer, is pure to read, is committed by
    the workflow, and is taken only in the post-close pass (BACKLOG 25 B).

    Same shape as the forward log's rules, for the same reasons: two writers
    on an append-only file collide; a row on an ephemeral runner that is not
    committed is destroyed; and a snapshot taken mid-session would pair a
    live chain with an unsettled spot, which is not the measurement the
    pre-registration fixed.
    """
    writers = []
    for q in sorted(Path(".").glob("*.py")):
        if q.name in ("iv_snapshot.py", "consistency_check.py"):
            continue
        txt = q.read_text()
        if any(f"iv_snapshot.{fn}(" in txt for fn in ("record", "grade", "_append")):
            writers.append(q.name)
    assert writers == ["scanner.py"], (
        f"iv_snapshots.jsonl must have exactly one writer, scanner.py; found "
        f"{writers or 'none'}")
    src = Path("iv_snapshot.py").read_text()
    for banned in ("import pandas", "import numpy", "import yfinance", "import requests"):
        assert banned not in src, f"iv_snapshot.py must stay stdlib: found {banned!r}"
    wf = Path(".github/workflows/scanner.yml").read_text()
    i = wf.find("git add")
    assert i > 0 and "iv_snapshots.jsonl" in wf[i:i + 160], \
        "scanner.yml does not stage iv_snapshots.jsonl; every snapshot would die with the runner"
    assert Path("iv_snapshots.jsonl").exists(), \
        "iv_snapshots.jsonl must exist in the repo (even empty) or `git add` fails on the runner"
    sc_src = Path("scanner.py").read_text()
    j = sc_src.find("iv_snapshot.record(")
    k = sc_src.rfind("if record_only:", 0, j)
    assert j > 0 and k > 0 and j - k < 2500, \
        "the snapshot must be taken inside the record-only (post-close) branch of run()"
    assert "python iv_snapshot.py --selftest" in Path(".github/workflows/tests.yml").read_text(), \
        "iv_snapshot's selftest is not in CI"
    print("  one writer (scanner.py, post-close only), stdlib reader, committed by scanner.yml")


def check_basket_log_single_writer_and_committed() -> None:
    """
    The RS basket record (BACKLOG 34 B, results/rs_basket_preregistration.md)
    has one writer, is stdlib to read, is committed by the workflow, is taken
    in the post-close pass only, and its selftest runs in CI. Same shape as
    the IV snapshot's rules, for the same reasons.
    """
    writers = []
    for q in sorted(Path(".").glob("*.py")):
        if q.name in ("basket_log.py", "consistency_check.py"):
            continue
        txt = q.read_text()
        if any(f"basket_log.{fn}(" in txt for fn in ("record", "grade", "append")):
            writers.append(q.name)
    assert writers == ["scanner.py"], (
        f"basket_log.jsonl must have exactly one writer, scanner.py; found {writers or 'none'}")
    src = Path("basket_log.py").read_text()
    for banned in ("import pandas", "import numpy", "import yfinance", "import requests"):
        assert banned not in src, f"basket_log.py must stay stdlib: found {banned!r}"
    wf = Path(".github/workflows/scanner.yml").read_text()
    i = wf.find("git add")
    assert i > 0 and "basket_log.jsonl" in wf[i:i + 200], \
        "scanner.yml does not stage basket_log.jsonl; every row would die with the runner"
    assert Path("basket_log.jsonl").exists(), \
        "basket_log.jsonl must exist in the repo (even empty) or `git add` fails on the runner"
    sc_src = Path("scanner.py").read_text()
    j = sc_src.find("basket_record_pass(_closes_for")
    k = sc_src.rfind("if record_only:", 0, j)
    assert j > 0 and k > 0 and j - k < 4000, \
        "the basket row must be taken inside the record-only (post-close) branch of run()"
    assert "basket_log.grade(_closes_for" in sc_src, "run() does not grade the baskets"
    assert "python basket_log.py --selftest" in Path(".github/workflows/tests.yml").read_text(), \
        "basket_log's selftest is not in CI"
    # And no fixture ever reaches the committed file (the forward log's rule).
    import json as _json
    fixtures = {"ZZ", "AAA", "BBB", "CCC", "XXX", "TEST", "BT"}
    for i, line in enumerate(Path("basket_log.jsonl").read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        names = set(_json.loads(line).get("names") or [])
        assert not (names & fixtures), f"basket_log.jsonl row {i} carries fixture names {names & fixtures}"
    print("  one writer (scanner.py, post-close only), stdlib reader, committed by scanner.yml, no fixtures")


def check_control_arm_wired() -> None:
    """
    The random-entry control arm (amendment 2026-10-28 A): scanner.analyze()
    and the pullback recorder each write one control beside a TAKEN row,
    from the frame in hand; the grader offers controls to the resolver; a
    control is never taken, never an episode, never a journal match.
    """
    import forward_log as fl
    sc_src = Path("scanner.py").read_text(encoding="utf-8", errors="replace")
    import re as _re
    calls = _re.findall(r"forward_log\.record_control\(_p?row, control_bars\(", sc_src)
    assert len(calls) == 2, (
        f"scanner.py must record a control beside taken rows in analyze() AND the pullback "
        f"path, from control_bars(); found {len(calls)} such call(s)")
    assert "control_bars(" in sc_src, "the control draws from the frame already fetched (control_bars)"
    assert not fl.counts_as_taken({"kind": "control", "decision": "taken"}), "a control is never taken"
    src = Path("forward_log.py").read_text()
    assert 's.get("kind") == "control"' in src[src.find("def open_signals"):src.find("def grade_open_signals")], \
        "open_signals() must offer control rows to the resolver"
    assert fl.CONTROL_LOOKBACK == 20, "the lookback is declared as 20 settled sessions"
    print("  one control per taken row in both recorders; graded with its signal; never taken")


def check_gates_compare_unrounded() -> None:
    """
    Amendment 2026-10-28 E: the ADX and R:R gates compare UNROUNDED values in
    the one evaluate() every path shares; the rounded fields are display. A
    comparison on a rounded value let 34.96 pass 35 and 0.996 pass 1.0
    (external review 2026-10-20). The scan config names the precision so the
    fingerprint moved once; the app's own ADX gate already compared raw.
    """
    import signal_core as sc
    import scanner
    src = Path("signal_core.py").read_text()
    body = src[src.find("def evaluate("):src.find("def _norm_trend(") if src.find("def _norm_trend(") > src.find("def evaluate(") else len(src)]
    assert "adx_ok = gate_adx(adx_raw, params.adx_min)" in body, "the ADX filter must read the raw value through gate_adx()"
    assert "if not gate_rr(rr_raw, params.min_rr):" in body, "the min R:R gate must read rr_raw through gate_rr()"
    assert "gate_rr(rr_raw, params.hq_min_rr)" in body, "the HQ R:R leg must read rr_raw through gate_rr()"
    assert "adx_val >= params.adx_min" not in body and "rr < params.min_rr" not in body \
        and "rr >= params.hq_min_rr" not in body, "a gate still compares a rounded value"
    assert sc.gate_adx(34.96, 35) is False and sc.gate_rr(0.996, 1.0) is False
    assert scanner._scan_cfg().get("gate_precision") == "unrounded", \
        "the scan config must name the gate precision (it is in the fingerprint)"
    sc_src = Path("scanner.py").read_text(encoding="utf-8", errors="replace")
    assert 'sc.gate_rr(r.get("rr_raw"' in sc_src, "the scanner's hq_fail leg must read rr_raw"
    app = Path("app.py").read_text(encoding="utf-8", errors="replace")
    assert "return adx_val >= ADX_MIN, round(adx_val, 1)" in app, \
        "app.py's own ADX gate must compare the raw value and round only for display"
    print("  ADX and R:R gates compare raw values in evaluate(), the scanner's hq_fail and the app")


def check_contract_selection_shared() -> None:
    """
    Group C (external review 2026-10-20): the scanner and the app name the
    same contract on the same chain. option_chain.get_option_data() is the
    one selector; scanner.suggest_option() fetches, then calls it, and keeps
    no ranking of its own.
    """
    import pandas as pd
    import scanner
    import option_chain
    src = Path("scanner.py").read_text(encoding="utf-8", errors="replace")
    body = src[src.find("def suggest_option("):src.find("def fetch_chain_data(")]
    assert "option_chain.get_option_data(" in body, "scanner.suggest_option() must select through option_chain.get_option_data()"
    assert 'sort_values("score"' not in body and '"score"]' not in body, \
        "scanner.suggest_option() still ranks contracts with a score of its own"
    rows = [(97, 2.00, 2.08, 300, 900), (100, 1.50, 1.56, 500, 2000), (103, 1.00, 1.04, 900, 4000)]
    cd = option_chain._chain(rows)
    for strength in ("Strong", "Normal"):
        mine = scanner.suggest_option("CKC", 100.0, "Bullish", 2.0, strength=strength, chain_data=cd)
        app = option_chain.get_option_data("CKC", 100.0, "Bullish", strength, scanner.OPT_MIN_DTE,
                                           scanner.PARAMS.atr_tgt_mult, 1e9, lambda: True, atr=2.0,
                                           chain_data=cd)
        assert mine and app.get("strike") == mine["strike"], (strength, mine, app)
    print("  scanner.suggest_option() selects through option_chain.get_option_data(); same strike on one chain")


def check_app_paths_group_c() -> None:
    """
    Group C, the rest: the batch fetch retries only the names the batch
    left out and surfaces the unresolved ones; the backtest's regime series
    shares market_context's minimum history; the app renders the regime's
    four states through market_context.regime_status().
    """
    import pandas as pd
    import numpy as np
    import backtest as bt
    import market_context as mc
    app = Path("app.py").read_text(encoding="utf-8", errors="replace")
    i = app.find("def batch_get_data(")
    body = app[i:app.find("\ndef ", i + 1)]
    assert "missing = [t for t in tickers if t not in result]" in body and "for t in missing:" in body, \
        "batch_get_data() must retry exactly the names the batch left out"
    assert "BATCH_GAPS[:] = [t for t in tickers if t not in result]" in body, "unresolved names must be reported"
    assert '(t, "no data after batch retry") for t in BATCH_GAPS' in app, \
        "the scan must list the unresolved names beside its results"
    assert "market_context.regime_status(spy_regime, bool(SPY_REGIME))" in app, \
        "the app's regime banner must render market_context.regime_status()"
    for st in ("off", "unavailable", "stale", "valid"):
        assert f'"state": "{st}"' in Path("market_context.py").read_text(), f"regime_status lacks the {st} state"
    # Shared readiness: on a 100-bar history both say "no view"; on a 260-bar
    # rising history both say Bull on the last bar.
    short = pd.DataFrame({"Close": np.linspace(300, 400, 100)})
    assert bt.build_regime_series(short).iloc[-1] == "Neutral" and mc.spy_regime_from_bars(short)["regime"] == "Unknown", \
        "on a 100-bar history the record has a verdict the live paths do not"
    long_ = pd.DataFrame({"Close": np.linspace(300, 400, 260), "High": np.linspace(301, 401, 260),
                          "Low": np.linspace(299, 399, 260)})
    assert bt.build_regime_series(long_).iloc[-1] == "Bull" == mc.spy_regime_from_bars(long_)["regime"]
    assert (bt.build_regime_series(long_) == "Neutral").sum() == mc.REGIME_MIN_BARS - 1, \
        "the record's no-view prefix must be exactly the live minimum"
    print("  batch retries only missing names and reports gaps; regime minimum shared; four regime states rendered")


def check_research_clocks_group_b() -> None:
    """
    Group B (external review 2026-10-20, built 2026-10-28): the research
    engines keep one clock each and the consumers read the same one.

    - option_backtest: dte is calendar days, priced over CALENDAR_DAYS, the
      entry session's close is marked, and a trade whose expiry lies past
      the sample's end is OPEN. option_decompose reprices on the SAME clock
      (dte_left/365), so its split reconciles to the engine's P&L; it and
      thesis_test censor OPEN rows instead of scoring them.
    - spread_backtest: a cycle is priced for the calendar days it lives and
      settles on the first session on or after entry + dte days.
    - longs_only: an exit on day D never funds an entry on day D; the span
      annualised over is the observed one.
    - pullback_signal: the raw history passes signal_core.validate_bars.
    - backtest.compute: pinned by check_compute_preserves_alignment.
    """
    import numpy as np
    import pandas as pd
    import option_backtest as ob
    import option_decompose as od
    import spread_backtest as sb
    import longs_only as lo
    import signal_core as sc

    rng = np.random.default_rng(11)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.015, 90)))
    frame = pd.DataFrame({"Date": pd.bdate_range("2025-03-03", periods=90).astype(str),
                          "Open": closes, "High": closes * 1.01, "Low": closes * 0.99, "Close": closes})
    cfg = {"iv_mult": 1.15, "dte": 30, "spread_pct": 8.0, "sl": 10**9, "tp": 10**9,
           "dte_exit": 0, "use_thesis": False}
    rec = ob.simulate_option_trade(frame, 30, "Bullish", cfg)
    assert rec["reason"] == "EXPIRY" and 20 <= rec["held"] <= 23, (rec["reason"], rec["held"],
        "a 30-DTE option must expire ~21 sessions after entry, not 30")
    exp = pd.Timestamp(frame["Date"].iloc[31]) + pd.Timedelta(days=30)
    assert pd.Timestamp(frame["Date"].iloc[31 + rec["held"]]) >= exp > pd.Timestamp(frame["Date"].iloc[30 + rec["held"]])
    timed = ob.simulate_option_trade(frame, 30, "Bullish", dict(cfg, dte_exit=7))
    assert timed["reason"] == "TIME" and timed["dte_left"] <= 7 < timed["dte_left"] + 4
    d = od.decompose(timed, cfg["spread_pct"])
    assert d is not None and abs(d["recon_pct"] - timed["pnl_pct"]) < 1e-6, (
        d and d["recon_pct"], timed["pnl_pct"], "option_decompose reprices on a different clock from the engine")
    left = ob.simulate_option_trade(frame, 80, "Bullish", cfg)
    assert left["reason"] == ob.OPEN and od.decompose(left, cfg["spread_pct"]) is None
    assert ob.summarise([rec, left]) ["n"] == 1 and ob.summarise([rec, left])["open"] == 1
    tt = Path("thesis_test.py").read_text()
    assert 'if t.get("reason") == ob.OPEN:' in tt, "thesis_test must censor OPEN trades before pairing"

    idx = pd.bdate_range("2020-01-02", periods=200)
    flat = pd.DataFrame({"Date": idx, "Close": np.full(len(idx), 400.0)})
    vols = pd.Series(np.full(len(idx), 20.0), index=idx)
    r = sb.run("SPY", structure=sb.PUT_SPREAD, width=10, otm_pct=5.0, dte=30, skew=0.0, years=1,
               cost_frac=0.0, account=5000.0, vol_series=vols, bars=flat)
    assert r is not None and all(30 <= c["days"] <= 32 for c in r["rows"]), [c["days"] for c in r["rows"]]
    for c in r["rows"]:
        want = sb.build(sb.PUT_SPREAD, 400.0, 0.20, c["days"] / 365.0, 10, 5.0, 0.0)["credit"]
        assert abs(c["credit"] - want) < 1e-12, "a spread cycle must be priced for the days it lives"

    def mk(date, r_, hold_to):
        return {"trend": "Bullish", "r": r_, "entry": 100.0, "stop": 98.0,
                "entry_date": date, "exit_date": hold_to, "ticker": "A"}
    s3 = lo.simulate([mk("2020-01-01", 1.0, "2020-01-03"), mk("2020-01-03", 1.0, "2020-01-04")],
                     account=5000.0, risk_pct=1.0, constrained=False)
    assert abs(s3["equity"] - 5100.0) < 1e-6, (s3["equity"], "same-day proceeds funded an entry")
    assert abs(s3["years"] - lo.observed_years("2020-01-01", "2020-01-04")) < 1e-12

    assert sc.validate_bars(pd.concat([frame, frame.iloc[[-1]]]), "Date") == "duplicate bar dates"
    src = Path("pullback_signal.py").read_text()
    assert src.count("sc.validate_bars(raw_df)") == 2, "pullback_signal must validate the raw history (both entry points)"
    assert "math.isfinite(atr) and atr > 0" in src, "pullback ATR must be finite and positive"
    print("  option clock calendar (engine, decompose, thesis); spread cycles priced for their span; "
          "no same-day funding; pullback history validated")


def check_spread_arm_preregistered() -> None:
    """
    The spread arms' forward record (BACKLOG 34 E,
    results/spread_preregistration.md): one writer (scanner.py, post-close,
    after the day's settlements), stdlib to read, committed by the workflow,
    its selftest in CI, no fixture in the committed file; settle(),
    CROSS_FRAC, the arms and the vol indices live in spread_log and the
    backtest imports them; and every constant the pre-registration states
    is the code's.
    """
    import spread_log as sl
    import spread_backtest as sb
    import risk_params as rp
    writers = []
    for q in sorted(Path(".").glob("*.py")):
        if q.name in ("spread_log.py", "consistency_check.py"):
            continue
        txt = q.read_text(encoding="utf-8", errors="replace")
        if any(f"spread_log.{fn}(" in txt for fn in ("record", "grade", "append")):
            writers.append(q.name)
    assert writers == ["scanner.py"], (
        f"spread_log.jsonl must have exactly one writer, scanner.py; found {writers or 'none'}")
    src = Path("spread_log.py").read_text()
    for banned in ("import pandas", "import numpy", "import yfinance", "import requests"):
        assert banned not in src, f"spread_log.py must stay stdlib: found {banned!r}"
    wf = Path(".github/workflows/scanner.yml").read_text()
    i = wf.find("git add")
    assert i > 0 and "spread_log.jsonl" in wf[i:i + 240], \
        "scanner.yml does not stage spread_log.jsonl; every row would die with the runner"
    assert Path("spread_log.jsonl").exists(), "spread_log.jsonl must exist in the repo (even empty)"
    assert "python spread_log.py --selftest" in Path(".github/workflows/tests.yml").read_text(), \
        "spread_log's selftest is not in CI"
    sc_src = Path("scanner.py").read_text(encoding="utf-8", errors="replace")
    j = sc_src.find("spread_record_pass(\n                _closes_for")
    k = sc_src.rfind("if record_only:", 0, j)
    g = sc_src.find("spread_log.grade(_closes_for)")
    assert j > 0 and k > 0 and j - k < 6000, \
        "the spread cycle must be opened inside the record-only (post-close) branch of run()"
    assert 0 < g < j, "run() must settle the expired cycles BEFORE opening the next (the backtest's re-entry rule)"
    assert "fetch_atm_chain(tk, min_dte=md)" in sc_src, "the chain is the first listed expiry at least DTE_DAYS out"
    # single sources
    assert sb.settle is sl.settle and sb.CROSS_FRAC is sl.CROSS_FRAC and sb.VOL_INDEX is sl.VOL_INDEX, \
        "spread_backtest must import settle, CROSS_FRAC and VOL_INDEX from spread_log"
    assert [sl.arm_key(a) for a in sl.ARMS] == [f"{tk} {st} w{w:.0f} otm5" for tk in ("SPY", "QQQ")
                                               for st in ("put_spread", "iron_condor") for w in (10.0, 5.0)]
    assert sb.PUT_SPREAD == sl.PUT_SPREAD and sb.IRON_CONDOR == sl.IRON_CONDOR
    # the pre-registration's constants are the code's
    pre = Path("results/spread_preregistration.md").read_text(encoding="utf-8")
    for text, ok in (("`REALISTIC_SKEW` = 20", sb.REALISTIC_SKEW == 20.0),
                     ("`MAX_DRAWDOWN_PCT` = 25 %", sb.MAX_DRAWDOWN_PCT == 25.0),
                     ("`risk_params.MAX_POSITION_PCT` = 25 %", rp.MAX_POSITION_PCT == 25.0),
                     ("`--cost-frac` 0.5", sl.COST_FRAC == 0.5),
                     ("`MAX_OPTION_SPREAD_PCT` (8 %)", rp.MAX_OPTION_SPREAD_PCT == 8.0),
                     ("`CROSS_FRAC` 0.5", sl.CROSS_FRAC == 0.5),
                     ("`DEFAULT_ACCOUNT_SIZE` = $5,000", sl.ACCOUNT == 5000.0),
                     ("`DTE_DAYS` = 30", sl.DTE_DAYS == 30),
                     ("`NO_BARS_DAYS` = 45", sl.NO_BARS_DAYS == 45),
                     ("`SPLIT_TOL` = 2 %", sl.SPLIT_TOL == 0.02),
                     ("`RESULT_CYCLES` = 12", sl.RESULT_CYCLES == 12),
                     ("First reading at 6 graded cycles", sl.FIRST_READ_CYCLES == 6)):
        assert text in pre, f"the pre-registration no longer states {text!r}"
        assert ok, f"the code disagrees with the pre-registration on {text!r}"
    import re as _re
    m = _re.search(r"default=([0-9.]+),\s*\n\s*help=\"fraction of MAX_OPTION_SPREAD_PCT", Path("spread_backtest.py").read_text())
    assert m and float(m.group(1)) == sl.COST_FRAC, "the backtest's --cost-frac default must be the forward arm's COST_FRAC"
    # no fixture ever reaches the committed file
    import json as _json
    for n, line in enumerate(Path("spread_log.jsonl").read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            row = _json.loads(line)
            assert row.get("ticker") in (None, "SPY", "QQQ"), f"spread_log.jsonl row {n} carries {row.get('ticker')}"
    print("  one writer (scanner.py, post-close, after settlement), stdlib, committed, in CI; settle/arms single-sourced; "
          "pre-registration constants == code")


def check_hq_tier_legs_declared() -> None:
    """
    The HQ tier's legs are the declared ones (amendments 2026-10-28 F and G):
    ADX >= DEFAULTS.adx_min unrounded, every filter, R:R >= hq_min_rr, and
    NOT the "Strong" tag. The flag that could restore the tag is False in
    DEFAULTS, evaluate() and the scanner's leg list both read it, the scan
    config names it so the fingerprint moved, and the pre-registration and
    KNOWN_CONFIGS carry that fingerprint.
    """
    import signal_core as sc
    import forward_log as fl
    assert sc.DEFAULTS.hq_requires_strong is False, "the HQ tier requires the Strong tag again; amendment G says it does not"
    assert sc.DEFAULTS.adx_min == 30.0, "the HQ tier's ADX leg is 30 (amendment F)"
    src = Path("signal_core.py").read_text()
    body = src[src.find("def evaluate("):src.find("\ndef ", src.find("def evaluate(") + 1)]
    assert 'and (strength == "Strong" or not params.hq_requires_strong))' in body, \
        "evaluate()'s high_quality must read hq_requires_strong"
    sc_src = Path("scanner.py").read_text(encoding="utf-8", errors="replace")
    assert 'if PARAMS.hq_requires_strong and r.get("strength") != "Strong":' in sc_src, \
        "the scanner's hq_fail leg list must read hq_requires_strong"
    assert '"hq_requires_strong": PARAMS.hq_requires_strong' in sc_src, "the scan config must name the flag"
    pre = Path("results/forward_record_preregistration.md").read_text(encoding="utf-8")
    assert "Amendment 2026-10-28 (G)" in pre and "from `81fd95d84cfc` to **`4ddd12a3095f`**" in pre, \
        "amendment G must declare the move of the fingerprint, old hash to new"
    assert "4ddd12a3095f" in fl.KNOWN_CONFIGS and "81fd95d84cfc" in fl.KNOWN_CONFIGS
    # Behaviour, not text: a Normal-strength signal with every other leg
    # passing is HQ under DEFAULTS and is not under the pre-G rule.
    import pandas as pd
    import numpy as np
    from dataclasses import replace
    n = 60
    idx = pd.bdate_range("2026-01-02", periods=n)
    close = np.linspace(90.0, 100.0, n)
    frame = pd.DataFrame({"Open": close, "Close": close, "High": close + 1.0, "Low": close - 1.0,
                          "Volume": [1_000_000] * n, "EMA20": close * 0.98, "EMA50": close * 0.96,
                          "RSI": 65.0, "MACD": 1.0, "Signal": 0.5, "ATR": 2.0, "ADX": 40.0,
                          "VOL_AVG20": 1_000_000}, index=idx)
    r = sc.evaluate(frame, "CHK", spy_regime={"regime": "Bullish"}, weekly_trend="Bullish")
    assert not r["blocked"] and r["strength"] == "Normal" and r["high_quality"] is True, (r["strength"], r["high_quality"])
    r2 = sc.evaluate(frame, "CHK", replace(sc.DEFAULTS, hq_requires_strong=True),
                     spy_regime={"regime": "Bullish"}, weekly_trend="Bullish")
    assert r2["high_quality"] is False
    # the rank tier ignores the retired leg
    assert fl.rank_score({"ticker": "Q", "setup": {"hq_fail": ["not Strong"], "adx": 1.0, "rr": 1.0}}) == \
        fl.rank_score({"ticker": "Q", "setup": {"hq_fail": [], "adx": 1.0, "rr": 1.0}})
    print("  HQ tier = ADX >= 30, every filter, R:R >= 1; 'Strong' is a recorded tag, not a leg; fingerprint declared")


def check_research_config_choice_declared() -> None:
    """
    BACKLOG 23, decided 2026-10-28: every NEW research run starts from the
    live config, built by ONE function (backtest.live_cfg) from
    signal_core.DEFAULTS through CFG_TO_PARAMS; the record's studies keep
    bt.DEFAULTS and the two differ on exactly the declared keys; the CLI
    offers `--config live`; run() prints which config it is running; the
    other builder of a live cfg (adx_retest) delegates; the convention is
    written down.
    """
    import backtest as bt
    import signal_core as sc
    assert bt.build_signal_params(bt.live_cfg()) == sc.DEFAULTS, "backtest.live_cfg() is not the scanner's config"
    assert {k for k in bt.CFG_TO_PARAMS if bt.live_cfg()[k] != bt.DEFAULTS[k]} == set(bt.RESEARCH_VS_LIVE)
    assert bt.describe_config(bt.live_cfg()) == "live" and bt.describe_config(dict(bt.DEFAULTS)) == "research"
    src = Path("backtest.py").read_text()
    assert '"--config"' in src and 'choices=("research", "live")' in src, "backtest.py must offer --config research|live"
    body = src[src.find("def run(cfg: dict"):src.find("\ndef ", src.find("def run(cfg: dict") + 1)]
    assert 'print(f"Config       : {describe_config(cfg)}"' in body, \
        "run() must print the config line naming describe_config(cfg)"
    ar = Path("adx_retest.py").read_text()
    assert "return bt.live_cfg(tickers, years, adx_min=adx_min)" in ar, "adx_retest.live_cfg must delegate to backtest.live_cfg"
    for q in sorted(Path(".").glob("*.py")):
        if q.name in ("backtest.py", "consistency_check.py"):
            continue
        txt = q.read_text(encoding="utf-8", errors="replace")
        assert "for key, field in bt.CFG_TO_PARAMS.items():" not in txt, \
            f"{q.name} rebuilds the live cfg by hand; use backtest.live_cfg()"
    bl = Path("BACKLOG.md").read_text(encoding="utf-8")
    assert "every NEW research run starts from `backtest.live_cfg()`" in bl, \
        "the working conventions must state the research-config rule (BACKLOG 23)"
    print("  live_cfg() == scanner's params; record keeps bt.DEFAULTS; --config live; run() names it; one builder")


def check_journal_hygiene() -> None:
    """
    BACKLOG 32, decided 2026-10-28. (1) A SIGNAL option entry is logged only
    in the measured structure (21-45 DTE at the trade date, +200 / -50, out
    at 7), from one dict in risk_params that the scanner's chain window and
    the app's defaults also read. (3) No discretionary option entry is
    logged as live money. (4) No fixture row sits in the committed journal
    or positions. The app catches every refusal at every call site.
    """
    import json as _json
    import risk_params as rp
    import scanner
    import journal_store as js
    m = rp.MEASURED_OPTION_STRUCTURE
    assert (scanner.OPT_MIN_DTE, scanner.OPT_MAX_DTE) == (m["min_dte"], m["max_dte"]), \
        "the scanner's chain window must be the measured structure's"
    app = Path("app.py").read_text(encoding="utf-8", errors="replace")
    for needle in ('value=int(risk_params.MEASURED_OPTION_STRUCTURE["min_dte"])',
                   'value=int(risk_params.MEASURED_OPTION_STRUCTURE["max_dte"])',
                   'value=int(risk_params.MEASURED_OPTION_STRUCTURE["tp_pct"])',
                   'value=int(risk_params.MEASURED_OPTION_STRUCTURE["sl_pct"])',
                   'value=int(risk_params.MEASURED_OPTION_STRUCTURE["dte_exit"])'):
        assert needle in app, f"app.py default no longer reads the structure: {needle}"
    # THE THESIS EXIT IS PART OF THE STRUCTURE (review 2026-10-29 late, H1,
    # owner's option (a)): the measurement ran with it on (option_backtest's
    # command line defaults use_thesis on; default run 3 exited 65 trades
    # on it), so the structure names it, the journal refuses a signal trade
    # without it, both app checkboxes default to it, and the alert's rules
    # line reads the structure instead of saying "thesis OFF". The HOLD
    # rule is recorded off on a signal trade (H2): it cannot bind.
    assert m["thesis"] is True and "thesis exit on" in rp.STRUCTURE_REASON
    assert "thesis exit off" in (rp.option_structure_violation("2026-11-20", "2026-10-28",
                                                               {"tp_pct": 200, "sl_pct": 50, "dte_exit": 7}) or "")
    assert rp.option_structure_violation("2026-11-20", "2026-10-28",
                                         {"tp_pct": 200, "sl_pct": 50, "dte_exit": 7, "invalidate_ema": True}) is None
    assert app.count('value=bool(risk_params.MEASURED_OPTION_STRUCTURE["thesis"])') == 2, \
        "both thesis checkboxes must default to the structure's thesis field"
    _sc = Path("scanner.py").read_text(encoding="utf-8", errors="replace")
    assert "thesis OFF\"]" not in _sc and "_m = risk_params.MEASURED_OPTION_STRUCTURE" in _sc and \
        "thesis {'ON (EMA20 break)' if _m.get('thesis') else 'OFF'}" in _sc, \
        "the alert's rules line must read the structure"
    assert "use_thesis=not a.no_thesis" in Path("option_backtest.py").read_text(encoding="utf-8"), \
        "the option engine's command line runs the thesis exit on by default (the measurement did)"
    import inspect as _ci
    assert 'rules = {**(rules or {}), "max_hold_bars": 0}' in _ci.getsource(js.open_option_position), \
        "a signal trade records the HOLD rule off (it cannot bind inside the structure)"
    # The window has a CEILING everywhere a chain is read (review 2026-10-28
    # F1): the app passes its max to the selector, and the selector, the
    # scanner's chain fetch and the ATM fetch all read one window function.
    import option_chain as oc
    import inspect as _insp
    assert "max_dte=MAX_DTE)" in app, "app.py must pass max_dte=MAX_DTE to get_option_data()"
    assert "in_dte_window(dte, min_dte, max_dte)" in _insp.getsource(oc.get_full_chain_data)
    sc_src = Path("scanner.py").read_text(encoding="utf-8", errors="replace")
    assert sc_src.count("option_chain.in_dte_window(dte, OPT_MIN_DTE, OPT_MAX_DTE)") == 2, \
        "scanner's two chain fetches must read the window through option_chain.in_dte_window()"
    assert oc.in_dte_window(45, 21, 45) and not oc.in_dte_window(46, 21, 45) and not oc.in_dte_window(20, 21, 45)
    # A signal-bar date later than the last recorded scan bar is refused by
    # the app's form (2026-10-29: the first NOW trade was keyed to the alert's
    # day, not the bar's, and only a warning stood in the way).
    assert "is later than the last bar any scan has recorded" in app and \
        app.count("        return None\n    if not _hit:") == 1, \
        "signal_bar_input must refuse a date after the last recorded scan bar (return None)"
    # H1 / H1' read from amendment F forward (review 2026-10-28 F3).
    import forward_log as fl
    assert fl.HQ_READ_EXCLUDES == frozenset({"e7d75b855a51", "c357a0d01ff5", "ba6b3b46afa6"}), \
        "the HQ reading window must exclude exactly the three pre-F hashes"
    assert all(h in fl.KNOWN_CONFIGS for h in fl.HQ_READ_EXCLUDES)
    assert fl.hq_readable({"config": "4ddd12a3095f"}) and not fl.hq_readable({"config": "c357a0d01ff5"})
    assert "read from amendment F (2026-10-28) forward" in Path("forward_log.py").read_text(), \
        "report() must say the HQ lines are read from amendment F"
    import re as _re
    sites = [m.start() for m in _re.finditer(r"open_option_position\(\n", app)]
    assert len(sites) >= 3, "app.py must log positions from the quick log, the manual form and the checker"
    for i in sites:
        before, after = app[max(0, i - 300):i], app[i:i + 3200]
        assert _re.search(r"try:\s*\n\s*$", before) and _re.search(r"except (ValueError|Exception)\b", after), \
            f"app.py call site at offset {i} does not catch journal_store's refusal (ValueError) with try/except"
    for f, fields in (("trade_journal.json", ("ticker", "id")), ("open_positions.json", ("ticker", "id"))):
        for row in _json.loads(Path(f).read_text(encoding="utf-8") or "[]"):
            vals = " ".join(str(row.get(k, "")) for k in fields)
            assert "ZZZ" not in vals and not str(row.get("id", "")).startswith("zr-"), \
                f"{f} carries a fixture row: {vals}"
    # Behavioural, in memory: the refusals and the one allowed path.
    mem = {"pos": []}
    saved = {k: getattr(js, k) for k in ("load_positions", "save_positions")}
    try:
        js.load_positions = lambda: list(mem["pos"])
        js.save_positions = lambda d: mem.__setitem__("pos", list(d))
        rules = {"tp_pct": 200, "sl_pct": 50, "dte_exit": 7, "max_hold_bars": 20, "invalidate_ema": True}
        for kw, needle in ((dict(source="discretionary", mode="live"), "discretionary"),
                           (dict(source="signal", signal_key="X|Bullish|2026-10-07",
                                 traded_at="2026-10-07 10:00 ET", rules=dict(rules, invalidate_ema=False)), "thesis exit off"),
                           (dict(source="signal", signal_key="X|Bullish|2026-10-07",
                                 traded_at="2026-10-07 10:00 ET", expiry="2026-10-16"), "9 DTE"),
                           (dict(source="signal", signal_key="X|Bullish|2026-10-07",
                                 traded_at="2026-10-07 10:00 ET", rules=dict(rules, sl_pct=30)), "stop loss 30")):
            args = dict(ticker="X", right="CALL", strike=10.0, expiry="2026-11-20", contracts=1,
                        entry_premium=0.5, rules=rules)
            args.update(kw)
            try:
                js.open_option_position(**args)
                raise AssertionError(f"journal_store must refuse {kw}")
            except ValueError as e:
                assert needle in str(e) and (needle == "discretionary" or "BACKLOG 32" in str(e)), str(e)
        ok = js.open_option_position("X", "CALL", 10.0, "2026-11-20", 1, 0.5, rules, source="signal",
                                     signal_key="X|Bullish|2026-10-07", traded_at="2026-10-28 10:00 ET")
        assert ok["mode"] == "live" and len(mem["pos"]) == 1
        pp = js.open_option_position("X", "CALL", 10.0, "2026-11-05", 1, 0.5, {"tp_pct": 50}, mode="paper")
        assert pp["mode"] == "paper" and pp["source"] == "discretionary"
    finally:
        for k, v in saved.items():
            setattr(js, k, v)
    print("  signal entries only in the measured structure (one dict; scanner window and app defaults read it); "
          "no discretionary live entry; no fixture row committed; every app call site catches the refusal; "
          "DTE ceiling everywhere; H1/H1' read from amendment F")


def check_bar_revision_note_wired() -> None:
    """
    BACKLOG 39 (owner 2026-10-29): when a scan re-reads a bar the record
    already holds, a differing read writes ONE `revision` row (the fields
    that moved, the decision and tag before and after) from analyze(), by
    the record's one writer; the recorded row stands; a revision is never
    taken, open or an episode; summary() counts them and report() prints
    them.
    """
    import forward_log as fl
    sc_src = Path("scanner.py").read_text(encoding="utf-8", errors="replace")
    body = sc_src[sc_src.find("def analyze("):sc_src.find("\ndef ", sc_src.find("def analyze(") + 1)]
    assert "forward_log.find_signal(ticker, r[\"trend\"], _bar)" in body and \
        "forward_log.record_revision(_prev, _setup, decision, _hq)" in body, \
        "analyze() must compare a re-read bar with its recorded row before record_signal()"
    assert body.find("record_revision(") < body.find("forward_log.record_signal("), \
        "the comparison must run before the (deduped) record_signal() call"
    writers = [q.name for q in sorted(Path(".").glob("*.py"))
               if q.name not in ("forward_log.py", "consistency_check.py")
               and "forward_log.record_revision(" in q.read_text(encoding="utf-8", errors="replace")]
    assert writers == ["scanner.py"], f"revision rows must have one writer, scanner.py; found {writers}"
    rev = {"kind": "revision", "decision": ["taken", "taken"], "key": "X|Bullish|2026-10-28"}
    assert not fl.counts_as_taken(rev), "a revision row is never taken"
    assert "revisions" in fl.summary(Path("forward_log.jsonl")) and "bar revisions" in Path("forward_log.py").read_text()
    print("  a differing re-read writes one revision row from analyze(); one writer; never taken; counted and reported")


def check_post_close_pass_hygiene() -> None:
    """
    Review 2026-10-28, improvements 3, 4, 5 (owner 2026-10-29: "go with 3
    as well"): the post-close record pass stamps its live heartbeat
    `pass: "post_close"`; a second record-only dispatch on the same ET date
    is skipped on that row unless --force; the pass ends with one `clocks:`
    line naming every forward clock, each gate read from the module that
    owns it, not a literal; and the spread report tallies the cycles whose
    legs could not be quoted after the close, by reason (F4).
    """
    import basket_log as bl
    import forward_log as fl
    import spread_log as sl
    sc_src = Path("scanner.py").read_text(encoding="utf-8", errors="replace")
    run_body = sc_src[sc_src.find("\ndef run("):]
    assert 'pass_kind="post_close" if record_only else None' in run_body, \
        "the record pass must stamp its live heartbeat post_close"
    gate = run_body.find("_dup = None if args.force else post_close_already_recorded()")
    assert 0 < gate < run_body.find("spread_record_pass(") and "if _dup:" in run_body[gate:gate + 200] \
        and "return 0" in run_body[gate:gate + 400], \
        "run() must skip the repeat record pass on today's heartbeat, before any fetch, unless --force"
    helper = sc_src[sc_src.find("def post_close_already_recorded("):sc_src.find("def clocks_line(")]
    assert 'forward_log.last_scan(bucket="live")' in helper and 'ls.get("pass") != "post_close"' in helper \
        and ".astimezone(ET)" in helper, "the skip reads the stamped live heartbeat on the ET date"
    assert 'logger.info("clocks: %s", clocks_line())' in run_body and \
        run_body.find('logger.info("clocks: %s", clocks_line())') > run_body.find('logger.info("spread arms: %s"'), \
        "the record pass must log the clocks line after the spread arms"
    clocks = sc_src[sc_src.find("def clocks_line("):sc_src.find("def basket_record_pass(")]
    for ref in ("forward_log.GATE_EPISODES", "basket_log.RESULT_WEEKS", "spread_log.RESULT_CYCLES",
                "sp['unquoted_cycles']", "iv['graded']"):
        assert ref in clocks, f"clocks_line() must read {ref}, not a literal"
    assert fl.GATE_EPISODES == 349 and bl.RESULT_WEEKS == 52 and bl.FIRST_READ_WEEKS == 26 \
        and sl.RESULT_CYCLES == 12, "the forward gates"
    fl_src = Path("forward_log.py").read_text(encoding="utf-8")
    assert "{GATE_EPISODES} settled EPISODES" in fl_src and "{RESULT_WEEKS} graded weeks" in \
        Path("basket_log.py").read_text(encoding="utf-8"), "report() prints the gate from its constant"
    _sp = sl.summary(Path("spread_log.jsonl"))
    assert all("open_ratio" in a for a in _sp["arms"].values()) and \
        'if _open:\n        print(f"  open cycles at entry, market mid / model (NOT A READING' in \
        Path("spread_log.py").read_text(encoding="utf-8"), \
        "the spread report prints the open cycles' entry ratios as counts, labelled not a reading (G2)"
    assert "unquoted" in sl.summary(Path("spread_log.jsonl")) and "unquoted cycles" in \
        Path("spread_log.py").read_text(encoding="utf-8"), "spread_log reports the unquoted tally by reason"
    print("  record pass stamps its heartbeat; a same-day repeat is skipped; clocks line reads every gate; "
          "unquoted spread legs tallied")


def check_alert_once_per_bar() -> None:
    """
    Logic review 2026-10-29 L1 (owner: "cooldown by signal key"): the same
    signal bar is never alerted twice. Both alerting loops (watchlist and
    pool) ask the record for a DELIVERED alert row with the signal's key
    before sending, after the 4-hour stamp check that stays as the outage
    backstop; a failed delivery (no delivered row) is re-sent.
    """
    sc_src = Path("scanner.py").read_text(encoding="utf-8", errors="replace")
    helper = sc_src[sc_src.find("def alerted_on_bar("):sc_src.find("def recently_alerted(")]
    assert "forward_log.alerts_sent()" in helper and 'a.get("delivered")' in helper and \
        'a.get("key") == signal_key' in helper and "if not signal_key:" in helper, \
        "alerted_on_bar() reads the delivered alert rows by key; no key never suppresses"
    run_body = sc_src[sc_src.find("\ndef run("):]
    n = run_body.count('if alerted_on_bar(r.get("signal_key")):')
    assert n == 2, f"both alerting loops must ask alerted_on_bar() before sending (found {n})"
    for loop in ("compose_alert(r, tk)\n", "compose_alert(r, tk, header=_hdr)"):
        i = run_body.find(loop)
        assert i > 0 and 'if alerted_on_bar(r.get("signal_key")):' in run_body[i - 1200:i] \
            and "if recently_alerted(state, tk, r[\"trend\"]):" in run_body[i - 1200:i], \
            f"the key check must sit between the stamp check and the send ({loop.strip()})"
    assert "one alert per signal bar" in sc_src, "the selftest proves it"
    print("  the same signal bar is never alerted twice: both loops read the delivered alert rows by key")


def check_fill_gate_and_blackout_bar() -> None:
    """
    Review 2026-10-30 J1/J2 (owner: "go with J1 and J2 as well"). J1: both
    alerting loops ask gapped_at_fill() — the grader's gap rule on the next
    open and the latest print — after the key check and before the send,
    withhold on a hit and record ONE undelivered "NOT SENT" row per key;
    the record is untouched (the row stays taken). J2: the earnings gate
    is computed against the signal bar's date in BOTH callers of
    evaluate(), and the shared rule blocks a release between the bar and
    the fill.
    """
    import inspect as _ci
    import datetime as _dt
    import market_context as mc
    sc_src = Path("scanner.py").read_text(encoding="utf-8", errors="replace")
    app_src = Path("app.py").read_text(encoding="utf-8", errors="replace")
    # J1 — the helper mirrors the grader: long voids at <= stop or >= target, short mirrored.
    import scanner as _sc
    assert _sc.gapped_at_fill({"trend": "Bullish", "stop": 98.0, "target": 106.0}, 98.0) and \
        _sc.gapped_at_fill({"trend": "Bullish", "stop": 98.0, "target": 106.0}, 100.0, 106.0) and \
        _sc.gapped_at_fill({"trend": "Bearish", "stop": 102.0, "target": 94.0}, 102.0) and \
        _sc.gapped_at_fill({"trend": "Bullish", "stop": 98.0, "target": 106.0}, 100.0, 100.0) is None and \
        _sc.gapped_at_fill({"trend": "Bullish", "stop": 98.0, "target": 106.0}, None) is None, \
        "gapped_at_fill() must mirror forward_log.resolve_signal's gap rule, inclusive, and never withhold on no price"
    gr = _ci.getsource(__import__("forward_log").resolve_signal)
    assert "gapped = ((fill <= stop or fill >= target) if long" in gr, "the grader's rule this mirrors"
    run_body = sc_src[sc_src.find("\ndef run("):]
    for loop, probe in (("compose_alert(r, tk)\n", "gapped_at_fill(r, *fill_probe(df, cdf))"),
                        ("compose_alert(r, tk, header=_hdr)", "gapped_at_fill(r, *fill_probe(frames[tk], pool_cdfs.get(tk)))")):
        i = run_body.find(loop)
        seg = run_body[i - 1500:i]
        assert i > 0 and probe in seg and seg.find('if alerted_on_bar(r.get("signal_key")):') < seg.find(probe), \
            f"the fill check must sit between the key check and the send ({loop.strip()})"
        gate = seg[seg.find(probe):]
        assert "if not withheld_on_bar(r.get(\"signal_key\")):" in gate and '"NOT SENT: " + _gap' in gate \
            and "skipped += 1" in gate and "continue" in gate, \
            f"a gapped fill records one NOT SENT row per key, counts as skipped and sends nothing ({loop.strip()})"
        assert "forward_log.record_signal" not in gate and "decision" not in gate, "the record is not touched by the alert gate"
    assert "fill check before alert" in sc_src, "the selftest proves it"
    # J2 — both callers pass the signal bar's date; the shared rule blocks the straddle.
    for path, src in (("scanner.py", sc_src), ("app.py", app_src)):
        assert "earnings=check_earnings_blackout(ticker, bar_date=_bar_for_earnings)" in src and \
            "_bar_for_earnings = " in src and "_bar_dates(df, None).max().date()" in src, \
            f"{path}: evaluate() must receive the blackout computed against the signal bar (J2)"
        assert "bar_date=bar_date)" in src[src.find("def check_earnings_blackout("):][:2500], \
            f"{path}: check_earnings_blackout() must pass bar_date through to market_context"
    assert mc.earnings_blackout_from_dates([_dt.date(2026, 10, 28)], _dt.date(2026, 10, 29),
                                           bar_date=_dt.date(2026, 10, 28))[0] is False, "NOW's straddle blocks"
    assert mc.earnings_blackout_from_dates([_dt.date(2026, 10, 27)], _dt.date(2026, 10, 29),
                                           bar_date=_dt.date(2026, 10, 28))[0] is True, "a release before the bar does not"
    assert mc.earnings_blackout_from_dates([_dt.date(2026, 11, 5)], _dt.date(2026, 10, 29),
                                           bar_date=_dt.date(2026, 10, 28))[0] is True, "nor a release after today, outside the window"
    assert mc.get_earnings_blackout("X", lambda t: {"Earnings Date": [_dt.date(2026, 10, 26)]}, _dt.date(2026, 10, 29),
                                    bar_date=_dt.date(2026, 10, 26))[0] is False, \
        "get_earnings_blackout() passes bar_date through (the release is outside the post-window, inside the straddle)"
    assert "the straddle" in Path("market_context.py").read_text(encoding="utf-8"), "the selftest proves it"
    print("  alerts ask the grader's fill rule first and record NOT SENT once; the blackout reads the signal bar's date (J1/J2)")


def check_risk_floor_single_sourced() -> None:
    """
    Grading amendment 2026-10-29 (logic review L2, owner's option (a)): the
    risk floor is ONE function, risk_params.min_risk = max($0.05, 0.3 % of
    price), read by the signal (zero_risk), the pullback rule and the
    grader, which VOIDs a fill inside it under gapped_before_fill with the
    note; the amendment names the constants and the three rows that stand.
    """
    import inspect
    import risk_params as rp
    import forward_log as fl
    assert rp.RISK_FLOOR_ABS == 0.05 and rp.RISK_FLOOR_PCT == 0.003 and rp.min_risk(10.0) == 0.05 \
        and abs(rp.min_risk(100.0) - 0.30) < 1e-9, "the floor's constants"
    for mod, call in (("signal_core.py", "risk_params.min_risk(price)"),
                      ("pullback_signal.py", "risk_params.min_risk(price)"),
                      ("forward_log.py", "risk_params.min_risk(fill)")):
        src = Path(mod).read_text(encoding="utf-8", errors="replace")
        assert call in src and "max(0.05, price * 0.003)" not in src and "max(0.05, price*0.003)" not in src, \
            f"{mod} must read the floor from risk_params.min_risk, not a literal"
    body = inspect.getsource(fl.resolve_signal)
    assert "if risk < floor:" in body and '"note": "fill inside the risk floor"' in body \
        and body.find("floor = risk_params.min_risk(fill)") > body.find("risk = abs(fill - stop)") \
        and body.find("if risk < floor:") < body.find("for j, b in enumerate(after[:max_hold]):"), \
        "the grader voids a fill inside the floor, after the fill and before the walk"
    row = {"trend": "Bullish", "bar_date": "2026-01-05",
           "setup": {"price": 100.0, "stop": 98.0, "target": 106.0}}
    bars = [{"date": "2026-01-05", "open": 100, "high": 101, "low": 99, "close": 100},
            {"date": "2026-01-06", "open": 98.05, "high": 110, "low": 98.01, "close": 108}]
    import datetime as _dt
    v = fl.resolve_signal(row, bars, today=_dt.date(2026, 3, 1))
    assert v["outcome"] == "void" and v["detail"].get("note") == "fill inside the risk floor", v
    doc = Path("results/forward_grading_preregistration.md").read_text(encoding="utf-8")
    assert "## AMENDED 2026-10-29" in doc and "max($0.05, 0.3 % of price)" in doc \
        and "made after 182 of them" in doc and "KO 2026-10-26" in doc and "void_risk_floor" in doc, \
        "the amendment names the rule, the breach of the no-amendment clause, and the rows that stand"
    assert "void_risk_floor" in fl.summary(Path("forward_log.jsonl")) and "voided at the risk floor" in \
        Path("forward_log.py").read_text(encoding="utf-8")
    bt_src = Path("backtest.py").read_text(encoding="utf-8", errors="replace")
    sim = bt_src[bt_src.find("def simulate_trade("):bt_src.find("\ndef ", bt_src.find("def simulate_trade(") + 1)]
    assert "floor = risk_params.min_risk(entry)" in sim and 'cfg.get("risk_floor", True)' in sim \
        and '"fill inside the risk floor"' in sim and sim.find("if risk < floor:") > sim.find("if gapped:") \
        and sim.find("if risk < floor:") < sim.find("mfe = mae = 0.0"), \
        "the research engine refuses a fill inside the floor, after the gap check and before the walk, unless risk_floor=False"
    assert '"risk_floor": False' in Path("intraday_rules.py").read_text(encoding="utf-8"), \
        "the intraday engine keeps its pre-registered engine (risk_floor=False), explicitly"
    ed = Path("engine_differential.py").read_text(encoding="utf-8")
    assert 'out["void_floor"] += 1' in ed and '"engine floored, grader did not"' in ed and \
        '"grader floored, engine filled"' in ed and 'assert tot["void_floor"] >= 1' in ed, \
        "the differential counts a fill both engines refuse and reports one engine flooring alone as a mismatch"
    run2 = Path("results/engine_differential_run2.txt").read_text(encoding="utf-8")
    assert "void at the risk floor in both engines (2026-10-29): 119" in run2 and "VERDICT: the engines AGREE" in run2 \
        and "mismatches 0 at" in run2, "run 2 of the differential, on the floored engines, must agree"
    assert Path("results/engine_differential_run1.txt").read_text(encoding="utf-8").startswith("SUPERSEDED by engine_differential_run2.txt"), \
        "run 1 is the pre-floor reading and says so"
    assert "**The research engine takes the same floor**" in doc and "`risk_floor=False`" in doc, \
        "the amendment says the research engine floors too, and which engine does not"
    print("  the risk floor is one function in risk_params, read by the signal, the pullback rule, the grader and "
          "the research engine; the differential counts it in both")


def check_hq_episodes_reanchored() -> None:
    """
    Review 2026-10-29 G1 (owner: "go with G1 as well"): the HQ questions
    (H1, H1') form their episodes from the readable rows, re-anchored at the
    amendment F cut; the control pairing from the rows that carry a control;
    the base question and the gate keep the full episodes. Pinned on the
    code, on a straddling fixture, and on the pre-registration's note.
    """
    import inspect
    import forward_log as fl
    src = inspect.getsource(fl.summary)
    assert 'episodes([s for s in taken if hq_readable(s)], sd_out)' in src, \
        "H1/H1' episodes must be formed from the readable rows"
    assert 'episodes([s for s in taken if s.get("seq") in ctl_by_ref], sd_out)' in src and \
        "for e in ctl_eps:" in src, "the control pairing's episodes must be formed from the controlled rows"
    assert "eps = episodes(taken, sd_out)" in src and "_episode_stats([e for e in eps if e[\"bucket\"] == b])" in src, \
        "the base question keeps the full episodes"
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "g1.jsonl"
    pre = {"adx_min": 35.0, "min_rr": 0.5, "atr_stop_mult": 1.25, "atr_tgt_mult": 4.0, "weekly_confirm": False,
           "longs_only": True, "hq_min_rr": 1.0, "volume_mult": 1.2, "volume_soft_mult": 0.7, "spy_regime_on": True}
    assert fl.config_fingerprint(pre) in fl.HQ_READ_EXCLUDES
    setup = {"price": 100.0, "entry": 100.0, "stop": 98.0, "target": 106.0, "rr": 3.0, "adx": 31.0, "hq_fail": []}
    a = fl.record_signal("ST", "Bullish", setup, "taken", "", pre, bar_date="2026-10-27", path=tmp, hq=True, bucket="pool")
    b = fl.record_signal("ST", "Bullish", setup, "taken", "", {"x": 1}, bar_date="2026-10-28", path=tmp, hq=True, bucket="pool")
    fl.record_outcome(a["seq"], "win", 5.0, "stop_distance", path=tmp)
    fl.record_outcome(b["seq"], "loss", -1.0, "stop_distance", path=tmp)
    s = fl.summary(tmp)
    assert s["by_episode"]["pool"]["episodes"] == 1 and s["by_episode"]["pool"]["settled"] == 1 \
        and abs(s["by_episode"]["pool"]["mean_r"] - 5.0) < 1e-9, "the base question reads the episode from 10-27"
    h = s["h1_rank"]
    assert h["episodes"] == 1 and h["tier"]["settled"] == 1 and abs(h["tier"]["mean_r"] + 1.0) < 1e-9, \
        (h, "H1' reads the straddling episode once, re-anchored at 10-28 with that row's outcome")
    doc = Path("results/forward_record_preregistration.md").read_text(encoding="utf-8")
    assert "**Note 2026-10-29 (review G1" in doc and "re-anchored at the cut" in doc and \
        "the pair is the first controlled row's" in doc, "the note under amendment F states the rule"
    assert "episodes re-anchored at the cut" in Path("forward_log.py").read_text(encoding="utf-8"), "report() says so"
    print("  H1/H1' and the control pairing form their episodes from the rows their arm can read; the base question keeps the full episodes")


def check_intraday_rules_match_preregistration() -> None:
    """
    intraday_rules.py's constants are the numbers its pre-registration fixed.

    The document was written first and the code from it; a constant edited in
    the code without a dated amendment in the document is the drift the
    pre-registration exists to prevent. Each number below is read from the
    document's text and compared to the module.
    """
    import intraday_rules as ir
    doc = Path("results/intraday_rules_preregistration.md").read_text(encoding="utf-8")
    assert "5 bps per side" in doc and ir.SLIPPAGE_BPS == 5.0, "slippage"
    assert "first 6 bars" in doc and ir.OR_BARS == 6, "opening range"
    assert "RVOL ≥ 1.5" in doc and ir.RVOL_MIN == 1.5, "RVOL floor"
    assert "≥ 1 ATR below VWAP" in doc and ir.VWAP_DEV_ATR == 1.0, "VWAP deviation"
    assert "≥ 10 bars" in doc and ir.DIV_MIN_BARS == 10, "divergence spacing"
    assert "±3 bars" in doc and ir.PIVOT_W == 3, "pivot window"
    assert "prior 20 sessions" in doc and ir.RVOL_LOOKBACK == 20, "RVOL lookback"
    assert "named 50.0–61.8 %" in doc and ir.FIB_ZONES["named"] == (0.500, 0.618), "named zone"
    assert "control-low 30.0–41.8 %" in doc and ir.FIB_ZONES["control_low"] == (0.300, 0.418)
    assert "control-high 70.0–81.8 %" in doc and ir.FIB_ZONES["control_high"] == (0.700, 0.818)
    assert "mean R ≥ **+0.15**" in doc and ir.BAR_MEAN_R == 0.15, "bar on the mean"
    assert "≥ **+0.15 R**" in doc and ir.BAR_OVER_NULL == 0.15, "bar over the null"
    assert "Twenty draws" in doc and ir.NULL_DRAWS == 20, "null draws"
    assert "20 symbols" in doc and len(ir.UNIVERSE) == 20, f"universe has {len(ir.UNIVERSE)} names"
    assert "60 calendar days" in doc and ir.PERIOD == "60d" and ir.INTERVAL == "5m"
    assert "python intraday_rules.py --selftest" in Path(".github/workflows/tests.yml").read_text()
    # The 5-minute profile is the numbers above and must not drift while
    # other profiles are added; the hourly profile is its own document's.
    p5 = ir.TIMEFRAMES["5m"]
    assert (p5["period"], p5["interval"], p5["or_bars"], p5["pivot_w"], p5["div_min_bars"],
            p5["structure_sessions"], p5["ci_z"]) == ("60d", "5m", 6, 3, 10, 1, 1.96), p5
    hdoc = Path("results/intraday_rules_hourly_preregistration.md").read_text(encoding="utf-8")
    p1 = ir.TIMEFRAMES["1h"]
    assert "**730 calendar days**" in hdoc and p1["period"] == "2y" and p1["interval"] == "1h", \
        "the hourly fetch is the provider's canonical 2y (= 730 days)"
    assert "opening range = the first bar" in hdoc and p1["or_bars"] == 1, "hourly opening range"
    assert "pivots over ±1 bar" in hdoc and p1["pivot_w"] == 1, "hourly pivot window"
    assert "≥ 3 bars apart" in hdoc and p1["div_min_bars"] == 3, "hourly divergence spacing"
    assert "the current and the previous session" in hdoc and p1["structure_sessions"] == 2
    assert "**97.5 %**" in hdoc and "z = 2.24" in hdoc and p1["ci_z"] == 2.24, "hourly interval"
    # The 5-minute and hourly tests are DAY trades: flat at the session's close.
    assert p5["group"] == "session" and p5["hold_bars"] is None and p5["resample"] is None, p5
    assert p1["group"] == "session" and p1["hold_bars"] is None and p1["resample"] is None, p1
    # The 4-hour profile is the third test (99%), built from the hourly cache,
    # read by the trading week, held across sessions (its document says why).
    fdoc = Path("results/intraday_rules_4h_preregistration.md").read_text(encoding="utf-8")
    p4 = ir.TIMEFRAMES["4h"]
    assert "**resampled from the frozen hourly cache**" in fdoc and \
        (p4["interval"], p4["period"], p4["resample"]) == ("1h", "2y", "4h"), \
        "the 4h profile reads the hourly cache key and resamples in code"
    assert "**the trading week**" in fdoc and p4["group"] == "week", "4h window is the week"
    assert "tenth 4-hour bar held" in fdoc and p4["hold_bars"] == 10, "4h hold"
    assert "opening range = the first bar of the week" in fdoc and p4["or_bars"] == 1
    assert "pivots over ±1 bar" in fdoc and p4["pivot_w"] == 1, "4h pivot window"
    assert "≥ 3 bars apart" in fdoc and p4["div_min_bars"] == 3, "4h divergence spacing"
    assert "the current and the previous week" in fdoc and p4["structure_sessions"] == 2
    assert "**99 %**" in fdoc and "z = 2.576" in fdoc and p4["ci_z"] == 2.576, "4h interval"
    assert "python intraday_rules.py --timeframe 4h" in fdoc
    assert ir.TIMEFRAME == "5m", "the module must default to the 5-minute profile"
    print("  intraday_rules constants equal the pre-registration's numbers (all three profiles); "
          "selftest in CI")


def check_daytrade_paper_isolated() -> None:
    """
    Phase 2 of the day-trading screen is a SEPARATE record with ONE writer.

    daytrade_paper.jsonl is the owner's discretionary paper record (BACKLOG
    29 Phase 2). It must never blend into the swing record — the paper/live
    lesson one level down — so the journal, the app, the scanner and the
    forward log never read it, and only daytrade_paper.py writes it. The
    module's constants are its pre-registration's numbers, and its grader
    is the Phase 1 engine (same costs, same null, same dust), so Phase 2 is
    read on the same scale as Phase 1.
    """
    import daytrade_paper as dp
    import intraday_rules as ir
    import forward_log as fl
    doc = Path("results/daytrade_paper_preregistration.md").read_text(encoding="utf-8")
    assert "**349 settled episodes**" in doc and dp.EPISODES_REQUIRED == 349, "the reading gate"
    assert "withholds the mean" in doc and "no peek flag" in doc, "the gate is documented"
    assert "plus 5 bps" in doc and dp.SLIPPAGE_BPS == ir.SLIPPAGE_BPS == 5.0, "Phase 1 costs"
    assert "±0.05 R is a scratch" in doc and dp.DUST_R == fl.GRADE_DUST_R == 0.05, "dust"
    assert "twenty fixed-seed random bars" in doc and dp.NULL_DRAWS == ir.NULL_DRAWS == 20
    assert "**95 %**" in doc and dp.CI_Z == 1.96, "one pooled test at 95%"
    assert "by more than 0.1 %" in doc and dp.PRICE_TOL == 0.001, "price-in-bar tolerance"
    assert "**50 days**" in doc and dp.NO_BARS_DAYS == 50, "stale-call void"
    assert "before 15:55 ET" in doc and dp.LAST_CALL_MINUTE == 15 * 60 + 55
    assert set(dp.SETUPS) == set(ir.RULES) | {"pattern", "other"}, "setup names are Phase 1's list"
    assert Path("daytrade_paper.jsonl").exists(), "the record is committed (seeded empty)"
    # One writer, never blended.
    for p in sorted(Path(".").glob("*.py")):
        if p.name in ("daytrade_paper.py", "consistency_check.py"):
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        assert "daytrade_paper" not in txt, \
            f"{p.name} references the Phase 2 paper record; only daytrade_paper.py may"
    src = Path("daytrade_paper.py").read_text(encoding="utf-8")
    import re as _re
    _calls = [a for a in _re.findall(r"forward_log\.append\(([^)]*)\)", src) if a.strip()]
    assert _calls and all("path=" in a for a in _calls), \
        "daytrade_paper.py writes its own chain through forward_log.append(path=...), never the forward log"
    assert "import streamlit" not in src and "import journal_store" not in src \
        and "import gh_sync" not in src, "daytrade_paper.py must not pull Streamlit"
    assert "python daytrade_paper.py --selftest" in Path(".github/workflows/tests.yml").read_text()
    print("  Phase 2 paper record: one writer, never read by journal/app/scanner, "
          "constants equal the pre-registration, Phase 1 engine")


def check_pool_bucket_wired() -> None:
    """
    The pool bucket (BACKLOG 30) is wired end to end and stays its own bucket.

    Owner's decision 2026-10-13, amended into
    results/forward_record_preregistration.md: once a session the scanner
    evaluates the candidate pool beyond the watchlist, alerts on at most
    POOL_ALERT_CAP HQ signals by relative strength, and records every pool
    signal under bucket=pool. Half-wired is the failure: a flag with no
    workflow input is a feature nobody can trigger; a pool scan whose rows
    say `live` poisons the watchlist's record; a pool scan row the coverage
    alarm counts as a heartbeat hides a silent watchlist.
    """
    import tempfile
    import scanner
    import universe as uni
    import forward_log as fl
    src = Path("scanner.py").read_text(encoding="utf-8")
    assert '"--force-pool"' in src and "def pool_tickers" in src and "def pool_due" in src
    assert "POOL_ALERT_CAP   = 5" in src and scanner.POOL_ALERT_CAP == 5, "the cap is five"
    import forward_log as _fl
    assert _fl.RANK_TIER_K == scanner.POOL_ALERT_CAP == 5, (
        "the H1' rank tier (forward_record_preregistration.md, amendment 2026-10-28 D) is "
        "the top POOL_ALERT_CAP rows a bar; the two constants must stay equal")
    assert 'bucket="pool"' in src and "def compose_alert" in src
    doc = Path("results/forward_record_preregistration.md").read_text(encoding="utf-8")
    assert "## Amendment 2026-10-13" in doc and "**at most five a session**" in doc \
        and "**relative strength**" in doc and "`universe.RS_LOOKBACK`" in doc, \
        "the amendment must state the cap and the ranking"
    wf = Path(".github/workflows/scanner.yml").read_text()
    assert "      pool:\n" in wf and "--force-pool" in wf, "scanner.yml has no `pool` input"
    j = wf.find("name: Run scanner")
    assert "inputs.pool && '--force-pool'" in wf[j:j + 1800], \
        "the alerting step must pass --force-pool on the `pool` input"
    # The pool is the universe's pool minus the watchlist: reserved-safe by
    # check_universe_not_spending_reserved, and never the watchlist twice.
    pt = scanner.pool_tickers(["NVDA", "ZZZZ"])
    assert pt == sorted(set(uni.CANDIDATE_POOL) - {"NVDA"}) and "NVDA" not in pt, pt[:5]
    # The record keeps the buckets apart and the heartbeat reads live only.
    tmp = Path(tempfile.mkdtemp()) / "pool_check.jsonl"
    cfg = {"adx_min": 25}
    fl.record_scan("2026-10-13", ["P1"], 0, cfg, path=tmp, bucket="pool")
    assert fl.last_scan(tmp) is None, "a pool scan row must not be the coverage heartbeat"
    row = fl.record_signal("P1", "Bullish", {"price": 1.0}, "taken", "", cfg,
                           bar_date="2026-10-13", path=tmp, hq=False, bucket="pool")
    assert row["bucket"] == "pool" and fl.summary(tmp)["by_bucket"].get("pool", {}).get("taken") == 1
    assert fl.bucket_of({"kind": "signal"}) == "live", "legacy rows are the live bucket's"
    # A forced record-only pass never scans the pool: the code path is gated.
    assert "if not record_only and (getattr(args, \"force_pool\", False) or pool_due(state))" in src
    print(f"  pool bucket: once a session, cap {scanner.POOL_ALERT_CAP} by RS, rows under "
          f"bucket=pool, heartbeat reads live; workflow input wired")


def check_pullback_bucket_wired() -> None:
    """
    The pullback bucket (BACKLOG 31) is the pre-registered rule, built from
    the live signal's own pieces, recorded under its own bucket, never alerted.

    results/pullback_preregistration.md fixes the rule: the base BEARISH
    setup (signal_core.base_trend, the function evaluate() itself calls),
    the close above its 200-session SMA, and the live LONG-side levels
    (signal_core.long_levels, also evaluate()'s own). A copy of either
    function would be the duplicated-logic failure this repo already paid
    for; a row written under the live bucket would poison the base record;
    an alert would make a measurement into a strategy.
    """
    import forward_log as fl
    import pullback_signal as pbs
    doc = Path("results/pullback_preregistration.md").read_text(encoding="utf-8")
    assert "`pullback_signal.SMA_SESSIONS = 200`" in doc and pbs.SMA_SESSIONS == 200
    assert "`signal_core.base_trend()`" in doc and "`signal_core.long_levels()`" in doc
    assert "**none, ever**" in doc and "349" in doc and "bucket | `pullback`" in doc
    core = Path("signal_core.py").read_text(encoding="utf-8")
    assert "def base_trend(" in core and "def long_levels(" in core
    ev = core[core.find("\ndef evaluate("):]
    assert "trend = base_trend(latest, params)" in ev and "= long_levels(df, price, atr, params)" in ev, \
        "evaluate() must use the shared functions, or the pullback rule reads a copy"
    src = Path("pullback_signal.py").read_text(encoding="utf-8")
    fn = src[src.find("def evaluate_pullback("):src.find("\n# ── self-test")]
    assert "sc.base_trend(" in fn and "sc.long_levels(" in fn, \
        "evaluate_pullback() must call the shared functions, not the selftest only"
    assert "forward_log" not in src.replace("the forward log", "") and "send_alert" not in src \
        and "yfinance" not in src, "pullback_signal.py must not write, alert or fetch"
    assert "pullback" in fl.BUCKETS
    sc_src = Path("scanner.py").read_text(encoding="utf-8")
    assert sc_src.count("_record_pullback(tk, df, cdf, spy_regime, ") == 2, \
        "the pullback is evaluated in BOTH loops (watchlist and pool)"
    i = sc_src.find("def _record_pullback(")
    body = sc_src[i:sc_src.find("\ndef run(", i)]
    assert 'bucket="pullback"' in body and "send_alert" not in body and "state[" not in body, \
        "the pullback record path must not alert or stamp a cooldown"
    assert "python pullback_signal.py --selftest" in Path(".github/workflows/tests.yml").read_text()
    print("  pullback bucket: shared base_trend/long_levels, SMA 200 pinned, bucket=pullback, "
          "both loops, never alerted")


def check_pullback_arrival_outcome_free() -> None:
    """
    pullback_arrival.py counts how often the pullback rule fires on history
    and NOTHING ELSE. The pullback bucket is a forward test read once at 349
    settled episodes; a history-based R, win rate or exit for the same rule
    would be the peek the pre-registration exists to prevent. The module's
    code (not its docstring) must contain no engine call and no outcome word,
    and the reporter must have nothing to print but counts.
    """
    src = Path("pullback_arrival.py").read_text(encoding="utf-8")
    body = src.split('"""', 2)[2]
    for banned in ("simulate_trade", "resolve_signal", "outcome", "exit_price", "mean_r",
                   "win_rate", "stop_distance", "record_signal", "record_outcome"):
        assert banned not in body, f"pullback_arrival.py mentions {banned!r} outside its docstring"
    assert "python pullback_arrival.py --selftest" in Path(".github/workflows/tests.yml").read_text()
    assert "python engine_differential.py --selftest" in Path(".github/workflows/tests.yml").read_text()
    print("  pullback_arrival.py is outcome-free (counts only); both data checks' selftests run in CI")


def check_yahoo_downloads_raw_prices() -> None:
    """
    Every yf.download() call in the live paths passes auto_adjust=False.

    yfinance's default is auto_adjust=True, and a fetch that leaves it out
    silently reads DIVIDEND-ADJUSTED history: the same bar carries a different
    close on a dividend payer, so one path signals and another does not. The
    app's batch fetch left it out while its single fetch named it (external
    review 2026-10-19, BACKLOG 35). Each call is read to its closing paren so
    a flag on a continuation line counts.
    """
    import re
    import backtest as _bt
    assert _bt.AUTO_ADJUST is False, "backtest.AUTO_ADJUST must stay False (raw prices)"
    calls = 0
    for fname in ("app.py", "scanner.py", "exit_monitor.py", "backtest.py", "data_source.py"):
        txt = Path(fname).read_text()
        for m in re.finditer(r"yf\.download\(", txt):
            line_start = txt.rfind("\n", 0, m.start()) + 1
            if "#" in txt[line_start:m.start()]:
                continue                                  # a comment that mentions the call
            depth, i = 0, m.end() - 1
            while i < len(txt):
                if txt[i] == "(":
                    depth += 1
                elif txt[i] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            call = txt[m.start():i + 1]
            if call[len("yf.download("):-1].strip() in ("", "...", "%s"):
                continue                                  # a log message or docstring, not a call
            calls += 1
            line = txt.count("\n", 0, m.start()) + 1
            assert "auto_adjust=False" in call or "auto_adjust=AUTO_ADJUST" in call, (
                f"{fname}:{line}: yf.download() without auto_adjust=False reads "
                f"adjusted prices — a different series from every other fetch")
    assert calls >= 6, f"only {calls} yf.download() calls found — the scan has stopped seeing them"
    print(f"  every yf.download() in the live paths ({calls} calls) names auto_adjust=False")


def check_bar_validator_wired() -> None:
    """
    BACKLOG 36 group A: signal_core.validate_bars() is the one validator, and
    it has to be CALLED. scanner.get_data() refuses a frame with the reason
    before anything is scanned; bars_for_grading() refuses it before the
    grader sees a NaN low. A validator nobody calls validates nothing.
    """
    src = Path("scanner.py").read_text(encoding="utf-8", errors="replace")
    for fn in ("def get_data(", "def bars_for_grading("):
        i = src.index(fn)
        j = src.find("\ndef ", i + 1)            # this function's body only, not the next one's
        body = src[i:j if j > 0 else len(src)]
        assert "sc.validate_bars(" in body, (
            f"scanner.{fn.split('(')[0][4:]}() no longer calls signal_core.validate_bars(); "
            f"a NaN close or a duplicated date reaches the scan or the grader unrefused")
    print("  get_data() and bars_for_grading() both run signal_core.validate_bars()")


CHECKS = [
    ("market calendars identical across 5 copies", check_calendars_identical),
    ("market calendar has runway left",            check_calendar_runway),
    ("access tokens have runway left",             check_token_runway),
    ("compute() agrees across modules",            check_compute_agrees),
    ("app.py compute() indicator set intact",      check_app_compute_source),
    ("app.py defaults derive from signal_core",    check_app_defaults_derived),
    ("every Yahoo download reads raw prices",      check_yahoo_downloads_raw_prices),
    ("backtest.evaluate_signal callers correct",   check_backtest_callers),
    ("weekly trend + SPY regime are one rule",     check_market_context_shared),
    ("earnings gate is one rule, reads every date", check_earnings_gate_shared),
    ("capital never gates an alert",             check_capital_never_gates_alerts),
    ("forward log carries no test data",         check_forward_log_has_no_test_data),
    ("forward log has one writer, and is committed", check_forward_log_single_writer),
    ("unattended workflows pinned + silence alarm",   check_unattended_workflows_pinned_and_alarmed),
    ("post-close record-only run is wired",       check_post_close_record_wired),
    ("research/live config divergence declared",   check_research_config_divergence_declared),
    ("HQ tier tags the record, gates only alerts", check_hq_tier_annotates_record),
    ("outcome bases coexist, never pooled",        check_outcome_bases_never_pooled),
    ("signal key rides alert->app->journal->log", check_signal_key_round_trip),
    ("option IV multiplier has one home",          check_option_iv_single_source),
    ("IV snapshot: one writer, committed, post-close", check_iv_snapshot_single_writer_and_committed),
    ("RS basket: one writer, committed, post-close", check_basket_log_single_writer_and_committed),
    ("control arm: one per taken row, graded",      check_control_arm_wired),
    ("gates compare unrounded values",             check_gates_compare_unrounded),
    ("contract selection: one selector",          check_contract_selection_shared),
    ("app paths: batch gaps, regime minimum, status", check_app_paths_group_c),
    ("intraday rules match their pre-registration", check_intraday_rules_match_preregistration),
    ("Phase 2 paper record isolated, one writer",   check_daytrade_paper_isolated),
    ("pool bucket wired, capped, kept apart",       check_pool_bucket_wired),
    ("pullback bucket: shared rule, never alerted", check_pullback_bucket_wired),
    ("pullback arrival check is outcome-free",       check_pullback_arrival_outcome_free),
    ("outcome buckets never gate a trade",       check_taxonomy_never_gates),
    ("CRLF files keep their line endings",       check_line_endings_preserved),
    ("direction gate is live-only",              check_direction_gate_is_live_only),
    ("every --selftest module runs in CI",        check_selftests_run_in_ci),
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
    ("bar validator runs on every live frame",    check_bar_validator_wired),
    ("research clocks: group B",                  check_research_clocks_group_b),
    ("spread arm pre-registered and wired",      check_spread_arm_preregistered),
    ("HQ tier legs declared (F, G)",             check_hq_tier_legs_declared),
    ("research config choice (BACKLOG 23)",      check_research_config_choice_declared),
    ("journal hygiene (BACKLOG 32)",             check_journal_hygiene),
    ("bar revision note wired (BACKLOG 39)",     check_bar_revision_note_wired),
    ("post-close pass hygiene (review 10-28, 3-5)", check_post_close_pass_hygiene),
    ("one alert per signal bar (review 10-29 L1)", check_alert_once_per_bar),
    ("risk floor single-sourced (review 10-29 L2)", check_risk_floor_single_sourced),
    ("HQ episodes re-anchored at the F cut (G1)",  check_hq_episodes_reanchored),
    ("fill gate before alert, blackout vs bar (J1/J2)", check_fill_gate_and_blackout_bar),
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
