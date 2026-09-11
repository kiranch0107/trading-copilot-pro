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

## 5. Stopping condition: MECHANISM-BASED — chosen 2026-09-11

Selected over literature-watching and cost-watching. It is the right choice in
principle and the **hardest to operationalise**, so it is specified here rather
than left as a sentiment.

### The failure mode this must avoid

Mechanisms decay silently. Nobody issues a notice. **If you cannot name
something you can actually watch, "mechanism-based" collapses into "never
stop"** — which is worse than a performance rule, because it feels principled
while being unfalsifiable. A mechanism-based rule is only valid if it comes with
an observable and a threshold.

### Grading the candidates by whether their mechanism is watchable

| premium | mechanism | observable | suitability |
|---|---|---|---|
| **value** | distress-risk compensation; overextrapolation of growth | **the value spread itself** — the cheapness gap between value and growth (P/B, P/E). Published, monthly, free. | **HIGH** |
| low-volatility | leverage constraints — investors wanting return who cannot lever bid up high-beta names | access to leverage: leveraged ETFs, 0DTE options, margin availability | MEDIUM — observable but fuzzy, and arguably **already loosened** |
| momentum | underreaction; slow diffusion of news | speed of price adjustment to news — **which is post-earnings drift** | **MECHANISM IMPAIRED — see below** |
| quality / profitability | contested; mispricing or a risk story | no agreed observable | LOW — cannot monitor |
| trend / CTA | risk transfer from hedgers; behavioural | none at retail reach | LOW — cannot monitor |

### Momentum is disqualified by this repo's own measurement

This is the consequential finding and it came free.

**Momentum and post-earnings drift rest on the same mechanism**: information
diffuses into prices slowly, so past moves predict future moves. That is one
claim, not two.

`results/pead_run1.md` tested it at the single most information-dense event a
stock has — an earnings surprise — across 953 events, and found **no diffusion
at all**. The grading was scrambled (ρ = −0.30); the worst surprises earned the
best subsequent returns.

Under a mechanism-based rule, that is **direct evidence against momentum's
premise**, measured here rather than assumed. A momentum bet would be taken
while holding a measurement that its mechanism is not operating in reachable
data. That is exactly the incoherence this document exists to prevent.

It does not prove momentum is dead — one 5-year large-cap window is not the
world. But a mechanism-based rule cannot ignore the one mechanism test this
project actually ran.

### The rule, stated so it can fire

**Watched premium: value.** It is the only candidate whose mechanism has a
published, freely available observable.

- **Observable**: the value spread — the valuation gap between value and growth
  (price/book or price/earnings ratio of a value index versus a growth index).
- **The mechanism holding** looks like: a persistent, wide gap. Value is cheap
  relative to growth because it is genuinely distressed or unloved, which is the
  raw material the premium is paid out of.
- **The mechanism broken** looks like: the spread compressing to historical lows
  and staying there. If value is no longer cheap relative to growth, there is
  nothing left to be compensated for, regardless of what the backtests said.
- **Threshold — FILL IN**: the spread percentile and the persistence required
  (e.g. "below the Nth percentile of its own history for M consecutive
  quarters"). Choose it before entering, not after a bad quarter.
- **Cadence**: quarterly. Frequent enough to notice a regime change, infrequent
  enough that quarter-to-quarter noise cannot trigger it.

### What is explicitly NOT a stopping trigger

Performance, in either direction. §4 stands: at Sharpe 0.40 your own P&L needs
49 years to say anything. A drawdown limit remains worth having as a **risk
control** — it caps loss — but it is not evidence, and it must never be
recorded here as the thesis having failed.

**FILL IN — the drawdown limit, labelled as a risk control.**

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
