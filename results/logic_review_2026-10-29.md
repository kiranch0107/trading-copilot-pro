# Logic review — 2026-10-29 (evening): the rules, criteria and clocks probed at their boundaries

Owner's request: "perform thorough testing on our conditions, criteria,
logic". Read against `main` at 4e4da3b. Four probe scripts (not committed;
scratch, ~140 assertions) drove the rule functions at every boundary they
declare, the grader on constructed bar paths, the calendar and windows
across half days, holidays and the DST change, the structure refusals,
the spread arithmetic and the exit monitor with stubbed quotes; then the
real record was audited for internal consistency. Nothing was fetched
from Yahoo. No engine, rule or threshold was changed by this review.

## What held (the boundaries behave as declared)

- **Base rules** (`signal_core`): RSI bands strict at 30/75 long and 25/70
  short, and the refusal text names the band in force; the volume floor
  passes at exactly 0.70x and fails below; a missing volume average
  blocks (fail closed); NaN RSI, ATR or ADX blocks or fails the leg,
  never passes.
- **Gates compare unrounded** (amendment E): ADX 29.96 displays 30.0 and
  fails; 30.0 passes. "Strong" needs RSI strictly above 60 and volume at
  or above 1.2x.
- **HQ tier**: R:R at or above 1.0, every filter, no Strong leg. Earnings
  blocks the tier, not the signal. "Bear", "bearish " and "Bear market"
  all block a long; Neutral/Unknown do not.
- **Levels**: a 10-bar low above price falls back to the ATR stop; a
  structural stop inside the ATR stop is the tighter one; the zero-risk
  gate is relative (0.3 % of price, floor 5 cents); recorded R:R equals
  |target − entry| / |entry − stop| on all 511 signal rows.
- **Grader** (`resolve_signal`): stop before target on the same bar; an
  exact touch is a hit; a gap through the stop exits at the worse open, a
  gap through the target at the better; timeout on bar 20; 19 bars plus
  45 stale days voids as no_bars; the 2 % basis check voids at 2.01 % and
  grades at 1.99 %.
- **Episodes**: a 4-day gap joins (Thanksgiving week, a holiday Monday),
  5 splits. Controls mirror the distances on the short side. The legacy
  "not high-quality" rows count as taken only before 2026-10-08.
- **Calendar**: 16:00:00 is open and not post-close, 16:00:01 is both
  closed and post-close, the window closes at 18:00:00 inclusive; the
  three module calendars (signal_core, scanner, exit_monitor) and the
  workflow's copy are identical through 2027, with 2027-12-24 a holiday
  and not a half day.
- **Half days**: the 16:20 dispatch is outside the 13:00–15:00 window on
  2026-11-27 and 2026-12-24, but the 13:07 dispatch lands inside it and
  the workflow guard mirrors the window, so the record pass runs at 13:07
  on those days (seven minutes after the close, the same exposure as
  16:10 on a full day).
- **DST (2026-11-01)**: the primary dispatches are in America/New_York at
  cron-job.org and do not move; the backup UTC crons shift to 10:07–14:07
  ET and the 21:20 UTC slot becomes the in-window 16:20 EST one, exactly
  as the workflow comments say.
- **Structure** (`risk_params.option_structure_violation`): 20 refused,
  21 and 45 accepted, 46 refused; rules as strings accepted; 199.9999
  refused; missing rules refused; extra keys ignored. `in_dte_window`
  agrees at the same four points.
- **Spread arithmetic**: settlement owed 10 at spot 80 on a 95/85 put
  spread, 0 at 95, the condor's call wing capped at the width; mid and
  executable credits as declared; a zero bid, a crossed quote or a NaN
  on any leg leaves `market` null with the reason; bid == ask accepted.
- **Exit monitor** with stubbed quotes on the NOW position ($0.25): mid
  0.12 is STOP (−52 %), 0.13 holds (−48 %), 0.75 is TARGET, a last-only
  quote skips the price rules and is surfaced, DTE 7 is TIME and 8 holds,
  STOP outranks TIME, and TIME fires without any quote.
- **The record**: chain verifies; every outcome references an existing
  row, once per basis, on a taken row; signal keys unique; every control
  references one taken row with a bar strictly before the signal bar;
  every alert keys to a recorded signal; every config hash is declared;
  no Bearish row is taken.
- **PR #169 live**: the 16:09 ET pass on the merged code stamped its
  heartbeat, printed `clocks: pool 32/349 settled episodes; bar revisions
  0; basket 0/52 weeks graded (1 recorded); spread 0/12 cycles graded
  per arm (8 opened, 0 unquoted); IV premium 0 graded of 120 snapshots`
  and opened the eight spread cycles. The 16:20 dispatch: see L6.

## Findings

### L1. The alert cooldown is a race measured in seconds (fix recommended)

`ALERT_COOLDOWN_HRS` = 4 equals the spacing of the 11:07 and 15:07 ET
dispatches. Today the 15:07 run checked ACN at 19:07:26.6Z against an
alert stamped 19:07:33Z minus four hours, and NOW at 19:07:29.8Z against
19:07:38Z: both suppressed by 7–8 seconds. A dispatch ten seconds later,
or a slower checkout, re-sends the same bar's alert, and the alert row
would record it as a second delivered alert on the same key. The
cooldown's purpose is "not the same signal twice"; the signal's identity
is its key (`ticker|trend|bar_date`), which the alert row already
carries. **Proposed fix:** suppress when an alert row with this key is
already delivered (read from the forward log, the same chain that writes
it), keep the 4-hour stamp only as the Telegram-outage backstop. An
intake change, not a rule change; no amendment.

### L2. A fill a few cents above the stop grades on a near-zero risk (owner decision)

`resolve_signal` voids an entry that gaps PAST the stop or target and
accepts one that lands a cent inside. The R unit is then |fill − stop|,
so the row's R is enormous in either direction. In the record: KO
2026-10-26 filled 0.048 above its stop (2.5 % of the recorded risk, 0.05 %
of price), ANET 2026-10-22 at 4.5 %, TMO 2026-10-20 at 3.9 %. All three
were stopped (−1.38, −1.36, −1.15 R, modest only because exit ≈ stop ≈
fill); had any reached its target the row would have booked +20 to
+120 R and owned the pool mean on its own. `evaluate()` refuses a setup
whose risk is under max(5 cents, 0.3 % of price); the grader applies no
such floor. **Options:** (a) void a fill whose risk is below the same
floor, under `gapped_before_fill` (reason "fill inside the risk floor"),
by amendment; (b) leave it and read the R distribution's tails at the
gate. (a) is the same rule the signal already applies to itself. Three
rows of 182 are affected today, all losses, so the re-read moves nothing
yet.

### L3. The first spread cycles price the model well below the market (not a reading, noted now)

Eight cycles opened at the 10-29 close, every leg quoted inside a 3-cent
spread. Market mid / model credit: SPY put spreads 0.645 and 0.645, QQQ
put spreads 0.771 and 0.791, SPY condors 0.463 and 0.477, QQQ condors
0.752 and 0.774; executable / model 0.43–0.73. The pre-registration
predicted 0.85–1.15 for puts and 0.80–1.00 for condors and names a
12-cycle interval entirely below 0.85 as voiding the backtest verdict.
One cycle per arm is not a reading and nothing is acted on. Where the
gap sits, from the model's own leg prices on the SPY row (^VIX 16.5, 32
days): the short 731 put is 2.90 model against 3.19 market and the long
721 put 1.78 against 2.46, so the market's put skew is STEEPER than the
linear 20 points and the long wing is dearer, which narrows the spread
credit (model 1.12, market 0.73; the 731 put's market-implied vol is
18.1 against the model's 17.5). On the call wing the linear skew runs
the wrong way: the 808 call is 3.09 model against 0.71 market and the
818 call 1.72 against 0.28, which is why the condors read 0.46–0.48 on
SPY. The condor-below-puts prediction holds in direction; the level does
not. To read at 6 graded cycles per arm, as declared; the first
settlement is the 11-30 close. If the first reading confirms this, the
engine's skew model is the thing to fix before any further backtest, and
the pre-registration already says the verdict is VOID until it prices
what the market quotes.

### L4. The NOW alert went out on a row the record tags hq = False, under a pre-F hash

The post-close pass of 10-28 ran the old code (checkout 5536198) and wrote
NOW's row (seq 700) under `c357a0d01ff5` with `hq_fail` = not Strong, ADX
(34.8 < 35), Earnings. The 11:07 scan of 10-29 re-read the same bar under
`4ddd12a3095f`, found it HQ (ADX ≥ 30, no Strong leg, the earnings window
had rolled past) and alerted; `record_signal` deduped on the key, so the
row stands at hq = False. The revision note (PR #167) was not yet live for
that scan, so no revision row says so. Consequence for the readings: none
for H1/H1′ (the row's hash is excluded by `HQ_READ_EXCLUDES`); the trade's
premium outcome attaches to seq 700 as before. Recorded here so the
"alert on a non-HQ row" audit line has its explanation; from 10-30 the
revision row carries it.

### L5. The resistance cap can push a long's target under its entry (minor, documented)

`long_levels` caps the target at 0.995 × the 20-bar high when that high
is at least one ATR above entry. When ATR is under about 0.5 % of price
(0.52 % at $100, 0.51 % at $500), 0.995 × (entry + ATR) is below entry,
the target clamps to entry + 2 cents and the bar is blocked as "rr". The
short side mirrors it through 1.005 × support. No recorded row carries
the clamp (none has target = entry + 0.02); the effect is a silent
block on very low-volatility names, not a false signal. Left as is; a
fix would be a rule change on a path the pre-registration freezes.

### L6. The 16:20 dispatch after an on-time 16:10 schedule

Filled in below from the run that followed this review's probes.

## Pending items after this review

1. L1 (cooldown by key) — small, recommended now; first exposure is
   tomorrow's 15:07 run.
2. L2 — the owner's choice between (a) and (b); (a) is an amendment to
   the grading table.
3. L3 — nothing until 6 graded cycles per arm (first settlements 11-30;
   six cycles ≈ April 2027). Read the `unquoted` tally weekly.
4. The 10-30 11:07 scan: the first revision rows against today's 16:09
   pass (CME live; the pool's rows).
5. IV premium rows 11-09/11-10; token rotation by 12-16; the forward
   clocks as the `clocks:` line reports them.
