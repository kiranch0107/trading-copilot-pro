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
     model ATM only (strike = round(the entry open)). The live selector does
     NOT target ATM: option_chain.get_option_data() ranks the strikes inside
     a +/-2 ATR window by liquidity alone, so the named strike can sit a few
     percent either side of the money (review 2026-11-03; the owner's call).
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
import risk_params             # the IV multiplier has ONE home (BACKLOG 25)

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
CALENDAR_DAYS = 365          # the option clock: dte and time value are calendar days
OPEN = "OPEN"                # censored at the sample's end, never a win or a loss


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
                          cfg: dict, target: float | None = None) -> dict | None:
    """
    Buy an ATM option at the next bar's open and walk it forward daily,
    applying the same exit rules the live monitor uses.

    Exit priority matches exit_monitor.py: STOP, TARGET, TIME, THESIS.

    THE D3 ARM (review 2026-11-03; owner: "go with D3"), OFF unless
    cfg["tgt_exit"]: the option is also sold when the UNDERLYING closes at
    or beyond the signal's own `target` (the level the alert names), at the
    close's mark like every other rule here; checked after STOP and before
    the premium TARGET. results/option_target_exit_preregistration.md fixes how it is
    read. Every committed run had it off and is unchanged.
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
    # KNOWN BEFORE THE ENTRY (external review 2026-10-19): closes through the
    # SIGNAL bar only. The entry fills on entry_i's open, so entry_i's close
    # is information the price did not have; it used to be in the window (one
    # bar of look-ahead; the review measured a 0.24-point change in mean
    # modelled premium return on its fixture). Committed option results
    # predate this and say so at their top.
    rv = realised_vol(df["Close"].iloc[:entry_i])
    if not np.isfinite(rv) or rv <= 0:
        return None
    iv = rv * cfg["iv_mult"]

    strike = round(spot0)                       # ATM
    dte0 = cfg["dte"]
    # THE CLOCK IS THE CALENDAR (external review 2026-10-20, group B;
    # 2026-10-28). `dte` is days to expiry, as on a real contract and as the
    # live monitor reads it: the expiry is entry_date + dte calendar days,
    # time to expiry on any session is the calendar days left over 365, and
    # the TIME exit fires at dte_exit calendar days. This used to count ROWS
    # and price with dte/252, so a "30-DTE" option carried 30 sessions of
    # time value (six weeks) and expired after 30 sessions. A frame with no
    # Date column (fixtures) counts rows as calendar days and says so.
    has_date = "Date" in df.columns
    if has_date:
        _d0 = pd.Timestamp(df["Date"].iloc[entry_i]).normalize()
        _expiry = _d0 + pd.Timedelta(days=dte0)
        _days_left = lambda j: int((_expiry - pd.Timestamp(df["Date"].iloc[j]).normalize()).days)  # noqa: E731
    else:
        _days_left = lambda j: dte0 - (j - entry_i)  # noqa: E731
    prem0 = bs_price(spot0, strike, dte0 / CALENDAR_DAYS, iv, right)
    if prem0 <= 0.05:                           # unpriceable / negligible
        return None

    # Entry cost: pay half the spread
    entry_prem = prem0 * (1 + cfg["spread_pct"] / 200)
    feats = entry_features(df, signal_i, rv=rv, prem0=prem0, spot0=spot0,
                           right=right)

    # THE ENTRY SESSION'S CLOSE IS EVALUATED (group B): the fill is at its
    # open, so its close is the first mark the position has. The walk used
    # to start one session later and could not see a stop hit on day one.
    for j in range(entry_i, n):
        held = j - entry_i
        dte_left = _days_left(j)
        if dte_left <= 0:
            break                                # expiry reached: settle below
        spot = float(df["Close"].iloc[j])
        t_left = dte_left / CALENDAR_DAYS
        raw = bs_price(spot, strike, t_left, iv, right)
        # Exit fill: give up half the spread
        mid = raw * (1 - cfg["spread_pct"] / 200)
        pnl_pct = (mid - entry_prem) / entry_prem * 100

        # 1. STOP
        if cfg["sl"] and pnl_pct <= -abs(cfg["sl"]):
            return _result("STOP", entry_prem, mid, held, dte_left, pnl_pct,
                           spot0=spot0, spot_exit=spot, strike=strike, iv=iv,
                           right=right, dte0=dte0, entry_date=entry_date, features=feats)
        # 1b. THE UNDERLYING'S TARGET (the D3 arm; off by default). MARKED ON
        # THE CLOSE, like every other rule here: the session's close at or
        # beyond the target sells at the close's mark. An intraday touch fill
        # was tried first and is NOT used: it priced arm B at the touch while
        # arm A's +200 % is marked on the close, and on driftless random walks
        # that asymmetry alone put B about 3.5 points ahead (the pre-registration
        # records it). The engine has no intraday option price for either arm.
        if cfg.get("tgt_exit") and target is not None:
            if (spot >= target) if right == "CALL" else (spot <= target):
                return _result("TARGET_U", entry_prem, mid, held, dte_left, pnl_pct,
                               spot0=spot0, spot_exit=spot, strike=strike, iv=iv,
                               right=right, dte0=dte0, entry_date=entry_date, features=feats)
        # 2. TARGET
        if cfg["tp"] and pnl_pct >= abs(cfg["tp"]):
            return _result("TARGET", entry_prem, mid, held, dte_left, pnl_pct,
                           spot0=spot0, spot_exit=spot, strike=strike, iv=iv,
                           right=right, dte0=dte0, entry_date=entry_date, features=feats)
        # 3. TIME
        if cfg["dte_exit"] and dte_left <= cfg["dte_exit"]:
            return _result("TIME", entry_prem, mid, held, dte_left, pnl_pct,
                           spot0=spot0, spot_exit=spot, strike=strike, iv=iv,
                           right=right, dte0=dte0, entry_date=entry_date, features=feats)
        # 4. THESIS — underlying closed the wrong side of EMA20
        if cfg["use_thesis"] and "EMA20" in df.columns:
            ema20 = float(df["EMA20"].iloc[j])
            broke = (spot < ema20) if right == "CALL" else (spot > ema20)
            if broke:
                return _result("THESIS", entry_prem, mid, held, dte_left, pnl_pct,
                               spot0=spot0, spot_exit=spot, strike=strike, iv=iv,
                               right=right, dte0=dte0, entry_date=entry_date, features=feats)

    # EXPIRY, or the END OF THE SAMPLE before it (group B): a trade whose
    # expiry lies past the last bar is OPEN -- censored at its last mark, not
    # settled at intrinsic as if it had expired. summarise() keeps OPEN
    # trades out of the win/loss statistics and counts them.
    j = n - 1
    if _days_left(j) > 0:
        spot = float(df["Close"].iloc[j])
        raw = bs_price(spot, strike, _days_left(j) / CALENDAR_DAYS, iv, right)
        mid = raw * (1 - cfg["spread_pct"] / 200)
        pnl_pct = (mid - entry_prem) / entry_prem * 100
        return _result("OPEN", entry_prem, mid, j - entry_i, _days_left(j), pnl_pct,
                       spot0=spot0, spot_exit=spot, strike=strike, iv=iv,
                       right=right, dte0=dte0, entry_date=entry_date, features=feats)
    # the first session on or after the expiry date settles at intrinsic
    j = next(k for k in range(entry_i, n) if _days_left(k) <= 0)
    spot = float(df["Close"].iloc[j])
    mid = max(0.0, (spot - strike) if right == "CALL" else (strike - spot))
    pnl_pct = (mid - entry_prem) / entry_prem * 100
    return _result("EXPIRY", entry_prem, mid, j - entry_i, 0, pnl_pct,
                   spot0=spot0, spot_exit=spot, strike=strike, iv=iv,
                   right=right, dte0=dte0, entry_date=entry_date, features=feats)


def entry_features(df: "pd.DataFrame", signal_i: int, *, rv: float,
                   prem0: float, spot0: float, right: str) -> dict:
    """
    Everything observable AT THE SIGNAL BAR, for after-the-fact analysis.

    Read at `signal_i`, never at `entry_i` or later: the entry fills on the NEXT
    bar's open, so anything read past signal_i is information the decision could
    not have had. That is the single lookahead chokepoint for this record, kept
    in one function so it can be checked in one place.

    Consumed by feature_sweep.py. Values are NaN when a column is missing rather
    than defaulted, because a default is indistinguishable from a measurement.
    """
    def at(col):
        # A MISSING COLUMN is a missing measurement -> NaN, and the consumer
        # drops the trade. An OUT-OF-RANGE signal_i is a bug and must raise.
        #
        # The first version wrapped this in `except Exception: return nan`,
        # which turned an IndexError into a silent NaN — bt.compute() drops the
        # indicator warmup and returns a SHORTER frame than it was handed, so a
        # stale index would have quietly emptied every bucket instead of failing.
        if col not in df.columns:
            return float("nan")
        return float(df[col].iloc[signal_i])

    close = at("Close")
    atr, ema20, ema50 = at("ATR"), at("EMA20"), at("EMA50")
    vol, vavg = at("Volume"), at("VOL_AVG20")
    macd, sig = at("MACD"), at("Signal")
    return {
        "side": 1.0 if right == "CALL" else 0.0,
        "adx": at("ADX"),
        "rsi": at("RSI"),
        "atr_pct": (atr / close * 100.0) if close else float("nan"),
        "rvol": (vol / vavg) if vavg else float("nan"),
        "ema20_dist": ((close - ema20) / atr) if atr else float("nan"),
        "ema_spread": ((ema20 - ema50) / close * 100.0) if close else float("nan"),
        "macd_hist": ((macd - sig) / close * 100.0) if close else float("nan"),
        "rv": rv * 100.0,
        "prem_pct": (prem0 / spot0 * 100.0) if spot0 else float("nan"),
    }


def _result(reason, entry_prem, exit_prem, held, dte_left, pnl_pct,
            *, spot0=None, spot_exit=None, strike=None, iv=None,
            right=None, dte0=None, entry_date=None, features=None) -> dict:
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
            "entry_date": entry_date,
            # Entry-time observables, read at the SIGNAL bar. feature_sweep.py
            # consumes these; empty rather than None so a consumer that forgets
            # to check does not crash on a missing key mid-loop.
            "features": features or {}}


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


def run_ticker_pairs(tk: str, cfg_a: dict, cfg_b: dict, sig_cfg: dict,
                     coverage: dict | None = None, hq_only: bool = True) -> list[tuple]:
    """
    THE D3 COMPARISON: both arms on IDENTICAL entries, as (a, b) pairs.
    Entries are the alerts the live system could send: longs only
    (risk_params.LONGS_ONLY), R:R at or above the tier's hq_min_rr when
    `hq_only`, and not gapped at the fill (the next open beyond the stop or
    the target: the live fill gate, J1, withholds those). A pair is kept
    only when both arms price it. The signal's own target reaches arm B.
    """
    params = bt.build_signal_params(sig_cfg)
    raw = bt.download(tk, cfg_a["years"])
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
    return pairs_on_frame(df, tk, cfg_a, cfg_b, params, sig_cfg["cooldown_bars"], hq_only)


def pairs_on_frame(df: "pd.DataFrame", tk: str, cfg_a: dict, cfg_b: dict, params,
                   cooldown: int, hq_only: bool = True) -> list[tuple]:
    """The pair loop on a computed frame (split out so it runs offline)."""
    out, i, n = [], 0, len(df)
    while i < n - 1:
        sig = bt.evaluate_signal(df, i, params)
        if sig and (not risk_params.LONGS_ONLY or sig["trend"] == "Bullish") \
                and (not hq_only or sc_gate_rr(sig, params)):
            o = float(df["Open"].iloc[i + 1])
            long = sig["trend"] == "Bullish"
            gapped = ((o <= sig["stop"] or o >= sig["target"]) if long
                      else (o >= sig["stop"] or o <= sig["target"]))
            if not gapped:
                a = simulate_option_trade(df, i, sig["trend"], cfg_a, target=sig["target"])
                b = simulate_option_trade(df, i, sig["trend"], cfg_b, target=sig["target"])
                if a and b:
                    for x in (a, b):
                        x["ticker"], x["trend"] = tk, sig["trend"]
                    out.append((a, b))
                    i += cooldown + 1
                    continue
        i += 1
    return out


def sc_gate_rr(sig: dict, params) -> bool:
    """The alert tier's R:R leg on the signal's unrounded R:R."""
    import signal_core as _sc
    return _sc.gate_rr(sig.get("rr_raw", sig.get("rr") or 0), params.hq_min_rr)


def paired_report(pairs: list[tuple]) -> dict:
    """B minus A per entry, % of premium; OPEN trades (censored) are left out."""
    d = [b["pnl_pct"] - a["pnl_pct"] for a, b in pairs
         if a["reason"] != "OPEN" and b["reason"] != "OPEN"]
    n = len(d)
    if n < 2:
        return {"n": n}
    m = float(np.mean(d)); s = float(np.std(d, ddof=1)); se = s / math.sqrt(n)
    ra = [a["pnl_pct"] for a, b in pairs if a["reason"] != "OPEN" and b["reason"] != "OPEN"]
    rb = [b["pnl_pct"] for a, b in pairs if a["reason"] != "OPEN" and b["reason"] != "OPEN"]
    reasons_b: dict = {}
    for _a, b in pairs:
        reasons_b[b["reason"]] = reasons_b.get(b["reason"], 0) + 1
    return {"n": n, "mean_a": float(np.mean(ra)), "mean_b": float(np.mean(rb)),
            "diff": m, "sd": s, "se": se, "lo": m - 1.96 * se, "hi": m + 1.96 * se,
            "changed": sum(1 for x in d if abs(x) > 1e-9), "reasons_b": reasons_b}


def d3_power(n_frames: int = 200, n_trend_frames: int = 60) -> int:
    """
    BACKLOG 13 step 2 for D3, through THIS engine: synthetic daily paths
    (lognormal, sd 1.7 % a day) go through backtest.compute, the live
    signal and both arms. Two worlds: no persistence (a random walk, where
    an option marked on the close is close to a fair game and the arms
    should tie) and persistent 60-session trends (where selling at the
    target gives up the run). Prints the paired difference's mean and sd at
    IV 1.00 and 1.15 and the smallest effect each n could detect. Seeded;
    results/option_target_exit_preregistration.md quotes its output.
    """
    params = bt.build_signal_params(bt.live_cfg())
    cool = bt.live_cfg()["cooldown_bars"]

    def _frame(rng, n, sig, trend):
        tr = np.repeat(rng.normal(0, trend, n // 60 + 1), 60)[:n]
        c = 100 * np.exp(np.cumsum(rng.normal(0, sig, n) + tr))
        o = c * np.exp(rng.normal(0, sig * 0.3, n))
        h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, sig * 0.5, n)))
        lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, sig * 0.5, n)))
        return pd.DataFrame({"Date": pd.bdate_range("2015-01-02", periods=n), "Open": o, "High": h,
                             "Low": lo, "Close": c, "Volume": rng.lognormal(14, 0.3, n)})
    for label, trend, frames in (("no persistence", 0.0, n_frames), ("60-session trends", 0.004, n_trend_frames)):
        for ivm in (1.00, 1.15):
            base = dict(years=5, dte=30, tp=200, sl=50, dte_exit=7, use_thesis=True, iv_mult=ivm, spread_pct=5.0)
            rng = np.random.default_rng(11)
            pairs = []
            for k in range(frames):
                df = bt.compute(_frame(rng, 1300, 0.017, trend))
                pairs += pairs_on_frame(df, f"S{k}", dict(base), dict(base, tgt_exit=True), params, cool)
            r = paired_report(pairs)
            mde = {n: 2.8 * r["sd"] / math.sqrt(n) for n in (150, 300, 600)}
            print(f"{label:18s} iv x{ivm:.2f}: pairs {r['n']:5d} | A {r['mean_a']:+6.2f}% B {r['mean_b']:+6.2f}% | "
                  f"B-A {r['diff']:+6.2f}% sd {r['sd']:5.1f} | MDE n150 {mde[150]:.1f} n300 {mde[300]:.1f} "
                  f"n600 {mde[600]:.1f}")
    return 0


def summarise(trades: list[dict]) -> dict:
    # OPEN trades (expiry past the sample's end) are counted, never scored.
    n_open = sum(1 for t in trades if t.get("reason") == OPEN)
    trades = [t for t in trades if t.get("reason") != OPEN]
    if not trades:
        return {"n": 0, "open": n_open}
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
        "open": n_open,
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
    # THE BARS THIS RUN READ, as one line (BACKLOG 36 group B, 2026-10-28). The
    # share backtest prints it from the same cache log; this engine did not, so
    # run 1 (2026-10-08, 393 trades) and run 2 (2026-10-28, 398 trades) of the
    # IV sweep cannot say whether they read the same bars. From now on they can.
    _bc = getattr(bt, "bar_cache", None)
    _log = getattr(bt, "_CACHE_LOG", None)
    if _bc is not None and _log:
        _seen, _metas, _stat = set(), [], []
        for _m, _st in _log:                      # one entry per series, first read
            _k = (_m or {}).get("key") or (_m or {}).get("ticker")
            if _k in _seen:
                continue
            _seen.add(_k); _metas.append(_m); _stat.append(_st)
        print(f"  {_bc.summarise(_metas, _stat)}")
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

    # NO ENTRY LOOK-AHEAD (external review 2026-10-19): the entry premium is
    # priced from closes through the signal bar. Changing the entry bar's
    # close, or any later close, must not change it.
    rng = np.random.default_rng(7)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, 80)))
    frame = pd.DataFrame({"Date": pd.bdate_range("2026-01-02", periods=80).astype(str),
                          "Open": closes, "High": closes * 1.01, "Low": closes * 0.99,
                          "Close": closes})
    cfg = {"iv_mult": 1.15, "dte": 30, "spread_pct": 8.0, "sl": 50, "tp": 100,
           "dte_exit": 7, "use_thesis": False}
    base = simulate_option_trade(frame, 40, "Bullish", cfg)
    assert base is not None
    moved = frame.copy()
    moved.loc[41:, "Close"] = moved.loc[41:, "Close"] * 1.30     # the entry bar's close and after
    alt = simulate_option_trade(moved, 40, "Bullish", cfg)
    assert alt is not None and abs(alt["entry_prem"] - base["entry_prem"]) < 1e-12, (
        base["entry_prem"], alt["entry_prem"], "the entry premium read the entry bar's close")
    before = frame.copy()
    before.loc[35, "Close"] = before.loc[35, "Close"] * 1.05       # one close inside the window
    assert abs(simulate_option_trade(before, 40, "Bullish", cfg)["entry_prem"] - base["entry_prem"]) > 1e-9, \
        "closes up to the signal bar DO price the entry (or the test is vacuous)"
    print("no look-ahead     : entry premium ignores the entry bar's close and after")

    # ── THE CLOCK IS THE CALENDAR (group B, 2026-10-28) ──
    _hold = dict(cfg, sl=10**9, tp=10**9, dte_exit=0)        # nothing exits but the calendar
    _e = simulate_option_trade(frame, 40, "Bullish", _hold)
    _d0 = pd.Timestamp(frame["Date"].iloc[41])
    _exp = _d0 + pd.Timedelta(days=30)
    _first_on_or_after = next(k for k in range(41, 80) if pd.Timestamp(frame["Date"].iloc[k]) >= _exp)
    assert _e["reason"] == "EXPIRY" and _e["held"] == _first_on_or_after - 41, (_e["reason"], _e["held"])
    assert 20 <= _e["held"] <= 23, (_e["held"], "30 calendar days is ~21 sessions, never 30 sessions")
    _spot0 = float(frame["Open"].iloc[41])
    _rv = realised_vol(frame["Close"].iloc[:41])
    _iv = _rv * cfg["iv_mult"]
    _want = bs_price(_spot0, round(_spot0), 30 / CALENDAR_DAYS, _iv, "CALL") * (1 + cfg["spread_pct"] / 200)
    assert abs(_e["entry_prem"] - _want) < 1e-9, (_e["entry_prem"], _want, "priced with dte/365, not dte/252")
    # the TIME exit counts calendar days left, so dte_exit=7 fires ~16 sessions in
    _t = simulate_option_trade(frame, 40, "Bullish", dict(cfg, sl=10**9, tp=10**9, dte_exit=7))
    assert _t["reason"] == "TIME" and _t["dte_left"] <= 7 and 14 <= _t["held"] <= 18, (_t["reason"], _t["held"], _t["dte_left"])
    # the entry session's close is the first mark: a crash on that close is a STOP held 0
    _crash = frame.copy()
    _crash.loc[41, "Close"] = float(_crash.loc[41, "Close"]) * 0.60
    _c = simulate_option_trade(_crash, 40, "Bullish", cfg)
    assert _c["reason"] == "STOP" and _c["held"] == 0, (_c["reason"], _c["held"])
    # a sample that ends before expiry leaves the trade OPEN, never "expired"
    _o = simulate_option_trade(frame, 70, "Bullish", _hold)
    assert _o["reason"] == OPEN and _o["held"] == 79 - 71 and _o["dte_left"] > 0, (_o["reason"], _o["held"], _o["dte_left"])
    _sm = summarise([_e, _o])
    assert _sm["n"] == 1 and _sm["open"] == 1 and OPEN not in _sm["reasons"], _sm
    assert summarise([_o]) == {"n": 0, "open": 1}
    # without a Date column, rows count as calendar days and the walk still starts at the entry session
    _nodate = frame.drop(columns=["Date"])
    _nd = simulate_option_trade(_nodate, 40, "Bullish", _hold)
    assert _nd["reason"] == "EXPIRY" and _nd["held"] == 30, (_nd["reason"], _nd["held"])
    print("calendar clock    : 30 DTE expires in ~21 sessions priced at 30/365; entry close marked; sample end -> OPEN")
    # ── the run says what bars it read: one fingerprint line, one entry per series ──
    import contextlib as _ctx, io as _io
    _saved_log = list(getattr(bt, "_CACHE_LOG", []))
    try:
        _meta = lambda tk: {"key": f"{tk}_1d_5y", "ticker": tk, "interval": "1d", "years": 5,
                            "variant": "raw", "hash": "ab" * 16, "fetched_at": "2026-10-01T00:00:00Z"}
        bt._CACHE_LOG[:] = [(_meta("AAA"), "hit"), (_meta("AAA"), "hit"), (_meta("BBB"), "stored")]
        _buf = _io.StringIO()
        with _ctx.redirect_stdout(_buf):
            _report_coverage({"ok": ["AAA", "BBB"], "no_data": [], "too_short": []}, ["AAA", "BBB"])
        _out = _buf.getvalue()
        assert "data: v2-" in _out and "2 series, 1 cached / 1 fetched" in _out, (
            _out, "every option run must print the bar-cache fingerprint, one entry per series")
    finally:
        bt._CACHE_LOG[:] = _saved_log
    print("fingerprint       : printed once per run, deduped to one entry per series")

    # ── D3 (owner 2026-11-03): THE UNDERLYING-TARGET ARM ──
    # Off by default: every call above ran without it. On, a close at or above
    # the signal's target sells at that close's mark (TARGET_U); an intraday
    # touch that closes below does not; STOP still comes first.
    _cfg3 = dict(cfg, sl=50, tp=10**9, dte_exit=0)
    _up = frame.copy()
    _up.loc[45, "Close"] = float(_up.loc[41, "Open"]) * 1.06
    _tg = float(_up.loc[41, "Open"]) * 1.05
    _off = simulate_option_trade(_up, 40, "Bullish", _cfg3, target=_tg)
    _on = simulate_option_trade(_up, 40, "Bullish", dict(_cfg3, tgt_exit=True), target=_tg)
    assert _off["reason"] != "TARGET_U" and _on["reason"] == "TARGET_U" and _on["held"] == 4 \
        and abs(_on["spot_exit"] - float(_up.loc[45, "Close"])) < 1e-9, (_off["reason"], _on)
    _touch = frame.copy()
    _touch.loc[45, "High"] = float(_touch.loc[41, "Open"]) * 1.10       # touched, closed below
    _t3 = simulate_option_trade(_touch, 40, "Bullish", dict(_cfg3, tgt_exit=True), target=_tg)
    assert _t3["reason"] != "TARGET_U" or _t3["held"] != 4, ("an intraday touch is not a close", _t3)
    assert simulate_option_trade(_up, 40, "Bullish", dict(_cfg3, tgt_exit=True))["reason"] != "TARGET_U", \
        "no target passed: the arm cannot fire"
    # the paired report reads B - A per entry and leaves OPEN trades out
    _pa = {"pnl_pct": -50.0, "reason": "STOP"}; _pb = {"pnl_pct": 20.0, "reason": "TARGET_U"}
    _rp3 = paired_report([(_pa, _pb), (dict(_pa, pnl_pct=10.0), dict(_pb, pnl_pct=10.0)),
                          (dict(_pa, reason=OPEN), _pb)])
    assert _rp3["n"] == 2 and abs(_rp3["diff"] - 35.0) < 1e-9 and _rp3["changed"] == 1, _rp3
    # the pair runner keeps only what the live system could alert: longs, R:R at or
    # over the tier's minimum, a next open inside the levels
    _real_es = bt.evaluate_signal
    _o42 = float(frame["Open"].iloc[42])
    _canned = {41: {"trend": "Bearish", "stop": 1e9, "target": 1.0, "rr_raw": 2.0},
               43: {"trend": "Bullish", "stop": 1.0, "target": 1e9, "rr_raw": 0.8},
               47: {"trend": "Bullish", "stop": 1.0, "target": float(frame["Open"].iloc[48]) * 0.99, "rr_raw": 2.0},
               50: {"trend": "Bullish", "stop": 1.0, "target": 1e9, "rr_raw": 1.5}}
    try:
        bt.evaluate_signal = lambda df, i, params: _canned.get(i)
        _prs = pairs_on_frame(frame, "T", dict(_cfg3), dict(_cfg3, tgt_exit=True),
                              bt.build_signal_params(bt.live_cfg()), 0)
    finally:
        bt.evaluate_signal = _real_es
    assert len(_prs) == 1 and _prs[0][0]["entry_date"] == str(frame["Date"].iloc[51]), \
        ([p[0]["entry_date"] for p in _prs], "short, R:R 0.8 and a gapped fill are not alerts")
    del _o42
    print("D3 arm            : off by default; a close at the signal's target sells at its mark; "
          "an intraday touch does not; pairs read B - A")

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
    ap.add_argument("--iv-mult", type=float, default=risk_params.OPT_IV_MULT,
                    help="IV as a multiple of realised vol (risk premium). "
                         "Default is risk_params.OPT_IV_MULT, the one home for "
                         "the assumption; every result prints the value used.")
    ap.add_argument("--spread-pct", type=float, default=5.0,
                    help="Round-trip bid-ask cost, %% of premium")
    ap.add_argument("--selftest", action="store_true",
                    help="offline checks for the coverage guard")
    ap.add_argument("--config", choices=("research", "live"), default="research",
                    help="entries from bt.DEFAULTS (the record's) or bt.live_cfg() (the scanner's; "
                         "BACKLOG 23: every NEW run uses live)")
    ap.add_argument("--target-exit-ab", action="store_true",
                    help="D3: the structure (A) against the structure plus 'sell when the underlying "
                         "reaches the signal's target' (B), on identical live alert entries; see "
                         "results/option_target_exit_preregistration.md")
    ap.add_argument("--d3-power", action="store_true",
                    help="D3's power check: the paired difference's sd on synthetic paths through "
                         "this engine (no market data; a few minutes; not run in CI)")
    ap.add_argument("--sweep", action="store_true",
                    help="Compare take-profit levels instead of one config")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if a.d3_power:
        return d3_power()
    if hasattr(bt, "_CACHE_LOG"):
        bt._CACHE_LOG.clear()                     # this run's reads only
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    if a.config == "live":
        sig_cfg = bt.live_cfg(tickers=tickers, years=a.years)
    else:
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
    print(f"Config    : {bt.describe_config(sig_cfg)}")
    print("=" * 78)

    if a.target_exit_ab:
        # D3: A is the measured structure as passed (--tp 200 --sl 50 --dte-exit 7,
        # thesis on); B adds the underlying-target exit. Same entries.
        cfg_a = dict(base)
        cfg_b = dict(base, tgt_exit=True)
        coverage = {"ok": [], "no_data": [], "too_short": []}
        pairs = []
        for tk in tickers:
            pairs += run_ticker_pairs(tk, cfg_a, cfg_b, sig_cfg, coverage=coverage)
        r = paired_report(pairs)
        print(f"\nD3 PAIRED: A = TP+{a.tp:g}/SL-{a.sl:g}/out at {a.dte_exit} DTE/thesis "
              f"{'off' if a.no_thesis else 'on'};  B = A + sell at the underlying's target")
        if r["n"] < 2:
            print(f"  pairs        : {r['n']} — nothing to read")
            return _report_coverage(coverage, tickers)
        print(f"  pairs        : {r['n']} (settled in both arms; {r['changed']} differ)")
        print(f"  mean A       : {r['mean_a']:+.2f}% of premium")
        print(f"  mean B       : {r['mean_b']:+.2f}% of premium")
        print(f"  B - A        : {r['diff']:+.2f}%  sd {r['sd']:.2f}  SE {r['se']:.2f}  "
              f"95% CI [{r['lo']:+.2f}, {r['hi']:+.2f}]")
        print(f"  B exits      : {r['reasons_b']}")
        print(f"  IV multiplier: {a.iv_mult}  (read with the other multiplier; see the pre-registration)")
        return _report_coverage(coverage, tickers)

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
    if s.get("open"):
        print(f"  Left OPEN    : {s['open']} (expiry past the sample's end; not scored)")

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
