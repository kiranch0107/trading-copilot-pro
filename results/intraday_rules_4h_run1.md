!! CONFIDENCE INTERVALS in this file were computed with the session-mean standard
!! error; from 2026-10-19 intraday_rules.clustered() reports the cluster-robust SE of
!! the trade-weighted mean it prints (BACKLOG 35). Every verdict here failed the mean-R
!! clause, which the SE does not touch, so no verdict can change; the CI bounds can.
# Six rules on 4-HOUR bars, held across sessions — run 1, read as pre-registered

**Run by the owner 2026-10-13 on the laptop**, from the hourly bars already
frozen in `.bar_cache/`: `python intraday_rules.py --timeframe 4h`, 20
names, 104 trading weeks, 5 bps a side, a hold of at most ten 4-hour bars
across sessions, **99 % week-clustered intervals** (the third test of the
family), data fingerprint `v2-eb11301b7b4c55e4`. **That fingerprint equals
the hourly run's**, which the pre-registration required: the 4-hour bars
were built in code from the same hourly bars and nothing new was fetched.
Raw output: `intraday_rules_4h_run1.txt`. Rules, benchmark and bar:
`intraday_rules_4h_preregistration.md`, unchanged. Read in its order.

This is a swing test of the six tools, not a day-trading test; the
pre-registration says why (a session holds two 4-hour bars).

## The table

| rule | setups | mean R | clustered 99 % CI | random null | over null | wins / timeouts | week clusters | verdict |
|---|---|---|---|---|---|---|---|---|
| 1 opening-range breakout (weekly) | 234 | −0.126 | [−0.385, +0.132] | +0.031 | −0.158 | 33 / 106 | 81 | does not clear |
| 2 VWAP reclaim (week-anchored) | 78 | −0.138 | [−0.394, +0.118] | −0.091 | −0.047 | 50 / 1 | 46 | does not clear |
| 3 EMA pullback in trend | 279 | −0.056 | [−0.338, +0.225] | −0.098 | +0.042 | 89 / 39 | 92 | does not clear |
| 4 RSI divergence | 200 | −0.562 | [−1.348, +0.225] | −0.130 | −0.431 | 53 / 35 | 84 | does not clear |
| 5 Fibonacci pullback | 303 | −0.272 | [−0.441, −0.102] | −0.142 | −0.130 | 152 / 13 | 98 | does not clear |
| 6 trendline break | 649 | −0.167 | [−0.344, +0.011] | +0.003 | −0.170 | 114 / 214 | 103 | does not clear |

**No rule clears.** Every mean is negative, so the decisive clause (mean R
≥ +0.15) fails for all six whatever the interval. Five of six sit at
percentile 0 of the random-entry draws: the rules chose worse bars than a
random bar with the same geometry. One rule (5) has a 99 % interval
entirely below zero, a measured loss; the other five are losses the
window cannot distinguish from zero, which is exactly the power limit the
pre-registration stated (~100 week clusters, detection limit +0.15 to
+0.30 R).

## Rule by rule, in the pre-registered order

**Rule 1, weekly opening-range breakout.** 234 setups, −0.126 R, 45 %
timeouts (down from 95 % at hourly: ten bars is time enough), 14 % wins.
Random entries with the same geometry made +0.031; the breakout made
−0.126, percentile 0. The RVOL terciles read −0.178 → −0.137 → −0.063:
monotone in sign for the first time on any timeframe, but each tercile is
78 trades with a standard error near 0.1 R, so the spread between the
first and third terciles is about one standard error. No dose-response at
any confidence; and the highest-volume tercile still loses.

**Rule 2, week-anchored VWAP reclaim.** 78 setups, 64 % wins and still
−0.138 R: the reclaim's target (VWAP + 1 ATR) is close and its stop (the
deviation's extreme) is far, so wins are small and losses large. Below
random by 0.05. The rule almost never times out (1 of 78) because its
levels are near.

**Rule 3, EMA pullback.** −0.056 R on 279 setups, the only rule above its
random null (+0.042, percentile 80), by a quarter of the required margin.
It is the least bad of the six and still loses money net of costs.

**Rule 4, RSI divergence.** −0.562 R on 200 setups with a 99 % interval
spanning 1.6 R. This is the gap tail the pre-registration warned about:
the stop sits at the divergence's new low, which is the level an
overnight gap goes through, and the engine fills a gapped stop at the
open (the tally shows 155 stops filled at the open across all rules). A
mean of −0.56 R means the average loser lost well over 1 R. Worse than
random by 0.43, percentile 0. Of the six, this is the rule the 4-hour
hold hurt most.

**Rule 5, Fibonacci pullback with controls.** The named 50–61.8 % zone
made **−0.278 R; the controls made −0.148 (30–41.8 %) and −0.704
(70–81.8 %)**. For the third time on a third timeframe the named ratios
were not the best zone; this time they sit between the two arbitrary
ones, worse than the shallow control by 0.13 R. The rule-specific clause
fails. Rule 5 is also the one rule with a measured loss at 99 %: its
interval is [−0.44, −0.10] on 98 week clusters. Half its trades win and
it still loses, which is the signature of a stop placed at the impulse's
origin: far away, and expensive when hit.

**Rule 6, trendline break.** 649 setups, the most of any rule, −0.167 R,
33 % timeouts, 18 % wins. Random entry made +0.003; the break made
−0.167, percentile 0. The interval's upper edge is +0.011, so it is a loss
at 99 % to within a hundredth of an R.

## The prediction, scored

Written before the run: no rule clears; timeouts fall well below the
hourly run's 60–95 %; mean R per rule moves toward the daily record's
value, near zero to mildly negative, with wider intervals; rule 5's named
zone does not separate from its controls; rule 1's RVOL dose-response is
absent; rules 4 and 6 are not above random by the margin.

- **No rule clears:** correct, all six on the mean alone.
- **Timeouts fall:** correct, 1–45 % against 60–95 %.
- **Means near zero, intervals wider:** half right. Intervals are wider,
  as power predicted. The means are NOT nearer zero: four of six are more
  negative than at hourly (rule 4 at −0.56, rule 5 at −0.27). Holding
  across sessions exposed the stops to overnight gaps, and the gapped
  fill rule (BACKLOG 21) priced them.
- **Rule 5's named zone no better than controls:** correct. Not the best
  zone for the third time; the clause fails by 0.13 R against the shallow
  control.
- **Rule 1's dose-response absent:** right on substance, wrong on the
  detail. The terciles happen to be ordered in sign, which did not happen
  on the other two timeframes, but the ordering is one standard error
  wide and every tercile loses.
- **Rules 4 and 6 not above random:** correct, both well below it.

## Two things the run says that the verdicts do not

**Crowding.** The one-position-per-name rule skipped 1,743 qualifying
setups while a position was open, about as many as were taken. With a
ten-bar hold, the first rule to fire in a week owns the name for most of
it. The setup counts above are after that filter; a reader comparing them
with the hourly counts should remember the filter, not the signal, is
what shrank them. This was the pre-registered design and it is not
revisited here.

**Gaps.** Across all rules, 155 stops and 100 targets were filled at the
open after a gap, and 50 setups were voided because the stop was gapped
before the fill. A day trade never sees these; a swing trade on 4-hour
bars sees them every night. The rule most exposed (4, stop at the new
low) lost the most, and the one least exposed (2, near levels, almost no
timeouts) lost the least in absolute R. That is a statement about stop
placement under gaps, not about the tools.

## What the three timeframes establish together

Across 60 days of 5-minute bars, two years of hourly bars, and two years
of 4-hour bars held across sessions, on 20 liquid names, net of 5 bps a
side, with random entry as the benchmark and the second and third tests
held to tighter intervals: **none of the six objective versions of
trendlines, patterns, Fibonacci, volume or RSI carries an edge, and on
every timeframe most lose with confidence.** The named Fibonacci zone was
never the best of three zones. The one effect that appeared anywhere, RSI
divergence beating random at 5 minutes, reversed to the worst deficit in
any table once the hold crossed sessions.

Phase 1 of the day-trading screen is complete on three timeframes and
negative on all three. No confirmation window is owed. Phase 2, the
owner's own chart reading on paper over 349 episodes read against the
base rate, remains the only untested thing, and it is the owner's
decision with that framing.
