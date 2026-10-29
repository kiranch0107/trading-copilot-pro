#!/usr/bin/env python3
"""
spread_log.py — the always-on defined-risk spread arms, recorded forward
(results/spread_preregistration.md, BACKLOG 34 E; owner 2026-10-28).

THE QUESTION. spread_backtest.py run 3 (the calendar-clock engine) passed
five of eight arms against its pre-registered bar. Every number in that
file rests on a MODELLED credit: Black-Scholes on the observed ^VIX / ^VXN
close with a linear skew. The one thing a forward record can settle inside
a year is whether that model prices what the market actually quotes. So
the primary forward question is the CREDIT RATIO, market mid over model,
per cycle; the realised P&L per arm is recorded beside it and read as a
path (drawdown, worst cycle), not as a mean, because a mean of ~5 %/cycle
noise needs decades (the pre-registration says how many).

WHAT IS RECORDED. One `cycle` row per arm whenever the arm has no open
cycle, from the scanner's post-close pass: the entry session, the spot
close, the vol index close, the first listed expiry at least DTE_DAYS
away, the legs spread_backtest.build() chose, the model credit at the
backtest's REALISTIC_SKEW (and flat), the gross the bid-ask is charged on,
and the market's own quotes on those legs (bid/ask per leg, mid credit,
executable credit). No market quote means the row is written with
`market: null` and the reason; the model side still grades.

WHAT IS GRADED. On the first post-close pass with a settled close ON the
expiry date: owed = intrinsic at that close (settle(), the one copy the
backtest also uses), P&L on the model credit after the backtest's cost,
and P&L on the executable market credit. A missing expiry session is HELD
and voided after NO_BARS_DAYS; a spot that no longer matches the recorded
one is a split: void.

ONE WRITER. scanner.py, post-close only, like basket_log.jsonl. The file is
hash-chained, append-only, committed by scanner.yml, and this module stays
stdlib so the chain can be read anywhere.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import risk_params as rp

LOG = Path("spread_log.jsonl")
GENESIS = "0" * 32

# ── the arms, exactly the backtest's (--skew-sweep default arms) ──
PUT_SPREAD = "put_spread"
IRON_CONDOR = "iron_condor"
TICKERS = ("SPY", "QQQ")
STRUCTURES = (PUT_SPREAD, IRON_CONDOR)
WIDTHS = (10.0, 5.0)
OTM_PCT = 5.0
VOL_INDEX = {"SPY": "^VIX", "QQQ": "^VXN"}
ARMS = [{"ticker": tk, "structure": st, "width": w, "otm_pct": OTM_PCT}
        for tk in TICKERS for st in STRUCTURES for w in WIDTHS]

DTE_DAYS = 30               # the first listed expiry at least this many calendar days out
NO_BARS_DAYS = 45           # an expiry still unsettled this long after its date -> void
SPLIT_TOL = 0.02            # recorded vs fetched entry close beyond this -> void:split
# Filling at mid and crossing to the executable side pays half the quoted
# width. One home for it; spread_backtest imports it from here.
CROSS_FRAC = 0.5
# The share of the bid-ask ceiling a real SPY/QQQ contract is assumed to
# quote: the backtest's own default (--cost-frac 0.5), so the model P&L
# here is the P&L the verdict was read on.
COST_FRAC = 0.5
ACCOUNT = float(rp.DEFAULT_ACCOUNT_SIZE)
FIRST_READ_CYCLES = 6       # per arm: reported with its SE, whatever it says
RESULT_CYCLES = 12          # per arm: not a result before this


def arm_key(arm: dict) -> str:
    """The backtest's own arm label, so the two records read on one key."""
    return f"{arm['ticker']} {arm['structure']} w{float(arm['width']):.0f} otm{float(arm['otm_pct']):.0f}"


def settle(legs, spot_at_exit: float) -> float:
    """Intrinsic value owed at expiry, per share. Positive = we owe."""
    owed = 0.0
    for right, strike, qty in legs:
        intr = (max(0.0, spot_at_exit - strike) if right == "CALL"
                else max(0.0, strike - spot_at_exit))
        owed -= qty * intr
    return owed


def market_credit(legs, quotes: dict) -> tuple[dict | None, str | None]:
    """
    The market's price for the structure from per-leg quotes
    {(right, strike): (bid, ask)}. `credit_mid` fills every leg at mid;
    `credit_fill` sells the short legs at the bid and buys the long legs at
    the ask, the executable side. None and the reason when any leg lacks a
    usable quote (missing, bid <= 0, or ask below bid).
    """
    out, mid_credit, fill_credit = [], 0.0, 0.0
    for right, strike, qty in legs:
        q = quotes.get((right, float(strike)))
        if q is None:
            return None, f"{right} {strike:g} unquoted"
        try:
            bid, ask = float(q[0]), float(q[1])
        except (TypeError, ValueError, IndexError):
            return None, f"{right} {strike:g} unquoted"
        if not (math.isfinite(bid) and math.isfinite(ask)) or bid <= 0 or ask < bid:
            return None, f"{right} {strike:g} no usable quote (bid {bid}, ask {ask})"
        mid = (bid + ask) / 2.0
        mid_credit -= qty * mid
        fill_credit -= qty * (bid if qty < 0 else ask)
        out.append({"right": right, "strike": float(strike), "qty": int(qty),
                    "bid": round(bid, 4), "ask": round(ask, 4)})
    return {"legs": out, "credit_mid": round(mid_credit, 4),
            "credit_fill": round(fill_credit, 4)}, None


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

def open_cycles(path: Path | None = None) -> dict:
    """{arm key: cycle row} for every cycle without an outcome."""
    rows = read_all(path)
    done = {r.get("ref_seq") for r in rows if r.get("kind") == "cycle_outcome"}
    return {r["arm"]: r for r in rows if r.get("kind") == "cycle" and r["seq"] not in done}


def record(entry: str, arm: dict, spot: float, vol_pts: float, expiry: str,
           legs: list, model: dict, market: dict | None = None,
           market_reason: str | None = None, path: Path | None = None,
           refused: dict | None = None) -> dict | None:
    """
    One `cycle` row, or None with the reason counted in `refused`: the arm
    already has an open cycle, the expiry is not at least DTE_DAYS after the
    entry, a non-finite spot / vol / credit, or malformed legs. `model` is
    spread_backtest.build()'s dict at the backtest's skew plus `credit_flat`
    (skew 0) and `skew`; the cost is the backtest's: COST_FRAC of the
    bid-ask ceiling, times CROSS_FRAC, on the gross.
    """
    def _refuse(why: str):
        if refused is not None:
            refused[why] = refused.get(why, 0) + 1
        return None

    entry = str(entry)[:10]
    expiry = str(expiry)[:10]
    key = arm_key(arm)
    try:
        days = (date.fromisoformat(expiry) - date.fromisoformat(entry)).days
    except ValueError:
        return _refuse("bad date")
    if days < DTE_DAYS:
        return _refuse("expiry too near")
    if key in open_cycles(path):
        return _refuse("already open")
    try:
        spot = float(spot); vol_pts = float(vol_pts)
        credit = float(model["credit"]); flat = float(model["credit_flat"])
        gross = float(model["gross"]); max_loss = float(model["max_loss"])
    except (KeyError, TypeError, ValueError):
        return _refuse("model incomplete")
    if not all(math.isfinite(x) for x in (spot, vol_pts, credit, flat, gross, max_loss)) \
            or spot <= 0 or vol_pts <= 0 or gross <= 0:
        return _refuse("not finite")
    clean_legs = []
    try:
        for right, strike, qty in legs:
            if right not in ("PUT", "CALL") or int(qty) not in (-1, 1) or not float(strike) > 0:
                return _refuse("legs malformed")
            clean_legs.append([right, float(strike), int(qty)])
    except (TypeError, ValueError):
        return _refuse("legs malformed")
    if not clean_legs:
        return _refuse("legs malformed")
    cost = COST_FRAC * rp.MAX_OPTION_SPREAD_PCT / 100.0 * CROSS_FRAC * gross
    return append("cycle", {
        "arm": key, "ticker": arm["ticker"], "structure": arm["structure"],
        "width": float(arm["width"]), "otm_pct": float(arm["otm_pct"]),
        "entry": entry, "spot": round(spot, 4),
        "vol_index": VOL_INDEX.get(arm["ticker"]), "vol_pts": round(vol_pts, 4),
        "expiry": expiry, "days": days, "legs": clean_legs,
        "model": {"skew": float(model.get("skew", 0.0)), "credit": round(credit, 4),
                  "credit_flat": round(flat, 4), "gross": round(gross, 4),
                  "max_loss": round(max_loss, 4), "cost": round(cost, 4)},
        "market": market, "market_reason": None if market else (market_reason or "no quotes"),
    }, path=path)


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def grade(fetch_closes, path: Path | None = None, today: date | None = None) -> dict:
    """
    Write a `cycle_outcome` for every open cycle whose expiry session has
    settled. `fetch_closes(ticker)` returns [{"date", "close"}] ascending, or
    None. One fetch per ticker per pass. Never raises on a row.
    """
    today = today or date.today()
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

    for key, r in sorted(open_cycles(path).items()):
        try:
            by = _closes(r["ticker"])
            age = (today - date.fromisoformat(r["expiry"])).days
            reason = None
            if not by:
                reason = "no_data"
            elif r["entry"] in by and abs(by[r["entry"]] - float(r["spot"])) / float(r["spot"]) > SPLIT_TOL:
                reason = "split"
            elif r["expiry"] not in by:
                reason = "gap" if any(d > r["expiry"] for d in by) else "unsettled"
            if reason == "unsettled" or (reason in ("no_data", "gap") and age <= NO_BARS_DAYS):
                rep["unsettled" if reason == "unsettled" else "held"] += 1
                if reason != "unsettled":
                    rep["detail"].append(f"{key} {r['entry']}: held ({reason})")
                continue
            if reason is not None:
                append("cycle_outcome", {"ref_seq": r["seq"], "arm": key, "entry": r["entry"],
                                         "expiry": r["expiry"], "outcome": "void", "reason": reason},
                       path=path)
                rep["void"] += 1
                rep["detail"].append(f"{key} {r['entry']}: void ({reason})")
                continue
            close = by[r["expiry"]]
            legs = [(lg[0], float(lg[1]), int(lg[2])) for lg in r["legs"]]
            owed = settle(legs, close)
            m = r["model"]
            pnl_model = (float(m["credit"]) - owed - float(m["cost"])) * 100.0
            mk = r.get("market")
            pnl_market = (float(mk["credit_fill"]) - owed) * 100.0 if mk else None
            ratio = (float(mk["credit_mid"]) / float(m["credit"])
                     if mk and float(m["credit"]) > 0 else None)
            append("cycle_outcome", {
                "ref_seq": r["seq"], "arm": key, "entry": r["entry"], "expiry": r["expiry"],
                "outcome": "graded", "settle_close": round(close, 4), "owed": round(owed, 4),
                "pnl_model": round(pnl_model, 2), "pnl_model_pct": round(pnl_model / ACCOUNT * 100.0, 4),
                "pnl_market": None if pnl_market is None else round(pnl_market, 2),
                "pnl_market_pct": None if pnl_market is None else round(pnl_market / ACCOUNT * 100.0, 4),
                "ratio": None if ratio is None else round(ratio, 4),
            }, path=path)
            rep["graded"] += 1
            rep["detail"].append(f"{key} {r['entry']}: settled {close:.2f}, model {pnl_model:+.0f} $"
                                 + (f", market {pnl_market:+.0f} $, ratio {ratio:.2f}" if mk else ", no market quote"))
        except Exception as e:                           # noqa: BLE001
            rep["failed"] += 1
            rep["detail"].append(f"{key}: grade failed: {e}")
    return rep


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _mean_se(xs: list) -> tuple[float, float]:
    n = len(xs)
    if not n:
        return float("nan"), float("nan")
    m = sum(xs) / n
    se = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1) / n) if n > 1 else float("nan")
    return m, se


def _max_dd_pct(pnls: list) -> float:
    eq, peak, dd = ACCOUNT, ACCOUNT, 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    return dd * 100.0


def summary(path: Path | None = None) -> dict:
    rows = read_all(path)
    cycles = [r for r in rows if r.get("kind") == "cycle"]
    outs = {r["ref_seq"]: r for r in rows if r.get("kind") == "cycle_outcome"}
    arms: dict = {}
    all_ratios: list = []
    for arm in ARMS:
        key = arm_key(arm)
        mine = [c for c in cycles if c["arm"] == key]
        graded = [outs[c["seq"]] for c in mine if c["seq"] in outs and outs[c["seq"]]["outcome"] == "graded"]
        ratios = [float(o["ratio"]) for o in graded if o.get("ratio") is not None]
        all_ratios += ratios
        model = [float(o["pnl_model"]) for o in graded]
        market = [float(o["pnl_market"]) for o in graded if o.get("pnl_market") is not None]
        rm, rse = _mean_se(ratios)
        mm, _ = _mean_se([p / ACCOUNT * 100.0 for p in model])
        km, _ = _mean_se([p / ACCOUNT * 100.0 for p in market])
        arms[key] = {
            "cycles": len(mine), "graded": len(graded),
            "void": sum(1 for c in mine if c["seq"] in outs and outs[c["seq"]]["outcome"] == "void"),
            "open": sum(1 for c in mine if c["seq"] not in outs),
            "quoted": sum(1 for c in mine if c.get("market")),
            "ratio_n": len(ratios), "ratio_mean": rm, "ratio_se": rse,
            "model_mean_pct": mm, "market_mean_pct": km,
            "model_worst_pct": min([p / ACCOUNT * 100.0 for p in model], default=float("nan")),
            "market_worst_pct": min([p / ACCOUNT * 100.0 for p in market], default=float("nan")),
            "model_max_dd_pct": _max_dd_pct(model) if model else float("nan"),
            "market_max_dd_pct": _max_dd_pct(market) if market else float("nan"),
        }
    pm, pse = _mean_se(all_ratios)
    # QUOTE QUALITY (review 2026-10-28 F4): the after-close bid/ask is the one
    # unknown the pre-registration names. Which legs could not be quoted, and
    # why, per reason, over every cycle recorded.
    unquoted: dict = {}
    for c in cycles:
        if not c.get("market"):
            why = str(c.get("market_reason") or "no quotes")
            unquoted[why] = unquoted.get(why, 0) + 1
    return {"arms": arms, "cycles": len(cycles), "graded": sum(a["graded"] for a in arms.values()),
            "unquoted": unquoted, "unquoted_cycles": sum(unquoted.values()),
            "ratio_pooled_mean": pm, "ratio_pooled_se": pse, "ratio_pooled_n": len(all_ratios),
            "first": cycles[0]["entry"] if cycles else None,
            "last": cycles[-1]["entry"] if cycles else None,
            "problems": verify(path)}


def report(path: Path | None = None) -> int:
    s = summary(path)
    print("=" * 96)
    print(f"SPREAD ARMS, FORWARD — {path or LOG}")
    print("=" * 96)
    if not s["cycles"]:
        print("  empty. Nothing has been recorded yet.")
        return 0
    f = lambda x, w=7: f"{'n/a':>{w}}" if x != x else f"{x:>+{w}.2f}"       # noqa: E731
    print(f"  cycles {s['cycles']} ({s['first']} .. {s['last']})   graded {s['graded']}")
    print(f"  {'arm':<26}{'cyc':>4}{'grd':>4}{'open':>5}{'void':>5}{'quoted':>7}"
          f"{'ratio':>8}{'+/-':>7}{'model%':>8}{'mkt%':>8}{'worstM%':>9}{'ddM%':>7}")
    for key, a in s["arms"].items():
        se = "n/a" if a["ratio_se"] != a["ratio_se"] else f"{a['ratio_se']:.2f}"
        print(f"  {key:<26}{a['cycles']:>4}{a['graded']:>4}{a['open']:>5}{a['void']:>5}{a['quoted']:>7}"
              f"{f(a['ratio_mean'], 8)}{se:>7}{f(a['model_mean_pct'], 8)}{f(a['market_mean_pct'], 8)}"
              f"{f(a['model_worst_pct'], 9)}{f(a['model_max_dd_pct'], 7)}")
    pse = "n/a" if s["ratio_pooled_se"] != s["ratio_pooled_se"] else f"{s['ratio_pooled_se']:.3f}"
    print(f"  credit ratio, market mid / model, pooled: {f(s['ratio_pooled_mean'], 6)} +/- {pse} "
          f"on {s['ratio_pooled_n']} quoted cycles")
    if s["unquoted_cycles"]:
        _why = ", ".join(f"{k}: {v}" for k, v in sorted(s["unquoted"].items(), key=lambda kv: -kv[1]))
        print(f"  unquoted cycles {s['unquoted_cycles']} of {s['cycles']} (after-close quotes; by reason): {_why}")
    else:
        print(f"  every cycle quoted ({s['cycles']} of {s['cycles']})")
    print(f"  NOT a result before {RESULT_CYCLES} graded cycles per arm (results/spread_preregistration.md);")
    print(f"  first reading at {FIRST_READ_CYCLES}, with its SE, whatever it says. The mean return is not")
    print("  a readable quantity on this record; the ratio and the path are.")
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
    print("spread_log.py selftest")
    print("=" * 72)
    assert len(ARMS) == 8 and arm_key(ARMS[0]) == "SPY put_spread w10 otm5" \
        and arm_key(ARMS[-1]) == "QQQ iron_condor w5 otm5", [arm_key(a) for a in ARMS]
    tmp = Path(tempfile.mkdtemp()) / "s.jsonl"
    arm = ARMS[0]                                   # SPY put_spread w10
    legs = [("PUT", 380.0, -1), ("PUT", 370.0, 1)]
    model = {"skew": 20.0, "credit": 1.20, "credit_flat": 1.10, "gross": 3.00, "max_loss": 8.80}
    quotes = {("PUT", 380.0): (1.95, 2.05), ("PUT", 370.0): (0.80, 0.90)}
    mk, why = market_credit(legs, quotes)
    assert why is None and abs(mk["credit_mid"] - 1.15) < 1e-9 and abs(mk["credit_fill"] - 1.05) < 1e-9, (mk, why)
    assert market_credit(legs, {("PUT", 380.0): (1.95, 2.05)})[1] == "PUT 370 unquoted"
    assert "no usable quote" in market_credit(legs, {**quotes, ("PUT", 370.0): (0.90, 0.80)})[1]
    assert "no usable quote" in market_credit(legs, {**quotes, ("PUT", 380.0): (0.0, 0.05)})[1]
    print("market credit    : mid and executable credits from per-leg quotes; crossed, zero or missing refused")

    ref: dict = {}
    r1 = record("2026-11-02", arm, 400.0, 18.5, "2026-12-02", legs, model, mk, path=tmp, refused=ref)
    assert r1 and r1["kind"] == "cycle" and r1["arm"] == "SPY put_spread w10 otm5" and r1["days"] == 30 \
        and r1["vol_index"] == "^VIX" and abs(r1["model"]["cost"] - 0.5 * 0.08 * 0.5 * 3.0) < 1e-9 \
        and r1["market"]["credit_fill"] == 1.05 and r1["market_reason"] is None, r1
    assert record("2026-11-02", arm, 400.0, 18.5, "2026-12-02", legs, model, mk, path=tmp, refused=ref) is None
    assert record("2026-11-02", ARMS[1], 400.0, 18.5, "2026-12-01", legs, model, path=tmp, refused=ref) is None
    assert record("2026-11-02", ARMS[1], float("nan"), 18.5, "2026-12-02", legs, model, path=tmp, refused=ref) is None
    assert record("2026-11-02", ARMS[1], 400.0, 18.5, "2026-12-02", [("PUT", 380.0, -2)], model, path=tmp, refused=ref) is None
    assert record("2026-11-02", ARMS[1], 400.0, 18.5, "2026-12-02", legs, {"credit": 1.0}, path=tmp, refused=ref) is None
    assert ref == {"already open": 1, "expiry too near": 1, "not finite": 1, "legs malformed": 1,
                   "model incomplete": 1}, ref
    r2 = record("2026-11-02", ARMS[1], 400.0, 18.5, "2026-12-02", [("PUT", 380.0, -1), ("PUT", 375.0, 1)],
                dict(model, max_loss=3.8), None, "PUT 375 unquoted", path=tmp, refused=ref)
    assert r2 and r2["market"] is None and r2["market_reason"] == "PUT 375 unquoted", r2
    assert set(open_cycles(tmp)) == {"SPY put_spread w10 otm5", "SPY put_spread w5 otm5"}
    assert not verify(tmp)
    print("record           : one open cycle per arm; near expiry, NaN, bad legs, partial model refused; unquoted kept")

    # ── grading: settlement ON the expiry close, the model and market P&L ──
    sess = ["2026-11-02", "2026-11-03", "2026-12-01", "2026-12-02", "2026-12-03"]
    feed = lambda closes: [{"date": d, "close": c} for d, c in zip(sess, closes)]   # noqa: E731
    fetched: list = []
    rep = grade(lambda tk: (fetched.append(tk) or feed([400.0, 401.0, 379.0, 376.0, 390.0])),
                path=tmp, today=date(2026, 12, 3))
    assert rep["graded"] == 2 and fetched == ["SPY"], (rep, fetched)
    outs = [r for r in read_all(tmp) if r.get("kind") == "cycle_outcome"]
    o1 = next(o for o in outs if o["ref_seq"] == r1["seq"])
    # SPY 376 at expiry: short 380 put owes 4, long 370 worth 0 -> owed 4.00
    assert o1["outcome"] == "graded" and o1["settle_close"] == 376.0 and abs(o1["owed"] - 4.0) < 1e-9, o1
    assert abs(o1["pnl_model"] - (1.20 - 4.0 - 0.06) * 100) < 1e-6 and abs(o1["pnl_market"] - (1.05 - 4.0) * 100) < 1e-6, o1
    assert abs(o1["ratio"] - 1.15 / 1.20) < 1e-4 and abs(o1["pnl_model_pct"] - o1["pnl_model"] / ACCOUNT * 100) < 1e-6
    o2 = next(o for o in outs if o["ref_seq"] == r2["seq"])
    assert o2["pnl_market"] is None and o2["ratio"] is None and abs(o2["owed"] - 4.0) < 1e-9, o2
    assert not open_cycles(tmp), "a graded cycle is closed; the arm may open again"
    assert settle(legs, 390.0) == 0.0 and abs(settle(legs, 300.0) - 10.0) < 1e-9, "settle: worthless above, width below"
    print("grade            : settles on the expiry close; model P&L after cost, market P&L on the fill, ratio")

    # ── unsettled, held (gap / no data), void after NO_BARS_DAYS, split ──
    r3 = record("2026-12-03", arm, 390.0, 20.0, "2027-01-04", legs, model, mk, path=tmp)
    rep = grade(lambda tk: feed([400.0, 401.0, 379.0, 376.0, 390.0]), path=tmp, today=date(2026, 12, 20))
    assert rep == {"graded": 0, "void": 0, "unsettled": 1, "held": 0, "no_data": 0, "failed": 0, "detail": []}, rep
    holed = feed([400.0, 401.0, 379.0, 376.0, 390.0]) + [{"date": "2027-01-05", "close": 395.0}]
    rep = grade(lambda tk: holed, path=tmp, today=date(2027, 1, 6))
    assert rep["held"] == 1 and rep["graded"] == 0 and "gap" in rep["detail"][0], rep
    rep = grade(lambda tk: None, path=tmp, today=date(2027, 1, 7))
    assert rep["held"] == 1 and "no_data" in rep["detail"][0], rep
    rep = grade(lambda tk: holed, path=tmp, today=date(2027, 2, 20))
    v = [r for r in read_all(tmp) if r.get("kind") == "cycle_outcome" and r["ref_seq"] == r3["seq"]]
    assert rep["void"] == 1 and v and v[0]["outcome"] == "void" and v[0]["reason"] == "gap", (rep, v)
    r4 = record("2026-12-03", arm, 390.0, 20.0, "2027-01-04", legs, model, mk, path=tmp)
    split = [{"date": "2026-12-03", "close": 195.0}, {"date": "2027-01-04", "close": 190.0}]
    rep = grade(lambda tk: split, path=tmp, today=date(2027, 1, 5))
    v4 = [r for r in read_all(tmp) if r.get("kind") == "cycle_outcome" and r["ref_seq"] == r4["seq"]]
    assert rep["void"] == 1 and v4[0]["reason"] == "split", (rep, v4)
    print("held / void      : unsettled before expiry; a missing expiry session is held then void; a split is void")

    # ── the reading ──
    s = summary(tmp)
    a = s["arms"]["SPY put_spread w10 otm5"]
    assert a["cycles"] == 3 and a["graded"] == 1 and a["void"] == 2 and a["open"] == 0 and a["quoted"] == 3, a
    assert abs(a["ratio_mean"] - 1.15 / 1.20) < 1e-4 and a["ratio_se"] != a["ratio_se"]
    assert abs(a["model_worst_pct"] - o1["pnl_model_pct"]) < 1e-6 and a["model_max_dd_pct"] > 5.0, a
    assert s["ratio_pooled_n"] == 1 and s["arms"]["SPY put_spread w5 otm5"]["ratio_n"] == 0
    assert s["unquoted"] == {"PUT 375 unquoted": 1} and s["unquoted_cycles"] == 1, s["unquoted"]
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = report(tmp)
    assert rc == 0 and "NOT a result before 12" in buf.getvalue() and "chain intact" in buf.getvalue()
    assert "unquoted cycles 1 of 4" in buf.getvalue() and "PUT 375 unquoted: 1" in buf.getvalue(), buf.getvalue()
    # tamper
    lines = tmp.read_text().splitlines()
    rec = json.loads(lines[0]); rec["spot"] = 1.0
    tmp.write_text("\n".join([json.dumps(rec)] + lines[1:]) + "\n")
    assert any("hash mismatch" in p for p in verify(tmp))
    print("reading          : per-arm ratio with SE, model and market means, worst and drawdown; tamper caught")
    print("=" * 72)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--path", default=None)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    p = Path(a.path) if a.path else None
    if a.verify:
        probs = verify(p)
        print("\n".join(probs) if probs else f"chain intact across {len(read_all(p))} rows")
        return 1 if probs else 0
    return report(p)


if __name__ == "__main__":
    sys.exit(main())
