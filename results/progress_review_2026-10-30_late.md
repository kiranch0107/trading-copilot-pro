# Progress review 2026-10-30 (late, ~16:00 ET): J1/J2 are live; the 15:07 run; NOW at −72 %

Written after PR #180 (J1, the fill checked before the alert; J2, the
earnings blackout read against the signal bar's date) merged at 14:28 ET.
Read with `results/progress_review_2026-10-30.md` (the 14:00 review that
found J1 and J2) and BACKLOG 43/44. Nothing here changes an engine, a
rule, a threshold, a reading or a committed result; the two small code
changes it carries (K1) are counters and wording.

## What ran since 14:00 ET

- **15:07 ET scan (Trading Scanner run 703, main at `4b62106`, after the
  merge).** The first live run of the J1/J2 code. Watchlist (dynamic,
  snapshot 2026-10-25): ACN, NOW, MPC, PSX, PFE, TMO, CME, ABNB. SPY
  regime Bull. CME Bullish qualified and was held by the 4-hour stamp —
  alerted 11:07:44, re-evaluated 15:07:41, **3 seconds inside the
  window**. Done: 0 sent, 1 suppressed, 0 failed. Scan row 955 (bar
  10-29, live, 2 signals, 8 tickers); grading deferred 171 pool/pullback
  signals in 34 names to the pool run or the post-close pass; coverage
  OK. No error from the new code; the key check and the fill gate were
  not reached, because the stamp sits first.
- **Exit monitor at 14:00, 14:30 (scheduled), 15:00, 15:30 ET.** NOW
  2026-11-20 150C: STOP, premium −72 % at 15:30 (entry 0.25); "already
  alerted STOP, and STOP does not outrank it — not re-sending (still
  watching)". `open_positions.json` still EXIT_SIGNALLED; `exit_premium`
  stays 0.11 (the first detection at 10:00 ET), `last_check_epoch`
  advances. Correct behaviour; the position is the owner's to close.
- **Another live scan at 15:53 ET** (run 704, `schedule` — the 15:37 ET
  cron delivered 16 minutes late by GitHub): one scan row (bar 10-29,
  2 signals), nothing sent. Two live scans in the 15:00 hour; the
  dispatcher-versus-schedule pattern the 10-29 reviews described.
- **16:20 ET post-close pass (run 705, dispatched, main at `30cdda3`).**
  Clean. ACN Bullish recorded on the 10-30 bar (HQ, R:R 3.2) and PFE
  (base, 3.18), each with its control row; 55 signals graded, 4 void
  (AMZN, ANET, NVDA pullback rows and QCOM pool, all `gapped_before_fill`
  on 10-30's open), 141 still open; 8 IV snapshot rows; the FIRST basket
  reading (2026-10-25 week: basket −4.13 % vs SPY −1.11 %, delta
  −3.02 %, NOW −34 % of it); spread 0 cycles opened. Clocks line: pool
  38/349 settled episodes; revisions 0; basket 1/52; spread 0/12 per arm;
  IV premium 0 of 128, oldest unresolved 2026-10-09. Done: 0 sent, 0
  suppressed, 0 failed. Coverage OK, last scan bar 2026-10-30.

## The record

After the post-close pass: 1,020 rows — 553 signals, 60 controls, 294
outcomes, 100 scans, 13 alerts (13 delivered, 0 withheld — J1 has had
nothing to withhold yet), 0 revision rows, 0 voided at the risk floor.
Pool: 47 episodes, 38 settled, 9 open, mean R −0.094 (17 wins), gate
38/349. Live: 18 episodes, 16 settled, mean R −0.246. Void at the fill
on graded live+pool rows: 7 of 198 (3.5 %; QCOM, pool, joined today).
Today's alerts: CME (live), COP, AMGN, V, PG, MA (pool, by RS; WMT,
UNP, UPS HQ beyond the cap of 5).

## Findings

### K1. A withheld alert was counted as "suppressed by cooldown" and as an undelivered alert (fixed here)

The scanner's Done line summed the fill gate's withholds into the
cooldown count, and `forward_log.summary()` counted a NOT SENT row only
as an alert that was not delivered, which reads as a Telegram failure.
Both now say which: the Done line carries "N withheld at the fill";
`summary()` has `alerts_withheld` (header starts "NOT SENT") and
`report()` prints "(M delivered, W withheld at the fill)". Pinned by the
forward_log selftest (a NOT SENT row is withheld, not failed; the report
line) and `check_fill_gate_and_blackout_bar` (the gate counts
`withheld`, not `skipped`). Falsified.

### K2. The fill probe's docstring described a bar no run sees (fixed here)

`fill_probe()`'s docstring, the amendment-G note and BACKLOG 43 said the
bar after the signal bar is "the settled next bar the morning after a
post-close record". It is not: the signal bar is always the last settled
bar, so the bar after it is the session in progress — today's partial
bar — whichever pass recorded the signal. Corrected in all three; the
pre-registration note keeps the correction visible rather than silently
rewritten.

### K3. The live fill-probe path, exercised offline on a Yahoo-shaped frame (nothing to do)

The selftest uses a plain frame; a Yahoo frame has a tz-aware index and
MultiIndex columns, and a tz mismatch inside `fill_probe()` would have
been caught by its `except` and read as "no fill to check" — J1 silently
off. Checked here with a 120-bar frame indexed in America/New_York,
columns flattened as `get_data()` does, today's partial bar last:

| case | `drop_partial_bar` | `fill_probe` |
|---|---|---|
| 11:07 ET, Friday bar 10-29 signal, 10-30 partial (open 110, last 109.5) | dropped | (110.0, 109.5) → "fill (next open) 110.00 is beyond the stop (stop 142.34, target 158.70)" |
| 16:20 ET post-close, frame ends on today's settled bar | kept | (None, None) → no check, as designed |
| Monday 11-02 11:07, Friday 10-30 bar signal, Monday partial | dropped | (110.0, 109.5) |

`signal_core._bar_dates()` normalises both frames to tz-naive, so the
comparison holds. Monday's 11:07 scan is still the first run where the
gate can bind on real data.

### K4. The 4-hour stamp held by 3 seconds (nothing to do)

11:07:44 to 15:07:41. Had the 15:07 run started 4 seconds later, CME
would have reached the key check (L1), which would have held it on the
delivered row. Either path is correct; J3 (the key check exercised
live) stays unexercised. Not a finding against anything.

### K5. The 16:20 ET pass, the first post-close run of J2 (read: clean)

On the post-close path `bar_date` is today, so the straddle rule reduces
to "a release today", which the window already blocked (delta 0). Read
at 16:23 ET: no error, no warning; ACN and PFE recorded on the 10-30
bar with their controls; the day's `pass: post_close` scan row (seq
1020's run) is stamped; `fill_probe()` had no bar after the signal bar
and the gate was not asked, as designed (record-only runs never reach
it anyway). Nothing to do.

### K6. The fill gate falls open when Yahoo has no bar after the signal bar (noted, by design)

In the first minutes of a session, or on a Yahoo gap, the raw frame may
end on the signal bar; `fill_probe()` returns (None, None) and the alert
goes out as before J1. The grader still voids a gapped fill. A fail-closed
gate (no fill price, no alert) would silence the 11:07 scan on every
Yahoo hiccup; not recommended.

### K7. The app's alert history reads a NOT SENT row correctly (nothing to do)

The alert history shows the header ("NOT SENT: fill (next open) …") with
"❌ not delivered". Readable as written; the "Delivered" metric counts it
as not delivered, which is true.

## Improvements, in the order they pay

1. **K1 and K2** — in this PR.
2. Nothing else from this pass. The owner-decided items stand: the
   post-close slot (~11-06), one alert per episode (J4) only if wanted.

## Pending items, in the order they block things

1. **Close the NOW position and log it** (EXIT_SIGNALLED since 09:30 ET;
   STOP −56 % at 10:00, −72 % at 15:30).
2. **Monday 11-02, 11:07 ET**: the first scan where J1 can bind. Read the
   log for "withheld at the fill" on the Done line and any "NOT SENT"
   row; read the key check (J3) if the stamp lapses.
3. ~2026-11-06: a week of revision rows (still 0), then the post-close
   slot decision (BACKLOG 39).
4. 2026-11-09/10: the first IV premium rows. 2026-11-30: the first spread
   settlements. By 2026-12-16: rotate both PATs.

## Not done here

No engine, rule, threshold, reading or committed result changed. The NOW
position is not closed by anything in the repo.
