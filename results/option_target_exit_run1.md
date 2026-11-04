# D3 run 1 — sell the option when the underlying reaches the signal's target? UNDERPOWERED (149 pairs, floor 150)

**Run by the owner 2026-11-04 in the Codespace that holds the frozen bars,
the two commands of `results/option_target_exit_preregistration.md`, main @
`90ba70d`.** Raw output: `option_target_exit_run1_iv1.15.txt`,
`option_target_exit_run1_iv1.00.txt`. Both print `Config : live`, the
frozen fingerprint `v2-78390259672cedbe` (7 series, 7 cached / 0 fetched:
the same bars as `option_iv_sensitivity_run3` and the structure's own
measurement), all 7 tickers covered, and the engine they ran on (the six
fixes, the default since 2026-11-04, as the pre-registration's intake note
said). Priced under IV: 1.15x realised (the primary run) and IV: 1.0x
realised (the second), as each file's header states.

## Verdict: UNDERPOWERED — no reading is made

Each run settled **149 pairs**. The pre-registration, fixed before any number
was seen: "If n < 150 pairs the run is labelled UNDERPOWERED in its file
whatever the CI says, and the reading above is not made from it." Both
files carry the label. **No ADOPT, REJECT or NOT ESTABLISHED is read.** The
live structure stays as measured (`MEASURED_OPTION_STRUCTURE`: TP +200 %,
SL −50 %, out at 7 DTE, thesis exit on), exactly as a NOT ESTABLISHED would
have left it.

## The numbers, descriptive only

| | IV x1.15 (primary) | IV x1.00 |
|---|---:|---:|
| pairs (settled in both arms) | 149 | 149 |
| pairs whose two arms differ | 28 | 27 |
| mean A (the structure as measured), % of premium | +0.07 | +10.00 |
| mean B (A + sell at the underlying's target) | +1.84 | +10.53 |
| **B − A** | **+1.77** (sd 36.4, SE 2.98) | **+0.53** (sd 40.8, SE 3.34) |
| 95 % CI of B − A | [−4.07, +7.61] | [−6.01, +7.08] |
| B's exits | STOP 65, TARGET_U 31, TIME 39, THESIS 12, TARGET 2 | STOP 72, TARGET_U 31, TIME 32, THESIS 9, TARGET 5 |

What they show, without a verdict: the target exit changes the outcome on
fewer than one pair in five (28 and 27 of 149), and over all 149 pairs the
two arms' means are within two points of premium of each other at both
multipliers (on the pairs that differ, B is ahead by about 9 and 3 points
on average). That is
the pre-registration's predicted range (B − A between −10 % and +2 %),
and the sd (36–41 % of premium) sits under the power check's 44–68, so the
95 % CI is about ±6 points; the smallest difference 149 pairs could detect
at 80 % power, from this sd, is 8–9 points (the power check said about 10).

## What is not done, and why

- **No re-run to clear 150.** The shortfall is one pair. The look-back fix
  (default since 2026-11-04) skips each ticker's first 19 bars after the
  warm-up; the intake note said it removes under 2 % of entries, and it
  plausibly cost the pair. Re-running on the earlier engine, or adding
  years or tickers, after seeing these numbers would be choosing the
  analysis by its result. A larger D3 would be a new pre-registration.
- **Nothing in the live system changes.** The pre-registration commits to
  a change only on ADOPT.

## Also visible, outside D3's question

Arm A, the structure as measured, averages **+0.07 % of premium at the
default IV assumption (x1.15) and +10 % at x1.00**: the option structure's
expectancy turns on the IV assumption, as `option_iv_sensitivity_run3`
found. The single-name IV premium record (`iv_snapshot`, first reading
2027-04-08) is the measurement that will say which assumption is real.
