# Six intraday rules on 5-minute bars — run 1, read as pre-registered

**Run by the owner 2026-10-10 on a laptop with Yahoo access.** `python
intraday_rules.py`, 20 names, 60 sessions (the 60 calendar days ending
2026-10-09), 5 bps a side, flat at the close, data fingerprint
`v2-9b37aca34d3c5b84` (frozen in that machine's `.bar_cache/`). Raw output:
`intraday_rules_run1.txt`. Rules, benchmark and bar:
`intraday_rules_preregistration.md`, unchanged. Read in its order.

## The table

| rule | setups | mean R | clustered 95 % CI | random null | over null | wins / timeouts | verdict |
|---|---|---|---|---|---|---|---|
| 1 opening-range breakout | 127 | −0.090 | [−0.184, +0.004] | −0.013 | −0.077 | 0 / 113 | does not clear |
| 2 VWAP reclaim | 646 | −0.159 | [−0.209, −0.108] | −0.111 | −0.048 | 463 / 22 | does not clear |
| 3 EMA pullback in trend | 440 | −0.394 | [−0.483, −0.305] | −0.225 | −0.169 | 82 / 114 | does not clear |
| 4 RSI divergence | 277 | −0.194 | [−0.354, −0.035] | −0.467 | **+0.273** | 84 / 57 | does not clear |
| 5 Fibonacci pullback | 741 | −0.145 | [−0.214, −0.076] | −0.154 | +0.009 | 392 / 104 | does not clear |
| 6 trendline break | 420 | −0.273 | [−0.384, −0.163] | −0.380 | +0.107 | 93 / 121 | does not clear |

**No rule clears.** Five of six have a clustered confidence interval
entirely below zero: they do not merely fail to show an edge, they lose
money net of 5 bps on this window. The sixth (rule 1) touches zero from
below.

## Rule by rule, in the pre-registered order

**Rule 1, opening-range breakout — the one the prediction gave a chance.**
127 setups and **zero** reached the 2 R target; 113 of 127 timed out at the
close. With the stop at the range's far side and the target twice that
distance, the remaining session on these names is not long enough for the
target, so almost every trade is marked at the close near where it entered
(mean −0.09 R, mostly the cost). The dose-response the mechanism predicted
— higher relative volume, better outcome — is **absent and if anything
inverted**: RVOL terciles −0.041 → −0.115 → −0.114. The volume leg carried
nothing here, as it carried nothing on daily bars (`rvol_confirm_run1.md`).
Real result at percentile 0 of the random draws: random entries with the
same geometry did slightly better than the breakout.

**Rule 2, VWAP reclaim.** A 72 % win rate (463 of 646) and a negative
expectancy, because the target (VWAP + 1 ATR) sits close to a reclaim entry
and the stop (the session extreme) sits far: small wins, full losses. The
rule's structure is the mirror of the record's option problem — a high hit
rate on a payoff that cannot carry it. Worse than random entry (percentile 0).

**Rule 3, EMA pullback in a pivot-defined trend.** The worst of the six at
−0.394 R, and worse than random entry by 0.169 R: buying a pullback in a
5-minute "trend" defined by two rising pivot pairs was a systematically bad
entry on this window. Trend-following on 5-minute pivots selected against
itself.

**Rule 4, RSI divergence.** The one place the entry carried information:
−0.194 R on its own, but random entries with the same geometry made −0.467,
so the divergence beat random by +0.273 R, percentile 100, clearing the
third clause by a wide margin. It still fails the first two: the trades lose.
Reading: the divergence picks bars from which price is more likely to run
than a random bar, by about a quarter of an R, and that is not enough to pay
for the geometry and the costs. This is the exact shape of the daily record
(an entry that beats random and still loses), one timeframe down.

**Rule 5, Fibonacci pullback with controls.** The named 50–61.8 % zone made
**−0.198 R; the arbitrary neighbours made −0.104 and −0.162.** The named
ratios were the worst of the three zones. Per the pre-registration this
refutes the tool, not just the signal: whatever a pullback into the middle of
an impulse is worth, the Fibonacci numbers add nothing to it, and on this
window subtracted.

**Rule 6, trendline break.** −0.273 R, beating random by +0.107 (percentile
100) — below the +0.15 margin and negative on its own. A regression-line
break has some timing content and loses money.

## The prediction, scored

Written before the run: rules 3, 4, 5, 6 show nothing distinguishable from
random; rule 2 unclear; rule 1 the one with a chance, its RVOL dose-response
the place to look.

- Rules 3 and 5: **as predicted** (3 worse than random; 5's named zone no
  better than its controls, in fact worse).
- Rules 4 and 6: **partly wrong.** Both beat random entry with the same
  geometry, rule 4 by a clear margin. The prediction said "nothing
  distinguishable"; the data say "a timing effect that does not survive its
  own geometry and costs." The verdict is the same; the mechanism reading is
  not, and it is recorded.
- Rule 2: worse than random, resolved.
- Rule 1: **wrong.** No wins at all, no dose-response, worse than random. The
  mechanism ("funds that must fill") did not show on these 20 large names;
  if it exists it is on names and days this universe does not contain.

## What this establishes, and what it does not

**Established, on this window and these names, net of costs:** none of the
six objective versions of trendlines, patterns, Fibonacci, volume or RSI
beats random entry by the pre-registered margin, and five of six lose with
confidence. The detection limit of the window was about ±0.15 R; a true
edge smaller than that would have been reported as unmeasurable, but every
rule's interval sits below zero, which is not "unmeasured".

**Not established:** anything about other windows (this is one 60-day
regime), other universes (small caps, names in the news), other timeframes,
or discretionary reading. The pre-registration's confirmation-window clause
applies only to a rule that clears; none did, so no second window is owed,
and none will be run to "see if it clears next time" — that is the optional
stopping the clause exists to forbid.

**The screen's phases.** Phase 1 is complete and negative. Phase 2 — a paper
forward test of the owner's own chart reading, logged before entry, graded
mechanically, read at 349 episodes against the base rate — remains the one
honest way to test what Phase 1 cannot, and it is the owner's decision
whether to run it with that framing.
