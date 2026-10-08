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
through the Contents API, so it must NOT append here.

SETTLED 2026-09-16, by the owner: the SCANNER attaches outcomes too. It reads
closed trades out of trade_journal.json (which the app writes and commits) and
appends their outcome rows itself — see attach_outcomes(). The app still never
touches this file, so the single-writer rule holds with outcomes in place.

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
                  path: Path | None = None, hq: bool | None = None) -> dict | None:
    """
    One signal, as the rules produced it, before any outcome exists.

    `decision` is what the PORTFOLIO did with it — taken, or skipped and why.
    Recording skips is the whole point: a log of only the trades capital
    allowed describes the account, not the rules, and the two diverge exactly
    where it matters.

    `hq` is the high-quality TIER AS A TAG, never a gate (owner decision
    2026-10-08, results/forward_record_preregistration.md). A long base
    signal is `taken` whether or not it clears the tier; `hq` records what the
    alert gate would have selected, so the record can later say whether the
    tier earned the alerts it suppressed. Rows written before the field
    existed carry the same fact as `setup.high_quality`.

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
    row = {
        "ticker": ticker, "trend": trend, "decision": decision,
        "reason": reason, "setup": setup, "bar_date": bar_date,
        "key": signal_key(ticker, trend, bar_date),
    }
    if hq is not None:
        row["hq"] = bool(hq)
    return append("signal", {
        **row,
        "config": config_fingerprint(cfg),
    }, path=path)


def record_scan(bar_date, tickers: list, n_signals: int, cfg: dict,
                path: Path | None = None) -> dict:
    """
    One row per EXECUTED scan: "this bar was evaluated, on these names, and
    this many signals fired." Never deduped — every real scan is a fact.

    WHY THIS EXISTS. Between 09-16 and 10-07 the log had no rows for bars
    09-30, 10-02, 10-05 and 10-06. Not because nothing fired — because the
    scan that would have evaluated each bar landed after the close and was
    skipped, and GitHub reported the workflow as `success`. A missing signal
    row meant EITHER "quiet market" OR "no scan ran", and the record built to
    be the clean record could not say which.

    With this row, absence of signal is a positive fact ("scanned, nothing"),
    and absence of scan is visible ("no scan row for this bar"). It is also
    the heartbeat scan_coverage() reads to alert when the scanner has gone
    silent — the failure this repo names as its worst, and the one it was in
    for three weeks.
    """
    return append("scan", {
        "bar_date": str(bar_date)[:10],
        "tickers": sorted(str(t) for t in tickers),
        "n_signals": int(n_signals),
        "config": config_fingerprint(cfg),
    }, path=path)


def last_scan(path: Path | None = None) -> dict | None:
    """The most recent scan row, or None if the log has never recorded one."""
    rows = [r for r in read_all(path) if r.get("kind") == "scan"]
    return rows[-1] if rows else None


def record_outcome(ref_seq: int, outcome: str, r: float | None, basis: str,
                   exit_price: float | None = None,
                   trade_id: str | None = None, mode: str | None = None,
                   path: Path | None = None, detail: dict | None = None,
                   reason: str | None = None) -> dict:
    """
    A settled trade, as a NEW row referencing the signal by sequence number.

    Never an edit. The original row keeps saying exactly what was known when it
    was written, and the chain stays verifiable.

    CALLED BY THE SCANNER, via attach_outcomes(). Owner's decision, 2026-09-16.
    That keeps the single-writer rule (see the module docstring): the app never
    appends here, it only writes trade_journal.json, which the scanner reads.

    `basis` IS REQUIRED AND HAS NO DEFAULT. `r` is meaningless without it.
    close_position() computes a return on PREMIUM; a signal row's `setup`
    carries entry/stop/target on the UNDERLYING. Those are different
    denominators, and recording one against the other is the same error that put
    a TP+100 win rate against a TP+200 breakeven in risk_params.py. A default
    would let a caller record the wrong one silently, which is the whole failure
    mode — so there is no default.

    `trade_id` is the journal row's id, and it makes attachment idempotent: the
    scanner re-reads the journal on every run, and without it a trade would get
    a second outcome row on the next scan.

    `mode` is paper/live, carried rather than filtered, because journal_store
    keeps those apart for good reason and an analysis here must be able to too.

    ONE OUTCOME PER (ref_seq, basis), since 2026-10-08
    (results/forward_grading_preregistration.md). A journal trade's premium
    outcome and the grader's stop-distance outcome may both exist for one
    signal; they are different measurements and summary() never pools them.
    A second row on the SAME basis is still refused.

    `outcome` "void" is a real row: the signal could not be graded, `r` is
    None, and `reason` says why (gapped_before_fill, split_in_window,
    no_bars). A void is closed, visibly, rather than left looking open.
    """
    BASES = ("premium", "stop_distance")
    if basis not in BASES:
        raise ValueError(
            f"basis must be one of {BASES}, not {basis!r}. An R with no stated "
            f"denominator is the error risk_params.py already paid for: a "
            f"return on premium and a stop-distance R are not comparable and "
            f"must never be summed.")
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
           and x.get("basis") == basis for x in rows):
        raise ValueError(
            f"seq {ref_seq} already has a {basis} outcome. A second one would "
            f"let a result be revised after the fact, which is what "
            f"append-only is for")
    if (outcome == "void") != (r is None):
        raise ValueError(
            "a void outcome carries r=None and nothing else does; a number on "
            "a void, or a void-shaped None on a settled row, would be summed "
            "or dropped by accident")
    if trade_id and any(x.get("kind") == "outcome" and x.get("trade_id") == trade_id
                        for x in rows):
        raise ValueError(
            f"trade {trade_id} already has an outcome row. The scanner re-reads "
            f"the journal every run, so without this a settled trade would be "
            f"recorded again on every scan")
    row = {
        "ref_seq": ref_seq, "ticker": ref.get("ticker"),
        "outcome": outcome, "r": None if r is None else round(float(r), 4),
        "basis": basis, "trade_id": trade_id, "mode": mode,
        "exit_price": round(float(exit_price), 4) if exit_price else None,
    }
    if reason:
        row["reason"] = reason
    if detail:
        row["detail"] = detail
    return append("outcome", row, path=path)


# ---------------------------------------------------------------------------
# Grading — results/forward_grading_preregistration.md (2026-10-08)
# ---------------------------------------------------------------------------
#
# Every rule below is fixed in that document. resolve_signal() is a PURE
# function of the signal row and the bars that followed it, stdlib only: it
# cannot fetch, so the only way market data reaches it is the scanner handing
# over plain rows. That keeps the grader testable bar by bar and keeps this
# module importable anywhere, which the tamper-evident record requires.

GRADE_MAX_HOLD = 20          # bars after entry, exit at the close of the last
GRADE_SLIPPAGE_BPS = 2.0     # per side, against the trade
GRADE_DUST_R = 0.05          # timeout inside this is BREAKEVEN (journal_store.DUST_R)
GRADE_BASIS_TOL = 0.02       # signal-bar close vs recorded price: beyond this, the
                             # price basis changed (a split) and the row is void
GRADE_NO_BARS_DAYS = 45      # calendar days after the signal with no bars -> void
VOID_REASONS = ("gapped_before_fill", "split_in_window", "no_bars")


def resolve_signal(row: dict, bars: list, today=None,
                   max_hold: int = GRADE_MAX_HOLD,
                   slippage_bps: float = GRADE_SLIPPAGE_BPS,
                   dust: float = GRADE_DUST_R) -> dict | None:
    """
    Grade one taken signal against the bars that followed it.

    `bars`: ascending rows {"date": "YYYY-MM-DD", "open", "high", "low",
    "close"} on the SAME price basis the signal was computed on (raw,
    auto_adjust=False). Returns None while the signal is UNSETTLED (its
    window has not closed), otherwise a dict ready for record_outcome():
      {"outcome": win|loss|breakeven, "r", "exit_price", "detail": {...}}
      {"outcome": "void", "reason": <VOID_REASONS>, "detail": {...}}

    THE RULES (the pre-registration's table, in code order):
      entry      open of the first bar after bar_date, +slippage in the trade's
                 direction; gapped past stop or target at that open -> void
      walk       up to max_hold bars starting WITH the entry bar; the stop is
                 checked before the target on every bar
      stop hit   exit at the WORSE of the stop and that bar's open (real fill)
      target hit exit at the BETTER of the target and that bar's open
      timeout    the close of the max_hold-th bar
      exit       slippage against the trade on every exit
      label      target -> win, stop -> loss, timeout by sign with the dust floor
      r_at_level the R the record's engine would book (fill AT the level),
                 recorded in detail so BACKLOG 21's defect is measured live.
                 `r` is the graded number.
    """
    import datetime as _dt
    setup = row.get("setup") or {}
    trend = row.get("trend")
    sig_date = str(row.get("bar_date"))[:10]
    try:
        price = float(setup["price"]); stop = float(setup["stop"])
        target = float(setup["target"])
    except (KeyError, TypeError, ValueError):
        return {"outcome": "void", "reason": "no_bars",
                "detail": {"exit_rule": "void:no_bars",
                           "note": "row has no usable price/stop/target"}}
    long = trend == "Bullish"
    today = today or _dt.date.today()
    age_days = (today - _dt.date.fromisoformat(sig_date)).days

    bars = sorted((b for b in bars or [] if b.get("date")), key=lambda b: b["date"])
    sig_bar = next((b for b in bars if b["date"] == sig_date), None)
    after = [b for b in bars if b["date"] > sig_date]

    if sig_bar is None or not after:
        if age_days > GRADE_NO_BARS_DAYS:
            return {"outcome": "void", "reason": "no_bars",
                    "detail": {"exit_rule": "void:no_bars", "age_days": age_days}}
        return None                                    # unsettled: too soon
    # THE PRICE BASIS MUST BE THE ONE THE SIGNAL WAS COMPUTED ON. A split or
    # reverse split between the signal and now rewrites the fetched history,
    # and the row's stop and target would then be in the wrong units.
    if price > 0 and abs(float(sig_bar["close"]) - price) / price > GRADE_BASIS_TOL:
        return {"outcome": "void", "reason": "split_in_window",
                "detail": {"exit_rule": "void:split_in_window",
                           "recorded_close": price,
                           "fetched_close": float(sig_bar["close"])}}

    slip = lambda px: px * slippage_bps / 10_000.0
    raw_open = float(after[0]["open"])
    fill = raw_open + slip(raw_open) if long else raw_open - slip(raw_open)
    gapped = ((fill <= stop or fill >= target) if long
              else (fill >= stop or fill <= target))
    if gapped or abs(fill - stop) <= 0:
        return {"outcome": "void", "reason": "gapped_before_fill",
                "detail": {"exit_rule": "void:gapped_before_fill",
                           "fill": round(fill, 4), "fill_date": after[0]["date"],
                           "stop": stop, "target": target}}
    risk = abs(fill - stop)

    exit_rule = exit_px = level_px = None
    exit_bar = None
    gap_fill = False
    for j, b in enumerate(after[:max_hold]):
        o, h, l = float(b["open"]), float(b["high"]), float(b["low"])
        if long:
            if l <= stop:
                exit_rule, level_px = "stop", stop
                exit_px = min(stop, o); gap_fill = o < stop
            elif h >= target:
                exit_rule, level_px = "target", target
                exit_px = max(target, o); gap_fill = o > target
        else:
            if h >= stop:
                exit_rule, level_px = "stop", stop
                exit_px = max(stop, o); gap_fill = o > stop
            elif l <= target:
                exit_rule, level_px = "target", target
                exit_px = min(target, o); gap_fill = o < target
        if exit_rule:
            exit_bar = (j, b); break
    if exit_rule is None:
        if len(after) < max_hold:
            return None                                # unsettled: window open
        j, b = max_hold - 1, after[max_hold - 1]
        exit_rule, exit_px, level_px = "timeout", float(b["close"]), float(b["close"])
        exit_bar = (j, b)

    def _r(px):
        px_net = px - slip(px) if long else px + slip(px)
        return ((px_net - fill) if long else (fill - px_net)) / risk

    r = _r(exit_px)
    r_level = _r(level_px)
    if exit_rule == "target":
        outcome = "win"
    elif exit_rule == "stop":
        outcome = "loss"
    else:
        outcome = "win" if r > dust else ("loss" if r < -dust else "breakeven")
    return {
        "outcome": outcome, "r": round(r, 4), "exit_price": round(exit_px, 4),
        "detail": {"fill": round(fill, 4), "fill_date": after[0]["date"],
                   "exit_date": exit_bar[1]["date"], "bars_held": exit_bar[0] + 1,
                   "exit_rule": exit_rule, "gap_fill": gap_fill,
                   "cost_bps": slippage_bps, "r_at_level": round(r_level, 4)},
    }


def grade_open_signals(fetch, path: Path | None = None, today=None) -> dict:
    """
    Write a stop_distance outcome for every taken signal whose window has
    closed. `fetch(ticker)` returns the plain bar rows resolve_signal() reads,
    or None. One fetch per ticker. Never raises on a row; the report says what
    happened to each.
    """
    rows = read_all(path)
    have = {(o.get("ref_seq"), o.get("basis")) for o in rows
            if o.get("kind") == "outcome"}
    open_sigs = [s for s in rows if s.get("kind") == "signal"
                 and s.get("decision") == "taken"
                 and (s.get("seq"), "stop_distance") not in have]
    report = {"graded": 0, "void": 0, "unsettled": 0, "no_data": 0,
              "failed": 0, "detail": []}
    by_ticker: dict = {}
    for s in open_sigs:
        by_ticker.setdefault(s.get("ticker"), []).append(s)
    for tk, sigs in sorted(by_ticker.items()):
        try:
            bars = fetch(tk)
        except Exception as e:                           # noqa: BLE001
            report["failed"] += len(sigs)
            report["detail"].append(f"{tk}: fetch failed: {e}")
            continue
        if not bars:
            report["no_data"] += len(sigs)
            continue
        for s in sigs:
            try:
                res = resolve_signal(s, bars, today=today)
                if res is None:
                    report["unsettled"] += 1
                    continue
                record_outcome(s["seq"], res["outcome"], res.get("r"),
                               "stop_distance", exit_price=res.get("exit_price"),
                               detail=res.get("detail"), reason=res.get("reason"),
                               path=path)
                if res["outcome"] == "void":
                    report["void"] += 1
                    report["detail"].append(
                        f"seq {s['seq']} {tk} {s.get('bar_date')}: void "
                        f"({res['reason']})")
                else:
                    report["graded"] += 1
                    d = res["detail"]
                    report["detail"].append(
                        f"seq {s['seq']} {tk} {s.get('bar_date')}: "
                        f"{res['outcome']} {res['r']:+.2f} R via {d['exit_rule']}"
                        f"{' (gap)' if d.get('gap_fill') else ''} "
                        f"after {d['bars_held']} bar(s)")
            except Exception as e:                       # noqa: BLE001
                report["failed"] += 1
                report["detail"].append(f"seq {s.get('seq')} {tk}: {e}")
    return report


# ---------------------------------------------------------------------------
# Attaching outcomes — THE SCANNER'S JOB, by the owner's decision (2026-09-16)
# ---------------------------------------------------------------------------
#
# Of the three routes in BACKLOG 17, this is route 1: the scanner reads closed
# trades out of trade_journal.json and appends their outcomes here. The app
# never appends — it only writes the journal, through the Contents API — so the
# single-writer rule that the chain depends on is preserved.
#
# THE HARD PART IS NOT THE APPEND, IT IS THE MATCH. A signal row knows
# (underlying, direction, signal bar). A journal row knows a composite option
# ticker ("NKE 2026-10-02 35P"), a direction, and when the position was opened.
# Joining them is a guess unless the rules are strict, and a wrong guess writes
# a PERMANENT row: the log is append-only, so a mis-attached outcome cannot be
# corrected, only contradicted by a later note nobody will read.
#
# So this refuses far more than it accepts, and reports every refusal.

SETTLED = ("WIN", "LOSS", "BREAKEVEN")


def _journal_date(row: dict):
    """The date a journal row's position was OPENED, as a date."""
    import datetime as _dt
    raw = str(row.get("date") or "").replace(" ET", "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime(raw[:16] if len(raw) >= 16 else raw,
                                         fmt).date()
        except ValueError:
            continue
    return None


def attach_outcomes(journal: list, path: Path | None = None,
                    max_lag_days: int = 5) -> dict:
    """
    Append an outcome row for every settled journal trade that maps to exactly
    one un-settled `taken` signal. Returns a report; never raises on a row.

    WHAT IS REFUSED, AND WHY EACH ONE MATTERS
    -----------------------------------------
    reconstructed  `source_backfilled` rows are owner RECOLLECTION, not capture
                   (BACKLOG 2 says so and says to exclude them). They also
                   predate this log entirely, so any match would be spurious.
    not_signal     `source` is discretionary or missing. There is no signal to
                   attach to, and inventing one puts a hypothetical in the
                   record.
    unsettled      outcome is not WIN/LOSS/BREAKEVEN — nothing to record yet.
    predates_log   the position was opened before the log's first signal bar.
                   Nothing could have been recorded for it, so it is counted
                   and NOT reported as a failure — the first journal rows
                   predate the log by weeks, and reporting them as `no_match`
                   on every scan was a nag that could never be resolved.
    no_match       no `taken` signal for that underlying and direction within
                   `max_lag_days` before the position opened.
    ambiguous      MORE THAN ONE candidate. Refused outright rather than
                   resolved by "nearest", because a wrong attachment is
                   permanent and a missing one is merely missing.
    already        the signal already has an outcome, or this trade_id does.

    R IS RECORDED ON THE PREMIUM BASIS, and labelled. `actual_rr` from the
    journal is (exit - entry) / entry on the OPTION premium. It is NOT the
    stop-distance R the signal's setup implies, and the two must never be summed.
    The underlying R is not recoverable here: close_position() writes stop and
    target as 0 for option rows, so premium is the only basis that exists.
    """
    import datetime as _dt
    rows = read_all(path)
    taken = [r for r in rows if r.get("kind") == "signal"
             and r.get("decision") == "taken"]
    # PREMIUM BASIS ONLY. A signal the grader has already resolved on the
    # stop-distance basis can still take the journal's premium outcome; the
    # two are different measurements of one signal and coexist.
    settled_seqs = {r.get("ref_seq") for r in rows
                    if r.get("kind") == "outcome" and r.get("basis") == "premium"}
    settled_trades = {r.get("trade_id") for r in rows if r.get("kind") == "outcome"}
    # THE LOG'S FIRST BAR. A trade opened before it has nothing to match and
    # is not a mismatch — it is simply older than the record.
    log_start = None
    for r in rows:
        if r.get("kind") != "signal":
            continue
        try:
            b = _dt.date.fromisoformat(str(r.get("bar_date")))
        except (TypeError, ValueError):
            continue
        if log_start is None or b < log_start:
            log_start = b

    report = {"attached": 0, "reconstructed": 0, "not_signal": 0,
              "unsettled": 0, "predates_log": 0, "no_match": 0, "ambiguous": 0,
              "already": 0, "detail": []}

    for row in journal or []:
        tid = row.get("id")
        if row.get("outcome") not in SETTLED:
            report["unsettled"] += 1
            continue
        if tid in settled_trades:
            report["already"] += 1
            continue
        if row.get("source_backfilled"):
            report["reconstructed"] += 1
            continue
        if row.get("source") != "signal":
            report["not_signal"] += 1
            continue

        underlying = str(row.get("ticker") or "").split()[0].upper()
        opened = _journal_date(row)
        if not underlying or opened is None:
            report["no_match"] += 1
            report["detail"].append(f"{tid}: no usable ticker or open date")
            continue
        if log_start is None or opened < log_start:
            # Counted, not reported: there is no action that resolves it.
            report["predates_log"] += 1
            continue

        cands = []
        for sig in taken:
            if sig.get("seq") in settled_seqs:
                continue
            if str(sig.get("ticker", "")).upper() != underlying:
                continue
            if sig.get("trend") != row.get("trend"):
                continue
            try:
                bar = _dt.date.fromisoformat(str(sig.get("bar_date")))
            except (TypeError, ValueError):
                continue
            lag = (opened - bar).days
            # The signal must PRECEDE the trade. A bar dated after the position
            # was opened cannot have caused it, and matching one would read the
            # record backwards.
            if 0 <= lag <= max_lag_days:
                cands.append(sig)

        if not cands:
            report["no_match"] += 1
            report["detail"].append(
                f"{tid}: no taken {row.get('trend')} signal for {underlying} "
                f"within {max_lag_days}d before {opened}")
            continue
        if len(cands) > 1:
            # NEVER resolved by "nearest". Two signals on the same name and
            # side inside a few days is exactly when a heuristic picks wrong,
            # and the row it writes cannot be taken back.
            report["ambiguous"] += 1
            report["detail"].append(
                f"{tid}: {len(cands)} candidate signals "
                f"({', '.join(str(c['bar_date']) for c in cands)}) — refused "
                f"rather than guessed")
            continue

        sig = cands[0]
        try:
            record_outcome(sig["seq"], str(row["outcome"]).lower(),
                           float(row.get("actual_rr") or 0.0),
                           basis="premium",
                           exit_price=row.get("exit_price"),
                           trade_id=tid, mode=row.get("mode"), path=path)
        except ValueError as e:
            report["already"] += 1
            report["detail"].append(f"{tid}: {e}")
            continue
        settled_seqs.add(sig["seq"])
        settled_trades.add(tid)
        report["attached"] += 1
        report["detail"].append(
            f"{tid}: attached to seq {sig['seq']} "
            f"({underlying} {sig['trend']} bar {sig['bar_date']})")

    return report


def _is_hq(row: dict) -> bool:
    """The HQ tag, reading the top-level field first and the setup for rows
    written before it existed."""
    if "hq" in row:
        return bool(row["hq"])
    return bool((row.get("setup") or {}).get("high_quality"))


def _mean_r(settled: list, sigs: list, hq: bool) -> float:
    by_seq = {s.get("seq"): s for s in sigs}
    rs = [o["r"] for o in settled
          if o.get("ref_seq") in by_seq and _is_hq(by_seq[o["ref_seq"]]) == hq]
    return sum(rs) / len(rs) if rs else float("nan")


def summary(path: Path | None = None) -> dict:
    rows = read_all(path)
    sigs = [r for r in rows if r.get("kind") == "signal"]
    outs = [r for r in rows if r.get("kind") == "outcome"]
    scans = [r for r in rows if r.get("kind") == "scan"]
    taken = [s for s in sigs if s.get("decision") == "taken"]
    skipped = [s for s in sigs if s.get("decision") == "skipped"]
    taken_hq = [s for s in taken if _is_hq(s)]
    configs = sorted({s.get("config") for s in sigs if s.get("config")})
    # BY BASIS, AND ONLY BY BASIS. A premium R and a stop-distance R are
    # different denominators; there is no pooled mean here on purpose.
    by_basis = {}
    for basis in ("premium", "stop_distance"):
        outs_b = [o for o in outs if o.get("basis") == basis]
        settled_b = [o for o in outs_b if o.get("r") is not None]
        by_basis[basis] = {
            "settled": len(settled_b),
            "void": sum(1 for o in outs_b if o.get("outcome") == "void"),
            "open": len(taken) - len(outs_b),
            "mean_r": (sum(o["r"] for o in settled_b) / len(settled_b)
                       if settled_b else float("nan")),
            "mean_r_hq": _mean_r(settled_b, sigs, True),
            "mean_r_base": _mean_r(settled_b, sigs, False),
        }
    return {
        "rows": len(rows), "scans": len(scans),
        "bars_scanned": len({x.get("bar_date") for x in scans}),
        "signals": len(sigs), "taken": len(taken),
        "taken_hq": len(taken_hq), "taken_base": len(taken) - len(taken_hq),
        "skipped": len(skipped), "outcomes": len(outs),
        "by_basis": by_basis,
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
    print(f"  {s['rows']} rows: {s['signals']} signals, {s['outcomes']} outcomes, "
          f"{s['scans']} scans")
    print(f"  taken {s['taken']} (hq {s['taken_hq']}, base {s['taken_base']})   "
          f"skipped {s['skipped']}")
    for basis, b in s["by_basis"].items():
        if not (b["settled"] or b["void"]):
            continue
        print(f"  {basis:14} settled {b['settled']}   void {b['void']}   "
              f"open {b['open']}   mean R {b['mean_r']:+.4f} "
              f"(hq {b['mean_r_hq']:+.4f} / base {b['mean_r_base']:+.4f})")
    if any(b["settled"] for b in s["by_basis"].values()):
        print(f"  NOT a result. It becomes one at 349 settled EPISODES on one")
        print(f"  basis (results/forward_record_preregistration.md); the two")
        print(f"  bases are different denominators and are never summed.")
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
    o = record_outcome(s1["seq"], "win", 3.0, "stop_distance",
                       exit_price=106.0, path=tmp)
    assert o["kind"] == "outcome" and o["ref_seq"] == s1["seq"]
    assert read_all(tmp)[0]["setup"] == setup, (
        "recording an outcome modified the original signal row; outcomes must "
        "be appended, never edited in")
    for bad, why in ((99, "nothing to attach"), (s2["seq"], "was skipped")):
        try:
            record_outcome(bad, "win", 1.0, "premium", path=tmp)
            raise SystemExit(f"outcome accepted for seq {bad} ({why})")
        except ValueError:
            pass
    # ONE OUTCOME PER (ref_seq, basis). A second on the SAME basis is a
    # revision and is refused; a different basis is a different measurement
    # of the same signal and coexists (results/forward_grading_preregistration.md).
    try:
        record_outcome(s1["seq"], "loss", -1.0, "stop_distance", path=tmp)
        raise SystemExit("a second outcome on one basis was accepted for one signal")
    except ValueError as e:
        assert "already has a stop_distance outcome" in str(e), e
    o2 = record_outcome(s1["seq"], "loss", -0.4, "premium", trade_id="TP1", path=tmp)
    assert o2["basis"] == "premium" and o2["ref_seq"] == s1["seq"]
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

    # ── ATTACHING OUTCOMES — what it accepts, and everything it refuses ──
    #
    # A mis-attached outcome is PERMANENT: the log is append-only, so a wrong
    # row cannot be corrected, only contradicted by a note nobody reads. So the
    # refusals matter more than the accept, and each is tested separately.
    ao = Path(tempfile.mkdtemp()) / "ao.jsonl"
    sig = record_signal("NKE", "Bearish", setup, "taken", "", cfg,
                        bar_date="2026-09-10", path=ao)

    def _trade(**kw):
        base = {"id": "T1", "ticker": "NKE 2026-10-02 35P", "trend": "Bearish",
                "date": "2026-09-11 11:56 ET", "outcome": "WIN",
                "actual_rr": 0.62, "source": "signal", "mode": "live"}
        base.update(kw)
        return base

    r = attach_outcomes([_trade()], path=ao)
    assert r["attached"] == 1, r
    _out = [x for x in read_all(ao) if x["kind"] == "outcome"][0]
    assert _out["ref_seq"] == sig["seq"] and _out["trade_id"] == "T1"
    assert _out["basis"] == "premium", (
        "actual_rr is a return on PREMIUM; recording it unlabelled, or as a "
        "stop-distance R, is the basis error risk_params.py already paid for")
    assert _out["mode"] == "live"
    print(f"attach           : journal trade -> seq {sig['seq']}, "
          f"basis {_out['basis']}")

    # THE HQ TAG rides on the row and the summary splits taken by it. Rows
    # from before the field existed read it from setup.high_quality.
    ao_hq = Path(tempfile.mkdtemp()) / "ao_hq.jsonl"
    record_signal("HQA", "Bullish", dict(setup, high_quality=False), "taken",
                  "", cfg, bar_date="2026-09-10", path=ao_hq, hq=False)
    record_signal("HQB", "Bullish", setup, "taken", "", cfg,
                  bar_date="2026-09-10", path=ao_hq, hq=True)
    record_signal("HQC", "Bullish", dict(setup, high_quality=True), "taken",
                  "", cfg, bar_date="2026-09-10", path=ao_hq)       # legacy row
    _hq_rows = {r["ticker"]: r for r in read_all(ao_hq) if r["kind"] == "signal"}
    assert _hq_rows["HQA"]["hq"] is False and _hq_rows["HQB"]["hq"] is True
    assert "hq" not in _hq_rows["HQC"], "no tag given -> no field invented"
    _hs = summary(ao_hq)
    assert (_hs["taken"], _hs["taken_hq"], _hs["taken_base"]) == (3, 2, 1), _hs
    print("hq tag           : on the row when given; summary splits taken "
          "2 hq / 1 base, legacy rows read from setup")

    # ── THE GRADER: every row of the pre-registration's rules table ──
    # (results/forward_grading_preregistration.md)
    import datetime as _dt
    def _bars(*rows):
        # rows: (date, open, high, low, close)
        return [{"date": d, "open": o, "high": h, "low": l, "close": c}
                for d, o, h, l, c in rows]
    _row = {"trend": "Bullish", "bar_date": "2026-09-01",
            "setup": {"price": 100.0, "stop": 98.0, "target": 106.0}}
    _sig = ("2026-09-01", 99.0, 101.0, 98.5, 100.0)
    _today = _dt.date(2026, 12, 1)
    _flat = lambda d: (d, 100.0, 101.0, 99.0, 100.0)
    _days = [f"2026-09-{d:02d}" for d in range(2, 31)] + \
            [f"2026-10-{d:02d}" for d in range(1, 15)]

    # Clean stop: bar 3 trades down through 98, opens above it -> fills AT 98.
    r1 = resolve_signal(_row, _bars(_sig, _flat(_days[0]), _flat(_days[1]),
                                    (_days[2], 99.5, 100.0, 97.0, 97.5)), today=_today)
    _fill = 100.0 * (1 + 2e-4)
    assert r1["outcome"] == "loss" and r1["detail"]["exit_rule"] == "stop" \
        and r1["detail"]["gap_fill"] is False and r1["detail"]["bars_held"] == 3, r1
    assert abs(r1["r"] - ((98.0 * (1 - 2e-4) - _fill) / (_fill - 98.0))) < 1e-4, \
        (r1, "r is rounded to 4 places")
    assert abs(r1["r"] - r1["detail"]["r_at_level"]) < 1e-9, "no gap: both Rs agree"
    # Gapped stop: opens at 90 -> fills at 90, not 98. r < -1; r_at_level = -1.
    r2 = resolve_signal(_row, _bars(_sig, _flat(_days[0]),
                                    (_days[1], 90.0, 91.0, 89.0, 90.5)), today=_today)
    assert r2["outcome"] == "loss" and r2["detail"]["gap_fill"] is True, r2
    assert r2["r"] < -4.5 and abs(r2["detail"]["r_at_level"] + 1.0) < 2e-2, \
        (r2, "at the level it is -1 R plus 4 bps of slippage")
    # Gapped target: opens at 110 -> fills at 110, better than 106.
    r3 = resolve_signal(_row, _bars(_sig, _flat(_days[0]),
                                    (_days[1], 110.0, 111.0, 109.0, 110.0)), today=_today)
    assert r3["outcome"] == "win" and r3["detail"]["gap_fill"] is True \
        and r3["r"] > r3["detail"]["r_at_level"] > 2.9, r3
    # Both levels inside one bar -> the stop, conservative.
    r4 = resolve_signal(_row, _bars(_sig, (_days[0], 100.0, 107.0, 97.0, 103.0)), today=_today)
    assert r4["outcome"] == "loss" and r4["detail"]["exit_rule"] == "stop", r4
    # Timeout at the 20th bar's close: win / loss / breakeven by the dust floor.
    def _hold(close_last):
        bs = [_sig] + [_flat(d) for d in _days[:19]]
        bs.append((_days[19], 100.0, 101.0, 99.0, close_last))
        return _bars(*bs)
    t_win = resolve_signal(_row, _hold(101.0), today=_today)
    t_loss = resolve_signal(_row, _hold(99.0), today=_today)
    t_be = resolve_signal(_row, _hold(100.05), today=_today)
    assert t_win["outcome"] == "win" and t_win["detail"]["exit_rule"] == "timeout" \
        and t_win["detail"]["bars_held"] == 20, t_win
    assert t_loss["outcome"] == "loss" and t_be["outcome"] == "breakeven", (t_loss, t_be)
    # Nineteen bars and no hit -> UNSETTLED, no row.
    assert resolve_signal(_row, _bars(_sig, *[_flat(d) for d in _days[:19]]),
                          today=_today) is None, "19 bars is not a closed window"
    # Gapped before the fill -> VOID, not a trade with an inverted stop.
    v1 = resolve_signal(_row, _bars(_sig, (_days[0], 97.0, 99.0, 96.0, 98.0)), today=_today)
    assert v1["outcome"] == "void" and v1["reason"] == "gapped_before_fill", v1
    # Split in the window: the fetched signal-bar close no longer matches.
    v2 = resolve_signal(_row, _bars(("2026-09-01", 49.5, 50.5, 49.2, 50.0),
                                    (_days[0], 50.0, 50.5, 49.0, 50.0)), today=_today)
    assert v2["outcome"] == "void" and v2["reason"] == "split_in_window", v2
    # No bars after the signal: unsettled while young, void once stale.
    assert resolve_signal(_row, _bars(_sig), today=_dt.date(2026, 9, 10)) is None
    v3 = resolve_signal(_row, _bars(_sig), today=_today)
    assert v3["outcome"] == "void" and v3["reason"] == "no_bars", v3
    # A short, mirrored: gap UP through the stop fills at the open.
    _srow = {"trend": "Bearish", "bar_date": "2026-09-01",
             "setup": {"price": 100.0, "stop": 102.0, "target": 94.0}}
    r5 = resolve_signal(_srow, _bars(_sig, _flat(_days[0]),
                                     (_days[1], 108.0, 109.0, 107.0, 108.0)), today=_today)
    assert r5["outcome"] == "loss" and r5["detail"]["gap_fill"] is True and r5["r"] < -3.5, r5
    print("grader           : clean stop, gapped stop (r<-1, r_at_level=-1), "
          "gapped target, both-in-bar->stop, timeout win/loss/breakeven, "
          "unsettled, 3 voids, short mirrored")

    # ── grade_open_signals: writes rows, per-basis, idempotent ──
    ag = Path(tempfile.mkdtemp()) / "ag.jsonl"
    _gsetup = {"price": 100.0, "entry": 100.0, "stop": 98.0, "target": 106.0, "rr": 3.0}
    g1 = record_signal("GA", "Bullish", _gsetup, "taken", "", cfg,
                       bar_date="2026-09-01", path=ag, hq=True)
    g2 = record_signal("GB", "Bullish", _gsetup, "taken", "", cfg,
                       bar_date="2026-09-01", path=ag, hq=False)
    record_signal("GC", "Bearish", _gsetup, "skipped", "direction gate", cfg,
                  bar_date="2026-09-01", path=ag)
    _feeds = {"GA": _bars(_sig, _flat(_days[0]), (_days[1], 90.0, 91.0, 89.0, 90.5)),
              "GB": _bars(_sig, _flat(_days[0]))}
    _calls = []
    def _fetch(tk):
        _calls.append(tk); return _feeds.get(tk)
    rep_g = grade_open_signals(_fetch, path=ag, today=_today)
    assert rep_g["graded"] == 1 and rep_g["unsettled"] == 1 and rep_g["void"] == 0, rep_g
    assert sorted(_calls) == ["GA", "GB"], "one fetch per ticker, skipped rows never fetched"
    _outs = [x for x in read_all(ag) if x["kind"] == "outcome"]
    assert len(_outs) == 1 and _outs[0]["ref_seq"] == g1["seq"] \
        and _outs[0]["basis"] == "stop_distance" and _outs[0]["detail"]["gap_fill"]
    rep_g2 = grade_open_signals(_fetch, path=ag, today=_today)
    assert rep_g2["graded"] == 0 and len([x for x in read_all(ag) if x["kind"] == "outcome"]) == 1, \
        "a graded signal must never be graded again"
    # SKIPPED, not refused: a graded signal is not offered to the resolver at
    # all. The record_outcome guard would catch a second row too, but it would
    # count as "failed" and read as a grading error on every run forever.
    assert rep_g2["failed"] == 0, (rep_g2, "a graded signal must be skipped, not retried")
    # A SIGNAL GRADED ON THE STOP-DISTANCE BASIS STILL TAKES THE JOURNAL'S
    # PREMIUM OUTCOME: different measurement, same signal.
    _jt = {"id": "GAJ", "ticker": "GA 2026-10-16 100C", "trend": "Bullish",
           "date": "2026-09-02 10:00 ET", "outcome": "WIN", "actual_rr": 0.3,
           "source": "signal", "mode": "live"}
    _ra = attach_outcomes([_jt], path=ag)
    assert _ra["attached"] == 1, (_ra, "a stop-distance row must not block the premium attachment")
    # A second stop_distance is refused (the premium row from the journal is
    # already beside it).
    try:
        record_outcome(g1["seq"], "win", 1.0, "stop_distance", path=ag)
    except ValueError as e:
        assert "stop_distance outcome" in str(e), e
    else:
        raise AssertionError("second stop_distance outcome accepted")
    try:
        record_outcome(g2["seq"], "void", -1.0, "stop_distance", reason="no_bars", path=ag)
    except ValueError as e:
        assert "void" in str(e), e
    else:
        raise AssertionError("a void with a number was accepted")
    _sg = summary(ag)["by_basis"]
    assert _sg["stop_distance"]["settled"] == 1 and _sg["premium"]["settled"] == 1
    assert _sg["stop_distance"]["open"] == 1 and _sg["premium"]["open"] == 1, _sg
    assert _sg["stop_distance"]["mean_r"] < -4 and abs(_sg["premium"]["mean_r"] - 0.3) < 1e-9
    assert _sg["stop_distance"]["mean_r_hq"] < -4 and _sg["stop_distance"]["mean_r_base"] != \
        _sg["stop_distance"]["mean_r_base"], "GB is unsettled, so base mean is nan"
    assert "mean_r" not in summary(ag), "no pooled mean, ever"
    print("grade_open       : one fetch per ticker, idempotent, premium beside "
          "stop_distance, void needs r=None, summary split by basis")

    # IDEMPOTENT. The scanner re-reads the journal on every run.
    r2 = attach_outcomes([_trade()], path=ao)
    assert r2["attached"] == 0 and r2["already"] == 1, r2
    assert len([x for x in read_all(ao) if x["kind"] == "outcome"]) == 1
    print("attach, re-run   : no second row — the scanner reads the journal "
          "every scan")

    # ── THE REFUSALS ──
    ao2 = Path(tempfile.mkdtemp()) / "ao2.jsonl"
    # An unrelated earlier row fixes the log's first bar at 09-01, so a trade
    # opened 09-09 is INSIDE the log's span and its refusal below is a real
    # no_match, not "older than the record".
    record_signal("XOM", "Bullish", setup, "skipped", "fixture", cfg,
                  bar_date="2026-09-01", path=ao2)
    record_signal("NKE", "Bearish", setup, "taken", "", cfg,
                  bar_date="2026-09-10", path=ao2)

    # Reconstructed provenance is owner RECOLLECTION (BACKLOG 2), and predates
    # this log entirely. Every journal row on file today is one of these.
    assert attach_outcomes([_trade(source_backfilled=True)],
                          path=ao2)["reconstructed"] == 1
    # A discretionary trade has no signal to attach to.
    assert attach_outcomes([_trade(source="discretionary")],
                          path=ao2)["not_signal"] == 1
    assert attach_outcomes([_trade(outcome="OPEN")], path=ao2)["unsettled"] == 1
    # Wrong direction, wrong name, and a signal AFTER the trade opened.
    assert attach_outcomes([_trade(trend="Bullish")], path=ao2)["no_match"] == 1
    assert attach_outcomes([_trade(ticker="AAPL 2026-10-02 35P")],
                          path=ao2)["no_match"] == 1
    assert attach_outcomes([_trade(date="2026-09-09 10:00 ET")],
                          path=ao2)["no_match"] == 1, \
        "a signal dated AFTER the position opened cannot have caused it"
    assert attach_outcomes([_trade(date="2026-09-30 10:00 ET")],
                          path=ao2)["no_match"] == 1, \
        "a signal three weeks before the trade is not evidence it caused it"
    assert not [x for x in read_all(ao2) if x["kind"] == "outcome"], \
        "a refusal must write NOTHING"
    print("refusals         : reconstructed, discretionary, unsettled, wrong "
          "name/side, signal after the trade, stale signal")

    # OLDER THAN THE RECORD is counted, not reported. The log's first bar in
    # ao2 is 09-01; a trade opened 08-20 could not have a row and saying
    # "no_match" about it every scan is a nag nothing can resolve. A trade
    # opened ON the first bar is inside the record and matched normally.
    _r_old = attach_outcomes([_trade(date="2026-08-20 10:00 ET")], path=ao2)
    assert _r_old["predates_log"] == 1 and _r_old["no_match"] == 0, _r_old
    assert not any("T1" in d for d in _r_old["detail"]), \
        "a trade older than the log must not appear in the nag list"
    _r_edge = attach_outcomes([_trade(date="2026-09-01 10:00 ET")], path=ao2)
    assert _r_edge["predates_log"] == 0, \
        "a trade opened ON the log's first bar is inside the record"
    assert not [x for x in read_all(ao2) if x["kind"] == "outcome"]
    # An EMPTY log predates everything.
    ao_empty = Path(tempfile.mkdtemp()) / "ao_empty.jsonl"
    assert attach_outcomes([_trade()], path=ao_empty)["predates_log"] == 1
    print("predates_log     : older than the first bar -> counted, not nagged; "
          "on the first bar -> matched normally")

    # AMBIGUITY IS REFUSED, NEVER RESOLVED BY "NEAREST".
    ao3 = Path(tempfile.mkdtemp()) / "ao3.jsonl"
    record_signal("NKE", "Bearish", setup, "taken", "", cfg,
                  bar_date="2026-09-09", path=ao3)
    record_signal("NKE", "Bearish", setup, "taken", "", cfg,
                  bar_date="2026-09-10", path=ao3)
    r3 = attach_outcomes([_trade()], path=ao3)
    assert r3["ambiguous"] == 1 and r3["attached"] == 0, r3
    assert not [x for x in read_all(ao3) if x["kind"] == "outcome"], \
        "an ambiguous match wrote a row — a wrong attachment here is permanent"
    print("ambiguity        : two candidates -> refused and reported, "
          "not resolved by nearest")

    # IDEMPOTENCY IS TWO LAYERS, and both are tested because the outer one
    # hides the inner. attach_outcomes() skips a trade_id it has already seen,
    # so removing record_outcome()'s own guard changes nothing above — which
    # means that guard was untested until this call drove it directly. It is
    # the one that protects a future second caller.
    #
    # Driven against a DIFFERENT signal row, deliberately. Re-using sig["seq"]
    # trips the ref_seq guard first, and both messages contain "already has an
    # outcome" — so that version of this test passed whether or not the
    # trade_id guard existed. A second signal row isolates it.
    _sig2 = record_signal("NKE", "Bearish", setup, "taken", "", cfg,
                          bar_date="2026-08-01", path=ao)
    try:
        record_outcome(_sig2["seq"], "win", 0.62, "premium", trade_id="T1",
                       path=ao)
    except ValueError as e:
        assert "trade T1 already has an outcome row" in str(e), e
        print("idempotency      : both layers — attach_outcomes skips, and "
              "record_outcome refuses the same trade under a NEW seq")
    else:
        raise AssertionError(
            "record_outcome recorded trade T1 a second time under a different "
            "signal; without this guard one trade can settle many signals")

    # A SKIPPED signal can never take an outcome, even by a clean match.
    ao4 = Path(tempfile.mkdtemp()) / "ao4.jsonl"
    record_signal("NKE", "Bearish", setup, "skipped", "no room", cfg,
                  bar_date="2026-09-10", path=ao4)
    r4 = attach_outcomes([_trade()], path=ao4)
    assert r4["attached"] == 0 and r4["no_match"] == 1, r4
    print("skipped signals  : never take an outcome — a hypothetical result "
          "is not a result")

    # basis has no default, and a bogus one is refused.
    try:
        record_outcome(1, "win", 1.0, "vibes", path=ao4)
    except ValueError as e:
        assert "basis must be one of" in str(e)
        print("basis            : required, and only a known denominator")
    else:
        raise AssertionError("record_outcome accepted an unknown basis")

    # ── SCAN ROWS: "scanned, nothing fired" is a fact, not an absence ──
    #
    # Bars 09-30, 10-02, 10-05, 10-06 had no rows for three weeks and the log
    # could not say whether the market was quiet or the scanner never ran. It
    # was the scanner. A scan row makes silence distinguishable from a miss.
    sl = Path(tempfile.mkdtemp()) / "scan.jsonl"
    assert last_scan(sl) is None, "an empty log has no last scan"
    s1 = record_scan("2026-10-06", ["TMO", "PFE"], 0, cfg, path=sl)
    s2 = record_scan("2026-10-06", ["TMO", "PFE"], 0, cfg, path=sl)
    assert s1["seq"] == 1 and s2["seq"] == 2, \
        "two scans of one bar are TWO facts — scan rows are never deduped, " \
        "because each is evidence the scanner ran"
    assert s1["kind"] == "scan" and s1["tickers"] == ["PFE", "TMO"]
    assert last_scan(sl)["seq"] == 2
    record_scan("2026-10-07 15:07:00", ["TMO"], 1, cfg, path=sl)
    assert last_scan(sl)["bar_date"] == "2026-10-07", \
        "a timestamp must key on its DAY, like record_signal"
    assert not verify(sl), verify(sl)
    sm = summary(sl)
    assert sm["scans"] == 3 and sm["bars_scanned"] == 2 and sm["signals"] == 0, sm
    # A scan row must never be mistaken for a signal by anything downstream.
    record_signal("TMO", "Bullish", setup, "taken", "", cfg,
                  bar_date="2026-10-07", path=sl)
    assert attach_outcomes([], path=sl)["attached"] == 0
    assert summary(sl)["signals"] == 1 and summary(sl)["scans"] == 3
    print("scan rows        : per-run heartbeat, never deduped, keyed on the "
          "day, invisible to signal/outcome logic")

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
