# Tranche C confirmation — result, and a process failure that was mine

Tranche C is spent. `data_reservation` records the purpose written in the
pre-registration, claimed before the fetch. There is no clean data left in this
project.

## Two things came back

### 1. The break-even rule replicated

| | in-sample (spent 20) | tranche C (fresh 20) |
|---|---|---|
| paired delta | +0.0408 R | **+0.0342 R** |
| p | 0.0093 | **0.0304** |
| effect size d | 0.054 | 0.047 |
| n | 2,360 | 2,128 |
| improvements from NEAR MISS + SHALLOW LOSS | 396 of 404 | **322 of 335** |

**84% of the effect retained**, and the mechanism replicated: the same two
buckets supply the gains in the same proportion. Out-of-sample effects usually
shrink much harder than this.

Recorded prediction was "between 0 and +0.04, p above 0.05". Wrong, in the
direction of the result being real.

### 2. The strategy still loses money

| | baseline | with the rule |
|---|---|---|
| in-sample | −0.0173 R | **+0.0235 R** — crosses zero |
| **tranche C** | **−0.0591 R** | **−0.0248 R** — does not |

On twenty names never used to build anything, the system loses 0.059 R per
trade. The rule genuinely improves it, and improves it to still losing.

The baseline is also **3.4× worse** than in-sample. That is the larger finding:
the signal's own performance is not stable across universes, and on one it was
not developed against, it is clearly negative.

## THE PROCESS FAILURE

**The module printed `NOT ESTABLISHED`. The pre-registration says `CONFIRMED`.**

`results/tranche_c_confirmation_preregistration.md`, committed before the
tranche was claimed, states:

> The verdict is on BREAK-EVEN alone, at α = 0.05, two-sided paired t on the
> per-trade deltas. No Holm, because there is one pre-specified hypothesis.

and, pre-emptively:

> TRAIL and PARTIAL will appear in the output because the module prints all
> three. They carry no verdict here and cannot rescue or sink the conclusion.

Under that bar, p 0.0304 < 0.05 with a positive delta is a pass. `exit_ab`
applied Holm across three arms (0.0167) because that is the exploratory
protocol it was built for.

**This mismatch is my error.** I wrote a pre-registration describing a
single-hypothesis protocol and pointed it at a module implementing a three-arm
one. A `--confirm` mode should have existed before the run. The failure put the
project in precisely the position the whole apparatus exists to prevent: a
document and an output disagreeing, with the document favouring the nicer
answer.

### The reading, and the argument against it

The pre-registered reading governs, on two grounds. It predates the data by
construction — the commit is before the spend. And the protocol is
statistically sound: Holm corrected the SELECTION in-sample, where break-even
was chosen as best-of-three; tranche C then tested that one selected
hypothesis. Select in-sample, confirm out-of-sample on one, is the standard
shape, and it is why the uncorrected α is earned rather than helped to.

**What genuinely weakens it, stated plainly:** all three arms came back
positive on tranche C — +0.0342, +0.0384, +0.0234. Had all three been
pre-registered, none would have passed. The single-hypothesis framing is
load-bearing. It is legitimate, and it is doing work.

A reader who discounts this result entirely on those grounds is not being
unreasonable. The honest summary is: **confirmed under the pre-registered
protocol, with the caveat above attached permanently.**

## What was NOT done

The module was not edited to make its printed verdict agree with the favourable
reading before this note was written. A `--confirm` mode is being added as a
remedy for the next test, not as a re-scoring of this one. The tranche C
numbers are what they are and this note records both readings.

## Where the project stands

- **Entry logic: no edge.** Setup readings null at n = 1,457 with power to
  detect d = 0.153. Random entries matched or beat the signal (`drift_null`).
- **Short side: worse than random**, switched off live (`risk_params.LONGS_ONLY`).
- **Inversion: missed** its bar by 0.008 R on the fill-corrected basis.
- **Exit management: one small confirmed improvement**, +0.034 R per trade.
- **Net: the system loses money on data it was not built on**, before and after
  that improvement.

The break-even exit is the one confirmed result this project has produced. It
is real, it is small, and it is not enough.
