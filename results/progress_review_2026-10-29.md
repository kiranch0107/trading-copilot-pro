# Progress review — 2026-10-29 (night)

Owner's request: "Review again thoroughly and let me know for improvements
and pending items." Read against `main` at 1364d87 (PR #172), one day after
the 10-28 review and after nine merges today (#164–#172). Every number
below was computed from the committed files in this session; nothing was
fetched from Yahoo.

## What changed in the day

- **The first alerts** (BACKLOG 39): seven on bar 10-28, all genuine by
  the declared legs; the NOW trade logged under BACKLOG 32's rules with
  its key corrected; the app refuses an unrecorded bar date; the bar
  revision note records a re-read that differs from its recorded row.
- **Post-close hygiene** (#169): the record pass stamps its heartbeat and
  a same-day repeat is skipped (the 16:20 dispatch ran one second today);
  one `clocks:` line ends the pass; the spread report tallies unquoted
  legs.
- **The logic review** (BACKLOG 40, #170–#172): about 140 boundary
  assertions, every declared boundary holding; then three fixes the owner
  chose: alerts suppressed by signal key (the 4-hour stamp held today by
  7 and 8 seconds), a risk floor at the fill in the forward grader AND the
  research engine (one function, `risk_params.min_risk`), with the four
  re-runs merged as one change. OOS run 5 still FAILs; the floor removed
  one AVGO row of roughly −15 R; the two engines agree on 10,450 rows with
  119 floored in both.
- **The spread arm's first cycles**: eight opened at the 10-29 close, every
  leg quoted; market mid / model credit 0.46–0.79 on every arm, below the
  0.85 void line, the market's put skew steeper than the linear 20 points
  and OTM calls far cheaper. Not a reading before 6 graded cycles per arm.
- Harness at 1364d87: 47 module commands, 63 consistency checks, pyflakes
  clean. About 60 mutants falsified today.

## The unattended system

- Scanner: 699 runs, every one today green: 11:07 (alerts), 13:07 and
  15:07 (cooldown held), 16:09 schedule (the record pass on the merged
  code: CME recorded, 36 graded, 8 IV rows, 8 spread cycles, the clocks
  line), 16:20 dispatch (skipped on the stamp). Exit monitor: 484 runs,
  green, the NOW position checked every 30 minutes in session, last at
  15:30 ET: premium 0.17 (−32 %), 22 DTE, OPEN, no exit rule fired (STOP
  at −50 %, TARGET at +200 %, TIME at 7 DTE).
- **DST ends 2026-11-01.** The primary dispatches are in America/New_York
  at cron-job.org and do not move; the backup UTC crons shift to
  10:07–14:07 ET and the 21:20 UTC slot becomes the in-window 16:20 EST
  one. Nothing to do; the first EST scan is Monday 11-02 at 11:07.
- Half days 11-27 and 12-24: the 13:07 dispatch lands inside the
  13:00–15:00 window and runs the record pass; the 15:07 and 16:20
  dispatches are outside it and skip. Nothing to do.

## The record, read both ways

864 rows: 512 signals, 221 outcomes, 94 scans, 30 controls, 7 alerts (all
delivered), 0 revisions, 0 voided at the floor. Taken 345: pool 240
(hq 5), live 79 (hq 2), pullback 26. Seven HQ rows under amendments F+G:
ACN and CME (live), AMGN, SPGI, UNP, V, WMT (pool).

| bucket | episodes | settled | open | mean R | wins | gate |
|---|---|---|---|---|---|---|
| pool (the population) | 45 | 32 | 13 | −0.268 | 13 | 32 of 349 |
| live (the alerting view) | 18 | 16 | 2 | −0.246 | 5 | — |
| pullback | 12 | 6 | 6 | +0.436 | 2 | — |

Arrival: 45 pool episodes on 13 pool bars, 3.5 a bar; the gate needs
about 90 more bars, ~4.3 months, unchanged.

## Findings

### G1. The H1/H1′ reading loses 18 of its 21 post-F rows to episodes that straddle the cut (reading defect, fix recommended)

Amendment F reads the HQ questions from 2026-10-28 forward; the episode
unit is dated at its first bar. The pool has scanned daily since 10-13,
so on 10-28 most names were CONTINUING an episode that began under a
pre-F hash: of the 21 taken pool rows under `4ddd12a3095f`, 18 continue
such an episode and only 3 start one. `summary()` keeps an episode for
H1/H1′ only when its FIRST row is readable, so today's report prints
"H1′ tier 0 episodes, rest 3" while five readable rows sit in the tier
and five are HQ. The control pairing has the same shape: 21 pool controls
recorded and 14 settled, 1 pair read, because the pair is the episode's
first row and controls began on 10-28. **Fix:** for the HQ questions and
the control pairing, form the episodes from the READABLE rows only (the
episode re-anchors at the cut: its first readable row is its first row,
with that row's outcome and control; every taken row is graded, so the
outcome exists). Re-anchored today: 21 episodes, 5 in the tier, 16 in
the rest, 3 settled. The base question, the 349 gate and every count keep
the full episodes. A reading rule, to be stated as a note under amendment
F; no row changes.

### G2. The spread ratio is readable at entry, and the report hides it until settlement

`spread_log.summary()` computes the market/model ratio from GRADED cycles,
so the report prints "n/a on 0 quoted cycles" while all eight open cycles
carry their quotes and ratios (0.46–0.79). The pre-registration fixes the
READING at 6 graded cycles per arm; it does not forbid printing the open
cycles' ratios as counts. Improvement: a descriptive line for open cycles
(per arm ratio at entry, labelled "not a reading"), so the F4 question is
visible weekly without opening the file. No amendment.

### G3. Report wording: "no HQ rows" when there are HQ rows and none settled

`report()` prints "mean R hq no HQ rows" for the pool although five HQ rows
exist; the phrase means "no settled HQ rows". A wording fix in the
formatter, nothing else.

### G4. Two cosmetic drifts from the day

`engine_differential.py`'s usage line still names run 1 as the tee
target; `oos_validate.py` numbers its runs by ledger entries (6) while the
committed readings are numbered by file (5), and the run-5 write-up says
so. Both cosmetic.

### G5. The grading amendment's own clause

`forward_grading_preregistration.md` said it changes nothing after the
first outcome row and was amended twice today after 182 of them, each
time saying so in its first line. Recorded here so a later reader of the
record knows the clause was knowingly set aside by the owner, not missed.

## Improvements, in the order they pay

1. **G1**: re-anchor the HQ and control readings at the amendment F cut.
   Small (one list comprehension and a note); it changes what the report
   shows tomorrow, not any row. Do with the next "go".
2. **G2**: the open-cycle ratio line in the spread report.
3. **G3** and **G4**: wording and docstrings.
4. The post-close slot decision (BACKLOG 39) waits on a week of revision
   rows from 10-30; nothing to build before then.

## Pending items, in the order they block things

1. 2026-10-30 11:07 ET: the first revision rows (NOW, ACN, CME and the
   pool's 10-29 rows against the 16:09 pass), the first run of the
   key-based suppression against the seven delivered alerts, and whether
   CME alerts (recorded HQ at the close, never alerted post-close).
2. ~2026-11-06: the revision count, then the post-close slot decision.
3. 2026-11-09 / 11-10: first IV premium rows (F5 of the 10-28 review).
4. 2026-11-30: the first spread settlements; 6 graded cycles per arm
   about April 2027; the read of the model's skew in both directions.
5. 2026-12-16: token rotation (app 2027-01-05, dispatch 2027-01-06).
6. The forward clocks as the `clocks:` line prints them: pool 32 of 349,
   basket 0 of 52 (1 recorded), spread 0 of 12 per arm (8 opened), IV
   premium 0 graded of 128 snapshots.

## Not done here

No engine, rule or threshold was changed by this review. No research
result was produced. G1 is proposed, not built.
