# ADX re-test: the filter is refuted, and the old headline was noise

**Run 2026-09-10, `python adx_retest.py`, 12 tickers / 10 years, exit 1 — NOT ESTABLISHED.**

| ADX | trades | exp R | sd | p | detectable | powered | Holm | n≥150 |
|---|---|---|---|---|---|---|---|---|
| 0 | 3,406 | **+0.062** | 1.67 | 0.0312 | 0.080 | NO | no | yes |
| 20 | 2,228 | +0.036 | 1.67 | 0.3098 | 0.099 | NO | no | yes |
| 25 | 1,486 | −0.039 | 1.63 | 0.3531 | 0.118 | NO | no | yes |
| 30 | 867 | −0.062 | 1.54 | 0.2370 | 0.147 | NO | no | yes |
| 35 | 466 | +0.004 | 1.61 | 0.9609 | 0.208 | NO | no | yes |

Spearman ρ = **−0.70** against a +0.6 bar.

## The old headline evaporated, exactly as the power analysis predicted

```
2026-09-01   +0.188 R   140 trades   7t/5y, three scoring bugs, adjusted prices
2026-09-10   +0.004 R   466 trades   12t/10y, fixed code
```

+0.188 R never cleared its own detection threshold of 0.237 at n=140. On a
sample 3.3× larger with the scoring bugs fixed, it is +0.004 — indistinguishable
from zero. It was noise, and the pre-registered bar called it before the re-run.

**The engine agrees with itself.** ADX 25 here (−0.039 R) reproduces the earlier
12-ticker/10-year figure (−0.048 R) from `longshort_split.md`. Only the 7×5
ADX-35 outlier vanished, which is what should happen to an outlier.

## The dose-response runs BACKWARDS

```
ADX  0   n=3406   +0.062
ADX 20   n=2228   +0.036
ADX 25   n=1486   -0.039
ADX 30   n= 867   -0.062
ADX 35   n= 466   +0.004
```

ρ = −0.70. **More filtering is worse.** If ADX were removing chop and keeping
trends, expectancy would rise with the threshold. It falls. The filter was
costing money, not saving it — and the live default sat at 25, in the worst part
of that range.

This is the clause that mattered. Without a dose-response test, ADX 0's +0.062
could have been written up as "the signal works unfiltered", when the honest
reading is that the whole ADX axis carries no usable information and the ordering
is, if anything, adverse.

## The one cell pointing anywhere

`ADX 0`, no filter at all: **+0.062 R over 3,406 trades, raw p = 0.031.**

It fails on two counts and must not be adopted:

- fails Holm across 5 comparisons (needs p ≤ 0.010 at its rank)
- sits under its own detection threshold (0.080 > 0.062), so it is
  **INCONCLUSIVE** — which is not "no edge", and is not "an edge" either

It is the only cell suggesting anything, and what it suggests is **removing the
filter**, not tuning it. Settling it needs a wider sample, not more ADX levels —
`power_check` says roughly 5,700 trades at this sd to detect +0.062.

## Status of the old files

`results/adx20.txt` … `adx35.txt` are superseded. They were produced on
2026-09-01 by code carrying an inverted gap-fill scoring bug, two further silent
mis-scoring bugs, no cost model, and an unstable adjusted-price basis. They
should not be cited again.
