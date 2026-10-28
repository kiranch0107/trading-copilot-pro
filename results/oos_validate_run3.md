# Out-of-sample validation — run 3 (2026-10-28), the reproduction owed by the shared regime minimum

**Run by the owner 2026-10-28 on the laptop, `python oos_validate.py`,
frozen config hash `a3c94a897f4d6f11` (unchanged since the 2026-09-02
re-lock), the same 12 held-out names, 15 years, regime ON, raw prices.**
Files: `oos_validate.ledger.json` (entry 3), `oos_validate_run3.txt` (the
harness summary). Owed since group C (2026-10-28) moved
`backtest.build_regime_series` from a 50-bar to the live 200-bar minimum.

## The three runs on record

| run | date | bars | trades | expectancy | PF | t | p | 95 % CI | verdict |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-08-22 | adjusted (unreproducible) | 591 | −0.014 R | 0.98 | −0.25 | 0.80 | [−0.124, +0.096] | FAIL |
| 2 | 2026-09-02 | raw, frozen (cache 09-02) | 585 | −0.021 R | 0.97 | −0.37 | 0.71 | [−0.133, +0.091] | FAIL |
| 3 | 2026-10-28 | raw, frozen (same cache) | 579 | **−0.112 R** | **0.85** | −1.76 | 0.079 | [−0.237, +0.013] | FAIL |

The verdict is the same: the frozen config's edge did not carry to the
held-out names. The reading is worse: −0.112 R against −0.021 R, and the
interval now excludes most of the positive side.

## What changed in the engine between run 2 and run 3

Run 2 and run 3 read the same frozen bars (the 12 names and SPY at the
15-year window were fetched 2026-09-02 and the cache never expires; the
owner's manifest of 2026-10-28 shows them 56 days old and unrefreshed). So
the whole movement is code. Three engine changes landed in between:

1. **Gapped levels fill at the open (BACKLOG 21, 2026-10-08).** Run 2 filled
   a stop that was gapped THROUGH at the stop, so no loss was worse than
   about −1 R. Real stops fill at the open. On the record's 10-year long
   side the same fix moved +0.076 → +0.042 R, one trade in six having a
   level gapped through. The held-out names are higher-volatility tech
   (AMD, AVGO, MU, NFLX, LRCX) and gap more, so a larger move here is the
   expected direction. **This is the change that can move expectancy by
   tenths of an R; it moves no trade count.**
2. **Unrounded gates (amendment E, 2026-10-28).** ADX 34.96 no longer
   passes 35; R:R 0.496 no longer passes 0.5. This removes bars within
   rounding distance of a threshold: **this is where the six fewer trades
   (585 → 579) come from**, in whole or in part. Its effect on expectancy
   is bounded by those six trades.
3. **Shared regime minimum (group C, 2026-10-28).** The first 150 SPY bars
   of the window (2011-09 → 2012-04) carry no regime view now where run 2
   had a 50-bar verdict. It can only ADD trades there (no view = no
   filter) or leave them; it cannot remove any. With 579 < 585 it is not
   what moved the count, and the trades it can touch are the earliest 4 %
   of the window.

The three are not separated by this run and will not be: the harness
itself prints that each repeat against the held-out set spends some of
its independence, and this is already run 3. Attribution arms, if they
are ever worth running, belong on the record set (`record_recheck`), not
here. The bound stated above is what the record can honestly say.

## What this means

- The 591-trade / 585-trade FAIL that the forward pre-registration and
  `longshort_split.md` cite is now a 579-trade FAIL at −0.112 R on the
  code that fills gapped stops at the open and compares its gates
  unrounded. The older number was flattered by the fill rule; the
  conclusion those documents drew (no demonstrated edge; the forward
  record is the only route to evidence) is strengthened, not changed.
- `risk_params` carries no constant from this run; nothing moves.

## The run's own provenance (raw output pushed the same day)

`results/oos_validate_run3_raw.txt` is the backtest's full output for run
3. Its fingerprint line:

```
data: v2-38fc8d83a7275823  (13 series, 13 cached / 0 fetched, oldest 56d)
```

Thirteen series (the 12 names and SPY), every one from the cache, the
oldest 56 days old on 2026-10-28, i.e. fetched 2026-09-02, the day of run
2. So run 2 and run 3 read the same bars; the movement between them is
code, as the section above assumed from the manifest. Bars tested:
43,024. The regime filter blocked 3,857 bars (28.1 % of the eligible
ones).

The fill tally: **92 of 579 trades had a level gapped through, 61 stops
and 31 targets filled at the open.** One trade in six, the same share the
record's 10-year re-run found. Sixty-one stops filled beyond −1 R are the
mechanism behind most of the −0.09 R move from run 2, which filled every
one of them at the stop. The 31 gapped targets go the other way (a better
fill than the target) and are already in the number.

From this run on, `oos_validate.py` prints both lines in its summary and
records them in the ledger entry, so the raw file no longer has to be
pushed separately.
