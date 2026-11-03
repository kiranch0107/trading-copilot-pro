# Progress review 2026-11-03 (15:45 ET): J1 withheld its first alert, the key check held twice, the first revision rows, a check gone red by calendar

Written on the first review after the weekend. Read with
`results/progress_review_2026-10-30_late.md` and BACKLOG 43–45. One code
change (M1, a fixture); no engine, rule, threshold, reading or committed
result changes.

## What ran since Friday's post-close pass

- **Sunday 11-01, universe snapshot.** The dynamic watchlist turned over:
  ACN, MPC, PSX stay; LLY, TSLA, CRWD, AMGN, ADI join; NOW, PFE, TMO, CME,
  ABNB leave (CME and TMO now reach the alerts through the pool; the NOW
  position is watched by the exit monitor, which reads positions, not the
  watchlist; NOW's open rows are graded through the departed-name fetch).
  Basket week 2 anchored on 10-30's closes.
- **Monday 11-02, 11:07 ET (run 707) — the first scan where J1 could bind,
  and it did.** Live: ACN alerted on bar 10-30 (HQ, R:R 3.2). Pool: 75
  scanned, 11 HQ; CVX, TMO, CME, V, MA delivered by RS; **XOM withheld**
  ("NOT SENT: last price 181.65 is beyond the stop (stop 182.29, target
  203.13) — the grader would void this fill"), one undelivered alert row,
  and the withheld name did not consume a cap slot (five delivered under
  the cap of five, then WMT, PG, UNP, UPS, UNH beyond it). Done line: "6
  alert(s) sent, 0 suppressed by cooldown, 1 withheld at the fill". 28
  graded, 3 void.
- **The first two revision rows, same run.** ACN's 10-30 row (recorded by
  Friday's 16:20 pass): price 257.62 → 258.24 (+0.24 %), entry, stop and
  target by the same 0.62, RSI 69.9 → 70.1, strength Normal → Strong;
  decision and HQ tag unchanged. PFE's 10-30 row: target 32.81 → 32.82,
  R:R 3.18 → 3.19, ADX 28.8 → 28.9. Neither moved a decision or the tag.
- **Monday 15:07 ET (run 709) — the key check's first live exercise
  (J3).** ACN was alerted at 11:07:32 and re-evaluated at 15:07:33 — the
  4-hour stamp had lapsed by about 20 seconds, exactly the race L1 was
  built for — and the delivered-row check held it: "ACN Bullish qualifies
  but bar 2026-10-30 was already alerted (delivered alert row) —
  suppressed." The same thing happened today at 15:07:37 on the 11-02
  bar. The 11:07 and 15:07 dispatches are four hours apart to the second,
  so the stamp alone would now lose this race most afternoons; the key
  check is the one holding it.
- **Monday 16:20 ET post-close pass (run 710).** ACN, ADI, AMGN, LLY
  recorded on the 11-02 bar (4 live rows) with controls; the day's
  `pass: post_close` row; IV rows; no revision of these four on today's
  11:07 re-read.
- **Today 11-03, 11:07 ET (run 713).** Live: ACN on bar 11-02 (HQ, 3.2;
  no contract inside the 8 % spread ceiling, tightest 8.1 %). Pool: 4 HQ,
  all four delivered: CVX (R:R 1.25), ABBV (1.02), CME (3.2), UNP (3.2).
  18 graded, 0 void. 13:07 and 15:07: nothing new, ACN held by the key.
- **The exit monitor** has run every half hour: NOW STOP, "already
  alerted, not re-sending (still watching)". `open_positions.json` is
  still EXIT_SIGNALLED. **The position is not closed.** Expiry 11-20 is
  12 sessions out; the structure's 7-DTE exit falls on 11-11.
- **DST ended 11-01 and the scheduling held by design:** the external
  dispatcher runs on ET (11:07, 13:07, 15:07, 16:20 as before); the UTC
  backup crons fired at odd hours (18:03 and 21:25 ET) and the
  market-hours guard discarded them, as the workflow comments say it
  will.

## The record

1,286 rows after the weekend: 644 signals, 123 controls, 383 outcomes,
109 scans, 25 alerts (24 delivered, 1 withheld at the fill), 2 revision
rows (max price move 0.24 %, 0 moved a decision or
the tag). Pool: 56 episodes, 40 settled, 16 open, mean R −0.057 (18
wins), gate 40/349. Live: 21 episodes, 17 settled, 4 open, mean R −0.219
(6 wins). Void at the fill: 15 of 383 graded rows (3.9 %). Basket week
1 graded −3.02 % vs SPY (NOW −34 % of the eight); week 2 anchored.

## Findings

### M1. The consistency check went red by calendar on Saturday (fixed here)

`check_signal_key_round_trip` opened a signal-sourced position with a
fixed 2026-11-20 expiry and no `traded_at`, so `journal_store` dated the
entry with the wall clock; on 10-31 the expiry fell under the 21-DTE
floor of the measured structure and the check has failed since (17 DTE
today). No push has run the selftests since Friday's merge, so CI did
not show it; the next PR would have. Fixed by dating the fixture
(`traded_at` 2026-09-11, expiry 2026-10-16, 35 DTE) and, so it cannot
recur, check 66 `check_fixtures_never_read_the_clock`: every call that
opens a signal-sourced position in this file's and `journal_store`'s
selftests must pass `traded_at=`. One `journal_store` fixture that is
refused for lacking a signal key (before the structure check) is dated
too, so the rule is unconditional. Falsified both ways.

### M2. J1 withheld XOM on its first binding day (nothing to do)

The withhold was on the LAST PRICE (181.65 against a 182.29 stop), not
the open: XOM opened inside its levels on 11-02 and had traded through
the stop by 11:10. The grader will read the open and may grade this row
rather than void it; the gate is deliberately conservative on the last
print (the trade is gone at the alert's moment either way), as the
amendment-G note says. One row per key: the 13:07 and 15:07 runs logged
nothing further for XOM.

### M3. The key check now carries the afternoon suppression (nothing to do)

Both 15:07 runs since Monday reached the key check because the stamp
lapsed by ~20 seconds. Without L1 (10-29) ACN would have been alerted
twice on Monday and twice today. The Done line counts these under
"suppressed by cooldown"; the log line says which check held. Fine as
is.

### M4. Pool alerts at R:R 1.25 and 1.02 are inside the rules (nothing to do)

CVX and ABBV were HQ today at R:R 1.25 and 1.02. The HQ tier's R:R leg
is `hq_min_rr = 1.0` (the 10-29 review's first alerts ranged 1.13–3.2);
3.2 is the capped target. By the rules, recorded as such, not a finding.
Whether the tier should ask more of R:R is a threshold question for the
record to answer, not for a review to move.

### M5. The revision measurement, interim (owner's decision due ~11-06)

Three re-reads of post-close bars so far: 10-29 bar, 0 of 1 live row
revised; 10-30 bar, 2 of 2; 11-02 bar, 0 of 4. Two of seven rows, the
largest price move 0.24 %, no decision or HQ tag moved (ACN's strength
tag moved Normal → Strong, which the tier no longer reads since
amendment G). Read so far: the 16:20 slot records a bar that still moves
a little, and nothing it has recorded would have been decided
differently. Recommendation when the week is in: keep the slot and keep
the measurement; move it only if a re-read ever moves a decision or the
tag. The decision is the owner's.

### M6. NOW (owner's)

Still open, EXIT_SIGNALLED since 10-30 09:30 ET, STOP −56 % at 10:00 and
−72 % at 15:30 that day. Nothing in the repo closes a position. The
hand-off line stays until it is logged.

## Pending items, in the order they block things

1. **Close the NOW position and log it** (expiry 11-20; 7-DTE exit on
   11-11).
2. ~11-06: the week's revision rows (2 of 7 so far), then the post-close
   slot decision (BACKLOG 39, M5).
3. 11-09/10: the first IV premium rows (snapshots from 10-09 settle 21
   sessions later).
4. 11-30: the first spread settlements. By 12-16: rotate both PATs.

## Resumed here

M1 (the fixture, check 66). The reads the hand-off asked for — J1's first
binding, the key check, the first revision rows, the first basket
reading — are all read above; the 10-30 hand-off line's read is retired,
the 10-29 line's revision read stays until the decision.

## Not done here

No engine, rule, threshold, reading or committed result changed. The NOW
position is not closed by anything in the repo.
