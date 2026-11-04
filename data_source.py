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

Fallback daily bars match what every caller in this repo expects: every one
of them (app.py, scanner.py, exit_monitor.py and, since 2026-09-02,
backtest.py) fetches Yahoo with auto_adjust=False, whose bars are SPLIT-
adjusted and not dividend-adjusted. Tiingo's raw columns are neither, and its
adj* columns are both, so fetch_tiingo() builds split-only bars from Tiingo's
own splitFactor (review 2026-11-03; owner: "fix the Tiingo split adjustment as
well").

THIS SAID THE OPPOSITE until 2026-11-03: that the raw columns matched Yahoo
"at all", and that a split would be "an obvious step in the chart, worth
knowing, not worth blocking on". It was worth blocking on. The grader's split
guard (forward_grading_preregistration.md) relies on the provider rewriting
the signal bar's close after a split: on raw bars it does not, and a 2:1
split after the fill graded as a LOSS of -19 R where Yahoo-style bars void
it; the exit monitor's THESIS read a halved close as a break of EMA20; and the
scanner's indicators spanned the step. A response without splitFactor is
refused, never passed through raw.

Tiingo's daily bars are end of day only, so on a fallback day there is no
forming bar. fetch_tiingo_quote() supplies the session's open and last price
from Tiingo's IEX endpoint instead, for the scanner's fill gate and its
price-at-send line (review 2026-11-03; owner: "fix the Tiingo end-of-day fill
gate gap as well"); with no quote either, the scanner withholds the alert
rather than send it unchecked. Still true: the post-close pass may read
yesterday's bar if Tiingo has not published today's yet.
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
TIINGO_IEX = "https://api.tiingo.com/iex"
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

    SPLIT-ADJUSTED, NOT DIVIDEND-ADJUSTED, as Yahoo's auto_adjust=False bars
    are (see the module docstring): built from the raw columns and Tiingo's
    splitFactor by split_adjust(). A response with no splitFactor is
    refused (None), so the caller falls through rather than read raw bars.

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
        if "splitFactor" not in df.columns:
            logger.warning("Tiingo %s: no splitFactor in the response — refused "
                           "(raw bars are not Yahoo's split-adjusted bars)", ticker)
            return None
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df.set_index("date").sort_index()
        df.index.name = "Date"
        factors = df["splitFactor"]
        df = df.rename(columns=keep)[list(keep.values())]
        return split_adjust(df, factors)
    except Exception as e:
        logger.warning("Tiingo fetch failed for %s: %s", ticker, e)
        return None


def fetch_tiingo_quote(ticker: str, today, _get=None) -> dict | None:
    """
    The session so far for `ticker`, from Tiingo's IEX endpoint
    (TIINGO_IEX/<ticker>), as {"open", "last", "ts"}: the stand-in for the
    forming bar Tiingo's end-of-day prices do not have. `today` is the ET
    date; a quote stamped on any other day is stale and refused. `last` is
    tngoLast (Tiingo's last price), else last; `open` is the session's open
    when the endpoint has one yet (None early in the session). One venue's
    view (IEX), close to the consolidated tape on a liquid large cap, not
    identical to it: good enough to tell a fill gapped past a level, which
    is the question it answers. None with no key, an HTTP error, an
    unreadable or stale quote, or no positive price. Field names follow
    Tiingo's IEX documentation; this sandbox cannot reach the API, so the
    parsing is tested on its documented shape.
    """
    api_key = os.environ.get(TIINGO_KEY_ENV)
    if not api_key:
        return None
    if requests is None and _get is None:
        return None
    get = _get or requests.get
    try:
        resp = get(f"{TIINGO_IEX}/{ticker.strip().lower()}", params={"token": api_key}, timeout=10)
        if resp.status_code != 200:
            logger.warning("Tiingo IEX %s: HTTP %s", ticker, resp.status_code)
            return None
        body = resp.json()
        row = body[0] if isinstance(body, list) and body else (body if isinstance(body, dict) else None)
        if not row:
            return None
        ts = pd.Timestamp(row.get("timestamp") or row.get("lastSaleTimestamp"))
        ts = (ts.tz_localize("UTC") if ts.tzinfo is None else ts).tz_convert("America/New_York")
        if ts.date() != today:
            logger.info("Tiingo IEX %s: quote is from %s, not today — refused", ticker, ts.date())
            return None

        def _pos(v):
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            return v if v == v and v > 0 else None
        last = _pos(row.get("tngoLast")) or _pos(row.get("last"))
        if last is None:
            return None
        return {"open": _pos(row.get("open")), "last": last, "ts": ts.isoformat()}
    except Exception as e:                                  # noqa: BLE001
        logger.warning("Tiingo IEX quote failed for %s: %s", ticker, e)
        return None


def split_adjust(df: pd.DataFrame, split_factor: pd.Series) -> pd.DataFrame:
    """
    Split-only adjustment, the convention of Yahoo's auto_adjust=False bars.
    `split_factor` is Tiingo's per-day splitFactor (2.0 on the first session
    after a 2:1 split, 1.0 otherwise); the raw bar on that day is already
    post-split. Each bar's prices are divided, and its volume multiplied, by
    the product of every factor dated AFTER it, so the whole frame is on the
    last bar's share basis. A missing, non-finite or non-positive factor is
    read as 1.0 (no split). Dividends are not touched.
    """
    import numpy as np
    f = pd.to_numeric(split_factor, errors="coerce").to_numpy(dtype=float)
    f = np.where(np.isfinite(f) & (f > 0), f, 1.0)
    later = np.concatenate([np.cumprod(f[::-1])[::-1][1:], [1.0]])   # product of factors after each bar
    out = df.copy()
    for c in ("Open", "High", "Low", "Close"):
        out[c] = out[c].to_numpy(dtype=float) / later
    out["Volume"] = out["Volume"].to_numpy(dtype=float) * later
    return out


def fetch_dividends(ticker: str, period: str = "2y", download=None,
                    closes: dict | None = None) -> dict | None:
    """
    {ex-date 'YYYY-MM-DD': amount per share}, from Yahoo's daily bars fetched
    WITH their actions (backtest engine fix `dividends`; grading amendment I,
    2026-11-04). Split-adjusted, the basis of the raw auto_adjust=False bars.

    THE BARS COME WITH THE DIVIDENDS ON PURPOSE. yfinance 1.x answers a
    throttled request with an EMPTY frame, and an empty dividend series
    cannot be told from a stock that pays none. A non-empty frame of bars
    with its Dividends column can: zeros there mean no dividend. So None
    (unavailable) when the frame is empty, fails, or carries no Dividends
    column; {} only when the bars came back and no ex-date is among them.
    `download(ticker, period)` is injected for tests.
    """
    try:
        if download is None:
            import yfinance as yf

            def download(t, p):
                return yf.download(t, period=p, interval="1d", actions=True,
                                   auto_adjust=False, progress=False)
        df = download(ticker, period)
    except Exception as e:                                   # noqa: BLE001
        logger.warning("dividend history for %s failed: %s", ticker, e)
        return None
    if df is None or getattr(df, "empty", True):
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    if "Dividends" not in df.columns:
        return None
    if closes is not None and "Close" in df.columns:
        # the bars' closes, so a caller holding OLDER bars can check that the
        # dividends are on its split basis (backtest.attach_dividends)
        for d, c in df["Close"].items():
            try:
                c = float(c)
            except (TypeError, ValueError):
                continue
            if c == c and c > 0:
                closes[str(pd.Timestamp(d).date())] = c
    out = {}
    for d, v in df["Dividends"].items():
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if v == v and v > 0:
            out[str(pd.Timestamp(d).date())] = v
    return out


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
    assert fetch_tiingo_quote("TGT", date.today(), _get=_unexpected_get) is None
    assert not calls, "no TIINGO_API_KEY must mean no network call at all"
    print("  no key              : returns None, makes NO network call")

    print("\ntiingo — key configured, injected transport")
    os.environ[TIINGO_KEY_ENV] = "test-key"
    try:
        class _Resp:
            status_code = 200
            def json(self):
                return [{"date": "2026-08-27T00:00:00.000Z", "open": 1.0,
                         "high": 1.2, "low": 0.9, "close": 1.1, "volume": 1000,
                         "splitFactor": 1.0}]
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

        # SPLIT-ONLY ADJUSTMENT (review 2026-11-03): a 2:1 split on 08-28 and
        # a 3:1 on 08-31. Raw closes 300, 306 | 153, 154 | 52; the frame comes
        # back on the last bar's basis, volume scaled the other way, and the
        # raw post-split bars are untouched.
        _raw = [("2026-08-26", 300.0, 100, 1.0), ("2026-08-27", 306.0, 100, 1.0),
                ("2026-08-28", 153.0, 200, 2.0), ("2026-08-31", 52.0, 600, 3.0),
                ("2026-08-25", 294.0, 100, 1.0)]                        # out of order on purpose
        class _RespSplit:
            status_code = 200
            def json(self):
                return [{"date": f"{d}T00:00:00.000Z", "open": c, "high": c * 1.01,
                         "low": c * 0.99, "close": c, "volume": v, "splitFactor": s,
                         "adjClose": -1.0, "divCash": 0.5} for d, c, v, s in _raw]
        ds = fetch_tiingo("TGT", _get=lambda *a, **k: _RespSplit())
        assert [round(x, 6) for x in ds["Close"]] == [49.0, 50.0, 51.0, 51.0, 52.0], list(ds["Close"])
        assert list(ds["Volume"]) == [600.0, 600.0, 600.0, 600.0, 600.0], list(ds["Volume"])
        assert abs(ds["High"].iloc[0] - 49.0 * 1.01) < 1e-9 and list(ds.columns) == \
            ["Open", "High", "Low", "Close", "Volume"], "every price column, and no adj/dividend column"
        class _RespNoSplit:
            status_code = 200
            def json(self):
                return [{"date": "2026-08-27T00:00:00.000Z", "open": 1.0, "high": 1.2,
                         "low": 0.9, "close": 1.1, "volume": 1000}]
        assert fetch_tiingo("TGT", _get=lambda *a, **k: _RespNoSplit()) is None, \
            "no splitFactor: refused, never raw"
        _odd = split_adjust(pd.DataFrame({"Open": [10.0, 10.0], "High": [10.0, 10.0], "Low": [10.0, 10.0],
                                          "Close": [10.0, 10.0], "Volume": [1.0, 1.0]}),
                            pd.Series([float("nan"), 0.0]))
        assert list(_odd["Close"]) == [10.0, 10.0], "a missing or zero factor is no split"
        print("  splits              : prices / and volume x the later factors (Yahoo's "
              "auto_adjust=False basis); no splitFactor -> refused")

        # THE SESSION SO FAR, from IEX (review 2026-11-03): today's quote is
        # the forming bar; yesterday's is stale; tngoLast before last.
        from datetime import date as _d
        class _Q:
            status_code = 200
            def __init__(self, ts, **kw): self._r = {"ticker": "tgt", "timestamp": ts, **kw}
            def json(self): return [self._r]
        _today = _d(2026, 11, 4)
        q = fetch_tiingo_quote("TGT", _today, _get=lambda *a, **k: _Q(
            "2026-11-04T16:07:12.000Z", open=101.0, tngoLast=102.3, last=102.25))
        assert q == {"open": 101.0, "last": 102.3, "ts": q["ts"]} and q["ts"].startswith("2026-11-04T11:07"), q
        assert fetch_tiingo_quote("TGT", _today, _get=lambda *a, **k: _Q(
            "2026-11-03T20:59:00.000Z", open=99.0, tngoLast=100.0)) is None, "yesterday's quote is stale"
        assert fetch_tiingo_quote("TGT", _today, _get=lambda *a, **k: _Q(
            "2026-11-04T14:31:00.000Z", open=None, last=101.5)) == \
            {"open": None, "last": 101.5, "ts": "2026-11-04T09:31:00-05:00"}, "no open yet; last stands in"
        assert fetch_tiingo_quote("TGT", _today, _get=lambda *a, **k: _Q(
            "2026-11-04T16:07:12.000Z", open=101.0, tngoLast=0, last=None)) is None, "no price, no quote"
        assert fetch_tiingo_quote("TGT", _today, _get=lambda *a, **k: _RespBad()) is None
        print("  IEX quote           : today's open/last as the forming bar; stale, empty or "
              "failed -> None")
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

    # ── dividends (engine fix `dividends`, grading amendment I) ──
    _ix = pd.DatetimeIndex(["2026-03-02", "2026-03-03", "2026-03-04"])
    _with = pd.DataFrame({"Close": [10.0, 10.1, 9.9], "Dividends": [0.0, 0.25, 0.0]}, index=_ix)
    assert fetch_dividends("X", download=lambda t, p: _with) == {"2026-03-03": 0.25}
    _cl: dict = {}
    fetch_dividends("X", download=lambda t, p: _with, closes=_cl)
    assert _cl == {"2026-03-02": 10.0, "2026-03-03": 10.1, "2026-03-04": 9.9}, _cl
    assert fetch_dividends("X", download=lambda t, p: _with.assign(Dividends=0.0)) == {}, \
        "bars with a zero Dividends column: a stock that paid none, known"
    assert fetch_dividends("X", download=lambda t, p: pd.DataFrame()) is None, \
        "an empty frame (a throttled answer) is UNAVAILABLE, never 'no dividends'"
    assert fetch_dividends("X", download=lambda t, p: _with.drop(columns=["Dividends"])) is None
    def _raise(t, p):
        raise RuntimeError("no network")
    assert fetch_dividends("X", download=_raise) is None
    _mi = _with.copy()
    _mi.columns = pd.MultiIndex.from_tuples([("Close", "X"), ("Dividends", "X")])
    assert fetch_dividends("X", download=lambda t, p: _mi) == {"2026-03-03": 0.25}
    print("dividends: bars with their actions; an empty answer is unavailable, a zero column is none")

    print("\nAll self-tests passed.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
