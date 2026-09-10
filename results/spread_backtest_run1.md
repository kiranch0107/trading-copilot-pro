# Always-on defined risk: no arm cleared the bar

**Run 2026-09-10, `python spread_backtest.py --skew-sweep`, 10 years, exit 1.**

83 non-overlapping 30-day cycles per arm. Implied vol at entry is the observed
`^VIX` / `^VXN` close; exits settle on real SPY/QQQ prices.

## Result at skew 20 (the middle of the swept range; all four skews agree)

| arm | mean%/cycle | 95% CI | total% | win% | maxDD% | worst% |
|---|---|---|---|---|---|---|
| QQQ put_spread w10 | +0.68 | [−0.39, +1.74] | +56.0 | 86.7 | 36.8 | −16.7 |
| SPY put_spread w10 | +0.32 | [−0.74, +1.37] | +26.3 | 88.0 | 33.8 | −19.3 |
| QQQ put_spread w5 | +0.29 | [−0.36, +0.94] | +24.3 | 86.7 | 25.1 | −8.4 |
| SPY put_spread w5 | +0.07 | [−0.58, +0.71] | +5.4 | 88.0 | 27.3 | −9.6 |
| SPY iron_condor w5 | −0.95 | [−1.78, −0.12] | −79.1 | 63.9 | 84.2 | −9.2 |
| SPY iron_condor w10 | −1.11 | [−2.55, +0.34] | −91.7 | 68.7 | 99.0 | −18.8 |
| QQQ iron_condor w5 | −1.26 | [−2.15, −0.36] | −104.4 | 53.0 | 120.7 | −8.4 |
| QQQ iron_condor w10 | −1.38 | [−2.91, +0.14] | −114.9 | 56.6 | 143.7 | −16.0 |

**Every arm failed. No arm's CI cleared zero; every arm breached the 25%
drawdown bar.** The skew sweep barely moved anything — the four skews differ by
hundredths of a point, so skew was never the deciding variable.

## Two predictions, both wrong, recorded because being wrong here was informative

**Predicted: clause 4 (worst cycle > MAX_POSITION_PCT) would be the failure.**
It never fired. Worst cycles ran −8.4% to −19.3%, all inside the 25% ceiling.
Sizing was the one thing that held. The failures were clause 1 (significance) and
clause 2 (drawdown) — neither of which had been flagged as the risk.

**Predicted: the iron condor would do BETTER, as the purer harvest of a
direction-agnostic variance premium.** It was ruinous: negative mean on all four
arms, drawdowns of 84–144%. Above 100% means the account went through zero.

The second error is the useful one, because the reason is structural.

## Why the condor lost: the premium is not harvestable without delta hedging

A variance premium is direction-agnostic, but *capturing* it that way requires
continuous delta hedging. A static iron condor does not hedge. It is short a call
spread on an index that tripled over the sample, and the call side gets run over
repeatedly — QQQ clears 5% in a month often enough to make that side a losing
directional bet regardless of how rich implied vol was.

So the condor is not "the pure version". It is short vol **and** short the equity
drift. The put spread is short vol **and long** the equity drift, which is the
entire reason it looks better.

That reframes the put-spread result rather than endorsing it, and is why clause 5
was added.

## The comparison the strategy has to beat, now measured rather than asserted

Clause 5, added after this run: an arm must beat holding the **same dollars it
ties up** in the underlying, over the same cycles. It is a tightening, declared
as an addition. Nothing was rescued by it — the run passed no arm, so no
already-passing result was re-judged.

The reasoning: a short put spread is net long delta. Over a decade in which the
index roughly tripled, a positive mean is what long delta produces whether or not
any premium was harvested. `SPY put_spread w10` returned **+26.3% over ten years
— about 2.6%/yr — while carrying a 33.8% drawdown.** Buy-and-hold SPY over the
same decade returned several times that at a comparable drawdown. **That
comparison is now computed inside the module from the same data, not estimated
here**, and the next run will print it as the `hold%` column.

## A flaw in this run, which makes the FAIL more robust, not less

Costs are charged as a fraction of the **net credit** (`cost_frac *
MAX_OPTION_SPREAD_PCT * credit * n_legs`), not of each leg's own price. Real
per-leg bid-ask on a 0.93 credit spread whose legs are ~2.5 and ~1.6 would be
several times what this charged. **This run therefore UNDER-charged costs**, so
the true results are worse than the table. The failure stands a fortiori and does
not need re-running to be believed; the model should still be corrected before
any result is ever used to argue *for* a structure.

## Where this leaves the thesis

`vrp_check` was right and remains right: implied vol is systematically richer
than subsequent realised, +3.63 vol points, in every one of eleven years. That is
a real, measured anomaly.

This run says the anomaly **is not reachable through static, defined-risk,
monthly index spreads at this account size**. Those are two compatible facts. The
premium is real; this particular vehicle does not deliver it, and what looked
like delivery in the put-spread arms is mostly equity beta wearing a volatility
strategy's language.

**No arm cleared the bar. That is a result, not a prompt to widen the grid until
one does** — the module prints that line itself, deliberately.
