"""
Show the SETUP behind real trades — the winners and the losers, side by side.

STAGE 1 OF 3, AND ONLY STAGE 1.

    1. THIS FILE   show complete setups for real trades that worked and failed
    2. (next)      define buckets as explicit rules, from what the cases suggest
    3. (next)      score every bucket across ALL trades, not just the cases

Stage 1 GENERATES hypotheses. It cannot test them, and the split matters: with
ten-odd parameters and a handful of cases, SOMETHING always differs between a
winner and a loser. That difference is those two trades until it is scored
against the full population in stage 3.

So read this file for what it is — the raw material for your judgement about
which distinctions are worth encoding — and not as evidence that any of them
work.

WHAT A "SETUP" IS HERE

Everything signal_core.evaluate() saw and built at the SIGNAL BAR: the four
named gates and their detail, the base conditions (EMA stack, MACD, RSI band,
participation), the constructed levels (entry, stop, target, planned R:R), and
the strength/quality flags. The entry fills on the NEXT bar's open, so nothing
in the setup could have used information the decision did not have.

Derived readings are shown in the instrument's own units — distances in ATR,
ATR as a percent of price — because 3.60 means nothing across tickers and
2.5% of price means the same thing everywhere.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import sys

import numpy as np

import backtest as bt

# TWENTY NAMES, EVERY ONE ALREADY SPENT. Deliberate.
#
# Stage 1 forms hypotheses by LOOKING at these setups, which burns them for any
# later test — you cannot find a pattern in a name and then confirm the pattern
# on that same name. So the cases come from ground already burned: the 12-name
# OOS set, three swept high-beta names, and five from spent tranche A.
#
# Chosen to span VOLATILITY, not sector, because ATR% is the live bucket
# candidate and a set that is all high-beta semis would make every case look the
# same. MU and AMD sit at one end, COST and MCD at the other.
#
# Any name NOT listed here stays clean, and clean names are the only thing that
# could ever make stage 3 mean something. See data_reservation.py.
DEFAULT_TICKERS = (
    # the 12-name OOS set — spent on the 591-trade validation
    "GOOGL,AVGO,AMD,NFLX,CRM,ADBE,QCOM,MU,ORCL,NOW,PANW,LRCX,"
    # swept in Aug 2026, high beta
    "TSLA,NVDA,META,"
    # spent tranche A — lower beta, different sectors, wider ATR spread
    "INTC,IBM,DIS,COST,MCD"
)


def collect(tickers: list[str], years: int) -> list[dict]:
    cfg = dict(bt.DEFAULTS, tickers=tickers, years=years)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        trades = bt.run(cfg) or []
    return [t for t in trades if t.get("r") is not None and t.get("setup")]


def _spread(group: list[dict], n: int) -> list[dict]:
    """n cases spread evenly across the sample period, oldest to newest.

    Not the first n (recency-free but era-biased) and not a random n (needs a
    seed, and a seed is a knob). Evenly spaced is deterministic and covers the
    whole window, so a regime that only existed in 2019 cannot supply every
    case.
    """
    g = sorted(group, key=lambda t: str(t.get("entry_date")))
    if len(g) <= n:
        return g
    if n <= 1:
        return [g[len(g) // 2]]
    step = (len(g) - 1) / (n - 1)
    return [g[round(i * step)] for i in range(n)]


def pick(trades: list[dict], ticker: str, n_each: int = 1) -> dict:
    """
    Trades that REACHED THEIR TARGET vs trades that HIT THEIR STOP.

    NOT the top and bottom by R, which is what this did first and why run 2
    had to be thrown away.

    R is measured against the ACTUAL fill: risk = |fill - stop|, and the fill
    is the next bar's open, which nothing at the signal bar knows. Of the 37
    target-exits in run 2, THIRTY-SEVEN filled toward the stop and none filled
    at or better than plan; the median winner kept 56% of its planned risk
    distance and the largest kept 21%. A shrinking denominator is what makes an
    18 R trade. Measured against the planned stop instead, the same winners ran
    +1.67 to +3.79 R — the spread collapsed 7.3x.

    The asymmetry is structural, not bad luck: a loser exits AT the stop, so it
    books -1.00 R however it filled. Fill luck can only ever appear on the
    winning side. Ranking by R therefore ranks fill accidents, and comparing
    the setups behind those accidents against the setups behind the losers
    answers a question nobody asked.

    Target-vs-stop is the binary the strategy actually faces. Timeouts are
    neither and are excluded rather than assigned to a side.
    """
    own = [t for t in trades if t.get("ticker") == ticker]
    if not own:
        return {"ticker": ticker, "winners": [], "losers": [], "n": 0}
    wins = [t for t in own if t.get("outcome") == "win"]
    losses = [t for t in own if t.get("outcome") == "loss"]
    return {"ticker": ticker, "n": len(own),
            "n_win": len(wins), "n_loss": len(losses),
            "n_timeout": sum(1 for t in own if t.get("outcome") == "timeout"),
            "winners": _spread(wins, n_each), "losers": _spread(losses, n_each),
            "avg_r": float(np.mean([t["r"] for t in own]))}


def derived(t: dict) -> dict:
    """Setup readings in units that compare across tickers."""
    s = t["setup"]
    atr = float(s.get("atr") or 0)
    price = float(s.get("price") or 0)
    e20, e50 = float(s.get("ema20") or 0), float(s.get("ema50") or 0)
    entry, stop = float(s.get("entry") or 0), float(s.get("stop") or 0)
    return {
        "atr_pct": (atr / price * 100.0) if price else float("nan"),
        "px_vs_ema20_atr": ((price - e20) / atr) if atr else float("nan"),
        "ema20_vs_ema50_atr": ((e20 - e50) / atr) if atr else float("nan"),
        "stop_pct": (abs(entry - stop) / entry * 100.0) if entry else float("nan"),
    }


def fill_stats(t: dict) -> dict:
    """What the FILL did to the risk denominator, and R without that effect.

    `setup` carries the levels the signal planned; the record's own `entry` and
    `stop` are what the trade actually got. fill_offset is the actual risk as a
    fraction of planned, minus one — so -0.44 means the next open landed 44% of
    the way to the stop and the denominator shrank to 56% of plan.
    """
    s = t.get("setup") or {}
    p_entry, p_stop = float(s.get("entry") or 0), float(s.get("stop") or 0)
    a_entry, a_stop = float(t.get("entry") or 0), float(t.get("stop") or 0)
    planned, actual = abs(p_entry - p_stop), abs(a_entry - a_stop)
    if planned <= 0 or actual <= 0 or t.get("r") is None:
        return {"fill_offset": float("nan"), "r_planned": float("nan")}
    return {"fill_offset": actual / planned - 1.0,
            "r_planned": float(t["r"]) * actual / planned}


def show_case(t: dict, label: str) -> None:
    s, d, f = t["setup"], derived(t), fill_stats(t)
    verdict = "REACHED TARGET" if t.get("outcome") == "win" else "HIT STOP"
    mark = "+" if t.get("outcome") == "win" else "-"
    print(f"\n  {mark*3} {label}  {t.get('ticker','?')}  {verdict}   "
          f"{t['r']:+.2f} R   held {t.get('hold','?')} bars")
    # R against the actual fill AND against the stop the signal planned. When
    # they disagree, the difference was decided by the next bar's open, which
    # no setup reading could have seen.
    if np.isfinite(f["r_planned"]):
        print(f"      {t['r']:+.2f} R on the fill  |  {f['r_planned']:+.2f} R on the "
              f"planned stop  |  fill offset {f['fill_offset']:+.2f}")
    print(f"      entry {str(t.get('entry_date'))[:10]}  ->  "
          f"exit {str(t.get('exit_date'))[:10]}")
    print(f"      {'-'*66}")
    print(f"      TRADE      {s.get('trend'):<9} {s.get('strength','?'):<9} "
          f"{'HIGH QUALITY' if s.get('high_quality') else ''}")
    print(f"        entry {float(s.get('entry',0)):>9.2f}   "
          f"stop {float(s.get('stop',0)):>9.2f}   "
          f"target {float(s.get('target',0)):>9.2f}   "
          f"planned R:R {float(s.get('rr',0)):>5.2f}")
    print(f"        stop is {d['stop_pct']:.2f}% of entry")
    print(f"      STRUCTURE")
    print(f"        price {float(s.get('price',0)):>9.2f}   "
          f"EMA20 {float(s.get('ema20',0)):>9.2f}   EMA50 {float(s.get('ema50',0)):>9.2f}")
    print(f"        price is {d['px_vs_ema20_atr']:+.2f} ATR from EMA20; "
          f"EMA20 is {d['ema20_vs_ema50_atr']:+.2f} ATR from EMA50")
    print(f"        ATR {float(s.get('atr',0)):>9.2f}  = {d['atr_pct']:.2f}% of price")
    print(f"      MOMENTUM")
    print(f"        RSI {float(s.get('rsi',0)):>5.1f}        ADX {float(s.get('adx',0)):>5.1f}")
    print(f"      PARTICIPATION")
    print(f"        volume {float(s.get('vol_ratio',0)):>5.2f}x its 20-day average")
    print(f"      GATES  ({s.get('filters_pass','?')}/{s.get('filters_total','?')} passed"
          f"{', ALL PASS' if s.get('all_pass') else ''})")
    for name, f in (s.get("filters") or {}).items():
        state = "PASS" if f.get("pass") else "FAIL"
        print(f"        {state:<5} {name:<22} {str(f.get('detail',''))[:38]}")


# Every reading the cases are compared on. `const` marks one the pipeline
# cannot vary — it is reported as a constant rather than as a +0.00 gap,
# because "no difference" and "no possible difference" are different claims.
ROWS = [
    ("planned R:R",     lambda t: float(t["setup"].get("rr", 0)),        False),
    ("RSI",             lambda t: float(t["setup"].get("rsi", 0)),       False),
    ("ADX",             lambda t: float(t["setup"].get("adx", 0)),       False),
    ("ATR %",           lambda t: derived(t)["atr_pct"],                 False),
    ("volume x",        lambda t: float(t["setup"].get("vol_ratio", 0)), False),
    ("price-EMA20 ATR", lambda t: derived(t)["px_vs_ema20_atr"],         False),
    ("EMA20-EMA50 ATR", lambda t: derived(t)["ema20_vs_ema50_atr"],      False),
    ("stop %",          lambda t: derived(t)["stop_pct"],                False),
    ("gates passed",    lambda t: float(t["setup"].get("filters_pass", 0)), True),
]


def _gaps(sub: list[dict], title: str) -> None:
    """Winners vs losers on every reading, for one side of the market."""
    w = [t for t in sub if t.get("outcome") == "win"]
    l = [t for t in sub if t.get("outcome") == "loss"]
    print(f"\n  {title}  ({len(w)} reached target, {len(l)} hit stop)")
    if not (w and l):
        print("    not enough of both to compare")
        return
    print(f"    {'reading':<18}{'target':>9}{'stopped':>9}{'gap':>9}"
          f"{'target range':>16}{'stopped range':>16}")
    print("    " + "-" * 77)
    dead = []
    for name, fn, _ in ROWS:
        wv = [fn(t) for t in w]
        lv = [fn(t) for t in l]
        wv = [x for x in wv if np.isfinite(x)]
        lv = [x for x in lv if np.isfinite(x)]
        if not (wv and lv):
            continue
        if min(wv + lv) == max(wv + lv):
            dead.append((name, wv[0]))
            continue
        print(f"    {name:<18}{np.mean(wv):>9.2f}{np.mean(lv):>9.2f}"
              f"{np.mean(wv)-np.mean(lv):>+9.2f}"
              f"{min(wv):>8.1f}..{max(wv):<7.1f}{min(lv):>8.1f}..{max(lv):<7.1f}")
    for name, val in dead:
        print(f"    {name:<18}{val:>9.2f}{val:>9.2f}{'CONSTANT':>9}"
              f"   one value across these cases — cannot discriminate")


def matrix(cases: list[tuple[str, dict]]) -> None:
    """Every case against every reading — where a pattern becomes visible."""
    print("\n" + "=" * 112)
    print("CROSS-CASE MATRIX — the same readings, every case, for eyeballing")
    print("=" * 112)
    hdr = (f"  {'case':<16}{'side':>5}{'R':>7}{'R plan':>8}{'fill':>7}{'RR':>6}"
           f"{'RSI':>6}{'ADX':>6}{'ATR%':>7}{'vol x':>7}{'px-E20':>8}"
           f"{'E20-E50':>9}{'stop%':>7}{'gates':>7}{'qual':>6}")
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    for label, t in cases:
        s, d, f = t["setup"], derived(t), fill_stats(t)
        print(f"  {label:<16}{('L' if s.get('trend')=='Bullish' else 'S'):>5}"
              f"{t['r']:>+7.2f}{f['r_planned']:>+8.2f}{f['fill_offset']:>+7.2f}"
              f"{float(s.get('rr',0)):>6.2f}"
              f"{float(s.get('rsi',0)):>6.1f}{float(s.get('adx',0)):>6.1f}"
              f"{d['atr_pct']:>7.2f}{float(s.get('vol_ratio',0)):>7.2f}"
              f"{d['px_vs_ema20_atr']:>+8.2f}{d['ema20_vs_ema50_atr']:>+9.2f}"
              f"{d['stop_pct']:>7.2f}"
              f"{str(s.get('filters_pass',''))+'/'+str(s.get('filters_total','')):>7}"
              f"{'HQ' if s.get('high_quality') else '-':>6}")

    ts = [t for _, t in cases]
    # SPLIT BY SIDE BEFORE COMPARING ANYTHING.
    #
    # Pooled, RSI runs 57.7-75.0 on longs and 25.2-47.4 on shorts, so its mean
    # averages two opposite conditions and lands in the middle regardless of
    # what either side is doing. price-EMA20 is the same story with the sign
    # flipped. A pooled gap on a direction-dependent reading measures the
    # long/short mix of the sample, not the setup.
    longs = [t for t in ts if (t.get("setup") or {}).get("trend") == "Bullish"]
    shorts = [t for t in ts if (t.get("setup") or {}).get("trend") == "Bearish"]
    print(f"\n  {'='*104}")
    print("  REACHED TARGET vs HIT STOP — mean of each reading, SPLIT BY SIDE")
    print(f"  {'='*104}")
    _gaps(longs, "LONGS")
    _gaps(shorts, "SHORTS")

    _collinear(ts)
    _fill_note(ts)

    nw = sum(1 for t in ts if t.get("outcome") == "win")
    nl = sum(1 for t in ts if t.get("outcome") == "loss")
    print(f"\n  {nw} reached their target, {nl} hit their stop "
          f"({len(longs)} long, {len(shorts)} short).")
    print("  WITH THIS FEW CASES SOME GAP IS GUARANTEED — these are")
    print("  candidates to encode and score, not findings.")
    print("=" * 112)


def _collinear(ts: list[dict]) -> None:
    """Readings that are the same number wearing two names."""
    pairs = [t for t in ts
             if np.isfinite(derived(t)["atr_pct"]) and derived(t)["atr_pct"] > 0]
    if not pairs:
        return
    ratio = [derived(t)["stop_pct"] / derived(t)["atr_pct"] for t in pairs]
    tied = sum(1 for r in ratio if abs(r - 1.0) < 0.02)
    print(f"\n  COLLINEAR READINGS")
    print(f"    stop % / ATR %: {tied}/{len(ratio)} cases equal to within 1%, "
          f"median {float(np.median(ratio)):.2f}")
    print("    The stop IS an ATR multiple, so these are one reading printed")
    print("    twice. Two rows moving together is not two pieces of evidence.")


def _fill_note(ts: list[dict]) -> None:
    """How much of R was decided after the signal bar."""
    off = [fill_stats(t)["fill_offset"] for t in ts]
    off = [x for x in off if np.isfinite(x)]
    if not off:
        return
    w = [t for t in ts if t.get("outcome") == "win"]
    ra = [t["r"] for t in w]
    rp = [fill_stats(t)["r_planned"] for t in w]
    rp = [x for x in rp if np.isfinite(x)]
    print(f"\n  WHAT THE FILL DID (this is why cases are no longer picked by R)")
    print(f"    fill offset: median {float(np.median(off)):+.2f}, "
          f"{sum(1 for x in off if x < -0.5)}/{len(off)} lost over half their "
          f"planned risk distance")
    print(f"    fills at or better than planned: "
          f"{sum(1 for x in off if x >= 0)}/{len(off)}")
    if ra and rp and len(ra) > 1 and len(rp) > 1:
        sa, sp = float(np.std(ra)), float(np.std(rp))
        print(f"    winners' R vs the ACTUAL fill : {min(ra):+.2f}..{max(ra):+.2f}"
              f"   sd {sa:.2f}")
        print(f"    winners' R vs the PLANNED stop: {min(rp):+.2f}..{max(rp):+.2f}"
              f"   sd {sp:.2f}"
              + (f"   ({sa/sp:.1f}x narrower)" if sp > 0 else ""))


def selftest() -> int:
    print("setup_cases.py selftest")
    print("=" * 72)

    def mk(tk, r, *, outcome=None, date="2024-01-02", fill=None, **kw):
        s = {"trend": "Bullish", "strength": "Strong", "price": 100.0,
             "entry": 100.5, "stop": 98.0, "target": 108.0, "rr": 3.0,
             "rsi": 60.0, "adx": 25.0, "atr": 2.5, "ema20": 99.0,
             "ema50": 96.0, "vol_ratio": 1.4, "filters_pass": 4,
             "filters_total": 4, "all_pass": True, "high_quality": True,
             "filters": {"ADX Trend Strength": {"pass": True, "detail": "adx 25"}}}
        s.update(kw)
        return {"ticker": tk, "r": r,
                "outcome": outcome or ("win" if r > 0 else "loss"),
                "hold": 5, "entry_date": date, "exit_date": "2024-01-09",
                "entry": fill if fill is not None else s["entry"],
                "stop": s["stop"], "setup": s}

    # ── CASES ARE CHOSEN BY OUTCOME, NEVER BY R ──
    # Ranking by R ranks the fill: 37/37 target-exits in run 2 filled toward
    # the stop, the denominator shrank, and "biggest winner" meant "luckiest
    # open". A +9 R trade must have no better claim to a slot than a +3 R one.
    trades = [mk("AAA", 9.0, date="2024-01-01"),
              mk("AAA", 3.0, date="2024-06-01"),
              mk("AAA", 3.5, date="2024-12-01"),
              mk("AAA", -1.0, date="2024-02-01"),
              mk("AAA", -1.0, date="2024-11-01"),
              mk("BBB", 2.0), mk("BBB", -0.9)]
    p = pick(trades, "AAA", 1)
    assert p["n"] == 5 and p["n_win"] == 3 and p["n_loss"] == 2, p
    assert all(t["ticker"] == "AAA" for t in p["winners"] + p["losers"]), (
        "a ticker's cases must come only from that ticker")
    assert p["winners"][0]["r"] != 9.0, (
        "the largest R was selected. Ranking by R selects the fill offset, "
        "not the setup — that is the defect this rule exists to avoid")
    assert p["winners"][0]["outcome"] == "win" and \
           p["losers"][0]["outcome"] == "loss", p
    print("picking          : by outcome, and the +9.0 R case gets no priority")

    # ── a TIMEOUT is neither, and must not be assigned to a side ──
    tt = [mk("CCC", 0.4, outcome="timeout"), mk("CCC", 2.0), mk("CCC", -1.0)]
    p = pick(tt, "CCC", 2)
    assert p["n_timeout"] == 1, p
    assert all(t["outcome"] != "timeout" for t in p["winners"] + p["losers"]), (
        "a timeout neither reached the target nor hit the stop; counting it as "
        "either invents an outcome the trade did not have")
    print("timeouts         : counted, excluded from both sides")

    # ── cases must span the period, not cluster in one era ──
    many = [mk("DDD", 1.0, date=f"2024-{m:02d}-01") for m in range(1, 13)]
    got = [t["entry_date"] for t in _spread(many, 3)]
    # Both ENDS must be present — that is what "spans the period" means — and
    # the middle pick is the midpoint of an even count, so June or July.
    assert got[0] == "2024-01-01" and got[-1] == "2024-12-01", got
    assert got[1] in ("2024-06-01", "2024-07-01"), got
    assert len(got) == 3, got
    assert len(_spread(many, 99)) == 12, "asking for more than exist returns all"
    assert len(_spread(many, 1)) == 1
    assert _spread([], 3) == []
    print(f"spread           : {got[0][:7]}, {got[1][:7]}, {got[2][:7]} — ends included")

    # ── fill_stats separates what the setup planned from what the fill gave ──
    # planned risk 100.5 - 98.0 = 2.5; a fill at 99.25 leaves 1.25, half of it.
    f = fill_stats(mk("AAA", 6.0, fill=99.25))
    assert abs(f["fill_offset"] - (-0.5)) < 1e-9, f
    assert abs(f["r_planned"] - 3.0) < 1e-9, (
        f"6.0 R on half the planned risk is 3.0 R on the planned stop, got {f}")
    f0 = fill_stats(mk("AAA", 3.0))
    assert abs(f0["fill_offset"]) < 1e-9 and abs(f0["r_planned"] - 3.0) < 1e-9, f0
    assert not np.isfinite(fill_stats({"setup": {}, "r": 1.0})["r_planned"]), \
        "a record with no levels must report nan, not a fabricated zero"
    print("fill stats       : offset -0.50 turns +6.0 R on the fill into +3.0 R")

    # ── derived readings are in the instrument's own units ──
    d = derived(mk("AAA", 1.0, price=100.0, atr=2.0, ema20=99.0, ema50=95.0,
                   entry=100.0, stop=98.0))
    assert abs(d["atr_pct"] - 2.0) < 1e-9, d["atr_pct"]
    assert abs(d["px_vs_ema20_atr"] - 0.5) < 1e-9, d["px_vs_ema20_atr"]
    assert abs(d["ema20_vs_ema50_atr"] - 2.0) < 1e-9, d["ema20_vs_ema50_atr"]
    assert abs(d["stop_pct"] - 2.0) < 1e-9, d["stop_pct"]
    print(f"derived units    : ATR 2.0% of price, price +0.50 ATR over EMA20")

    # ── the SAME setup on a $10 and a $1000 stock must read identically ──
    cheap = derived(mk("A", 1.0, price=10.0, atr=0.2, ema20=9.9, ema50=9.5,
                       entry=10.0, stop=9.8))
    rich = derived(mk("B", 1.0, price=1000.0, atr=20.0, ema20=990.0,
                      ema50=950.0, entry=1000.0, stop=980.0))
    for k in cheap:
        assert abs(cheap[k] - rich[k]) < 1e-6, (
            f"{k} reads {cheap[k]:.4f} on a $10 stock and {rich[k]:.4f} on a "
            f"$1000 one for the SAME setup — the units do not compare")
    print("scale free       : identical setup reads identically at $10 and $1000")

    # ── a trade with no setup is dropped, never shown with blanks ──
    import backtest as _bt
    real = _bt.run
    try:
        _bt.run = lambda cfg: [mk("AAA", 1.0), {"ticker": "AAA", "r": 1.0}]
        got = collect(["AAA"], 1)
    finally:
        _bt.run = real
    assert len(got) == 1, f"a record without a setup must be dropped, got {len(got)}"
    print("no setup         : dropped, never rendered as empty fields")

    # ── DIRECTION-DEPENDENT READINGS MUST BE SPLIT, NOT POOLED ──
    # Pooled, RSI on longs (57-75) and shorts (25-47) average to the middle and
    # cancel. Run 2 printed a +2.27 pooled RSI gap that was an artifact of the
    # long/short mix. These four cases are built so the pooled gap is exactly
    # zero while each side is clearly non-zero.
    quad = [mk("A",  2.0, rsi=70.0),
            mk("A", -1.0, rsi=68.0, outcome="loss"),
            mk("A",  2.0, rsi=30.0, trend="Bearish"),
            mk("A", -1.0, rsi=32.0, trend="Bearish", outcome="loss")]
    assert abs(np.mean([70.0, 30.0]) - np.mean([68.0, 32.0])) < 1e-9, \
        "the fixture must have a pooled gap of zero or it proves nothing"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _gaps([t for t in quad if t["setup"]["trend"] == "Bullish"], "LONGS")
        _gaps([t for t in quad if t["setup"]["trend"] == "Bearish"], "SHORTS")
    out = buf.getvalue()
    long_rsi = [ln for ln in out.splitlines()
                if "RSI" in ln and "LONGS" not in ln][0]
    assert "+2.00" in long_rsi, (
        f"longs' RSI gap must survive the split, got: {long_rsi!r}")
    short_rsi = [ln for ln in out.splitlines() if "RSI" in ln][1]
    assert "-2.00" in short_rsi, (
        f"shorts' RSI gap must survive the split, got: {short_rsi!r}")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        matrix([(f"c{i}", t) for i, t in enumerate(quad)])
    both = buf.getvalue()
    assert "LONGS" in both and "SHORTS" in both, \
        "matrix must report each side separately"
    # Asserting the two HEADINGS appear proves nothing — pooling every case
    # under "LONGS" and passing an empty list to "SHORTS" prints both. That
    # break survived this guard until the numbers inside each section were
    # checked. Read the sections.
    _lsec = both.split("LONGS", 1)[1].split("SHORTS", 1)[0]
    _ssec = both.split("SHORTS", 1)[1]
    _lrsi = [ln for ln in _lsec.splitlines() if ln.strip().startswith("RSI")]
    _srsi = [ln for ln in _ssec.splitlines() if ln.strip().startswith("RSI")]
    assert _lrsi and "+2.00" in _lrsi[0], (
        f"the LONGS section must show the longs' own +2.00 RSI gap, not a "
        f"pooled one, got: {_lrsi!r}")
    assert _srsi and "-2.00" in _srsi[0], (
        f"the SHORTS section must show the shorts' own -2.00 RSI gap, got: "
        f"{_srsi!r}")
    assert "(1 reached target, 1 hit stop)" in _lsec, (
        f"each side must count only its own cases: {_lsec.splitlines()[0]!r}")
    print("side split       : pooled gap 0.00 resolves to +2.00 long, -2.00 short")

    # ── A READING THAT CANNOT VARY IS NOT A NULL RESULT ──
    # evaluate_signal() returns nothing unless every gate passes, so
    # filters_pass is 4 on every case that reaches this output. Printing that
    # as a "+0.00 gap" says the gates do not discriminate; they cannot.
    assert "CONSTANT" in out and "gates passed" in out, (
        "a reading with one possible value must be reported as constant, not "
        "as a zero gap — 'no difference' and 'no possible difference' are "
        "different claims")
    assert "cannot discriminate" in out, out[:400]
    print("constant reading : gates passed reported as CONSTANT, not as +0.00")

    # ── the renderers must not crash on a sparse setup ──
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        show_case({"ticker": "Z", "r": -1.0, "outcome": "loss", "hold": 2,
                   "entry_date": "2024-01-02", "exit_date": "2024-01-04",
                   "setup": {"trend": "Bearish"}}, "sparse")
        matrix([("w", mk("A", 1.0)), ("l", mk("A", -1.0))])
    out = buf.getvalue()
    assert "Bearish" in out and "CROSS-CASE MATRIX" in out, out[:200]
    # "WITH THIS FEW CASES" is the substantive claim — small n means a gap is
    # an artifact — and a break that dropped only that half survived a guard
    # keyed on the rest. The reason clause is the part worth pinning.
    for phrase in ("WITH THIS FEW CASES", "GUARANTEED",
                   "candidates to encode and score", "not findings"):
        assert phrase in out, (
            f"the matrix must carry its own caveat and is missing {phrase!r} — "
            f"this output's whole risk is being read as evidence, and the "
            f"warning is the only thing standing between it and that reading")
    print("rendering        : survives a sparse setup, matrix warns about itself")

    # ── THE DEFAULT SET MUST BE ALREADY SPENT ──
    # Stage 1 forms hypotheses by looking, which burns whatever it looks at. A
    # clean name in this list would be destroyed silently — no error, no
    # warning, just a name that can no longer confirm anything. Clean names are
    # the ONLY thing that could make stage 3 mean more than "in-sample".
    import data_reservation as _dr
    _names = [x.strip().upper() for x in DEFAULT_TICKERS.split(",") if x.strip()]
    assert len(_names) == len(set(_names)), (
        f"duplicate tickers in the default set: "
        f"{sorted({x for x in _names if _names.count(x) > 1})}")
    _chk = _dr.check_clean(_names, purpose="setup_cases stage 1")
    # `spent` is a dict keyed by TRANCHE — {"A": ["INTC", ...]} — so membership
    # against it tests the keys, not the names. A first version did exactly that
    # and reported five spent tickers as clean. Flatten the values.
    _burned = set(_chk.get("contaminated") or [])
    for _tranche_names in (_chk.get("spent") or {}).values():
        _burned |= set(_tranche_names)
    _clean = [t for t in _names if t not in _burned]
    assert not _clean, (
        f"{_clean} are still CLEAN and must not be in stage 1's default set. "
        f"Looking at a setup is how a hypothesis is formed, so it spends the "
        f"name; put burned names here and keep clean ones for stage 3")
    print(f"default set      : {len(_names)} tickers, none of them clean — "
          f"looking costs nothing")

    # ── WIRING: the readings must ARRIVE, not merely be produced upstream ──
    #
    # The first version of this guard read signal_core.evaluate()'s SOURCE and
    # checked the key names appeared in it. They did. Every one of them was then
    # discarded by backtest.evaluate_signal(), which returned a six-key
    # projection, and the guard stayed green while the first real run printed 80
    # trades with RSI 0.0, ADX 0.0, ATR% nan and a +0.00 gap on every feature.
    #
    # Checking the producer can never catch a broken handoff. Run the real
    # signal on a real frame and assert on the dict that comes back.
    import inspect as _i
    assert '"setup"' in _i.getsource(bt.simulate_trade), (
        "backtest.simulate_trade no longer carries the setup; every case would "
        "be dropped and this module would print nothing")

    _df = bt._synthetic_ohlc(up=True, adx=40.0)
    _sig = bt.evaluate_signal(_df, len(_df) - 1, bt.build_signal_params(bt.DEFAULTS))
    assert _sig is not None, "the synthetic bar no longer signals; guard is blind"
    for _k in ("price", "rsi", "adx", "atr", "ema20", "ema50", "vol_ratio",
               "strength", "high_quality", "filters"):
        assert _k in _sig, (
            f"backtest.evaluate_signal() drops {_k!r} before it reaches the "
            f"setup. This module would render it as 0.00 or nan — a silent "
            f"zero, not an error, which is how 80 empty cases got printed")

    # Rendered through this module's own units, so a reading that arrives but
    # is structurally useless — ATR of zero makes every ATR-distance nan — is
    # caught here rather than in the output.
    _d = derived({"setup": _sig})
    for _k, _v in _d.items():
        assert np.isfinite(_v), (
            f"derived reading {_k!r} is {_v} on a live signal; the inputs "
            f"behind it did not survive the handoff")
    assert _d["atr_pct"] > 0, "ATR% of zero means no ATR arrived"
    assert _sig["rsi"] > 0 and _sig["price"] > 0, \
        "RSI and price must be real readings, not defaulted zeros"
    print("wiring           : real signal -> real readings, checked at the sink")

    print("=" * 72)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default=DEFAULT_TICKERS)
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--each", type=int, default=1,
                    help="winners and losers to show per ticker")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    print(f"  collecting ...", file=sys.stderr)
    trades = collect(tickers, a.years)
    if not trades:
        print("NOTHING TO SHOW — no trades carried a setup.")
        return 2
    print("=" * 100)
    print("SETUP CASES — what the logic saw, on trades that worked and failed")
    print("=" * 100)
    print(f"  {len(trades)} trades across {len(tickers)} tickers, {a.years} years")
    print(f"  STAGE 1 of 3: this generates candidates. It cannot test them.")
    print(f"  Cases are trades that REACHED THEIR TARGET vs trades that HIT")
    print(f"  THEIR STOP, spread evenly across the period — not the best and")
    print(f"  worst by R. See pick() for why ranking by R ranks the fill.")
    print("=" * 100)

    cases = []
    for tk in tickers:
        p = pick(trades, tk, a.each)
        if not p["n"]:
            print(f"\n  {tk}: no trades")
            continue
        print(f"\n{'='*100}\n{tk}   {p['n']} trades "
              f"({p.get('n_win',0)} target, {p.get('n_loss',0)} stop, "
              f"{p.get('n_timeout',0)} timeout), average {p['avg_r']:+.3f} R"
              f"\n{'='*100}")
        for i, t in enumerate(p["winners"]):
            show_case(t, f"WIN {i+1}")
            cases.append((f"{tk} win{i+1}", t))
        for i, t in enumerate(p["losers"]):
            show_case(t, f"LOSS {i+1}")
            cases.append((f"{tk} loss{i+1}", t))
    if cases:
        matrix(cases)
    return 0


if __name__ == "__main__":
    sys.exit(main())
