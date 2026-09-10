# Long/short split — three cuts, and why the question is closed

**Date:** 2026-09-09
**Status:** hypothesis tested and not supported. Tranche A NOT spent.

> ### All three runs are post-fix as of 2026-09-10
>
> `simulate_trade()` had a sign inversion on fills that gapped past their own
> levels. A setup whose next open was already beyond the stop was booked as
> `outcome="loss"` with **r = +1.000**; one past the target as `outcome="win"`
> with **r = −0.200**, because `r = pnl / |entry − stop|` was computed against
> a distance that had inverted. The worst fills in each sample were scoring as
> full winners. Those fills are now rejected and counted.
>
> It was material, and NOT in one direction:
>
> | run | expectancy pre-fix | corrected | gapped fills |
> |---|---:|---:|---:|
> | 7 tickers, 5y | +0.004 R | **+0.012 R** | 8 |
> | 12 tickers, 5y | +0.007 R | **−0.012 R** | 17 |
> | 12 tickers, 10y | −0.026 R | **−0.048 R** | 42 |
>
> The 12-ticker 5-year run crossed from positive to negative. Every figure
> below is post-fix, so the whole file sits on one basis and the subtractions
> are valid.

---

## What prompted it

`backtest.py` gained a per-direction breakdown (PR #20). The aggregate had been
reporting the system as flat — +0.012 R per trade, PF 1.02 — which reads as
"no edge". The split showed that flatness was a **cancellation**, not an
absence: longs made money, shorts lost roughly the same amount.

That is a real distinction and worth knowing. It is not, on its own, evidence
of a tradeable edge, and the two runs that followed are why.

---

## The three cuts

### LONG

| sample | n | avg R | PF | 95% CI |
|---|---:|---:|---:|---|
| 7 tickers, 5y — **discovery** | 260 | **+0.268** | 1.47 | [+0.055, +0.481] |
| 12 tickers, 5y | 429 | +0.106 | 1.17 | [−0.080, +0.292] |
| 12 tickers, 10y | 889 | **+0.085** | 1.13 | [−0.037, +0.206] |

### SHORT

| sample | n | avg R | PF | 95% CI |
|---|---:|---:|---:|---|
| 7 tickers, 5y — discovery | 158 | −0.409 | 0.46 | [−0.584, −0.235] |
| 12 tickers, 5y | 255 | −0.210 | 0.72 | [−0.426, +0.005] |
| 12 tickers, 10y | 497 | **−0.287** | 0.63 | [−0.432, −0.141] |

### Blended

| sample | n | avg R | PF | total R |
|---|---:|---:|---:|---:|
| 7 tickers, 5y | 418 | +0.012 | 1.02 | +5.1 R |
| 12 tickers, 5y | 684 | **−0.012** | 0.98 | −8.2 R |
| 12 tickers, 10y | 1386 | **−0.048** | 0.93 | **−67.2 R** |

Universes:
- **7 tickers** — TSLA, NVDA, AAPL, MSFT, AMZN, META, SPY (the Aug 2026 sweep set)
- **12 tickers** — GOOGL, AVGO, AMD, NFLX, CRM, ADBE, QCOM, MU, ORCL, NOW, PANW, LRCX
  (the 591-trade OOS set — already spent, which is why re-running it cost nothing)

---

## The long side decays as the sample grows

```
+0.268           ->      +0.106          ->      +0.085
 (n=260, 7t/5y)          (n=429, 12t/5y)         (n=889, 12t/10y)
```

Discovery kept **40%** of its size on the first replication. The only interval
that ever cleared zero is the discovery one.

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

| side | older half (2016–2021) | n |
|---|---:|---:|
| LONG | +0.065 R | 460 |
| SHORT | **−0.368 R** | 242 |

Both halves are now scored by the same code, so this subtraction is valid.

**The short result is the robust finding here, and it destroys its own excuse.**
2016–2021 contains the COVID crash and the 2022 bear — the window in which a
bearish signal should earn its keep. Shorts lost *more* there (−0.319 R) than in
the bull years (−0.190 R). So shorts are not regime-handicapped. The rule is bad.

Shorts are negative in all three cuts, and the 10-year CI clears zero.

---

## Why more tickers cannot fix this

Per-trade SD, backed out of the printed intervals: **1.855 R** (LONG side;
1.752 / 1.966 / 1.848 across the three runs — consistent).

Sample needed for 80% power, alpha 0.05 two-sided:

| to detect | long trades | tickers @5y | tickers @10y |
|---|---:|---:|---:|
| +0.268 R (discovery) | 383 | 11 | 6 |
| +0.106 R (first replication) | 2,443 | 67 | 33 |
| +0.085 R (10 years) | 3,740 | **104** | **50** |

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

2. **A win would be unusable.** +0.085 R at 1% of a $1,500 account is $1.28 per
   trade — roughly **$50/year gross on shares**, before commissions, theta, IV
   crush, and an 8% spread paid both ways. `option_backtest.py` already measured
   what that thinness looks like once it becomes an option: a 23.8% win rate
   against a 23.3% breakeven.

The right time to ask "how much data do I need" is before the effect size is
known. Once it is +0.085 R on shares, no sample size makes it tradeable.

---

## Pre-registered rule, and its outcome

Written down **before** the 10-year run (this conversation, 2026-09-09):

> - Long CI clears zero AND implied older half clearly positive → the split
>   survived a regime change. A real finding.
> - Long CI includes zero AND the older half prices near zero → the long bias
>   describes 2021–2026, not the signal. Close the question.

**Outcome:** the long CI did **not** clear zero — [−0.019, +0.219] on the run
as first scored, [−0.037, +0.206] once the gapped-fill inversion was
corrected. The older half was mildly positive (+0.065) rather than near zero,
so this landed between the two branches, but the primary clause failed on both
versions of the numbers. By the rule as written, the long hypothesis is not
supported.

The rule was judged against the run that existed when it was written. The
correction moved the result further from clearing the bar, so the verdict does
not depend on which scoring is used — which is the only reason it is worth
recording that the rule predated the fix.

The predicted interval half-width was ±0.12 at n≈870; the run returned ±0.119,
±0.122 corrected. The test had the power claimed for it and did not clear the
bar.

---

## Two diagnostics added after the fact (10-year run, post-fix)

### Concentration — the long side is broad, not one name

| run | LONG positive in | avg R | without its best name |
|---|---:|---:|---|
| 7t/5y | 6 of 7 | +0.268 | +0.192 without META |
| 12t/5y | 8 of 12 | +0.106 | +0.085 without MU |
| 12t/10y | 8 of 12 | +0.085 | +0.060 without AVGO |

| run | SHORT positive in | avg R | without its best name |
|---|---:|---:|---|
| 7t/5y | 0 of 7 | −0.409 | −0.446 without MSFT |
| 12t/5y | 3 of 12 | −0.210 | −0.316 without ORCL |
| 12t/10y | 2 of 12 | −0.287 | −0.320 without NOW |

This was checked expecting to find the long result resting on one or two
tickers. It does not, in any cut. Recorded because a check that comes back in
the strategy's favour has to be written down as faithfully as one that does
not.

It changes nothing, and the reason is worth keeping: eight of twelve
correlated large-cap tech names being positive over one rising decade is close
to ONE common factor appearing eight times, not eight independent
confirmations. Breadth retires the "it is one ticker" objection and leaves the
two that decide it — the CI still spans zero outside the discovery set, and
+0.085 R cannot survive option costs.

Shorts are the mirror image: positive in 0, 3 and 2 names respectively, and
every one of them gets WORSE when its best name is removed.

### Hold profile — where the loss actually is

| run | same session | of those, lose | their avg R | everything else |
|---|---:|---:|---:|---:|
| 7t/5y | 17.9% | 76% | −0.646 R | +0.156 R |
| 12t/5y | 17.5% | 84% | −0.695 R | +0.133 R |
| 12t/10y | 17.2% | 84% | −0.770 R | +0.101 R |

**That ~17.5% is the most stable number in this whole file.** It reproduces
across three cuts spanning different tickers and different decades, which
makes it a structural property of a 1x ATR stop rather than a quirk of one
sample. `0.172 x (-0.770) + 0.828 x (+0.101) = -0.048` — in every cut, the
entire negative expectancy is the entry-bar group.

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
   −67.2 R, max drawdown −86.1 R. Two of the three cuts are negative outright;
   the third is the discovery set at +0.012 R. This is the largest sample and the widest regime coverage the
   project has produced, and it agrees with the earlier 591-trade OOS failure.
   The two five-year windows read flat because five recent years were kind.
2. **The short side is reliably negative**, including through a bear market.
3. **The long side is not established.** Weakly positive in every cut, never
   clearing zero outside the discovery sample, and decaying with sample size.
4. **Long-only is not a rescue.** +0.085 R on shares over ten years, CI
   spanning zero, before any option cost.

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
`6b5c07fe41849b1c` (12t/5y), `e09e31d6eaf30233` (12t/10y).

The fingerprint hashes the DATA, not the code. All three were unchanged across
the gapped-fill fix while every result moved — which is exactly what it is for:
a moved number under an unchanged fingerprint is the code, and a moved
fingerprint is the data. Check it before comparing anything else.
