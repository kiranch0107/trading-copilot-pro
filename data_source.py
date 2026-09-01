#!/usr/bin/env python3
"""
data_source.py — Yahoo first, Stooq as a fallback

WHY THIS EXISTS
---------------
yfinance scrapes unofficial Yahoo endpoints. When Yahoo throttles it does not
raise — it returns an empty frame — which produced four separate bugs in this
codebase, each surfacing as a confident but wrong message ("check the symbol",
"no option chain", "not a listed expiry"). Retries help, but they only make a
throttled app slower; they cannot make it work.

Stooq is a second, independent source of daily OHLCV. No API key, no account,
decades of history. When Yahoo comes back empty after its retries, we ask
Stooq instead of failing.

WHAT IT DOES NOT COVER
----------------------
Options. Stooq has no option chains, so the Options tab and Contract Check
still depend entirely on Yahoo. This fallback protects PRICE data — which is
what the scan, the signal, the charts and the universe all run on. That is the
majority of the calls and all of the ones that block the app from working.

Stooq daily bars are also NOT split/dividend adjusted the way Yahoo's are.
For indicators computed over a year of data (EMA, ADX, RSI, ATR) that is
immaterial unless the ticker split inside the window — and a split would be
visible as an obvious step in the chart. Worth knowing, not worth blocking on.
"""

from __future__ import annotations

import logging

try:
    import pandas as pd
except ImportError:
    raise SystemExit("Missing pandas. Run: pip install pandas")

logger = logging.getLogger(__name__)

# NOTE: a period -> calendar-days map used to live here, for trimming a
# provider's full-history response to the window requested. It went with the
# Stooq parser. Any replacement provider that returns everything it has will
# need it back — most keyed APIs accept a date range directly instead.


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


def fetch_daily(ticker: str, period: str = "1y", interval: str = "1d",
                yahoo_fetch=None) -> tuple[pd.DataFrame | None, str]:
    """
    Yahoo first, Stooq if Yahoo comes back empty.

    `yahoo_fetch(ticker, period, interval)` is injected so this module has no
    dependency on app.py — and so the fallback logic can be tested without a
    network. Returns (frame, source) where source is "yahoo", "stooq" or
    "none".
    """
    if yahoo_fetch is not None:
        try:
            df = yahoo_fetch(ticker, period, interval)
            if df is not None and not getattr(df, "empty", True):
                return df, "yahoo"
            logger.info("Yahoo returned nothing for %s — trying fallback", ticker)
        except Exception as e:
            logger.warning("Yahoo failed for %s (%s) — trying fallback", ticker, e)

    df = fetch_stooq(ticker, period, interval)
    if df is not None and not df.empty:
        logger.info("Fallback provider supplied %d bars for %s", len(df), ticker)
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

    print("\nrouting — the part that still matters")
    good = pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0],
                         "Close": [1.0], "Volume": [1.0]},
                        index=pd.to_datetime(["2026-08-27"]))

    df, src = fetch_daily("TGT", yahoo_fetch=lambda t, p, i: good)
    print(f"  yahoo healthy       : source={src}")
    assert src == "yahoo" and df is not None

    df, src = fetch_daily("TGT", yahoo_fetch=lambda t, p, i: pd.DataFrame())
    print(f"  yahoo empty         : source={src}, frame=None")
    assert src == "none" and df is None

    def _raise(t, p, i): raise RuntimeError("rate limited")
    df, src = fetch_daily("TGT", yahoo_fetch=_raise)
    print(f"  yahoo raised        : source={src}, frame=None")
    assert src == "none" and df is None

    print("\n  With no second provider, fetch_daily degrades to Yahoo-only —")
    print("  which is the pre-fallback behaviour, so nothing regressed. When a")
    print("  replacement provider arrives, only fetch_stooq() changes and these")
    print("  routing tests start asserting a real fallback again.")

    print("\nAll self-tests passed.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
