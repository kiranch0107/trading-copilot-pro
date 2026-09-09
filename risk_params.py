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


def option_budget(account_size: float | None = None,
                  budget_pct: float | None = None) -> float:
    """Maximum dollars for one contract. Kept as a function so both callers
    compute it identically rather than each re-deriving the arithmetic."""
    acct = DEFAULT_ACCOUNT_SIZE if account_size is None else account_size
    pct = DEFAULT_OPTION_BUDGET_PCT if budget_pct is None else budget_pct
    return acct * pct / 100.0


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

    print("streamlit-free            : safe for the unattended workflows")
    print("\nAll self-tests passed.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
