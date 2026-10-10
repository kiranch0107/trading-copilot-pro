# PRE-REGISTRATION — the six rules on 4-HOUR bars, held across sessions (day-trading screen, Phase 1c)

**Written 2026-10-10 at the owner's request ("lets do 4hr timeframe"), and
committed BEFORE any 4-hour bar is built.** Everything below is fixed. It may
be AMENDED in a dated section at the top only until the first run is
committed. Parents: `intraday_rules_preregistration.md` (5-minute, run
2026-10-10, no rule cleared) and `intraday_rules_hourly_preregistration.md`
(hourly, run 2026-10-10, no rule cleared). Neither verdict is reopened here.

## What a 4-hour bar does to the question, stated before the data

A US regular session is six and a half hours, so it holds **two** 4-hour
bars: 09:30–13:30 and 13:30–16:00. A rule that enters on one bar's close and
is flat at the session's close has exactly one trade available — enter at
13:30, exit at 16:00 — which is not chart reading on 4-hour bars, it is a
fixed-time bet. So on this timeframe the session cannot be the unit. **This
document therefore tests the same six tools as a SWING method**: the rules
read the trading WEEK as their window (up to ten bars) and a position is
held for up to ten 4-hour bars, five sessions, across session and week
boundaries. That is the hypothesis a 4-hour chart reader actually holds. It
is a different hypothesis from day trading, and it sits next to the daily
record this project already measured (RSI, volume and ADX at d ≈ 0 on
1,457 daily setups; random entries matched the signal).

**Multiplicity, paid up front.** This is the **third** test of the same
family of tools. A rule clears here only at the **99 %** clustered interval
(z = 2.576), not 95 % or 97.5 %, so that testing the family three times
buys no false positive.

## What is held fixed

| item | value |
|---|---|
| bars | **resampled from the frozen hourly cache** (`.bar_cache/`, key `1h`, variant `2y-raw`, the bars of the hourly run, fingerprint `v2-eb11301b7b4c55e4`). Yahoo serves no 4-hour interval. Two bars per session: 09:30–13:30 (four hourly bars) and 13:30–16:00 (three; the last is the half-hour bar); a half-day session yields one bar. OHLC aggregate, volume sums; the bar's timestamp is its first hourly bar's. **No new fetch**: the run's fingerprint must equal the hourly run's, or the data are not the data this document was written for |
| universe | the same 20 symbols as the 5-minute and hourly tests |
| window ("session" in the code) | **the trading week** (ISO week, Monday to Friday, at most ten bars). One trade per rule per name per week; one position per name at a time across rules. A week of fewer than three bars (a one-session holiday week) is skipped |
| hold | next-bar open + 5 bps; stop first, then target, else **marked at the close of the tenth 4-hour bar held, the entry bar included** (five sessions). The hold crosses session and week boundaries. A signal on the week's last bar fills at Monday's open |
| costs / fills | 5 bps a side; gapped levels fill at the open (overnight and weekend gaps are now inside the hold, so the left tail is real and is reported, not floored) |
| indicators | EMA9, EMA20, RSI14, ATR14 on the 4-hour close; **VWAP anchored at the week's open** (the cumulative VWAP from Monday's first bar, a standard anchored-VWAP reading); **pivots over ±1 bar** (strictly above the left bar, at least equal to the right, ties to the earlier bar); **opening range = the first bar of the week** (Monday 09:30–13:30) |
| structure look-back | pivots, impulses and trendlines may use **the current and the previous week** (up to 20 bars) |
| RVOL | the week's first-bar volume over the median first-bar volume of the prior 20 weeks |

## The six rules, restated for the week

1. **Opening-range breakout.** After Monday's first bar, a close above its
   high (long) or below its low (short), RVOL ≥ 1.5. Stop at the range's
   other side; target 2 R. Dose-response by RVOL tercile.
2. **VWAP reclaim.** A close ≥ 1 ATR beyond the week-anchored VWAP then a
   close back through it; stop at the extreme since the deviation; target
   VWAP ± 1 ATR.
3. **EMA pullback in a pivot-defined trend.** Trend from the last two pivot
   highs and lows (±1-bar pivots, two-week look-back); a touch of EMA20 with
   the close above EMA9 (long). Stop at the last pivot low; target 2 R.
4. **RSI divergence.** Pivot lows **≥ 3 bars apart** (a session and a half),
   price lower low, RSI14 higher low → long on the next close. Stop at the
   new low; target 2 R. Mirrored.
5. **Fibonacci pullback with controls.** Same three equal-width zones
   (named 50.0–61.8 %, controls 30.0–41.8 % and 70.0–81.8 %); impulse from
   ±1-bar pivots over the two-week look-back.
6. **Trendline break.** Regression through the last three pivot lows over
   the two-week look-back; a close below it → short. Mirrored.

## The benchmark and the bar

Benchmark unchanged in method: twenty fixed-seed random-entry draws, same
side, same stop and target distances, same engine and costs, same ten-bar
hold. Random bars are drawn from the week's second bar to its
second-from-last.

A rule **clears** only if, net of costs: mean R ≥ +0.15; the **99 %**
week-clustered interval lies above zero; mean R exceeds the random null by
≥ +0.15 R; and for rule 5 the named zone beats both controls by ≥ +0.15 R.

Clusters are the **signal's week**. A ten-bar hold can run into the
following week, so adjacent clusters are not fully independent; the
interval is therefore somewhat optimistic, which is one more reason for
the 99 % level and for the margin over the null, which does not depend on
the interval.

## Power, honestly

Two years is about **100 week clusters**, not 500 session clusters. With a
rule firing on a third of name-weeks there are ~700 setups, and the
clustered SE will be on the order of 0.06–0.12 R, so at 99 % the window
can see an edge of roughly **+0.15 to +0.30 R**. Two consequences, stated
now: the "mean R ≥ +0.15" clause is decisive whatever the interval (a rule
below it fails regardless of power), but a small loss may read as "not
distinguishable from zero" rather than the measured loss the hourly run
gave. A rule that clears gets ONE pre-stated confirmation: the following
26 weeks of hourly bars, resampled the same way, same code, same bar.

## Prediction, so it can be scored

- **No rule clears.**
- Timeouts fall well below the hourly run's 60–95 %: ten bars is enough
  time to reach a 2 R target or a stop, so most trades resolve at a level.
- Mean R per rule moves toward the daily record's value: near zero to
  mildly negative, with wider intervals than the hourly run.
- Rule 5's named zone does not separate from its controls (it was the
  worst zone twice; here it will be inside noise of them).
- Rule 1's RVOL dose-response is absent, as on both prior timeframes.
- Rules 4 and 6 are not above random entry by the margin.

## Enforcement

`python intraday_rules.py --timeframe 4h` applies this profile: it reads the
hourly cache key, resamples in code (`resample_4h`), groups by ISO week,
holds ten bars, and uses z = 2.576. Its selftest proves the resample
(two bars a session, one on a half day, OHLCV aggregation), the weekly
window, a hold that crosses the week boundary and stops at the tenth bar,
the two-week structure look-back, and the 99 % interval.
`check_intraday_rules_match_preregistration()` holds all three profiles to
their documents' numbers.
