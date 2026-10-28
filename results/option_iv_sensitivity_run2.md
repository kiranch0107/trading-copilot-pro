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

## Two things this run cannot settle

1. **398 trades against run 1's 393.** 398 is also the 2026-09-10 count.
   Run 1's 1.00x file says ROKU had a bar dated 2026-10-08 dropped as still
   forming, so run 1 read bars fetched that day; that bar is settled now
   and the cache is never-expiring, which accounts for at most ROKU's last
   bar, not five trades. The option engine printed no bar-cache fingerprint
   until this change, so the two runs cannot prove they read the same bars.
   `option_backtest.py` now prints the same `data: v2-…` line the share
   backtest prints, at the end of every run, so run 3 will be comparable.
   Until then the row-to-row movements above are read as "the fix plus
   possibly a bar-set difference", and the findings that held do not
   depend on which.
2. **The default run's exit mix** (THESIS 159, STOP 123, TARGET 70, TIME
   45, EXPIRY 1 of 398) is consistent with `option_decompose`'s 42.6 %
   thesis share; nothing here re-reads that study.
