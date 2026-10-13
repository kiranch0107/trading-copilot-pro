#!/usr/bin/env python3
"""
daytrade_paper.py — the owner's own chart reading, on paper, graded by the Phase 1 engine
=========================================================================================

Phase 2 of results/day_trading_screen.md, pre-registered in
results/daytrade_paper_preregistration.md. Read that first; nothing here
is tunable without a dated amendment there.

WHAT THIS IS
    Phase 1 codified six chart-reading rules and measured no edge on three
    timeframes. This is the test of the one thing it could not codify: the
    owner's discretionary reading. A call is logged BEFORE entry (ticker,
    side, setup name, the price on the screen, the planned stop and target)
    into an append-only hash chain; later, the Phase 1 engine grades it
    mechanically from 5-minute bars — next bar's open, stop first, target,
    else flat at the close, 5 bps a side, gapped levels fill at the open —
    and attaches a twenty-draw random-entry null at the same session with
    the same geometry. Nothing about how the exit would have been managed
    is measured, by design.

THE GATE
    Nothing is read before 349 settled episodes (power_check.py --sd 1.0,
    +0.15 R, 80%). report() withholds the mean, the interval, the null and
    the verdict until then. There is no peek flag.

THE BAR (one pooled test, read once)
    mean R >= +0.15, session-clustered 95% CI above zero, >= +0.15 R over
    the random null.

ONE WRITER
    This module is the only writer of daytrade_paper.jsonl; the journal,
    the app and the scanner never read it. The chain is forward_log's
    format (forward_log.verify() checks it) but a separate file with a
    separate writer, so the two records can never blend and a merge cannot
    cut either chain.

Run
---
    python daytrade_paper.py log TSLA long orb --price 241.30 --stop 239.80 --target 244.30 --note "..."
    python daytrade_paper.py grade            # fetch 5m bars, grade open calls (inside 60 days)
    python daytrade_paper.py report           # counts; the verdict only at 349 settled
    python daytrade_paper.py verify           # the chain
    python daytrade_paper.py --selftest       # offline
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import forward_log
from forward_log import GENESIS, _digest, read_all
import intraday_rules as ir

LOG = Path("daytrade_paper.jsonl")
ET = ZoneInfo("America/New_York")

# ── fixed by the pre-registration ───────────────────────────────────────
SETUPS = tuple(ir.RULES) + ("pattern", "other")
SIDES = {"long": "Bullish", "short": "Bearish"}
EPISODES_REQUIRED = 349            # power_check.py --sd 1.0, +0.15 R, 80%
SLIPPAGE_BPS = ir.SLIPPAGE_BPS     # 5 bps a side, the Phase 1 cost
DUST_R = forward_log.GRADE_DUST_R  # ±0.05 R is a scratch
NULL_DRAWS = ir.NULL_DRAWS         # twenty fixed-seed draws
CI_Z = 1.96                        # one pooled test, 95%
PRICE_TOL = 0.001                  # the logged price must sit inside its bar, ±0.1%
NO_BARS_DAYS = 50                  # Yahoo serves 60 days of 5m bars; VOID after this
LAST_CALL_MINUTE = 15 * 60 + 55    # the last 5m bar opens 15:55; nothing opens after it


# ── the chain (forward_log's format, a separate file, a separate writer) ──

def _append(kind: str, body: dict, path: Path, now: datetime | None = None) -> dict:
    """The forward log's append(), on THIS file. Written here rather than
    called there because consistency_check names every caller of that
    function a forward-log writer, and this module must never be one."""
    rows = read_all(path)
    prev = rows[-1]["hash"] if rows else GENESIS
    body = dict(body)
    body["seq"] = len(rows) + 1
    body["kind"] = kind
    body["ts"] = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
    body["prev"] = prev
    rec = dict(body)
    rec["hash"] = _digest(prev, body)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return rec


def verify(path: Path | None = None) -> list[str]:
    return forward_log.verify(path or LOG)


# ── the call ────────────────────────────────────────────────────────────

def _market_open(now_et: datetime) -> bool:
    from scanner import is_market_open        # the one calendar; lazy: scanner pulls yfinance
    return is_market_open(now_et)


def log_call(ticker: str, side: str, setup: str, price: float, stop: float, target: float,
             note: str = "", shot: str | None = None, path: Path | None = None,
             now: datetime | None = None, open_check=None) -> dict:
    """One call, before entry. Refuses anything the pre-registration refuses."""
    path = path or LOG
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_et = now.astimezone(ET)
    ticker = ticker.upper().strip()
    if not ticker.isalnum():
        raise ValueError(f"ticker {ticker!r}")
    if side not in SIDES:
        raise ValueError(f"side must be long or short, not {side!r}")
    if setup not in SETUPS:
        raise ValueError(f"setup must be one of {', '.join(SETUPS)}; not {setup!r}")
    for name, v in (("price", price), ("stop", stop), ("target", target)):
        if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
            raise ValueError(f"{name} must be a positive number")
    if side == "long" and not (stop < price < target):
        raise ValueError("a long needs stop < price < target")
    if side == "short" and not (target < price < stop):
        raise ValueError("a short needs target < price < stop")
    if not (open_check or _market_open)(now_et):
        raise ValueError(f"the market is closed at {now_et:%Y-%m-%d %H:%M} ET; a call outside "
                         f"regular hours cannot be graded")
    if now_et.hour * 60 + now_et.minute >= LAST_CALL_MINUTE:
        raise ValueError("no 5-minute bar opens after a call at 15:55 ET or later")
    body = {"ticker": ticker, "side": SIDES[side], "setup": setup,
            "price": float(price), "stop": float(stop), "target": float(target),
            "rr": round(abs(target - price) / abs(price - stop), 3),
            "note": note or "", "shot": shot}
    return _append("call", body, path, now)


# ── grading ─────────────────────────────────────────────────────────────

def fetch_5m(ticker: str):
    """The Phase 1 fetch, unchanged: 60 days of 5-minute regular-hours bars."""
    assert ir.TIMEFRAME == "5m"
    return ir._yahoo_intraday(ticker)


def _bars_hash(d, idx) -> str:
    closes = ",".join(f"{float(x):.4f}" for x in d["Close"].iloc[idx[0]:idx[-1] + 1])
    return hashlib.sha256(closes.encode()).hexdigest()[:16]


def resolve_call(call: dict, bars, today: datetime | None = None) -> dict | None:
    """
    Pure grader for one call. Returns an outcome body, or None when the call
    cannot be graded YET (its session's bars are not in the frame and the
    call is younger than NO_BARS_DAYS).

    VOID reasons: no_bars, no_bar_after_call, price_outside_bar,
    gapped_before_fill, geometry.
    """
    import pandas as pd
    t_call = datetime.fromisoformat(call["ts"]).astimezone(timezone.utc)
    today = (today or datetime.now(timezone.utc)).astimezone(timezone.utc)
    session = t_call.astimezone(ET).strftime("%Y-%m-%d")
    base = {"ref_seq": call["seq"], "ticker": call["ticker"], "session": session,
            "setup": call["setup"], "side": call["side"]}

    def void(reason):
        return dict(base, outcome="void", r=None, reason=reason)

    # A SESSION IS GRADED ONLY AFTER IT HAS CLOSED. "Flat at the session's
    # last bar" needs the last bar; a frame fetched mid-session ends at the
    # current bar, and the first real call (2026-10-13, graded 28 seconds
    # after it was logged) was voided as "no bar after the call" for exactly
    # that reason. Same-day sessions are not gradable yet, whatever the frame.
    if session >= today.astimezone(ET).strftime("%Y-%m-%d"):
        return None
    if bars is None or len(bars) == 0:
        return void("no_bars") if (today - t_call).days > NO_BARS_DAYS else None
    assert ir.TIMEFRAME == "5m", "the grader is the Phase 1 engine on the 5-minute profile"
    d = ir.prepare(bars)
    idx = [int(i) for i in d.index[d["session"] == session]]
    if not idx:
        return void("no_bars") if (today - t_call).days > NO_BARS_DAYS else None
    starts = pd.to_datetime(d["Date"].iloc[idx], utc=True)
    k = None
    for i, t0 in zip(idx, starts):
        if t0.to_pydatetime() <= t_call:
            k = i
    if k is None or k >= idx[-1]:
        return void("no_bar_after_call")
    lo, hi = float(d["Low"].iloc[k]), float(d["High"].iloc[k])
    if not (lo * (1 - PRICE_TOL) <= call["price"] <= hi * (1 + PRICE_TOL)):
        return dict(void("price_outside_bar"), bar_low=lo, bar_high=hi)
    setup = ir._setup(call["setup"], k, call["side"], call["price"], call["stop"], call["target"])
    if setup is None:
        return void("geometry")
    tally: dict = {}
    r = ir.simulate(d, setup, tally)
    if r is None:
        return void("gapped_before_fill")
    nulls = []
    for draw in range(NULL_DRAWS):
        for n in ir.random_null(d, [r], draw):
            nulls.append(float(n["r"]))
    # The engine reports the fill as "entry" and no exit price; the exit is
    # implied by r, the fill and the risk, so it is not stored twice.
    out = dict(base, outcome=r["outcome"], r=round(float(r["r"]), 4),
               fill=round(float(r["entry"]), 4), mfe_r=round(float(r["mfe_r"]), 3),
               mae_r=round(float(r["mae_r"]), 3),
               signal_bar=str(d["Date"].iloc[k]), exit_bar=str(d["Date"].iloc[r["exit_i"]]),
               null_r=round(sum(nulls) / len(nulls), 4) if nulls else None, null_n=len(nulls),
               bars_hash=_bars_hash(d, idx))
    return out


def grade_open_calls(fetch=None, path: Path | None = None, today: datetime | None = None,
                     now: datetime | None = None) -> dict:
    """Grade every call without an outcome. One outcome per call, ever."""
    path = path or LOG
    fetch = fetch or fetch_5m
    rows = read_all(path)
    calls = [r for r in rows if r.get("kind") == "call"]
    done = {r["ref_seq"] for r in rows if r.get("kind") == "outcome"}
    counts = {"graded": 0, "void": 0, "open": 0, "fetch_failed": 0}
    frames: dict = {}
    for c in calls:
        if c["seq"] in done:
            continue
        tk = c["ticker"]
        if tk not in frames:
            try:
                frames[tk] = fetch(tk)
            except Exception as e:                       # noqa: BLE001
                print(f"  ! {tk}: fetch failed: {type(e).__name__}: {e}", flush=True)
                frames[tk] = None
                counts["fetch_failed"] += 1
        out = resolve_call(c, frames[tk], today)
        if out is None:
            counts["open"] += 1
            continue
        _append("outcome", out, path, now)
        counts["void" if out["outcome"] == "void" else "graded"] += 1
        print(f"  #{c['seq']} {tk} {c['side']} {c['setup']}: {out['outcome']}"
              f"{'' if out['r'] is None else f' {out[chr(114)]:+.3f} R'}"
              f"{' (' + out['reason'] + ')' if out.get('reason') else ''}", flush=True)
    return counts


# ── the report ──────────────────────────────────────────────────────────

def summary(path: Path | None = None, today: datetime | None = None) -> dict:
    rows = read_all(path or LOG)
    calls = [r for r in rows if r.get("kind") == "call"]
    outs = {r["ref_seq"]: r for r in rows if r.get("kind") == "outcome"}
    settled = [o for o in outs.values() if o.get("r") is not None]
    void = [o for o in outs.values() if o.get("r") is None]
    open_calls = [c for c in calls if c["seq"] not in outs]
    by_setup: dict = {}
    for c in calls:
        by_setup[c["setup"]] = by_setup.get(c["setup"], 0) + 1
    sessions = {datetime.fromisoformat(c["ts"]).astimezone(ET).strftime("%Y-%m-%d") for c in calls}
    first = min((c["ts"] for c in calls), default=None)
    today = (today or datetime.now(timezone.utc)).astimezone(timezone.utc)
    days = (today - datetime.fromisoformat(first).astimezone(timezone.utc)).days if first else 0
    s = {"calls": len(calls), "open": len(open_calls), "settled": len(settled), "void": len(void),
         "by_setup": by_setup, "sessions": len(sessions), "days": days,
         "required": EPISODES_REQUIRED, "gate_open": len(settled) >= EPISODES_REQUIRED,
         "stale_open": sum(1 for c in open_calls
                           if (today - datetime.fromisoformat(c["ts"]).astimezone(timezone.utc)).days > 40)}
    if not s["gate_open"]:
        return s
    trades = [{"r": float(o["r"]), "session": o["session"]} for o in settled]
    st = ir.clustered(trades)
    st["z"] = CI_Z
    st["lo"], st["hi"] = st["mean"] - CI_Z * st["se"], st["mean"] + CI_Z * st["se"]
    nulls = [float(o["null_r"]) for o in settled if o.get("null_r") is not None]
    null_mean = sum(nulls) / len(nulls) if nulls else float("nan")
    ok, notes = ir.verdict(st, null_mean)
    rs = [float(o["r"]) for o in settled]
    s.update({"stats": st, "null_mean": null_mean, "clears": ok, "notes": notes,
              "wins": sum(1 for r in rs if r > DUST_R), "losses": sum(1 for r in rs if r < -DUST_R),
              "scratch": sum(1 for r in rs if abs(r) <= DUST_R),
              "by_setup_mean": {k: (sum(float(o["r"]) for o in settled if o["setup"] == k) /
                                    max(1, sum(1 for o in settled if o["setup"] == k)))
                                for k in by_setup}})
    return s


def report(path: Path | None = None, today: datetime | None = None) -> int:
    s = summary(path, today)
    print("=" * 72)
    print("PHASE 2 — the owner's chart reading, on paper — results/daytrade_paper_preregistration.md")
    print("=" * 72)
    print(f"  calls {s['calls']}   open {s['open']}   settled {s['settled']}   void {s['void']}"
          f"   sessions {s['sessions']}   days since first call {s['days']}")
    print("  by setup: " + (", ".join(f"{k} {v}" for k, v in sorted(s["by_setup"].items())) or "none"))
    if s["stale_open"]:
        print(f"  ! {s['stale_open']} open call(s) older than 40 days: grade them before the "
              f"provider's 60-day window closes")
    if not s["gate_open"]:
        print(f"\n  READING GATE: {s['settled']} of {s['required']} settled episodes. The mean, the "
              f"interval, the null and the verdict are WITHHELD by the pre-registration.")
        print("=" * 72)
        return 0
    st = s["stats"]
    print(f"\n  settled {st['n']}   mean R {st['mean']:+.3f}   95% CI [{st['lo']:+.3f}, {st['hi']:+.3f}]"
          f"   ({st['sessions']} session clusters)")
    print(f"  wins {s['wins']}   losses {s['losses']}   scratch (|R| <= {DUST_R:g}) {s['scratch']}")
    print(f"  random null at the same times {s['null_mean']:+.3f}   over null {st['mean'] - s['null_mean']:+.3f}")
    for k, m in sorted(s["by_setup_mean"].items()):
        print(f"    {k:<16} mean R {m:+.3f}   (information, no verdict)")
    for n in s["notes"]:
        print(f"      - {n}")
    print(f"  VERDICT: {'CLEARS — Phase 3 may be considered' if s['clears'] else 'does not clear'}")
    print("=" * 72)
    return 0


# ── self-test ───────────────────────────────────────────────────────────

def selftest() -> int:
    import tempfile
    import pandas as pd
    print("daytrade_paper.py selftest")
    print("=" * 72)
    assert ir.TIMEFRAME == "5m"
    # The pre-registration's literals, pinned here as well as in consistency_check.
    assert (EPISODES_REQUIRED, NULL_DRAWS, NO_BARS_DAYS, SLIPPAGE_BPS, DUST_R, CI_Z, PRICE_TOL) == \
        (349, 20, 50, 5.0, 0.05, 1.96, 0.001), "constants drifted from the pre-registration"
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "paper.jsonl"
    always_open = lambda now_et: True      # noqa: E731
    utc = timezone.utc
    # Grading happens the NEXT morning (12:00 UTC = 08:00 ET on the 14th): a
    # session is gradable only after its ET date has passed.
    t_call = datetime(2026, 10, 13, 14, 7, tzinfo=utc)          # 10:07 ET, inside the 10:05 bar

    # Refusals, each with its reason.
    for kw, why in (
        (dict(setup="fibonacci"), "setup not on the list"),
        (dict(stop=242.0), "long with the stop above the price"),
        (dict(side="short"), "short with the target above the price"),
        (dict(now=datetime(2026, 10, 13, 19, 56, tzinfo=utc)), "15:56 ET: no bar opens after"),
        (dict(open_check=lambda n: False), "market closed"),
        (dict(price=-1.0), "negative price"),
    ):
        args = dict(ticker="TSLA", side="long", setup="orb", price=241.30, stop=239.80, target=244.30,
                    path=path, now=t_call, open_check=always_open)
        args.update(kw)
        try:
            log_call(**args)
            raise AssertionError(f"accepted: {why}")
        except ValueError:
            pass
    assert not path.exists(), "a refused call writes nothing"
    print("log              : refuses an off-list setup, a wrong-side stop, a call after 15:55 "
          "or outside hours, a bad price")

    # Two calls on a shaped session: a long that reaches its target, a short that times out.
    c1 = log_call("TSLA", "long", "orb", 241.30, 239.80, 244.30, note="test", path=path,
                  now=t_call, open_check=always_open)
    # 10:22 ET is inside the 10:20 bar (k=10: open 242.0, close 242.6, ±0.15).
    c2 = log_call("tsla", "short", "pattern", 242.30, 250.00, 230.00, path=path,
                  now=datetime(2026, 10, 13, 14, 22, tzinfo=utc), open_check=always_open)
    assert c1["seq"] == 1 and c1["kind"] == "call" and c1["ts"] == "2026-10-13T14:07:00+00:00"
    assert c1["ticker"] == "TSLA" and c2["ticker"] == "TSLA" and c1["rr"] == 2.0 and c2["rr"] == 1.597
    assert verify(path) == [], verify(path)
    closes = [240.9, 241.0, 241.1, 241.0, 241.2, 241.3, 241.4, 241.2,     # 09:30..10:05 (k=7)
              241.5, 242.0, 242.6, 243.2, 243.8, 244.4, 244.9, 245.2] + [245.0] * 62
    bars = ir._session_frame(closes, "2026-10-13", spread=0.15)
    assert len(bars) == 78
    prev_day = ir._session_frame([240.0] * 78, "2026-10-12", spread=0.15)
    frame = pd.concat([prev_day, bars], ignore_index=True)
    counts = grade_open_calls(fetch=lambda tk: frame, path=path,
                              today=datetime(2026, 10, 14, 12, 0, tzinfo=utc),
                              now=datetime(2026, 10, 14, 12, 0, tzinfo=utc))
    assert counts == {"graded": 2, "void": 0, "open": 0, "fetch_failed": 0}, counts
    rows = read_all(path)
    o1, o2 = [r for r in rows if r.get("kind") == "outcome"]
    assert o1["ref_seq"] == 1 and o1["outcome"] == "win" and o1["r"] > 1.5, o1
    assert o1["signal_bar"].startswith("2026-10-13 10:05") and o1["null_n"] == 20, o1
    assert abs(o1["fill"] - bars["Open"].iloc[8] * (1 + SLIPPAGE_BPS / 1e4)) < 1e-6, \
        "entry is the open of the first bar after the call (10:10), plus 5 bps"
    assert o2["ref_seq"] == 2 and o2["outcome"] == "timeout" and o2["r"] < 0, o2
    assert o2["signal_bar"].startswith("2026-10-13 10:20") and o2["mae_r"] < 0 <= o1["mfe_r"]
    assert o2["exit_bar"].startswith("2026-10-13 15:55"), "flat at the session's last bar"
    assert o1["bars_hash"] == o2["bars_hash"] and len(o1["bars_hash"]) == 16
    assert verify(path) == []
    print("grade            : next-bar-open entry +5 bps, stop/target/flat-at-close, "
          f"{NULL_DRAWS}-draw null, bars hash")

    # One outcome per call: a second grading pass writes nothing.
    again = grade_open_calls(fetch=lambda tk: frame, path=path, today=datetime(2026, 10, 14, 12, 0, tzinfo=utc))
    assert again["graded"] == 0 and len(read_all(path)) == 4, again
    # VOIDs: a price outside its bar; a stale call with no bars; a young one stays open.
    c3 = log_call("TSLA", "long", "other", 250.00, 248.00, 254.00, path=path, now=t_call,
                  open_check=always_open)
    c4 = log_call("NVDA", "long", "orb", 100.0, 99.0, 102.0, path=path,          # 55 days old
                  now=datetime(2026, 8, 20, 14, 0, tzinfo=utc), open_check=always_open)
    c5 = log_call("AMD", "long", "orb", 100.0, 99.0, 102.0, path=path,
                  now=datetime(2026, 10, 9, 14, 0, tzinfo=utc), open_check=always_open)
    counts = grade_open_calls(fetch=lambda tk: frame if tk == "TSLA" else None, path=path,
                              today=datetime(2026, 10, 14, 12, 0, tzinfo=utc))
    assert counts == {"graded": 0, "void": 2, "open": 1, "fetch_failed": 0}, counts
    outs = {r["ref_seq"]: r for r in read_all(path) if r.get("kind") == "outcome"}
    assert outs[c3["seq"]]["reason"] == "price_outside_bar" and outs[c3["seq"]]["r"] is None
    assert outs[c4["seq"]]["reason"] == "no_bars", "older than 50 days with no bars is VOID"
    young = dict(c4, ts="2026-08-26T14:00:00+00:00")                               # 49 days old
    assert resolve_call(young, None, datetime(2026, 10, 14, 12, 0, tzinfo=utc)) is None, "49 days: still open"
    assert c5["seq"] not in outs, "a young call with no bars yet stays OPEN"
    # A call inside the session's last bar has nothing to fill into.
    c6 = {"seq": 99, "ts": "2026-10-13T19:56:00+00:00", "ticker": "TSLA", "side": "Bullish",
          "setup": "orb", "price": 245.0, "stop": 244.0, "target": 247.0}
    assert resolve_call(c6, frame, datetime(2026, 10, 14, 12, 0, tzinfo=utc))["reason"] == "no_bar_after_call"
    # The session must have CLOSED: graded the same day, a call stays open even
    # when the frame ends right after it (the mid-session fetch that voided the
    # first real call), and even when the frame holds the whole session.
    partial = frame.iloc[:78 + 9]                      # 2026-10-12 and 10-13 up to 10:10
    c7 = dict(c1, seq=98)
    assert resolve_call(c7, partial, datetime(2026, 10, 13, 18, 0, tzinfo=utc)) is None, \
        "a frame ending at the current bar must not void a same-day call"
    assert resolve_call(c7, frame, datetime(2026, 10, 13, 23, 0, tzinfo=utc)) is None, \
        "same ET date: not gradable until the next day"
    assert resolve_call(c7, frame, datetime(2026, 10, 14, 4, 0, tzinfo=utc)) is not None, \
        "00:00 ET the next day: gradable"
    print("void             : price outside its bar, stale with no bars, call inside the last bar; "
          "a young call stays open; one outcome per call; same-day sessions wait for the close")

    # Tamper: edit a logged stop and the chain says so.
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"stop": 239.8', '"stop": 230.0')
    tampered = tmp / "tampered.jsonl"
    tampered.write_text("\n".join(lines) + "\n", encoding="utf-8")
    probs = verify(tampered)
    assert len(probs) >= 2 and "edited" in probs[0] and "cut" in probs[1], probs
    assert verify(path) == [], "the original is untouched"
    print("chain            : forward_log.verify() reads this file; an edited stop is reported "
          "and the chain is cut after it")

    # The gate: counts only below 349; the verdict at 349.
    s = summary(path, today=datetime(2026, 10, 14, 12, 0, tzinfo=utc))
    assert s["settled"] == 2 and not s["gate_open"] and "stats" not in s and "clears" not in s
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        report(path, today=datetime(2026, 10, 14, 12, 0, tzinfo=utc))
    txt = buf.getvalue()
    assert "WITHHELD" in txt and "mean R" not in txt and "VERDICT" not in txt, txt
    gate = tmp / "gate.jsonl"
    import random
    rng = random.Random(7)
    for i in range(EPISODES_REQUIRED):
        day = f"2026-{1 + i % 12:02d}-{1 + (i // 12) % 28:02d}"
        _append("call", {"ticker": "X", "side": "Bullish", "setup": "orb", "price": 1.0,
                         "stop": 0.9, "target": 1.2, "rr": 2.0, "note": "", "shot": None}, gate,
                datetime.fromisoformat(f"{day}T14:00:00+00:00"))
    for i in range(EPISODES_REQUIRED - 1):
        _append("outcome", {"ref_seq": i + 1, "ticker": "X", "session": f"s{i % 60}", "setup": "orb",
                            "side": "Bullish", "outcome": "win", "r": round(rng.uniform(-1, 2.5), 3),
                            "null_r": 0.0, "null_n": 20}, gate)
    assert not summary(gate)["gate_open"], "348 settled is not 349"
    _append("outcome", {"ref_seq": EPISODES_REQUIRED, "ticker": "X", "session": "s0", "setup": "orb",
                        "side": "Bullish", "outcome": "win", "r": 1.0, "null_r": 0.0, "null_n": 20}, gate)
    s = summary(gate)
    assert s["gate_open"] and "clears" in s and s["stats"]["n"] == EPISODES_REQUIRED and s["stats"]["z"] == CI_Z
    assert s["wins"] + s["losses"] + s["scratch"] == EPISODES_REQUIRED
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        report(gate)
    assert "VERDICT" in buf.getvalue() and "WITHHELD" not in buf.getvalue()
    print(f"gate             : counts only below {EPISODES_REQUIRED} settled; mean, CI, null and "
          f"verdict only at {EPISODES_REQUIRED}")
    print("=" * 72)
    print("All self-tests passed.")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lg = sub.add_parser("log", help="log a call BEFORE entry")
    lg.add_argument("ticker"); lg.add_argument("side", choices=sorted(SIDES))
    lg.add_argument("setup", choices=SETUPS)
    lg.add_argument("--price", type=float, required=True, help="the price on the screen now")
    lg.add_argument("--stop", type=float, required=True); lg.add_argument("--target", type=float, required=True)
    lg.add_argument("--note", default=""); lg.add_argument("--shot", default=None, help="screenshot path")
    sub.add_parser("grade", help="grade open calls from 5-minute bars (inside the provider's 60 days)")
    sub.add_parser("report", help="counts; the verdict only at 349 settled episodes")
    sub.add_parser("verify", help="check the chain")
    a = ap.parse_args()
    if a.cmd == "log":
        try:
            rec = log_call(a.ticker, a.side, a.setup, a.price, a.stop, a.target, a.note, a.shot)
        except ValueError as e:
            print(f"REFUSED: {e}")
            return 2
        print(f"logged call #{rec['seq']} {rec['ticker']} {rec['side']} {rec['setup']} at {rec['ts']} "
              f"(price {rec['price']}, stop {rec['stop']}, target {rec['target']}, rr {rec['rr']}). "
              f"Commit and push today.")
        return 0
    if a.cmd == "grade":
        probs = verify()
        if probs:
            print("REFUSED: the chain does not verify:\n  " + "\n  ".join(probs))
            return 2
        c = grade_open_calls()
        print(f"graded {c['graded']}, void {c['void']}, still open {c['open']}, fetch failed {c['fetch_failed']}")
        return 0
    if a.cmd == "report":
        return report()
    probs = verify()
    print("chain intact" if not probs else "\n".join(probs))
    return 0 if not probs else 1


if __name__ == "__main__":
    sys.exit(main())
