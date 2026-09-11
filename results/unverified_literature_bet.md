# Trading unverified literature, made explicit

**Started 2026-09-11. Sections marked FILL IN are decisions for the account
owner, not for this repo to make.**

`results/verifiability_screen.md` established that the classic factor literature
cannot be verified with data this project can reach — Sharpe 0.30–0.50 needs
31–87 years, and ~20 are available. The remaining legitimate option is to trade
it **on the strength of the literature, openly labelled as unverified**, rather
than to launder it through an underpowered backtest that "confirmed" it.

This document is what "openly" means. It exists so the decision is recorded
before the money moves, and so a future session cannot mistake it for a
measured result.

## 1. What is being accepted on faith

Not "factors work." Something narrower and checkable-in-principle:

> That the published long-run premia for one or more of value, quality,
> low-volatility, momentum and trend are real and persist at a magnitude
> materially above zero, net of the costs of the chosen implementation.

**FILL IN — which specific premia, and via which instruments.**

## 2. Honest evidence grade

What the literature genuinely supports:

- Decades of data, multiple countries, multiple asset classes, and out-of-sample
  survival since original publication for momentum and value in particular.
- Independent replication by parties with no stake in the original result.

What it does **not** support, and must not be quietly dropped:

- **Replication rates are poor in aggregate.** Hou, Xue & Zhang re-tested 452
  published anomalies under consistent methodology; roughly two-thirds failed to
  reach conventional significance. Being *in the literature* is weak evidence on
  its own; the handful above are the survivors, not the average.
- **Premia have decayed post-publication.** Several studies find anomaly returns
  fall materially after the paper appears. This is exactly what PEAD showed here
  — mechanism plus forty years of literature, and nothing in reachable data.
- **The long-only, retail-implementable version is not what was measured.** The
  published premia are long-short. Long-only funds capture roughly half, and the
  factor ETFs correlate 0.5–0.7 with each other because they all hold the market.
  The screen put the excess-over-SPY Sharpe at 0.25–0.50.

**Net: this is a reasonable bet, not a safe one, and the implementable version
is materially weaker than the published one.**

## 3. Why it cannot be verified here

`(z / Sharpe)^2`. At Sharpe 0.40 that is 49 years; ~20 are reachable. No
rebalancing frequency, ticker count or clever cut changes it —
`power_check.span_tstat()` pins that the t-statistic is invariant to sampling
frequency over a fixed calendar span.

## 4. The trap: your own P&L cannot be the stopping rule

This is the part that is easy to get wrong, and it follows from the same
arithmetic.

**Your live track record is a sample too.** Years of your own returns needed
before they could distinguish "this works" from "this does not":

| Sharpe | years of live P&L |
|---|---|
| 0.30 | 87 |
| 0.40 | 49 |
| 0.50 | 31 |
| 0.80 | 12 |

A 0.40-Sharpe bet needs **49 years of your own returns** before the P&L is
evidence about the strategy. Three bad years tells you nothing. **Three good
years tells you nothing either** — and that direction is the more dangerous one,
because it invites adding size to a bet that has not been validated.

So the abandonment condition **must not be performance-based.** A drawdown limit
is still worth having, but it is a *risk control*, not evidence: it caps the
loss, it does not tell you the premise was wrong.

## 5. Valid stopping conditions — not about your returns

- **The literature moves.** A credible failed replication of the specific premia
  relied on, or a well-documented post-publication decay measured by others.
- **The implementation degrades.** Fund fees rise, tracking error widens, the
  vehicle changes mandate, or the spreads you pay materially increase.
- **The premise changes.** The mechanism you named in §1 stops being plausible —
  e.g. the structural reason the premium existed is arbitraged or regulated away.
- **A pre-committed review date**, at which the above are re-examined on their
  own merits rather than on how the position happened to perform.

**FILL IN — review cadence, and a drawdown limit stated explicitly as a risk
control rather than as a test of the thesis.**

## 6. Size it as what it is

An unverified bet should not be sized like a verified one. This repo has one
measured, reproduced result (`vrp_check`: +3.63 vol points, 11/11 years) and it
still did not license trading, because the harvest failed separately.

**FILL IN — allocation, stated as a fraction of the account, with the reasoning
for why that fraction and not double it.**

## 7. What this document is not

It is not a recommendation, and this repo is not competent to give one. It is a
record that makes an unverifiable premise *legible* — so that in a year, the
reasoning can be audited, and so that a good run cannot be retold as
confirmation.

If §§1, 5 and 6 are left blank, the bet has not actually been made explicit and
this file is decoration.
