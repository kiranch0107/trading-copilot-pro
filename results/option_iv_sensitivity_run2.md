!! SUPERSEDED BY GROUP B (BACKLOG 36, built 2026-10-28). The option engine in this
!! file counted DTE in SESSIONS (priced dte/252, held dte rows), skipped the entry
!! session's close, and settled a trade cut off by the sample's end as if it had
!! expired. The engine now runs on the calendar (expiry = entry + dte days, time
!! value over 365), marks the entry session, and leaves sample-end trades OPEN.
!! RE-RUN OWED on the frozen bars: results/option_iv_sensitivity_run3.md. Until it lands, no constant may
!! be read from this file (consistency_check refuses a SUPERSEDED source).
# Option engine IV sensitivity — run 2 (the no-look-ahead code)

**Run by the owner 2026-10-28 on the laptop whose `.bar_cache/` holds the
frozen bars**, the same four commands as run 1 (`option_backtest.py --sweep
--iv-mult m`, m = 1.00 / 1.15 / 1.30 / 1.50, the engine's seven names, 5y),
plus the default run (`python option_backtest.py`, IV: 1.15x realised, the
engine's constant; the sweep files each state their own multiplier). Files:
`option_iv_sensitivity_run2_1.00.txt` … `_1.50.txt`,
`option_backtest_default_run2.txt`. Code at main after PR #149.

**Why it was re-run.** On 2026-10-19 (BACKLOG 35, group 5)
`option_backtest.simulate_option_trade` stopped reading the entry bar's own
close into the realised-vol window that prices the entry premium — one bar
of look-ahead. Every modelled premium in run 1 carried it. The seven files
that printed those premiums were caveated on 2026-10-19 and the re-run was
owed.

## What moved

Win rate at TP+100 / SL−50 by IV multiplier (the `OPT_WIN_RATE_BY_IV_MULT`
table), run 1 → run 2:

| multiplier | run 1 (393 trades) | run 2 (398 trades) |
|---|---|---|
| 1.00 | 27.7 % | 26.9 % |
| 1.15 | 25.4 % | 25.1 % |
| 1.30 | 24.7 % | 24.1 % |
| 1.50 | 20.6 % | 20.4 % |

Expectancy at 1.15x (the engine's constant), per TP level, run 1 → run 2:
+50: −6.60 → −6.77 %; +75: −4.79 → −6.47 %; +100: −3.10 → −4.12 %;
+150: −3.84 → −4.75 %; +200: −3.05 → −3.36 %; +300: −2.68 → −4.15 %.
Every row is negative in both runs; run 2 is worse by 0.2–2.3 points,
which is the direction removing look-ahead should move a premium that was
priced with one bar of hindsight.

At 1.00x (no premium): +150 is +2.80 % (run 1: +3.82 %), +200 +1.18 %
(+3.51 %), +300 +1.47 % (+2.12 %); +50 to +100 negative in both runs.

## What held

- **Ordering of take-profit levels by win rate is the same at every
  multiplier**: a wider target is hit strictly less often. Unchanged.
- **The sign of expectancy at TP ≥ +100 is still the constant's, not the
  data's**: positive with no premium (1.00), negative at 1.15 and above.
  Unchanged; Part B (the forward single-name premium) still decides it.
- **+50 and +75 are refuted at every multiplier.** Unchanged.
- **No structure clears its realised breakeven at 1.15x.** Unchanged: at
  TP+200, 22.1 % against a realised breakeven of 23.9 % (run 1: 21.9 %
  against 23.8 %).

## What changed in the repo, as one change with this re-run

`risk_params.OPT_WIN_RATE` 0.249 → 0.251; `OPT_WIN_RATE_BY_IV_MULT` and
`OPT_SWEEP_BY_TP` are run 2's rows, digit for digit, and
`consistency_check.check_option_iv_single_source` now reads them from the
files `risk_params.OPT_SWEEP_SOURCE` names (the run-2 files) and refuses a
caveated file. The spread-ceiling arithmetic in `risk_params` is restated
at 25.1 %: the 0 % breakeven of 33.3 % is cleared by nothing, so
`MAX_OPTION_SPREAD_PCT` stays the loss cap it already was. The app's
captions read the function, so they moved with it.

## The 393-against-398 question, settled by the cache manifest (same day)

The owner's `python bar_cache.py --info` after run 2: every one of the
seven engine names at the 5-year window (`TSLA/NVDA/AAPL/MSFT/AMZN/META/ROKU_1d_5y_raw`)
was fetched **48 days before 2026-10-28, i.e. on 2026-09-10**, bars
2021-09-10 → 2026-09-08 (ROKU to 09-09), and none was refreshed since.
So:

- **Run 2 read the frozen 2026-09-10 bars.** That is why it has 398
  trades, the 2026-09-10 count.
- **Run 1 (2026-10-08) did not.** Its 1.00x file reports a ROKU bar dated
  2026-10-08 dropped as still forming, a bar this cache does not hold. Run
  1 was a live fetch whose 5-year window ran a month later at both ends.
  Run 1 and run 2 therefore differ in BARS as well as code, and the
  run-1 → run-2 movements above are mostly the window, not the fix.
- **The clean, same-bars, code-only comparison is the 2026-09-10 sweep
  (the rows `risk_params.OPT_SWEEP_BY_TP` held until today) against run
  2, both at 1.15x on 398 trades:**

| TP | 09-10: win / avg win / avg loss / exp | run 2: win / avg win / avg loss / exp |
|---|---|---|
| +50 | 32.7 / +74.4 / −46.7 / −7.10 % | 33.2 / +74.6 / −47.2 / −6.76 % |
| +75 | 26.9 / +103.8 / −45.7 / −5.48 % | 26.6 / +102.4 / −46.0 / −6.53 % |
| +100 | 24.9 / +120.0 / −45.1 / −3.99 % | 25.1 / +119.3 / −45.5 / −4.14 % |
| +150 | 22.1 / +135.4 / −44.6 / −4.82 % | 22.6 / +132.8 / −45.0 / −4.82 % |
| +200 | 21.9 / +143.0 / −44.6 / −3.52 % | 22.1 / +142.8 / −44.9 / −3.42 % |
| +300 | 21.9 / +144.7 / −44.6 / −3.14 % | 21.9 / +141.3 / −44.8 / −4.04 % |

  The look-ahead fix alone moves the win rate by at most 0.5 points and
  the expectancy by at most 1.1 points, with no row changing sign. Every
  finding above holds on this comparison as well.

- The thesis test's 12 names at the 10-year window were also fetched
  2026-09-10/11 and never refreshed, so run 1 (2026-09-12) and run 2 of
  `thesis_test` read the SAME bars; that comparison was already clean.

`option_backtest.py` now prints the fingerprint on every run, so this
reading never has to be reconstructed from a manifest again.

## One thing this run does not re-read

The default run's exit mix (THESIS 159, STOP 123, TARGET 70, TIME 45,
EXPIRY 1 of 398) is consistent with `option_decompose`'s 42.6 % thesis
share; nothing here re-reads that study.
