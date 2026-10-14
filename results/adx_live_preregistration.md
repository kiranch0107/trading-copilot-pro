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

## Result — run 1, 2026-10-14, NOT ESTABLISHED on both bars

**Run by the owner on the laptop, `results/adx_live_run1.txt`, fingerprint
`v2-4ffbb6814f505b97` (13 series, 7 cached, 6 fetched fresh, oldest 5 days).**
Six series were not in the cache at this window and were fetched for the
run, so this fingerprint matches no earlier run's; the file does not say
which six. Longs only, live config, 12 names, 10 years.

**Primary, the band [30, 35): NOT ESTABLISHED.**

| band | longs | mean R | 95 % CI | p | detectable |
|---|---:|---:|---|---:|---:|
| [30, 35) | 199 | +0.104 | [−0.159, +0.367] | 0.440 | 0.376 |

The band clears the 150-trade floor and fails clauses 2 and 3: not
distinguishable from zero, and under its detectable effect by a factor of
nearly four. This is the outcome stated in advance. The 199 trades that
lowering the threshold to 30 would admit carry no measurable expectancy
either way. **Lowering the threshold is therefore a choice about alert
volume, not a measured improvement, and the pre-registration does not
pre-commit to it.** At the record's arrival rates it would roughly
double the HQ tier's intake (the ADX-30 ladder row holds 544 longs to the
ADX-35 row's 329) while adding nothing the data can see.

**Secondary, the ladder: NOT ESTABLISHED, no dose-response (ρ = +0.10).**

```
ADX  0   n=2135   +0.196   Holm yes   powered yes
ADX 20   n=1347   +0.206   Holm yes   powered yes
ADX 25   n= 895   +0.171   Holm yes   powered NO
ADX 30   n= 544   +0.132   Holm no    powered NO
ADX 35   n= 329   +0.264   Holm yes   powered NO
```

The bands below 30 are all around +0.2 R and the band at or above 35 is
+0.196; expectancy does not rise with the threshold. On the live config as
on the record config, ADX carries no information about which long setups
pay. The 35 gate keeps 15 % of the long base signals (329 of 2,135) and
the kept 15 % are not better than the rest. This is the same reading as
run 1 (ρ = −0.70) with the sign of the noise flipped, which is what no
information looks like twice.

**What the ladder does NOT say.** The ungated long side reads +0.196 R on
2,135 trades, powered and surviving Holm. That is not a finding and must
not be quoted as one, for three reasons fixed before the run:

1. **The names.** `SWEPT_MIX_12` holds the seven tickers the parameters
   were searched on. Their long side on the fixed engine is +0.297 R
   (`record_recheck_gapfill_run1.txt`, 7t/5y); the clean OOS twelve's is
   +0.042 R (12t/10y). This set mixes the two and lands between.
2. **Drift.** `drift_null_run1.txt`: on 20 names over the same decade,
   random long entries with the same exits and costs earned a median
   +0.149 R (95 % of draws +0.039 to +0.270), and the real signal's
   +0.105 R sat inside that band (empirical p 0.81, SIGNAL ADDS NOTHING).
   +0.196 R on a regime-gated, long-only cut of a bull decade is inside
   what random entry earns. It has not been nulled on this config and is
   not evidence until it is.
3. **The cut.** Longs only, regime on, live geometry, no ADX gate is a
   configuration chosen after the shorts were measured to lose and after
   the owner's question, on data every prior test has read. It can refute;
   it cannot confirm, as the pre-registration says.

**Answer to the owner's question.** ADX 30 is not measurably better or
worse than 35. Neither threshold has evidence behind it; the forward record
is the only clean data, and it has 0 HQ signals in 94 rows because the gate
is set where this bull tape does not go (`logic_review_2026-10-13.md` §(d)).
If the owner wants more HQ alerts to grade forward, lowering to 30 is a
defensible intake decision as long as it is recorded as that: an amendment
to `forward_record_preregistration.md` dated before the first row under the
new gate, with the config fingerprint changing as it must. The decision is
the owner's and is not made here.
