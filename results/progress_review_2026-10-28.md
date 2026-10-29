# Progress review — 2026-10-28 (evening)

Owner's request: "Review again thoroughly and let me know for improvements
and pending items." Read against `main` at 40dc969 (PR #163), two days
after the 2026-10-26 review and after eighteen merges in one day
(#146–#163). Every number below was computed from the committed files in
this session; nothing was fetched from Yahoo.

## What changed in the two days

Every decision the 10-26 review listed as pending has been made and built:

- **Amendments C, D, A, B, E, F, G** to `forward_record_preregistration.md`
  and the RS basket and spread-arm pre-registrations. The pool is the
  population; H1 is a rank tier; a control row sits beside every taken
  signal; the basket is recorded weekly; gates compare unrounded; the HQ
  tier is ADX ≥ 30, every filter, R:R ≥ 1.0, with the "Strong" tag recorded
  but no longer gating. The fingerprint moved four times today and every
  hash is declared (`forward_log.KNOWN_CONFIGS`, five entries).
- **BACKLOG 36 groups A, B and C** (validators; the research engines on one
  clock each with every owed re-run folded in; the app paths), **BACKLOG 23**
  (new research runs start from the live config), **BACKLOG 32** (the
  journal enforces the measured structure, refuses discretionary live
  entries, the test row is gone), **BACKLOG 33** (ADX 30), **BACKLOG 34 E**
  (the spread arm pre-registered and recorded forward; run 4 reproduced run
  3 and named its bars).
- **Re-runs on the frozen bars**: option sweep run 3 (constants moved),
  thesis run 3 (KEEP), spread runs 3 and 4 (five of eight arms clear the bar
  on the fixed engine; not a result, pre-registered), portfolio replay run
  4, pullback arrival run 2, record re-check (CODE MOVED, as expected), OOS
  run 4 (identical to run 3).
- Harness at 40dc969: 48 module commands pass, 59 consistency checks pass,
  pyflakes clean. Every guard added today was falsified (about 130 mutants).

## The unattended system

- Scanner: 693 runs; the five today (11:07, 13:07, 15:07 ET dispatches, the
  16:10 schedule and the 16:20 dispatch) all green. Exit monitor: 467 runs,
  green. The 16:20 post-close log: watchlist snapshot 10-25 (3 days old),
  SPY Bull, IV snapshot "already recorded today" (the schedule landed on
  time at 16:10 for once and did the pass first), 5 departed names fetched
  for IV grading, basket already recorded, coverage OK, state pushed.
- **Every scan today ran code from before today's merges** (the post-close
  pass checked out 5536198, after PR #152 and before E, C, B, F, G, 32).
  Tomorrow's 11:07 scan is the first under `4ddd12a3095f`. Nothing in the
  record was produced by rules that were not declared at the time.
- Two post-close passes ran today (schedule 16:10, dispatch 16:20). The
  second found everything recorded and wrote one heartbeat. Harmless.

## The record, read both ways

724 rows: 469 signals, 164 outcomes (56 win, 105 loss, 3 void), 89 scans,
2 controls (the first, on today's live rows). Taken: live 77, pool 219,
pullback 21. HQ rows: 0, alerts: 0, journal rows 14 (none attachable),
open positions 0.

| bucket | episodes | settled | open | mean R | wins | gate |
|---|---|---|---|---|---|---|
| pool (the population) | 42 | 26 | 16 | −0.332 | 10 | 26 of 349 |
| live (the alerting view) | 18 | 16 | 2 | −0.246 | 5 | — |
| pullback | 9 | 4 | 5 | −0.517 | 1 | — |

H1′ rank tier (amendment D): 9 episodes, 7 settled, −0.924 R, against the
rest 33 / 19 / −0.114. Seven settled episodes is not a reading; the sign is
noted because it is the opposite of H1′'s prediction. 14 of 56 wins are
≥ 1.5 R (max 3.35 R): the 4-ATR target is reached, as the 10-26 review
found.

Arrival: 42 pool episodes on 12 pool bars, 3.5 a bar; 349 settled needs
about 100 more bars, ~4.5 months, unchanged.

## Findings

### F1. The app's contract window has a floor but no ceiling

`option_chain.get_full_chain_data(ticker, min_dte)` reads up to three
listed expiries at or beyond `MIN_DTE` (now 21) and the selector may pick
the third, which on a monthly-only name can be 50–60 DTE. Since BACKLOG 32
the journal refuses a signal entry above 45 DTE, so the app can suggest a
contract it will then refuse to log. The scanner's window has both bounds
(21–45, from the structure); the app's selector should take the same
`max_dte`. Small, one parameter, pinned by the existing hygiene check.

### F2. The first alerts of the record are due, and the pool cap will be tested

Under amendments F and G, 30 of the 277 tagged rows so far would have been
HQ, about 1.5 a pool bar. Alerts are sent only by the in-session scans (the
post-close pass records and never alerts), so the first can come from
tomorrow's 11:07 scan. `POOL_ALERT_CAP` is 5 a session, the live bucket is
uncapped at 8 names, and the 4-hour per-name cooldown applies. What to read
after tomorrow: `forward_log` alert rows (delivered true/false), the pool
scan's "cap" line if it binds, and whether the Telegram text's "Signal bar"
date matches the row's key.

### F3. H1 and H1′ now read from amendment F forward, and nothing says so in `report()`

The amendments fix the reading rule (HQ questions from 2026-10-28, hash
`81fd95d84cfc` and later), but `forward_log.report()` still prints the
H1′ line over every row. Until there are HQ rows this is moot; when there
are, the line should carry the hash cut or split by config. A reading
change, no amendment.

### F4. The spread arm's first rows land tomorrow, and the quote quality is unknown

`spread_log.jsonl` is empty, as expected: the arm merged after today's
post-close pass. Tomorrow's pass should log "spread arms: 8 cycle(s)
opened" or the reason not. The one unknown the pre-registration names is
Yahoo's after-close bid/ask: a zero bid or a crossed quote leaves `market`
null with the reason, and the first week's `market_reason` counts say
whether the credit ratio can be read at all.

### F5. First IV premium rows are still due 2026-11-09

112 IV snapshots (8 a day), 0 graded; the departed-name fetch ran today (5
fetched, 0 deferred, 0 held). Unchanged from the 10-26 review.

### F6. Documentation drift from today's pace

The BACKLOG "still pending" line still listed BACKLOG 32, 33 and 23 after
they were decided, and the 10-26 review's status note said F1 waits on the
owner's wording after amendment C landed. Both fixed in this commit.

### F7. Token runway

The forcing check fails from 2026-12-16 until a dated BACKLOG line says
both PATs were rotated (app 2027-01-05, dispatch 2027-01-06). Seven weeks.

## Improvements, in the order they pay

1. **F1**: give the app's selector the structure's `max_dte` (one
   parameter, one check extension). Do with the next "go".
2. **F3**: `report()` prints the HQ lines from the F cut, or by hash.
3. **Spread arm hygiene after the first week**: a `market_reason` tally in
   `spread_log.report()` and a note in the pre-registration if quotes are
   unusable after the close (the fix would be a pre-open fetch, which is a
   different record).
4. **The second post-close pass**: when the schedule lands on time the
   dispatch repeats the pass; harmless, but the dispatch could check for
   today's heartbeat first. Low value.
5. **A readiness line in the post-close log** for the forward readings
   ("pool 26 of 349; basket 1 of 52; spread 0 of 12; IV premium first
   grade 11-09") so the owner sees the clocks without opening files.

## Pending items, in the order they block things

1. Tomorrow's scans: the first rows under `4ddd12a3095f`, the first
   alerts, the first spread-arm cycles. Read the 11:07 and 16:20 logs once.
2. The first signal trade logged under BACKLOG 32's rules (starts the
   premium-basis reading). The form refuses anything outside the structure
   and says why.
3. F1 (app `max_dte`), F3 (report cut), both small.
4. 2026-11-09 / 11-10: first IV premium rows; F5.
5. 2026-12-16: token rotation.
6. The forward clocks: spread arm first reading at 6 graded cycles per arm
   (~April 2027), RS basket at 26 weeks (~April 2027), pool gate at 349
   settled episodes (~March 2027).

## Not done here

No engine, rule or threshold was changed by this review. Two documentation
notes were corrected (F6). No research result was produced.
