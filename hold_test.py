#!/usr/bin/env python3
"""
hold_test.py — does the signal make the price HOLD over an option's life?
==========================================================================

Pre-registered in results/hold_spread_preregistration.md (owner 2026-11-04:
"write the pre-registration for the 'price holds over 21–45 days' test and
the signal-timed put-spread arm"). This module holds the RULES that
document fixes, as functions, and the seeded power check it quotes. It
records nothing and grades nothing yet: the recorder is built only after
the pre-registration is committed and the owner says go.

The two questions, on the forward record's pool episodes and their control
rows (amendment 2026-10-28 A):

  H-hold    Is the close HOLD_SESSIONS sessions after the fill at or above
            fill x exp(-HOLD_Z x sigma x sqrt(HOLD_SESSIONS)) more often
            after a signal than after its control?
  H-spread  Does a bull put spread with its short strike at that level and
            its long strike at LONG_Z, model-priced the same way for both,
            return more on its risk after a signal than after its control?

    python hold_test.py --power       # the power check the document quotes (seeded, offline)
    python hold_test.py --selftest    # offline
"""
from __future__ import annotations

import argparse
import math
import sys

import numpy as np

import risk_params as rp

# ── the rules, fixed by results/hold_spread_preregistration.md ──
HOLD_SESSIONS = 21                 # primary horizon: the fill session is session 1
SECONDARY_SESSIONS = (15, 31)      # ~21 and ~45 calendar days: reported, never decided on
HOLD_Z = 1.0                       # the level, in sigma x sqrt(horizon) below the fill
LONG_Z = 2.0                       # the long put's strike, the same way
VOL_LOOKBACK = 20                  # sessions of log returns ending at the entry bar
TRADING_DAYS = 252
CALENDAR_PER_SESSION = 365.0 / TRADING_DAYS
COST_FRAC_SINGLE = 1.0             # of rp.MAX_OPTION_SPREAD_PCT: single names quote wider than SPY
GATE_PAIRS = 349                   # the forward record's gate, in settled pool pairs
EPISODES_PER_SESSION = 3.7         # the pool's arrival rate (forward_record_preregistration.md)


def realized_vol(closes) -> float:
    """Daily sigma of the last VOL_LOOKBACK log returns of `closes` (ddof 1).
    NaN when there are not enough closes."""
    c = np.asarray(closes, dtype=float)
    if len(c) < VOL_LOOKBACK + 1 or not np.all(np.isfinite(c[-(VOL_LOOKBACK + 1):])) \
            or np.any(c[-(VOL_LOOKBACK + 1):] <= 0):
        return float("nan")
    r = np.diff(np.log(c[-(VOL_LOOKBACK + 1):]))
    return float(np.std(r, ddof=1))


def hold_level(fill: float, sigma: float, z: float = HOLD_Z, n: int = HOLD_SESSIONS) -> float:
    """The price the close must hold at: fill x exp(-z x sigma x sqrt(n))."""
    return float(fill) * math.exp(-z * float(sigma) * math.sqrt(n))


def margin(fill: float, close_n: float, sigma: float, n: int = HOLD_SESSIONS) -> float:
    """Where the horizon's close lands, in sigma x sqrt(n) from the fill:
    log(close_n / fill) / (sigma x sqrt(n)). HELD is margin >= -HOLD_Z; the
    margin keeps what the yes/no throws away, so it is the primary metric."""
    return math.log(float(close_n) / float(fill)) / (float(sigma) * math.sqrt(n))


def hold_equivalent(delta_margin: float, z: float = HOLD_Z) -> float:
    """The change in P(held) a shift of the margin's mean by delta buys, for a
    normal margin of unit sd: Phi(z + delta) - Phi(z)."""
    phi = lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))   # noqa: E731
    return phi(z + delta_margin) - phi(z)


def held(close_n: float, level: float) -> int:
    """1 when the horizon's close is at or above the level."""
    return int(float(close_n) >= float(level))


def spread_model(fill: float, sigma: float, n: int = HOLD_SESSIONS) -> dict:
    """
    The bull put spread both arms are priced with: short put at the hold
    level, long put at LONG_Z, Black-Scholes with the IV at the realized
    vol x rp.OPT_IV_MULT, spread_backtest's skew and rate, strikes unrounded.
    Entry cost: COST_FRAC_SINGLE of the 8 % ceiling, crossed at CROSS_FRAC of
    each leg's price; nothing at expiry (settled at intrinsic).
    """
    import spread_backtest as sb
    import spread_log as sl
    k1 = hold_level(fill, sigma, HOLD_Z, n)
    k2 = hold_level(fill, sigma, LONG_Z, n)
    t = n * CALENDAR_PER_SESSION / 365.0
    base_vol = float(sigma) * math.sqrt(TRADING_DAYS) * rp.OPT_IV_MULT
    p1 = sb._leg(fill, k1, t, base_vol, "PUT", sb.REALISTIC_SKEW)
    p2 = sb._leg(fill, k2, t, base_vol, "PUT", sb.REALISTIC_SKEW)
    credit = p1 - p2
    cost = COST_FRAC_SINGLE * rp.MAX_OPTION_SPREAD_PCT / 100.0 * sl.CROSS_FRAC * (p1 + p2)
    return {"legs": [("PUT", k1, -1), ("PUT", k2, +1)], "credit": credit, "cost": cost,
            "width": k1 - k2, "max_loss": (k1 - k2) - credit}


def spread_return(model: dict, close_n: float) -> float:
    """Return on the risk taken: (credit - cost - owed at expiry) / max loss."""
    import spread_log as sl
    owed = sl.settle(model["legs"], float(close_n))
    return (model["credit"] - model["cost"] - owed) / model["max_loss"]


# ── the power check (BACKLOG 13: computed BEFORE the build) ──
def _panel(rng, n_tickers: int, n_sessions: int, mkt_vol: float, idio_vol: float,
           drift: float = 0.0003):
    """Synthetic daily closes and opens: a market factor with persistent
    high-vol spells (stress days hit every name at once, which is what
    makes holds fail together) plus idiosyncratic noise."""
    high = np.zeros(n_sessions, dtype=bool)
    s = False
    for t in range(n_sessions):
        s = (rng.random() < 0.03) if not s else (rng.random() > 0.15)
        high[t] = s
    mult = np.where(high, 2.5, 1.0)
    m = rng.normal(0.0, mkt_vol, n_sessions) * mult
    e = rng.normal(0.0, idio_vol, (n_sessions, n_tickers)) * np.where(high, 1.5, 1.0)[:, None]
    r = drift + m[:, None] + e
    close = 100.0 * np.exp(np.cumsum(r, axis=0))
    prev = np.vstack([np.full((1, n_tickers), 100.0), close[:-1]])
    opn = prev * np.exp(0.35 * r)
    return close, opn


def _cr1_se(d: np.ndarray, groups: np.ndarray) -> tuple[float, float]:
    """(independent SE, CR1 SE clustered by group, never narrower)."""
    n = len(d)
    e = d - d.mean()
    se_ind = float(np.std(d, ddof=1) / math.sqrt(n))
    sums: dict = {}
    for g, v in zip(groups, e):
        sums[g] = sums.get(g, 0.0) + float(v)
    G = len(sums)
    var = (G / (G - 1)) * sum(v * v for v in sums.values()) / (n * n)
    return se_ind, max(se_ind, math.sqrt(var))


def power(seed: int = 7, n_tickers: int = 80, n_sessions: int = 1500,
          settings=(("market share low ", 0.006, 0.0160),
                    ("market share mid ", 0.010, 0.0140),
                    ("market share high", 0.013, 0.0110))) -> list[dict]:
    """
    Under the NULL (signal bars drawn at random, independent of the future),
    simulate the pool's arrival (EPISODES_PER_SESSION a session) and each
    episode's control (1-20 sessions earlier, same ticker), grade both, and
    return per setting: hold rates, the paired difference's sd, its design
    effect when clustered by entry week, and the MDE at GATE_PAIRS, 700 and
    1,000 pairs, for H-hold and H-spread. 2.8 = z(0.975) + z(0.80).
    """
    out = []
    for label, mv, iv in settings:
        rng = np.random.default_rng(seed)
        close, opn = _panel(rng, n_tickers, n_sessions, mv, iv)
        n = HOLD_SESSIONS
        dh, ds, dm, wk, hs, hc = [], [], [], [], [], []
        for t in range(VOL_LOOKBACK + 25, n_sessions - n - 2):
            k = rng.poisson(EPISODES_PER_SESSION)
            for j in rng.choice(n_tickers, size=min(k, n_tickers), replace=False):
                c = t - int(rng.integers(1, 21))
                vals = []
                for b in (t, c):
                    fill = float(opn[b + 1, j])
                    sig = realized_vol(close[b - VOL_LOOKBACK:b + 1, j])
                    lvl = hold_level(fill, sig)
                    cn = float(close[b + n, j])
                    mdl = spread_model(fill, sig)
                    vals.append((held(cn, lvl), spread_return(mdl, cn), margin(fill, cn, sig)))
                (h1, s1, m1), (h0, s0, m0) = vals
                dh.append(h1 - h0); ds.append(s1 - s0); dm.append(m1 - m0); wk.append((t + 1) // 5)
                hs.append(h1); hc.append(h0)
        dh, ds, dm, wk = np.array(dh, float), np.array(ds, float), np.array(dm, float), np.array(wk)
        row = {"label": label, "pairs": len(dh), "hold_sig": float(np.mean(hs)),
               "hold_ctl": float(np.mean(hc))}
        for key, d in (("margin", dm), ("hold", dh), ("spread", ds)):
            se_i, se_c = _cr1_se(d, wk)
            sd = float(np.std(d, ddof=1))
            deff = (se_c / se_i) ** 2
            row[key] = {"mean": float(d.mean()), "sd": sd, "deff": deff,
                        "mde": {m: 2.8 * sd * math.sqrt(deff / m) for m in (GATE_PAIRS, 700, 1000)}}
        out.append(row)
    return out


def print_power(rows: list[dict]) -> None:
    print(f"H-hold / H-spread power under the null (seeded; {HOLD_SESSIONS} sessions, z {HOLD_Z:g} / "
          f"{LONG_Z:g}, pairs clustered by entry week)")
    for r in rows:
        h, s, mg = r["hold"], r["spread"], r["margin"]
        print(f"  {r['label']}: pairs {r['pairs']:>5} | hold signal {r['hold_sig']:.3f} control "
              f"{r['hold_ctl']:.3f}")
        print(f"     margin diff {mg['mean']:+.4f} sd {mg['sd']:.3f} DEFF {mg['deff']:.2f} | MDE "
              + "  ".join(f"n{m} {v:.2f} (= {hold_equivalent(v) * 100:.1f}pp held)"
                          for m, v in mg["mde"].items()))
        print(f"     hold   diff {h['mean']:+.4f} sd {h['sd']:.3f} DEFF {h['deff']:.2f} | MDE "
              + "  ".join(f"n{m} {v * 100:.1f}pp" for m, v in h["mde"].items()))
        print(f"     spread diff {s['mean']:+.4f} sd {s['sd']:.3f} DEFF {s['deff']:.2f} | MDE "
              + "  ".join(f"n{m} {v * 100:.1f}% of risk" for m, v in s["mde"].items()))


def selftest() -> int:
    print("hold_test.py selftest")
    print("=" * 68)
    # the level and the verdict
    assert abs(hold_level(100.0, 0.02) - 100.0 * math.exp(-0.02 * math.sqrt(21))) < 1e-12
    assert held(91.2, 91.2) == 1 and held(91.19, 91.2) == 0, "at the level holds; a cent under does not"
    # realized vol: a constant 1 % daily log move has sd 0; alternating moves have the textbook sd
    c = 100.0 * np.exp(np.cumsum([0.0] + [0.01, -0.01] * 10))
    assert abs(realized_vol(c) - float(np.std([0.01, -0.01] * 10, ddof=1))) < 1e-12
    assert math.isnan(realized_vol(c[:10])), "too few closes is NaN, not a number"
    assert abs(margin(100.0, 100.0 * math.exp(-0.02 * math.sqrt(21)), 0.02) + 1.0) < 1e-12, \
        "a close exactly at the level is a margin of -HOLD_Z"
    assert abs(hold_equivalent(0.0)) < 1e-15 and 0.03 < hold_equivalent(0.2) < 0.06
    print("level            : fill x exp(-z sigma sqrt(n)); at the level holds; the margin is -z there")
    # the spread: a credit, a bounded loss, the two ends of the payoff
    m = spread_model(100.0, 0.02)
    k1, k2 = m["legs"][0][1], m["legs"][1][1]
    assert k2 < k1 < 100.0 and m["credit"] > 0 and m["cost"] > 0 and m["max_loss"] > 0, m
    top = spread_return(m, 120.0)
    bot = spread_return(m, 50.0)
    assert abs(top - (m["credit"] - m["cost"]) / m["max_loss"]) < 1e-12, "above the short strike: keep the credit"
    assert abs(bot - (-1.0 - m["cost"] / m["max_loss"])) < 1e-12, "below the long strike: lose the risk, plus cost"
    mid = spread_return(m, (k1 + k2) / 2.0)
    assert bot < mid < top
    print(f"spread           : short {k1:.2f} / long {k2:.2f} on a 100 fill, sigma 2 %: credit "
          f"{m['credit']:.3f}, cost {m['cost']:.3f}; +{top * 100:.1f} % of risk above, {bot * 100:.1f} % below")
    # the power check runs and is unbiased under the null
    rows = power(seed=3, n_tickers=30, n_sessions=300, settings=(("t", 0.010, 0.014),))
    r = rows[0]
    assert r["pairs"] > 500 and 0.7 < r["hold_sig"] < 0.98 and abs(r["hold"]["mean"]) < 0.05, r
    assert r["hold"]["deff"] >= 1.0 and r["spread"]["deff"] >= 1.0
    assert r["hold"]["mde"][GATE_PAIRS] > r["hold"]["mde"][1000]
    print(f"power            : null difference {r['hold']['mean']:+.3f} on {r['pairs']} pairs; clustered "
          f"design effect >= 1; the MDE falls with n")
    assert HOLD_SESSIONS == 21 and SECONDARY_SESSIONS == (15, 31) and HOLD_Z == 1.0 and LONG_Z == 2.0 \
        and VOL_LOOKBACK == 20 and GATE_PAIRS == 349, "the pre-registered constants"
    print("=" * 68)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--power", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.power:
        print_power(power())
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
