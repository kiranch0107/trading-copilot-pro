#!/usr/bin/env python3
"""
risk_params.py — one home for account size and the two percentage budgets

WHY THIS EXISTS
---------------
app.py and scanner.py each hardcoded their own ACCOUNT_SIZE and RISK_PCT:

    app.py      ACCOUNT_SIZE = 1500   RISK_PCT = 1.0
    scanner.py  ACCOUNT_SIZE = 1500   RISK_PCT = 5.0

A review flagged that as a 5x position-sizing discrepancy. It is not — the two
RISK_PCTs are different quantities that happen to share a name, which is worse
in one way and better in another:

    app.RISK_PCT      -> calc_position_size(entry, stop, account, risk_pct)
                         POSITION SIZING. Risk is the distance to the stop, so
                         1% of a 1500 account means "lose at most $15 if the
                         stop is hit", and the position can be far larger.

    scanner.RISK_PCT  -> budget = ACCOUNT_SIZE * RISK_PCT / 100
                         MAX PREMIUM PER CONTRACT. On a long option the
                         premium IS the maximum loss, so 5% means "never
                         suggest a contract costing more than $75".

Both numbers are defensible for what they actually do. 1% of the account as a
premium budget would be $15, which buys essentially no liquid contract; 5% as a
stop-distance risk would be reckless. The bug was never the values — it was
that one identifier meant two things across two modules that are supposed to
agree, which is precisely how the earlier app/scanner signal divergences
started. They are named apart here and each says what it governs.

THE REAL DIVERGENCE, LEFT ALONE DELIBERATELY
---------------------------------------------
There IS one genuine inconsistency and it is not fixed here, because fixing it
changes which contracts the system suggests:

    scanner  caps premium at ACCOUNT_SIZE * OPTION_BUDGET_PCT / 100  = $75
    app      caps premium at BUDGET_MAX (option mid) * 100           = $200

Same concept, two rules, differing by 2.7x. The percentage rule is the better
one — it scales with the account and ties the cap to risk — but switching
app.py to it would change what it shows mid-experiment. Recorded here as a
decision to be taken deliberately, not folded into a cleanup commit.
"""
from __future__ import annotations

# Deliberately Streamlit-free: scanner.py imports this and runs on GitHub
# Actions, where Streamlit is not installed (see consistency_check.py's
# unattended-import check).

DEFAULT_ACCOUNT_SIZE = 1500

# Position sizing: percentage of the account risked between entry and stop.
# app.py exposes this as a sidebar input; this is the default it starts at.
DEFAULT_RISK_PCT = 1.0

# Option premium cap: percentage of the account allowed as the FULL cost of a
# single contract, because on a debit position the premium is the max loss.
# Used by scanner.py to decide whether a suggested contract is affordable.
DEFAULT_OPTION_BUDGET_PCT = 5.0

# HARD CEILING on a single position, as a percentage of the account.
#
# Two thresholds rather than one, deliberately. A gate set at the 5% premium
# budget would fire on almost every contract actually traded here ($48, $106,
# $109, $136, $225 — four of six over budget), and a warning that fires every
# time is a warning you learn to click through. So:
#
#   over DEFAULT_OPTION_BUDGET_PCT  -> WARN. You are outside your stated rule.
#   over MAX_POSITION_PCT           -> BLOCK. Requires an explicit override.
#
# 25% is not a considered optimum; it is a backstop. It exists because a paper
# trade on 2026-09-03 was logged at $625 on a $1,500 account — 42% of capital
# in one long option, where the premium is the entire maximum loss — and no
# code path objected. It lost $448. On paper, which is exactly what paper
# trading is for; the hole it found is real.
MAX_POSITION_PCT = 25.0

# MEASURED OPTION-LEVEL WIN RATE for this signal.
#
# 23.8% — option_backtest.py, TP +100% / SL -50%, 5 years, 7 tickers.
#
# It lives here because two separate places reason about it and they must not
# drift: app.py's expected-value line, and the spread ceiling derived below.
# An earlier version of app.py used 40%, which is the SHARE backtest's win
# rate. An option needs a far larger underlying move to gain 100% than a share
# needs to reach 3xATR, and theta works against you the whole time, so the
# share number made losing configurations look profitable.
OPT_WIN_RATE = 0.238

# THE BASIS THAT 23.8% WAS MEASURED AT. Not decoration — a win rate is
# meaningless without the payoff rule it was measured under, and this repo
# already paid for that once: the spread ceiling below was derived by comparing
# 23.8% against a breakeven computed at a DIFFERENT take-profit.
#
# option_backtest.py's defaults are `--tp 100 --sl 50 --dte-exit 7
# --spread-pct 5`, so 23.8% is the rate at which a contract reached +100%
# before -50%, with a 5% round-trip spread ALREADY charged inside it.
#
# A +200% target is strictly harder to reach than +100%, so the win rate at
# +200% is necessarily LOWER than 23.8% — it is not measured, and it must not
# be assumed. option_backtest.py --sweep prints the rate at each TP level;
# until one is recorded here, nothing may compare 23.8% to a +200% breakeven.
OPT_WIN_RATE_TP_PCT = 100.0
OPT_WIN_RATE_SL_PCT = 50.0

# MAXIMUM BID-ASK SPREAD, as a percentage of the option mid.
#
# CORRECTED 2026-09-10 — THE ORIGINAL DERIVATION COMPARED TWO DIFFERENT RULES
#
# This block used to read: "take profit +200%, stop -50%, so the raw payoff is
# 4:1, breakeven is 20% and 23.8% clears it by 3.8 points", and concluded that
# the ceiling sat at the point where that arithmetic turned. Both halves
# were unsound, because 23.8%
# was never measured at +200% — option_backtest.py's default is `--tp 100`
# (see OPT_WIN_RATE_TP_PCT above). A win rate from an EASIER target was being
# used to validate the breakeven of a HARDER one, which flatters the result in
# exactly the direction that makes trading look justified.
#
# Scored at the basis it was actually measured at, TP +100% / SL -50%:
#
#     spread   breakeven WR   vs measured 23.8%
#        0%        33.3%            BELOW by 9.5 points
#        5%        36.8%            BELOW
#        8%        38.9%            BELOW
#       10%        40.4%            BELOW
#       15%        44.1%            BELOW
#
# THERE IS NO SPREAD AT WHICH THIS CONFIGURATION BREAKS EVEN. It loses before
# any spread is paid, and the 23.8% already has a 5% round trip inside it. That
# is also what option_backtest.py prints on its own defaults — "the measured win
# rate does NOT support this payoff structure" — so the two halves of the repo
# were stating opposite conclusions from the same number.
#
# WHY 8.0 STAYS ANYWAY, AND WHAT IT IS NOW
#
# It is no longer "where the arithmetic turns" — no such point exists here. It
# is a LOSS-MINIMISING COST CAP: a tighter spread makes each losing trade lose
# less, and tightening a cost cannot overfit because it does not touch the
# signal. Lowering it further would mostly empty the tradeable universe; raising
# it pays a larger toll on a structure that is already negative.
#
# The VALUE is left alone deliberately, following this file's own convention for
# the app/scanner premium-cap divergence below: changing which contracts the
# system suggests is a decision to take on purpose, not to fold into a
# correctness fix. What changed here is the claim, which was false.
#
# TO ACTUALLY SETTLE IT: run `option_backtest.py --sweep` and record the win
# rate at the TP you intend to trade, then re-derive against THAT. Comparing
# across bases is the error this block exists to prevent.
MAX_OPTION_SPREAD_PCT = 8.0


def option_budget(account_size: float | None = None,
                  budget_pct: float | None = None) -> float:
    """Maximum dollars for one contract. Kept as a function so both callers
    compute it identically rather than each re-deriving the arithmetic."""
    acct = DEFAULT_ACCOUNT_SIZE if account_size is None else account_size
    pct = DEFAULT_OPTION_BUDGET_PCT if budget_pct is None else budget_pct
    return acct * pct / 100.0


def spread_breakeven_wr(spread_pct: float,
                        tp_pct: float = OPT_WIN_RATE_TP_PCT,
                        sl_pct: float = OPT_WIN_RATE_SL_PCT) -> float:
    """
    Win rate needed to break even once the bid-ask spread is paid both ways.

    You buy above the mid and sell below it, so a `spread_pct` round trip
    shrinks the win and deepens the loss. Kept as a function so the threshold
    above can be re-derived rather than re-typed if the rules change.

    THE DEFAULTS ARE THE MEASURED BASIS, not a plausible-looking payoff. They
    used to be tp_pct=200.0 while OPT_WIN_RATE was measured at +100%, so every
    caller that relied on the defaults silently compared a win rate from one
    rule against the breakeven of another. Pass tp_pct/sl_pct explicitly to ask
    about a different structure — and if you do, remember that OPT_WIN_RATE is
    NOT the win rate for that structure.

    IT MODELS THE SPREAD, NOT THE EARLY EXITS — SO IT UNDERSTATES BREAKEVEN.
    Measured 2026-09-10 by `option_backtest.py --sweep` (BACKLOG 7): trades do
    not reach their nominal levels, because the DTE-7 floor and max-hold close
    them first. At TP+200/SL-50 the REALISED average win is +143.0%, not +200%,
    and the average loss -44.6%, not -50%:

        nominal payoff   4.00:1  ->  breakeven 20.0%
        with 5% spread            ->  breakeven 22.1%   (what this returns)
        REALISED payoff  3.21:1  ->  breakeven 23.8%   (what actually happened)

    The gap is roughly 1.7 points across the sweep. This function still reaches
    the right verdict at the current 8% ceiling, but by margin rather than by
    modelling, and that is not a property to rely on. For a real decision use the
    realised avg win / avg loss from the sweep table in BACKLOG.md, not nominal
    TP/SL.
    """
    h = spread_pct / 2 / 100
    buy = 1 + h
    tp = (1 + tp_pct / 100) * (1 - h) / buy - 1
    sl = (1 - sl_pct / 100) * (1 - h) / buy - 1
    return -sl / (tp - sl) * 100


def check_option_cost(entry_premium: float, contracts: float,
                     account_size: float | None = None,
                     budget_pct: float | None = None,
                     max_position_pct: float | None = None) -> dict:
    """
    Verdict on the size of ONE option position, before it is logged.

    On a long option the premium IS the maximum loss, so this is not an
    approximation of risk — it is the whole of it.

    Returns a dict with:
        level    "ok" | "warn" | "block" | "invalid"
        cost     total dollars at risk
        pct      percentage of the account
        budget   the soft budget in dollars
        ceiling  the hard ceiling in dollars
        message  one line, written for a human about to click a button
    """
    acct = DEFAULT_ACCOUNT_SIZE if account_size is None else float(account_size)
    bpct = DEFAULT_OPTION_BUDGET_PCT if budget_pct is None else float(budget_pct)
    cpct = MAX_POSITION_PCT if max_position_pct is None else float(max_position_pct)

    prem = float(entry_premium or 0)
    qty = float(contracts or 0)

    if prem <= 0 or qty <= 0:
        # A zero premium makes every percentage rule meaningless: a -50% stop
        # on a $0 entry can never trigger, so the position would be monitored
        # by rules that cannot fire. One logging path could reach this via
        # `entry_premium=... or 0.0`.
        return {"level": "invalid", "cost": 0.0, "pct": 0.0,
                "budget": acct * bpct / 100, "ceiling": acct * cpct / 100,
                "message": ("Premium and contracts must both be above zero. "
                            "At a zero entry premium the percentage stop and "
                            "target can never trigger, so the monitor would "
                            "watch this position forever without alerting.")}

    cost = prem * 100.0 * qty
    pct = cost / acct * 100 if acct else 0.0
    budget = acct * bpct / 100
    ceiling = acct * cpct / 100

    if cost > ceiling:
        return {"level": "block", "cost": cost, "pct": pct,
                "budget": budget, "ceiling": ceiling,
                "message": (f"${cost:,.0f} is {pct:.0f}% of your ${acct:,.0f} "
                            f"account in ONE long option, where the premium is "
                            f"the entire maximum loss. The ceiling is "
                            f"{cpct:g}% (${ceiling:,.0f}). Reduce contracts, "
                            f"choose a cheaper strike, or tick the override "
                            f"box to log it anyway.")}
    if cost > budget:
        return {"level": "warn", "cost": cost, "pct": pct,
                "budget": budget, "ceiling": ceiling,
                "message": (f"${cost:,.0f} is {pct:.0f}% of the account — over "
                            f"your {bpct:g}% premium budget (${budget:,.0f}), "
                            f"under the {cpct:g}% ceiling. Allowed, but this is "
                            f"outside the rule you set.")}
    return {"level": "ok", "cost": cost, "pct": pct,
            "budget": budget, "ceiling": ceiling,
            "message": (f"${cost:,.0f} — {pct:.0f}% of the account, within "
                        f"your {bpct:g}% premium budget.")}


def selftest() -> int:
    assert DEFAULT_ACCOUNT_SIZE > 0
    assert 0 < DEFAULT_RISK_PCT <= 10, \
        "position risk outside 0-10% is almost certainly a typo"
    assert 0 < DEFAULT_OPTION_BUDGET_PCT <= 25, \
        "premium budget outside 0-25% is almost certainly a typo"
    assert DEFAULT_RISK_PCT != DEFAULT_OPTION_BUDGET_PCT or True  # may coincide

    assert option_budget() == 75.0, option_budget()
    assert option_budget(3000) == 150.0
    assert option_budget(1500, 1.0) == 15.0
    print(f"account ${DEFAULT_ACCOUNT_SIZE:,} · position risk "
          f"{DEFAULT_RISK_PCT:g}% · premium budget "
          f"{DEFAULT_OPTION_BUDGET_PCT:g}% (${option_budget():,.0f}/contract)")

    # This module must never pull in Streamlit: scanner.py imports it and runs
    # on GitHub Actions, which installs no Streamlit.
    import sys
    assert "streamlit" not in sys.modules or __name__ != "__main__", \
        "risk_params must be importable without Streamlit"
    # ── position-size verdicts ──
    # Anchored on the real trades in trade_journal.json so the thresholds are
    # checked against what actually gets logged, not invented examples.
    ok = check_option_cost(0.48, 1)          # $48  — a real trade
    assert ok["level"] == "ok", ok
    warn = check_option_cost(2.25, 1)        # $225 — a real trade, 15%
    assert warn["level"] == "warn", warn
    block = check_option_cost(6.25, 1)       # $625 — the paper trade, 42%
    assert block["level"] == "block", block
    assert "42%" in block["message"]
    print(f"size verdicts             : $48 ok · $225 warn · $625 BLOCK")

    # Contracts multiply. Two cheap contracts can breach a ceiling one cannot.
    assert check_option_cost(2.00, 1)["level"] == "warn"
    assert check_option_cost(2.00, 2)["level"] == "block", \
        "the check must multiply by contracts — sizing up is how a $200 " \
        "position becomes a $400 one"
    print(f"contracts counted        : 1x$200 warn, 2x$200 BLOCK")

    for bad in ((0.0, 1), (2.25, 0), (-1.0, 1)):
        v = check_option_cost(*bad)
        assert v["level"] == "invalid", (bad, v)
    print(f"zero / negative premium  : invalid (a %-stop on $0 never fires)")

    assert check_option_cost(6.25, 1, account_size=100_000)["level"] == "ok", \
        "the verdict must scale with the account, not be a fixed dollar rule"
    print(f"scales with account      : $625 is fine on a $100k account")

    # ── the spread arithmetic must be scored at the MEASURED basis ──
    # Read the constants rather than re-typing them. A selftest that carries its
    # own copy of the number it is checking keeps passing after the real number
    # moves — which is the failure this repo keeps finding.
    wr = OPT_WIN_RATE * 100

    # The defaults MUST be the basis the win rate was measured at. They were
    # tp_pct=200 while OPT_WIN_RATE came from --tp 100, which is how a losing
    # structure came to be documented as clearing breakeven by 3.8 points.
    import inspect
    _d = inspect.signature(spread_breakeven_wr).parameters
    assert _d["tp_pct"].default == OPT_WIN_RATE_TP_PCT, (
        f"spread_breakeven_wr defaults to TP {_d['tp_pct'].default}% but "
        f"OPT_WIN_RATE was measured at {OPT_WIN_RATE_TP_PCT}%. Comparing a win "
        f"rate to the breakeven of a different payoff is the original bug.")
    assert _d["sl_pct"].default == OPT_WIN_RATE_SL_PCT, "SL basis drifted"

    be0 = spread_breakeven_wr(0)
    assert abs(be0 - 100 * OPT_WIN_RATE_SL_PCT /
               (OPT_WIN_RATE_TP_PCT + OPT_WIN_RATE_SL_PCT)) < 1e-9, be0

    # The honest finding: at the measured basis NO spread breaks even, so the
    # ceiling is a loss cap and must not be described as clearing breakeven.
    assert be0 > wr, (
        f"breakeven at zero spread is {be0:.1f}% and the measured win rate is "
        f"{wr:.1f}% — if this ever flips, the whole comment block above is "
        f"stale and the ceiling can be re-derived properly.")
    at_gate = spread_breakeven_wr(MAX_OPTION_SPREAD_PCT)
    assert at_gate > wr, (
        f"at the measured basis a {MAX_OPTION_SPREAD_PCT:g}% spread needs "
        f"{at_gate:.1f}% and the signal delivers {wr:.1f}%")
    # Monotone in cost: a wider spread can only need a higher win rate. If this
    # breaks, the formula is wrong, not the constant.
    assert spread_breakeven_wr(15) > at_gate > be0, \
        "breakeven must rise with spread"

    # LIVENESS: the basis has to MATTER, or pinning it proves nothing.
    be_wrong_basis = spread_breakeven_wr(MAX_OPTION_SPREAD_PCT, tp_pct=200.0)
    assert be_wrong_basis < wr < at_gate, (
        f"the two bases no longer disagree (TP+200 -> {be_wrong_basis:.1f}%, "
        f"TP+{OPT_WIN_RATE_TP_PCT:g} -> {at_gate:.1f}%), so this check would "
        f"pass even with the basis confusion restored")
    print(f"spread basis pinned      : TP+{OPT_WIN_RATE_TP_PCT:g}/SL-"
          f"{OPT_WIN_RATE_SL_PCT:g}, the basis 23.8% was measured at")
    print(f"spread ceiling           : {MAX_OPTION_SPREAD_PCT:g}% -> breakeven "
          f"{at_gate:.1f}% vs measured {wr:.1f}% — NEGATIVE at every spread "
          f"(zero-spread breakeven {be0:.1f}%); it is a loss cap, not an edge")
    print(f"  (the old TP+200 basis made the same gate look like {be_wrong_basis:.1f}% "
          f"— 'clears by {wr - be_wrong_basis:.1f} points'. That was the bug.)")

    print("streamlit-free            : safe for the unattended workflows")
    print("\nAll self-tests passed.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
