# Conditional test — run 1: nothing beyond `side`

**Run 2026-09-12, `python conditional_test.py --years 10` on the OOS 12.
Code at `a47d5b4`.** Pre-registered in
`results/conditional_test_preregistration.md`.

## Verdict: NOTHING BEYOND `side`

> clause 2: neither `rsi` nor `atr_pct` separates within CALLs — best is `rsi`
> at p **0.1707**, needing **0.0250**

CALL stratum 897, PUT stratum 501 (descriptive only).

| CALLs | top n | rest n | top exp | rest exp | t | p | Holm | rho | top 95% CI |
|---|---:|---:|---:|---:|---:|---:|:---:|---:|---|
| rsi | 299 | 598 | +2.19 | −5.67 | 1.37 | 0.1707 | no | +0.50 | [−7.2, +11.6] |
| atr_pct | 299 | 598 | −5.10 | −2.02 | −0.59 | 0.5580 | no | −1.00 | [−13.0, +2.8] |

## `rsi` was `side` wearing a different hat

| | t | p | rho |
|---|---:|---:|---:|
| marginal (run 1, whole sample) | 3.67 | **0.0002** | +1.00 |
| **conditional (within CALLs)** | **1.37** | **0.1707** | **+0.50** |

Holding `side` fixed took `rsi` from highly significant to nothing. It also lost
its monotonicity: the terciles read **−4.0, −7.4, +2.2** — the middle group is
*worse* than the bottom, which is not a gradient.

That is the cleanest possible answer to the question run 1 could not ask. **Run 1
found `side` six times.**

## A design flaw, and the check that it did not change the answer

`top_vs_rest()` compares the **highest** tercile against the rest. `atr_pct` is a
**low-is-good** feature (ρ −1.00), so the module tested the *worst* group and
asked whether it beat the others. That is the wrong tail.

Recomputed on the correct tail — bottom tercile against the other two:

```
bottom tercile  +0.5%   vs rest  -4.85%    diff +5.35%
se 5.30  ->  t 1.01   p 0.3126     (Holm needs 0.0250)
CI on the bottom tercile: [-8.0, +9.0]%
```

**Fails clause 2 and clause 4 on the correct tail as well.** The flaw is real and
should be fixed before this module is reused; it did not change this verdict, and
that was checked rather than assumed.

## What `atr_pct` actually did

It kept a **perfect ordering** (ρ −1.00, terciles +0.5 / −4.6 / −5.1) and lost
all magnitude (p 0.5580). Low volatility at entry is still pointing the right
way; the gap is 5.35 points where the design needs ~16 to detect one.

That is the honest shape of it: **a direction that survives conditioning and a
magnitude that does not.** Not refuted, not established, and not distinguishable
from noise at any sample this project can reach.

## The PUT stratum, descriptive only

Never tested, never in the Holm family, and reported for symmetry:

```
rsi      terciles  -23.4 / -19.9 / -17.8      win 18.0 / 17.4 / 12.0 %
atr_pct  terciles  -12.8 / -22.6 / -25.8      win 20.4 / 13.8 / 13.2 %
```

Uniformly bad across every bucket of both features. The short side is not a
population with a good corner in it.

## Prediction scorecard

| prediction | outcome |
|---|---|
| neither feature separates within CALLs | **right** |
| `rsi` shrinks sharply once `side` is fixed | **right** — p 0.0002 → 0.1707, and ρ +1.00 → +0.50 |
| `atr_pct` lands "real and unprovable" | **right in shape** — ρ held at −1.00, magnitude vanished |

Recorded because the run-1 prediction was wrong. One right, one wrong across the
two runs.

## What this closes

The entry-feature line is finished. The only structure in the data is the
long/short asymmetry, which is:

- already in `results/longshort_split.md`
- already decaying: **+0.268 → +0.106 → +0.085**, CI [−0.037, +0.206] at 10y
- already diagnosed there as *"an estimate inflated by the noise that made it
  visible in the first place"*

**Do not condition on something else.** Each additional stratum costs the sample
that makes the next test possible, and the sample is already spent — both
tranches, permanently. There is no clean data left to promote any survivor, and
after this run there is no survivor to promote.
