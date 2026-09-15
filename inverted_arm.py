#!/usr/bin/env python3
"""
inverted_arm.py — the short signal, inverted, split by regime.

WHY

drift_null measured the short signal as WORSE than random entry: excess -9.5pp
against a -4.6pp random null, all 200 draws better, mean R -0.341 vs -0.210.

A signal reliably worse than random carries information with the sign flipped.
The bars where the short signal fires are bars from which price rises MORE than
random bars do, by roughly 4.9pp of hit rate beyond drift. So: enter LONG at
those bars, against the same random-long null.

THE OBJECTION, WHICH IS THE POINT OF THE DESIGN

"Buy the pullback in an uptrend" is near-tautological over 2016-2026, because
there was an uptrend. The short signal fires on RSI under 40, price under both
EMAs, MACD bearish -- in a bull market that is a dip, and dips got bought. In a
bear market the same setup is a falling knife.

The headline number is therefore not the test. The test is whether the effect
survives when the market is not rising.

THE NULL IS COMPUTED PER REGIME

2016-2026 is mostly Bull, so a null drawn across all bars is a mostly-Bull
null. Comparing a regime-clustered arm against it would confound regime mix
with effect. Within each regime, an arm is compared only against random entries
from that same regime.

Pre-registered in results/inverted_arm_preregistration.md, committed before
this file existed.
"""
from __future__ import annotations

import argparse
import random
import sys

import numpy as np

import backtest as bt
import drift_null as dn
import setup_cases as scs

MIN_TRADES = 100          # below this a regime cell refuses a verdict
MIN_DRAWS = 20


def regime_of(df, trades: list[dict], reg: "object") -> None:
    """Tag each trade with SPY's regime at its SIGNAL bar, in place.

    The signal bar, not the exit: the regime a trade was ENTERED into is the
    condition being tested. Reading it at the exit would let a trade that
    started in a bull market and ended in a crash be filed under Bear, which is
    the outcome leaking into the label.
    """
    for t in trades:
        i = (t.get("setup") or {}).get("signal_i")
        # EVERY arm here stamps signal_i. A fallback to bar 0 would file the
        # whole sample under whatever regime the series opens in, silently, and
        # the regime split is the entire point of this module.
        if i is None:
            raise AssertionError(
                "a trade reached regime_of() with no signal_i. Every arm "
                "stamps it; defaulting would file trades under the wrong "
                "regime without erroring, and the regime split is the test")
        i = int(i)
        t["regime"] = (str(reg.iloc[i]) if reg is not None and i < len(reg)
                       else "Unknown")


def arm_trades(df, cfg, params, want: str) -> list[dict]:
    """Real signal bars for one direction, entered LONG.

    `want` is the direction the SIGNAL fired in. The trade is always taken
    long: that is the inversion. Levels are rebuilt long-side from the same ATR
    multiples, so the inverted arm has the same geometry as the random null and
    as the real long arm -- only the choice of BAR differs, which is the one
    thing under test.
    """
    out = []
    n = len(df)
    i = 0
    while i < n - 1:
        sig = bt.evaluate_signal(df, i, params)
        if sig and sig.get("trend") == want:
            atr = float(sig.get("atr") or 0)
            px = float(df["Close"].iloc[i])
            if atr > 0 and px > 0:
                stop = px - cfg["atr_stop_mult"] * atr
                target = px + cfg["atr_tgt_mult"] * atr
                trade = {"trend": "Bullish", "entry": px, "stop": stop,
                         "target": target,
                         "rr": round(abs(target - px) / abs(px - stop), 2),
                         "atr": atr, "price": px, "signal_i": i}
                res = bt.simulate_trade(df, i, trade, cfg)
                if res.get("filled"):
                    out.append(res)
                    i += cfg["cooldown_bars"] + 1
                    continue
        i += 1
    return out


def by_regime(trades: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for t in trades:
        out.setdefault(t.get("regime", "Unknown"), []).append(t)
    return out


def run(tickers: list[str], years: int, draws: int, seed: int) -> int:
    cfg = dict(bt.DEFAULTS, tickers=tickers, years=years)
    params = bt.build_signal_params(cfg)
    rng = random.Random(seed)

    # SPY's regime, keyed by date, exactly as backtest.run builds it. SPY is
    # already spent (the contaminated set records it as the regime filter and
    # RS benchmark), so this consumes no new data.
    spy = bt.download("SPY", years)
    if spy is None:
        print("no SPY data — the regime split is the whole point, refusing to "
              "run without it")
        return 2
    reg_by_date = dict(zip(spy["Date"], bt.build_regime_series(spy)))

    arms: dict[str, list[dict]] = {"REAL LONG": [], "INVERTED": []}
    null: list[list[dict]] = [[] for _ in range(draws)]
    unmatched = 0
    print("  collecting ...", file=sys.stderr)
    for tk in tickers:
        raw = bt.download(tk, years)
        if raw is None:
            print(f"  ! {tk}: no data", file=sys.stderr)
            continue
        df = bt.compute(raw).copy()
        if "Date" not in df.columns:
            continue
        mapped = df["Date"].map(reg_by_date)
        # A ticker date SPY does not have would silently become one regime or
        # another. Counted and printed rather than absorbed — this project has
        # already paid once for a regime join that failed quietly.
        unmatched += int(mapped.isna().sum())
        reg = mapped.fillna("Unknown").reset_index(drop=True)

        real_long = arm_trades(df, cfg, params, "Bullish")
        inverted = arm_trades(df, cfg, params, "Bearish")
        regime_of(df, real_long, reg)
        regime_of(df, inverted, reg)
        arms["REAL LONG"].extend(real_long)
        arms["INVERTED"].extend(inverted)

        # The null is drawn once per ticker per draw, at the size of the
        # INVERTED arm, then split by regime downstream.
        k = len(inverted)
        if k:
            for d in range(draws):
                rt = dn.random_entries(df, k, "Bullish", cfg, rng)
                regime_of(df, rt, reg)
                null[d].extend(rt)
        print(f"  {tk}: {len(real_long)} long, {len(inverted)} inverted",
              file=sys.stderr)

    print("=" * 84)
    print("INVERTED ARM — the short signal's bars, entered LONG")
    print("=" * 84)
    print(f"  {len(tickers)} tickers, {years} years, {draws} random draws")
    print(f"  regime: SPY vs its own 200-SMA, read at the SIGNAL bar")
    if unmatched:
        print(f"  ! {unmatched} ticker bars had no SPY date — filed Unknown")
    print(f"  pre-registered: results/inverted_arm_preregistration.md")

    regimes = ["Bull", "Bear", "Neutral"]
    verdicts: dict[str, str] = {}
    for label in ("REAL LONG", "INVERTED"):
        print(f"\n{'#'*84}\n# {label}\n{'#'*84}")
        split = by_regime(arms[label])
        for rg in regimes:
            ts = split.get(rg, [])
            nd_lists = [[t for t in d if t.get("regime") == rg] for d in null]
            nd = [dn.excess(x) for x in nd_lists if x]
            n_real = len([t for t in ts if t.get("outcome") in ("win", "loss")])
            if n_real < MIN_TRADES or len(nd) < MIN_DRAWS:
                print(f"\n  {rg}: {n_real} trades, {len(nd)} usable draws — "
                      f"UNDERPOWERED, no verdict.")
                print(f"     Below {MIN_TRADES} trades or {MIN_DRAWS} draws a "
                      f"percentile is arithmetic, not evidence. An")
                print(f"     underpowered cell is NOT support for a "
                      f"regime-independent effect.")
                if label == "INVERTED":
                    verdicts[rg] = "UNDERPOWERED"
                continue
            c = dn.compare(ts, nd, f"{label} — {rg}")
            dn.report(c)
            if label == "INVERTED":
                verdicts[rg] = c["excess"]["verdict"]

    print(f"\n{'='*84}")
    print("VERDICT — does the inversion survive when the market is not rising?")
    bull, bear = verdicts.get("Bull"), verdicts.get("Bear")
    print(f"  INVERTED in Bull : {bull}")
    print(f"  INVERTED in Bear : {bear}")
    if bull == "SIGNAL CONTRIBUTES" and bear == "SIGNAL CONTRIBUTES":
        v = "REGIME-INDEPENDENT EFFECT"
        note = ("Clears the random null in a rising AND a falling market. The "
                "only result here that would justify spending tranche C.")
    elif bull == "SIGNAL CONTRIBUTES" and bear == "UNDERPOWERED":
        v = "BULL-ONLY, BEAR UNTESTED"
        note = ("Clears in Bull and the Bear cell has too few trades to say "
                "anything. This is NOT a regime-independent effect — it is an "
                "untested one, and twenty mega-cap tickers over one decade may "
                "simply not contain the bear data to settle it.")
    elif bull == "SIGNAL CONTRIBUTES":
        v = "BULL-ONLY EFFECT"
        note = ("Clears in a rising market and fails in a falling one. That is "
                "a description of 2016-2026, not a strategy. Does not go to "
                "tranche C.")
    else:
        v = "NOTHING"
        note = ("The inversion does not beat random entry even in the regime "
                "that should flatter it most.")
    print(f"\n  {v}")
    for line in note.split(". "):
        if line.strip():
            print(f"    {line.strip().rstrip('.')}.")
    print("\nIN-SAMPLE. Twenty spent tickers, one decade. A positive result is")
    print("a hypothesis for tranche C, not a finding, and not a reason to trade.")
    print("=" * 84)
    return 0


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------

def selftest() -> int:
    import pandas as pd
    print("inverted_arm.py selftest")
    print("=" * 72)

    cfg = dict(bt.DEFAULTS)
    params = bt.build_signal_params(cfg)

    # ── THE INVERSION: a BEARISH signal bar must produce a LONG trade ──
    down = bt._synthetic_ohlc(up=False, adx=40.0)
    inv = arm_trades(down, cfg, params, "Bearish")
    assert inv, "no bearish signal fired on a clean downtrend — guard is blind"
    for t in inv:
        s = t["setup"]
        assert s["trend"] == "Bullish", (
            f"the inverted arm must be LONG — that is the inversion. Got "
            f"{s['trend']}")
        assert s["stop"] < s["entry"] < s["target"], (
            f"a long's stop sits below its entry and its target above: {s}")
        assert abs(float(s["rr"]) - 3.0) < 0.01, s["rr"]
    # AND IT MUST BE THE REQUESTED DIRECTION'S BARS. Every assertion above
    # stays true if arm_trades takes EVERY signal bar regardless of `want` —
    # the trades would still be long with the right geometry. The two arms have
    # to select different bars, or `want` is decorative.
    lng = arm_trades(down, cfg, params, "Bullish")
    i_inv = {t["setup"]["signal_i"] for t in inv}
    i_lng = {t["setup"]["signal_i"] for t in lng}
    assert not (i_inv & i_lng), (
        f"the bullish and bearish arms share {len(i_inv & i_lng)} signal bars. "
        f"A bar cannot fire both ways, so arm_trades is ignoring `want` and "
        f"both arms are the same sample")
    assert len(i_inv) != len(i_lng), (
        f"both arms selected {len(i_inv)} bars on a one-sided downtrend, which "
        f"is what taking every signal regardless of direction looks like")
    print(f"inversion        : {len(inv)} bearish bars entered LONG, R:R 3.00; "
          f"bullish arm picks {len(lng)}, disjoint")

    # ── and the geometry must match the null it is compared against ──
    rn = dn.random_entries(down, 5, "Bullish", cfg, random.Random(2))
    assert rn, "no random entry filled"
    assert abs(float(rn[0]["setup"]["rr"]) - float(inv[0]["setup"]["rr"])) < 0.01, (
        "the inverted arm and the null must share geometry, or the comparison "
        "measures the levels rather than the choice of bar")
    print("geometry         : inverted arm and random null share R:R exactly")

    # ── REGIME IS READ AT THE SIGNAL BAR, never at the exit ──
    # A trade entered deep in a Bull stretch that exits after the flip must be
    # filed Bull. Reading the exit would let the outcome pick the label.
    reg = pd.Series(["Bull"] * 60 + ["Bear"] * 200)
    probe = [
        # entered at bar 10 (Bull), held 100 bars, so it EXITS at bar 110
        # (Bear). Any label taken from the exit files this under Bear and lets
        # the outcome choose the condition being tested.
        {"setup": {"signal_i": 10}, "hold": 100, "outcome": "loss", "r": -1.0},
        {"setup": {"signal_i": 120}, "hold": 3, "outcome": "win", "r": 3.0},
    ]
    regime_of(None, probe, reg)
    assert probe[0]["regime"] == "Bull", (
        f"a trade entered at bar 10 is a BULL trade however it ended; it "
        f"exited at bar 110 and was filed {probe[0]['regime']}")
    assert probe[1]["regime"] == "Bear", probe[1]
    # A self-defeating try/except: raising "did not raise" INSIDE the try is
    # caught by the same except, and the message check then reads my own text.
    # The flag has to live outside the block.
    raised = False
    try:
        regime_of(None, [{"setup": {}, "outcome": "win", "r": 1.0}], reg)
    except AssertionError as e:
        raised = "signal_i" in str(e)
    assert raised, (
        "a trade with no signal_i was filed anyway. Defaulting to bar 0 would "
        "put the whole sample in whichever regime the series opens in")
    print("regime label     : read at the signal bar, not the exit; missing raises")

    # ── by_regime splits, and nothing is lost ──
    sp = by_regime(probe)
    assert set(sp) == {"Bull", "Bear"} and sum(len(v) for v in sp.values()) == 2
    print("regime split     : every trade lands in exactly one bucket")

    # ── THE NULL MUST BE PER REGIME ──
    # A null drawn across a mostly-Bull decade is a mostly-Bull null. Comparing
    # a Bear-clustered arm against it measures the regime mix, not the effect.
    def mk(o, r, rg, rr=3.0):
        return {"outcome": o, "r": r, "regime": rg, "setup": {"rr": rr}}
    mixed = ([mk("win", 3.0, "Bull") for _ in range(40)] +
             [mk("loss", -1.0, "Bull") for _ in range(60)] +
             [mk("loss", -1.0, "Bear") for _ in range(90)] +
             [mk("win", 3.0, "Bear") for _ in range(10)])
    sp = by_regime(mixed)
    e_bull, e_bear = dn.excess(sp["Bull"]), dn.excess(sp["Bear"])
    assert abs(e_bull["excess"] - 0.15) < 1e-9, e_bull
    assert abs(e_bear["excess"] - (-0.15)) < 1e-9, e_bear
    e_pooled = dn.excess(mixed)
    assert abs(e_pooled["excess"]) < 1e-9, (
        f"the fixture must pool to zero or it proves nothing: {e_pooled}")
    assert e_bull["excess"] > e_pooled["excess"] > e_bear["excess"], (
        "pooling a +15pp Bull cell with a -15pp Bear cell reports zero and "
        "hides both. That is what a single null across all regimes does")
    print("per-regime null  : +15pp Bull and -15pp Bear pool to 0.0pp")

    # ── UNDERPOWERED IS NOT A PASS ──
    assert MIN_TRADES >= 100 and MIN_DRAWS >= 20, (MIN_TRADES, MIN_DRAWS)
    thin = dn.compare([mk("win", 3.0, "Bear")] * 5 + [mk("loss", -1.0, "Bear")] * 5,
                      [{"excess": 0.05, "mean_r": 0.1} for _ in range(5)], "T")
    assert thin["excess"]["verdict"] == "NOT ENOUGH DRAWS", thin["excess"]
    print(f"underpowered     : refuses below {MIN_TRADES} trades / "
          f"{MIN_DRAWS} draws, never reads as support")

    print("=" * 72)
    print("All self-tests passed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default=scs.DEFAULT_TICKERS)
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--draws", type=int, default=dn.DRAWS)
    ap.add_argument("--seed", type=int, default=20260915)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    return run(tickers, a.years, a.draws, a.seed)


if __name__ == "__main__":
    sys.exit(main())
