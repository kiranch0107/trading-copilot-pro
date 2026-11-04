# PRE-REGISTRATION — does the signal make the price HOLD over an option's life (H-hold), and a signal-timed put spread (H-spread)

**Owner's request 2026-11-04: "write the pre-registration for the 'price
holds over 21–45 days' test and the signal-timed put-spread arm".** Written
and committed BEFORE any code records or grades a hold, and before anyone
has computed a hold rate on any row of the forward record. The rules below
are fixed as functions in `hold_test.py`; the power check was run before
this text was finished, and it decides what this document recommends.

**Verdict of the power check (BACKLOG 13, step 2): the decision test is
VETOED as underpowered.** At the forward record's gate (349 pool pairs) it
can detect only an effect of 6–10 percentage points on the hold rate; the
plausible effect is 0–3. Detecting 3 points needs about 1,800–6,600 pairs,
2 to 7 years of signals. Section 6 says why, and section 8 what is offered
instead. Nothing is built by this document.

## 1. The question

The alerts buy a call with 21–45 days to expiry (`MEASURED_OPTION_STRUCTURE`)
and sell it at +200 % or 7 days before expiry. A long call needs the stock to
move up enough, soon enough, to beat time decay. A bull put credit spread
needs only that the price **holds** above its short strike until expiry: it
wins if the stock rises, goes sideways or dips a little. So the question an
options trader asks of the signal is not only "does it go up?" but **"after a
signal, does the price hold over the option's life more often than it would
anyway?"**

## 2. The mechanism (BACKLOG 13, step 1)

Two different sources of edge are in play, and only one has evidence:

- **Selling premium, always.** Option buyers pay for protection: implied
  volatility exceeds what is realised. Measured on the index:
  `results/vrp_measurement.md`, +3.63 vol points, positive in all 11 years.
  This needs no signal. The always-on SPY/QQQ put-spread and iron-condor
  arms test it forward (`spread_log`, BACKLOG 34 E).
- **Timing it with the signal.** That needs the signal to make a fall
  through the short strike LESS likely than the option's price implies, over
  ~21 sessions. The record's evidence is against a directional effect:
  `drift_null_run1` found the long signal's excess hit rate (+4.2 points)
  inside the random-entry null (median +5.2, 95 % [+2.8, +7.2]). No
  mechanism for the timing component has been shown. This document asks
  whether one exists.

## 3. The rules (fixed)

- **Population.** The forward record's taken LONG rows of the pool bucket
  (`HEADLINE_BUCKET`, the population the record's question is asked of),
  read by episode (the episode's first row, as the 349 gate counts), each
  paired with its control row (amendment 2026-10-28 A: same ticker, a bar
  1–20 sessions earlier, drawn by the row's key). Rows on file from
  2026-10-28 (the first controls) are included: no hold reading has been
  computed on any of them. The live bucket is read beside, never decides.
- **Entry.** The fill: the open of the first session after the row's bar
  (the grader's rule). The fill session is session 1.
- **Horizon.** `HOLD_SESSIONS` = 21 sessions (about 30 calendar days, the
  middle of 21–45 DTE): the close of session 21. Secondary, reported and
  never decided on: 15 and 31 sessions (about 21 and 45 days).
- **Volatility.** sigma = the sample sd (ddof 1) of the last
  `VOL_LOOKBACK` = 20 daily log returns ending at the row's own bar: known
  at the bar, no look-ahead. The control has its own sigma at its own bar.
- **The level.** L = fill x exp(−`HOLD_Z` x sigma x sqrt(21)), HOLD_Z = 1:
  one expected move below the fill (about the 16-delta put). Under a
  driftless lognormal, P(close ≥ L) = Phi(1) = 0.841.
- **The margin (primary).** log(close_21 / fill) / (sigma x sqrt(21)): where
  the close lands, in expected moves. **HELD** = margin ≥ −1, the same
  event as close_21 ≥ L.
- **The put spread (H-spread).** Short put at L, long put at
  fill x exp(−`LONG_Z` x sigma x sqrt(21)), LONG_Z = 2; strikes unrounded.
  Priced at entry with Black-Scholes through `spread_backtest._leg`: IV =
  sigma x sqrt(252) x `risk_params.OPT_IV_MULT` (1.15), skew
  `REALISTIC_SKEW` (20), rate `RISK_FREE`, t = 21 x 365/252 days. Entry
  cost: `COST_FRAC_SINGLE` = 1.0 of the 8 % ceiling (single names quote
  wider than SPY; the always-on arms use 0.5), crossed at `CROSS_FRAC` 0.5
  of each leg's price. Held to expiry and settled at intrinsic
  (`spread_log.settle`) on close_21. **Return** = (credit − cost − owed) /
  (width − credit), a fraction of the risk taken. The control is priced the
  same way at its own fill and sigma, so the paired difference measures the
  PATH, not the pricing.
- **Earnings.** Every episode is read; the subset whose 21 sessions hold a
  known release is reported apart.

## 4. The metrics

Per pair, signal minus control: the **margin difference (primary)**, the
held difference, and the spread-return difference. For each, the mean and
its standard error **clustered by the signal's entry week** (CR1, never
narrower than the independent SE): a market drop breaks many holds in the
same week, so pairs entered the same week are not independent. Also the
absolute hold rates of both arms against Phi(1) = 0.841.

## 5. Power (BACKLOG 13, step 2) — computed before the build

`python hold_test.py --power` (seeded; under the NULL the signal bar is
drawn at random; 80 synthetic names, 1,500 sessions, a market factor with
persistent stress spells, daily sd about 1.7 %, the pool's 3.7 episodes a
session, each episode's control 1–20 sessions earlier; three settings for
how much of a name's move is the market's):

```
H-hold / H-spread power under the null (seeded; 21 sessions, z 1 / 2, pairs clustered by entry week)
  market share low : pairs  5272 | hold signal 0.853 control 0.848
     margin diff +0.0120 sd 1.115 DEFF 3.22 | MDE n349 0.30 (= 6.2pp held)  n700 0.21 (= 4.6pp held)  n1000 0.18 (= 3.9pp held)
     hold   diff +0.0046 sd 0.420 DEFF 2.05 | MDE n349 9.0pp  n700 6.4pp  n1000 5.3pp
     spread diff +0.0026 sd 0.298 DEFF 2.06 | MDE n349 6.4% of risk  n700 4.5% of risk  n1000 3.8% of risk
  market share mid : pairs  5272 | hold signal 0.846 control 0.844
     margin diff +0.0119 sd 1.175 DEFF 6.60 | MDE n349 0.45 (= 8.5pp held)  n700 0.32 (= 6.5pp held)  n1000 0.27 (= 5.6pp held)
     hold   diff +0.0021 sd 0.425 DEFF 3.45 | MDE n349 11.8pp  n700 8.4pp  n1000 7.0pp
     spread diff +0.0021 sd 0.311 DEFF 3.54 | MDE n349 8.8% of risk  n700 6.2% of risk  n1000 5.2% of risk
  market share high: pairs  5272 | hold signal 0.840 control 0.836
     margin diff +0.0105 sd 1.244 DEFF 9.63 | MDE n349 0.58 (= 10.1pp held)  n700 0.41 (= 7.9pp held)  n1000 0.34 (= 6.9pp held)
     hold   diff +0.0040 sd 0.441 DEFF 5.07 | MDE n349 14.9pp  n700 10.5pp  n1000 8.8pp
     spread diff -0.0011 sd 0.322 DEFF 5.79 | MDE n349 11.6% of risk  n700 8.2% of risk  n1000 6.9% of risk
```

(MDE = 2.8 x sd x sqrt(DEFF / n): 80 % power, 5 % two-sided; DEFF is the
design effect of clustering by entry week.) The null differences are zero
within noise, so the pairing itself is unbiased.

**Pairs needed** at the pool's 3.7 a session, from the same sd and DEFF:

| effect on the hold rate | market share low | mid | high |
|---|---:|---:|---:|
| +3 points (margin +0.13) | 1,781 pairs, 1.9 years | 4,056, 4.4 years | 6,627, 7.1 years |
| +5 points (margin +0.23) | 575, 0.6 years | 1,309, 1.4 years | 2,139, 2.3 years |

## 6. Why it is vetoed as a decision test

- **The plausible effect is below what the gate can see.** The record found
  no directional effect beyond random entry (section 2). A timing effect on
  holds of more than a few points would be a large discovery on a signal
  that shows none on direction. At the gate the test can see only 6–10
  points; a NOT ESTABLISHED there would say nothing about the 0–3 points
  that matter.
- **More names do not help; only calendar time does.** Holds fail together
  in market drops, so pairs entered the same week share most of their
  outcome: the design effect grows with the number of names a week, and
  the effective sample is roughly the number of distinct market weeks.
  This is the repo's own lesson (BACKLOG 13: "sampling more often buys no
  power"), here on a 21-session horizon.
- **The profitable part does not need the signal.** If selling premium
  over 21–45 days pays, the evidence says it pays always (section 2), and
  the always-on arms already measure that.

This follows momentum (`results/momentum_veto.md`): vetoed without a study,
because the available calendar could not answer.

## 7. The bar, fixed in case the owner runs it anyway

If the owner chooses to build the decision test regardless, this is its
bar, fixed now: one look at 349 settled pool pairs.

- **ADOPT** ("the signal improves holds; a signal-timed put spread is worth
  pre-registering as an alert structure") only if the margin difference is
  > 0 with its week-clustered 95 % CI excluding zero, AND the spread-return
  difference is > 0 (sign agreement).
- **REJECT** ("after a signal the price holds no better, or worse") if the
  margin difference is < 0 with its CI excluding zero.
- Otherwise **NOT ESTABLISHED**, which with the power above is the expected
  output, and is not evidence of no effect.

**Prediction, stated now:** the margin difference within ±0.10 (about ±2
points held) and NOT ESTABLISHED; both arms' hold rates near 0.85.

## 8. What is offered instead (the owner's choice)

1. **A descriptive reading, built cheaply, no decision.** The rows and bars
   are already fetched by the post-close grading pass, so H-hold and
   H-spread can be computed on every settled pool pair and printed beside
   the record's other readings, with the MDE stated next to them. It would
   catch a LARGE effect (≥ 6–10 points) and nothing smaller.
2. **The question that can be answered: does selling 21–45-day premium pay
   at all, signal or not?** Two forward records already measure it: the
   always-on SPY/QQQ put spreads and iron condors (`spread_log`; first
   settlements 2026-11-30, a reading at 12 graded cycles per arm) and the
   single-name IV premium (`iv_snapshot`, `option_iv_preregistration.md`;
   first graded rows due 2026-11-09/10), which says whether single stocks
   carry the premium the index does.
3. **Not offered: a historical test.** Every tranche of clean history is
   spent (`data_reservation`, BACKLOG 15); a backtest of this on the record's
   bars would be one more look at used data and could not confirm anything.

## 9. What it commits anyone to

Nothing is recorded, graded or alerted by this document. Building option 1
is a separate change, on the owner's word. An ADOPT under section 7 would
lead to a separate pre-registration of a put-spread ALERT structure, as
the call structure has (`MEASURED_OPTION_STRUCTURE`).

## Enforcement

`hold_test.py --selftest` (in CI): the level, the margin and its
hold-equivalent, the spread's credit, cost and both ends of its payoff,
and a small seeded power run that is unbiased under the null with a design
effect of at least 1. `consistency_check.check_hold_preregistered`: the
module's constants are the ones written here, this file states the veto
and names the power command, and nothing records a hold.
