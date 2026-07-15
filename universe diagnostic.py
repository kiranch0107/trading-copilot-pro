"""
Trading Copilot ELITE — Universe Diagnostic
===========================================
Answers ONE question: is the edge STRUCTURAL (a momentum strategy works on
trend-friendly names and fails on mean-reverting ones, consistently across
MANY tickers) — or did we just get lucky with META/NVDA/AAPL?

Method (designed to avoid selection bias)
-----------------------------------------
  • The universe is split into THREE buckets by an OBJECTIVE property fixed in
    advance, NOT by backtest outcome:
        MOMENTUM  — high-beta, narrative/growth large caps
        DEFENSIVE — mega-cap value / low-beta / staples
        INDEX_ETF — broad-market & sector ETFs (diversified, less trending)
  • Every ticker runs the EXACT same strategy with the SAME fixed parameters
    as the live app (imported from backtest.py). No per-bucket tuning.
  • We report the DISTRIBUTION within each bucket (how many names positive,
    median expectancy), not just the mean — 3 winners out of 7 is luck; 20 of
    28 is a structural pattern.
  • The regime filter is ON here, matching the live app, so this reflects what
    you'd actually trade.

Interpretation
--------------
  If MOMENTUM names are mostly positive AND defensives/indices mostly negative,
  across dozens of tickers, the edge is STRUCTURAL and the takeaway is
  "trade this strategy on trend-friendly names." If results are random across
  all three buckets, the earlier winners were luck and the strategy has no
  real edge.

Run
---
  pip install yfinance pandas ta tabulate numpy
  python universe_diagnostic.py
  python universe_diagnostic.py --years 5 --regime      # regime filter on
  python universe_diagnostic.py --quick                 # smaller universe, faster
"""
from __future__ import annotations

import argparse
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

# Reuse the SAME validated engine as backtest.py so the logic is identical.
import backtest as bt

try:
    from tabulate import tabulate
except ImportError:
    def tabulate(rows, headers, **kw):
        out = ["  ".join(str(h) for h in headers)]
        for r in rows:
            out.append("  ".join(str(c) for c in r))
        return "\n".join(out)


# ══════════════════════════════════════════════════════════════════════
# UNIVERSE — bucketed by an OBJECTIVE property, fixed in advance.
# These are well-known, liquid names/ETFs. The point is NOT that they're
# hand-picked winners — it's that they're sorted by a property (momentum vs
# defensive vs index) BEFORE seeing any result, so the per-bucket pattern
# is meaningful.
# ══════════════════════════════════════════════════════════════════════
UNIVERSE = {
    "MOMENTUM": [   # high-beta / growth / narrative-driven large caps
        "TSLA", "NVDA", "META", "AMD", "NFLX", "CRM", "AVGO", "SHOP",
        "PLTR", "COIN", "MSTR", "SMCI", "PANW", "SNOW", "UBER", "ABNB",
        "CRWD", "DDOG",
    ],
    "DEFENSIVE": [  # mega-cap value / low-beta / staples / healthcare
        "JNJ", "PG", "KO", "PEP", "WMT", "MRK", "PFE", "VZ", "T",
        "CVX", "XOM", "MCD", "COST", "HD", "UNH",
    ],
    "INDEX_ETF": [  # broad-market & sector ETFs — diversified, less trending
        "SPY", "QQQ", "DIA", "IWM", "XLF", "XLE", "XLK", "XLV", "XLP",
    ],
}


def bucket_stats(results: list[dict]) -> dict:
    """Distribution stats for one bucket's per-ticker results."""
    valid = [r for r in results if r["trades"] >= 20]   # ignore tiny samples
    if not valid:
        return {"n": 0}
    exps = np.array([r["avg_r"] for r in valid])
    pfs  = np.array([r["pf"] for r in valid if np.isfinite(r["pf"])])
    n_pos = int((exps > 0).sum())
    return {
        "n":          len(valid),
        "n_pos":      n_pos,
        "pct_pos":    n_pos / len(valid) * 100,
        "mean_exp":   float(exps.mean()),
        "median_exp": float(np.median(exps)),
        "mean_pf":    float(pfs.mean()) if len(pfs) else float("nan"),
        "best":       max(valid, key=lambda r: r["avg_r"]),
        "worst":      min(valid, key=lambda r: r["avg_r"]),
    }


def run(cfg: dict) -> None:
    print("=" * 80)
    print("UNIVERSE DIAGNOSTIC — is the momentum edge STRUCTURAL?")
    print("=" * 80)
    total = sum(len(v) for v in UNIVERSE.values())
    print(f"Universe   : {total} tickers in 3 buckets "
          f"(MOMENTUM {len(UNIVERSE['MOMENTUM'])}, "
          f"DEFENSIVE {len(UNIVERSE['DEFENSIVE'])}, "
          f"INDEX_ETF {len(UNIVERSE['INDEX_ETF'])})")
    print(f"History    : {cfg['years']} years daily   "
          f"Regime filter: {'ON' if cfg['use_regime'] else 'OFF'}   "
          f"Params: ADX≥{cfg['adx_min']} stop×{cfg['atr_stop_mult']} "
          f"tgt×{cfg['atr_tgt_mult']}")
    print("Buckets are fixed by TYPE before any result is seen — this is what")
    print("makes a per-bucket pattern meaningful rather than cherry-picking.")
    print("=" * 80)

    # Optional shared regime series from SPY
    regime_by_date = None
    if cfg["use_regime"]:
        spy_raw = bt.download("SPY", cfg["years"])
        if spy_raw is not None:
            reg = bt.build_regime_series(spy_raw)
            regime_by_date = dict(zip(spy_raw["Date"], reg))

    bucket_results: dict[str, list[dict]] = {}
    detail_rows = []

    for bucket, tickers in UNIVERSE.items():
        bucket_results[bucket] = []
        for tk in tickers:
            raw = bt.download(tk, cfg["years"])
            if raw is None:
                continue
            df = bt.compute(raw)
            if len(df) < bt.MIN_BARS_AFTER:
                continue
            tail = raw.tail(len(df)).reset_index(drop=True)
            for col in ["Open", "Date"]:
                if col in tail.columns:
                    df[col] = tail[col].values

            reg_series = None
            if regime_by_date is not None and "Date" in df.columns:
                reg_series = df["Date"].map(regime_by_date).fillna("Neutral").reset_index(drop=True)

            trades = bt.backtest_ticker(df, cfg, regime_series=reg_series)
            s = bt.stats(trades)
            if s["trades"] == 0:
                continue
            s["ticker"] = tk
            s["bucket"] = bucket
            bucket_results[bucket].append(s)
            detail_rows.append([
                bucket, tk, s["trades"], f"{s['win_rate']:.0f}%",
                f"{s['avg_r']:+.3f}", f"{s['pf']:.2f}",
            ])

    # ── Per-ticker detail ──
    print("\nPER-TICKER DETAIL")
    print(tabulate(detail_rows,
                   headers=["Bucket", "Ticker", "Trades", "Win%", "Avg R", "PF"],
                   tablefmt="simple"))

    # ── Bucket summary ──
    print("\n" + "=" * 80)
    print("BUCKET SUMMARY  (the actual diagnostic)")
    print("=" * 80)
    summary_rows = []
    stats_by_bucket = {}
    for bucket in UNIVERSE:
        bs = bucket_stats(bucket_results[bucket])
        stats_by_bucket[bucket] = bs
        if bs["n"] == 0:
            summary_rows.append([bucket, 0, "—", "—", "—", "—"])
            continue
        summary_rows.append([
            bucket, bs["n"],
            f"{bs['n_pos']}/{bs['n']} ({bs['pct_pos']:.0f}%)",
            f"{bs['mean_exp']:+.3f}",
            f"{bs['median_exp']:+.3f}",
            f"{bs['mean_pf']:.2f}",
        ])
    print(tabulate(summary_rows,
                   headers=["Bucket", "N", "Positive names", "Mean exp",
                            "Median exp", "Mean PF"],
                   tablefmt="simple"))

    # ── Verdict ──
    print("\n" + "=" * 80)
    print("VERDICT")
    print("=" * 80)
    mom = stats_by_bucket.get("MOMENTUM", {})
    dfn = stats_by_bucket.get("DEFENSIVE", {})
    idx = stats_by_bucket.get("INDEX_ETF", {})

    if not mom.get("n"):
        print("Insufficient data to judge. Try --years 5.")
        return

    mom_edge = mom["median_exp"]
    def_edge = dfn.get("median_exp", 0)
    idx_edge = idx.get("median_exp", 0)

    print(f"  MOMENTUM  median expectancy: {mom_edge:+.3f} R  "
          f"({mom.get('n_pos',0)}/{mom.get('n',0)} names positive)")
    print(f"  DEFENSIVE median expectancy: {def_edge:+.3f} R  "
          f"({dfn.get('n_pos',0)}/{dfn.get('n',0)} names positive)")
    print(f"  INDEX_ETF median expectancy: {idx_edge:+.3f} R  "
          f"({idx.get('n_pos',0)}/{idx.get('n',0)} names positive)")
    print()

    structural = (
        mom_edge > 0.02 and
        mom.get("pct_pos", 0) >= 60 and
        mom_edge > def_edge and
        mom_edge > idx_edge
    )
    if structural:
        print("  ✅ STRUCTURAL EDGE CONFIRMED.")
        print("     Momentum names are positive as a GROUP (not 3 lucky tickers),")
        print("     and clearly beat defensives and indices. The strategy has a")
        print("     real, explainable edge on trend-friendly names. The earlier")
        print("     META/NVDA/AAPL result was a symptom of this, not luck.")
        print("     ACTION: build the watchlist from high-momentum names; drop")
        print("     index ETFs and low-beta defensives — the strategy is not")
        print("     built for them and the data confirms it.")
    elif mom_edge > 0 and mom_edge > def_edge and mom_edge > idx_edge:
        print("  🟡 DIRECTIONAL BUT WEAK.")
        print("     Momentum names lean positive and beat the other buckets, but")
        print("     the margin is thin. The pattern is real but small — costs and")
        print("     slippage could eat it. Promising, not yet a green light.")
    else:
        print("  🔴 NO STRUCTURAL EDGE.")
        print("     Momentum names are NOT systematically better than defensives/")
        print("     indices. That means the earlier META/NVDA/AAPL wins were most")
        print("     likely LUCK, not a repeatable property. Do not scale this up.")

    print("\n  Caveats: 5y window covers one broad regime; a different decade")
    print("  could differ. Share-based test — options add theta/slippage on top.")
    print("  Survivorship: these are today's known names; some had big runs.")


def parse_args() -> dict:
    p = argparse.ArgumentParser(description="Universe structural-edge diagnostic")
    p.add_argument("--years", type=int, default=5)
    p.add_argument("--adx-min", type=float, default=bt.DEFAULTS["adx_min"])
    p.add_argument("--atr-stop", type=float, default=bt.DEFAULTS["atr_stop_mult"])
    p.add_argument("--atr-tgt", type=float, default=bt.DEFAULTS["atr_tgt_mult"])
    p.add_argument("--min-rr", type=float, default=bt.DEFAULTS["min_rr"])
    p.add_argument("--max-hold", type=int, default=bt.DEFAULTS["max_hold"])
    p.add_argument("--regime", action="store_true", default=True,
                   help="Apply SPY 200-SMA macro filter (matches live app; on by default)")
    p.add_argument("--no-regime", dest="regime", action="store_false")
    p.add_argument("--quick", action="store_true",
                   help="Use a smaller universe for a faster run")
    a = p.parse_args()

    if a.quick:
        global UNIVERSE
        UNIVERSE = {
            "MOMENTUM":  ["TSLA", "NVDA", "META", "AMD", "NFLX", "PLTR"],
            "DEFENSIVE": ["JNJ", "PG", "KO", "WMT", "MRK"],
            "INDEX_ETF": ["SPY", "QQQ", "DIA"],
        }

    return dict(
        tickers=[], years=a.years, adx_min=a.adx_min,
        atr_stop_mult=a.atr_stop, atr_tgt_mult=a.atr_tgt, min_rr=a.min_rr,
        volume_mult=bt.DEFAULTS["volume_mult"], max_hold=a.max_hold,
        slippage_bps=bt.DEFAULTS["slippage_bps"], commission=bt.DEFAULTS["commission"],
        use_regime=a.regime, cooldown_bars=bt.DEFAULTS["cooldown_bars"],
    )


if __name__ == "__main__":
    run(parse_args())
