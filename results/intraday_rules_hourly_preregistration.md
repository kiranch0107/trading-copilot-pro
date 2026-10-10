# PRE-REGISTRATION — the six intraday rules on HOURLY bars, two years (day-trading screen, Phase 1b)

**Written 2026-10-10 at the owner's request ("go ahead with hourly"), and
committed BEFORE any hourly bar is fetched.** Everything below is fixed. It
may be AMENDED in a dated section at the top only until the first run is
committed. Parent: `intraday_rules_preregistration.md` (the 5-minute test,
run 2026-10-10, no rule cleared — that verdict stands and is NOT reopened
by this document).

## Why a second timeframe is a different hypothesis, and what it costs

The 5-minute rules were defined in bars, and at 5 minutes most of their
structure (pivots, impulses, trendlines) lived inside noise. At one hour
the same tools describe the shapes a chart reader actually draws, and the
provider serves two years of hourly bars against sixty days of 5-minute,
so the sample is roughly eight times the sessions and several regimes.
That makes "do these tools work on hourly bars" a distinct question with
far more power, not a re-roll of the 5-minute one.

**The cost is multiplicity, paid up front.** This is the second test of
the same family of tools. A rule clears here only at the **97.5 %**
clustered interval (z = 2.24), not 95 %, so that testing the family twice
buys no false positive. A 30-minute run is NOT part of this document: on
sixty days it would have a detection limit near 0.3 R and could only
return "unmeasurable".

## What is held fixed

| item | value |
|---|---|
| bars | Yahoo, 1-hour, regular hours only, `auto_adjust=False`, **730 calendar days** (the provider's `2y`) ending the run date, frozen in `.bar_cache/` under its own key. Sessions have 7 bars starting 09:30; the last (15:30) is a half-hour bar and is treated like any other |
| universe | the same 20 symbols as the 5-minute test |
| sessions | one trade per rule per name per session; one position per name at a time; **flat at the session's last bar** |
| entry / exit / costs / fills | identical to the 5-minute test: next-bar open + 5 bps, stop first, target, else the close; gapped levels fill at the open; 5 bps a side |
| indicators | EMA9, EMA20, RSI14, ATR14 on the hourly close; session VWAP; **pivots over ±1 bar** (strictly above the left bar, at least equal to the right, ties to the earlier bar); **opening range = the first bar** (09:30–10:30) |
| structure look-back | pivots, impulses and trendlines may use **the current and the previous session** (up to 14 bars). Entries and exits stay inside the current session. A chart reader at hourly bars reads yesterday's structure; a 5-minute reader does not |
| RVOL | first-bar volume over the median first-bar volume of the prior 20 sessions |

## The six rules, restated in hourly clock time

1. **Opening-range breakout.** After bar 1, a close above the first bar's
   high (long) or below its low (short), RVOL ≥ 1.5. Stop at the range's
   other side; target 2 R. Dose-response by RVOL tercile.
2. **VWAP reclaim.** As before: a close ≥ 1 ATR beyond VWAP then a close
   back through it; stop at the extreme since the deviation; target VWAP ±
   1 ATR.
3. **EMA pullback in a pivot-defined trend.** Trend from the last two pivot
   highs and lows (±1-bar pivots, two-session look-back); a touch of EMA20
   with the close above EMA9 (long). Stop at the last pivot low; target 2 R.
4. **RSI divergence.** Pivot lows **≥ 3 bars apart** (three hours), price
   lower low, RSI14 higher low → long on the next close. Stop at the new
   low; target 2 R. Mirrored.
5. **Fibonacci pullback with controls.** Same three equal-width zones
   (named 50.0–61.8 %, controls 30.0–41.8 % and 70.0–81.8 %); impulse from
   ±1-bar pivots over the two-session look-back.
6. **Trendline break.** Regression through the last three pivot lows over
   the two-session look-back; a close below it → short. Mirrored.

## The benchmark and the bar

Benchmark unchanged: twenty fixed-seed random-entry draws, same side, same
stop and target distances, same engine, flat at the close. Random bars are
drawn from bar 1 to the second-from-last of the session.

A rule **clears** only if, net of costs: mean R ≥ +0.15; the **97.5 %**
session-clustered interval lies above zero; mean R exceeds the random null
by ≥ +0.15 R; and for rule 5 the named zone beats both controls by ≥ +0.15 R.

## Power

About 500 sessions, so 500 clusters. Even a rule that fires on a fifth of
name-days has ~2,000 setups; the clustered SE is on the order of 0.04–0.06
R, so the window can see an edge of **+0.10 to +0.15 R** at the tightened
confidence. A "no" from this window is a no, not an "unmeasured". A rule
that clears gets ONE pre-stated confirmation: the following 180 days of
hourly bars, fetched later, same code, same bar.

## Prediction, so it can be scored

Hourly bars reduce the noise, not the mechanism problem. Rules 4 and 6 may
again beat random entry and again lose money; rule 1 with six bars left
to reach a 2 R target will time out at the close more often than at 5
minutes, not less; rule 5's named zone will not separate from its
controls. **No rule clears**, and the intervals are tight enough that the
verdict is a measured loss, not an absence of evidence.

## Enforcement

`intraday_rules.py --timeframe 1h` applies the hourly profile; its selftest
drives the opening-range and divergence detectors on 7-bar synthetic
sessions and proves the two-session structure look-back, the flat-at-close
exit inside 7 bars, and the 97.5 % interval.
`check_intraday_rules_match_preregistration()` holds BOTH profiles to
their documents' numbers, so the 5-minute profile cannot drift while the
hourly one is added.
