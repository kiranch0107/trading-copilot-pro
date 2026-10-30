# Progress review — 2026-10-30 (14:00 ET): the first day's reads, and the NOW trade

Owner's request: "review the details now." Read against `main` at 622f991
after the 11:07 and 13:07 ET scans and the exit monitor's 14:00 check.
Nothing was fetched from Yahoo; every number is from the committed files
and the workflow logs.

## What the day's runs did

- **11:07 ET** (run #701, on the 10-29 night's code): bar 10-29. Live: CME
  alerted (HQ: ADX 30.1, R:R 1.51, every filter); PFE a base signal,
  recorded. Pool: 75 scanned, 34 signals, 8 HQ (AMGN, COP, MA, PG, UNP,
  UPS, V, WMT), 5 alerted by relative strength (COP +21.5, AMGN +16.6,
  V +9.1, PG +8.2, MA +7.9), WMT, UNP and UPS beyond the cap and recorded.
  Six alerts, all delivered. 13 rows graded (7 wins, 6 losses), NVDA 10-21
  void at the fill. Four pullback rows. The alert text carried the new
  rules line.
- **13:07 ET** (run #702): CME "alerted within 4h — suppressed". The
  4-hour stamp fires before the key check, so the key-based suppression
  (L1) is exercised only when the stamp has lapsed: the 15:07 run is its
  first real test (yesterday's 15:07 run held by 7 seconds).
- **Bar revisions: none.** CME's 10-29 row was the only live row the 16:09
  pass had written, and the 11:07 re-read matched it field for field, so
  no `revision` row was due. A revision the note could not see: NOW's
  10-28 close was recorded at 149.85 by the 16:11 pass and read as 150.13
  by the app at 11:21 the next day (+0.19 %). From today the note is live
  for every re-read.
- **Exit monitor**: 496 runs. NOW: THESIS at 09:30 ET ("closed 109.67 —
  below EMA20 143.47"), premium 0.16 (−36 %); escalated to STOP at 14:00
  (premium −56 %, limit −50 %); `exit_premium` 0.11; status
  EXIT_SIGNALLED, the exit alert delivered. **The position is yours to
  close and log**; the journal row then attaches to the NOW 10-28 signal
  on the premium basis.

## The record after the morning

| bucket | episodes | settled | open | mean R | gate |
|---|---|---|---|---|---|
| pool | 45 | 32 | 13 | −0.268 | 32 of 349 |
| live | 18 | 16 | 2 | −0.246 | — |

(The 13 rows graded this morning settle episodes that first fired weeks
ago; the episode counts move at the 16:09 pass, which reads the pool.)
Alerts 13 in two days, all delivered. HQ rows under F+G: 15. Void at the
fill, all buckets: 6 of 188 graded rows (3.2 %); the risk floor has voided
none yet.

## Findings

### J1. The alert fires on a bar whose next open has already gapped past the stop (fix recommended, before Monday)

NOW reported after the 10-28 close and opened 10-29 about 27 % lower. The
11:07 scan on 10-29 evaluated the SETTLED 10-28 bar (close 149.85, stop
142.34, target 158.70), found it HQ under the new rules, and alerted
"Bullish, entry 149.85" while the stock traded near 110. The forward
grader, run at the 16:09 pass, VOIDed the same row as `gapped_before_fill`
(the 10-29 open was below the stop), and the research engine refuses the
same fill: both engines say "no trade"; the alert path does not ask. The
owner bought a 150 call for $0.25 with the stock at 110, which is the
premium of a strike 36 % out of the money, and the thesis exit fired the
next morning on the 10-29 close.

The scanner has the number it needs: `get_data()` returns today's partial
bar and `drop_partial_bar()` removes it before evaluating. Its Open is the
fill the grader will use. **Fix:** before sending an alert, compare that
open (and, as a second guard, the latest price) with the setup's stop and
target; a long whose open is at or below the stop, or at or above the
target, is "gapped at the fill": the row stays `taken` (the rules
produced it), no alert is sent, an alert row records `delivered: false`
with the reason, and the log says so. The same rule, in the same words
as the grader's. On the record so far this would have withheld 6 alerts
of 188 graded rows (3.2 %), every one of which the grader later voided.
An intake change; no amendment, since the recorded row and its grading
are unchanged.

### J2. The earnings blackout passes the morning after a release, on a bar from before it (fix recommended, with J1)

The 16:11 pass on 10-28 recorded NOW with `hq_fail` = Earnings Blackout:
the release was that evening. The 11:07 re-read on 10-29 found the date
past and the leg passing, so the tag flipped to HQ and the alert went
out, on a signal bar that closed BEFORE the release. The blackout asks
"is there a release within N days of today"; the question the tier needs
is "does a release fall between the signal bar's close and the fill", and
the morning after is the worst case of it. **Fix:** in
`market_context.earnings_blackout_from_dates`, read the window against
the signal bar's date as well as today, and treat a release on or after
the bar's date and before the fill date as inside the blackout. A change
to an HQ leg's definition: an intake note under amendment G.

### J3. The key-based suppression has not yet been exercised (nothing to do)

At 13:07 the 4-hour stamp held CME. The L1 check only decides when the
stamp has lapsed, which is the 15:07 run; read that log once.

### J4. AMGN and V were alerted on two consecutive bars (by design, noted)

Both were HQ on 10-28 and again on 10-29; each bar is its own signal key
and the 4-hour stamp had lapsed, so each alerted. The old cooldown did the
same. If one alert per episode is wanted instead, that is an intake choice
to decide, not a defect.

## Improvements, in the order they pay

1. **J1** now: the gap-at-the-fill gate on the alert path. Small, mirrors
   the grader, prevents the trade the record will void.
2. **J2** with it: the blackout reads the signal bar's date.
3. **J4** only if the owner wants one alert per episode.

## Pending items, in the order they block things

1. Close the NOW position and log it (EXIT_SIGNALLED since 09:30 ET).
2. J1 and J2 with a "go", before Monday 11-02's first scan.
3. Today 15:07 ET: the key-based suppression's first real test; 16:09 ET:
   the first stamped pass with the clocks line on the day's rows.
4. ~2026-11-06: a week of revision rows, then the post-close slot decision.
5. 2026-11-09/10: the first IV premium rows. 2026-11-30: the first spread
   settlements. By 2026-12-16: token rotation.

## Not done here

No engine, rule or threshold was changed by this review. J1 and J2 are
proposed, not built.
