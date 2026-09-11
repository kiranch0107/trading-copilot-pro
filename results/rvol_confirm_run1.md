# RVOL: REFUTED out-of-sample

**Run 2026-09-11. Pre-registered in `results/rvol_confirm_preregistration.md`,
committed before the fetch.**

## The primary test — tranche A, 16 clean tickers

**4,544 trades. 1,145 at RVOL ≥ 1.2, against a pre-registered floor of ~1,060 —
so the test was adequately powered and this is a genuine negative, not
UNMEASURABLE.**

| bucket | RVOL | trades | exp R | p | detectable |
|---|---|---|---|---|---|
| 1 | 0.75 | 909 | **+0.118** | 0.0300 | 0.152 |
| 2 | 0.86 | 909 | −0.033 | 0.5186 | 0.144 |
| 3 | 0.98 | 909 | +0.021 | 0.6966 | 0.151 |
| 4 | 1.15 | 909 | −0.006 | 0.9143 | 0.169 |
| 5 | 1.76 | 908 | +0.053 | 0.3558 | 0.160 |

**Spearman ρ = −0.10** against a +0.6 bar. No grading whatsoever.

Clause 4: RVOL ≥ 1.2 gives +0.040 R on 1,145 trades against +0.027 R on 3,399.
Difference **+0.013 R**, CI [−0.100, +0.127], **p = 0.8162**. Nothing.

## Every out-of-sample look, together

| universe | trades | ρ | clause 4 | status |
|---|---|---|---|---|
| 12 mega-cap tech — **discovery** | 3,406 | **+0.90** | **+0.147** | contaminated |
| 16 tranche A — **PRIMARY** | 4,544 | **−0.10** | +0.013 | **clean** |
| 32 tranche B — supplement | 9,131 | **−0.90** | −0.061 | contaminated |
| 48 combined — supplement | 13,675 | **−0.70** | −0.037 | mixed |

**The +0.90 existed only on the set the hypothesis was found on.** Every
independent look is flat or backwards. Two of the three show the high-volume
half doing *worse*.

## Verdict, as written before the run

> *"FAIL on sign or dose-response. The original ρ = +0.90 was a property of
> mega-cap tech over 2016–2026, not of the mechanism. Treated as REFUTED."*

**REFUTED.** Clauses 1–3 and clause 4 all fail on the clean primary.

## This is a textbook discovery-set illusion, and it is worth naming

The ρ = +0.90 was the most convincing thing this project produced. It was nearly
monotone, it matched a mechanism stated in advance, and it was the first
dose-response to pass after ADX (−0.70) and PEAD (−0.30) both failed that clause.

It was an artifact of the 12 tickers it was found on.

**The dose-response test did not prevent this.** It was passed *by* the
artifact. What caught it was held-out data — the one thing that cannot be
reasoned around. Both guards were necessary and neither was sufficient:
dose-response killed ADX without needing a tranche; only a tranche could kill
RVOL.

**Note bucket 1 in the primary run: +0.118 R at p = 0.0300 — the LOWEST volume
bucket, the only nominally significant one, and the exact opposite of the
hypothesis.** It fails Holm (needs ≤0.010). Anyone fishing would now be writing
up "low relative volume predicts returns". That is what fishing looks like from
the inside, and it is why the bar was fixed in advance.

## What this means for `volume_mult = 1.2` in live code

**Narrower than earlier sessions implied, and the earlier framing was imprecise.**

- `volume_soft_mult = 0.70` gates **whether a signal exists at all**. Not tested
  here.
- `volume_mult = 1.2` only sets the **"Strong" vs "Normal" strength tag** — and
  only in conjunction with an RSI extreme. It does not decide whether an alert
  fires.

The tag is not inert: `option_chain.get_option_data()` takes `strength` and
"Strong" shifts the strike window toward ITM/ATM. So it reaches the contract
that gets bought.

What was measured is **RVOL alone, not the RSI-and-volume conjunction**. So the
honest statement is narrow: *one of the two components of the "Strong" tag has
no demonstrated predictive value, on 4,544 clean trades.* The tag as a whole was
not tested and is not refuted.

**No live change is recommended on this result.** A null licenses neither
keeping nor removing. Ripping out a gate on a non-significant negative is the
same error as adopting one on a non-significant positive — the mistake ADX-35
made, with the sign reversed.

## The cost

**Tranche A is spent.** It was spent correctly: on a pre-registered test, with
the bar and the floor fixed in writing beforehand, and it returned a clear
answer instead of an ambiguous one. That is what held-out data is for, and it
worked exactly as designed.

**There is no clean data left.** Any future hypothesis in this repo must be
judged on contaminated data, or not at all. Combined with
`results/verifiability_screen.md` — which found the public factor literature
fails on Sharpe before a line is written — the research programme has now
exhausted both its ideas and its ability to test new ones.
