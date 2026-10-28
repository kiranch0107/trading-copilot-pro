# Run 4 — the reproduction under the pre-registration: identical to run 3, bars named

**Run by the owner 2026-10-28, `python spread_backtest.py --skew-sweep
--years 10`, main @ `c4cb64a` (PR #158 merged: `results/spread_preregistration.md`
governs this run).** Raw output: `spread_backtest_run4.txt`. The
pre-registration calls this a REPRODUCTION, not a new look: same eight
arms, same four skews, same bar, with the bar-cache fingerprint the engine
now prints.

## Verdict: the same five arms PASS, the same three FAIL, every number the same

`diff spread_backtest_run3.txt spread_backtest_run4.txt` is one line, the
fingerprint. Every cycle count, mean, interval, drawdown, worst cycle and
hold figure at every skew is identical to run 3, so the run-3 reading
(`spread_backtest_run3.md`) is the run-4 reading.

## The data question, settled

```
data: v2-9c744bd1aa11ecb2  (4 series, 4 cached / 0 fetched, oldest 48d)
```

Four series (SPY, QQQ, ^VIX, ^VXN), all read from the cache, none fetched,
the oldest entry 48 days old on 2026-10-28: fetched **2026-09-10**, the
same day as the seven option-engine series (`option_iv_sensitivity_run3_*.txt`:
"oldest 48d") and the record's own bars (`record_recheck_groupb_run1.txt`
reproduced the record's fingerprints from this cache). So:

- **Run 3 read these bars.** It ran on the same laptop hours earlier,
  nothing was fetched since, and run 4 reproduces it exactly.
- **Run 2 (2026-09-11) almost certainly read them too.** The 10-year
  entries were written on 2026-09-10 and never refreshed; a run the next
  day on the same machine would have hit the cache. Run 2 printed no
  fingerprint, so this is an inference from the manifest, not a record,
  and it is stated as one.
- **The cycle count (83 → 120) and the verdict flip are therefore the
  code**: run 2 held each 30-day cycle for 30 SESSIONS (42 calendar days)
  of path on 30 days of premium; the group B engine holds it 30 days.

## What this run does and does not change

It changes nothing in the repo but this file. Per the pre-registration,
five PASS verdicts commit to one thing: the forward arm runs for 12 graded
cycles per arm (`spread_log.jsonl`, first row at the next post-close pass,
first reading at 6 cycles, about six months out). The primary forward
question is whether the model's credit is what the market quotes; a
12-cycle ratio interval entirely below 0.85 voids the arm's verdict here.
No arm is traded on this file. The other caveats of the run-3 reading
stand: a third look at the same ten years (now a fourth, as a
reproduction), modelled prices, one decade of one regime, and clause 5's
capital-matched comparison being weaker than the whole-account one.
