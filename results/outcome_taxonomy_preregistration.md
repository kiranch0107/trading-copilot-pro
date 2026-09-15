# Pre-registration — what happened, against what we predicted

Written and committed **before** `outcome_taxonomy.py` exists.

## The question, and why it is not the one already answered

Every test so far asked: **do the setup readings predict the outcome?** The
answer is no, comprehensively — RSI d = +0.04, ADX d = −0.01, volume d = +0.02
at n = 1,457 with power to detect d = 0.153, and random entries on the same
charts did as well or better.

This asks a different question and the null above does not touch it:

> For each historical setup, we PREDICTED price would reach the target before
> the stop. What actually happened — not the verdict, the **path** — and how do
> those paths group?

A trade that stopped out after travelling 2.8 R our way is a **management**
failure. A trade that stopped out having never gone 0.3 R our way is a
**signal** failure. Both book −1 R and are indistinguishable in every number
this project has produced so far. They need opposite fixes.

## What gets measured

For every trade, from the entry bar to the exit bar inclusive, in units of that
trade's own risk (`|fill − stop|`, the same denominator R uses):

- **MFE** — maximum favourable excursion. How far our way did it ever go?
- **MAE** — maximum adverse excursion. How far against?
- **bars to MFE** — when the best moment arrived.

Intrabar High/Low, never closes: a trade that touched the target intrabar and
closed below it did reach the target, and pretending otherwise would hide
exactly the cases this is built to find.

## The buckets, fixed now, evaluated in this order

Thresholds are in R and are chosen from the strategy's own geometry (a stop at
−1 R, a target at +rr), not from the data, which has not been looked at.

| # | bucket | rule |
|---|---|---|
| 1 | **CLEAN TARGET** | `win` and `MAE > −0.5` — reached target never seriously threatened |
| 2 | **SCARE TARGET** | `win` and `MAE ≤ −0.5` — got there after going deep against first |
| 3 | **DEAD WRONG** | `loss` and `MFE < 0.5` — stopped having barely moved our way |
| 4 | **NEAR MISS** | `loss` and `MFE ≥ 0.5 × rr` — stopped after getting at least halfway to target |
| 5 | **SHALLOW LOSS** | `loss`, the remainder |
| 6 | **STALLED RUNNER** | `timeout` and `MFE ≥ 1.0` — moved our way, ran out of time |
| 7 | **CHOP** | `timeout`, the remainder — went nowhere |
| 8 | **OTHER** | anything unclassified; must be reported and should be empty |

Order matters and is fixed here: 3 is tested before 4, so a trade with
`rr < 1` cannot fall in both.

## What each bucket would imply, written before the counts exist

So the numbers cannot be reinterpreted to suit whatever comes back:

- **NEAR MISS heavy** → the exit is the problem, not the entry. A trailing
  stop, a partial at 1.5 R, or a break-even move would convert these. This is
  the only bucket that points at a fix requiring no predictive power at all.
- **DEAD WRONG heavy** → the entry is the problem. Consistent with everything
  already measured, and not fixable by exit management.
- **SCARE TARGET heavy** → the stop is too tight relative to the noise the
  trade must survive; widening it costs R per trade but may raise the hit rate.
- **STALLED RUNNER heavy** → `max_hold` is cutting trades that were working.
- **CHOP heavy** → the signal fires in conditions where nothing happens either
  way, and a volatility or participation floor might skip them.

## What this cannot do, stated now

**It cannot establish an edge.** It is a description of 2,360 in-sample trades
on twenty spent tickers. A bucket being large says what happened; it does not
say a change would have helped, because every "fix" above is a new rule that
would need its own out-of-sample test.

In particular: counting NEAR MISS trades and multiplying by a hypothetical
trailing-stop payoff is **backtesting on the answer sheet**. If that bucket is
large, the honest next step is a pre-registered A/B of a specific exit rule
across the whole population, not an arithmetic estimate of the prize.

## Predictions

- **DEAD WRONG is the largest loss bucket.** Everything measured says the
  entry carries no information, so most losses should be trades that simply
  never went anywhere. I expect 45–65% of losses.
- **NEAR MISS is under 20% of losses.** With a 3 R target, reaching 1.5 R and
  then retracing a full 2.5 R to the stop is a big round trip.
- **CLEAN TARGET outnumbers SCARE TARGET**, because a 1 ATR stop is tight and a
  trade that goes −0.5 R against usually continues to −1.
- **CHOP is small**, under 10% of all trades, since timeouts were only 3.2% of
  the population.

If NEAR MISS comes back large, that is the most actionable result this project
has produced, and it would be the first thing worth an out-of-sample test.
