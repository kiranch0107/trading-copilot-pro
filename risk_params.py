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


def option_budget(account_size: float | None = None,
                  budget_pct: float | None = None) -> float:
    """Maximum dollars for one contract. Kept as a function so both callers
    compute it identically rather than each re-deriving the arithmetic."""
    acct = DEFAULT_ACCOUNT_SIZE if account_size is None else account_size
    pct = DEFAULT_OPTION_BUDGET_PCT if budget_pct is None else budget_pct
    return acct * pct / 100.0


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
    print("streamlit-free            : safe for the unattended workflows")
    print("\nAll self-tests passed.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
