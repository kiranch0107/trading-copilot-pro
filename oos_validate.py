#!/usr/bin/env python3
"""
oos_validate.py — Out-of-sample validation harness for Trading Copilot ELITE.

PURPOSE
-------
The August parameter sweep tested ~60 configurations on a 7-ticker universe
(TSLA, NVDA, AAPL, MSFT, AMZN, META, SPY). The winning config was found by
searching that space. This harness tests that ONE frozen config against
tickers that were never part of the search.

The whole point is that you run this ONCE. If you tweak the config after
seeing the result, you are back to searching, and the p-value is meaningless.
This script enforces that by hashing the config and refusing to pretend a
changed config is still an out-of-sample test.

USAGE
-----
    python oos_validate.py                 # run the frozen test
    python oos_validate.py --selftest      # verify the parser, no backtest run
    python oos_validate.py --show-config   # print frozen config + hash, exit

Place next to backtest.py in the trading-copilot-pro repo.
"""

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

# ============================================================================
# PRE-REGISTERED CONFIG — FROZEN 2026-08-21
# ----------------------------------------------------------------------------
# Derived from the best-supported cell of the August sweep:
#   NVDA,META,MSFT / ADX>=35 / ATR stop 1.25 / ATR tgt 4.0 / hold 30 / regime ON
#     5yr : 59 trades, +0.731 R, PF 2.76
#    15yr : 149 trades, +0.286 R, PF 1.51   <- already degrading
#
# DO NOT EDIT THESE VALUES AFTER SEEING A RESULT.
# ============================================================================
FROZEN = {
    "adx_min": 35.0,
    "atr_stop": 1.25,
    "atr_tgt": 4.0,
    "max_hold": 30,
    "slippage_bps": 2.0,
    "use_regime": True,
    "years": 15,
    # Held-out universe: liquid US large/mid-cap names of similar character to
    # NVDA/META/MSFT, none of which appeared anywhere in the August sweep.
    "tickers": [
        "GOOGL", "AVGO", "AMD", "NFLX", "CRM", "ADBE",
        "QCOM", "MU", "ORCL", "NOW", "PANW", "LRCX",
    ],
}

# Pre-committed pass criteria. Decided BEFORE the run.
CRITERIA = {
    "min_trades": 60,        # below this, the test is uninformative either way
    "min_expectancy": 0.20,  # R per trade
    "min_profit_factor": 1.30,
    "require_ci_above_zero": True,  # 95% CI lower bound must exceed 0
}

LOCK_FILE = "oos_validate.lock.json"
LEDGER_FILE = "oos_validate.ledger.json"
BACKTEST = "backtest.py"


# ---------------------------------------------------------------------------
# Config integrity
# ---------------------------------------------------------------------------

def config_hash():
    blob = json.dumps({"frozen": FROZEN, "criteria": CRITERIA}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def check_lock():
    """Returns (is_first_run, prior_hash_or_None)."""
    h = config_hash()
    if not os.path.exists(LOCK_FILE):
        with open(LOCK_FILE, "w") as f:
            json.dump({
                "config_hash": h,
                "frozen_at": datetime.now(timezone.utc).isoformat(),
                "frozen": FROZEN,
                "criteria": CRITERIA,
            }, f, indent=2)
        return True, None

    with open(LOCK_FILE) as f:
        prior = json.load(f)
    return False, prior.get("config_hash")


# ---------------------------------------------------------------------------
# Running + parsing backtest.py
# ---------------------------------------------------------------------------

def build_cmd():
    cmd = [
        sys.executable, BACKTEST,
        "--tickers", ",".join(FROZEN["tickers"]),
        "--years", str(FROZEN["years"]),
        "--adx-min", str(FROZEN["adx_min"]),
        "--atr-stop", str(FROZEN["atr_stop"]),
        "--atr-tgt", str(FROZEN["atr_tgt"]),
        "--max-hold", str(FROZEN["max_hold"]),
        "--slippage-bps", str(FROZEN["slippage_bps"]),
    ]
    if FROZEN["use_regime"]:
        cmd.append("--use-regime")
    return cmd


PER_TICKER_RE = re.compile(
    r"^\s*([A-Z][A-Z0-9.\-]{0,9})\s+"   # ticker
    r"(\d+)\s+"                          # trades
    r"(-?[\d.]+)%\s+"                    # win rate
    r"(-?[\d.]+)\s+"                     # avg R
    r"(-?[\d.]+)\s+"                     # total R
    r"(-?[\d.]+)\s+"                     # profit factor
    r"(-?[\d.]+)\s+"                     # max DD
    r"(-?[\d.]+)\s*$"                    # avg hold
)

AGG_PATTERNS = {
    "total_trades":  re.compile(r"Total trades\s*:\s*(\d+)"),
    "win_rate":      re.compile(r"Win rate\s*:\s*(-?[\d.]+)%"),
    "expectancy":    re.compile(r"Expectancy\s*:\s*([+-]?[\d.]+)\s*R"),
    "total_return":  re.compile(r"Total return\s*:\s*([+-]?[\d.]+)\s*R"),
    "profit_factor": re.compile(r"Profit factor\s*:\s*(-?[\d.]+)"),
    "max_drawdown":  re.compile(r"Max drawdown\s*:\s*([+-]?[\d.]+)\s*R"),
}


def parse_output(text):
    """Extract per-ticker rows and the aggregate block."""
    per_ticker = []
    in_table = False
    for line in text.splitlines():
        if "PER-TICKER RESULTS" in line:
            in_table = True
            continue
        if "AGGREGATE" in line:
            in_table = False
            continue
        if not in_table:
            continue
        if line.strip().startswith("Ticker") or set(line.strip()) <= set("- "):
            continue
        m = PER_TICKER_RE.match(line)
        if m:
            per_ticker.append({
                "ticker": m.group(1),
                "trades": int(m.group(2)),
                "win_rate": float(m.group(3)) / 100.0,
                "avg_r": float(m.group(4)),
                "total_r": float(m.group(5)),
                "profit_factor": float(m.group(6)),
                "max_dd": float(m.group(7)),
                "avg_hold": float(m.group(8)),
            })

    agg = {}
    for key, pat in AGG_PATTERNS.items():
        m = pat.search(text)
        if m:
            agg[key] = float(m.group(1))
    if "total_trades" in agg:
        agg["total_trades"] = int(agg["total_trades"])
    if "win_rate" in agg:
        agg["win_rate"] = agg["win_rate"] / 100.0

    return per_ticker, agg


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

DEFAULT_SIGMA = 1.8  # fallback per-trade R std when the two-point solve fails


def two_point_variance(win_rate, expectancy, profit_factor):
    """
    Recover an approximate per-trade R variance from summary stats.

    With win rate w, gross win mass A = w*W and gross loss mass B = (1-w)*L:
        A - B = E        (expectancy)
        A / B = PF       (profit factor)
    =>  B = E / (PF - 1),  A = PF * B
    Then treat outcomes as two-point: +W with prob w, -L with prob (1-w).

    This understates variance slightly (real outcomes are spread, not two-point)
    but is the best available estimate without per-trade logs. Understating
    variance makes the t-stat LOOK BIGGER, so treat a marginal pass with
    suspicion.
    """
    w = win_rate
    if not (0.02 < w < 0.98):
        return DEFAULT_SIGMA ** 2
    if abs(profit_factor - 1.0) < 0.05 or abs(expectancy) < 1e-9:
        return DEFAULT_SIGMA ** 2
    try:
        B = expectancy / (profit_factor - 1.0)
        A = profit_factor * B
        W = A / w
        L = B / (1.0 - w)
        if W <= 0 or L <= 0:
            return DEFAULT_SIGMA ** 2
        var = w * (W - expectancy) ** 2 + (1 - w) * (-L - expectancy) ** 2
        if not math.isfinite(var) or var <= 0:
            return DEFAULT_SIGMA ** 2
        return var
    except ZeroDivisionError:
        return DEFAULT_SIGMA ** 2


def pooled_stats(per_ticker, agg):
    """Law of total variance across tickers -> SE, t, 95% CI on expectancy."""
    n_total = sum(t["trades"] for t in per_ticker)
    if n_total == 0:
        return None

    e_pool = agg.get("expectancy")
    if e_pool is None:
        e_pool = sum(t["avg_r"] * t["trades"] for t in per_ticker) / n_total

    within = 0.0
    between = 0.0
    for t in per_ticker:
        v = two_point_variance(t["win_rate"], t["avg_r"], t["profit_factor"])
        within += t["trades"] * v
        between += t["trades"] * (t["avg_r"] - e_pool) ** 2
    var_pool = (within + between) / n_total

    se = math.sqrt(var_pool / n_total)
    t_stat = e_pool / se if se > 0 else float("nan")
    # normal approximation is fine at n >= 60
    p_two = math.erfc(abs(t_stat) / math.sqrt(2))
    ci_lo = e_pool - 1.96 * se
    ci_hi = e_pool + 1.96 * se

    return {
        "n": n_total,
        "expectancy": e_pool,
        "sigma": math.sqrt(var_pool),
        "se": se,
        "t": t_stat,
        "p_value": p_two,
        "ci_low": ci_lo,
        "ci_high": ci_hi,
    }


def verdict(stats, agg):
    reasons = []
    ok = True

    if stats["n"] < CRITERIA["min_trades"]:
        ok = False
        reasons.append(
            f"only {stats['n']} trades (need >= {CRITERIA['min_trades']}) "
            "— inconclusive, not a fail")
    if stats["expectancy"] < CRITERIA["min_expectancy"]:
        ok = False
        reasons.append(
            f"expectancy {stats['expectancy']:+.3f} R < "
            f"{CRITERIA['min_expectancy']:+.2f} R")
    pf = agg.get("profit_factor")
    if pf is not None and pf < CRITERIA["min_profit_factor"]:
        ok = False
        reasons.append(f"profit factor {pf:.2f} < {CRITERIA['min_profit_factor']:.2f}")
    if CRITERIA["require_ci_above_zero"] and stats["ci_low"] <= 0:
        ok = False
        reasons.append(f"95% CI lower bound {stats['ci_low']:+.3f} R does not clear 0")

    return ok, reasons


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

def append_ledger(entry):
    ledger = []
    if os.path.exists(LEDGER_FILE):
        try:
            with open(LEDGER_FILE) as f:
                ledger = json.load(f)
        except Exception:
            ledger = []
    ledger.append(entry)
    with open(LEDGER_FILE, "w") as f:
        json.dump(ledger, f, indent=2)
    return len(ledger)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def report(per_ticker, agg, stats, run_number, hash_changed):
    W = 74
    print("=" * W)
    print("OUT-OF-SAMPLE VALIDATION — TRADING COPILOT ELITE")
    print("=" * W)
    print(f"Config hash   : {config_hash()}")
    print(f"Run number    : {run_number}")
    print(f"Held-out      : {', '.join(FROZEN['tickers'])}")
    print(f"Params        : ADX>={FROZEN['adx_min']}  stop {FROZEN['atr_stop']}x  "
          f"tgt {FROZEN['atr_tgt']}x  hold {FROZEN['max_hold']}  "
          f"regime {'ON' if FROZEN['use_regime'] else 'OFF'}")
    print()

    if hash_changed:
        print("!" * W)
        print("CONFIG CHANGED SINCE THE LOCK FILE WAS WRITTEN.")
        print("This is no longer an out-of-sample test. The p-value below is")
        print("not valid. Delete the lock only if you are starting a genuinely")
        print("new pre-registration on a fresh held-out universe.")
        print("!" * W)
        print()

    if run_number > 1:
        print(f"NOTE: this is run #{run_number} against the same held-out set.")
        print("      Each repeat spends some of the set's independence.")
        print()

    print("PER-TICKER")
    print(f"  {'Ticker':<8}{'Trades':>7}{'Win%':>7}{'Avg R':>9}{'Total R':>9}{'PF':>7}")
    for t in sorted(per_ticker, key=lambda x: -x["avg_r"]):
        print(f"  {t['ticker']:<8}{t['trades']:>7}{t['win_rate']*100:>6.0f}%"
              f"{t['avg_r']:>9.3f}{t['total_r']:>9.1f}{t['profit_factor']:>7.2f}")
    print()

    print("AGGREGATE")
    print(f"  Trades        : {stats['n']}")
    print(f"  Win rate      : {agg.get('win_rate', float('nan'))*100:.1f}%")
    print(f"  Expectancy    : {stats['expectancy']:+.3f} R per trade")
    print(f"  Profit factor : {agg.get('profit_factor', float('nan')):.2f}")
    print(f"  Total return  : {agg.get('total_return', float('nan')):+.1f} R")
    print()

    print("SIGNIFICANCE  (sigma estimated from summary stats — approximate)")
    print(f"  Est. per-trade sigma : {stats['sigma']:.2f} R")
    print(f"  Standard error       : {stats['se']:.3f} R")
    print(f"  t-statistic          : {stats['t']:+.2f}")
    print(f"  p-value (two-sided)  : {stats['p_value']:.4f}")
    print(f"  95% CI on expectancy : [{stats['ci_low']:+.3f}, {stats['ci_high']:+.3f}] R")
    print()

    ok, reasons = verdict(stats, agg)
    print("=" * W)
    if ok:
        print("RESULT: PASS — the edge survived on tickers it was never fitted to.")
        print()
        print("This is real evidence, but it is one test. Before risking size:")
        print("  - the sweep's own 15-year run degraded from +0.731 R to +0.286 R;")
        print("    expect live results near the lower end, not the 5-year peak")
        print("  - forward-test on the paper protocol before scaling")
    else:
        print("RESULT: FAIL — the edge did not carry to untouched tickers.")
        print()
        for r in reasons:
            print(f"  - {r}")
        print()
        print("The most likely reading: the August result was curve-fit to the")
        print("specific tickers and window it was searched over. That is the")
        print("expected outcome of a ~60-cell sweep, and finding it now is worth")
        print("far more than finding it later with real money.")
    print("=" * W)
    print()
    print("Whatever this says, do not re-run with adjusted parameters and treat")
    print("the new number as out-of-sample. That is the trap this file exists")
    print("to prevent.")
    return ok


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

SAMPLE = """
TRADING COPILOT ELITE - HISTORICAL BACKTEST (real data)
==========================================================
Tickers    : NVDA, META, MSFT
History    : 15 years daily
ADX min    : 35.0   ATR stop: 1.25  ATR tgt: 4.0   Min R:R: 0.5
Max hold   : 30 bars  Slippage: 2.0bps/side  Regime filter: ON (SPY 200-SMA)
==========================================================

PER-TICKER RESULTS
Ticker    Trades  Win%    Avg R     Total R    PF     MaxDD   Hold
------    ------  ----    -----     -------    --     -----   ----
NVDA      43      58%     0.618     26.6       2.52   -3.0    7
META      50      38%     0.068     3.4        1.11   -13.0   6
MSFT      56      36%     0.226     12.7       1.35   -12.5   6

==========================================================
AGGREGATE (all tickers combined)
==========================================================
  Total trades  : 149
  Win rate      : 43.0%
  Expectancy    : +0.286 R per trade
  Total return  : +42.7 R
  Profit factor : 1.51
  Max drawdown  : -13.0 R
  Avg hold      : 6.2 bars
  Best / worst  : +5.74 R / -1.85 R
"""


def selftest():
    per_ticker, agg = parse_output(SAMPLE)
    assert len(per_ticker) == 3, f"expected 3 tickers, got {len(per_ticker)}"
    assert per_ticker[0]["ticker"] == "NVDA"
    assert per_ticker[0]["trades"] == 43
    assert abs(per_ticker[0]["avg_r"] - 0.618) < 1e-9
    assert agg["total_trades"] == 149
    assert abs(agg["expectancy"] - 0.286) < 1e-9
    assert abs(agg["profit_factor"] - 1.51) < 1e-9

    stats = pooled_stats(per_ticker, agg)
    print("Parser OK — 3 tickers, 149 trades, +0.286 R aggregate.")
    print(f"Recovered sigma {stats['sigma']:.2f} R, SE {stats['se']:.3f}, "
          f"t {stats['t']:+.2f}, p {stats['p_value']:.4f}")
    print(f"95% CI [{stats['ci_low']:+.3f}, {stats['ci_high']:+.3f}] R")
    print()
    print("Sanity check against the known 5-year cell (59 trades, +0.731 R, PF 2.76):")
    fake = [{"ticker": "COMBO", "trades": 59, "win_rate": 0.576, "avg_r": 0.731,
             "total_r": 43.1, "profit_factor": 2.76, "max_dd": -4.1, "avg_hold": 7.7}]
    fa = {"expectancy": 0.731, "profit_factor": 2.76, "win_rate": 0.576}
    fs = pooled_stats(fake, fa)
    print(f"  t {fs['t']:+.2f}, p {fs['p_value']:.4f} for a SINGLE pre-specified test")
    print(f"  Bonferroni across ~60 sweep cells: p ~ {min(1.0, fs['p_value']*60):.3f}")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global BACKTEST
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true",
                    help="verify parser and stats on known output, run nothing")
    ap.add_argument("--show-config", action="store_true",
                    help="print the frozen config and its hash, then exit")
    ap.add_argument("--backtest", default=BACKTEST,
                    help="path to backtest.py (default: ./backtest.py)")
    args = ap.parse_args()

    if args.show_config:
        print(json.dumps({"hash": config_hash(),
                          "frozen": FROZEN,
                          "criteria": CRITERIA}, indent=2))
        return 0

    if args.selftest:
        selftest()
        return 0

    BACKTEST = args.backtest
    if not os.path.exists(BACKTEST):
        print(f"ERROR: {BACKTEST} not found. Run this from the repo root.")
        return 2

    first_run, prior_hash = check_lock()
    hash_changed = (prior_hash is not None and prior_hash != config_hash())

    cmd = build_cmd()
    print("Running:", " ".join(cmd))
    print()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print("backtest.py failed:")
        print(proc.stdout[-4000:])
        print(proc.stderr[-4000:])
        return proc.returncode

    out = proc.stdout
    with open("oos_raw_output.txt", "w") as f:
        f.write(out)

    per_ticker, agg = parse_output(out)
    if not per_ticker or "expectancy" not in agg:
        print("Could not parse backtest output. Raw output saved to "
              "oos_raw_output.txt — check the format and adjust the regexes.")
        return 3

    stats = pooled_stats(per_ticker, agg)
    ok, reasons = verdict(stats, agg)

    run_number = append_ledger({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config_hash": config_hash(),
        "hash_changed": hash_changed,
        "tickers": FROZEN["tickers"],
        "n_trades": stats["n"],
        "expectancy": round(stats["expectancy"], 4),
        "profit_factor": agg.get("profit_factor"),
        "t": round(stats["t"], 3),
        "p_value": round(stats["p_value"], 5),
        "ci": [round(stats["ci_low"], 4), round(stats["ci_high"], 4)],
        "passed": ok,
        "fail_reasons": reasons,
    })

    report(per_ticker, agg, stats, run_number, hash_changed)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
