#!/usr/bin/env python3
"""
excursion_analysis.py — MAE/MFE and exit-quality analysis of trade_journal.json

WHAT THIS IS FOR
----------------
Entry and exit alone tell you what happened, not whether the exit was any
good. "+80%" means one thing if the contract peaked at +85% and something
else entirely if it peaked at +400%.

This measures excursions on the UNDERLYING, not the option premium, for two
reasons. First, historical option premiums cannot be reconstructed after the
fact; daily underlying bars can, so this works retroactively on trades you
have already closed. Second, it separates two failure modes that premium-only
data blends together:

    signal wrong        -> underlying never moved your way   (bad MFE)
    structure ate it    -> underlying moved, premium didn't  (good MFE, bad P&L)

Those need completely different fixes.

WHAT IT IS NOT FOR
------------------
Finding a better exit rule. At n=30 the confidence intervals on anything you
compute here are wide enough to fit almost any conclusion. Read this as a
description of what happened, not as a search for a configuration that works.
The edge question was already answered by the 591-trade out-of-sample test.

USAGE
-----
    python excursion_analysis.py --selftest         # verify math, no network
    python excursion_analysis.py                    # analyse trade_journal.json
    python excursion_analysis.py --journal path.json --csv out.csv
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timedelta

try:
    import pandas as pd
except ImportError:
    raise SystemExit("Missing pandas. Run: pip install pandas yfinance")

# R is defined the same way the app defines it: stop distance = mult x ATR.
ATR_STOP_MULT = 1.0
ATR_WINDOW = 14
POST_EXIT_SESSIONS = 30       # how far past the exit to measure drift
PREFETCH_DAYS = 120           # calendar days of history before entry, for ATR


# ---------------------------------------------------------------------------
# Journal parsing
# ---------------------------------------------------------------------------

OPT_TICKER_RE = re.compile(r"^([A-Z][A-Z0-9.\-]{0,9})\s+(\d{4}-\d{2}-\d{2})\s+([\d.]+)([CP])$")


def parse_dt(raw: str):
    """app.py writes '%Y-%m-%d %H:%M ET' — the literal ET is not parseable."""
    if not raw:
        return None
    raw = raw.replace(" ET", "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_trade(j: dict) -> dict | None:
    """
    Normalise one journal row. Handles both the option schema written by
    close_position() and the legacy share schema from add_journal_trade().
    Returns None for rows too incomplete to measure.
    """
    tk_raw = (j.get("ticker") or "").strip()
    m = OPT_TICKER_RE.match(tk_raw)
    if m:
        underlying, expiry, strike, right = m.groups()
        is_option = True
        direction = "LONG" if right == "C" else "SHORT"
    else:
        underlying = tk_raw.split()[0] if tk_raw else ""
        is_option = False
        expiry, strike, right = None, None, None
        direction = "SHORT" if (j.get("trend") or "").lower().startswith("bear") else "LONG"

    entry_date = parse_dt(j.get("date"))
    exit_date = parse_dt(j.get("closed"))
    if not underlying or entry_date is None or exit_date is None:
        return None
    if exit_date < entry_date:
        return None

    return {
        "id": j.get("id", ""),
        "trade_type": classify(entry_date, exit_date, j.get("notes", "")),
        "features": j.get("entry_features") or {},
        "underlying": underlying,
        "is_option": is_option,
        "expiry": expiry,
        "strike": float(strike) if strike else None,
        "right": right,
        "direction": direction,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "entry_premium": _f(j.get("entry")),
        "exit_premium": _f(j.get("exit_price")),
        "outcome": j.get("outcome", ""),
        "premium_r": _f(j.get("actual_rr")),
        "notes": j.get("notes", ""),
    }


DAY_TOKEN = re.compile(r"\bDAY\b", re.IGNORECASE)


def classify(entry_date, exit_date, notes: str) -> str:
    """
    DAY or SWING.

    Same-session open and close is hard evidence and wins over anything in the
    notes — a position that opened and closed on one date was a day trade
    whatever the note says. Otherwise the DAY token in the notes decides.
    Default is SWING, matching the convention that only day trades get marked.
    """
    if entry_date == exit_date:
        return "DAY"
    if notes and DAY_TOKEN.search(notes):
        return "DAY"
    return "SWING"


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Price data
# ---------------------------------------------------------------------------

def fetch_bars(tickers: set[str], start, end) -> dict:
    """One download per ticker, cached in a dict. Network required."""
    import yfinance as yf
    out = {}
    for t in sorted(tickers):
        try:
            df = yf.download(t, start=start, end=end, interval="1d",
                             progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if df is None or df.empty:
                print(f"  ! no data for {t}", file=sys.stderr)
                continue
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            out[t] = df
        except Exception as e:
            print(f"  ! {t}: {e}", file=sys.stderr)
    return out


def wilder_atr(df: pd.DataFrame, window: int = ATR_WINDOW) -> pd.Series:
    """Wilder-smoothed ATR, matching ta.volatility.average_true_range."""
    high, low, close = df["High"], df["Low"], df["Close"]
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


# ---------------------------------------------------------------------------
# Excursion math
# ---------------------------------------------------------------------------

def measure(trade: dict, df: pd.DataFrame) -> dict | None:
    """
    Compute MAE/MFE in R over the holding window, plus post-exit drift.

    R is ATR_STOP_MULT x ATR as of the entry bar — the same unit the app uses
    to size its stop, so 1R here means the same thing it means in the app.
    """
    if df is None or df.empty:
        return None

    atr = wilder_atr(df)
    idx = df.index
    e_ts = pd.Timestamp(trade["entry_date"])
    x_ts = pd.Timestamp(trade["exit_date"])

    # Entry bar: the session on or immediately after the logged entry date.
    e_pos = idx.searchsorted(e_ts, side="left")
    if e_pos >= len(idx):
        return None
    # Exit bar: the session on or immediately before the logged exit date.
    x_pos = idx.searchsorted(x_ts, side="right") - 1
    if x_pos < e_pos:
        x_pos = e_pos

    # A same-session trade cannot be measured on daily bars: entry and exit
    # resolve to the same close, so realised move is 0 and efficiency is
    # meaningless. Say so rather than reporting zeros as if they were results.
    same_bar = (x_pos == e_pos)

    entry_px = float(df["Close"].iloc[e_pos])
    exit_px = float(df["Close"].iloc[x_pos])
    atr_entry = atr.iloc[e_pos]
    if not (isinstance(atr_entry, float) or hasattr(atr_entry, "item")):
        return None
    atr_entry = float(atr_entry)
    if not math.isfinite(atr_entry) or atr_entry <= 0 or entry_px <= 0:
        return None

    r_unit = ATR_STOP_MULT * atr_entry
    window = df.iloc[e_pos:x_pos + 1]
    hi, lo = float(window["High"].max()), float(window["Low"].min())
    long = trade["direction"] == "LONG"

    if long:
        mfe_px, mae_px = hi - entry_px, entry_px - lo
        realised_px = exit_px - entry_px
    else:
        mfe_px, mae_px = entry_px - lo, hi - entry_px
        realised_px = entry_px - exit_px

    mfe_r = mfe_px / r_unit
    mae_r = mae_px / r_unit
    realised_r = realised_px / r_unit

    # Exit efficiency: how much of the move that was actually available did
    # you take? 1.0 = sold the high. Negative = the trade never went your way.
    efficiency = (realised_px / mfe_px) if mfe_px > 1e-9 else None

    # Post-exit drift: did continuing to hold have helped or hurt?
    post = df.iloc[x_pos + 1:x_pos + 1 + POST_EXIT_SESSIONS]
    if len(post) >= 5:
        post_ext = (float(post["High"].max()) - exit_px) if long \
            else (exit_px - float(post["Low"].min()))
        post_r = post_ext / r_unit
        post_n = len(post)
    else:
        post_r, post_n = None, len(post)

    return {
        **trade,
        "daily_bar_measurable": not same_bar,
        "bars_held": x_pos - e_pos + 1,
        "entry_px": round(entry_px, 2),
        "exit_px": round(exit_px, 2),
        "atr_entry": round(atr_entry, 3),
        "mfe_r": round(mfe_r, 3),
        "mae_r": round(mae_r, 3),
        "realised_r": round(realised_r, 3),
        "efficiency": round(efficiency, 3) if efficiency is not None else None,
        "post_exit_r": round(post_r, 3) if post_r is not None else None,
        "post_exit_bars": post_n,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def mean(xs):
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    return sum(xs) / len(xs) if xs else None


def median(xs):
    xs = sorted(x for x in xs if x is not None and math.isfinite(x))
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def fmt(v, nd=2, suffix=""):
    return "n/a" if v is None else f"{v:+.{nd}f}{suffix}" if nd else f"{v}{suffix}"


def report(rows: list[dict]) -> None:
    W = 78
    print("=" * W)
    print("EXCURSION ANALYSIS — measured on the underlying, in R units")
    print("=" * W)
    if not rows:
        print("No measurable trades. Need `date`, `closed` and a ticker on each row.")
        return

    day = [r for r in rows if r["trade_type"] == "DAY"]
    swing = [r for r in rows if r["trade_type"] == "SWING"]

    print(f"\nTrades          : {len(rows)}  ({len(swing)} SWING, {len(day)} DAY)")
    print(f"R unit          : {ATR_STOP_MULT:g} x ATR({ATR_WINDOW}) at entry")
    print("\nSWING and DAY are reported separately and never pooled. They differ")
    print("in theta exposure, gap risk and how many independent bets they")
    print("represent, so an average across both describes neither.")

    if swing:
        _section("SWING", swing)
    if day:
        _day_section(day)

    if swing and len(swing) < 30:
        print()
        print("!" * W)
        print(f"n={len(swing)} SWING trades. Standard error on a per-trade mean is")
        print(f"roughly {1.5/math.sqrt(len(swing)):.2f} R — wide enough to contain almost any")
        print("conclusion. Read the numbers above as description, not as findings.")
        print("!" * W)


def _day_section(rows: list[dict]) -> None:
    W = 78
    print("\n" + "=" * W)
    print(f"DAY TRADES ({len(rows)})")
    print("=" * W)
    print("NOT MEASURED. Daily bars cannot resolve an intraday entry and exit —")
    print("both collapse to the same close, which is why realised move and exit")
    print("efficiency would read 0.00 for every row. Those would be artifacts,")
    print("not results, so they are omitted rather than printed.")
    print()
    print("Measuring these needs intraday bars. yfinance serves 1-minute data")
    print("for the trailing 30 days and 5-minute for 60 — so it is possible, but")
    print("only within that window. Trades older than that can no longer be")
    print("reconstructed at all.")
    print()
    print(f"  {'Ticker':<8}{'Dir':<6}{'Entry':<12}{'PremR':>8}  Outcome")
    for r in sorted(rows, key=lambda x: x["entry_date"]):
        pr = f"{r['premium_r']:+.2f}" if r["premium_r"] is not None else "n/a"
        print(f"  {r['underlying']:<8}{r['direction']:<6}{str(r['entry_date']):<12}"
              f"{pr:>8}  {r['outcome']}")
    prem = [r["premium_r"] for r in rows if r["premium_r"] is not None]
    if prem:
        m = mean(prem)
        print(f"\n  Mean premium R  : {m:+.3f} over {len(prem)} trade(s)")
        if len(prem) < 30:
            print(f"  At n={len(prem)} this is noise. It is shown so the trades are not")
            print("  invisible, not because it means anything yet.")


def _section(label: str, rows: list[dict]) -> None:
    W = 78
    print("\n" + "=" * W)
    print(f"{label} TRADES ({len(rows)})")
    print("=" * W)

    measurable = [r for r in rows if r.get("daily_bar_measurable", True)]
    if len(measurable) < len(rows):
        print(f"({len(rows) - len(measurable)} row(s) resolved to a single daily bar "
              f"and were dropped.)\n")
    if not measurable:
        print("Nothing measurable on daily bars.")
        return

    print(f"  {'Ticker':<8}{'Dir':<6}{'Bars':>5}{'MFE R':>8}{'MAE R':>8}"
          f"{'Real R':>8}{'Eff':>7}{'Post R':>8}  Outcome")
    for r in sorted(measurable, key=lambda x: x["entry_date"]):
        eff_s = f"{r['efficiency']:.2f}" if r["efficiency"] is not None else "n/a"
        post_s = f"{r['post_exit_r']:.2f}" if r["post_exit_r"] is not None else "n/a"
        print(f"  {r['underlying']:<8}{r['direction']:<6}{r['bars_held']:>5}"
              f"{r['mfe_r']:>8.2f}{r['mae_r']:>8.2f}{r['realised_r']:>8.2f}"
              f"{eff_s:>7}{post_s:>8}  {r['outcome']}")

    mfe, mae = mean([r["mfe_r"] for r in measurable]), mean([r["mae_r"] for r in measurable])
    print("\n" + "-" * W)
    print("EDGE RATIO")
    print("-" * W)
    if mfe is not None and mae is not None:
        print(f"  Mean MFE            : {mfe:.3f} R")
        print(f"  Mean MAE            : {mae:.3f} R")
    if mfe and mae and mae > 1e-9:
        ratio = mfe / mae
        print(f"  Edge ratio MFE/MAE  : {ratio:.2f}")
        print()
        if ratio > 1.15:
            print("  Above 1.0 — favourable excursion exceeded adverse. Suggestive")
            print("  that entries carry directional information. Not a significance")
            print("  test, and not a substitute for the out-of-sample result.")
        elif ratio < 0.87:
            print("  Below 1.0 — adverse excursion exceeded favourable. Entries went")
            print("  the wrong way more than the right way.")
        else:
            print("  Near 1.0 — favourable and adverse excursions are balanced, which")
            print("  is what a signal with no directional information looks like.")

    print("\n" + "-" * W)
    print("EXIT QUALITY")
    print("-" * W)
    eff = [r["efficiency"] for r in measurable if r["efficiency"] is not None]
    if eff:
        print(f"  Mean efficiency     : {mean(eff):.2f}   (1.0 = exited at the high)")
        print(f"  Median efficiency   : {median(eff):.2f}")
        print(f"  Exits above 0.7     : {sum(1 for e in eff if e > 0.7)}/{len(eff)}")
        print(f"  Exits below 0.3     : {sum(1 for e in eff if e < 0.3)}/{len(eff)}")
    post = [r["post_exit_r"] for r in measurable if r["post_exit_r"] is not None]
    if post:
        print(f"\n  Mean post-exit run  : {mean(post):+.2f} R in the "
              f"{POST_EXIT_SESSIONS} sessions after exit")
        print("  (How far it kept going your way AFTER you were out. Large and")
        print("   positive means exits were early; near zero means about right.)")

    print("\n" + "-" * W)
    print("SIGNAL vs STRUCTURE")
    print("-" * W)
    both = [r for r in measurable if r["premium_r"] is not None]
    if both:
        good_move_bad_pnl = [r for r in both if r["realised_r"] > 0.25 and r["premium_r"] < 0]
        bad_move = [r for r in both if r["mfe_r"] < 0.5]
        print(f"  Underlying moved your way, option still lost : "
              f"{len(good_move_bad_pnl)}/{len(both)}")
        print("    -> theta, IV crush or spread ate a correct call")
        print(f"  Underlying barely moved at all (MFE < 0.5R)  : "
              f"{len(bad_move)}/{len(both)}")
        print("    -> the entry signal itself was wrong")
    else:
        print("  No premium data on these rows — cannot separate the two.")

    feat = [r for r in measurable if r.get("features")]
    if feat:
        print("\n" + "-" * W)
        print(f"ENTRY FEATURES CAPTURED — {len(feat)}/{len(measurable)} trades")
        print("-" * W)
        keys = ("adx", "rsi", "rvol", "ext_atr", "rs_20d")
        for k in keys:
            vals = [r["features"].get(k) for r in feat]
            m = mean([v for v in vals if v is not None])
            if m is not None:
                print(f"  mean {k:<10}: {m:+.3f}")
        print()
        print("  Logged only. No breakdown by outcome is shown, and that is")
        print(f"  deliberate: slicing {len(feat)} trades by ADX or RSI bucket will")
        print("  always surface a bucket that looks good, because that is what")
        print("  random data does. Revisit at a few hundred trades, with the")
        print("  question written down before you look.")


def write_csv(rows, path):
    if not rows:
        return
    cols = ["trade_type", "underlying", "direction", "entry_date", "exit_date", "bars_held",
            "entry_px", "exit_px", "atr_entry", "mfe_r", "mae_r", "realised_r",
            "efficiency", "post_exit_r", "premium_r", "outcome"]
    pd.DataFrame([{c: r.get(c) for c in cols} for r in rows]).to_csv(path, index=False)
    print(f"\nPer-trade detail written to {path}")


# ---------------------------------------------------------------------------
# Self-test — synthetic bars, no network
# ---------------------------------------------------------------------------

def selftest() -> int:
    import numpy as np
    dates = pd.bdate_range("2026-01-02", periods=90)
    # Flat 100 with 2.0 ATR, then a clean run to 110 and a fade back to 104.
    close = np.concatenate([np.full(40, 100.0),
                            np.linspace(100, 110, 20),
                            np.linspace(110, 104, 30)])
    df = pd.DataFrame({"Open": close, "Close": close,
                       "High": close + 1.0, "Low": close - 1.0}, index=dates)

    entry_i, exit_i = 40, 60
    trade = {"id": "T1", "underlying": "TEST", "is_option": True, "expiry": None,
             "strike": None, "right": "C", "direction": "LONG",
             "entry_date": dates[entry_i].date(), "exit_date": dates[exit_i].date(),
             "entry_premium": 2.0, "exit_premium": 3.0, "outcome": "WIN",
             "premium_r": 0.5, "notes": ""}
    m = measure(trade, df)
    assert m is not None, "measure returned None"

    atr = m["atr_entry"]
    print(f"ATR at entry        : {atr:.3f}  (bars span 2.0, expect ~2.0)")
    assert 1.8 < atr < 2.2, atr

    # Peak high within window is 110+1 = 111 -> MFE 11 px
    exp_mfe = 11.0 / atr
    print(f"MFE                 : {m['mfe_r']:.2f} R  (expect {exp_mfe:.2f})")
    assert abs(m["mfe_r"] - exp_mfe) < 0.05

    # Low within window is 100-1 = 99 -> MAE 1 px
    exp_mae = 1.0 / atr
    print(f"MAE                 : {m['mae_r']:.2f} R  (expect {exp_mae:.2f})")
    assert abs(m["mae_r"] - exp_mae) < 0.05

    # Exited at the top close (110) -> realised 10 px, efficiency 10/11
    print(f"Realised            : {m['realised_r']:.2f} R")
    print(f"Efficiency          : {m['efficiency']:.2f}  (expect ~0.91)")
    assert abs(m["efficiency"] - 10.0 / 11.0) < 0.02

    # After exit the series fades, so post-exit favourable extension is ~0
    print(f"Post-exit run       : {m['post_exit_r']:+.2f} R  (expect ~0, it faded)")
    assert m["post_exit_r"] < 0.6

    # Short direction mirrors
    trade_s = {**trade, "direction": "SHORT"}
    ms = measure(trade_s, df)
    print(f"SHORT mirror MFE    : {ms['mfe_r']:.2f} R  (expect {exp_mae:.2f})")
    assert abs(ms["mfe_r"] - exp_mae) < 0.05

    # Ticker parsing
    p = parse_trade({"id": "x", "ticker": "NVDA 2026-12-18 100C",
                     "date": "2026-07-01 10:00 ET", "closed": "2026-07-20 15:30 ET",
                     "entry": 2.0, "exit_price": 1.0, "outcome": "LOSS",
                     "actual_rr": -0.5, "trend": "Bullish"})
    assert p["underlying"] == "NVDA" and p["direction"] == "LONG", p
    print(f"Option row parsed   : {p['underlying']} {p['direction']} "
          f"{p['entry_date']} -> {p['exit_date']}")

    p2 = parse_trade({"id": "y", "ticker": "TSLA", "date": "2026-07-01",
                      "closed": "2026-07-05", "entry": 300, "exit_price": 310,
                      "outcome": "WIN", "trend": "Bearish"})
    assert p2["direction"] == "SHORT" and not p2["is_option"]
    print(f"Share row parsed    : {p2['underlying']} {p2['direction']}")

    bad = parse_trade({"id": "z", "ticker": "", "date": "", "closed": ""})
    assert bad is None
    print("Malformed row       : skipped, not guessed")
    print("\nAll self-tests passed.")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--journal", default="trade_journal.json")
    ap.add_argument("--csv", default=None, help="also write per-trade detail here")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    try:
        with open(args.journal) as f:
            raw = json.load(f)
    except FileNotFoundError:
        print(f"No journal at {args.journal}. Pass --journal <path>.")
        return 2
    if isinstance(raw, dict):
        raw = raw.get("trades", [])

    trades = [t for t in (parse_trade(j) for j in raw) if t]
    skipped = len(raw) - len(trades)
    if skipped:
        print(f"Skipped {skipped} row(s) missing dates or ticker.\n")
    if not trades:
        print("Nothing measurable in the journal.")
        return 1

    start = min(t["entry_date"] for t in trades) - timedelta(days=PREFETCH_DAYS)
    end = max(t["exit_date"] for t in trades) + timedelta(
        days=int(POST_EXIT_SESSIONS * 1.6) + 5)
    print(f"Fetching daily bars {start} -> {end} for "
          f"{len({t['underlying'] for t in trades})} underlying(s)...")
    bars = fetch_bars({t["underlying"] for t in trades}, start, end)

    rows, unmeasured = [], 0
    for t in trades:
        m = measure(t, bars.get(t["underlying"]))
        if m:
            rows.append(m)
        else:
            unmeasured += 1
    if unmeasured:
        print(f"{unmeasured} trade(s) had no usable price data.\n")

    report(rows)
    if args.csv:
        write_csv(rows, args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
