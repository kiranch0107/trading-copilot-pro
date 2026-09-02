#!/usr/bin/env python3
"""
bar_cache.py — on-disk bars for the research harnesses, so a backtest is
reproducible

THE MEASUREMENT THIS FILE EXISTS TO RESCUE
-------------------------------------------
On 2026-09-02 two runs of an IDENTICAL backtest command, four minutes apart,
returned different answers:

    10:27   591 trades   -0.018 R
    10:31   584 trades   -0.032 R

Same config, same code, same day. Yahoo simply returned different history the
second time — MU lost 6 trades, ORCL lost 1. Nothing in the output said so.

That 7-trade / 0.014 R gap is the noise floor of the data feed, and it is
LARGER than most effects worth testing. The weekly-filter A/B that prompted
all this was trying to resolve a 1-trade difference. It never could have.

So the problem is not "which provider" — it is that a research harness must
read the SAME BARS every time or it is not measuring anything. That is what
this module provides, and it costs nothing and needs no API key.

SEMANTICS: STALE ON PURPOSE
----------------------------
A cache entry NEVER expires. That is the opposite of what a cache normally
does, and it is the entire point: reproducibility means today's re-run reads
the bars the original run read. Freshness is a deliberate act —
--refresh-cache — not something that happens to you between two runs of the
same command.

The age is reported on every run so "these bars are three weeks old" is a
thing you know, not a thing you discover.

THE FINGERPRINT IS THE POINT
-----------------------------
Every entry is hashed over its canonical bytes, and the run prints one
fingerprint over all of them. Two runs with the same fingerprint read
byte-identical inputs, so any difference in their results came from the code.
Two runs with different fingerprints are NOT COMPARABLE, and now you can see
that before you draw a conclusion instead of after.

This is deliberately not a general-purpose cache. It does not do TTLs,
eviction, or partial-window merging: a window is fetched whole and stored
whole, because a cache clever enough to stitch together two windows is a
cache clever enough to silently hand you a frame that never existed.

NOT FOR LIVE
-------------
Live scans must not read this. A stale price in a live alert is worse than a
slow one, and the whole value here — frozen bars — is exactly wrong for a
signal that fires on today's close. backtest.py, oos_validate.py and the
universe/option harnesses use it; app.py, scanner.py and exit_monitor.py
deliberately do not.

Run
---
    python bar_cache.py --selftest      # no network
    python bar_cache.py --info          # what is cached, how old, what hash
    python bar_cache.py --clear         # drop everything
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    raise SystemExit("Missing pandas. Run: pip install pandas")

CACHE_DIR = Path(__file__).with_name(".bar_cache")
MANIFEST = CACHE_DIR / "manifest.json"

# Columns a cached frame must carry. Storing anything else invites a caller to
# depend on a column that the fallback provider does not supply, which would
# make the cache a source of divergence rather than a cure for it.
COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume"]


def _key(ticker: str, interval: str, years: int) -> str:
    return f"{ticker.strip().upper()}_{interval}_{years}y"


def _path(key: str) -> Path:
    return CACHE_DIR / f"{key}.csv"


def frame_hash(df: "pd.DataFrame") -> str:
    """
    Content hash over the canonical CSV bytes.

    Canonical means: the COLUMNS above in that order, dates as ISO days, floats
    at 6dp. Hashing the in-memory frame instead would make the digest depend on
    dtype and float repr, so a pandas upgrade would "change the data" without a
    single bar moving.
    """
    d = df.copy()
    for c in COLUMNS:
        if c not in d.columns:
            d[c] = pd.NA
    d = d[COLUMNS]
    if "Date" in d.columns:
        d["Date"] = pd.to_datetime(d["Date"]).dt.strftime("%Y-%m-%d")
    # Cast the numeric columns explicitly. Without this the digest is
    # DTYPE-sensitive: a Volume column of constant 1000000 writes as "1000000"
    # and reads back as int64, whose %.6f rendering differs from the float64
    # original — so storing and loading the same bars produced two different
    # hashes and the cache could never register a hit.
    for c in COLUMNS[1:]:
        d[c] = pd.to_numeric(d[c], errors="coerce").astype("float64")
    body = d.to_csv(index=False, float_format="%.6f")
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def _load_manifest() -> dict:
    if not MANIFEST.exists():
        return {}
    try:
        return json.loads(MANIFEST.read_text())
    except Exception:
        # A corrupt manifest must not take the run down — the CSVs are the
        # data, the manifest is bookkeeping. Rebuilding it costs one refetch.
        return {}


def _save_manifest(m: dict) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")


def load(ticker: str, interval: str, years: int) -> tuple["pd.DataFrame | None", dict | None]:
    """Cached bars for this exact (ticker, interval, years), or (None, None)."""
    k = _key(ticker, interval, years)
    p = _path(k)
    if not p.exists():
        return None, None
    meta = _load_manifest().get(k)
    try:
        # float_precision="round_trip" is not optional. pandas' default CSV
        # float parser is fast, not correctly-rounded: writing 20 significant
        # digits and reading them back still moved 247 of 800 values by one
        # ULP. The loss was in the READ, so no write format could fix it —
        # %.15g through %.20g all failed until this flag was set. Without it
        # the cache quietly hands back bars that are not the ones it stored,
        # which is the drift this module exists to eliminate, reintroduced at
        # a smaller scale.
        df = pd.read_csv(p, parse_dates=["Date"], float_precision="round_trip")
    except Exception:
        return None, None
    if df.empty:
        return None, None
    # Pin dtypes rather than accept read_csv's inference, so a cached frame is
    # indistinguishable from a freshly fetched one to every caller downstream.
    for c in COLUMNS[1:]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    # A frame whose bytes no longer match the recorded hash has been edited or
    # truncated on disk. Refetching is the only safe answer: silently trusting
    # it would reintroduce exactly the invisible-data-drift this file exists
    # to stop.
    if meta and meta.get("hash") and frame_hash(df) != meta["hash"]:
        return None, None
    return df, meta


def store(ticker: str, interval: str, years: int, df: "pd.DataFrame",
          source: str = "unknown") -> dict:
    """Freeze a frame and record what it is. Returns the manifest entry."""
    CACHE_DIR.mkdir(exist_ok=True)
    k = _key(ticker, interval, years)
    keep = [c for c in COLUMNS if c in df.columns]
    out = df[keep].copy()
    # %.17g is the shortest format that round-trips a float64 EXACTLY. With
    # pandas' default formatting, 41 of 120 values came back differing in the
    # last ULP — they printed identically and compared unequal. That would
    # mean the run that populated the cache used marginally different floats
    # from every run after it, which is precisely the non-reproducibility this
    # module exists to remove.
    out.to_csv(_path(k), index=False, float_format="%.17g")
    dates = pd.to_datetime(out["Date"]) if "Date" in out.columns else None
    meta = {
        "ticker": ticker.strip().upper(),
        "interval": interval,
        "years": years,
        "rows": int(len(out)),
        "first": str(dates.min().date()) if dates is not None and len(out) else None,
        "last": str(dates.max().date()) if dates is not None and len(out) else None,
        "hash": frame_hash(out),
        "source": source,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    m = _load_manifest()
    m[k] = meta
    _save_manifest(m)
    return meta


def get_or_fetch(ticker: str, interval: str, years: int, fetch,
                 refresh: bool = False,
                 enabled: bool = True) -> tuple["pd.DataFrame | None", dict | None, str]:
    """
    The one entry point callers need.

    `fetch()` takes no arguments and returns a frame or None — the caller
    closes over whatever provider routing it already has, so this module never
    learns about yfinance, Tiingo or anything else.

    Returns (frame, meta, status) where status is one of:
        hit      — served from disk, no network call
        stored   — fetched and frozen
        refresh  — refetched on request, replacing what was there
        bypass   — caching disabled, straight passthrough
        miss     — fetch returned nothing; nothing cached
    """
    if not enabled:
        df = fetch()
        return (df, None, "bypass") if df is not None else (None, None, "miss")

    if not refresh:
        df, meta = load(ticker, interval, years)
        if df is not None:
            return df, meta, "hit"

    # On a refresh, capture what the old copy held so the change can be
    # characterised rather than merely detected.
    previous = None
    if refresh:
        previous, _ = load(ticker, interval, years)

    df = fetch()
    if df is None or getattr(df, "empty", True):
        return None, None, "miss"
    meta = store(ticker, interval, years, df, source="fetch")
    if previous is not None:
        meta = dict(meta, refresh_diff=diff_rows(previous, df))
    # Return what is ON DISK, not the frame we just fetched. The run that
    # populates the cache must see exactly what every later run will see —
    # otherwise the first run of an A/B is silently the odd one out, which is
    # the same class of bug as the data drift this module was built to stop.
    reread, meta2 = load(ticker, interval, years)
    if reread is not None:
        # meta2 comes from the manifest, which does not carry refresh_diff
        # (it describes a transition, not the stored frame). Merge it back on
        # or the caller silently loses the one thing that explains the change.
        out_meta = dict(meta2 or meta)
        if "refresh_diff" in meta:
            out_meta["refresh_diff"] = meta["refresh_diff"]
        return reread, out_meta, ("refresh" if refresh else "stored")
    return df, meta, ("refresh" if refresh else "stored")


def diff_rows(old_df: "pd.DataFrame", new_df: "pd.DataFrame") -> dict:
    """
    What actually changed between two fetches of the same series.

    The distinction this exists to draw: ONE changed row dated today is a bar
    that is still forming. MANY changed rows spread across history is the
    provider re-adjusting the whole series (a dividend or split re-applied
    under auto_adjust). Those are different problems with different fixes,
    and a bare "the hash changed" cannot tell them apart — which is exactly
    the position this project was in when every one of 13 series changed
    between two fetches four minutes apart.
    """
    out = {"rows_before": len(old_df), "rows_after": len(new_df),
           "changed": 0, "first_changed": None, "last_changed": None}
    if "Date" not in old_df.columns or "Date" not in new_df.columns:
        return out
    o = old_df.set_index(pd.to_datetime(old_df["Date"]))
    n = new_df.set_index(pd.to_datetime(new_df["Date"]))
    shared = o.index.intersection(n.index)
    if len(shared) == 0:
        return out
    cols = [c for c in COLUMNS[1:] if c in o.columns and c in n.columns]
    ne = (o.loc[shared, cols].to_numpy() != n.loc[shared, cols].to_numpy()).any(axis=1)
    changed_idx = shared[ne]
    out["changed"] = int(len(changed_idx))
    if len(changed_idx):
        out["first_changed"] = str(min(changed_idx).date())
        out["last_changed"] = str(max(changed_idx).date())
    return out


def fingerprint(metas: "list[dict]") -> str:
    """
    One digest over every entry a run read. Two runs sharing this value read
    byte-identical inputs; two runs that differ are not comparable, whatever
    their aggregate numbers say.
    """
    parts = sorted(f"{m['ticker']}|{m['interval']}|{m['years']}|{m['hash']}"
                   for m in metas if m and m.get("hash"))
    if not parts:
        return "none"
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def age_days(meta: dict | None) -> int | None:
    if not meta or not meta.get("fetched_at"):
        return None
    try:
        t = datetime.strptime(meta["fetched_at"], "%Y-%m-%dT%H:%M:%SZ")
        return (datetime.utcnow() - t).days
    except Exception:
        return None


def summarise(metas: "list[dict]", statuses: "list[str]") -> str:
    """The one line a run prints so its inputs are never a mystery."""
    live = [m for m in metas if m]
    if not live:
        return "data: uncached (no fingerprint — runs are NOT comparable)"
    ages = [a for a in (age_days(m) for m in live) if a is not None]
    hits = statuses.count("hit")
    fresh = len(statuses) - hits
    oldest = max(ages) if ages else 0
    return (f"data: {fingerprint(live)}  ({len(live)} series, {hits} cached / "
            f"{fresh} fetched, oldest {oldest}d)")


def info() -> int:
    m = _load_manifest()
    if not m:
        print(f"cache empty ({CACHE_DIR})")
        return 0
    print(f"{CACHE_DIR}  —  {len(m)} entries")
    print(f"{'Key':<26} {'Rows':>6} {'First':<12} {'Last':<12} "
          f"{'Hash':<18} {'Age':>5}")
    print("-" * 88)
    for k in sorted(m):
        e = m[k]
        a = age_days(e)
        print(f"{k:<26} {e.get('rows', 0):>6} {str(e.get('first')):<12} "
              f"{str(e.get('last')):<12} {str(e.get('hash')):<18} "
              f"{('%dd' % a) if a is not None else '?':>5}")
    print(f"\nfingerprint over all: {fingerprint(list(m.values()))}")
    return 0


def clear() -> int:
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
        print(f"removed {CACHE_DIR}")
    else:
        print("nothing to remove")
    return 0


# ---------------------------------------------------------------------------
# Self-test — no network, and does not touch the real cache directory
# ---------------------------------------------------------------------------

def selftest() -> int:
    global CACHE_DIR, MANIFEST
    import tempfile

    real_dir, real_manifest = CACHE_DIR, MANIFEST
    tmp = Path(tempfile.mkdtemp(prefix="barcache_test_"))
    CACHE_DIR, MANIFEST = tmp, tmp / "manifest.json"
    try:
        def frame(n: int, start: str = "2020-01-01", bump: float = 0.0):
            """
            Values chosen to DISCRIMINATE, not to look tidy.

            An earlier version used 100.0 + i — exact integers, which
            round-trip through any float format — so the fidelity assertion
            below passed even with a lossy write and tested nothing. The
            multiplications here produce values like 101.2546218487395 that
            only %.17g preserves. Volume is int64 on purpose: read_csv infers
            int64 back from a constant column, so this also pins the dtype
            handling.
            """
            idx = pd.bdate_range(start, periods=n)
            base = [100.0 + i * (1 / 3) + bump for i in range(n)]
            return pd.DataFrame({
                "Date": idx,
                "Open": base,
                "High": [x * 1.0101010101 for x in base],
                "Low": [x * 0.9899989999 for x in base],
                "Close": [x * 1.0000000001 for x in base],
                "Volume": [1_000_000] * n,
            })

        # ── the hash is about CONTENT, not object identity ──
        a, b = frame(50), frame(50)
        assert frame_hash(a) == frame_hash(b), "same bars must hash the same"
        assert frame_hash(a) != frame_hash(frame(50, bump=0.01)), \
            "different bars must hash differently"
        assert frame_hash(a) != frame_hash(frame(49)), (
            "a truncated frame must hash differently — this is the exact "
            "failure mode that went undetected (MU lost 6 trades silently)")
        print(f"content hash        : stable, and moves on any bar change")

        # ── miss, then hit, with NO second fetch ──
        calls = []
        def fetch_ok():
            calls.append(1)
            return frame(50)

        df, meta, st1 = get_or_fetch("AAA", "1d", 5, fetch_ok)
        assert df is not None and st1 == "stored" and len(calls) == 1
        df2, meta2, st2 = get_or_fetch("AAA", "1d", 5, fetch_ok)
        assert st2 == "hit", f"second call must be served from disk, got {st2}"
        assert len(calls) == 1, "a cache hit must make NO network call"
        assert meta2["hash"] == meta["hash"]
        print(f"miss -> store -> hit: 1 fetch for 2 reads, hash {meta['hash']}")

        # THE WHOLE POINT: identical reads across runs — and the run that
        # POPULATED the cache must be identical to them too. Returning the
        # freshly-fetched frame there instead of the stored one left 41 of 120
        # values differing in the last ULP: equal to the eye, unequal to the
        # comparison that decides a stop fill.
        assert frame_hash(df) == frame_hash(df2), \
            "two reads of one entry must return byte-identical bars"
        assert df.equals(df2), \
            "the storing run must get back exactly what later runs read, " \
            "float-for-float — not merely something that prints the same"
        print(f"reproducibility     : storing run == later runs, exactly")

        # FIDELITY, which is a different claim from the one above: what lands
        # on disk must equal what the provider actually returned, not merely
        # be self-consistent. Written with pandas' default float formatting,
        # 41 of 120 values came back one ULP off — self-consistent, but no
        # longer the bars the feed served. Only %.17g round-trips float64
        # exactly. The assertion above cannot catch this (it compares disk to
        # disk), so this one compares disk to the ORIGINAL frame.
        original = frame(50)
        # Guard the fixture itself: if these values survive a lossy write, the
        # assertion below proves nothing. This is the check the ADX fixture
        # and the weekly-lookahead fixture both needed and did not have.
        import io as _io
        lossy = pd.read_csv(_io.StringIO(original.to_csv(index=False)),
                            parse_dates=["Date"])
        assert not (lossy["High"].values == original["High"].values).all(), \
            "fixture does not discriminate: these values round-trip even " \
            "with a lossy write, so the fidelity test would pass either way"

        stored_meta = store("FID", "1d", 5, original)
        back, _ = load("FID", "1d", 5)
        assert back is not None
        cols = [c for c in COLUMNS if c != "Date"]
        assert (back[cols].values == original[cols].values.astype("float64")).all(), \
            "cached bars must equal the fetched bars float-for-float — a " \
            "lossy write means the cache is not serving what the feed sent"
        assert str(back["Volume"].dtype) == "float64", \
            "dtypes must be pinned on load — an int64 Volume inferred from a " \
            "constant column would make cached frames differ from fetched ones"
        assert stored_meta["hash"] == frame_hash(back)
        print(f"fidelity            : disk bars == fetched bars, bit for bit")

        # ── keys do not collide across interval or window ──
        _, m_wk, s_wk = get_or_fetch("AAA", "1wk", 5, fetch_ok)
        assert s_wk == "stored", "interval must be part of the key"
        _, m_15, s_15 = get_or_fetch("AAA", "1d", 15, fetch_ok)
        assert s_15 == "stored", "years must be part of the key"
        print(f"key separation      : ticker+interval+years, no collisions")

        # ── refresh is deliberate, and replaces ──
        def fetch_changed():
            calls.append(1)
            return frame(60)
        df3, meta3, st3 = get_or_fetch("AAA", "1d", 5, fetch_changed, refresh=True)
        assert st3 == "refresh" and meta3["rows"] == 60
        assert meta3["hash"] != meta["hash"]
        df4, _, st4 = get_or_fetch("AAA", "1d", 5, fetch_ok)
        assert st4 == "hit" and len(df4) == 60, \
            "after a refresh the NEW bars must be what is served"
        print(f"refresh             : explicit only, replaces the entry")

        # WHAT changed, not just THAT it changed. One altered row dated today
        # is a bar still forming; forty scattered through history is the
        # provider re-adjusting the series. The fix differs, so the report
        # must distinguish them.
        d = meta3.get("refresh_diff")
        assert d is not None, "a refresh must characterise what it replaced"
        assert d["rows_before"] == 50 and d["rows_after"] == 60
        assert d["changed"] == 0, \
            f"the shared rows were untouched here, got {d}"
        print(f"refresh diff (grew) : {d['rows_before']}->{d['rows_after']} rows, "
              f"{d['changed']} shared rows altered")

        # Now the case that matters: same length, one row rewritten — the
        # signature of a live final bar.
        base = frame(40)
        get_or_fetch("TAIL", "1d", 5, lambda: base)
        edited = base.copy()
        edited.loc[edited.index[-1], "Close"] = edited["Close"].iloc[-1] * 1.03
        _, m_tail, _ = get_or_fetch("TAIL", "1d", 5, lambda: edited, refresh=True)
        dt = m_tail["refresh_diff"]
        assert dt["changed"] == 1, f"exactly one row moved, got {dt}"
        assert dt["first_changed"] == dt["last_changed"], \
            "a single changed row must report the same first and last date"
        print(f"refresh diff (tail) : 1 row altered on {dt['last_changed']} "
              f"— the live-bar signature")

        # ...versus a wholesale re-adjustment, which must look different.
        readjusted = base.copy()
        for c in ("Open", "High", "Low", "Close"):
            readjusted[c] = readjusted[c] * 0.997
        _, m_adj, _ = get_or_fetch("TAIL", "1d", 5, lambda: readjusted, refresh=True)
        da = m_adj["refresh_diff"]
        assert da["changed"] == 40, f"every row moved, got {da}"
        assert da["first_changed"] != da["last_changed"], \
            "a re-adjustment spans history and must not look like a tail edit"
        print(f"refresh diff (adj)  : {da['changed']} rows altered from "
              f"{da['first_changed']} — the re-adjustment signature")

        # ── a tampered file is refetched, never trusted ──
        # The edit is asserted to have actually changed the bytes. An earlier
        # version did a string replace on "100.000000" when the file holds
        # "100.0", so it modified nothing and the test passed without ever
        # exercising the check — the same shape of dead test as the ADX
        # fixture that measured 26.3 while claiming to be low.
        p = _path(_key("AAA", "1d", 5))
        before_txt = p.read_text()
        lines = before_txt.rstrip("\n").split("\n")
        lines.append(lines[-1])          # a duplicated bar: valid CSV, wrong data
        p.write_text("\n".join(lines) + "\n")
        assert p.read_text() != before_txt, "the tamper must actually alter the file"
        assert frame_hash(pd.read_csv(p, parse_dates=["Date"])) != meta3["hash"], \
            "the tampered file must hash differently, or this tests nothing"
        df5, meta5 = load("AAA", "1d", 5)
        assert df5 is None, \
            "bars whose bytes no longer match the manifest hash must be " \
            "rejected — trusting them reintroduces invisible data drift"
        print(f"tamper detection    : edited file is rejected, not served")

        # ── bypass makes no promises and stores nothing ──
        before = len(_load_manifest())
        df6, meta6, st6 = get_or_fetch("BBB", "1d", 5, fetch_ok, enabled=False)
        assert st6 == "bypass" and meta6 is None
        assert len(_load_manifest()) == before, "bypass must not write"
        print(f"bypass              : passthrough, writes nothing")

        # ── a fetch that returns nothing caches nothing ──
        df7, meta7, st7 = get_or_fetch("CCC", "1d", 5, lambda: None)
        assert df7 is None and st7 == "miss" and meta7 is None
        assert not _path(_key("CCC", "1d", 5)).exists(), \
            "a failed fetch must not leave an empty entry behind"
        print(f"failed fetch        : cached as nothing, not as empty bars")

        # ── the fingerprint is what makes two runs comparable ──
        m = _load_manifest()
        f1 = fingerprint(list(m.values()))
        assert f1 == fingerprint(list(m.values())), "fingerprint must be stable"
        assert fingerprint([]) == "none"
        mutated = [dict(e) for e in m.values()]
        mutated[0]["hash"] = "deadbeefdeadbeef"
        assert fingerprint(mutated) != f1, \
            "one changed series must change the run fingerprint — otherwise " \
            "two incomparable runs look comparable"
        print(f"run fingerprint     : {f1}, moves if ANY series moves")

        line = summarise(list(m.values()), ["hit"] * len(m))
        assert f1 in line
        print(f"summary line        : {line}")

        print("\nAll self-tests passed.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        CACHE_DIR, MANIFEST = real_dir, real_manifest


def main() -> int:
    p = argparse.ArgumentParser(description="On-disk bars for reproducible backtests")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--info", action="store_true")
    p.add_argument("--clear", action="store_true")
    a = p.parse_args()
    if a.selftest:
        return selftest()
    if a.clear:
        return clear()
    return info()


if __name__ == "__main__":
    sys.exit(main())
