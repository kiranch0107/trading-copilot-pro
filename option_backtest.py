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
            return _result("STOP", entry_prem, mid, held, dte_left, pnl_pct)
        # 2. TARGET
        if cfg["tp"] and pnl_pct >= abs(cfg["tp"]):
            return _result("TARGET", entry_prem, mid, held, dte_left, pnl_pct)
        # 3. TIME
        if cfg["dte_exit"] and dte_left <= cfg["dte_exit"]:
            return _result("TIME", entry_prem, mid, held, dte_left, pnl_pct)
        # 4. THESIS — underlying closed the wrong side of EMA20
        if cfg["use_thesis"] and "EMA20" in df.columns:
            ema20 = float(df["EMA20"].iloc[j])
            broke = (spot < ema20) if right == "CALL" else (spot > ema20)
            if broke:
                return _result("THESIS", entry_prem, mid, held, dte_left, pnl_pct)

    # Ran out of data or reached expiry — mark to intrinsic
    j = min(entry_i + dte0, n - 1)
    spot = float(df["Close"].iloc[j])
    mid = max(0.0, (spot - strike) if right == "CALL" else (strike - spot))
    pnl_pct = (mid - entry_prem) / entry_prem * 100
    return _result("EXPIRY", entry_prem, mid, j - entry_i, 0, pnl_pct)


def _result(reason, entry_prem, exit_prem, held, dte_left, pnl_pct) -> dict:
    return {"reason": reason, "entry_prem": round(entry_prem, 2),
            "exit_prem": round(exit_prem, 2), "held": held,
            "dte_left": dte_left, "pnl_pct": round(pnl_pct, 1)}


# ══════════════════════════════════════════════════════════════════
# DRIVER
# ══════════════════════════════════════════════════════════════════
def run_ticker(tk: str, cfg: dict, sig_cfg: dict) -> list[dict]:
    # backtest.evaluate_signal() takes a signal_core.SignalParams, not the
    # sig_cfg dict — it delegates to signal_core.evaluate() rather than
    # reimplementing the signal. sig_cfg still supplies the values it is
    # built from, so the share-leg signal here stays identical to backtest.py's.
    params = bt.build_signal_params(sig_cfg)
    raw = bt.download(tk, cfg["years"])
    if raw is None:
        return []
    df = bt.compute(raw)
    if len(df) < bt.MIN_BARS_AFTER:
        return []
    tail = raw.tail(len(df)).reset_index(drop=True)
    for col in ("Open", "Date"):
        if col in tail.columns:
            df[col] = tail[col].values

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


def main() -> None:
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
    ap.add_argument("--sweep", action="store_true",
                    help="Compare take-profit levels instead of one config")
    a = ap.parse_args()

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
        for tp in (50, 75, 100, 150, 200, 300):
            cfg = dict(base, tp=tp)
            trades = []
            for tk in tickers:
                trades += run_ticker(tk, cfg, sig_cfg)
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
        return

    trades = []
    for tk in tickers:
        trades += run_ticker(tk, cfg=base, sig_cfg=sig_cfg)

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
        return
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


if __name__ == "__main__":
    main()
