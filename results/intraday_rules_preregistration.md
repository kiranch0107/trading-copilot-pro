# PRE-REGISTRATION — six intraday rules on 60 days of 5-minute bars (day-trading screen, Phase 1)

**Written 2026-10-09 at the owner's decision ("go ahead with Phase 1"), and
committed BEFORE any intraday bar is fetched.** Everything below is fixed.
It may be AMENDED in a dated section at the top only until the first run is
committed. The parent screen is `results/day_trading_screen.md` (BACKLOG 29);
the code is `intraday_rules.py`, written from this document.

## The question

Does any objective version of the tools the owner proposed — trendlines,
patterns, Fibonacci levels, volume, RSI — carry an edge intraday on liquid
US names, net of costs, that random entries with the same exits do not?

## What is held fixed

| item | value | why |
|---|---|---|
| bars | Yahoo, 5-minute, regular hours only, `auto_adjust=False`, 60 calendar days ending the run date, frozen in `.bar_cache/` under its own key | the provider's maximum intraday window; raw matches every other engine here |
| universe | the 7 sweep names (SPY among them), the 12 OOS names, and QQQ — 20 symbols | liquid, already in the record, no selection on intraday behaviour |
| sessions | one trade per rule per name per session at most; a second setup while a position is open is skipped | one position per name, as the live book |
| entry | the OPEN of the bar after the signal bar, + 5 bps | the record's convention, intraday cost |
| exit | stop (checked first), target, or the session's last bar at its close — **flat by the close, always** | day trading means no overnight exposure |
| fills | gapped levels fill at the open (the engine's rule since 2026-10-08) | intraday gaps are small; the rule costs nothing to keep |
| costs | 5 bps per side slippage, 0 commission | a 2-cent spread on a $100 stock is 2 bps; 5 is the round number above it |
| indicators | EMA9, EMA20, RSI14, ATR14 on the 5-minute close; session VWAP; pivots as local extremes over ±3 bars (strictly above the left three, at least equal to the right three, so a tie flags the earlier bar once); opening range = the first 6 bars (09:30–10:00) | one set, defined here, shared by every rule |
| RVOL | the session's first-30-minute volume over the median first-30-minute volume of the prior 20 sessions | relative volume at the open, the one volume measure with a mechanism |

## The six rules, as code will define them

1. **Opening-range breakout.** After bar 6, a close above the opening-range
   high (long) or below its low (short), with RVOL ≥ 1.5. Stop at the range's
   other side; target at 2 R. Dose-response: RVOL terciles among setups.
2. **VWAP reclaim.** Price has closed ≥ 1 ATR below VWAP (long) or above
   (short) earlier in the session, then closes back through VWAP. Stop at the
   session extreme since the deviation; target at VWAP ± 1 ATR.
3. **EMA pullback in a pivot-defined trend.** Uptrend: the last two pivot
   highs rise and the last two pivot lows rise. A bar's low touches EMA20
   while the close is above EMA9 → long. Stop at the last pivot low; target
   at 2 R. Mirrored.
4. **RSI divergence.** A pivot low below the previous pivot low (≥ 10 bars
   apart) with RSI14 at the new low above RSI14 at the old → long on the
   next close. Stop at the new low; target at 2 R. Mirrored.
5. **Fibonacci pullback, with controls.** Impulse = last pivot low → pivot
   high (bull). A close inside a retracement zone followed by a close above
   the prior close → long. Stop at the impulse low; target at the impulse
   high. THREE zones of equal width (11.8 points): **named 50.0–61.8 %**,
   control-low 30.0–41.8 %, control-high 70.0–81.8 %. The test is the named
   zone against the controls. Mirrored.
6. **Trendline break.** Regression through the last three pivot lows
   (uptrend); a close below the line → short. Stop at the last pivot high;
   target at 2 R. Mirrored on pivot highs.

A rule fires at most once per name per session. Every setup records its
rule, side, entry, stop, target, planned R:R and the rule's own feature
value.

## The benchmark, fixed

For every (rule, name, session) that produced k setups, k **random entry
bars** are drawn uniformly over the session (bar 6 to the fourth-from-last),
each with the SAME side and the SAME stop distance and target distance as
the real setup it replaces, run through the same engine, costs and flat-at-
close exit. Twenty draws, seeds fixed in code (20260 + draw). The null is the
mean across draws; the real result is reported with its percentile among
the draws.

## The bar, per rule, fixed

A rule **clears** only if all three hold, net of costs:

1. mean R ≥ **+0.15**;
2. the **session-clustered** 95 % CI of the mean (one cluster per session,
   because trades on the same day across names are correlated) lies above
   zero;
3. mean R exceeds the random-entry null by ≥ **+0.15 R**.

For rule 5 additionally: the named zone's mean R exceeds BOTH control zones'
by ≥ +0.15 R, else the ratios carry nothing beyond "a pullback".

For rule 1 additionally reported: mean R by RVOL tercile. Monotone and
rising is the dose-response the mechanism predicts; flat is a refutation of
the volume leg.

## Power, said before the run

Per-trade R has sd ≈ 1. Sixty sessions is sixty clusters. If a rule fires on
a third of name-days it has ~400 setups across ~60 clusters; the clustered
SE is roughly sd(session means)/√60 ≈ 0.06–0.08 R, so the smallest edge this
window can see at the bar's confidence is about **+0.15 R** — the bar is set
AT the detection limit, deliberately: anything smaller is unmeasurable here
and will be reported as such, not as absent. A rule that clears gets ONE
pre-stated confirmation window: the NEXT 60 days, fetched later, same code,
same bar. Not "run again until it clears".

## What this does not measure, said now

- Discretionary chart reading. This is Phase 1; the owner's own reading is
  Phase 2 and has its own instrument.
- Anything with a mechanism outside these six. If none clears, that is the
  verdict on these tools as rules, not on day trading as such.
- Live fills. 5 bps is a model; a cleared rule's first live trades are
  paper, per the screen's Phase 3.

## Prediction, so it can be scored

Rules 3, 4, 5 and 6 show nothing distinguishable from random entry (the
daily versions of the indicator claims measured d ≈ 0; the Fibonacci
controls are expected to match the named zone). Rule 2 is unclear. Rule 1
is the one with a real chance of clearing conditions 1–3; its RVOL
dose-response is where the mechanism either shows or does not.

## Enforcement

`intraday_rules.py --selftest` drives every detector on synthetic sessions
built to fire it exactly once, proves the flat-at-close exit, the one-
position rule, the fixed-seed benchmark's reproducibility and spread, the
clustered SE, and the Fibonacci zones' disjointness. Falsified before merge.
