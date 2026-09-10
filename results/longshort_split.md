# Long/short split — three cuts, and why the question is closed

**Date:** 2026-09-09
**Status:** hypothesis tested and not supported. Tranche A NOT spent.

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
| 12 tickers, 10y | 906 | **+0.100** | 1.16 | [−0.019, +0.219] |

### SHORT

| sample | n | avg R | PF | 95% CI |
|---|---:|---:|---:|---|
| 7 tickers, 5y — discovery | 160 | −0.393 | 0.47 | [−0.567, −0.219] |
| 12 tickers, 5y | 257 | −0.190 | 0.74 | [−0.404, +0.025] |
| 12 tickers, 10y | 503 | **−0.253** | 0.66 | [−0.397, −0.108] |

### Blended

| sample | n | avg R | PF | total R |
|---|---:|---:|---:|---:|
| 7 tickers, 5y | 423 | +0.004 | 1.01 | +1.5 R |
| 12 tickers, 5y | 693 | +0.007 | 1.01 | +5.1 R |
| 12 tickers, 10y | 1409 | **−0.026** | 0.96 | **−36.2 R** |

Universes:
- **7 tickers** — TSLA, NVDA, AAPL, MSFT, AMZN, META, SPY (the Aug 2026 sweep set)
- **12 tickers** — GOOGL, AVGO, AMD, NFLX, CRM, ADBE, QCOM, MU, ORCL, NOW, PANW, LRCX
  (the 591-trade OOS set — already spent, which is why re-running it cost nothing)

---

## The long side decays as the sample grows

```
+0.245  (n=263)   ->   +0.124  (n=436)   ->   +0.100  (n=906)
```

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
| LONG | +0.078 R | 470 |
| SHORT | **−0.319 R** | 246 |

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
| +0.100 R (10y estimate) | 2,636 | **73** | **35** |

The target recedes as you measure it, because each larger sample revises the
effect downward and required n scales with 1/effect².

**Tranche A is 16 tickers.** At 10 years that is ~1,208 long trades, CI
half-width ±0.103. If the true effect is exactly +0.100:

```
[-0.003, +0.203]     <- still touching zero, still unresolved
```

Spending the last clean tranche would end exactly where it started.

Two further reasons the arithmetic above is **optimistic**:

1. **Trades are not independent.** Every formula here assumes they are. ADX-25
   momentum regimes cluster in time — the signal fires across many correlated
   large-cap names simultaneously, and those trades win or lose together. The
   effective sample is materially smaller than the trade count, so real
   intervals are wider than the ones above.

2. **A win would be unusable.** +0.100 R at 1% of a $1,500 account is $1.50 per
   trade — roughly **$60/year gross on shares**, before commissions, theta, IV
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

**Outcome:** the long CI did **not** clear zero — [−0.019, +0.219]. The older
half was mildly positive (+0.078) rather than near zero, so this landed between
the two branches, but the primary clause failed. By the rule as written, the
long hypothesis is not supported.

The predicted interval half-width was ±0.12 at n≈870; the run returned ±0.119.
The test had the power claimed for it and did not clear the bar.

---

## Conclusions

1. **Over 10 years and 1,409 trades the system is negative** — −0.026 R, PF 0.96,
   −36.2 R. This is the largest sample and the widest regime coverage the
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
