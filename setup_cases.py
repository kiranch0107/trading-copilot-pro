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


def pick(trades: list[dict], ticker: str, n_each: int = 1) -> dict:
    """The clearest winners and losers for one ticker, by R."""
    own = [t for t in trades if t.get("ticker") == ticker]
    if not own:
        return {"ticker": ticker, "winners": [], "losers": [], "n": 0}
    ranked = sorted(own, key=lambda t: t["r"], reverse=True)
    return {"ticker": ticker, "n": len(own),
            "winners": ranked[:n_each], "losers": ranked[-n_each:][::-1],
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


def show_case(t: dict, label: str) -> None:
    s, d = t["setup"], derived(t)
    verdict = "WORKED" if t["r"] > 0 else "FAILED"
    mark = "+" if t["r"] > 0 else "-"
    print(f"\n  {mark*3} {label}  {t.get('ticker','?')}  {verdict}   "
          f"{t['r']:+.2f} R   {t.get('outcome','?')}   held {t.get('hold','?')} bars")
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


def matrix(cases: list[tuple[str, dict]]) -> None:
    """Every case against every reading — where a pattern becomes visible."""
    print("\n" + "=" * 100)
    print("CROSS-CASE MATRIX — the same readings, every case, for eyeballing")
    print("=" * 100)
    hdr = (f"  {'case':<16}{'R':>7}{'RR':>6}{'RSI':>6}{'ADX':>6}{'ATR%':>7}"
           f"{'vol x':>7}{'px-E20':>8}{'E20-E50':>9}{'stop%':>7}{'gates':>7}{'qual':>6}")
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    for label, t in cases:
        s, d = t["setup"], derived(t)
        print(f"  {label:<16}{t['r']:>+7.2f}{float(s.get('rr',0)):>6.2f}"
              f"{float(s.get('rsi',0)):>6.1f}{float(s.get('adx',0)):>6.1f}"
              f"{d['atr_pct']:>7.2f}{float(s.get('vol_ratio',0)):>7.2f}"
              f"{d['px_vs_ema20_atr']:>+8.2f}{d['ema20_vs_ema50_atr']:>+9.2f}"
              f"{d['stop_pct']:>7.2f}"
              f"{str(s.get('filters_pass',''))+'/'+str(s.get('filters_total','')):>7}"
              f"{'HQ' if s.get('high_quality') else '-':>6}")

    print(f"\n  WINNERS vs LOSERS — mean of each reading")
    w = [t for lab, t in cases if t["r"] > 0]
    l = [t for lab, t in cases if t["r"] <= 0]
    if w and l:
        rows = [("planned R:R", lambda t: float(t["setup"].get("rr", 0))),
                ("RSI", lambda t: float(t["setup"].get("rsi", 0))),
                ("ADX", lambda t: float(t["setup"].get("adx", 0))),
                ("ATR %", lambda t: derived(t)["atr_pct"]),
                ("volume x", lambda t: float(t["setup"].get("vol_ratio", 0))),
                ("price-EMA20 ATR", lambda t: derived(t)["px_vs_ema20_atr"]),
                ("EMA20-EMA50 ATR", lambda t: derived(t)["ema20_vs_ema50_atr"]),
                ("stop %", lambda t: derived(t)["stop_pct"]),
                ("gates passed", lambda t: float(t["setup"].get("filters_pass", 0)))]
        print(f"    {'reading':<20}{'winners':>10}{'losers':>10}{'gap':>10}")
        print("    " + "-" * 50)
        for name, fn in rows:
            wv, lv = float(np.mean([fn(t) for t in w])), float(np.mean([fn(t) for t in l]))
            print(f"    {name:<20}{wv:>10.2f}{lv:>10.2f}{wv-lv:>+10.2f}")
    print(f"\n  {len(w)} winners, {len(l)} losers. WITH THIS FEW CASES SOME GAP IS")
    print(f"  GUARANTEED — these are candidates to encode and score, not findings.")
    print("=" * 100)


def selftest() -> int:
    print("setup_cases.py selftest")
    print("=" * 72)

    def mk(tk, r, **kw):
        s = {"trend": "Bullish", "strength": "Strong", "price": 100.0,
             "entry": 100.5, "stop": 98.0, "target": 108.0, "rr": 3.0,
             "rsi": 60.0, "adx": 25.0, "atr": 2.5, "ema20": 99.0,
             "ema50": 96.0, "vol_ratio": 1.4, "filters_pass": 4,
             "filters_total": 4, "all_pass": True, "high_quality": True,
             "filters": {"ADX Trend Strength": {"pass": True, "detail": "adx 25"}}}
        s.update(kw)
        return {"ticker": tk, "r": r, "outcome": "win" if r > 0 else "loss",
                "hold": 5, "entry_date": "2024-01-02", "exit_date": "2024-01-09",
                "entry": s["entry"], "stop": s["stop"], "setup": s}

    # ── picks the CLEAREST winner and loser, per ticker ──
    trades = [mk("AAA", 3.0), mk("AAA", -1.0), mk("AAA", 0.1),
              mk("BBB", 2.0), mk("BBB", -0.9)]
    p = pick(trades, "AAA")
    assert p["n"] == 3, p["n"]
    assert p["winners"][0]["r"] == 3.0 and p["losers"][0]["r"] == -1.0, p
    assert all(t["ticker"] == "AAA" for t in p["winners"] + p["losers"]), (
        "a ticker's cases must come only from that ticker")
    print(f"picking          : best +3.0 R and worst -1.0 R, AAA only")

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

    # ── WIRING: the setup must actually arrive from the backtest ──
    import inspect as _i
    assert '"setup"' in _i.getsource(bt.simulate_trade), (
        "backtest.simulate_trade no longer carries the setup; every case would "
        "be dropped and this module would print nothing")
    import signal_core as _sc
    ev = _i.getsource(_sc.evaluate)
    for key in ("high_quality", "filters", "vol_ratio", "strength"):
        assert f'"{key}"' in ev, f"signal_core.evaluate stopped returning {key!r}"
    print("wiring           : simulate_trade carries it, evaluate still produces it")

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
    print("=" * 100)

    cases = []
    for tk in tickers:
        p = pick(trades, tk, a.each)
        if not p["n"]:
            print(f"\n  {tk}: no trades")
            continue
        print(f"\n{'='*100}\n{tk}   {p['n']} trades, average {p['avg_r']:+.3f} R\n{'='*100}")
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
