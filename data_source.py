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

import io
import logging

try:
    import pandas as pd
except ImportError:
    raise SystemExit("Missing pandas. Run: pip install pandas")

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger(__name__)

STOOQ_URL = "https://stooq.com/q/d/l/"
STOOQ_TIMEOUT = 10

# Yahoo period strings -> approximate calendar days, for trimming Stooq's
# full-history response down to what was actually asked for.
_PERIOD_DAYS = {
    "1mo": 31, "3mo": 92, "6mo": 183, "1y": 366,
    "2y": 731, "5y": 1827, "10y": 3653, "max": 100_000,
}


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
    Daily or weekly OHLCV from Stooq, shaped exactly like a yfinance frame:
    columns Open/High/Low/Close/Volume, DatetimeIndex ascending, tz-naive.

    Returns None on any failure. A fallback that raises is worse than no
    fallback, because it turns a recoverable outage into a crash.
    """
    if requests is None:
        logger.warning("requests not installed — Stooq fallback unavailable")
        return None

    freq = {"1d": "d", "1wk": "w", "1mo": "m"}.get(interval)
    if freq is None:
        # Intraday intervals are not available on this endpoint. Say so rather
        # than silently returning daily bars where minutes were requested.
        logger.info("Stooq has no %s bars — fallback skipped", interval)
        return None

    sym = to_stooq_symbol(ticker)
    try:
        r = requests.get(STOOQ_URL, params={"s": sym, "i": freq},
                         timeout=STOOQ_TIMEOUT)
        if r.status_code != 200 or not r.text:
            logger.warning("Stooq HTTP %s for %s", r.status_code, sym)
            return None
        # Stooq answers an unknown symbol with a plain-text body rather than a
        # 404, so check the payload actually looks like the CSV we expect.
        head = r.text[:200].lower()
        if "date" not in head or "open" not in head:
            logger.warning("Stooq returned no data for %s (%s)",
                           sym, r.text[:60].strip())
            return None

        df = pd.read_csv(io.StringIO(r.text))
    except Exception as e:
        logger.warning("Stooq fetch failed for %s (%s)", sym, e)
        return None

    if df is None or df.empty or "Date" not in df.columns:
        return None

    try:
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()      # Stooq can return newest-first
        keep = [c for c in ("Open", "High", "Low", "Close", "Volume")
                if c in df.columns]
        df = df[keep].astype(float)
        if "Volume" not in df.columns:
            # Some Stooq series omit volume. The volume filter would then
            # reject everything, so fill rather than let it fail silently.
            df["Volume"] = 0.0
        df = df.dropna(subset=["Close"])
        if df.empty:
            return None

        days = _PERIOD_DAYS.get(period, 366)
        if days < 100_000:
            cutoff = df.index.max() - pd.Timedelta(days=days)
            df = df[df.index >= cutoff]
        return df if not df.empty else None
    except Exception as e:
        logger.warning("Stooq parse failed for %s (%s)", sym, e)
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
            logger.info("Yahoo empty for %s — trying Stooq", ticker)
        except Exception as e:
            logger.warning("Yahoo failed for %s (%s) — trying Stooq", ticker, e)

    df = fetch_stooq(ticker, period, interval)
    if df is not None and not df.empty:
        logger.info("Stooq supplied %d bars for %s", len(df), ticker)
        return df, "stooq"
    return None, "none"


# ---------------------------------------------------------------------------
# Self-test — no network
# ---------------------------------------------------------------------------

_SAMPLE_CSV = """Date,Open,High,Low,Close,Volume
2026-08-27,168.10,170.40,167.80,169.90,4210000
2026-08-26,166.50,168.60,166.10,168.20,3980000
2026-08-25,165.20,166.90,164.70,166.40,4550000
"""


def selftest() -> int:
    import types, sys

    print("symbol mapping")
    for raw, want in [("TGT", "tgt.us"), ("tgt", "tgt.us"),
                      ("^SPX", "^spx"), ("BRK.B", "brk.b")]:
        got = to_stooq_symbol(raw)
        print(f"  {raw:<8} -> {got:<10} {'OK' if got == want else 'FAIL'}")
        assert got == want

    # stub requests so the parser can be exercised offline
    class _Resp:
        def __init__(self, text, code=200):
            self.text, self.status_code = text, code

    global requests
    real = requests
    requests = types.SimpleNamespace(
        get=lambda url, params=None, timeout=None: _Resp(_SAMPLE_CSV))
    try:
        df = fetch_stooq("TGT")
        print(f"\nparsed frame          : {len(df)} rows, cols {list(df.columns)}")
        assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert df.index.is_monotonic_increasing, "must be oldest-first"
        print(f"  index ascending     : {df.index[0].date()} -> {df.index[-1].date()}")
        assert float(df['Close'].iloc[-1]) == 169.90

        # unknown symbol: Stooq answers 200 with a text body, not a 404
        requests = types.SimpleNamespace(
            get=lambda url, params=None, timeout=None: _Resp("No data"))
        assert fetch_stooq("NOPE") is None
        print("  unknown symbol      : returns None, not a crash")

        requests = types.SimpleNamespace(
            get=lambda url, params=None, timeout=None: _Resp("", 429))
        assert fetch_stooq("TGT") is None
        print("  HTTP 429            : returns None")

        def _boom(*a, **k): raise ConnectionError("network down")
        requests = types.SimpleNamespace(get=_boom)
        assert fetch_stooq("TGT") is None
        print("  network exception   : swallowed, returns None")

        # intraday is not offered here
        requests = types.SimpleNamespace(
            get=lambda url, params=None, timeout=None: _Resp(_SAMPLE_CSV))
        assert fetch_stooq("TGT", interval="5m") is None
        print("  intraday interval   : skipped rather than faked")

        # ── the routing itself ──
        print("\nrouting")
        good = pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0],
                             "Close": [1.0], "Volume": [1.0]},
                            index=pd.to_datetime(["2026-08-27"]))
        df, src = fetch_daily("TGT", yahoo_fetch=lambda t, p, i: good)
        print(f"  yahoo healthy       : source={src}")
        assert src == "yahoo"

        df, src = fetch_daily("TGT", yahoo_fetch=lambda t, p, i: pd.DataFrame())
        print(f"  yahoo empty         : source={src}  <- the throttle case")
        assert src == "stooq" and len(df) == 3

        def _raise(t, p, i): raise RuntimeError("rate limited")
        df, src = fetch_daily("TGT", yahoo_fetch=_raise)
        print(f"  yahoo raised        : source={src}")
        assert src == "stooq"

        requests = types.SimpleNamespace(
            get=lambda url, params=None, timeout=None: _Resp("No data"))
        df, src = fetch_daily("TGT", yahoo_fetch=lambda t, p, i: pd.DataFrame())
        print(f"  both unavailable    : source={src}, frame={df}")
        assert src == "none" and df is None
    finally:
        requests = real

    print("\nAll self-tests passed.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
