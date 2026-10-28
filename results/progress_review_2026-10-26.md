# Progress review — 2026-10-26

Owner's request: "Review again thoroughly and let me know for improvements and
pending items." Read against `main` at 053253f (the 10-26 15:07 scan), one
week after the 2026-10-19 review and the three external reviews of 10-19/10-20.
Every number below was computed from the committed files in this session;
nothing was fetched from Yahoo.

## What changed in the week

- **Eight PRs merged (#138–#145)**, all five groups of the first external
  review, the second review's persistence gaps, a regression of mine, and
  the third review's five gaps. Local harness on 053253f: 45 module
  commands pass, 47 consistency checks pass, pyflakes clean.
- **The unattended system ran every slot.** 24 scanner runs 10-20 → 10-26,
  all green; the exit monitor ran every 30 minutes, all green. The 10-26
  11:07 ET scan: watchlist 8 (snapshot 10-25, 1 day old), SPY regime Bull,
  pool 75 scanned, 0 HQ, 0 alerted, 0 failed; 3 pullback rows; coverage OK,
  0 sessions unscanned. The dispatch slots fire at :07 on the minute;
  GitHub's own `schedule` events fired 40–110 minutes late every day (the
  21:20 UTC cron landed 23:12–23:27 UTC and found nothing to do). The
  dispatch is carrying the post-close pass, as designed.
- **The hand-off is resolved.** NVDA 10-16 250C expired worthless (−1.0 R,
  −$91, closed in the app 10-16 13:08 ET); PFE 10-16 29C was closed manually
  10-14 14:46 ET (+0.09 R, +$4). `open_positions.json` is empty. The app's
  writes have landed since the token was replaced; the only commits to
  `main` this week other than the scanner's are the merge and the Sunday
  universe snapshot.
- **The record grew.** 579 rows: 383 signals, 121 outcomes, 75 scans.
  Taken rows: live 69 (19 of them the declared legacy rows), pool 178,
  pullback 15. Outcomes: 51 win, 67 loss, 3 void (all `gapped_before_fill`).

## The record, read both ways

The pre-registration counts episodes (consecutive bars, same ticker and
direction, first row graded). `report()` still prints rows (BACKLOG 34
finding 1, undecided). Both readings, stop-distance basis:

| bucket | rows settled | mean R by rows | episodes | settled | open | mean R by episodes | wins |
|---|---|---|---|---|---|---|---|
| live | 44 | −0.115 | 17 | 12 | 5 | +0.007 | 5 |
| pool | 70 | −0.287 | 37 | 22 | 15 | −0.272 | 9 |
| pullback | 4 | −0.517 | 7 | 4 | 3 | −0.517 | 1 |

None of these is a result. The gate is 349 settled episodes on one basis.

**The 10-19 "never reached" finding is out of date.** On 10-19, 0 of 21
settled rows carrying the full 4-ATR target had reached it. Today 8 of 57
have: CME (10-09, 10-13), KO (10-12, 10-13), PG (10-12, 10-13, 10-14), CVX
(10-12), all exiting 10-22/10-23 after 6–9 bars. Their booked R is 1.64 to
2.80, not 3.2, because the pre-registered denominator is the risk AS FILLED
(`abs(fill − stop)`, next open plus 2 bps): a fill above the signal close
both raises the risk and shortens the run to the target. That is the
declared rule, not a defect, and it is the realised payoff the live geometry
actually pays. Of the 57, 47 stopped and 2 timed out.

**Zero HQ rows, zero alerts, ever.** 243 taken rows, 0 `hq: true`; the pool
has scanned 75 names on each of 10 sessions and produced 0 HQ. The alert
log is empty. H1 (the HQ tier adds ≥ +0.15 R) has no treatment arm; the
system is a recorder today, not an alerter. BACKLOG 34 (D) stands.

## Findings

*Status 2026-10-28:* F2, F3, F4, F6, F7 and BACKLOG 36 group A are fixed
(BACKLOG 37, "Fixed 2026-10-28"). F1 and F5 stand: F1 is a
pre-registration amendment and waits on the owner's wording; F5 is a date.

### F1. The live bucket cannot reach its gate; the pool can

Arrival rates from the committed scans:

| bucket | bars | episodes | per bar | bars to 349 |
|---|---|---|---|---|
| live (8 names, since 09-16) | 28 | 17 | 0.61 | ~570 ≈ 2.3 years |
| pool (75 names, since 10-12) | 10 | 37 | 3.7 | ~94 ≈ 4.5 months |

The pre-registration estimated 0.5 episodes a bar on 8 names and said
"about three years"; the measured rate agrees. The pool is the only
population whose gate arrives inside a horizon anyone will act on.
BACKLOG 34 idea C (an amendment naming the pool as the population for the
base-signal question) is the decision that matters most; it is a
pre-registration, not code.

Compounding it, the live population is not fixed: the watchlist is the
dynamic weekly snapshot (`USE_DYNAMIC_UNIVERSE=1`) and the last four
snapshots swapped 3–4 of 8 names each week (10-04 → 10-25: CRM, TGT, NVDA,
CRWD, MRK out; CME, ABNB, NOW in). The forward pre-registration never names
the rotation, so "the watchlist" means whichever eight names the snapshot
chose that week. A reading of the live bucket is a reading of a changing
population. Amendment C would make the pool, which is fixed, the population
and leave the live bucket as the alerting view it already is.

### F2. The episode reading is still not in `report()`

Recommended 10-19 as a reading change needing no amendment; undecided. The
table above is the reading the pre-registration asks for, and the gate
line in `report()` counts rows. Beside it, `report()` prints
`mean R hq +nan` for every bucket because no HQ row exists; it should say
"no HQ rows" instead of a NaN.

### F3. A name that stops trading inside its window is never settled

`forward_log.resolve_signal` voids `no_bars` only when there is NO bar
after the signal and the row is 45 days old. A name that has some bars
after the signal and then none (delisting, acquisition, a long halt) returns
"unsettled" for ever: the row stays open, is counted open, and is re-fetched
on every pass. Not biting now (oldest open row PSX 10-08, 12 bars, data
flowing), and S&P names rarely vanish inside 20 bars, but the record will
run for years. Fix: void `no_bars` when the window has not closed and the
last fetched bar is older than `GRADE_NO_BARS_DAYS` from today. Small,
falsifiable, no amendment (it is the declared `no_bars` reason applied to
the case the text already describes).

### F4. The journal still cannot feed the premium basis

15 journal rows: 15 without `signal_key`, 14 with `stop` 0, 5 marked
discretionary. `attach_outcomes` refuses all of them (by design), so the
premium basis has zero rows and the live-money side of the record is
empty. BACKLOG 32 unchanged. The app can require a signal key and a stop
when the owner logs a trade as a system signal; that is a form rule, not a
research decision.

### F5. First IV premium rows are due 2026-11-09 and the departed-name path has never run live

88 snapshots across 12 names, 0 graded, first 10-09, last 10-23. The 21st
session after 10-09 is 11-09. Five of the 12 names have left the watchlist
(CRM, CRWD, MRK, NVDA, TGT); the post-close pass fetches departed names
for IV grading (`IV_GRADE_FETCH_CAP` 20) and logs fetched/deferred/held
counts, built and selftested 10-19 but exercised only by fixtures. The
11-09 and 11-10 post-close logs should show premium rows and the held
counts; a window held for `gap` on a name with a clean history would mean
the injected `trading_days_between` disagrees with the calendar.

### F6. Two PATs expire in ten weeks and nothing enforces the rotation

The app token expires 2027-01-05 and the cron-job.org dispatch token
2027-01-06. BACKLOG's scheduler item names the week; the calendar runway
has a consistency check (`check_calendar_runway`), the tokens do not. When
the dispatch token dies the scanner falls back to GitHub's late `schedule`
(F-above: 40–110 minutes late, and the post-close window is two hours), and
when the app token dies every app write fails silently again (the 10-13
incident, now reported in the sidebar but still a dead write). A check that
fails from 2026-12-15 until a dated BACKLOG line says both were rotated is
a forcing function of the same shape as the calendar one.

### F7. Cosmetic

`universe-snapshot.yml` says "Mondays, 12:30 UTC"; the cron is `30 12 * * 0`,
Sunday, which is when it ran (10-25). The snapshot is one day old at Monday's
scan, which is fine; the comment is wrong.

## Pending items, in the order they block things

1. **Owner's decisions from BACKLOG 34**: amendment C (pool as population),
   the `episodes()` reading (F2), D (re-pose H1), A and B if wanted.
2. **BACKLOG 36 group A** validators (finite OHLC/R/spot, bid ≤ ask,
   integer contracts, future-dated bars, id size) and the unrounded-gate
   declaration. Small, mechanical, waiting on "go".
3. **F3** dead-name void, **F6** token forcing function, **F2** NaN line:
   small code, no decisions.
4. **BACKLOG 36 group B** research-engine fixes and the owed option re-runs
   on frozen bars (seven caveated result files): owner's laptop, Yahoo.
5. **BACKLOG 36 group C** app paths; **BACKLOG 32** journal hygiene (F4).
6. **BACKLOG 33** ADX 30 decision; **BACKLOG 23** live-vs-research config.
7. **F5**: read the 11-09/11-10 post-close logs when they land.

## Not done here

No code was changed by this review. The hand-off note of 2026-10-13 is
retired in the same commit, since everything it waited on has landed: the
AAPL close, the token replacement, the NVDA and PFE closes. The token
expiry dates it carried live on in the scheduler item (BACKLOG, "FORCING
FUNCTION — TWO PATs").
