# Run 2 — the same arms with the cost model corrected

**Run 2026-09-11, `python spread_backtest.py --skew-sweep --years 10`, exit 1.**
Code at `6bec8c9`. Same data, same arms, same bar as run 1; the only change is
that the bid-ask is now charged on each leg's own price rather than on the net
credit times leg count.

## Verdict: unchanged. NO ARM CLEARED THE BAR.

## Every arm moved exactly as the cost table predicted

Run 1's correction predicted that the two **w5 put spreads** had been
*under*-charged and everything else *over*-charged. Eight arms, eight correct
directions:

| arm | mean %/cycle | max DD % | hold % | mean − hold | predicted | moved |
|---|---|---|---:|---:|---|---|
| QQQ iron_condor w10 | −1.38 → **−0.82** | 143.7 → **101.1** | +0.34 | −1.16 | better | better |
| QQQ iron_condor w5 | −1.26 → **−1.12** | 120.7 → **107.3** | +0.15 | −1.27 | better | better |
| QQQ put_spread w10 | +0.68 → **+0.72** | 36.8 → **35.9** | +0.42 | **+0.30** | better | better |
| QQQ put_spread w5 | +0.29 → **+0.22** | 25.1 → **26.4** | +0.20 | **+0.02** | worse | worse |
| SPY iron_condor w10 | −1.11 → **−0.65** | 99.0 → **71.2** | +0.24 | −0.89 | better | better |
| SPY iron_condor w5 | −0.95 → **−0.84** | 84.2 → **75.6** | +0.11 | −0.95 | better | better |
| SPY put_spread w10 | +0.32 → **+0.35** | 33.8 → **33.2** | +0.29 | **+0.06** | better | better |
| SPY put_spread w5 | +0.07 → **+0.01** | 27.3 → **29.3** | +0.14 | −0.13 | worse | worse |

That is the strongest available evidence the corrected model is wired to the
thing it claims to charge: the predictions were made from `build()`'s own leg
prices before this run existed, and every sign matched.

---

## THE FINDING: clause 5 now PASSES for three arms, and the record said it would not

`hold%` did not exist in run 1. It exists now, and it says the opposite of what
run 1's prose asserted. Three arms **beat** the capital-matched benchmark:

- `QQQ put_spread w10` +0.72 vs +0.42
- `SPY put_spread w10` +0.35 vs +0.29
- `QQQ put_spread w5` +0.22 vs +0.20

Their printed failure reasons list only clause 1 (CI) and clause 2 (drawdown).
The "does not beat simply holding" line appears on the four condors and on
`SPY put_spread w5` — and nowhere else.

Run 1 wrote: *"Buy-and-hold SPY over the same decade returned several times that
at a comparable drawdown."* Measured, that is **wrong for the test clause 5
actually implements.**

### Why the prose and the clause disagree — they compare different things

`hold%` is **capital-matched**: it puts only the dollars the spread ties up
(~$900 of a $5,000 account for a 10-wide) into the index over the same cycles.
Back out the deployed capital and `hold%` is just SPY itself:

```
+0.29% of $5,000 = $14.50 per cycle on ~$900 deployed
                 = 1.61%/cycle = ~14.2%/yr     <- SPY's own return
```

So the put spread genuinely beats SPY **per dollar deployed**. What it does not
do is beat putting the *whole account* in SPY, which is what run 1's prose meant
and what someone with $5,000 actually chooses between. The strategy deploys
about 18% of the account, returns **+2.9%/yr on the whole account**, and carries
a **33.2% drawdown on the whole account**. The comparison that decides anything
is therefore not the one clause 5 runs.

**Clause 5 is a weaker test than its own results file claimed it was.** It is
not wrong — it correctly asks whether the premium or the delta paid you — but
passing it is not evidence the strategy is worth running.

### And the margin is far inside the noise

`SPY put_spread w10` beats hold by **+0.06%/cycle**. The smallest effect this
design can detect is **+1.51%/cycle**. The winning margin is **25× below the
detection threshold**. Three arms passing clause 5 is not three arms with a
demonstrated edge over holding; it is three arms whose point estimate landed on
the right side of a line the test cannot resolve.

---

## Cycle length was being mis-annualised, including by me

`dte=30` counts **sessions**, not calendar days, so a cycle is ~6 calendar weeks
and there are **8.3 per year, not 12**. Corrected:

| | quoted earlier | correct |
|---|---:|---:|
| cycles/year | 12 | **8.3** |
| SPY put_spread w10 | +3.84%/yr | **+2.91%/yr** |
| Sharpe | 0.226 | **0.206** |
| years to verify at t = 1.96 | 75 | **91** |

The direction of every earlier conclusion survives; the numbers were ~20% off.

## What still fails, and it is the same two things

- **Clause 1** — no arm's CI clears zero, at any of the four skews.
- **Clause 2** — every arm breaches the 25% drawdown bar. The best put spread
  carries 33.2%; the condors, 71–107%.

The condors improved a great deal and remain catastrophic. Drawdowns above 100%
mean the account went through zero, and `QQQ iron_condor w5` is still at 107.3%.

## Prediction scorecard

| prediction | outcome |
|---|---|
| FAIL stands, no arm clears | **right** |
| every arm moves in the cost table's predicted direction | **right**, 8 of 8 |
| SPY put_spread w10 → "roughly +0.35%/cycle" | **right**, +0.35 |
| condor → "roughly −0.94%/cycle" | **close**, −0.82 |
| "drawdowns essentially unchanged" | **wrong** — true for the put spreads (33.8 → 33.2) but badly wrong for the condors (143.7 → 101.1). An over-charge of ~$22/cycle across 83 cycles is ~$1,800 on a $5,000 account, and it compounds into the equity path |
| "still loses to holding the same dollars in the index" | **wrong** — three of four put-spread arms beat the capital-matched benchmark |

## Where this leaves the thesis — unchanged, on better evidence

The premium is real (`results/vrp_measurement.md`, +3.63 vol points, eleven for
eleven years). This vehicle still does not deliver it at this account size, and
now fails for two clean reasons rather than three muddled ones: **the CI does not
clear zero and the drawdown is a third of the account.** The opportunity-cost
argument, as clause 5 implements it, has been withdrawn — it was never measured
before today and it does not hold.

That does not reopen anything. The binding constraints are elsewhere and none of
them moved: power is invariant to the structure traded, the best arm's Sharpe is
0.21 against the 0.62 needed to verify in ten years, and one indivisible 10-wide
contract is 20% of a $5,000 account.
