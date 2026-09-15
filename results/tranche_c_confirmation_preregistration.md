# Pre-registration — tranche C confirmation of the break-even exit

**This spends the last clean data this project has.** Written and committed
before tranche C is claimed and before a single bar of it is fetched.

## The hypothesis, stated once and frozen

> Moving the stop to the entry price once a trade has traded **+1.0 R** in its
> favour improves mean R per trade, versus leaving the stop fixed.

One hypothesis. One threshold. Not a family, not a sweep.

## What was found in-sample, and why it is a candidate rather than a finding

`exit_ab` run 1, 2,360 trades, twenty **spent** tickers, paired by signal:

| arm | delta | p | verdict |
|---|---|---|---|
| **BREAK-EVEN** | **+0.0408 R** | **0.0093** | PASSES (Holm bar 0.0167) |
| TRAIL | +0.0266 R | 0.2483 | not established |
| PARTIAL | −0.0035 R | 0.7833 | not established |

The movement table accounts exactly: 252 NEAR MISS + 144 SHALLOW LOSS + 8
STALLED = 404 better; 55 SCARE + 36 STALLED + 35 CLEAN = 126 worse.

**Why this is worth the tranche and the earlier candidates were not.** Every
previous positive in this project was a best-of-N reading — take seven
features, see which pops, watch it die. This one ran the other way round: the
NEAR MISS bucket was identified first, the mechanism was written down first,
the +1.0 R threshold was fixed in `results/exit_ab_preregistration.md` before
the run and **has never been swept**, and the rule then moved exactly the
trades the mechanism named.

**What is uncomfortable about it, recorded here rather than discovered later:**

- Effect size **d = 0.054**. Tiny. The per-trade delta has sd 0.76 R.
- It is positive on **count, not magnitude**: 404 trades improved by +0.99 R
  each, 126 worsened by −2.40 R each. Worst single trade −8.14 R, a runner
  scratched at break-even.
- **Project-wide multiplicity is uncorrected.** Holm covered three arms inside
  one module. Across this project roughly forty tests have been run. One result
  at p = 0.0093 out of forty is close to what chance produces. That is the
  single best reason to doubt it, and it is the reason this test exists.

## The test

    python exit_ab.py --tickers <the twenty tranche C names> --years 10

Identical code, identical config (`backtest.DEFAULTS`, 10 years), identical
geometry, identical costs, paired by signal exactly as in-sample.

**The verdict is on BREAK-EVEN alone**, at α = 0.05, two-sided paired t on the
per-trade deltas. No Holm, because there is one pre-specified hypothesis.

TRAIL and PARTIAL will appear in the output because the module prints all three.
**They carry no verdict here and cannot rescue or sink the conclusion.** If
BREAK-EVEN fails and TRAIL happens to clear 0.05, that is not a result — it is
the best of three, which is exactly the move this document exists to refuse.

## Power, and what a null will and will not mean

At the observed d = 0.054:

| n | power |
|---|---|
| 1,500 | 55% |
| **~2,360** (what twenty tickers should give) | **75%** |
| 3,000 | 84% |

**There is a ~25% chance of a false negative even if the effect is exactly as
measured in-sample.** Fixed now so it cannot be deployed afterwards as an
excuse.

## The three outcomes, decided now

1. **Mean delta positive AND p < 0.05** → **CONFIRMED.** The first confirmed
   result this project has produced. It becomes a candidate for the live exit,
   after a separate decision about capital constraints and position sizing —
   not automatically.
2. **Mean delta positive AND p ≥ 0.05** → **NOT CONFIRMED.** At 75% power this
   is genuinely ambiguous, and the honest phrasing is "consistent with a real
   but small effect, not established". **It is not a pass**, it does not go
   live, and it is not re-tested on tranche C by another route. This is the
   outcome most likely to tempt reinterpretation, which is why it is written
   down first.
3. **Mean delta negative** → the in-sample result was noise, and the exit
   line closes alongside the entry line.

## One shot

Tranche C is spent by this run. There is no second look, no re-run with a
different threshold, no "let me check 1.2 R". The spend is recorded before the
fetch:

    python data_reservation.py --spend C --purpose "..."

and `check_clean` will refuse a second, different hypothesis on these names
afterwards. That refusal is not to be worked around.

## Prediction

**The effect shrinks.** Out-of-sample effects almost always do, and this one
starts at d = 0.054 with an uncorrected forty-test project behind it. My
expectation is a point estimate between 0 and +0.04 with p above 0.05 —
outcome 2, the ambiguous one.

I have been wrong on the direction of nearly every prediction recorded in this
project, most recently on this very rule, which I expected to be neutral to
slightly negative and which passed. That record is the reason these are written
before the run and not after.

## What a confirmation would and would not license

It would establish that a break-even stop improved mean R on data never used to
find it. It would **not** establish that the strategy makes money: the baseline
is −0.0173 R per trade and the improvement takes it to roughly +0.02, which is
thin, unmodelled for capital constraints, and measured on twenty large-cap US
equities over one bull decade. Going live remains a separate decision requiring
the position-sizing and concurrency work in `longs_only.py`.
