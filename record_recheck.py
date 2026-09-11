"""
Re-run the three recorded cuts on today's code. Does the record still hold?

WHY THIS EXISTS

results/longshort_split.md is the load-bearing document in this repo. Every
later hypothesis — ADX, RVOL, the verifiability screen, the decision to stop —
leans on its conclusion that the mechanical signal has no edge. It was last
validated 2026-09-10, and the commit order that day is not reassuring:

    00:35  gap-fill scoring bug FIXED (fills had scored INVERTED)
    01:03  the three cuts RE-RUN "post-fix"
    03:23  TWO SILENT MIS-SCORING BUGS FIXED   <-- after that re-run
    04:21  "all three cuts reproduced exactly"

The 04:21 confirmation does postdate the 03:23 fix. But "reproduced exactly"
after a scoring fix means either the fix was latent on this sample, or that run
did not pick it up. Commit messages cannot tell those apart. Only a re-run can,
and it costs nothing: the universes are already contaminated, so no tranche is
spent.

THE DIAGNOSTIC THAT MAKES THIS WORTH RUNNING

The bar fingerprint hashes the DATA. So:

    fingerprint SAME + numbers SAME  -> record confirmed on current code
    fingerprint SAME + numbers MOVED -> the CODE moved. The record is stale and
                                        every conclusion resting on it needs
                                        revisiting.
    fingerprint MOVED               -> the provider re-adjusted history; the
                                        comparison is not clean and says nothing
                                        about the code either way.

That separation is the whole point. Without the fingerprint a moved number is
ambiguous between "we fixed a bug" and "Yahoo rewrote the data".

A CORRECTION THIS MODULE ENCODES

adx_retest.py and rvol_retest.py both defaulted to

    TSLA NVDA AAPL MSFT AMZN META SPY GOOGL AMD NFLX ORCL CRM

and that was described as "the 12-ticker cut". It is not. The record's 12-ticker
set is the 591-trade OOS set — GOOGL AVGO AMD NFLX CRM ADBE QCOM MU ORCL NOW
PANW LRCX — with no overlap with the 7-ticker sweep set. The list actually used
was all SEVEN Aug-2026 parameter-sweep tickers (~60 configurations swept over
them) plus five of the OOS twelve.

The claim that "ADX 25's -0.039 R reproduces the record's -0.048 R" was
therefore comparing two different universes. It was a coincidence, and it was
cited as evidence the engine agreed with itself. Retracted here.

The universes below are read from the record, not retyped from memory.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import sys

import re

import numpy as np

# ── The record, pinned. Source: results/longshort_split.md ──
SWEEP_7 = "TSLA,NVDA,AAPL,MSFT,AMZN,META,SPY"
OOS_12 = "GOOGL,AVGO,AMD,NFLX,CRM,ADBE,QCOM,MU,ORCL,NOW,PANW,LRCX"

RECORD = [
    {"name": "7t/5y  (discovery, swept)", "tickers": SWEEP_7, "years": 5,
     "trades": 418, "avg_r": +0.012, "fingerprint": "v2-24b0f52e1203ecd5",
     "long_n": 260, "long_r": +0.268, "short_n": 158, "short_r": -0.409},
    {"name": "12t/5y (OOS set)", "tickers": OOS_12, "years": 5,
     "trades": 684, "avg_r": -0.012, "fingerprint": "v2-6b5c07fe41849b1c",
     "long_n": 429, "long_r": +0.106, "short_n": 255, "short_r": -0.210},
    {"name": "12t/10y (OOS set)", "tickers": OOS_12, "years": 10,
     "trades": 1386, "avg_r": -0.048, "fingerprint": "v2-e09e31d6eaf30233",
     "long_n": 889, "long_r": +0.085, "short_n": 497, "short_r": -0.287},
]

# A number is "moved" if it differs by more than this. Expectancy is quoted to
# three decimals in the record, so anything beyond half a unit in the last place
# is a real change rather than rounding.
R_TOL = 0.0005


def measure(cut: dict) -> dict | None:
    """Run one cut on today's code and return what the record would compare."""
    import backtest as bt
    cfg = dict(bt.DEFAULTS,
               tickers=[t.strip() for t in cut["tickers"].split(",")],
               years=cut["years"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        trades = bt.run(cfg)
    if not trades:
        return None
    rs = np.array([t["r"] for t in trades if t.get("r") is not None], dtype=float)
    longs = np.array([t["r"] for t in trades
                      if t.get("trend") == "Bullish" and t.get("r") is not None])
    shorts = np.array([t["r"] for t in trades
                       if t.get("trend") == "Bearish" and t.get("r") is not None])
    # The fingerprint is printed by run(); pull it back out of the captured text
    # rather than recomputing it, so this compares what the run itself reported.
    fp = None
    for line in buf.getvalue().splitlines():
        if "v2-" in line:
            for tok in line.replace(",", " ").split():
                if tok.startswith("v2-"):
                    fp = tok.strip(".:;")
                    break
        if fp:
            break
    # Did the FIXED code paths actually fire on this data? "Reproduced exactly"
    # cannot distinguish a latent fix from a dead one, and run() already counts
    # the gapped fills the 2026-09-10 scoring fix rejects — the harness just
    # never surfaced it. Without this, CONFIRMED is ambiguous.
    text = buf.getvalue()
    gap = 0
    m = re.search(r"gapped past its levels\s*:\s*(\d+)", text)
    if m:
        gap = int(m.group(1))
    passed = None
    m2 = re.search(r"survived every gate\s*:\s*(\d+)", text)
    if m2:
        passed = int(m2.group(1))

    return {"trades": len(rs), "avg_r": float(rs.mean()), "fingerprint": fp,
            "gapped": gap, "passed_gates": passed,
            "long_n": len(longs),
            "long_r": float(longs.mean()) if len(longs) else float("nan"),
            "short_n": len(shorts),
            "short_r": float(shorts.mean()) if len(shorts) else float("nan"),
            "stdout": buf.getvalue()}


def compare(cut: dict, got: dict) -> tuple[str, list[str]]:
    """Classify the drift. Returns (verdict, notes)."""
    notes = []
    fp_known = cut["fingerprint"]
    fp_now = got.get("fingerprint")
    fp_same = (fp_now == fp_known) if fp_now else None

    moved = []
    for key, label, tol in (("trades", "trade count", 0),
                            ("avg_r", "expectancy", R_TOL),
                            ("long_n", "long count", 0),
                            ("long_r", "long avg R", R_TOL),
                            ("short_n", "short count", 0),
                            ("short_r", "short avg R", R_TOL)):
        want, have = cut[key], got[key]
        if isinstance(want, int):
            if have != want:
                moved.append(f"{label} {want} -> {have}")
        elif abs(have - want) > tol:
            moved.append(f"{label} {want:+.3f} -> {have:+.3f}")

    if fp_same is None:
        notes.append("no fingerprint printed — cannot separate code from data")
        return ("INDETERMINATE", notes + moved)
    if not fp_same:
        notes.append(f"fingerprint {fp_known} -> {fp_now}: the DATA changed, so "
                     f"any moved number says nothing about the code")
        return ("DATA CHANGED", notes + moved)
    if not moved:
        # CONFIRMED is not one thing. If the 2026-09-10 scoring fix rejected
        # fills on this data, the numbers reproducing is a STRONG confirmation:
        # the fix ran, changed which setups became trades, and the survivors
        # still score identically. If it rejected nothing, the fix was never
        # exercised here, and this run says nothing about whether it works —
        # only that it did not need to.
        g = got.get("gapped", 0)
        if g:
            return ("CONFIRMED", [
                "fingerprint and every figure reproduce",
                f"and the 2026-09-10 gap-fill fix FIRED: {g} setups rejected, "
                f"so the fixed path ran and the survivors still score the same"])
        return ("CONFIRMED (fix not exercised)", [
            "fingerprint and every figure reproduce",
            "but the 2026-09-10 gap-fill fix rejected NOTHING on this data, so "
            "this run confirms the record without testing that fix. The fix is "
            "not dead code — backtest.py's own selftest exercises it on "
            "synthetic bars — it simply had no qualifying trade here"])
    notes.append("fingerprint is UNCHANGED, so the data is identical and these "
                 "moves are the CODE:")
    return ("CODE MOVED", notes + moved)


def report(results: list[tuple[dict, dict | None]]) -> int:
    print("=" * 78)
    print("RECORD RE-CHECK — do the three recorded cuts still hold on today's code?")
    print("=" * 78)
    worst = 0
    for cut, got in results:
        print(f"\n  {cut['name']}   ({cut['years']}y)")
        print("  " + "-" * 74)
        if got is None:
            print("    ! no trades returned — nothing to compare")
            worst = max(worst, 2)
            continue
        print(f"    {'':<14} {'recorded':>12} {'now':>12}")
        for key, label in (("trades", "trades"), ("avg_r", "avg R"),
                           ("long_n", "long n"), ("long_r", "long R"),
                           ("short_n", "short n"), ("short_r", "short R")):
            w, h = cut[key], got[key]
            fmt = "{:>12}" if isinstance(w, int) else "{:>+12.3f}"
            flag = ""
            if isinstance(w, int):
                flag = "" if h == w else "   <-- MOVED"
            elif abs(h - w) > R_TOL:
                flag = "   <-- MOVED"
            print(f"    {label:<14} " + fmt.format(w) + " " + fmt.format(h) + flag)
        print(f"    {'fingerprint':<14} {cut['fingerprint']:>12} "
              f"{str(got['fingerprint']):>12}")
        g, pg = got.get("gapped", 0), got.get("passed_gates")
        print(f"    {'gapped fills':<14} {'':>12} {g:>12}"
              + ("   <-- the 09-10 scoring fix FIRED here" if g else
                 "   (fix did not fire on this data)"))
        if pg is not None:
            print(f"    {'passed gates':<14} {'':>12} {pg:>12}")
        verdict, notes = compare(cut, got)
        print(f"\n    VERDICT: {verdict}")
        for n in notes:
            print(f"      - {n}")
        worst = max(worst, {"CONFIRMED": 0,
                            "CONFIRMED (fix not exercised)": 0,
                            "CODE MOVED": 1,
                            "DATA CHANGED": 2, "INDETERMINATE": 2}[verdict])

    print("\n" + "=" * 78)
    if worst == 0:
        print("  ALL THREE CONFIRMED on current code. results/longshort_split.md")
        print("  stands, and every conclusion resting on it stands with it.")
    elif worst == 1:
        print("  THE CODE MOVED under an unchanged fingerprint. The record is")
        print("  STALE. Every hypothesis that cited it — ADX, RVOL, the")
        print("  verifiability screen, the decision to stop — must be re-read")
        print("  against the new numbers before being relied on again.")
    else:
        print("  INDETERMINATE. The data or the fingerprint moved, so this run")
        print("  cannot separate a code change from a provider re-adjustment.")
        print("  Do not read it as either confirming or refuting the record.")
    print("=" * 78)
    return worst


def selftest() -> int:
    print("record_recheck.py selftest")
    print("=" * 70)

    base = RECORD[2]

    # ── the four verdicts, each reachable ──
    same = {k: base[k] for k in ("trades", "avg_r", "long_n", "long_r",
                                 "short_n", "short_r", "fingerprint")}
    same["gapped"] = 3          # default: the fix fired
    v, _ = compare(base, dict(same))
    assert v == "CONFIRMED", v

    moved_r = dict(same, avg_r=base["avg_r"] + 0.01)
    v, notes = compare(base, moved_r)
    assert v == "CODE MOVED", v
    assert any("CODE" in n for n in notes), notes

    moved_fp = dict(same, fingerprint="v2-deadbeefdeadbeef", avg_r=base["avg_r"] + 0.01)
    v, notes = compare(base, moved_fp)
    assert v == "DATA CHANGED", (
        "a changed fingerprint must NOT be reported as a code change — the "
        "provider rewriting history is a different fact with a different fix")

    # ── CONFIRMED must distinguish "fix fired" from "fix never ran" ──
    # Numbers reproducing means two very different things depending on whether
    # the fixed code path was exercised, and collapsing them is how a dead fix
    # gets mistaken for a latent one.
    fired = compare(base, dict(same, gapped=7))
    assert fired[0] == "CONFIRMED" and any("FIRED" in n for n in fired[1]), fired
    idle = compare(base, dict(same, gapped=0))
    assert idle[0] == "CONFIRMED (fix not exercised)", idle[0]
    assert any("rejected NOTHING" in n for n in idle[1]), idle[1]
    assert fired[0] != idle[0], (
        "a run where the fix rejected setups and one where it rejected none "
        "must not report the same verdict — that is the difference between "
        "testing the fix and merely not needing it")
    print("fix exercised?   : CONFIRMED splits on whether the gap fix fired")

    v, _ = compare(base, dict(same, fingerprint=None))
    assert v == "INDETERMINATE", v
    print("verdicts         : CONFIRMED / CODE MOVED / DATA CHANGED / INDETERMINATE")

    # ── rounding must not masquerade as drift ──
    v, _ = compare(base, dict(same, avg_r=base["avg_r"] + 0.0004))
    assert v == "CONFIRMED", (
        "a move inside the record's own quoting precision is rounding, not "
        "drift, and must not be reported as the code moving")
    v, _ = compare(base, dict(same, avg_r=base["avg_r"] + 0.0006))
    assert v == "CODE MOVED", "a move beyond the tolerance must be caught"
    print(f"tolerance        : +/-{R_TOL} is rounding; beyond it is drift")

    # ── a moved TRADE COUNT is never rounding ──
    v, notes = compare(base, dict(same, trades=base["trades"] + 1))
    assert v == "CODE MOVED" and any("trade count" in n for n in notes), notes
    print("integer fields   : one extra trade is always drift, never rounding")

    # ── the universes must be the RECORD's, not the ones adx_retest used ──
    import adx_retest as ar
    wrong = set(t.strip() for t in ar.DEFAULT_TICKERS.split(","))
    right = set(t.strip() for t in OOS_12.split(","))
    assert wrong != right, "sanity: these were the sets that got confused"
    assert "TSLA" not in right and "SPY" not in right, (
        "the record's 12-ticker OOS set must contain NONE of the 7 swept "
        "tickers — if it does, the universes have been mixed up again")
    assert len(right) == 12 and len(set(SWEEP_7.split(","))) == 7
    assert not (right & set(SWEEP_7.split(","))), (
        "the OOS 12 and the swept 7 must not overlap at all")
    print(f"universes        : swept 7 and OOS 12 are disjoint, as the record says")

    # ── WIRING: main() must run the RIGHT three cuts ──
    # The lesson from adx_retest, where main() called an API that did not exist
    # while every selftest passed. Here the specific risk is different and
    # worse: main() could run cleanly on the WRONG universes, which is exactly
    # the mistake this module exists to correct.
    import backtest as _bt
    calls = []
    def _fake_run(cfg):
        calls.append((tuple(cfg["tickers"]), cfg["years"]))
        return [{"r": 0.0, "trend": "Bullish" if i % 2 else "Bearish"}
                for i in range(10)]
    real_run, real_argv = _bt.run, sys.argv
    try:
        _bt.run = _fake_run
        sys.argv = ["record_recheck.py"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = main()
    finally:
        _bt.run, sys.argv = real_run, real_argv

    assert len(calls) == 3, f"three cuts must run, got {len(calls)}"
    assert [c[1] for c in calls] == [5, 5, 10], [c[1] for c in calls]
    assert calls[0][0] == tuple(SWEEP_7.split(",")), (
        f"cut 1 must be the swept 7, got {calls[0][0]}")
    assert calls[1][0] == calls[2][0] == tuple(OOS_12.split(",")), (
        f"cuts 2 and 3 must be the OOS 12, got {calls[1][0]}")
    assert "RECORD RE-CHECK" in out.getvalue()
    assert rc in (0, 1, 2)
    print(f"main() wiring    : ran 7t/5y, 12t/5y, 12t/10y on the RECORD's "
          f"universes, exit {rc}")

    print("=" * 70)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    out = []
    for cut in RECORD:
        print(f"  running {cut['name']} ...", file=sys.stderr)
        out.append((cut, measure(cut)))
    return report(out)


if __name__ == "__main__":
    sys.exit(main())
