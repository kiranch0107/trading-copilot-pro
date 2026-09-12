"""
Option-Leg Backtest — does the strategy work on the CONTRACT you actually buy?
==============================================================================
Every previous backtest measured the SHARE leg: buy the stock, exit at 3xATR or
1xATR. But you trade options, and an option does not track its underlying 1:1.

  • DELTA   — an ATM option gains only ~0.5 of a small underlying move
  • GAMMA   — but accelerates on large moves, so big winners are amplified
  • THETA   — it bleeds value every single day regardless of direction

So "how often does a call reach +100% before -50% or 7 DTE?" is a genuinely
different question from "how often does the stock reach 3xATR before 1xATR."
The 40% win rate used to justify the TP+100/SL-50 defaults came from the SHARE
test and may not transfer. This script measures the option question directly.

═══════════════════════════════════════════════════════════════════════════
WHAT THIS IS NOT — read before trusting a number
═══════════════════════════════════════════════════════════════════════════
Historical per-contract option prices are not available from this data source,
so option values are MODELLED with Black-Scholes on the real underlying path.
That means:

  1. IMPLIED VOLATILITY IS ASSUMED, NOT OBSERVED. We estimate it from trailing
     realised volatility times a risk-premium multiplier. Real IV moves on its
     own: it spikes in selloffs (helping puts beyond what this model shows) and
     collapses after earnings (hurting both sides). This is the single largest
     source of error, and it is not small.
  2. NO VOLATILITY SMILE. Real OTM strikes carry different IV than ATM. We
     model ATM only, which is what the app's strike selection targets anyway.
  3. NO EARLY-EXERCISE OR PIN RISK, and dividends are ignored.
  4. FILLS ARE MODELLED AS MID PLUS A FIXED SPREAD COST. Real fills on wide
     contracts are worse and vary with size.

Treat the output as directional evidence about PAYOFF STRUCTURE — which is
what it is good for, because the structural comparison between TP levels is
far more robust than any single absolute number. Do not treat it as a
prediction of your P&L.

Run
---
    pip install yfinance pandas ta numpy tabulate
    python option_backtest.py
    python option_backtest.py --tp 100 --sl 50 --dte-exit 7
    python option_backtest.py --sweep          # compare payoff structures
"""
from __future__ import annotations

import argparse
import sys
import math
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

import backtest as bt          # reuse the SAME validated signal logic

try:
    from tabulate import tabulate
except ImportError:
    def tabulate(rows, headers, **kw):
        out = ["  ".join(str(h) for h in headers)]
        for r in rows:
            out.append("  ".join(str(c) for c in r))
        return "\n".join(out)


RISK_FREE = 0.04
TRADING_DAYS = 252


# ══════════════════════════════════════════════════════════════════
# BLACK-SCHOLES
# ══════════════════════════════════════════════════════════════════
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot: float, strike: float, t_years: float, vol: float,
             right: str, r: float = RISK_FREE) -> float:
    """Black-Scholes value of a European option. Floors at intrinsic."""
    intrinsic = max(0.0, (spot - strike) if right == "CALL" else (strike - spot))
    if t_years <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        return intrinsic
    sq = vol * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * vol * vol) * t_years) / sq
    d2 = d1 - sq
    if right == "CALL":
        val = spot * _norm_cdf(d1) - strike * math.exp(-r * t_years) * _norm_cdf(d2)
    else:
        val = strike * math.exp(-r * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
    return max(val, intrinsic)


def realised_vol(closes: pd.Series, window: int = 20) -> float:
    """Annualised realised volatility — our proxy for implied vol."""
    rets = np.log(closes / closes.shift(1)).dropna()
    if len(rets) < window:
        return float("nan")
    return float(rets.tail(window).std() * math.sqrt(TRADING_DAYS))


# ══════════════════════════════════════════════════════════════════
# OPTION TRADE SIMULATION
# ══════════════════════════════════════════════════════════════════
def simulate_option_trade(df: pd.DataFrame, signal_i: int, trend: str,
                          cfg: dict) -> dict | None:
    """
    Buy an ATM option at the next bar's open and walk it forward daily,
    applying the same exit rules the live monitor uses.

    Exit priority matches exit_monitor.py: STOP, TARGET, TIME, THESIS.
    """
    n = len(df)
    entry_i = signal_i + 1
    if entry_i >= n:
        return None

    spot0 = float(df["Open"].iloc[entry_i]) if "Open" in df.columns \
        else float(df["Close"].iloc[signal_i])
    entry_date = (str(df["Date"].iloc[entry_i]) if "Date" in df.columns
                  else f"i{entry_i}")
    right = "CALL" if trend == "Bullish" else "PUT"

    # IV proxy: trailing realised vol x a risk premium. Options systematically
    # trade above realised vol; ignoring that would make every entry look cheap.
    rv = realised_vol(df["Close"].iloc[:entry_i + 1])
    if not np.isfinite(rv) or rv <= 0:
        return None
    iv = rv * cfg["iv_mult"]

    strike = round(spot0)                       # ATM
    dte0 = cfg["dte"]
    prem0 = bs_price(spot0, strike, dte0 / TRADING_DAYS, iv, right)
    if prem0 <= 0.05:                           # unpriceable / negligible
        return None

    # Entry cost: pay half the spread
    entry_prem = prem0 * (1 + cfg["spread_pct"] / 200)

    for j in range(entry_i + 1, min(entry_i + dte0 + 1, n)):
        held = j - entry_i
        dte_left = dte0 - held
        spot = float(df["Close"].iloc[j])
        t_left = max(dte_left, 0) / TRADING_DAYS
        raw = bs_price(spot, strike, t_left, iv, right)
        # Exit fill: give up half the spread
        mid = raw * (1 - cfg["spread_pct"] / 200)
        pnl_pct = (mid - entry_prem) / entry_prem * 100

        # 1. STOP
        if cfg["sl"] and pnl_pct <= -abs(cfg["sl"]):
            return _result("STOP", entry_prem, mid, held, dte_left, pnl_pct,
                           spot0=spot0, spot_exit=spot, strike=strike, iv=iv,
                           right=right, dte0=dte0, entry_date=entry_date)
        # 2. TARGET
        if cfg["tp"] and pnl_pct >= abs(cfg["tp"]):
            return _result("TARGET", entry_prem, mid, held, dte_left, pnl_pct,
                           spot0=spot0, spot_exit=spot, strike=strike, iv=iv,
                           right=right, dte0=dte0, entry_date=entry_date)
        # 3. TIME
        if cfg["dte_exit"] and dte_left <= cfg["dte_exit"]:
            return _result("TIME", entry_prem, mid, held, dte_left, pnl_pct,
                           spot0=spot0, spot_exit=spot, strike=strike, iv=iv,
                           right=right, dte0=dte0, entry_date=entry_date)
        # 4. THESIS — underlying closed the wrong side of EMA20
        if cfg["use_thesis"] and "EMA20" in df.columns:
            ema20 = float(df["EMA20"].iloc[j])
            broke = (spot < ema20) if right == "CALL" else (spot > ema20)
            if broke:
                return _result("THESIS", entry_prem, mid, held, dte_left, pnl_pct,
                               spot0=spot0, spot_exit=spot, strike=strike, iv=iv,
                               right=right, dte0=dte0, entry_date=entry_date)

    # Ran out of data or reached expiry — mark to intrinsic
    j = min(entry_i + dte0, n - 1)
    spot = float(df["Close"].iloc[j])
    mid = max(0.0, (spot - strike) if right == "CALL" else (strike - spot))
    pnl_pct = (mid - entry_prem) / entry_prem * 100
    return _result("EXPIRY", entry_prem, mid, j - entry_i, 0, pnl_pct,
                   spot0=spot0, spot_exit=spot, strike=strike, iv=iv,
                   right=right, dte0=dte0, entry_date=entry_date)


def _result(reason, entry_prem, exit_prem, held, dte_left, pnl_pct,
            *, spot0=None, spot_exit=None, strike=None, iv=None,
            right=None, dte0=None, entry_date=None) -> dict:
    """
    One option trade.

    The entry state (spot0, strike, iv, right, dte0) is carried so the P&L can
    be decomposed after the fact. Without it a trade records only that it lost,
    never whether it lost to time or to direction — and those have opposite
    remedies. option_decompose.py is the consumer; keyword-only and defaulted so
    every existing caller and fixture keeps working.
    """
    # NOT ROUNDED. These used to be round(entry_prem, 2) and round(pnl_pct, 1),
    # which is a display concern stored in the record. It cost precision that a
    # consumer cannot recover: option_decompose recomputes the P&L exactly from
    # the entry state, and reconciling that against a 1dp pnl_pct left a 0.05%
    # gap across 1398 trades on the first real run — a module whose whole claim
    # is exact reconciliation, drifting on real data while reconciling perfectly
    # on fixtures. Every reader formats with a specifier anyway.
    return {"reason": reason, "entry_prem": entry_prem,
            "exit_prem": exit_prem, "held": held,
            "dte_left": dte_left, "pnl_pct": pnl_pct,
            "spot0": spot0, "spot_exit": spot_exit, "strike": strike,
            "iv": iv, "right": right, "dte0": dte0,
            # The identity of the SIGNAL, so consumers can pair trades across
            # arms. thesis_test keyed on (ticker, spot0) because this field did
            # not exist and its .get() fallback was never checked — two trades
            # sharing an entry spot collided and one was silently dropped.
            "entry_date": entry_date}


# ══════════════════════════════════════════════════════════════════
# DRIVER
# ══════════════════════════════════════════════════════════════════
def run_ticker(tk: str, cfg: dict, sig_cfg: dict, coverage: dict | None = None) -> list[dict]:
    # backtest.evaluate_signal() takes a signal_core.SignalParams, not the
    # sig_cfg dict — it delegates to signal_core.evaluate() rather than
    # reimplementing the signal. sig_cfg still supplies the values it is
    # built from, so the share-leg signal here stays identical to backtest.py's.
    params = bt.build_signal_params(sig_cfg)
    # `coverage` (when passed) records whether this ticker supplied usable bars,
    # so the caller can tell "no data" apart from "data, but no setups". Those
    # are the same empty list, and conflating them is how a run over half the
    # intended universe reports a clean number.
    raw = bt.download(tk, cfg["years"])
    if raw is None:
        if coverage is not None:
            coverage["no_data"].append(tk)
        return []
    df = bt.compute(raw)
    if len(df) < bt.MIN_BARS_AFTER:
        if coverage is not None:
            coverage["too_short"].append((tk, len(df)))
        return []
    if coverage is not None:
        coverage["ok"].append(tk)
    # Open/Date arrive correct from bt.compute(); re-attaching them by tail
    # position corrupted both whenever a NaN dropped an interior row. See
    # backtest.run() for the measurement. This matters here more than anywhere
    # else: OPT_WIN_RATE in risk_params.py is measured by THIS function, and the
    # option spread ceiling is derived from that number.

    trades, i, n = [], 0, len(df)
    while i < n - 1:
        sig = bt.evaluate_signal(df, i, params)
        if sig:
            res = simulate_option_trade(df, i, sig["trend"], cfg)
            if res:
                res["ticker"] = tk
                res["trend"] = sig["trend"]
                trades.append(res)
                i += sig_cfg["cooldown_bars"] + 1
                continue
        i += 1
    return trades


def summarise(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    p = np.array([t["pnl_pct"] for t in trades])
    wins, losses = p[p > 0], p[p < 0]
    gp, gl = wins.sum(), abs(losses.sum())
    return {
        "n": len(p),
        "win_rate": len(wins) / len(p) * 100,
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "expectancy": float(p.mean()),
        "pf": (gp / gl) if gl > 0 else float("inf"),
        "avg_held": float(np.mean([t["held"] for t in trades])),
        "reasons": pd.Series([t["reason"] for t in trades]).value_counts().to_dict(),
    }


def _report_coverage(coverage: dict | None, requested: list[str]) -> int:
    """
    Say how much of the intended universe actually supplied data, and refuse to
    look successful when none of it did.

    WHY THIS EXISTS. Running --sweep with the data provider unreachable printed a
    fully-formatted table of em-dashes and EXITED 0. That is the obvious case.
    The dangerous one is PARTIAL: if three of seven tickers fail to download, the
    table fills with real-looking numbers computed from four, nothing says so,
    and the run still exits 0.

    This matters more here than anywhere else in the repo, because
    risk_params.OPT_WIN_RATE is measured by this file and the live option spread
    ceiling is derived from that number. A win rate quietly taken over half the
    intended universe would flow straight into a gate that decides which
    contracts get suggested.

    backtest.py already does this — per-ticker "no data — skipped" plus a "Bars
    tested" line it tells you to compare across runs FIRST. This is the same idea.
    """
    if coverage is None:
        return 0
    ok, no_data, short = coverage["ok"], coverage["no_data"], coverage["too_short"]
    n_req = len(requested)
    print(f"\nDATA COVERAGE: {len(ok)} of {n_req} ticker(s) supplied usable bars"
          f" — compare this across runs BEFORE comparing any number above")
    if ok:
        print(f"  used    : {', '.join(ok)}")
    if no_data:
        print(f"  NO DATA : {', '.join(no_data)}")
    if short:
        print(f"  TOO SHORT: {', '.join(f'{t}={n}' for t, n in short)}")

    if not ok:
        print("\n" + "!" * 78)
        print("NO TICKER SUPPLIED DATA. Every number above is empty, not measured.")
        print("This run measured nothing — do not read the table, and do not put")
        print("any figure from it into risk_params.OPT_WIN_RATE.")
        print("!" * 78)
        return 2
    if no_data or short:
        print("\n" + "!" * 78)
        print(f"PARTIAL UNIVERSE: {len(ok)} of {n_req} tickers. The numbers above are")
        print("real but they are NOT the measurement they claim to be — they cover")
        print("a subset chosen by which downloads happened to succeed, which is a")
        print("selection nobody made on purpose. Re-run until coverage is complete")
        print("before deriving anything from this.")
        print("!" * 78)
        return 1
    return 0


def selftest() -> int:
    """
    Offline checks for the coverage guard. This module had NO test, and it is
    the one that produces risk_params.OPT_WIN_RATE — the input the live option
    spread ceiling is derived from.
    """
    import io, contextlib

    def _run(cov, requested):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = _report_coverage(cov, requested)
        return rc, buf.getvalue()

    req = ["A", "B", "C", "D"]

    rc, out = _run({"ok": req, "no_data": [], "too_short": []}, req)
    assert rc == 0, f"a complete universe must exit 0, got {rc}"
    assert "4 of 4" in out, out
    print("complete universe : exit 0")

    rc, out = _run({"ok": ["A", "B"], "no_data": ["C"],
                    "too_short": [("D", 12)]}, req)
    assert rc == 1, f"a partial universe must exit 1, got {rc}"
    assert "PARTIAL UNIVERSE" in out and "2 of 4" in out, out
    assert "C" in out and "D=12" in out, "the missing tickers must be named"
    print("partial universe  : exit 1, missing tickers named")

    rc, out = _run({"ok": [], "no_data": req, "too_short": []}, req)
    assert rc == 2, f"an empty run must exit 2, got {rc}"
    assert "NO TICKER SUPPLIED DATA" in out, out
    assert "OPT_WIN_RATE" in out, \
        "the empty case must say why it matters — this number feeds the live gate"
    print("nothing measured  : exit 2, refuses to look successful")

    # LIVENESS: the three verdicts must actually differ, or the guard is a
    # formality. A run with the provider unreachable used to exit 0 and print a
    # full table of em-dashes.
    codes = {
        _run({"ok": req, "no_data": [], "too_short": []}, req)[0],
        _run({"ok": ["A"], "no_data": ["B"], "too_short": []}, req)[0],
        _run({"ok": [], "no_data": req, "too_short": []}, req)[0],
    }
    assert codes == {0, 1, 2}, \
        f"complete / partial / empty must be distinguishable, got {codes}"
    print("verdicts distinct : 0 / 1 / 2")

    assert _report_coverage(None, req) == 0, \
        "no coverage recorded must not invent a failure"
    print("no coverage       : exit 0, no false alarm")
    print("\nAll self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Option-leg backtest")
    ap.add_argument("--tickers", default="TSLA,NVDA,AAPL,MSFT,AMZN,META,ROKU")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--dte", type=int, default=30, help="DTE at entry")
    ap.add_argument("--tp", type=float, default=100)
    ap.add_argument("--sl", type=float, default=50)
    ap.add_argument("--dte-exit", type=int, default=7)
    ap.add_argument("--no-thesis", action="store_true")
    ap.add_argument("--iv-mult", type=float, default=1.15,
                    help="IV as a multiple of realised vol (risk premium)")
    ap.add_argument("--spread-pct", type=float, default=5.0,
                    help="Round-trip bid-ask cost, %% of premium")
    ap.add_argument("--selftest", action="store_true",
                    help="offline checks for the coverage guard")
    ap.add_argument("--sweep", action="store_true",
                    help="Compare take-profit levels instead of one config")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    sig_cfg = dict(bt.DEFAULTS)
    sig_cfg.update(tickers=tickers, years=a.years)

    base = dict(years=a.years, dte=a.dte, tp=a.tp, sl=a.sl,
                dte_exit=a.dte_exit, use_thesis=not a.no_thesis,
                iv_mult=a.iv_mult, spread_pct=a.spread_pct)

    print("=" * 78)
    print("OPTION-LEG BACKTEST")
    print("=" * 78)
    print(f"Tickers   : {', '.join(tickers)}")
    print(f"History   : {a.years}y   Entry DTE: {a.dte}   IV: {a.iv_mult}x realised")
    print(f"Costs     : {a.spread_pct}% round-trip spread")
    print("Option prices are MODELLED (Black-Scholes on the real underlying path),")
    print("not historical quotes. IV is assumed, not observed — see the header.")
    print("=" * 78)

    if a.sweep:
        print("\nPAYOFF STRUCTURE SWEEP — the question the defaults depend on\n")
        rows = []
        coverage = None
        for tp in (50, 75, 100, 150, 200, 300):
            cfg = dict(base, tp=tp)
            trades = []
            # Coverage is identical for every TP (same download, same bars), so
            # record it once on the first pass and report it against the table.
            cov = {"ok": [], "no_data": [], "too_short": []} if coverage is None else None
            for tk in tickers:
                trades += run_ticker(tk, cfg, sig_cfg, coverage=cov)
            if cov is not None:
                coverage = cov
            s = summarise(trades)
            if s["n"] == 0:
                rows.append([f"+{tp:g}% / -{a.sl:g}%", 0, "—", "—", "—", "—", "—"])
                continue
            rows.append([
                f"+{tp:g}% / -{a.sl:g}%", s["n"], f"{s['win_rate']:.1f}%",
                f"{s['avg_win']:+.1f}%", f"{s['avg_loss']:+.1f}%",
                f"{s['expectancy']:+.2f}%", f"{s['pf']:.2f}",
            ])
        print(tabulate(rows, headers=["TP / SL", "Trades", "Win%", "Avg win",
                                      "Avg loss", "Expectancy", "PF"],
                       tablefmt="simple"))
        print("\nExpectancy is % of premium risked per trade. The COMPARISON between")
        print("rows is far more reliable than any single absolute number, because")
        print("the IV assumption shifts every row in the same direction.")
        return _report_coverage(coverage, tickers)

    coverage = {"ok": [], "no_data": [], "too_short": []}
    trades = []
    for tk in tickers:
        trades += run_ticker(tk, cfg=base, sig_cfg=sig_cfg, coverage=coverage)

    per = []
    for tk in tickers:
        s = summarise([t for t in trades if t["ticker"] == tk])
        if s["n"]:
            per.append([tk, s["n"], f"{s['win_rate']:.0f}%",
                        f"{s['expectancy']:+.1f}%", f"{s['pf']:.2f}",
                        f"{s['avg_held']:.0f}"])
    print("\nPER-TICKER")
    print(tabulate(per, headers=["Ticker", "Trades", "Win%", "Expectancy",
                                 "PF", "Held"], tablefmt="simple"))

    s = summarise(trades)
    print("\n" + "=" * 78)
    print("AGGREGATE")
    print("=" * 78)
    if s["n"] == 0:
        print("No trades generated.")
        return _report_coverage(coverage, tickers)
    print(f"  Trades       : {s['n']}")
    print(f"  Win rate     : {s['win_rate']:.1f}%")
    print(f"  Avg win      : {s['avg_win']:+.1f}% of premium")
    print(f"  Avg loss     : {s['avg_loss']:+.1f}% of premium")
    print(f"  Expectancy   : {s['expectancy']:+.2f}% per trade")
    print(f"  Profit factor: {s['pf']:.2f}")
    print(f"  Avg held     : {s['avg_held']:.1f} sessions")
    print(f"  Exit reasons : {s['reasons']}")

    print("\nINTERPRETATION")
    be = a.sl / (a.tp + a.sl) * 100
    print(f"  TP+{a.tp:g}/SL-{a.sl:g} needs a {be:.1f}% win rate to break even.")
    print(f"  This test measured {s['win_rate']:.1f}% at the OPTION level.")
    if s["win_rate"] >= be and s["expectancy"] > 0:
        print("  -> The payoff structure is supported by the measured win rate.")
    else:
        print("  -> The measured win rate does NOT support this payoff structure.")
        print("     Run --sweep to see which TP level the data actually favours.")
    print("\n  Reminder: modelled prices, assumed IV, no vol dynamics. Directional")
    print("  evidence about structure — not a P&L forecast.")
    return _report_coverage(coverage, tickers)


if __name__ == "__main__":
    # EXIT CODE CARRIES THE VERDICT: 0 complete, 1 partial universe, 2 nothing
    # measured. It used to be 0 in all three cases, so a run with the provider
    # unreachable printed a formatted table and reported success.
    sys.exit(main() or 0)
