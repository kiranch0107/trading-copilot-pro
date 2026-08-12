"""
Option Liquidity Check — pick a watchlist on cost, not on past returns
=======================================================================
Ranks candidate tickers by the quality of their OPTION market: bid-ask spread,
open interest, volume, and whether contracts are affordable on your account.

WHY THIS CRITERION AND NOT BACKTEST PERFORMANCE
-----------------------------------------------
Selecting tickers because they scored well in a backtest is curve-fitting: the
universe diagnostic on 41 tickers found NO structural edge, which means past
per-ticker results were mostly noise and will not repeat.

Liquidity is different. It is:
  • structurally persistent — a mega-cap with tight options this month will
    almost certainly have tight options next month;
  • a CERTAIN cost, not a speculative gain — at a 15% round-trip spread, a 2:1
    payoff at a 40% win rate goes from +0.10 to -0.05 expected value. The
    spread alone decides the sign;
  • forward-looking — it describes the market you will actually trade in, not
    a sample of history you already know the answer to.

So: choose names whose option markets are cheap to trade, then let the signal
be whatever it is. This is the one selection decision that is defensible.

Run
---
    pip install yfinance pandas
    python liquidity_check.py
    python liquidity_check.py --account 1500 --risk 5
    python liquidity_check.py --tickers TSLA,NVDA,AAPL,AMD,GOOGL
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, date

import pandas as pd

try:
    import yfinance as yf
except ImportError:
    raise SystemExit("Missing yfinance. Run: pip install yfinance pandas")

DEFAULT_CANDIDATES = [
    "TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "META", "ROKU",
    "AMD", "GOOGL", "NFLX", "INTC", "QQQ", "SPY",
]

MAX_SPREAD_PCT = 15.0   # above this, the round trip eats a 2:1 edge outright


def nearest_expiry(ticker: str, min_dte: int) -> tuple[str | None, int | None]:
    try:
        for e in (yf.Ticker(ticker).options or []):
            dte = (datetime.strptime(e, "%Y-%m-%d").date() - date.today()).days
            if dte >= min_dte:
                return e, dte
    except Exception:
        pass
    return None, None


def assess(ticker: str, min_dte: int, account: float, risk_pct: float) -> dict:
    out = {"ticker": ticker}
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="5d")
        if hist is None or hist.empty:
            return {**out, "error": "no price data"}
        spot = float(hist["Close"].iloc[-1])
        out["spot"] = round(spot, 2)

        expiry, dte = nearest_expiry(ticker, min_dte)
        if not expiry:
            return {**out, "error": f"no expiry >= {min_dte} DTE"}
        out["expiry"], out["dte"] = expiry, dte

        chain = tk.option_chain(expiry)
        calls = chain.calls
        if calls is None or calls.empty:
            return {**out, "error": "empty chain"}

        # Look at strikes within +-5% of spot: the region actually traded.
        near = calls[(calls["strike"] >= spot * 0.95) &
                     (calls["strike"] <= spot * 1.05)].copy()
        if near.empty:
            return {**out, "error": "no near-the-money strikes"}

        near["mid"] = (near["bid"] + near["ask"]) / 2
        near = near[(near["bid"] > 0) & (near["ask"] > 0) & (near["mid"] > 0)]
        if near.empty:
            return {**out, "error": "no two-sided quotes"}

        near["spread_pct"] = (near["ask"] - near["bid"]) / near["mid"] * 100

        out["med_spread_pct"] = round(float(near["spread_pct"].median()), 1)
        out["med_oi"]         = int(near["openInterest"].fillna(0).median())
        out["med_vol"]        = int(near["volume"].fillna(0).median())
        out["cheapest_mid"]   = round(float(near["mid"].min()), 2)

        # Affordability: one contract costs mid x 100, and on a long option
        # that IS the maximum loss, so it must fit the risk budget.
        budget = account * risk_pct / 100
        out["risk_budget"]  = round(budget, 2)
        out["affordable"]   = bool(out["cheapest_mid"] * 100 <= budget)
        out["cheapest_cost"] = round(out["cheapest_mid"] * 100, 2)
        return out
    except Exception as e:
        return {**out, "error": f"{type(e).__name__}: {e}"}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", default=",".join(DEFAULT_CANDIDATES))
    p.add_argument("--min-dte", type=int, default=9)
    p.add_argument("--account", type=float, default=1500)
    p.add_argument("--risk", type=float, default=5.0,
                   help="Risk %% of account per trade")
    a = p.parse_args()

    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    print("=" * 84)
    print("OPTION LIQUIDITY CHECK")
    print(f"Account ${a.account:,.0f} · risk {a.risk}% "
          f"(= ${a.account*a.risk/100:,.0f}/trade) · min {a.min_dte} DTE")
    print("Ranked by median bid-ask spread on strikes within ±5% of spot.")
    print("=" * 84)

    rows = []
    for t in tickers:
        time.sleep(0.6)          # be gentle with the shared rate limit
        r = assess(t, a.min_dte, a.account, a.risk)
        rows.append(r)
        if "error" in r:
            print(f"  {t:<6} — {r['error']}")

    ok = [r for r in rows if "error" not in r]
    if not ok:
        print("\nNo usable data. Yahoo may be rate-limiting; try again shortly.")
        return

    ok.sort(key=lambda r: r["med_spread_pct"])
    print(f"\n{'Ticker':<8}{'Spot':>9}{'Spread':>9}{'Med OI':>9}{'Med Vol':>9}"
          f"{'Cheapest':>10}{'Affordable':>12}{'Verdict':>10}")
    print("-" * 84)
    for r in ok:
        if r["med_spread_pct"] <= 5:
            v = "excellent"
        elif r["med_spread_pct"] <= 10:
            v = "good"
        elif r["med_spread_pct"] <= MAX_SPREAD_PCT:
            v = "marginal"
        else:
            v = "AVOID"
        print(f"{r['ticker']:<8}{r['spot']:>9,.2f}{r['med_spread_pct']:>8.1f}%"
              f"{r['med_oi']:>9,}{r['med_vol']:>9,}"
              f"{r['cheapest_cost']:>9,.0f}"
              f"{'yes' if r['affordable'] else 'NO':>12}{v:>10}")

    print("\n" + "=" * 84)
    print("HOW TO READ THIS")
    print("=" * 84)
    print(f"""
  Spread is the cost you pay with certainty on every round trip. At a 40% win
  rate and a 2:1 payoff (TP+100/SL-50), expected value is +0.10 per unit
  risked. A {MAX_SPREAD_PCT:.0f}% round-trip spread turns that NEGATIVE. Anything marked
  AVOID is not a signal problem — it is a cost problem you cannot out-trade.

  'Affordable' asks whether the cheapest near-the-money contract fits your
  risk budget, because on a long option the premium IS the maximum loss.
  A 'NO' means you would have to exceed your own risk rule to take any trade
  in that name — which is a reason to drop it regardless of how it looks.

  Pick your watchlist from the top of this table. Do NOT pick from whichever
  names happened to score well in the backtest; that is selecting on noise.
""")


if __name__ == "__main__":
    main()
