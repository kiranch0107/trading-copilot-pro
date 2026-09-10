# Long/short split — three cuts, and why the question is closed

**Date:** 2026-09-09
**Status:** hypothesis tested and not supported. Tranche A NOT spent.

> ### The 10-year run below is post-fix. The two 5-year runs are not.
>
> **2026-09-10.** `simulate_trade()` had a sign inversion on fills that gapped
> past their own levels. A setup whose next open was already beyond the stop
> was booked as `outcome="loss"` with **r = +1.000**; one past the target as
> `outcome="win"` with **r = −0.200**, because `r = pnl / |entry − stop|` is
> computed against a distance that has inverted. The worst fills in each
> sample were scoring as full winners. Those fills are now rejected and
> counted (42 of them in the 10-year run: 31 past the stop, 11 past the
> target).
>
> **It was material.** On the 10-year run it hid roughly half the loss:
>
> | | pre-fix | corrected |
> |---|---:|---:|
> | expectancy | −0.026 R | **−0.048 R** |
> | total return | −36.2 R | **−67.2 R** |
> | profit factor | 0.96 | 0.93 |
> | max drawdown | −66.7 R | **−86.1 R** |
> | LONG | +0.100 R | +0.085 R |
> | SHORT | −0.253 R | −0.287 R |
>
> The 10-year rows throughout this file are the corrected ones. **The two
> 5-year runs have not been repeated**, so their figures are still pre-fix and
> read better than the truth by an unknown margin. Every conclusion is
> unchanged in direction and stronger in magnitude.

This file exists so the +0.245 R figure below cannot be rediscovered in six
months and mistaken for a finding. It was the first of three measurements, and
it was the largest one. Everything after it was smaller.

---

## What prompted it

`backtest.py` gained a per-direction breakdown (PR #20). The aggregate had been
reporting the system as flat — +0.004 R per trade, PF 1.01 — which reads as
"no edge". The split showed that flatness was a **cancellation**, not an
absence: longs made money, shorts lost roughly the same amount.

That is a real distinction and worth knowing. It is not, on its own, evidence
of a tradeable edge, and the two runs that followed are why.

---

## The three cuts

### LONG

| sample | n | avg R | PF | 95% CI |
|---|---:|---:|---:|---|
| 7 tickers, 5y — **discovery** | 263 | **+0.245** | 1.43 | [+0.037, +0.453] |
| 12 tickers, 5y | 436 | +0.124 | 1.20 | [−0.059, +0.307] |
| 12 tickers, 10y — **corrected** | 889 | **+0.085** | 1.13 | [−0.037, +0.206] |

### SHORT

| sample | n | avg R | PF | 95% CI |
|---|---:|---:|---:|---|
| 7 tickers, 5y — discovery | 160 | −0.393 | 0.47 | [−0.567, −0.219] |
| 12 tickers, 5y | 257 | −0.190 | 0.74 | [−0.404, +0.025] |
| 12 tickers, 10y — **corrected** | 497 | **−0.287** | 0.63 | [−0.432, −0.141] |

### Blended

| sample | n | avg R | PF | total R |
|---|---:|---:|---:|---:|
| 7 tickers, 5y | 423 | +0.004 | 1.01 | +1.5 R |
| 12 tickers, 5y | 693 | +0.007 | 1.01 | +5.1 R |
| 12 tickers, 10y — **corrected** | 1386 | **−0.048** | 0.93 | **−67.2 R** |

Universes:
- **7 tickers** — TSLA, NVDA, AAPL, MSFT, AMZN, META, SPY (the Aug 2026 sweep set)
- **12 tickers** — GOOGL, AVGO, AMD, NFLX, CRM, ADBE, QCOM, MU, ORCL, NOW, PANW, LRCX
  (the 591-trade OOS set — already spent, which is why re-running it cost nothing)

---

## The long side decays as the sample grows

```
+0.245        ->   +0.124        ->   +0.100          ->   +0.085
 (n=263)           (n=436)            (n=906, pre-fix)     (n=889, corrected)
```

Four measurements, each larger than the last, each smaller in effect.

The only interval that ever cleared zero is the one in which the effect was
discovered. That monotone decay toward zero as n grows is the signature of an
estimate inflated by the noise that made it visible in the first place — not
of an edge being measured more precisely.

The obvious defence — "different regime" — is not available. The 12-ticker
5-year run covers the *same five years* as the discovery run, in the same
market, in correlated large-cap names. A durable property of the signal should
have replicated at roughly its original size there. It came back at 51%.

---

## The older half, and what it does to the short side

The 10-year window contains the 5-year window, so the ten-year number is not an
independent test. The new information is only the older half. With trade counts
weighting each half:

```
older-half effect  =  (n10 x avg10  -  n5 x avg5) / (n10 - n5)
```

| side | older half (2016–2021) | n | basis |
|---|---:|---:|---|
| LONG | +0.078 R | 470 | both pre-fix |
| SHORT | **−0.319 R** | 246 | both pre-fix |

> **Mixed-basis warning.** This subtraction needs both runs on the same
> footing. The 10-year run is now corrected and the 5-year one is not, so the
> table above is the pre-fix pair — internally consistent, but understating
> both sides. Recomputing with the corrected 10-year against the pre-fix
> 5-year gives LONG +0.048 and SHORT −0.391, and those numbers are not
> trustworthy either, because the two halves were scored by different code.
> **Re-run the 12-ticker 5-year backtest to settle this properly.** The
> qualitative reading — shorts lost more in the older half than the newer —
> holds on every version of the arithmetic.

**The short result is the robust finding here, and it destroys its own excuse.**
2016–2021 contains the COVID crash and the 2022 bear — the window in which a
bearish signal should earn its keep. Shorts lost *more* there (−0.319 R) than in
the bull years (−0.190 R). So shorts are not regime-handicapped. The rule is bad.

Shorts are negative in all three cuts, and the 10-year CI clears zero.

---

## Why more tickers cannot fix this

Per-trade SD, backed out of the printed intervals: **1.833 R** (LONG side;
1.721 / 1.950 / 1.827 across the three runs — consistent).

Sample needed for 80% power, alpha 0.05 two-sided:

| to detect | long trades | tickers @5y | tickers @10y |
|---|---:|---:|---:|
| +0.124 R (5y estimate) | 1,715 | 47 | 23 |
| +0.100 R (10y, pre-fix) | 2,681 | 74 | 36 |
| +0.085 R (10y, corrected) | 3,711 | **101** | **50** |

The target recedes as you measure it, because each larger sample revises the
effect downward and required n scales with 1/effect².

**Tranche A is 16 tickers.** At 10 years that is ~1,186 long trades, CI
half-width ±0.105. If the true effect is the corrected +0.085:

```
[-0.020, +0.190]     <- still spanning zero, still unresolved
```

The fix moved this further out of reach, not closer.

Spending the last clean tranche would end exactly where it started.

Two further reasons the arithmetic above is **optimistic**:

1. **Trades are not independent.** Every formula here assumes they are. ADX-25
   momentum regimes cluster in time — the signal fires across many correlated
   large-cap names simultaneously, and those trades win or lose together. The
   effective sample is materially smaller than the trade count, so real
   intervals are wider than the ones above.

2. **A win would be unusable.** +0.100 R at 1% of a $1,500 account is $1.50 per
   trade at the pre-fix +0.100, or $1.28 at the corrected +0.085 — roughly
   **$50–60/year gross on shares**, before commissions, theta, IV
   crush, and an 8% spread paid both ways. `option_backtest.py` already measured
   what that thinness looks like once it becomes an option: a 23.8% win rate
   against a 23.3% breakeven.

The right time to ask "how much data do I need" is before the effect size is
known. Once it is +0.100 R on shares, no sample size makes it tradeable.

---

## Pre-registered rule, and its outcome

Written down **before** the 10-year run (this conversation, 2026-09-09):

> - Long CI clears zero AND implied older half clearly positive → the split
>   survived a regime change. A real finding.
> - Long CI includes zero AND the older half prices near zero → the long bias
>   describes 2021–2026, not the signal. Close the question.

**Outcome:** the long CI did **not** clear zero — [−0.019, +0.219] as first
run, [−0.037, +0.206] once the gapped-fill scoring was corrected. The older
half was mildly positive rather than near zero, so this landed between the two
branches, but the primary clause failed on both versions of the numbers. By
the rule as written, the long hypothesis is not supported.

The rule was judged on the pre-fix run, which is the version that existed when
the rule was written. The correction moved the result further from clearing
the bar, so the verdict does not depend on which one is used.

The predicted interval half-width was ±0.12 at n≈870; the run returned ±0.119
(±0.122 corrected).
The test had the power claimed for it and did not clear the bar.

---

## Two diagnostics added after the fact (10-year run, post-fix)

### Concentration — the long side is broad, not one name

```
LONG   positive in 8/12 tickers   +0.085 R -> +0.060 R without AVGO
SHORT  positive in 2/12 tickers   -0.287 R -> -0.320 R without NOW
```

This was checked expecting to find the long result resting on one or two
tickers. It does not. Recorded because a check that comes back in the
strategy's favour has to be written down as faithfully as one that does not.

It changes nothing, and the reason is worth keeping: eight of twelve
correlated large-cap tech names being positive over one rising decade is close
to ONE common factor appearing eight times, not eight independent
confirmations. Breadth retires the "it is one ticker" objection and leaves the
two that decide it — the CI still spans zero, and +0.085 R cannot survive
option costs.

Shorts are positive in only 2/12 and get worse when their best name leaves.

### Hold profile — where the loss actually is

```
same session :  238 (17.2%)  84% lose  -0.770 R
held longer  : 1148 (82.8%)            +0.101 R
```

`0.172 x (-0.770) + 0.828 x (+0.101) = -0.048` — the entire negative
expectancy is the entry-bar group.

**This is not a licence to exclude them.** You cannot know in advance which
trades gap into their stop; removing them afterwards is selecting on the
outcome. The only lever that reduces them is a wider stop, and it moves both
sides of the ledger:

| stop | target | payoff | breakeven WR |
|---|---|---:|---:|
| 1.0 x ATR | 3.0 x ATR | 3:1 | 25.0% |
| 1.5 x ATR | 3.0 x ATR | 2:1 | 33.3% |

The observed win rate is 31.3%, so widening to 1.5 ATR puts breakeven ABOVE
it — while also raising the win rate by an unknown amount. Both sides move.
Which one wins is a pre-registered test, not an adjustment.

Same-session winners average about **+0.44 R**, not +3.00. A gap in your
favour raises `|entry - stop|` and shrinks `target - entry`, so it costs more
and pays less. Correctly modelled, and easy to miss.

## Conclusions

1. **Over 10 years and 1,386 trades the system is negative** — −0.048 R, PF 0.93,
   −67.2 R, max drawdown −86.1 R. This is the largest sample and the widest regime coverage the
   project has produced, and it agrees with the earlier 591-trade OOS failure.
   The two five-year windows read flat because five recent years were kind.
2. **The short side is reliably negative**, including through a bear market.
3. **The long side is not established.** Weakly positive in every cut, never
   clearing zero outside the discovery sample, and decaying with sample size.
4. **Long-only is not a rescue.** +0.100 R on shares, CI touching zero, before
   any option cost.

## Decisions taken

- **Do not spend tranche A.** Nothing earned it, and it could not settle the
  question anyway.
- **Stop taking bearish setups.** The one change the data supports outright —
  and free, since all four live trades to date were calls.
- **Do not scale the live test.** Keep it as data collection on execution;
  do not expect profit from it.

## The question that is still open

The mechanical signal is settled. Whether *operator discretion* adds anything is
not — and it is untested. `skipped_signals.json` and the `source` field on each
journal row exist to answer it: the setups passed on are the control group for
the ones taken. That is a smaller, cheaper and more relevant question than 73
tickers, and it is the only one left where a positive answer would be worth
acting on.

Requires the `source` back-fill (signal vs discretionary) on the existing rows.

---

## Reproducing

```bash
python backtest.py                                   # 7 tickers, 5y
python backtest.py --tickers GOOGL,AVGO,AMD,NFLX,CRM,ADBE,QCOM,MU,ORCL,NOW,PANW,LRCX
python backtest.py --years 10 --tickers GOOGL,AVGO,AMD,NFLX,CRM,ADBE,QCOM,MU,ORCL,NOW,PANW,LRCX
```

Run fingerprints (`bar_cache`, v2): `24b0f52e1203ecd5` (7t/5y),
`6b5c07fe41849b1c` (12t/5y), `e09e31d6eaf30233` (12t/10y). A changed
fingerprint means the data moved — compare that before comparing any result.
