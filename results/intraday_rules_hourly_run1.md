# Six intraday rules on HOURLY bars, two years — run 1, read as pre-registered

**Run by the owner 2026-10-10 on a laptop with Yahoo access**, from the
bars frozen in `.bar_cache/` on the first attempt: `python intraday_rules.py
--timeframe 1h`, 20 names, 495 sessions (the 730 calendar days ending
2026-10-09), 5 bps a side, flat at the close, **97.5 % session-clustered
intervals** (the second test of the family), data fingerprint
`v2-eb11301b7b4c55e4`. Raw output: `intraday_rules_hourly_run1.txt`. Rules,
benchmark and bar: `intraday_rules_hourly_preregistration.md`, unchanged.
Read in its order. (Two attempts before this one crashed in the cache's
date parsing across a daylight-saving change; that defect was fixed and
falsified before any report was read — PR #114.)

## The table

| rule | setups | mean R | clustered 97.5 % CI | random null | over null | wins / timeouts | clusters | verdict |
|---|---|---|---|---|---|---|---|---|
| 1 opening-range breakout | 820 | −0.006 | [−0.047, +0.035] | +0.021 | −0.027 | 13 / 779 | 349 | does not clear |
| 2 VWAP reclaim | 100 | −0.184 | [−0.310, −0.059] | +0.098 | −0.282 | 29 / 61 | 65 | does not clear |
| 3 EMA pullback in trend | 1,076 | −0.141 | [−0.205, −0.077] | −0.118 | −0.023 | 54 / 792 | 408 | does not clear |
| 4 RSI divergence | 1,709 | −0.136 | [−0.189, −0.083] | −0.135 | −0.001 | 89 / 1,273 | 456 | does not clear |
| 5 Fibonacci pullback | 1,742 | −0.099 | [−0.134, −0.064] | −0.078 | −0.021 | 592 / 952 | 471 | does not clear |
| 6 trendline break | 854 | −0.150 | [−0.203, −0.097] | −0.063 | −0.087 | 20 / 713 | 387 | does not clear |

**No rule clears.** Five of six have a 97.5 % interval entirely below
zero: measured losses, at the tightened confidence, on 350–470 session
clusters each. The sixth (rule 1) is a measured **zero**: its interval is
±0.04 R around −0.006, which is the detection limit the pre-registration
promised, and it sits below random entry.

## Rule by rule, in the pre-registered order

**Rule 1, opening-range breakout.** 820 setups, 13 target hits, **779
timeouts (95 %)**: with a one-bar range and six bars left in the session,
the 2 R target is almost never reached, so nearly every trade is marked at
the close near its entry. Expectancy is zero to within ±0.04 R. The RVOL
dose-response is absent (−0.009 → −0.034 → +0.024 across terciles, not
monotone, differences inside noise). Random entries with the same geometry
made +0.021; the breakout made −0.006, percentile 0. On hourly bars the
opening-range rule is a coin toss minus costs, and relative volume does not
move it.

**Rule 2, VWAP reclaim.** Fired rarely at hourly (100 setups) and lost
−0.184 R while random entries with the same geometry made +0.098 — worse
than random by 0.28 R, the largest deficit in the table. A reclaim of VWAP
on hourly bars selected the worst bars to buy.

**Rule 3, EMA pullback.** −0.141 R, 792 of 1,076 timed out, slightly worse
than random. Buying a pullback in an hourly trend defined by two rising
pivot pairs carried nothing.

**Rule 4, RSI divergence.** −0.136 R on 1,709 setups against a random null
of −0.135: **identical to random entry** (percentile 55). The timing content
this rule showed at 5 minutes (+0.27 R over random) is gone at one hour.
The daily version measured d ≈ 0; the hourly version measures the same.

**Rule 5, Fibonacci pullback with controls.** The named 50–61.8 % zone made
**−0.163 R; the arbitrary controls made −0.054 and −0.144.** For the second
time, on an independent sample eight times the size, the named ratios were
the worst zone of the three. The tool is refuted on two timeframes.

**Rule 6, trendline break.** −0.150 R, worse than random by 0.087, 713 of
854 timed out. At 5 minutes it had beaten random by 0.11; at one hour it
does not.

## The prediction, scored

Written before the run: no rule clears; intervals tight enough that the
verdict is a measured loss; rules 4 and 6 may again beat random and still
lose; rule 1 times out more often than at 5 minutes; rule 5's named zone
does not separate from its controls.

- **No rule clears, as a measured result:** correct. Five intervals below
  zero at 97.5 %, one a measured zero.
- **Rules 4 and 6 beat random:** wrong on both. Neither did; rule 4 equals
  random exactly and rule 6 is worse. The timing content seen at 5 minutes
  was a property of that window, not of the tools.
- **Rule 1 times out more:** correct, 95 % against 89 %.
- **Rule 5's named zone no better than controls:** correct, again worst.

## What the two timeframes establish together

Across 60 days of 5-minute bars and two years of hourly bars, on 20 liquid
names, net of 5 bps a side, with random entry as the benchmark and the
second test held to a tighter interval: **none of the six objective
versions of trendlines, patterns, Fibonacci, volume or RSI carries an edge,
and most lose with confidence.** The only effect that appeared anywhere —
RSI divergence beating random at 5 minutes — did not survive the change of
timeframe, which is what a chance finding looks like.

Two structural facts carried from the tables are worth more than the
verdicts for anyone still drawn to this: **timeouts dominate every rule**
(60–95 % of trades end at the close near their entry, so the session is
too short for the targets these tools imply), and **the Fibonacci ratios
were the worst retracement zone twice**.

Phase 1 of the day-trading screen is complete on both timeframes and
negative on both. No confirmation window is owed. Phase 2 — the owner's own
chart reading, on paper, 349 episodes, read against the base rate — remains
the only untested thing, and it is the owner's decision with that framing.
