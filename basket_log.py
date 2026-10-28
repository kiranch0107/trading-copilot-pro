#!/usr/bin/env python3
"""
basket_log.py — the RS basket as a holding strategy, recorded forward
(results/rs_basket_preregistration.md, BACKLOG 34 B; owner 2026-10-28).

THE QUESTION. The weekly universe snapshot picks eight names by relative
strength against SPY. Does holding those eight, equal-weight, for the week
they are selected beat holding SPY? The logic review's decision 3 ("does
the selection lose to the index") is the question that matters for a $5,000
account, and the system already makes every number it needs.

WHAT IS RECORDED. One `basket` row per snapshot: the snapshot date, the eight
names, each name's close on the ANCHOR session (the last settled session on
or before the snapshot date) and SPY's close on the same session. No signal,
no geometry, no stops.

WHAT IS GRADED. HORIZON_SESSIONS exchange sessions after the anchor, from
closes the scanner fetches anyway: the equal-weight basket's simple return
minus SPY's. A window is graded only when the session exactly HORIZON_SESSIONS
after the anchor is present (the scanner injects its exchange calendar); a
missing session is HELD, never shifted, and voided only once the row is
NO_BARS_DAYS old. A name whose anchor close no longer matches the recorded
one beyond SPLIT_TOL is a split: void.

ONE WRITER. scanner.py, in the post-close pass, like iv_snapshots.jsonl. The
file is hash-chained like the forward log, append-only, committed by
scanner.yml, and this module stays stdlib so the chain can be read anywhere.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

LOG = Path("basket_log.jsonl")
GENESIS = "0" * 32
HORIZON_SESSIONS = 5        # one trading week after the anchor session
NO_BARS_DAYS = 45           # a window still missing its end this long after the anchor -> void
MAX_SESSION_GAP_DAYS = 4    # stdlib fallback when no calendar is injected
SPLIT_TOL = 0.02            # recorded vs fetched anchor close beyond this -> void:split
BENCH = "SPY"


# ---------------------------------------------------------------------------
# Chain
# ---------------------------------------------------------------------------

def _canon(body: dict) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


def _digest(prev: str, body: dict) -> str:
    return hashlib.sha256((prev + _canon(body)).encode("utf-8")).hexdigest()[:32]


def read_all(path: Path | None = None) -> list:
    p = path or LOG
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def append(kind: str, body: dict, path: Path | None = None,
           now: datetime | None = None) -> dict:
    p = path or LOG
    rows = read_all(p)
    prev = rows[-1]["hash"] if rows else GENESIS
    body = dict(body)
    body["seq"] = len(rows) + 1
    body["kind"] = kind
    body["ts"] = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) \
        .isoformat(timespec="seconds")
    body["prev"] = prev
    rec = dict(body)
    rec["hash"] = _digest(prev, body)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")
    return rec


def verify(path: Path | None = None) -> list[str]:
    rows = read_all(path)
    problems: list[str] = []
    prev = GENESIS
    for i, rec in enumerate(rows, start=1):
        body = {k: v for k, v in rec.items() if k != "hash"}
        if rec.get("seq") != i:
            problems.append(f"row {i}: seq {rec.get('seq')} (a row was removed or reordered)")
        if rec.get("prev") != prev:
            problems.append(f"row {i}: prev does not match row {i - 1}")
        if _digest(prev, body) != rec.get("hash"):
            problems.append(f"row {i}: hash mismatch (edited after the fact)")
        prev = rec.get("hash", "")
    return problems


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def already_recorded(as_of: str, path: Path | None = None) -> bool:
    as_of = str(as_of)[:10]
    return any(r.get("kind") == "basket" and r.get("as_of") == as_of for r in read_all(path))


def record(as_of: str, names: list, closes: dict, bench_close: float, anchor: str,
           bench: str = BENCH, path: Path | None = None,
           refused: dict | None = None) -> dict | None:
    """
    One basket row, or None with the reason counted in `refused`: a duplicate
    snapshot date, a name without a finite positive close, an anchor after
    the snapshot date, or an empty basket.
    """
    def _refuse(why: str):
        if refused is not None:
            refused[why] = refused.get(why, 0) + 1
        return None

    as_of = str(as_of)[:10]
    anchor = str(anchor)[:10]
    names = sorted({str(n).upper() for n in names if str(n).strip()})
    if not names:
        return _refuse("empty basket")
    if anchor > as_of:
        return _refuse("anchor after snapshot date")
    if already_recorded(as_of, path):
        return _refuse("already recorded")
    clean = {}
    for n in names:
        try:
            c = float(closes[n])
        except (KeyError, TypeError, ValueError):
            return _refuse("close missing")
        if not (math.isfinite(c) and c > 0):
            return _refuse("close not finite")
        clean[n] = round(c, 4)
    try:
        b = float(bench_close)
    except (TypeError, ValueError):
        return _refuse("bench close missing")
    if not (math.isfinite(b) and b > 0):
        return _refuse("bench close not finite")
    return append("basket", {
        "as_of": as_of, "anchor": anchor, "names": names, "closes": clean,
        "bench": bench, "bench_close": round(b, 4), "horizon": HORIZON_SESSIONS,
    }, path=path)


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def _days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def window_end(by_date: dict, anchor: str, sessions: int = HORIZON_SESSIONS,
               sessions_between=None):
    """
    (end_date, end_close, None) for the session exactly `sessions` after the
    anchor, or (None, None, reason): anchor_missing, unsettled, gap.
    With `sessions_between(a, b)` injected (the scanner's exchange calendar),
    the end is the date at exactly that distance; without it, consecutive
    dates must be at most MAX_SESSION_GAP_DAYS apart and the end is the
    `sessions`-th date after the anchor.
    """
    if anchor not in by_date:
        return None, None, "anchor_missing"
    after = sorted(d for d in by_date if d > anchor)
    if sessions_between is not None:
        for d in after:
            n = sessions_between(anchor, d)
            if n == sessions:
                return d, by_date[d], None
            if n > sessions:
                return None, None, "gap"
        return None, None, "unsettled"
    if len(after) < sessions:
        return None, None, "unsettled"
    prev = anchor
    for d in after[:sessions]:
        if _days_between(prev, d) > MAX_SESSION_GAP_DAYS:
            return None, None, "gap"
        prev = d
    d = after[sessions - 1]
    return d, by_date[d], None


def grade(fetch_closes, path: Path | None = None, sessions_between=None,
          today: date | None = None) -> dict:
    """
    Write a `basket_outcome` for every basket row whose window has closed.
    `fetch_closes(ticker)` returns [{"date", "close"}] ascending, or None.
    One fetch per name per pass. Never raises on a row.
    """
    today = today or date.today()
    rows = read_all(path)
    done = {r.get("ref_seq") for r in rows if r.get("kind") == "basket_outcome"}
    rep = {"graded": 0, "void": 0, "unsettled": 0, "held": 0, "no_data": 0,
           "failed": 0, "detail": []}
    cache: dict = {}

    def _closes(tk):
        if tk not in cache:
            try:
                raw = fetch_closes(tk)
            except Exception as e:                       # noqa: BLE001
                rep["detail"].append(f"{tk}: fetch failed: {e}")
                raw = None
            by = {}
            for b in raw or []:
                try:
                    d = str(b["date"])[:10]
                    c = float(b["close"])
                except (KeyError, TypeError, ValueError):
                    continue
                if math.isfinite(c) and c > 0:
                    by[d] = c
            cache[tk] = by if raw else None
        return cache[tk]

    for r in rows:
        if r.get("kind") != "basket" or r.get("seq") in done:
            continue
        anchor = r["anchor"]
        age = (today - date.fromisoformat(anchor)).days
        rets, reason, missing = {}, None, []
        for tk, rec_close in list(r["closes"].items()) + [(r["bench"], r["bench_close"])]:
            by = _closes(tk)
            if not by:
                missing.append(tk)
                continue
            if anchor in by and abs(by[anchor] - float(rec_close)) / float(rec_close) > SPLIT_TOL:
                reason = "split"
                break
            end, close, why = window_end(by, anchor, r.get("horizon", HORIZON_SESSIONS),
                                         sessions_between)
            if why:
                reason = why
                break
            rets[tk] = close / float(rec_close) - 1.0
        if missing and reason is None:
            reason = "no_data"
        if reason in ("unsettled",) or (reason in ("anchor_missing", "gap", "no_data") and age <= NO_BARS_DAYS):
            rep["unsettled" if reason == "unsettled" else "held"] += 1
            if reason != "unsettled":
                rep["detail"].append(f"seq {r['seq']} {r['as_of']}: held ({reason})")
            continue
        if reason is not None:
            append("basket_outcome", {"ref_seq": r["seq"], "as_of": r["as_of"],
                                      "anchor": anchor, "outcome": "void", "reason": reason},
                   path=path)
            rep["void"] += 1
            rep["detail"].append(f"seq {r['seq']} {r['as_of']}: void ({reason})")
            continue
        bench_ret = rets.pop(r["bench"])
        basket_ret = sum(rets.values()) / len(rets)
        end = window_end(_closes(r["bench"]), anchor, r.get("horizon", HORIZON_SESSIONS),
                         sessions_between)[0]
        append("basket_outcome", {
            "ref_seq": r["seq"], "as_of": r["as_of"], "anchor": anchor, "end": end,
            "outcome": "graded", "n_names": len(rets),
            "basket_ret": round(basket_ret, 6), "bench_ret": round(bench_ret, 6),
            "delta": round(basket_ret - bench_ret, 6),
            "per_name": {k: round(v, 6) for k, v in sorted(rets.items())},
        }, path=path)
        rep["graded"] += 1
        rep["detail"].append(f"seq {r['seq']} {r['as_of']}: basket {basket_ret:+.2%} "
                             f"vs {r['bench']} {bench_ret:+.2%} -> {basket_ret - bench_ret:+.2%}")
    return rep


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def summary(path: Path | None = None) -> dict:
    rows = read_all(path)
    baskets = [r for r in rows if r.get("kind") == "basket"]
    outs = [r for r in rows if r.get("kind") == "basket_outcome"]
    graded = [o for o in outs if o.get("outcome") == "graded"]
    deltas = [float(o["delta"]) for o in graded]
    n = len(deltas)
    mean = sum(deltas) / n if n else float("nan")
    se = (math.sqrt(sum((d - mean) ** 2 for d in deltas) / (n - 1) / n)
          if n > 1 else float("nan"))
    return {
        "baskets": len(baskets), "graded": n,
        "void": sum(1 for o in outs if o.get("outcome") == "void"),
        "open": len(baskets) - len(outs),
        "mean_delta": mean, "se_delta": se,
        "mean_basket": sum(float(o["basket_ret"]) for o in graded) / n if n else float("nan"),
        "mean_bench": sum(float(o["bench_ret"]) for o in graded) / n if n else float("nan"),
        "weeks_ahead": sum(1 for d in deltas if d > 0),
        "first": baskets[0]["as_of"] if baskets else None,
        "last": baskets[-1]["as_of"] if baskets else None,
        "problems": verify(path),
    }


def report(path: Path | None = None) -> int:
    s = summary(path)
    print("=" * 76)
    print(f"RS BASKET — {path or LOG}")
    print("=" * 76)
    if not s["baskets"]:
        print("  empty. Nothing has been recorded yet.")
        return 0
    f = lambda x: "n/a" if x != x else f"{x:+.2%}"               # noqa: E731
    print(f"  baskets {s['baskets']} ({s['first']} .. {s['last']})   graded {s['graded']}   "
          f"void {s['void']}   open {s['open']}")
    se_txt = "n/a" if s["se_delta"] != s["se_delta"] else f"{s['se_delta']:.2%}"
    print(f"  weekly basket - {BENCH}: mean {f(s['mean_delta'])} +/- {se_txt}   "
          f"(basket {f(s['mean_basket'])}, {BENCH} {f(s['mean_bench'])}; "
          f"{s['weeks_ahead']} of {s['graded']} weeks ahead)")
    print("  NOT a result before 52 graded weeks (results/rs_basket_preregistration.md);")
    print("  first reading at 26, with its SE, whatever it says.")
    if s["problems"]:
        print(f"  CHAIN BROKEN — {len(s['problems'])} problem(s):")
        for m in s["problems"]:
            print(f"    {m}")
        return 1
    print(f"  chain intact across {len(read_all(path))} rows")
    return 0


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------

def selftest() -> int:
    import tempfile
    print("basket_log.py selftest")
    print("=" * 72)
    tmp = Path(tempfile.mkdtemp()) / "b.jsonl"
    names = ["AAA", "BBB", "CCC"]
    closes = {"AAA": 100.0, "BBB": 50.0, "CCC": 20.0}
    ref: dict = {}
    r1 = record("2026-10-25", names, closes, 500.0, "2026-10-23", path=tmp, refused=ref)
    assert r1 and r1["kind"] == "basket" and r1["names"] == names and r1["anchor"] == "2026-10-23", r1
    assert record("2026-10-25", names, closes, 500.0, "2026-10-23", path=tmp, refused=ref) is None
    assert record("2026-11-01", names, dict(closes, BBB=float("nan")), 500.0, "2026-10-30", path=tmp, refused=ref) is None
    assert record("2026-11-01", names, closes, 500.0, "2026-11-02", path=tmp, refused=ref) is None
    assert record("2026-11-01", [], {}, 500.0, "2026-10-30", path=tmp, refused=ref) is None
    assert ref == {"already recorded": 1, "close not finite": 1, "anchor after snapshot date": 1,
                   "empty basket": 1}, ref
    assert not verify(tmp)
    print("record           : one row per snapshot; duplicate, NaN close, bad anchor, empty refused")

    # Sessions: Fri 10-23 anchor, then 10-26..10-30 (5 sessions), 11-02 ...
    sess = ["2026-10-23", "2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30",
            "2026-11-02", "2026-11-03"]
    sb = lambda a, b: sess.index(b) - sess.index(a)                  # noqa: E731
    def series(start, step):
        return [{"date": d, "close": start * (1 + step) ** i} for i, d in enumerate(sess)]
    feeds = {"AAA": series(100.0, 0.01), "BBB": series(50.0, 0.0), "CCC": series(20.0, -0.01),
             "SPY": series(500.0, 0.002)}
    fetched = []
    rep = grade(lambda tk: (fetched.append(tk) or feeds.get(tk)), path=tmp,
                sessions_between=sb, today=date(2026, 11, 3))
    assert rep["graded"] == 1 and sorted(fetched) == ["AAA", "BBB", "CCC", "SPY"], (rep, fetched)
    o = next(r for r in read_all(tmp) if r["kind"] == "basket_outcome")
    assert o["end"] == "2026-10-30" and o["n_names"] == 3, o
    want_b = ((1.01 ** 5 - 1) + 0.0 + (0.99 ** 5 - 1)) / 3
    want_s = 1.002 ** 5 - 1
    assert abs(o["basket_ret"] - want_b) < 1e-6 and abs(o["bench_ret"] - want_s) < 1e-6 \
        and abs(o["delta"] - (want_b - want_s)) < 1e-6, o
    assert grade(lambda tk: feeds.get(tk), path=tmp, sessions_between=sb, today=date(2026, 11, 3))["graded"] == 0, \
        "one outcome per basket, ever"
    print(f"grade            : exactly {HORIZON_SESSIONS} sessions after the anchor; "
          f"equal-weight basket {o['basket_ret']:+.2%} vs SPY {o['bench_ret']:+.2%}")

    # Unsettled while the week is open; HELD on a missing session; VOID when stale.
    _i30 = sess.index("2026-10-30")
    r2 = record("2026-11-01", names, {k: feeds[k][_i30]["close"] for k in names},
                feeds["SPY"][_i30]["close"], "2026-10-30", path=tmp)
    short = {k: [b for b in v if b["date"] <= "2026-11-02"] for k, v in feeds.items()}
    rep2 = grade(lambda tk: short.get(tk), path=tmp, sessions_between=sb, today=date(2026, 11, 2))
    assert rep2["unsettled"] == 1 and rep2["graded"] == 0, rep2
    # The END session missing (10-30): the calendar puts the 5th session there,
    # the feed jumps from the 4th to the 6th, so the window is HELD, never
    # graded on 11-02 as a shifted end. An interior hole does not matter to a
    # two-point return and is not a gap.
    holed = {k: [b for b in v if b["date"] != "2026-10-30"] for k, v in feeds.items()}
    r3 = record("2026-10-24", names, closes, 500.0, "2026-10-23", path=tmp)   # anchor inside the feed
    assert r3 is not None
    rep3 = grade(lambda tk: holed.get(tk), path=tmp, sessions_between=sb, today=date(2026, 11, 3))
    # r2's anchor (10-30) is the missing session too, so r2 is held as anchor_missing.
    assert rep3["held"] == 2 and any("held (gap)" in d for d in rep3["detail"]) \
        and any("held (anchor_missing)" in d for d in rep3["detail"]), rep3
    assert not any(x.get("ref_seq") == r3["seq"] for x in read_all(tmp) if x["kind"] == "basket_outcome"), \
        "a window missing its end is held, not graded on a shifted end"
    interior = {k: [b for b in v if b["date"] != "2026-10-28"] for k, v in feeds.items()}
    assert window_end({b["date"]: b["close"] for b in interior["AAA"]}, "2026-10-23",
                      sessions_between=sb)[0] == "2026-10-30", "an interior hole is not a gap"
    rep4 = grade(lambda tk: holed.get(tk), path=tmp, sessions_between=sb, today=date(2026, 12, 31))
    v = next(x for x in read_all(tmp) if x["kind"] == "basket_outcome" and x.get("ref_seq") == r3["seq"])
    assert rep4["void"] >= 1 and v["outcome"] == "void" and v["reason"] == "gap", (rep4, v)
    print("held / void      : open week unsettled; missing session held, voided after "
          f"{NO_BARS_DAYS} days, never graded on a shifted end")

    # A split between the anchor and the fetch voids the row.
    r5 = record("2026-11-08", names, closes, 500.0, "2026-10-23", path=tmp)
    split = dict(feeds, AAA=[dict(b, close=b["close"] / 2) for b in feeds["AAA"]])
    rep5 = grade(lambda tk: split.get(tk), path=tmp, sessions_between=sb, today=date(2026, 11, 3))
    v5 = next(x for x in read_all(tmp) if x["kind"] == "basket_outcome" and x.get("ref_seq") == r5["seq"])
    assert v5["outcome"] == "void" and v5["reason"] == "split", (rep5, v5)
    print("split            : recorded anchor close no longer matches -> void")

    s = summary(tmp)
    assert s["baskets"] == 4 and s["graded"] == 1 and s["void"] == 3 and s["open"] == 0, s
    assert abs(s["mean_delta"] - (want_b - want_s)) < 1e-6 and s["se_delta"] != s["se_delta"], s
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert report(tmp) == 0
    assert "NOT a result before 52" in buf.getvalue() and "chain intact" in buf.getvalue(), buf.getvalue()
    # Tamper: the chain breaks.
    lines = tmp.read_text().splitlines()
    rec = json.loads(lines[0]); rec["bench_close"] = 1.0
    tmp.write_text("\n".join([json.dumps(rec)] + lines[1:]) + "\n")
    assert verify(tmp), "an edited row must break the chain"
    print("summary / chain  : counts and SE; an edited row breaks the chain")
    print("=" * 72)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="RS basket forward record")
    ap.add_argument("--path", default=None)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    p = Path(a.path) if a.path else None
    if a.verify:
        probs = verify(p)
        print("\n".join(probs) if probs else "chain intact")
        return 1 if probs else 0
    return report(p)


if __name__ == "__main__":
    sys.exit(main())
