"""
iv_snapshot.py — the forward single-name volatility premium, measured one day at a time
======================================================================================

Part B of results/option_iv_preregistration.md (BACKLOG 25).

THE QUESTION. option_backtest.py prices every modelled contract at realised
vol x risk_params.OPT_IV_MULT. The sensitivity sweep (Part A) showed the
SIGN of the live option structure's expectancy depends on that constant and
nothing else: positive with no premium, negative with any. Whether these
names carry a premium -- implied vol above the vol they go on to realise --
is the whole question, and historical single-name IV is not available from
this data source. So it is measured FORWARD: one snapshot per name per
session, graded 21 sessions later.

WHAT A ROW IS
    {"kind": "iv", "date", "ticker", "expiry", "dte", "strike", "spot",
     "iv", "rv20", "source"}
        iv    at-the-money implied vol, in VOL POINTS (45.0 = 45%), the mean
              of the call and put IV at the strike nearest spot, at the
              nearest expiry inside 21-45 DTE (the band option_chain.py
              selects from)
        rv20  trailing 20-session realised vol, same units
    {"kind": "premium", "date", "ticker", "realised", "premium", "sessions"}
        realised  annualised vol over the 21 sessions AFTER the snapshot
        premium   iv - realised: positive means options were priced above
                  what the stock then did (the seller's edge)

THE RULES, FIXED IN THE PRE-REGISTRATION
    - one iv row per (ticker, date); a second is refused, not overwritten
    - a row is refused, and the refusal COUNTED, when DTE is outside 21-45,
      IV is missing or absurd, or the strike is more than 10% from spot
    - a premium row is written only when 21 full sessions after the snapshot
      exist; never a partial; exactly one per snapshot
    - the first READING is at READING_SESSIONS distinct snapshot dates;
      before that report() prints counts and says it is not a reading
    - pooled across names through the daily cross-name mean, whose HAC
      standard error carries the between-name correlation; per name beside it

ONE WRITER: scanner.py, in its post-close pass. Same rule as the forward
log, for the same reason (two writers on one append-only file collide).
No hash chain: nothing here is a claim about a trade.

Stdlib only, like forward_log.py: the scanner hands over plain rows.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

LOG = Path("iv_snapshots.jsonl")

MIN_DTE, MAX_DTE = 21, 45           # the band option_chain.py selects from
ATM_TOL = 0.10                      # strike within 10% of spot
FORWARD_SESSIONS = 21               # same horizon as vrp_check.py
RV_WINDOW = 20
TRADING_DAYS = 252
HAC_LAG = FORWARD_SESSIONS - 1      # overlapping windows -> Newey-West
READING_SESSIONS = 126              # six months of sessions before a reading
MAX_IV_POINTS = 500.0               # above this the quote is garbage, not vol
# THE WINDOW MUST BE WHOLE (external review 2026-10-19, N04; BACKLOG 35).
# Consecutive sessions are at most this many calendar days apart: Thu -> Mon
# over a Friday holiday, or Fri -> Tue over a Monday one. A larger gap means
# the provider is missing a session and the 21 closes are not 21 sessions.
MAX_SESSION_GAP_DAYS = 4


# ---------------------------------------------------------------------------
# File
# ---------------------------------------------------------------------------

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


def _append(row: dict, path: Path | None = None) -> dict:
    p = path or LOG
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


# ---------------------------------------------------------------------------
# Vol arithmetic, stdlib
# ---------------------------------------------------------------------------

def realised_vol_points(closes: list, window: int = RV_WINDOW) -> float | None:
    """Annualised stdev of log returns over the LAST `window` returns, in
    vol points. None when there are not enough closes."""
    cs = [float(c) for c in closes if c is not None and float(c) > 0]
    if len(cs) < window + 1:
        return None
    rets = [math.log(cs[i] / cs[i - 1]) for i in range(len(cs) - window, len(cs))]
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS) * 100.0


def forward_realised_points(closes_after: list, sessions: int = FORWARD_SESSIONS) -> float | None:
    """Annualised vol of the FIRST `sessions` returns after the snapshot:
    closes_after[0] is the snapshot day's close, so the window is the
    `sessions` closes that follow it. None until all of them exist.

    STRICT (N04): a missing, non-finite or non-positive close inside the
    window returns None. It used to be dropped, which compressed time and
    computed a 21-session vol over fewer sessions without saying so."""
    if len(closes_after) < sessions + 1:
        return None
    cs = []
    for c in closes_after[: sessions + 1]:
        try:
            v = float(c)
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(v) and v > 0):
            return None
        cs.append(v)
    rets = [math.log(cs[i] / cs[i - 1]) for i in range(1, sessions + 1)]
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS) * 100.0


def hac_se(x: list, lag: int = HAC_LAG) -> float:
    """Newey-West (Bartlett) standard error of the mean. Same formula as
    vrp_check.hac_se, vendored so this module stays stdlib."""
    e = [float(v) for v in x]
    n = len(e)
    if n < 2:
        return float("nan")
    m = sum(e) / n
    e = [v - m for v in e]
    gamma0 = sum(v * v for v in e) / n
    total = gamma0
    for k in range(1, min(lag, n - 1) + 1):
        w = 1.0 - k / (lag + 1.0)
        total += 2.0 * w * (sum(e[i] * e[i - k] for i in range(k, n)) / n)
    if total <= 0.0:
        total = gamma0
    return math.sqrt(total / n)


# ---------------------------------------------------------------------------
# The snapshot
# ---------------------------------------------------------------------------

def atm_iv_from_chain(calls: list, puts: list, spot: float) -> dict | None:
    """
    The at-the-money implied vol from plain chain rows
    [{"strike", "impliedVolatility"}, ...] (fractions, as the provider gives
    them). Picks the strike nearest spot; averages the call and put IV at
    that strike when both exist. Returns {"strike", "iv"} in vol points, or
    None when nothing usable is within ATM_TOL of spot.
    """
    if not spot or spot <= 0:
        return None
    by_strike: dict = {}
    for side in (calls or [], puts or []):
        for r in side:
            try:
                k = float(r.get("strike")); iv = float(r.get("impliedVolatility"))
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(iv) and iv > 0 and math.isfinite(k) and k > 0):
                continue
            if abs(k - spot) / spot > ATM_TOL:
                continue
            by_strike.setdefault(k, []).append(iv * 100.0)
    if not by_strike:
        return None
    k = min(by_strike, key=lambda s: abs(s - spot))
    ivs = by_strike[k]
    return {"strike": k, "iv": sum(ivs) / len(ivs)}


def record(date: str, ticker: str, expiry: str, dte: int, strike: float,
           spot: float, iv: float, rv20: float | None, source: str,
           path: Path | None = None, refused: dict | None = None) -> dict | None:
    """
    One snapshot row, or None with the reason counted in `refused`.
    """
    def _refuse(why: str):
        if refused is not None:
            refused[why] = refused.get(why, 0) + 1
        return None
    date = str(date)[:10]
    ticker = str(ticker).upper()
    try:
        dte = int(dte); strike = float(strike); spot = float(spot); iv = float(iv)
    except (TypeError, ValueError):
        return _refuse("malformed")
    if not (MIN_DTE <= dte <= MAX_DTE):
        return _refuse("dte outside 21-45")
    if not (math.isfinite(iv) and 0 < iv < MAX_IV_POINTS):
        return _refuse("iv missing or absurd")
    if spot <= 0 or abs(strike - spot) / spot > ATM_TOL:
        return _refuse("strike not at the money")
    if any(r.get("kind") == "iv" and r.get("ticker") == ticker and r.get("date") == date
           for r in read_all(path)):
        return _refuse("already recorded today")
    return _append({"kind": "iv", "date": date, "ticker": ticker, "expiry": str(expiry)[:10],
                    "dte": dte, "strike": round(strike, 4), "spot": round(spot, 4),
                    "iv": round(iv, 4), "rv20": None if rv20 is None else round(float(rv20), 4),
                    "source": source}, path=path)


# ---------------------------------------------------------------------------
# Grading: the premium, 21 sessions later
# ---------------------------------------------------------------------------

def _days_between(a: str, b: str) -> int:
    import datetime as _dt
    return (_dt.date.fromisoformat(b) - _dt.date.fromisoformat(a)).days


def window_after(by_date: dict, anchor: str, sessions: int = FORWARD_SESSIONS,
                 sessions_between=None):
    """
    The snapshot day's close and the `sessions` DISTINCT sessions after it,
    from `by_date` ({"YYYY-MM-DD": close}). Returns (dates, closes, None) or
    (None, None, reason):

      anchor_missing  the history passes over the snapshot date without it
                      (or starts after it): a shifted window is not this
                      snapshot's window, so it is never computed
      unsettled       fewer than `sessions` sessions exist after the anchor
      gap             a session is missing from the provider's history.
                      With `sessions_between(a, b)` injected (the scanner
                      passes its exchange calendar) every consecutive pair
                      must be exactly one session apart, which catches a
                      missing Tuesday. Without it the rule is the calendar
                      gap MAX_SESSION_GAP_DAYS, which catches only a hole
                      next to a weekend; stdlib has no exchange calendar.
      bad_close       a close in the window is missing, non-finite or <= 0

    The review's case: a 22-row history with one date repeated counted as 21
    sessions, and a history that started before the snapshot but skipped its
    date produced a premium for a window that began a day late. Neither can
    here: sessions are distinct dates, and the anchor must be one of them.
    """
    anchor = str(anchor)[:10]
    dates = sorted(by_date)
    if not dates:
        return None, None, "unsettled"
    if anchor not in by_date:
        return None, None, ("anchor_missing" if dates[-1] > anchor else "unsettled")
    after = [d for d in dates if d > anchor]
    if len(after) < sessions:
        return None, None, "unsettled"
    win = [anchor] + after[:sessions]
    for a, b in zip(win, win[1:]):
        if sessions_between is not None:
            if sessions_between(a, b) != 1:
                return None, None, "gap"
        elif _days_between(a, b) > MAX_SESSION_GAP_DAYS:
            return None, None, "gap"
    vals = []
    for d in win:
        try:
            v = float(by_date[d])
        except (TypeError, ValueError):
            return None, None, "bad_close"
        if not (math.isfinite(v) and v > 0):
            return None, None, "bad_close"
        vals.append(v)
    return win, vals, None


def closes_hash(dates: list, closes: list) -> str:
    """Fingerprint of the exact dated closes a premium was computed from."""
    import hashlib
    body = ",".join(f"{d}:{float(c):.4f}" for d, c in zip(dates, closes))
    return hashlib.sha256(body.encode()).hexdigest()[:16]


GRADE_HOLDS = ("anchor_missing", "gap", "bad_close")   # counted, left open, never graded


def grade(fetch_closes, path: Path | None = None, sessions_between=None) -> dict:
    """
    For every iv row without a premium row, `fetch_closes(ticker)` returns
    [{"date", "close"}, ...] ascending; a premium row is written when the
    snapshot date itself and the 21 distinct, gap-free, finite sessions after
    it exist (window_after). One fetch per ticker. Never raises on a row.

    A window that fails validation is HELD, not graded and not voided: it is
    counted under its reason and stays open for a later, better fetch. The
    premium row records the window it was computed from (anchor, first and
    last session) and a hash of the dated closes, so the result can be
    reproduced or disputed.
    """
    rows = read_all(path)
    graded = {(r.get("ticker"), r.get("date")) for r in rows if r.get("kind") == "premium"}
    open_rows = [r for r in rows if r.get("kind") == "iv"
                 and (r.get("ticker"), r.get("date")) not in graded]
    rep = {"graded": 0, "unsettled": 0, "no_data": 0, "failed": 0,
           "anchor_missing": 0, "gap": 0, "bad_close": 0, "detail": []}
    by_tk: dict = {}
    for r in open_rows:
        by_tk.setdefault(r["ticker"], []).append(r)
    for tk, snaps in sorted(by_tk.items()):
        try:
            closes = fetch_closes(tk)
        except Exception as e:                           # noqa: BLE001
            rep["failed"] += len(snaps); rep["detail"].append(f"{tk}: {e}"); continue
        if not closes:
            rep["no_data"] += len(snaps); continue
        by_date: dict = {}
        conflict = None
        for c in closes:
            d = str(c.get("date"))[:10]
            if d in by_date and by_date[d] != c.get("close"):
                conflict = d
            by_date[d] = c.get("close")
        if conflict:
            rep["failed"] += len(snaps)
            rep["detail"].append(f"{tk}: two different closes dated {conflict} — history rejected")
            continue
        for s in snaps:
            try:
                win, vals, why = window_after(by_date, s["date"],
                                              sessions_between=sessions_between)
                if why == "unsettled":
                    rep["unsettled"] += 1; continue
                if why:
                    rep[why] += 1
                    rep["detail"].append(f"{tk} {s['date']}: held, {why}")
                    continue
                realised = forward_realised_points(vals)
                if realised is None:                     # cannot happen past window_after
                    rep["bad_close"] += 1; continue
                _append({"kind": "premium", "date": s["date"], "ticker": tk,
                         "realised": round(realised, 4),
                         "premium": round(float(s["iv"]) - realised, 4),
                         "sessions": FORWARD_SESSIONS,
                         "anchor": win[0], "window_start": win[1], "window_end": win[-1],
                         "closes_hash": closes_hash(win, vals)}, path=path)
                rep["graded"] += 1
            except Exception as e:                       # noqa: BLE001
                rep["failed"] += 1; rep["detail"].append(f"{tk} {s.get('date')}: {e}")
    return rep


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def summary(path: Path | None = None) -> dict:
    rows = read_all(path)
    ivs = [r for r in rows if r.get("kind") == "iv"]
    prems = [r for r in rows if r.get("kind") == "premium"]
    dates = sorted({r["date"] for r in ivs})
    per_name: dict = {}
    for tk in sorted({r["ticker"] for r in ivs}):
        p = sorted((r for r in prems if r["ticker"] == tk), key=lambda r: r["date"])
        xs = [r["premium"] for r in p]
        per_name[tk] = {
            "snapshots": sum(1 for r in ivs if r["ticker"] == tk),
            "graded": len(xs),
            "mean": (sum(xs) / len(xs)) if xs else float("nan"),
            "se_hac": hac_se(xs) if len(xs) >= 2 else float("nan"),
            "pct_positive": (100.0 * sum(1 for x in xs if x > 0) / len(xs)) if xs else float("nan"),
        }
    # Pooled through the daily cross-name mean: its autocorrelation carries
    # the overlap, its construction carries the between-name correlation.
    by_date: dict = {}
    for r in prems:
        by_date.setdefault(r["date"], []).append(r["premium"])
    daily = [sum(v) / len(v) for _, v in sorted(by_date.items())]
    pooled = {"days": len(daily),
              "mean": (sum(daily) / len(daily)) if daily else float("nan"),
              "se_hac": hac_se(daily) if len(daily) >= 2 else float("nan")}
    done = {(r["ticker"], r["date"]) for r in prems}
    unresolved = sorted(r["date"] for r in ivs if (r["ticker"], r["date"]) not in done)
    return {"snapshots": len(ivs), "graded": len(prems), "sessions": len(dates),
            "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
            "names": per_name, "pooled": pooled,
            "unresolved": len(unresolved),
            "oldest_unresolved": unresolved[0] if unresolved else None,
            "is_reading": len(dates) >= READING_SESSIONS}


def report(path: Path | None = None) -> int:
    s = summary(path)
    print("=" * 76)
    print(f"SINGLE-NAME VOLATILITY PREMIUM, FORWARD — {path or LOG}")
    print("=" * 76)
    if not s["snapshots"]:
        print("  empty. The post-close pass has not written a snapshot yet.")
        return 0
    print(f"  {s['snapshots']} snapshots on {s['sessions']} sessions "
          f"({s['first']} -> {s['last']}), {s['graded']} graded, "
          f"{s['unresolved']} unresolved (oldest {s['oldest_unresolved']})")
    print(f"  {'name':<8}{'snaps':>7}{'graded':>8}{'mean':>9}{'HAC se':>9}{'% >0':>7}")
    for tk, v in s["names"].items():
        print(f"  {tk:<8}{v['snapshots']:>7}{v['graded']:>8}{v['mean']:>+9.2f}"
              f"{v['se_hac']:>9.2f}{v['pct_positive']:>7.1f}")
    p = s["pooled"]
    print(f"  pooled   daily cross-name mean over {p['days']} days: "
          f"{p['mean']:+.2f} vol points, HAC se {p['se_hac']:.2f}")
    if not s["is_reading"]:
        print(f"\n  NOT A READING. {s['sessions']} of {READING_SESSIONS} sessions. "
              f"The pre-registration fixes the first reading at six months;")
        print("  the numbers above are counts and running means, not a result.")
    else:
        print(f"\n  A READING ({s['sessions']} sessions >= {READING_SESSIONS}). Report with the SE;")
        print("  the SPX premium was +3.63 (HAC se 0.63) for scale.")
    print("=" * 76)
    return 0


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest() -> int:
    import tempfile
    print("iv_snapshot.py selftest")
    print("=" * 72)
    tmp = Path(tempfile.mkdtemp()) / "iv.jsonl"
    ref: dict = {}

    # ATM pick: nearest strike, call/put averaged, far strikes ignored.
    calls = [{"strike": 95, "impliedVolatility": 0.50}, {"strike": 100, "impliedVolatility": 0.40},
             {"strike": 130, "impliedVolatility": 0.90}]
    puts = [{"strike": 100, "impliedVolatility": 0.44}, {"strike": 101, "impliedVolatility": 0.60}]
    a = atm_iv_from_chain(calls, puts, 100.4)
    assert a["strike"] == 100 and abs(a["iv"] - 42.0) < 1e-9, a
    assert atm_iv_from_chain([{"strike": 130, "impliedVolatility": 0.9}], [], 100.0) is None
    assert atm_iv_from_chain([{"strike": 100, "impliedVolatility": "nan"}], [], 100.0) is None
    print("atm pick         : nearest strike, call/put mean, far strikes ignored")

    # Guards, each counted.
    ok = record("2026-10-08", "NVDA", "2026-11-13", 36, 250.0, 251.0, 48.5, 41.2, "yahoo", path=tmp, refused=ref)
    assert ok and ok["kind"] == "iv" and ok["iv"] == 48.5
    assert record("2026-10-08", "NVDA", "2026-11-13", 36, 250.0, 251.0, 48.5, 41.2, "yahoo", path=tmp, refused=ref) is None
    assert record("2026-10-08", "AAA", "2026-10-24", 16, 100.0, 100.0, 30.0, 25.0, "yahoo", path=tmp, refused=ref) is None
    assert record("2026-10-08", "BBB", "2026-11-27", 50, 100.0, 100.0, 30.0, 25.0, "yahoo", path=tmp, refused=ref) is None
    assert record("2026-10-08", "CCC", "2026-11-13", 36, 100.0, 100.0, 0.0, 25.0, "yahoo", path=tmp, refused=ref) is None
    assert record("2026-10-08", "DDD", "2026-11-13", 36, 100.0, 100.0, 900.0, 25.0, "yahoo", path=tmp, refused=ref) is None
    assert record("2026-10-08", "EEE", "2026-11-13", 36, 120.0, 100.0, 30.0, 25.0, "yahoo", path=tmp, refused=ref) is None
    assert ref == {"already recorded today": 1, "dte outside 21-45": 2,
                   "iv missing or absurd": 2, "strike not at the money": 1}, ref
    assert len(read_all(tmp)) == 1, "a refusal must write nothing"
    print(f"guards           : {sum(ref.values())} refusals, each counted by reason; one row per name per day")

    # Vol arithmetic: a constant +1%/-1% alternation has a known stdev.
    closes = [100.0]
    for i in range(40):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))
    rv = realised_vol_points(closes)
    assert rv is not None and abs(rv - math.log(1.01) * math.sqrt(252) * 100 * (20 / 19) ** 0.5) < 0.5, rv
    assert realised_vol_points(closes[:15]) is None, "too few closes -> None, never a guess"
    assert forward_realised_points(closes[:21]) is None, "20 sessions after is not a closed window"
    assert forward_realised_points(closes[:22]) is not None
    print(f"vol arithmetic   : realised {rv:.1f} pts on a +-1% alternation; windows refuse to settle early")

    # Grading: one premium per snapshot, only when 21 sessions exist, one fetch per name.
    tmp2 = Path(tempfile.mkdtemp()) / "iv2.jsonl"
    record("2026-09-01", "NVDA", "2026-10-02", 31, 100.0, 100.0, 40.0, 30.0, "yahoo", path=tmp2)
    record("2026-09-02", "NVDA", "2026-10-02", 30, 100.0, 100.0, 41.0, 30.0, "yahoo", path=tmp2)
    record("2026-09-01", "AMD", "2026-10-02", 31, 100.0, 100.0, 60.0, 50.0, "yahoo", path=tmp2)
    import datetime as _dt
    d0 = _dt.date(2026, 9, 1)
    series = [{"date": (d0 + _dt.timedelta(days=i)).isoformat(), "close": closes[i]} for i in range(23)]
    calls_made: list = []
    def _fetch(tk):
        calls_made.append(tk)
        return series if tk == "NVDA" else None
    g = grade(_fetch, path=tmp2)
    assert sorted(calls_made) == ["AMD", "NVDA"], calls_made
    assert g["graded"] == 2 and g["no_data"] == 1 and g["unsettled"] == 0, g
    prem = [r for r in read_all(tmp2) if r["kind"] == "premium"]
    assert len(prem) == 2 and all(r["sessions"] == 21 for r in prem)
    assert abs(prem[0]["premium"] - (40.0 - prem[0]["realised"])) < 1e-6
    g2 = grade(_fetch, path=tmp2)
    assert g2["graded"] == 0 and len([r for r in read_all(tmp2) if r["kind"] == "premium"]) == 2, \
        "a graded snapshot must never be graded again"
    # A snapshot with only 20 sessions after it stays unsettled.
    record("2026-09-03", "NVDA", "2026-10-02", 29, 100.0, 100.0, 42.0, 30.0, "yahoo", path=tmp2)
    g3 = grade(_fetch, path=tmp2)
    assert g3["unsettled"] == 1 and g3["graded"] == 0, g3
    print("grading          : one premium per snapshot after 21 sessions, idempotent, "
          "unsettled stays open, one fetch per name")

    # ── THE WINDOW MUST BE WHOLE (external review 2026-10-19, N04) ──
    # Business-day histories so the gap rule is exercised on real spacing.
    import datetime as _dtw
    def _bdays(start: _dtw.date, n: int) -> list:
        out, d = [], start
        while len(out) < n:
            if d.weekday() < 5:
                out.append(d.isoformat())
            d += _dtw.timedelta(days=1)
        return out
    _dates = _bdays(_dtw.date(2026, 9, 1), 30)
    _hist = {d: closes[i] for i, d in enumerate(_dates)}
    _anchor = _dates[2]
    win, vals, why = window_after(_hist, _anchor)
    assert why is None and win[0] == _anchor and len(win) == 22 and win[1] == _dates[3] \
        and win[-1] == _dates[23], (why, win[:2], win[-1])
    # the anchor itself missing, history on both sides: HELD, never a shifted window
    _no_anchor = {d: v for d, v in _hist.items() if d != _anchor}
    assert window_after(_no_anchor, _anchor)[2] == "anchor_missing"
    # history that starts after the snapshot: held the same way
    assert window_after({d: v for d, v in _hist.items() if d > _anchor}, _anchor)[2] == "anchor_missing"
    # history that has not reached the snapshot yet: unsettled, not held
    assert window_after({d: v for d, v in _hist.items() if d < _anchor}, _anchor)[2] == "unsettled"
    # 22 rows dated the same day are ONE session: unsettled
    assert window_after({_anchor: 100.0}, _anchor)[2] == "unsettled"
    assert window_after({d: v for d, v in _hist.items() if d <= _dates[22]}, _anchor)[2] == "unsettled", \
        "20 sessions after the anchor is not a closed window"
    # a missing session inside the window. With the exchange calendar
    # injected a single missing Tuesday (09-15) is a gap; without it only a
    # hole next to a weekend is visible (Fri 09-11 and Mon 09-14 removed:
    # Thu -> Tue is 5 days), and a lone Tuesday is NOT, which is why the
    # scanner injects its calendar.
    def _weekday_sessions(a, b):
        da, db = _dtw.date.fromisoformat(a), _dtw.date.fromisoformat(b)
        return sum(1 for i in range(1, (db - da).days + 1)
                   if (da + _dtw.timedelta(days=i)).weekday() < 5)
    _gappy = {d: v for d, v in _hist.items() if d != _dates[10]}
    assert window_after(_gappy, _anchor, sessions_between=_weekday_sessions)[2] == "gap"
    assert window_after(_hist, _anchor, sessions_between=_weekday_sessions)[2] is None
    _hole = {d: v for d, v in _hist.items() if d not in (_dates[8], _dates[9])}
    assert window_after(_hole, _anchor)[2] == "gap", window_after(_hole, _anchor)
    assert window_after(_gappy, _anchor)[2] is None, "stdlib fallback cannot see a lone missing Tuesday"
    # a bad close inside the window is held, never dropped to compress time
    for bad in (None, 0.0, -1.0, float("nan"), float("inf"), "x"):
        _badh = dict(_hist); _badh[_dates[12]] = bad
        assert window_after(_badh, _anchor)[2] == "bad_close", bad
    assert forward_realised_points([100.0, 0.0] + [100.0] * 20) is None
    assert forward_realised_points([100.0, None] + [100.0] * 20) is None
    print("window           : exact anchor, 21 distinct sessions, no gap > 4 days, finite closes; else HELD")

    # grade() applies it and records the window: the anchor-missing snapshot is
    # counted and left open, the whole one is graded with its window and hash.
    tmp3 = Path(tempfile.mkdtemp()) / "iv3.jsonl"
    record(_anchor, "XYZ", "2026-10-02", 30, 100.0, 100.0, 40.0, 30.0, "yahoo", path=tmp3)
    record(_dates[4], "XYZ", "2026-10-02", 30, 100.0, 100.0, 41.0, 30.0, "yahoo", path=tmp3)
    _series_no_anchor = [{"date": d, "close": v} for d, v in sorted(_no_anchor.items())]
    g4 = grade(lambda tk: _series_no_anchor, path=tmp3)
    assert g4["anchor_missing"] == 1 and g4["graded"] == 1, g4
    _p4 = [r for r in read_all(tmp3) if r["kind"] == "premium"]
    assert len(_p4) == 1 and _p4[0]["anchor"] == _dates[4] and _p4[0]["window_start"] == _dates[5] \
        and _p4[0]["window_end"] == _dates[25] and len(_p4[0]["closes_hash"]) == 16, _p4
    g5 = grade(lambda tk: [{"date": d, "close": v} for d, v in sorted(_hist.items())], path=tmp3)
    assert g5["graded"] == 1 and g5["anchor_missing"] == 0, (g5, "a held snapshot is graded by a later whole fetch")
    _dup = [{"date": _dates[0], "close": 100.0}, {"date": _dates[0], "close": 101.0}]
    record(_dates[6], "DUP", "2026-10-02", 30, 100.0, 100.0, 41.0, 30.0, "yahoo", path=tmp3)
    g6 = grade(lambda tk: _dup if tk == "DUP" else None, path=tmp3)
    assert g6["failed"] == 1 and any("two different closes" in d for d in g6["detail"]), g6
    s3 = summary(tmp3)
    assert s3["unresolved"] == 1 and s3["oldest_unresolved"] == _dates[6], s3
    print("grade holds      : anchor missing -> held and counted, graded on a later whole fetch; "
          "conflicting duplicates reject the history; unresolved visible")

    # Reading gate and pooled SE.
    s = summary(tmp2)
    assert s["names"]["NVDA"]["graded"] == 2 and s["is_reading"] is False
    assert s["pooled"]["days"] == 2 and math.isfinite(s["pooled"]["se_hac"])
    import io as _io, contextlib as _cl
    buf = _io.StringIO()
    with _cl.redirect_stdout(buf):
        report(tmp2)
    assert "NOT A READING" in buf.getvalue(), buf.getvalue()
    # HAC on a POSITIVELY autocorrelated series (slow cycle, period >> lag)
    # is larger than iid; that is the overlap correction doing its job.
    ac = [math.sin(i / 40.0) for i in range(200)]
    iid = (sum((v - sum(ac) / 200) ** 2 for v in ac) / 199) ** 0.5 / 200 ** 0.5
    assert hac_se(ac) > 2 * iid, (hac_se(ac), iid)
    print(f"reading gate     : NOT A READING below {READING_SESSIONS} sessions; HAC se > iid on "
          f"overlapping windows")

    print("=" * 72)
    print("All self-tests passed.")
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    return report()


if __name__ == "__main__":
    sys.exit(main())
