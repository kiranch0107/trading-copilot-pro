# Option engine IV sensitivity — run 3 (the calendar-clock engine)

**Run by the owner 2026-10-28 on the laptop whose `.bar_cache/` holds the
frozen bars** (`data: v2-78390259672cedbe`, 7 series, 7 cached / 0 fetched,
oldest 48 days: the same 2026-09-10 bars as run 2, now printed by the engine
itself), the same four commands as runs 1 and 2 (`option_backtest.py --sweep
--iv-mult m`, m = 1.00 / 1.15 / 1.30 / 1.50, the engine's seven names, 5y),
plus the default run (`python option_backtest.py`, IV: 1.15x realised).
Files: `option_iv_sensitivity_run3_1.00.txt` … `_1.50.txt`,
`option_backtest_default_run3.txt`. Code: branch
`claude/trading-code-review-wt48q8` @ `c1de652` (BACKLOG 36 group B).

**Why it was re-run.** The engine that produced runs 1 and 2 counted days
to expiry in SESSIONS: a "30-DTE" contract was priced with 30/252 of a year
of time value and expired 30 sessions (six weeks) after entry, the TIME exit
fired at 7 sessions left, the entry session's close was never marked, and a
trade cut off by the sample's end was settled at intrinsic as if it had
expired. Group B put the engine on the calendar: expiry is entry date + 30
calendar days, time value is days left over 365, the entry session's close
is the first mark, the TIME exit counts calendar days, expiry settles on the
first session on or after the date, and a trade whose expiry lies past the
sample's end is OPEN and never scored. Run 3 reports no OPEN trade: every
signal's 30 days fit inside the frozen window.

## What moved

Win rate at TP+100 / SL−50 by IV multiplier (the `OPT_WIN_RATE_BY_IV_MULT`
table), run 2 → run 3, same bars:

| multiplier | run 2 (398 trades) | run 3 (395 trades) |
|---|---|---|
| 1.00 | 26.9 % | 30.6 % |
| 1.15 | 25.1 % | 28.1 % |
| 1.30 | 24.1 % | 26.1 % |
| 1.50 | 20.4 % | 23.8 % |

Every win rate rose by two to four points, and every average loss grew with
it (1.15x: −45.5 % → −51.5 % at TP+100). Both are the clock: a contract
priced for 30 days instead of 30 sessions costs about 15 % less, so the same
underlying move is a larger share of the premium in both directions. The
realised average WIN at the wide targets fell (TP+200: +142.8 % → +134.3 %)
because the TIME exit at 7 calendar days left now fires about 16 sessions in,
not 23, so fewer winners run. Average hold is 7.8 sessions.

Expectancy at 1.15x per TP level, run 2 → run 3 (as the engine printed it):
+50: −6.77 → −5.07 %; +75: −6.47 → −6.50 %; +100: −4.12 → −5.86 %;
+150: −4.75 → −2.49 %; +200: −3.36 → −4.65 %; +300: −4.15 → −3.38 %.
Every row negative in both runs. Exit reasons on the default run: STOP 194,
TIME 72, THESIS 65, TARGET 64.

At 1.00x (no premium): +100 is now −0.19 % (run 2: +0.2 %, positive),
+150 +2.24 %, +200 +4.66 %, +300 +3.45 %; +50 and +75 negative.

Three trades fewer (398 → 395) is `backtest.compute()`'s fixed warm-up head
and indicator-gap rows (group B, the same change that moved the record
cuts by 3–9 trades; `record_recheck_groupb_run1.txt`), not the clock.

## What held

- **Ordering of take-profit levels by win rate is the same at every
  multiplier**: a wider target is hit strictly less often. Unchanged.
- **The sign of expectancy at the wide targets is still the constant's, not
  the data's**: positive with no premium (1.00), negative at 1.15 and above.
  The line now sits at TP ≥ +150 rather than ≥ +100 (the 1.00x +100 row is
  −0.19 %, a rounding distance from zero). Part B (the forward single-name
  premium) still decides it.
- **+50 and +75 are refuted at every multiplier.** Unchanged.
- **No structure clears its realised breakeven at 1.15x.** Unchanged: at
  TP+200, 25.1 % against a realised breakeven of 27.6 % (run 2: 22.1 %
  against 23.9 %); at TP+100, 28.1 % against 31.7 %.

## What changed in the repo, as one change with this re-run

`risk_params.OPT_WIN_RATE` 0.251 → 0.281; `OPT_WIN_RATE_BY_IV_MULT` and
`OPT_SWEEP_BY_TP` are run 3's rows, digit for digit; `OPT_SWEEP_SOURCE`
names the run-3 files, and `check_option_iv_single_source` refuses a
SUPERSEDED file as well as a look-ahead one, so run 2 can no longer be read
as a source. The spread-ceiling arithmetic is restated at 28.1 %: the 0 %
breakeven of 33.3 % is still cleared by nothing (by 5.2 points now, not
8.2), so `MAX_OPTION_SPREAD_PCT` stays the loss cap it already was. The
app's captions read the function, so they moved with it.

## Not done

The IV multiplier itself is still an assumption (BACKLOG 25, Part B). The
run is the third on the same seven names and bars; nothing here was
searched over, but a reader should know the number of looks.
