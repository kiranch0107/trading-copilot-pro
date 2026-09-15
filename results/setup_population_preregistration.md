# Pre-registration — setup readings across the whole population

Written and committed **before** `setup_population.py` exists. The commit
timestamp precedes the result.

## What this tests

Stage 1 looked at 80 hand-chosen cases out of 2,360 trades. On longs it found
nothing: every Holm-corrected p ≥ 0.43, RSI differing by +0.03, and no tercile
gradient in any of seven readings. On shorts it found four readings with
Cohen's d between 0.93 and 1.08 and raw p < 0.025, **none surviving Holm**, at
n = 12 vs 11.

Neither result is trustworthy at that n. This scores the same comparison —
**trades that reached their target vs trades that hit their stop** — across
every trade, so a null is a real null and a large effect has somewhere to die.

Timeouts are excluded. They are neither outcome, and assigning them to a side
invents a result the trade did not have.

## The readings, fixed now so the Holm denominator cannot drift

    1. planned R:R
    2. RSI
    3. ADX
    4. ATR %
    5. volume x (vol_ratio)
    6. price-EMA20, in ATR
    7. EMA20-EMA50, in ATR

Seven. **`stop %` is deliberately excluded**: 75 of 80 cases had it equal to
`ATR %` because the stop IS 1.0 x ATR. Including it would inflate the Holm
denominator with a duplicate and count one reading as two pieces of evidence.

`gates passed` is excluded because it cannot vary — `evaluate_signal()` returns
nothing unless every gate passes, and three of the four gates are disabled.

**Longs and shorts are tested separately, each with its own Holm denominator of
7.** They are different hypotheses about different setups, not 14 shots at one.
Pooling them was run 2's error: RSI runs 57.7-75.0 on longs and 25.2-47.4 on
shorts, so a pooled mean measures the sample's long/short mix.

## The pass bar — all three clauses, decided now

A reading counts as a bucket candidate only if:

1. **Statistical.** Welch two-sample t on target vs stop, two-sided, survives
   Holm at alpha = 0.05 within its own side.
2. **Dose-response.** Sorted into terciles by that reading, the target-hit rate
   is monotone across the three buckets in the direction clause 1 implies —
   Spearman rho on (tercile index, target rate) with |rho| = 1.0 and the right
   sign. **This clause can only sink a reading, never rescue one.** A shift of
   means with no gradient is what a lucky tail looks like.
3. **Economic.** The favoured tercile's **mean R must be positive.** This is the
   clause the earlier lines lacked and it is the one that matters most here.
   The whole population runs at mean R -0.012 — break-even to slightly
   negative. A reading can separate a 28% target rate from a 33% one, clear
   Holm at n = 1,700, and still leave every bucket losing money. Statistical
   separation on a zero-edge system is not an edge, and a bucket nobody can
   profitably trade is not worth encoding into the tool.

A reading that clears 1 and 2 but fails 3 is recorded as REAL BUT UNPROFITABLE
and is not carried to stage 2.

## Power, stated before the numbers arrive

Two-sample Welch, alpha 0.05, power 0.80: the detectable effect is

    d_mde = 2.802 * sqrt(1/n_target + 1/n_stop)

At the population's split — roughly 704 target and 1,580 stop across both
sides — longs alone should give a few hundred against a thousand-odd, so
d_mde lands near 0.20. The shorts' observed d ~ 1.0 is five times that. If the
shorts effect is real at anything like the size stage 1 showed, it cannot hide
here. If it vanishes, it was 23 cases.

## Predictions, recorded to be wrong in public

- **Longs: nothing survives.** Stage 1's null was flat across means, terciles
  and flags, and the population's mean R is ~0. I expect zero of seven.
- **Shorts: at most one of the four survives Holm, and I expect none to clear
  clause 3.** The four readings (volume, RSI, px-EMA20, R:R) are not four
  findings — they are one story about extension, and they will shrink together.
  d ~ 1.0 at n = 23 is what noise looks like when you pick the biggest of
  seven after the fact.
- **The most likely outcome is a complete null on both sides**, which ends the
  bucket line rather than advancing it.

I have been wrong on the direction of every prediction I have recorded in this
project so far — the ATR stop, the THESIS delta, the feature sweep, the
longs-only constraint. That record is the reason for writing this down before
running anything.

## What this is not

In-sample. Every one of these 20 tickers is spent. This has real statistical
power and **zero confirmatory value** — it generates a hypothesis with a
trustworthy n. Tranche C is still unspent and is still the only thing that
could confirm whatever comes out of here.
