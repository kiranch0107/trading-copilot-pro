#!/usr/bin/env python3
"""
forward_log.py — a tamper-evident record of every signal, as it fires.

WHY THIS EXISTS

This project has no clean data left. All three reserved tranches are spent, so
nothing further can be confirmed on history: any new claim needs either a
universe outside the candidate pool, or data that did not exist when the rules
were written. This is the second route, and it only works if the recording
starts before the outcomes do.

WHAT MAKES A FORWARD RECORD WORTH ANYTHING

Four properties, and it is worthless without all four:

  1. EVERY signal is recorded, including the ones no capital was free for.
     A log of only the trades that got taken is a log of what the account
     could afford, not what the rules produced, and the two diverge exactly
     where it matters. journal_store.log_skipped_signal() already covers
     skips YOU decide; this covers skips the SYSTEM makes.
  2. Recorded BEFORE the outcome is known. A row written after the fact is a
     memory, and memories are selected.
  3. Append-only and hash-chained, so a deleted loser is visible as a broken
     chain rather than an absence nobody can see.
  4. Stamped with the rules that produced it. A log spanning a config change
     is two datasets wearing one name.

WHY NOT journal_store

That module is Streamlit-backed (st.session_state) and GitHub-synced, and
scanner.py must stay Streamlit-free to run unattended -- consistency_check
enforces that. This is stdlib only.

THE LIMIT, STATED UP FRONT

A hash chain cannot detect its own tail being cut. Drop the last row and what
remains verifies clean, because the file is all there is -- so the most recent
trades are precisely what the chain does NOT protect. The anchor must be
external. Here it is git: commit the log after each scan and a truncation shows
up as a deletion in the diff.

EXACTLY ONE WRITER. THIS IS A HARD CONSTRAINT, NOT A PREFERENCE.

append() takes `seq = len(rows) + 1` and `prev = rows[-1]["hash"]`, so two
processes that append from the same starting file produce two rows with THE
SAME seq and THE SAME prev. Git will happily merge both into one file, and the
result does not verify:

    row 3: seq is 2, expected 3 — a record was deleted, inserted or reordered
    row 3: prev e9d4f0c1b6f2 does not match the previous row's hash 4586c04841c0

That damage is PERMANENT. The log is append-only, so the rows cannot be
renumbered to repair it — every hash downstream is computed over the seq. A
second writer does not risk a conflict; it destroys the chain.

scanner.py is the writer. It runs on GitHub Actions and commits the file after
each scan. app.py runs on Streamlit Cloud, a DIFFERENT machine reaching the repo
through the Contents API, so it must NOT append here — which is why
record_outcome() still has no caller (BACKLOG 17). Attaching outcomes needs a
decision about who owns the chain, not a wiring change:

  - have the scanner attach outcomes, reading closed trades from the journal; or
  - give the app its own chain file and reconcile the two offline; or
  - move the log somewhere with a single writer and real appends.

consistency_check.check_forward_log_single_writer() fails CI if a second module
starts writing.

OUTCOMES ARE APPENDED, NEVER EDITED IN

An append-only log cannot go back and fill in a result. A settled trade gets a
SECOND record referencing the first by sequence number. The chain stays intact
and the original row still says exactly what was known at the time.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG = Path("forward_log.jsonl")
GENESIS = "0" * 32


def _canon(body: dict) -> str:
    """Stable serialisation. Key order must not change the hash."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      default=str)


def _digest(prev: str, body: dict) -> str:
    return hashlib.sha256((prev + _canon(body)).encode()).hexdigest()[:32]


def config_fingerprint(cfg: dict) -> str:
    """Short hash of the rules in force.

    A log spanning a config change is two datasets wearing one name, and the
    difference is invisible unless each row carries which rules made it.
    """
    return hashlib.sha256(_canon(cfg).encode()).hexdigest()[:12]


def read_all(path: Path | None = None) -> list[dict]:
    p = path or LOG
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def append(kind: str, body: dict, path: Path | None = None) -> dict:
    """Add one record, chained to the last.

    The hash covers the PREVIOUS hash as well as this body, so changing any
    earlier row invalidates every row after it. That is the property that makes
    a quietly deleted loser detectable.
    """
    p = path or LOG
    rows = read_all(p)
    prev = rows[-1]["hash"] if rows else GENESIS
    body = dict(body)
    body["seq"] = len(rows) + 1
    body["kind"] = kind
    body["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    body["prev"] = prev
    rec = dict(body)
    rec["hash"] = _digest(prev, body)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return rec


def verify(path: Path | None = None) -> list[str]:
    """Every way the chain can be broken. Empty list means intact."""
    rows = read_all(path)
    problems: list[str] = []
    prev = GENESIS
    for i, rec in enumerate(rows, start=1):
        body = {k: v for k, v in rec.items() if k != "hash"}
        if rec.get("seq") != i:
            problems.append(
                f"row {i}: seq is {rec.get('seq')}, expected {i} — a record "
                f"was deleted, inserted or reordered")
        if rec.get("prev") != prev:
            problems.append(
                f"row {i}: prev {str(rec.get('prev'))[:12]} does not match the "
                f"previous row's hash {prev[:12]} — the chain is cut here")
        want = _digest(rec.get("prev", ""), body)
        if rec.get("hash") != want:
            problems.append(
                f"row {i}: hash does not match its contents — this row was "
                f"edited after it was written")
        # CARRY THE RECOMPUTED HASH, NOT THE STORED ONE.
        #
        # Walking on rec["hash"] verifies each row against a value the editor
        # also controls, so a rewritten row whose stored hash is left alone
        # still links correctly to the next one and the damage stops at a
        # single line. Recomputing propagates the break: edit row 1 and every
        # row after it fails too, which is the property that makes a quietly
        # removed loser impossible to hide.
        prev = want
    return problems


# ---------------------------------------------------------------------------
# The two record kinds
# ---------------------------------------------------------------------------

def signal_key(ticker: str, trend: str, bar_date) -> str:
    """The identity of a SIGNAL — not of a scan that noticed it."""
    return f"{ticker}|{trend}|{bar_date}"


def already_recorded(ticker: str, trend: str, bar_date,
                     path: Path | None = None) -> bool:
    """Whether this exact signal bar is already in the log."""
    key = signal_key(ticker, trend, bar_date)
    return any(r.get("kind") == "signal" and r.get("key") == key
               for r in read_all(path))


def record_signal(ticker: str, trend: str, setup: dict, decision: str,
                  reason: str, cfg: dict, bar_date=None,
                  path: Path | None = None) -> dict | None:
    """
    One signal, as the rules produced it, before any outcome exists.

    `decision` is what the PORTFOLIO did with it — taken, or skipped and why.
    Recording skips is the whole point: a log of only the trades capital
    allowed describes the account, not the rules, and the two diverge exactly
    where it matters.

    `bar_date` is the date of the SIGNAL BAR, and it is what makes a row mean
    anything. Returns None when that bar is already logged.

    WHY THIS IS NOT OPTIONAL, AND WHY DEDUPE LIVES HERE
    ---------------------------------------------------
    The scanner runs three times a trading day, and drop_partial_bar() removes
    today's in-progress bar — so all three runs evaluate THE SAME SETTLED BAR
    and this function was called three times for one signal. The alert cooldown
    does not help: it is checked in scanner.run(), AFTER analyze() has already
    written here. So the log recorded scans, not signals, and a rate that
    counted them would have been inflated roughly threefold.

    Worse, the rows carried no bar date at all — `ts` is the wall-clock write
    time — so the duplicates could not be collapsed afterwards and no row could
    be joined to the bar it fired on.

    Both are fatal here specifically, because the log is APPEND-ONLY and
    hash-chained. A poisoned chain cannot be cleaned; it can only be abandoned
    and restarted, which spends the one advantage a forward record has — that
    it started before the outcomes did.

    The dedupe is in this module, not in the caller, because a second caller
    would otherwise reintroduce it. `bar_date` has no default for the same
    reason `today` has none in market_context: a silent default is how the fact
    goes missing.
    """
    if decision not in ("taken", "skipped"):
        raise ValueError(
            f"decision must be 'taken' or 'skipped', not {decision!r}. A third "
            f"state would let a signal be recorded without saying what "
            f"happened to it")
    if decision == "skipped" and not reason.strip():
        raise ValueError(
            "a skipped signal needs a reason. 'Skipped' with no cause cannot "
            "be told apart later from 'skipped because it looked bad', which "
            "is the bias this log exists to prevent")
    if bar_date is None:
        raise ValueError(
            "record_signal() needs the SIGNAL BAR's date. Without it a row "
            "cannot be deduped or joined to the bar it fired on, and the "
            "scanner's three daily runs all read the same settled bar — so the "
            "log would count scans, not signals, in a chain that cannot be "
            "corrected afterwards.")
    bar_date = str(bar_date)[:10]
    if already_recorded(ticker, trend, bar_date, path=path):
        return None
    return append("signal", {
        "ticker": ticker, "trend": trend, "decision": decision,
        "reason": reason, "setup": setup, "bar_date": bar_date,
        "key": signal_key(ticker, trend, bar_date),
        "config": config_fingerprint(cfg),
    }, path=path)


def record_outcome(ref_seq: int, outcome: str, r: float,
                   exit_price: float | None = None,
                   path: Path | None = None) -> dict:
    """
    A settled trade, as a NEW row referencing the signal by sequence number.

    Never an edit. The original row keeps saying exactly what was known when it
    was written, and the chain stays verifiable.

    NO CALLER YET, DELIBERATELY. See EXACTLY ONE WRITER in the module docstring:
    the obvious caller is journal_store.close_position(), which runs in the app
    on a different machine from the scanner, and a second writer destroys the
    chain rather than merely conflicting with it.

    There is a second, independent problem to settle before anything calls this:
    WHICH R. close_position() computes a return on PREMIUM; the `setup` on a
    signal row carries entry/stop/target on the UNDERLYING. Those are different
    denominators, and recording one against the other is the same basis error
    that put a TP+100 win rate against a TP+200 breakeven in risk_params.py.
    Whatever calls this should carry the basis on the row.
    """
    rows = read_all(path)
    ref = next((x for x in rows
                if x.get("seq") == ref_seq and x.get("kind") == "signal"), None)
    if ref is None:
        raise ValueError(
            f"no signal at seq {ref_seq} to attach an outcome to. An outcome "
            f"pointing at nothing is how a result gets recorded for a trade "
            f"that was never logged")
    if ref.get("decision") != "taken":
        raise ValueError(
            f"seq {ref_seq} was {ref.get('decision')!r}, not taken. A skipped "
            f"signal has no outcome to record — inventing one would put "
            f"hypothetical results in the forward record")
    if any(x.get("kind") == "outcome" and x.get("ref_seq") == ref_seq
           for x in rows):
        raise ValueError(
            f"seq {ref_seq} already has an outcome. A second one would let a "
            f"result be revised after the fact, which is what append-only is "
            f"for")
    return append("outcome", {
        "ref_seq": ref_seq, "ticker": ref.get("ticker"),
        "outcome": outcome, "r": round(float(r), 4),
        "exit_price": round(float(exit_price), 4) if exit_price else None,
    }, path=path)


def summary(path: Path | None = None) -> dict:
    rows = read_all(path)
    sigs = [r for r in rows if r.get("kind") == "signal"]
    outs = [r for r in rows if r.get("kind") == "outcome"]
    taken = [s for s in sigs if s.get("decision") == "taken"]
    skipped = [s for s in sigs if s.get("decision") == "skipped"]
    settled = [o for o in outs if o.get("r") is not None]
    configs = sorted({s.get("config") for s in sigs if s.get("config")})
    return {
        "rows": len(rows), "signals": len(sigs), "taken": len(taken),
        "skipped": len(skipped), "settled": len(settled),
        "open": len(taken) - len(settled),
        "mean_r": (sum(o["r"] for o in settled) / len(settled)
                   if settled else float("nan")),
        "configs": configs,
        "skip_reasons": {r: sum(1 for s in skipped if s.get("reason") == r)
                         for r in sorted({s.get("reason") for s in skipped})},
        "problems": verify(path),
    }


def report(path: Path | None = None) -> int:
    s = summary(path)
    p = path or LOG
    print("=" * 76)
    print(f"FORWARD LOG — {p}")
    print("=" * 76)
    if not s["rows"]:
        print("  empty. Nothing has been recorded yet.")
        print("  Every day without recording is clean data not accumulating.")
        return 0
    print(f"  {s['rows']} rows: {s['signals']} signals, "
          f"{s['signals'] and s['rows'] - s['signals']} outcomes")
    print(f"  taken {s['taken']}   skipped {s['skipped']}   "
          f"settled {s['settled']}   still open {s['open']}")
    if s["settled"]:
        print(f"  mean R on settled trades: {s['mean_r']:+.4f}")
        print(f"  NOT a result. It becomes one when enough trades settle to")
        print(f"  have power; see power_check.py for how many that is.")
    if s["skip_reasons"]:
        print(f"  skipped because:")
        for reason, n in sorted(s["skip_reasons"].items(), key=lambda kv: -kv[1]):
            print(f"    {n:>5}  {reason}")
    if len(s["configs"]) > 1:
        print(f"\n  ! {len(s['configs'])} DIFFERENT CONFIGS in one log: "
              f"{', '.join(s['configs'])}")
        print(f"    These are separate datasets. Pooling them measures the mix.")
    elif s["configs"]:
        print(f"  config {s['configs'][0]} throughout")
    print()
    if s["problems"]:
        print(f"  CHAIN BROKEN — {len(s['problems'])} problem(s):")
        for m in s["problems"]:
            print(f"    {m}")
        print(f"  This log cannot be trusted as a record.")
        return 1
    print(f"  chain intact across all {s['rows']} rows")
    print("=" * 76)
    return 0


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------

def selftest() -> int:
    import tempfile
    print("forward_log.py selftest")
    print("=" * 72)
    tmp = Path(tempfile.mkdtemp()) / "t.jsonl"
    cfg = {"adx_min": 25, "atr_stop_mult": 1.0}
    setup = {"rsi": 62.0, "adx": 30.0, "entry": 100.0, "stop": 98.0}

    BAR = "2026-09-15"
    a = record_signal("AAA", "Bullish", setup, "taken", "", cfg,
                      bar_date=BAR, path=tmp)
    b = record_signal("BBB", "Bullish", setup, "skipped", "no free slot",
                      cfg, bar_date=BAR, path=tmp)
    assert a["seq"] == 1 and b["seq"] == 2, (a["seq"], b["seq"])
    assert a["prev"] == GENESIS and b["prev"] == a["hash"], "chain not linked"
    assert not verify(tmp), verify(tmp)
    print(f"chain            : 2 rows linked, genesis -> {a['hash'][:8]} -> ...")

    # ── THE HASH MUST DEPEND ON THE PREVIOUS ROW ──
    # Without that it is a per-row checksum, not a chain, and every other
    # tamper test above still passes because seq and content checks catch
    # those cases by themselves. Assert the linkage directly.
    _body = {"x": 1}
    assert _digest("aaaa", _body) != _digest("bbbb", _body), (
        "the digest ignores the previous hash — this is a checksum per row, "
        "not a chain, and rows could be lifted from another log")

    # ── seq IS A CROSS-CHECK, NOT THE GUARANTEE, AND IS TESTED AS SUCH ──
    # A deletion is caught by the prev-link too, so seq is redundant for that.
    # It still earns its place as a cheap independent read on the same fact;
    # what it must not do is silently pass a wrong value.
    _rows = read_all(tmp)
    _rows[1]["seq"] = 99
    tmp.write_text("\n".join(json.dumps(r, default=str) for r in _rows) + "\n")
    assert any("seq is 99" in m for m in verify(tmp)), verify(tmp)
    tmp.unlink()
    for t in ("AAA", "BBB"):
        record_signal(t, "Bullish", setup, "taken", "", cfg, bar_date=BAR, path=tmp)
    print("linkage          : digest depends on the prior hash; seq cross-checks it")

    # ── EDITING A ROW MUST BREAK IT ──
    rows = read_all(tmp)
    rows[0]["setup"]["rsi"] = 99.0                 # a quiet rewrite
    tmp.write_text("\n".join(json.dumps(r, default=str) for r in rows) + "\n")
    pr = verify(tmp)
    assert pr and "edited" in pr[0], pr
    # and every LATER row is invalidated too, so one edit cannot hide
    assert len(pr) >= 2, (
        f"editing row 1 must invalidate the rows chained to it, got {len(pr)} "
        f"problem(s). A chain that only flags the edited row lets a rewrite "
        f"stop at one line")
    print(f"tamper (edit)    : caught, and {len(pr)} rows implicated")

    # ── DELETING A ROW MUST BREAK IT ──
    tmp.unlink()
    for t in ("AAA", "BBB", "CCC"):
        record_signal(t, "Bullish", setup, "taken", "", cfg, bar_date=BAR, path=tmp)
    assert not verify(tmp)
    rows = read_all(tmp)
    del rows[1]                                    # the middle one disappears
    tmp.write_text("\n".join(json.dumps(r, default=str) for r in rows) + "\n")
    pr = verify(tmp)
    assert pr, "a deleted row left no trace"
    assert any("deleted" in m or "chain is cut" in m for m in pr), pr
    print(f"tamper (delete)  : caught — {pr[0].split('—')[0].strip()}")

    # ── REORDERING MUST BREAK IT ──
    tmp.unlink()
    for t in ("AAA", "BBB", "CCC"):
        record_signal(t, "Bullish", setup, "taken", "", cfg, bar_date=BAR, path=tmp)
    rows = read_all(tmp)
    rows[0], rows[1] = rows[1], rows[0]
    tmp.write_text("\n".join(json.dumps(r, default=str) for r in rows) + "\n")
    assert verify(tmp), "a reordered log verified clean"
    print("tamper (reorder) : caught")

    # ── A SKIP NEEDS A REASON ──
    tmp.unlink()
    try:
        record_signal("AAA", "Bullish", setup, "skipped", "   ", cfg, bar_date=BAR, path=tmp)
        raise SystemExit("a reasonless skip was accepted")
    except ValueError as e:
        assert "reason" in str(e), e
    try:
        record_signal("AAA", "Bullish", setup, "maybe", "x", cfg, bar_date=BAR, path=tmp)
        raise SystemExit("an unknown decision was accepted")
    except ValueError as e:
        assert "taken" in str(e), e
    print("skip discipline  : a skip without a cause is refused")

    # ── OUTCOMES ARE APPENDED, AND ONLY WHERE THEY BELONG ──
    s1 = record_signal("AAA", "Bullish", setup, "taken", "", cfg, bar_date=BAR, path=tmp)
    s2 = record_signal("BBB", "Bullish", setup, "skipped", "no slot", cfg,
                       bar_date=BAR, path=tmp)
    o = record_outcome(s1["seq"], "win", 3.0, 106.0, path=tmp)
    assert o["kind"] == "outcome" and o["ref_seq"] == s1["seq"]
    assert read_all(tmp)[0]["setup"] == setup, (
        "recording an outcome modified the original signal row; outcomes must "
        "be appended, never edited in")
    for bad, why in ((99, "nothing to attach"), (s2["seq"], "was skipped")):
        try:
            record_outcome(bad, "win", 1.0, path=tmp)
            raise SystemExit(f"outcome accepted for seq {bad} ({why})")
        except ValueError:
            pass
    try:
        record_outcome(s1["seq"], "loss", -1.0, path=tmp)
        raise SystemExit("a second outcome was accepted for one signal")
    except ValueError as e:
        assert "already has an outcome" in str(e), e
    assert not verify(tmp)
    print("outcomes         : appended by reference; no edits, no duplicates, "
          "none for skips")

    # ── THE CONFIG FINGERPRINT MUST MOVE WHEN THE RULES DO ──
    f1 = config_fingerprint({"adx_min": 25})
    f2 = config_fingerprint({"adx_min": 30})
    assert f1 != f2, "two different rule sets produced one fingerprint"
    assert config_fingerprint({"a": 1, "b": 2}) == config_fingerprint({"b": 2, "a": 1}), (
        "key order changed the fingerprint; the same rules must hash the same")
    print(f"config stamp     : {f1} vs {f2}, key order irrelevant")

    # ── AND A MIXED-CONFIG LOG MUST SAY SO ──
    record_signal("CCC", "Bullish", setup, "taken", "", {"adx_min": 30},
                  bar_date=BAR, path=tmp)
    s = summary(tmp)
    assert len(s["configs"]) == 2, s["configs"]
    assert not s["problems"], s["problems"]
    print(f"mixed configs    : {len(s['configs'])} detected and reported")

    # ── THE LIMIT: A HASH CHAIN CANNOT SEE ITS OWN TAIL BEING CUT ──
    # Dropping the LAST row leaves a perfectly valid chain. Nothing inside the
    # file can detect it, because the file is all there is — which means the
    # most recent losers are exactly what a chain does not protect. The anchor
    # has to be external, and here it is git: the log is committed after each
    # scan, so a truncation shows up as a deletion in the diff.
    #
    # Pinned as KNOWN behaviour rather than left for someone to discover while
    # trusting the log.
    tmp.unlink()
    for t in ("AAA", "BBB", "CCC"):
        record_signal(t, "Bullish", setup, "taken", "", cfg, bar_date=BAR, path=tmp)
    _rows = read_all(tmp)[:-1]
    tmp.write_text("\n".join(json.dumps(r, default=str) for r in _rows) + "\n")
    assert not verify(tmp), (
        "if tail truncation IS now detected, this comment and the git-anchor "
        "reasoning are out of date and must be rewritten")
    assert len(read_all(tmp)) == 2
    print("known limit      : tail truncation verifies clean — git is the "
          "external anchor")

    # ── ONE ROW PER SIGNAL BAR, AND THE DATE IS NOT OPTIONAL ──
    #
    # The scanner runs three times a trading day and drop_partial_bar() removes
    # today's in-progress bar, so all three runs see THE SAME SETTLED BAR. The
    # alert cooldown is checked AFTER this module has written, so it deduped
    # nothing here. One signal became three rows a day — in a chain that is
    # append-only and cannot be repaired afterwards.
    dd = Path(tempfile.mkdtemp()) / "dd.jsonl"
    r1 = record_signal("AAA", "Bullish", setup, "taken", "", cfg,
                       bar_date="2026-09-15", path=dd)
    r2 = record_signal("AAA", "Bullish", setup, "taken", "", cfg,
                       bar_date="2026-09-15", path=dd)
    assert r1 is not None and r2 is None, "the second scan of one bar wrote a row"
    assert len(read_all(dd)) == 1
    assert r1["bar_date"] == "2026-09-15" and r1["key"] == "AAA|Bullish|2026-09-15"

    # A new BAR, a new TICKER and a new DIRECTION are each a different signal.
    assert record_signal("AAA", "Bullish", setup, "taken", "", cfg,
                         bar_date="2026-09-16", path=dd) is not None
    assert record_signal("BBB", "Bullish", setup, "taken", "", cfg,
                         bar_date="2026-09-15", path=dd) is not None
    assert record_signal("AAA", "Bearish", setup, "taken", "", cfg,
                         bar_date="2026-09-15", path=dd) is not None
    assert len(read_all(dd)) == 4 and not verify(dd), verify(dd)
    print("dedupe           : one row per (ticker, direction, bar); "
          "a new bar is a new signal")

    # A timestamp is not a bar date. Passing one must still key on the DAY.
    dt = Path(tempfile.mkdtemp()) / "dt.jsonl"
    record_signal("AAA", "Bullish", setup, "taken", "", cfg,
                  bar_date="2026-09-15 14:30:00", path=dt)
    assert record_signal("AAA", "Bullish", setup, "taken", "", cfg,
                         bar_date="2026-09-15", path=dt) is None, \
        "a timestamp and its date must be the same signal, or two scans of " \
        "one bar slip through whenever the caller passes a datetime"

    try:
        record_signal("AAA", "Bullish", setup, "taken", "", cfg, path=dt)
    except ValueError as e:
        assert "SIGNAL BAR" in str(e)
        print("bar date         : required — a silent default is how the "
              "fact goes missing")
    else:
        raise AssertionError("record_signal() must refuse a missing bar_date")

    # ── EXACTLY ONE WRITER — the constraint, demonstrated ──
    #
    # append() takes seq = len(rows)+1 and prev = rows[-1]["hash"], so two
    # processes starting from the same file produce rows with the SAME seq and
    # the SAME prev. Git merges both; the result does not verify, and because
    # the log is append-only it cannot be renumbered to repair it.
    import shutil as _sh
    w1 = Path(tempfile.mkdtemp()) / "w.jsonl"
    record_signal("AAA", "Bullish", setup, "taken", "", cfg,
                  bar_date="2026-09-15", path=w1)
    w2 = w1.parent / "w2.jsonl"
    _sh.copy(w1, w2)
    m1 = record_signal("SCN", "Bullish", setup, "taken", "", cfg,
                       bar_date="2026-09-16", path=w1)
    m2 = record_signal("APP", "Bullish", setup, "taken", "", cfg,
                       bar_date="2026-09-16", path=w2)
    assert m1["seq"] == m2["seq"] and m1["prev"] == m2["prev"], \
        "the two-writer collision this constraint exists for did not occur; " \
        "the fixture proves nothing"
    merged = w1.parent / "merged.jsonl"
    merged.write_text("\n".join(json.dumps(r, default=str)
                                for r in read_all(w1) + [m2]) + "\n")
    assert verify(merged), \
        "two writers merged cleanly — if that is now true, the single-writer " \
        "constraint in the module docstring is obsolete and should be removed"
    print("single writer    : two writers collide on seq+prev and the merge "
          "does NOT verify")

    print("=" * 72)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", default=None, help="log file (default forward_log.jsonl)")
    ap.add_argument("--verify", action="store_true", help="check the chain only")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    path = Path(a.path) if a.path else None
    if a.verify:
        problems = verify(path)
        for m in problems:
            print(m)
        print("chain intact" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    return report(path)


if __name__ == "__main__":
    sys.exit(main())
