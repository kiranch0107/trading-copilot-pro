# PRE-REGISTRATION — ADX 30 instead of 35, on the config the scanner runs (BACKLOG 33)

**Owner's question 2026-10-14: "can we do a backtest on existing parameters
but ADX as 30 instead of 35?" Written and committed BEFORE the run.** The run
is the owner's, on the laptop whose `.bar_cache/` holds the frozen bars (web
sessions cannot reach Yahoo). Everything below is fixed; it may be AMENDED in
a dated section at the top only until the result file is committed.

## What is being asked, precisely

The scanner alerts on the high-quality tier only, and that tier needs
`ADX >= 35` (`signal_core.DEFAULTS.adx_min`). Of the 46 live signals and the
48 pool signals recorded so far, none passed the tier, and ADX failed every
tagged row (`logic_review_2026-10-13.md` §(d)). Lowering the threshold to 30
would admit exactly the signals whose signal-bar ADX lies in **[30, 35)** and
nothing else, because the ADX-30 set is the ADX-35 set plus that band.

So the question "is 30 better than 35" is the question "what do the [30, 35)
trades earn". That band is the primary measurement. The full ladder is kept
as the secondary one, because the earlier run found the ladder's shape was
the evidence, not any one cell.

## Why the 2026-09-10 run does not already answer it

`adx_retest_run1.md` measured ADX 30 at −0.062 R (867 trades) and 35 at
+0.004 R (466), with the ladder running backwards (ρ = −0.70). But that run
was on the RECORD config (stop 1.0 / target 3.0 ATR, regime off, both
sides, `backtest.RESEARCH_VS_LIVE`) and before the gap-fill fix (BACKLOG 21),
and it compared nested aggregates. The owner's question is about the LIVE
config: stop 1.25 / target 4.0, volume 1.2×, regime on, longs only (the
direction gate). None of those is a detail; BACKLOG 23 exists because the two
configs were conflated for a year. **For this run the owner's question
decides BACKLOG 23's open "which config" for the first time: the live one.**

## The run, fixed

```
python adx_retest.py --config live --longs-only --levels 0,20,25,30,35 --band 30,35
```

- `--config live`: every key `backtest.build_signal_params()` reads is taken
  from `signal_core.DEFAULTS` through `backtest.CFG_TO_PARAMS`; the selftest
  asserts the built params equal the scanner's exactly. Only `adx_min` is
  swept. `max_hold` 20 (the forward grader's hold), cooldown 3, slippage
  2 bps a side, commission 0, raw prices, bar cache on.
- `--longs-only`: Bearish trades dropped after the run, as the live direction
  gate drops them.
- Tickers: the module default, `SWEPT_MIX_12` (TSLA, NVDA, AAPL, MSFT, AMZN,
  META, SPY, GOOGL, AMD, NFLX, ORCL, CRM), 10 years. This data has been read
  many times (`data_reservation.lock.json`: every tranche spent). **This run
  can refute; it cannot confirm.** A pass here would need a clean tranche to
  mean anything, and none exists.
- The bar-cache fingerprint is printed with the result and recorded in the
  result file. Two runs with the same fingerprint read the same bars.

## The bars, fixed

**Primary — the band [30, 35), longs only, from the ungated (ADX 0) run,
cut by the signal bar's ADX.** Lowering the threshold is SUPPORTED only if
ALL of (`adx_retest.judge_band`):

1. the band holds at least `MIN_TRADES` = 150 trades;
2. its mean R is positive and p ≤ 0.05 (one pre-specified test, no
   correction needed because there is one primary);
3. |mean R| is at or above the band's detectable effect at 80 % power.

Anything else is NOT ESTABLISHED. An INCONCLUSIVE band (clause 3) is not
"no edge" and will not be written up as one.

**Secondary — the ladder 0 / 20 / 25 / 30 / 35, the module's standing
three-clause bar** (Holm across the five, power, Spearman ρ ≥ 0.6). This
says whether ADX carries information on the live config at all. It is
secondary because the owner asked about one threshold, not about the filter.

**Expected, stated in advance.** From run 1 and from
`setup_population_run2.txt` (ADX d = −0.01 between winners and losers on
1,457 setups): the band's mean R is near zero and under its detectable
effect, so the honest outcome is NOT ESTABLISHED on clause 3. If that is the
result, the consequence is BACKLOG 24's: the tier gates intake on a feature
with no measured information, and 30 versus 35 is a choice about how many
alerts the owner wants from a signal with no measured edge, **not** a
measured improvement. That choice stays the owner's. This document does not
pre-commit to changing `adx_min` on any outcome.

**What would change the reading.** A band that clears all three clauses
on spent data is a lead for the forward record (a fourth bucket or a
threshold change recorded forward), not a licence to change the scanner on
the back of a 10-year re-read.

## Stated limits

- Bands are cut from one ungated run, so the 3-bar cooldown is the
  ungated run's. A gated re-run at 30 would admit a few bars the ungated
  cooldown consumed. The ladder rows ARE gated re-runs, so the two agree
  up to that cooldown effect; neither is corrected to the other.
- Earnings blackout is not applied in the backtest (no historical calendar);
  the scanner applies it live. The band is therefore slightly wider than the
  live tier would be.
- The volume 1.2× "Strong" leg gates the live tier too; it is in the cfg
  (`volume_mult`) and feeds the `strength` tag, but the backtest trades the
  base signal gated on ADX and regime only, as it always has. This run
  measures the ADX leg alone, which is the leg the question is about.

## Result

_(to be filled by the run: `results/adx_live_run1.txt`, raw output, with the
fingerprint; and a dated reading here or in `adx_live_run1.md`)_
