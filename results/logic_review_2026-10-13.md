# Logic review, 2026-10-13: "most trades fail, others make money", and where the gap is

**Owner's question, 2026-10-13:** most trades fail or never pass the setup;
the market looks different; others are making money; is there a gap in the
logic or in the understanding? **Written as an assessment, from the repo's
own record first and the outside evidence second. Nothing here is a new
result; every number is already committed.**

## 1. What the record says, in one table

| measurement | where | what it found |
|---|---|---|
| the long signal, 10 years, 20 names | `drift_null_run1.txt` | +0.105 R mean, 35.7 % hit; random entries with the same exits: +5.2 pp excess, 95 % band [+2.8, +7.2]; the real signal's +4.2 pp sits inside it. **The signal adds nothing; the positive mean is the market's drift through a 1:3 geometry** |
| the short signal | same | −0.341 R, worse than every one of 200 random draws |
| the inverted long (buy the bearish setup) in a Bull regime | `inverted_arm_run2.txt` | +0.398 R on 423 trades, +7.9 pp excess against a null band [+0.9, +8.2]: the strongest number in the record and still inside its null, empirical p ≈ 0.1–0.3 |
| the features behind the HQ tier | `setup_population_run2.txt` | ADX d = −0.01, RSI d = +0.04, ATR % d = −0.07, volume d ≈ 0: none informative on 1,457 setups |
| the stop width | `atr_stop_run1.md` | win rate minus breakeven is −0.6 to −2.2 points at every width: "the stop is not mis-calibrated; there is nothing for it to be mis-calibrated against" |
| exits (trailing, breakeven, partial), the EMA20 thesis break | `exit_ab_run1.txt`, `thesis_exit_run1.md` | zero |
| capital-constrained replay vs the index | `longs_only_run1.md` | "there is no version of this signal that beats owning the index" |
| the forward record, live config, since 2026-09-16 | `forward_log.jsonl` | 46 signals, **0 high-quality**; 16 settled on stop distance: **13 stopped at −1.0 R, 3 wins of +0.5 to +0.8 R**; six of the losses are one TMO episode on consecutive bars |
| the pool, first scan, 75 names | same | 19 base signals on one bar, 0 high-quality; ADX failed 18 of 19 |

Everything the owner is seeing is in that table. Trades fail because the
signal has no edge and the geometry pays the noise. Setups never pass
because the gate is closed by construction. Both are measured, not
suspected.

## 2. The arithmetic the record is reporting

The live geometry is a 1.25 ATR stop (tighter when the 10-bar swing low is
closer), a 4 ATR target, a 20-bar hold. ATR is the daily range; a large
cap's close-to-close move is about 0.6–0.9 ATR. Under a **driftless**
random walk with those exits:

| daily sd (ATR) | stop first | target first | timeout | mean R |
|---|---|---|---|---|
| 0.60 | 68 % | 13 % | 19 % | −0.06 |
| 0.75 | 72 % | 18 % | 10 % | −0.03 |
| 0.90 | 73 % | 22 % | 5 % | +0.02 |

Breakeven at 3.2 R is a 23.8 % win rate. The forward record's 3 of 16
(19 %) and the 10-year record's "just under fair odds at every width" are
what noise produces. **A stop one and a half days of normal movement away,
held for twenty days, is hit about seven times in ten whatever the entry
was.** Widening it moves along an indifference curve (the ATR test said
exactly this) because there is no drift to capture, only the market's.

## 3. Questioning the logic, in five places

**(a) It is a timing system in a market that pays for holding.** Every
documented equity premium is a holding premium: the index itself; momentum
at 3–12 month formation and 1–12 month holds; the volatility risk premium,
earned by selling; value and quality over years. None is a daily-bar entry
with a 20-day exit. The outside evidence on technical entry rules is the
same as ours: Sullivan, Timmermann and White's reality-check test of
7,846 rules found the in-sample winners insignificant out of sample on the
Dow (best rule 8.6 % a year before costs, not significant), and later
re-examinations find survivors only in young or illiquid markets, not the
S&P 500. Our `drift_null` is that test on our own rules, with the same
answer.

**(b) The entries sit on the wrong side of the one short-horizon effect
that is real.** The base signal buys after the move (price above EMA20
above EMA50, MACD above its signal) and sells the mirror. At 1–4 week
horizons single stocks show *reversal*, not continuation (Jegadeesh,
Lehmann; de Groot, Huij and Zhou show it survives costs in large caps when
turnover is controlled; Medhat and Schmeling find it concentrated in
low-turnover names and short-term momentum in the highest-turnover ones).
Our inverted arm, buying the bearish setup in a Bull regime, is the only
number in the record that leans positive (+0.40 R, 423 trades) and it did
not clear its null. The verifiability screen marked reversal "wrong side:
you pay the spread it earns", which is true for a weekly long-short
portfolio and less obviously true for a long-only 20-day hold in large
caps. That is the one place the record's own reasoning deserves a second
look; it is a hypothesis, not a finding, and **every reserved tranche is
spent** (A on the RVOL confirmation, B by live trading, C on the break-even
exit): there is no clean historical data left to test it on. Only forward
data can.

**(c) "Others are making money" is a selection, not a benchmark.** Since
the record began the SPY regime has read Bull. In a rising market a long
position with leverage, which is what a call option is, looks like skill
for as long as the market rises. The people who post are the ones who
won; the base rates are the ones that count: under 1 % of day traders
profit persistently (Taiwan, every trader 1992–2006), 97 % of persistent
Brazilian day traders lose, and retail options traders lose money on
average while paying an average 12.6 % bid-ask spread on the weekly
contracts they prefer (Bryzgalova, Pavlova and Sikorskaya, Journal of
Finance 2023). The relevant comparison for this account is in
`longs_only_run1.md`: the index beat every version of the signal on both
return and drawdown. "The market looks different" is what a bull market
looks like from inside a strategy that is flat.

**(d) The gate is tuned to a regime that does not occur.** ADX ≥ 35 on a
daily bar is a strongly trending name; large caps in a grinding bull sit
at 15–25. Of 46 recorded signals none passed the tier, and ADX failed
every tagged row. Worse, the setup-population table's best ADX bucket by
mean R was **25–28** (+0.217, a CI that touches zero) and the ≥ 35 bucket was middling: the gate
selects against the one bucket that looked best, which BACKLOG 24 called
"dose-response runs backwards". So "never passes our setup" is not the
market refusing to cooperate. It is a filter with no predictive content
set at a level that is almost never reached, which is the worst of both:
it suppresses alerts without improving them.

**(e) What the record has not tested, honestly listed.** (i) The universe's
RS top-eight held as a basket, rebalanced with the snapshot, against SPY:
the ranker is used only to choose names for a timing signal, never
measured as the holding strategy it resembles (survivorship caveat in
BACKLOG 8). (ii) A pullback entry in an uptrend (buy weakness in names
above the 200-day average): the inverted arm's lead, testable only as a
second signal in the forward record, since no clean tranche remains.
(iii) Selling premium: the VRP is real here (+3.63
vol points, 11 of 11 years) and every defined-risk arm failed its bar
because the harvestable version needs hedging this account cannot run.
(iv) Longer holds with wider stops: the arithmetic in §2 says this only
helps with a drift source behind it, which is (i), not a new indicator.

## 4. The gap, named

There is no bug and no missing indicator. The gap is a **category error**:
the system looks for an edge in short-horizon direction, which the
record and the literature say is not there for daily-bar technical
rules on large caps; it does so with a geometry that converts noise into
losses at about seven stops in ten; and it gates alerts on a condition
that does not occur. The market is not different. The sample of "others"
is. What the owner is watching is a flat strategy inside a rising market,
which is the one configuration that feels most like being wrong.

## 5. What follows, as decisions for the owner

1. **Stop reading alerts as the system's output.** Its output is the
   forward record, the pool bucket, the IV snapshots and the Phase 2
   paper record, all pre-registered and all running. They will say what
   they say in months, not days.
2. **If one more hypothesis is to be tested, make it the one with a
   mechanism and a lead in our own data:** a pullback entry in an uptrend,
   long only, recorded as a second signal in the forward record (its own
   bucket, no alerts, graded by the same resolver), pre-registered with
   `drift_null`'s bar and the power check first. It would take as long as
   the base record takes, because no clean historical data remains.
   Prediction, stated now: it beats random entry by a margin inside costs
   and does not beat the index.
3. **Measure the RS basket as a holding strategy against SPY** over the
   ten years the snapshots can be rebuilt for. The verifiability screen
   says ten years cannot prove alpha; it can show whether the basket
   loses to the index, which is the question that matters for a $5,000
   account.
4. **The honest alternative is already written:** `unverified_literature_bet.md`
   frames holding a broad fund and stopping the timing. Nothing in this
   review argues against it.

Sources consulted outside the repo: Sullivan, Timmermann and White (1999),
"Data-snooping, technical trading rule performance, and the bootstrap",
Journal of Finance, and its re-examinations with White's Reality Check and
Hansen's SPA test; de Groot, Huij and Zhou, "Another look at trading costs
and short-term reversal profits"; Medhat and Schmeling (2022) on turnover
and short-term momentum; Nagel on reversal as liquidity provision;
Bryzgalova, Pavlova and Sikorskaya (2023), "Retail trading in options and
the rise of the big three wholesalers", Journal of Finance 78(6).
