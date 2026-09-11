# PRE-REGISTRATION — the 1.5× ATR stop

**Written 2026-09-11, committed BEFORE the study is built or run.** Nothing here
may change once a number is seen.

## What kind of question this is

**Not an edge hypothesis.** Every prior test in this repo asked "is there an
anomaly here?" This asks something narrower and more mechanical: *is the current
exit rule mis-calibrated against the instrument's own volatility?*

The signal has no demonstrated edge at a 1× ATR stop. This asks whether that is
a property of the signal or of the stop.

## The mechanism, stated first

A 1× ATR stop sits **inside the instrument's own daily noise band**. A stop that
narrow is hit by ordinary daily range rather than by the thesis failing, and the
extreme case is measured: **~17.5% of trades die on the bar they opened on, 84%
of those lose, averaging −0.770 R.** Arithmetically that group is the *entire*
negative expectancy:

```
0.172 × (−0.770) + 0.828 × (+0.101) = −0.048
```

It reproduces across three cuts spanning different tickers and different
decades, so it is structural to the stop width, not a quirk of one sample.

Widening the stop trades a worse payoff for fewer noise-stops. Which side wins
is the question.

## Why the usual dose-response clause does NOT apply here

Every previous study in this repo required expectancy to grade monotonically
with the swept parameter. **That would be the wrong prediction here**, and
reusing it would be cargo-culting a clause that fits a different shape of
hypothesis.

Expectancy is a composite:

```
E = WR × payoff − (1 − WR)
```

As the stop widens, win rate **rises** and payoff **falls**. The product has an
**interior optimum**, not a ramp. An expectancy that rose monotonically across
every stop width tested would actually be evidence something is wrong.

What the mechanism *does* predict monotonically, and what is therefore tested:

| quantity | predicted | why |
|---|---|---|
| same-bar death rate | **falls** | directly mechanical — a wider stop is harder to hit on the opening bar |
| win rate | **rises** | fewer trades stopped by noise before the thesis resolves |
| payoff | falls | arithmetic, not a test — reported, not judged |

## Design: paired by signal

Trades are paired on `(ticker, entry_date)` across stop settings. A signal that
hits neither stop produces an **identical** trade in both arms, so ΔR = 0 and
the variance concentrates entirely in the pairs that actually differ.

This is materially more powerful than comparing two independent samples, and it
is available because `backtest.run()` tags each trade with its ticker and entry
date.

## Power, checked before unblinding

The paired standard deviation will be computed **before** the paired mean is
looked at. Computing a spread reveals nothing about direction, so this is not
peeking. If the design cannot detect the effect, that is reported as
UNMEASURABLE and no mean is quoted.

## PRE-REGISTERED BAR

Swept stops: **1.0, 1.25, 1.5, 2.0 × ATR**, target fixed at 3.0 × ATR.

    The wider stop is an improvement worth adopting only if ALL of:

      1. the paired mean ΔR versus the 1.0× baseline is positive and its 95% CI
         clears zero
      2. |ΔR| is at least the minimum detectable effect for the paired sample —
         otherwise UNMEASURABLE, not "no effect"
      3. the same-bar death rate falls monotonically across the swept widths
      4. the win rate rises monotonically across the swept widths
      5. **the resulting expectancy clears ZERO, not merely improves**

**Clause 5 is the one that matters and it is the easiest to fudge.** Moving from
−0.048 R to −0.020 R would satisfy clauses 1–4 and still lose money on every
trade. A significant improvement to a losing system is not a tradeable result,
and this bar refuses to call it one.

## What a pass would and would not license

**Would:** changing `atr_stop_mult` in `signal_core.DEFAULTS`, since this is a
measurement of the exit rule rather than a claim about an anomaly.

**Would NOT:** be treated as confirmed. **Both tranches are spent.** There is no
clean data left in this project, so any positive result here is out-of-sample
against nothing and remains a hypothesis permanently. It must be recorded as
such, and a future session must not find it and mistake it for a validated
finding.

That constraint is why clause 5 is absolute rather than directional: with no
confirmation possible, the in-sample result has to clear the bar that matters on
its own.
