# The verifiability screen: what this project can and cannot settle

**2026-09-11. No study. This is the map the five completed studies produced.**

The momentum veto generalises. Years of data required collapses to one number:

```
years = (z / annual Sharpe)^2          z = 2.802 at alpha 0.05, power 0.80
```

Effect size and volatility matter *only* through their ratio, so **Sharpe is the
entire filter.**

| Sharpe | years to verify |
|---|---|
| 0.3 | 87 |
| 0.4 | 49 |
| 0.5 | 31 |
| 0.63 | **20 — the bar** |
| 0.8 | 12 |
| 1.0 | 8 |
| 1.3 | 5 |

**With ~20 years of retail-accessible data, only strategies with Sharpe above
0.63 are verifiable. With 10 years, 0.89.**

## Applying it

| candidate | Sharpe | years | verifiable? | and if so, harvestable? |
|---|---|---|---|---|
| value (HML) | 0.30 | 87 | no | — |
| quality, low-volatility | 0.40 | 49 | no | — |
| cross-sectional momentum | 0.45 | 39 | no | — |
| time-series trend (CTA) | 0.50 | 31 | no | — |
| short-term reversal | 0.80 | 12 | **yes** | **no — see below** |
| VRP, delta-hedged | 0.90 | 10 | yes | needs daily hedging |
| VRP, as measured here | 1.30 | 5 | **yes, and it was** | **no — beta, not premium** |

The entire classic factor literature sits below the bar. That is not a criticism
of those factors — it is why their papers use 40–90 year samples.

## Diversification is a real way out, and then it isn't

Combining k weakly-correlated factors raises portfolio Sharpe:

```
portfolio Sharpe = s * sqrt(k) / sqrt(1 + (k-1) * rho)
```

Five 0.40-Sharpe factors at ρ = 0.1 give **0.79 — clears the bar.** The
*combination* is verifiable even though no component is. Genuinely non-obvious,
and it is how multi-factor funds are actually justified.

**Then the retail implementation destroys it.** A long-short factor portfolio
needs shorting dozens of names — infeasible at $5,000. The reachable version is
long-only factor ETFs (MTUM, VLUE, QUAL, USMV), which costs twice:

1. **Long-only halving.** A long-only fund captures roughly half the long-short
   premium; much of the documented spread lives in the short leg it cannot hold.
2. **Correlation collapse.** Factor ETFs run ρ ≈ 0.5–0.7 with each other
   *because they all hold the market*. Diversification barely helps when the
   common factor **is** the correlation:

| k | ρ = 0.1 | ρ = 0.5 | ρ = 0.7 |
|---|---|---|---|
| 5 | 0.76 | 0.52 | 0.46 |

And measured as excess over SPY, which is the only thing worth paying for:

```
excess 2.0%/yr at 4.0% tracking error -> Sharpe 0.50 ->  31 years
excess 1.5%/yr at 5.0% tracking error -> Sharpe 0.30 ->  87 years
excess 1.0%/yr at 4.0% tracking error -> Sharpe 0.25 -> 126 years
```

Back below the bar, and by a wide margin.

## Short-term reversal: verifiable, and structurally unavailable

The one untested candidate that clears the screen (Sharpe ≈ 0.8, 12 years).
It should not be built, and the reason is the mechanism itself.

Short-term reversal is **compensation for providing liquidity** — absorbing
order imbalances and being paid the spread for it. Retail accounts *pay* the
spread. You would be on the wrong side of the exact mechanism that generates the
premium, at every single rebalance, in the highest-turnover strategy in the
literature. Its Sharpe is gross of the cost that is your entire position.

Same shape as the VRP finding: real, verifiable, and the part reachable from
here is not the part that pays.

## Why each door is closed — and they are different doors

This is the useful output. Five distinct failure modes, not one:

| path | closed by |
|---|---|
| directional signals | **no effect** — measured, well powered, trustworthy |
| ADX filter | **refuted** — dose-response runs backwards |
| PEAD | **no effect** in reachable data |
| classic factors | **unverifiable** — Sharpe too low for 20 years |
| factor ETFs | **diluted** — long-only + shared beta puts it further below |
| short-term reversal | **wrong side** — you pay the spread it earns |
| VRP | **real**, but harvesting needs hedging; what is reachable is beta |

Only one of those seven is "nothing there." The rest are constraints of
**instrument, scale, cost structure, or measurability** — which is a different
and more honest finding than "no strategy works."

## The stopping rule this implies

The screen is not a prompt to generate hypothesis #6, #7 and #8. Anything from
the public factor literature fails on Sharpe before a line is written, and the
things that clear the Sharpe bar have so far failed on mechanism access.

Generating more candidates without a *new source of edge* — different data,
different instrument, different cost structure, or materially more capital — is
motion, not progress. **The screen exists to stop that.**

What would genuinely reopen the search:

- **more capital** (~$100k+): shorting becomes possible, per-trade costs stop
  dominating, and the long-short implementations that carry the premium become
  reachable
- **a different cost structure**: the VRP and reversal results both closed on
  the spread paid, not on the effect
- **data nobody else has**: every test here used free, universally available
  data, which is precisely the data whose edges are already arbitraged
- **accepting unverified literature**: a legitimate choice, but it should be
  made explicitly rather than smuggled in by a underpowered backtest that
  "confirmed" it
