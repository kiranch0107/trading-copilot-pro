# Longs only, in shares — run 1

**Run 2026-09-12, `python longs_only.py --years 10` on the OOS 12. Code at
`268c13c`.** A measurement, not a test: no bar, nothing searched over.

```
                total %   CAGR %   max DD %   trades   win %
idealised        +83.2     +6.2       45.7      889    35.7
constrained      +62.9     +5.0       20.0      686    37.2
SPY buy-hold    +252.4    +13.4       34.1        —       —
```

## THE SURPRISE: the capital constraint nearly DOUBLED risk-adjusted return

| | CAGR | max DD | return / drawdown |
|---|---:|---:|---:|
| idealised | +6.2% | 45.7% | 0.136 |
| **constrained** | **+5.0%** | **20.0%** | **0.250** |
| SPY buy-hold | +13.4% | 34.1% | **0.393** |

Capping positions at 25% and skipping when the account is full cut the drawdown
**56%** while costing only **19%** of the return. That is not a cost — it is the
best risk management in this file, and it arrived by accident, as a side effect
of not being able to afford the trades.

**It also means every R-sum this project has ever reported described the
idealised row**, whose 45.7% drawdown is *worse than SPY's*.

## And SPY still wins on both axes

+13.4%/yr against +5.0%, and 0.393 return-per-drawdown against 0.250. Owning the
index beat this on raw return **and** on risk-adjusted return, with no signals to
follow, no positions to size and nothing to monitor.

## 83% of positions could not be taken at full size

```
signals skipped, no room      203   (23% of 889)
positions sized down          741   (83% of those taken)
peak concurrent positions       5
peak capital deployed        101% of equity
```

At 1% risk behind a typical 2% stop the natural position is **50% of equity**.
The 25% ceiling halves it, so the constrained run was effectively risking
**~0.5% per trade, not the 1.0% configured**.

**This is an account-size limit, not a strategy setting.** At $50,000 the same
positions would be ~5% of equity, nothing would be capped or skipped, and the
result would be the *idealised* row — higher return and a 45.7% drawdown. The
small account is what produced the better risk profile.

## Half the profit is one year

| year | trades | P&L |
|---|---:|---:|
| 2017 | 57 | +113 |
| 2018 | 61 | +482 |
| 2019 | 63 | +558 |
| 2020 | 88 | +368 |
| 2021 | 82 | +104 |
| **2022** | **24** | **−540** |
| 2023 | 78 | +150 |
| 2024 | 111 | +89 |
| **2025** | **70** | **+1,482** |
| 2026 | 52 | +340 |

**2025 alone is $1,482 of $3,146 — 47% of all profit.** Strip it out and nine
years produce +33.3% total, about **+3.7%/yr**.

2022 is the only losing year, and it also has the fewest trades (24). The system
traded least and lost most in the one bear market in the sample.

## A modelling flaw, found in this run's own output

`peak capital deployed 101% of equity` should be impossible — the room check
refuses a trade when `deployed + value > equity`.

The cause: **P&L is booked at entry while the position stays open for the
concurrency check until its exit date.** A losing trade shrinks equity
immediately while its capital is still counted as deployed, so the ratio can
exceed 100%.

**Direction of the bias: optimistic.** Booking profits at entry compounds them
earlier than reality allows, so **+5.0%/yr is, if anything, generous**. The
effect is small at ~8-bar holds and ~89 trades a year, and it does not change any
conclusion here — but it is an error in my model, found by a diagnostic I put in
to catch exactly this, and it should be fixed before the module is reused.

## Prediction scorecard

| prediction | outcome |
|---|---|
| constrained CAGR +5 to +8%/yr | **right** — +5.0%, at the bottom of the range |
| 20–40% of signals unavailable | **right** — 23% |
| SPY ~14.2%/yr | **close** — +13.4% |
| drawdown "well north of 20%" | **wrong** — exactly 20.0%, and I had the direction of the constraint's effect backwards: I expected it to hurt, and it halved the drawdown |

## What this settles

The best available cut of this signal — longs only, in shares, at the account
size that actually exists — returns **+5.0%/yr with a 20% drawdown**, against an
index that returned **+13.4% with a 34% drawdown** over the same decade. It loses
on return, loses on return-per-drawdown, and depends on a single year for half
its profit.

The long side's expectancy is +0.085 R with a CI of [−0.037, +0.206]. A single
realised path cannot tell you which end of that interval you were on, and the
decay +0.268 → +0.106 → +0.085 suggests the low end.

**There is no version of this signal that beats owning the index.**
