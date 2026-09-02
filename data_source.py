#!/usr/bin/env python3
"""
data_source.py — Yahoo first, Tiingo as a fallback, Stooq kept as a dead stub

WHY THIS EXISTS
---------------
yfinance scrapes unofficial Yahoo endpoints. When Yahoo throttles it does not
raise — it returns an empty frame — which produced four separate bugs in this
codebase, each surfacing as a confident but wrong message ("check the symbol",
"no option chain", "not a listed expiry"). Retries help, but they only make a
throttled app slower; they cannot make it work.

A second, independent source of daily OHLCV closes that gap. When Yahoo comes
back empty after its retries, we ask the fallback instead of failing.

WHY TIINGO AND NOT STOOQ
------------------------
Stooq was the original fallback: no API key, no account. It now serves a
JavaScript proof-of-work challenge instead of CSV (see fetch_stooq() below),
so it is disabled — solving that from Python would mean running a JS engine
or reimplementing a hash loop whose parameters can change without notice, not
something to build a data path on.

Tiingo has a real REST API (https://api.tiingo.com/tiingo/daily/{ticker}/prices)
gated by a free API key from tiingo.com/account/api/token. Set TIINGO_API_KEY
and fetch_tiingo() activates automatically; leave it unset and this module
degrades to the pre-Tiingo Yahoo-only behaviour, same as before this fallback
existed. Verify Tiingo's current free-tier terms and rate limits yourself
before relying on them in production — API terms change and this comment will
not chase that.

WHAT IT DOES NOT COVER
----------------------
Options. Neither fallback has option chains, so the Options tab and Contract
Check still depend entirely on Yahoo. This fallback protects PRICE data —
which is what the scan, the signal, the charts and the universe all run on.
That is the majority of the calls and all of the ones that block the app from
working.

Fallback daily bars match what every caller in this repo now expects: Tiingo's
RAW columns, because all of them — app.py, scanner.py, exit_monitor.py and,
since 2026-09-02, backtest.py — fetch Yahoo with auto_adjust=False. The
backtest used to be the one exception; that was measured and closed (adjusted
bars are not reproducible, see backtest.py's AUTO_ADJUST note), so the
fallback and the primary source no longer disagree about adjustment at all.
For indicators computed over a year of data (EMA, ADX, RSI, ATR) that is
immaterial unless the ticker split inside the window — and a split would be
visible as an obvious step in the chart. Worth knowing, not worth blocking on.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta

try:
    import pandas as pd
except ImportError:
    raise SystemExit("Missing pandas. Run: pip install pandas")

try:
    import requests
except ImportError:
    requests = None   # Tiingo leg degrades to a no-op; Yahoo-only still works

logger = logging.getLogger(__name__)

TIINGO_BASE = "https://api.tiingo.com/tiingo/daily"
TIINGO_KEY_ENV = "TIINGO_API_KEY"


def to_stooq_symbol(ticker: str) -> str:
    """
    Stooq namespaces symbols by exchange: US equities need a '.us' suffix, so
    TGT becomes tgt.us. Indices use a different convention (^SPX), which we
    pass through untouched rather than guessing wrong.
    """
    t = ticker.strip().lower()
    if t.startswith("^") or "." in t:
        return t
    return f"{t}.us"


def fetch_stooq(ticker: str, period: str = "1y",
                interval: str = "1d") -> pd.DataFrame | None:
    """
    DISABLED. Stooq now serves a JavaScript proof-of-work challenge instead of
    CSV: the page computes SHA-256 hashes until one has leading zeros, then
    POSTs the answer to /__verify. Solving that from Python would mean running
    a JS engine or reimplementing a hash loop whose parameters can change
    without notice — not something to build a data path on.

    Kept as a stub rather than deleted because everything AROUND it is still
    good: fetch_daily()'s routing, the symbol mapping, and the offline tests.
    Swapping in another provider is a change to this one function.
    """
    return None


def _period_to_start(period: str) -> str:
    """
    Approximate a yfinance-style period string ("1y", "6mo", "15y", "max", …)
    into a start date for providers that want an explicit range instead of a
    period keyword. Deliberately generous — callers trim/validate row counts
    downstream, this only needs to request ENOUGH history, not exactly enough.
    """
    if not period:
        return "1990-01-01"
    p = period.strip().lower()
    if p == "max":
        return "1990-01-01"
    try:
        if p.endswith("mo"):
            days = int(p[:-2]) * 31
        elif p.endswith("y"):
            days = int(p[:-1]) * 366
        elif p.endswith("d"):
            days = int(p[:-1])
        else:
            days = 400
    except ValueError:
        days = 400
    return str(date.today() - timedelta(days=days + 10))


def fetch_tiingo(ticker: str, period: str = "1y", interval: str = "1d",
                 _get=None) -> pd.DataFrame | None:
    """
    Tiingo end-of-day daily bars. Needs TIINGO_API_KEY; returns None
    immediately (no network call) if it is unset, so an app with no key
    configured behaves exactly as it did before this function existed.

    Daily only — an intraday `interval` returns None and the caller falls
    through to the next leg, same contract as fetch_stooq() always had.

    Uses Tiingo's RAW open/high/low/close/volume, not its adjusted columns.
    Most callers in this repo (app.py, scanner.py, exit_monitor.py) fetch
    Yahoo with auto_adjust=False and expect raw bars; matching that here
    means a mid-session fallback doesn't also silently switch adjustment
    convention. backtest.py wants auto_adjust=True and documents the small
    resulting mismatch at its own call site instead.

    `_get` is injected for offline tests (default: requests.get).
    """
    if interval != "1d":
        return None
    api_key = os.environ.get(TIINGO_KEY_ENV)
    if not api_key:
        return None
    if requests is None and _get is None:
        logger.warning("Tiingo configured (TIINGO_API_KEY set) but the "
                       "'requests' package is not installed — skipping.")
        return None
    get = _get or requests.get

    url = f"{TIINGO_BASE}/{ticker.strip().lower()}/prices"
    try:
        resp = get(url, params={"startDate": _period_to_start(period),
                                "token": api_key, "format": "json"},
                   timeout=15)
        if resp.status_code != 200:
            logger.warning("Tiingo %s: HTTP %s", ticker, resp.status_code)
            return None
        rows = resp.json()
        if not rows:
            return None
        df = pd.DataFrame(rows)
        keep = {"open": "Open", "high": "High", "low": "Low",
               "close": "Close", "volume": "Volume"}
        missing = [c for c in keep if c not in df.columns]
        if missing:
            logger.warning("Tiingo %s: response missing %s", ticker, missing)
            return None
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df.set_index("date").sort_index()
        df.index.name = "Date"
        df = df.rename(columns=keep)[list(keep.values())]
        return df
    except Exception as e:
        logger.warning("Tiingo fetch failed for %s: %s", ticker, e)
        return None


def fetch_daily(ticker: str, period: str = "1y", interval: str = "1d",
                yahoo_fetch=None, tiingo_fetch=None) -> tuple[pd.DataFrame | None, str]:
    """
    Yahoo first, Tiingo if configured and Yahoo comes back empty, Stooq last
    (currently always a no-op — see fetch_stooq()).

    `yahoo_fetch(ticker, period, interval)` is injected so this module has no
    dependency on app.py/scanner.py/exit_monitor.py/backtest.py — and so the
    fallback logic can be tested without a network. `tiingo_fetch` is injected
    the same way for tests; production leaves it as fetch_tiingo. Returns
    (frame, source) where source is "yahoo", "tiingo", "stooq" or "none".
    """
    if yahoo_fetch is not None:
        try:
            df = yahoo_fetch(ticker, period, interval)
            if df is not None and not getattr(df, "empty", True):
                return df, "yahoo"
            logger.info("Yahoo returned nothing for %s — trying fallback", ticker)
        except Exception as e:
            logger.warning("Yahoo failed for %s (%s) — trying fallback", ticker, e)

    tiingo = tiingo_fetch or fetch_tiingo
    df = tiingo(ticker, period, interval)
    if df is not None and not df.empty:
        logger.info("Tiingo supplied %d bars for %s", len(df), ticker)
        return df, "tiingo"

    df = fetch_stooq(ticker, period, interval)
    if df is not None and not df.empty:
        logger.info("Stooq supplied %d bars for %s", len(df), ticker)
        return df, "stooq"

    return None, "none"


# ---------------------------------------------------------------------------
# Self-test — no network
# ---------------------------------------------------------------------------

def selftest() -> int:
    print("symbol mapping")
    for raw, want in [("TGT", "tgt.us"), ("tgt", "tgt.us"),
                      ("^SPX", "^spx"), ("BRK.B", "brk.b")]:
        got = to_stooq_symbol(raw)
        print(f"  {raw:<8} -> {got:<10} {'OK' if got == want else 'FAIL'}")
        assert got == want

    print("\nprovider stub")
    assert fetch_stooq("TGT") is None
    print("  fetch_stooq         : returns None (Stooq disabled)")

    print("\nperiod -> start-date parsing")
    for p in ("1y", "6mo", "15y", "30d", "max", ""):
        s = _period_to_start(p)
        print(f"  {p or '(empty)':<8} -> {s}")
        assert s < str(date.today())

    print("\ntiingo — no key configured")
    os.environ.pop(TIINGO_KEY_ENV, None)
    calls = []
    def _unexpected_get(*a, **kw):
        calls.append((a, kw)); raise AssertionError("should not be called")
    assert fetch_tiingo("TGT", _get=_unexpected_get) is None
    assert not calls, "no TIINGO_API_KEY must mean no network call at all"
    print("  no key              : returns None, makes NO network call")

    print("\ntiingo — key configured, injected transport")
    os.environ[TIINGO_KEY_ENV] = "test-key"
    try:
        class _Resp:
            status_code = 200
            def json(self):
                return [{"date": "2026-08-27T00:00:00.000Z", "open": 1.0,
                         "high": 1.2, "low": 0.9, "close": 1.1, "volume": 1000}]
        got_url = {}
        def _fake_get(url, params=None, timeout=None):
            got_url["url"] = url; got_url["params"] = params
            return _Resp()
        df = fetch_tiingo("TGT", _get=_fake_get)
        assert df is not None and len(df) == 1
        assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert "token" in got_url["params"] and "startDate" in got_url["params"]
        print(f"  healthy response    : {len(df)} bar(s), raw OHLCV columns, "
             f"token sent")

        class _RespBad:
            status_code = 429
            def json(self): return []
        assert fetch_tiingo("TGT", _get=lambda *a, **k: _RespBad()) is None
        print("  HTTP error          : returns None")

        assert fetch_tiingo("TGT", interval="1h", _get=_unexpected_get) is None
        print("  intraday interval   : returns None, no call (daily-only)")
    finally:
        os.environ.pop(TIINGO_KEY_ENV, None)

    print("\nrouting — yahoo -> tiingo -> stooq -> none")
    good = pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0],
                         "Close": [1.0], "Volume": [1.0]},
                        index=pd.to_datetime(["2026-08-27"]))

    df, src = fetch_daily("TGT", yahoo_fetch=lambda t, p, i: good)
    print(f"  yahoo healthy       : source={src}")
    assert src == "yahoo" and df is not None

    df, src = fetch_daily("TGT", yahoo_fetch=lambda t, p, i: pd.DataFrame(),
                          tiingo_fetch=lambda t, p, i: good)
    print(f"  yahoo empty, tiingo healthy : source={src}")
    assert src == "tiingo" and df is not None

    df, src = fetch_daily("TGT", yahoo_fetch=lambda t, p, i: pd.DataFrame(),
                          tiingo_fetch=lambda t, p, i: None)
    print(f"  yahoo empty, tiingo empty   : source={src}, frame=None "
         f"(stooq is a dead stub)")
    assert src == "none" and df is None

    def _raise(t, p, i): raise RuntimeError("rate limited")
    df, src = fetch_daily("TGT", yahoo_fetch=_raise,
                          tiingo_fetch=lambda t, p, i: None)
    print(f"  yahoo raised        : source={src}, frame=None")
    assert src == "none" and df is None

    df, src = fetch_daily("TGT", yahoo_fetch=lambda t, p, i: pd.DataFrame())
    print(f"  no tiingo_fetch injected, no key set -> source={src} "
         f"(fetch_tiingo itself returns None with no key)")
    assert src == "none" and df is None

    print("\nAll self-tests passed.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
