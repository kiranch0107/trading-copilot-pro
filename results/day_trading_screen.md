# Day trading by chart reading — the screen, before a line of code

**Written 2026-10-09 at the owner's request ("explore this idea to the
fullest"). Read with BACKLOG 13 (power before build) and 14 (the
verifiability screen) in hand: this document is those two rules applied to
intraday discretionary trading. Nothing below has been run.**

## What the owner proposed

Day trade by studying the chart: trendlines, patterns, Fibonacci levels,
volume, RSI. Explore it fully. Reuse what the project has.

## The base rates, stated first

**The activity.** Two population studies, each on every day trader in a
market rather than a sample. Barber, Lee, Liu and Odean (Taiwan, 1992–2006):
fewer than 1 % of day traders earn positive abnormal returns net of fees in a
way that persists. Chague, De-Losso and Giovannetti (Brazil, 2013–2015, "Day
Trading for a Living?"): of those who persisted past 300 sessions, 97 % lost
money; about 1 % earned more than the minimum wage; skill did not accumulate
with experience in either study. These are the numbers any result here has to
beat, and they answer the mechanism question too: intraday, the counterparty
is market-making and automated flow with lower costs and faster information.
Retail intraday order flow is the liquidity it harvests.

**The tools, one by one.**

| tool | best evidence | verdict for this screen |
|---|---|---|
| RSI, volume, ADX-type indicators | This repo: 1,457 daily setups, power to detect d = 0.15, measured d = +0.04 / +0.02 / −0.01 (`setup_population_run2.txt`); random entries matched the signal (`drift_null_run1.txt`) | strong negative prior; intraday is a different sample, not a different mechanism |
| chart patterns | Lo, Mamaysky & Wang (2000): objectively defined patterns carry some distributional information; profitability after costs not shown; later stock replications weak | unverified for profit |
| Fibonacci retracements | no controlled evidence of predictive content beyond arbitrary fixed fractions; no mechanism in price formation | folklore (see `unverified_literature_bet.md` for the honest way to hold one) |
| trendlines | subjective until defined by rule (pivots + regression); once defined they are a breakout/momentum rule and inherit that literature | testable only as a rule |
| intraday effects with a stated mechanism | first-half-hour return predicting the last half-hour on index ETFs (Gao, Han, Li & Zhou 2018); VWAP mean reversion from institutional execution; opening-range breakout on high relative-volume names (working-paper evidence only) | the only candidates with a mechanism; none is chart reading |

**The constraints on THIS account.** `risk_params.DEFAULT_ACCOUNT_SIZE` is
$5,000 at 1 % risk: $50 of risk per trade. Under FINRA's pattern-day-trader
rule a margin account below $25,000 is limited to three day trades in any
five sessions, options included. A cash account escapes the rule but trades
settled cash only (T+1), so roughly half the account per day. A two-cent
spread on 100 shares plus slippage is a double-digit percentage of $50 of
risk before any edge is measured. The time cost is the full session, every
session.

## Power — the part that is not opinion

Per-trade R on tight intraday setups has a standard deviation near 1, as on
daily bars. From `power_check.py --sd 1.0`:

| edge sought (R per trade) | independent trades needed |
|---|---|
| +0.15 | 349 |
| +0.10 | 785 |

At two or three trades a session that is **four to twelve months** of
forward trades before a reading, and the between-trade correlation of
trades taken the same hour on correlated names discounts it further
(`--corr`). The 30-trade journal run was never going to answer this and must
not be read as if it could.

**Costs are inside the bar.** An edge is measured net of spread and slippage
charged per trade, or it is not an edge. At this account size that cost is
likely the whole of any effect found.

## The approach, if it is pursued — fixed order, each gate before the next

**Phase 0, this document.** Mechanism stated per rule, power stated,
benchmark stated: random entries on the same instruments at the same times
with the same exits (the `drift_null` method). Every rule must beat THAT,
not zero.

**Phase 1 — codify, then test on 60 days of 5-minute bars (weeks, laptop).**
Everything on the list becomes a rule once defined, and the engine does not
care what a bar is: `backtest.simulate_trade()` takes any OHLC frame, and
`download()` already carries an `interval` argument (an intraday fetch needs
a period in days, not years — a small change; Yahoo serves 60 days of 5-minute
and two years of hourly bars). The rules to pre-register, each with its
mechanism:

1. **Opening-range breakout**, first 15 or 30 minutes, on names in the top
   decile of relative volume at the open; stop at the range's other side,
   target at 2 R, flat by the close. Mechanism: funds that must fill.
2. **VWAP reclaim / reject**: entry on a close back through VWAP after a
   deviation of ≥ 1 intraday ATR; stop beyond the extreme; target VWAP ± 1
   ATR. Mechanism: execution algorithms anchored to VWAP.
3. **Pullback to the 9/20 EMA in a trend defined by rule** (higher highs and
   lows on a 15-minute pivot set); stop under the pivot; target 2 R.
4. **RSI divergence** (price lower low, RSI higher low over ≥ 10 bars) — the
   indicator claim, tested so it can be refuted the way the daily version was.
5. **Fibonacci 50 / 61.8 % pullback of the last impulse** (impulse defined by
   pivots), against **a control of 45 / 55 / 70 %**: the test is whether the
   named ratios differ from arbitrary neighbours. If they do not, the tool is
   refuted as a tool, not just as a signal.
6. **Trendline break**, with the line defined as the regression through the
   last three pivot lows, entry on a close below it.

For each: setup population, dose-response on its feature (`setup_population`
and `conditional_test` machinery), random-entry benchmark (`drift_null`),
MFE / MAE taxonomy (`outcome_taxonomy`), the gapped-level fill rule, 5 bps of
cost per side charged. Pre-registered bar per rule: expectancy ≥ +0.15 R net
of costs with the HAC-corrected CI clear of zero AND the random-entry
benchmark beaten by the same margin. Prediction, written now so it can be
scored: rules 4, 5 and 6 show nothing, as their daily cousins did; rule 1 is
the one with a real chance of a measurable effect, and costs at this account
size are likely to consume it.

**Phase 2 — the discretionary forward test, on paper, for what cannot be
codified.** If the real question is the owner's own chart reading, the only
honest instrument is a forward record, and the repo now has one. Rules: log
BEFORE entry — setup name from the Phase 1 list, planned stop and target,
screenshot; `mode = paper`, `source = discretionary`, a new `strategy =
daytrade` tag so these never blend into the swing record (the paper/live
lesson, one level down); grade mechanically on the stop-distance basis so
the result does not depend on how the exit was managed; apply the dust
floor; read nothing before 349 episodes; compare against random entries at
the same times. Framing, stated now: this is a test of whether the owner is
in the top percent of the Brazilian distribution, and it is run with that
framing, on paper, for the full sample.

**Phase 3 — live, only on a cleared bar.** `calc_position_size()`, 1 % risk,
inside the settled-cash and day-trade limits. Nothing goes live on a Phase 2
result whose interval spans zero.

## What the repo gives this, and what it does not

**Reusable unchanged:** the pre-registration convention and its enforcement;
`power_check.py` with the correlation discount; `drift_null`'s random-entry
benchmark; `outcome_taxonomy` (MFE / MAE); the gapped-level fill rule in both
engines; `journal_store`'s dust floor and paper/live split; the forward-log
grader; `bar_cache` for reproducible samples; the market calendar;
`signal_core.compute()` on intraday frames; Telegram; the consistency-check
discipline.

**Needs small work:** an intraday fetch path (period in days); a `strategy`
tag on journal rows and positions; intraday VWAP and relative-volume columns
in `compute()`.

**Not reusable:** the daily signal itself; the option engine; the scanner's
schedule; the exit monitor (30-minute polling cannot watch an intraday stop
— a live intraday position needs a broker-side stop, not this repo).

## The decision this document asks for

Phase 0 is this file. Phase 1 costs a week and a few laptop runs and answers
whether any objective version of these tools carries anything on these
instruments at this account size. It should run before anything
discretionary. If it says no — which is what the evidence predicts — a Phase
2 forward test of pure chart reading is still a legitimate thing to run,
provided it is run as described: on paper, pre-registered, for the full
sample, and read against the base rate it is trying to beat.
