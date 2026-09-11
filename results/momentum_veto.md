# Momentum: vetoed before building, and the reason generalises

**2026-09-11. No study was written. `power_check.py` refused the design.**

This is the first time the power discipline paid for itself *before* work was
done rather than after. The spread backtest cost a full build and a wrong
conclusion; this cost one command.

## The design that was going to be built

Standard cross-sectional momentum: rank on the 12-month return skipping the most
recent month, long the top quintile, short the bottom, rebalance monthly,
t-test the time series of monthly spread returns. **One observation is one
month**, not one stock — that is the part that kills it.

Literature parameters for US equities: premium **0.5–1.0 %/month**, long-short
spread sd **5–8 %/month**.

## Years of data required

| premium | sd | months | years |
|---|---|---|---|
| 0.50% | 5% | 786 | **66** |
| 0.50% | 6% | 1,131 | **94** |
| 0.50% | 8% | 2,010 | **168** |
| 0.75% | 6% | 503 | **42** |
| 1.00% | 5% | 197 | **16** |
| 1.00% | 8% | 503 | **42** |

Against what is reachable here — yfinance daily, 15–20 years, i.e. 180–240
months:

```
10 years, sd 5%/mo  ->  detectable 1.28%/month  (15%/yr)
20 years, sd 5%/mo  ->  detectable 0.90%/month  (11%/yr)
20 years, sd 8%/mo  ->  detectable 1.45%/month  (17%/yr)
```

Momentum's premium is 0.5–1.0%/month. **Only the single most optimistic corner
of the parameter space is reachable, and only barely.** A study run anyway would
return "not significant" whether or not momentum is real, which is the same
output for both answers — worthless.

## Rebalancing more often does not help. This was the tempting error.

The obvious workaround is to rebalance weekly or daily for more data points.
It buys nothing, and `span_tstat()` pins why:

```
sampling         n    mu/obs   sd/obs   t-stat
monthly        240    0.7500    6.000    1.936
weekly       1,040    0.1731    2.882    1.936
daily        5,040    0.0357    1.309    1.936
```

The effect grows linearly with time; the noise grows with its square root. So
sampling more often shrinks the per-observation signal *exactly* as fast as it
multiplies the observations. **Over a fixed calendar span, statistical power is
fixed.**

Only three things move it:

1. more **calendar time** — and t grows only with √years
2. lower strategy **volatility** (diversification, hedging)
3. a bigger underlying **effect**

"More data points" via faster sampling is none of those. A selftest asserts the
invariance at 12, 52, 252 and 1000 observations/year, and the falsification —
making noise scale linearly instead of as √t — fails it.

## Why this matters far beyond momentum

**Any strategy whose natural unit is a periodic portfolio return gets 12
observations a year, permanently.** That shape covers essentially the whole
factor literature: value, quality, low-volatility, carry, cross-sectional
momentum. It is why those papers use 40–90 year samples, and why an individual
researcher cannot independently validate any of them.

What *is* testable at this scale is anything producing many independent events:

| what | sample | testable? |
|---|---|---|
| PEAD | 953 events | yes — and it was tested |
| directional signal | 3,406 trades | yes — and it was tested |
| VRP existence | ~119 independent windows | marginal, but the effect was large |
| monthly factors | ~240 months | **no, not ever, here** |

## The honest position this leaves

Momentum is the best-evidenced anomaly in the literature — replicated across
decades, countries and asset classes, and out-of-sample since 1993. It is
probably real.

**And this project cannot verify it.** That is a genuine tension, because the
repo's whole discipline is *do not trade what you have not verified*. The
options are to trust the literature and accept an unverified premise, or to
decline on principle. Both are defensible; pretending a 20-year test settled it
is not.

No code was written. That is the correct outcome, not a failed attempt.
