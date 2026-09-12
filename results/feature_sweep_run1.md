# Feature sweep — run 1: PASS by the bar, and the bar had a hole

**Run 2026-09-12, `python feature_sweep.py --years 10` on the OOS 12. Code at
`a287fe3`.** Pre-registered in `results/feature_sweep_preregistration.md`.

## Verdict as recorded: PASS

> `rsi` survives every clause: filtered expectancy **+2.66%** keeping 20% of signals

1398 trades, base expectancy **−9.27%** (matching `option_decompose` exactly),
base win rate 22.3%, breakeven 33.3%.

| feature | lo exp | hi exp | t | p | Holm | rho |
|---|---:|---:|---:|---:|:---:|---:|
| side | −20.4 | −3.1 | 4.72 | 0.0000 | **YES** | +1.00 |
| ema_spread | −28.2 | −7.3 | 3.99 | 0.0001 | **YES** | +0.60 |
| rsi | −20.5 | +2.7 | 3.67 | 0.0002 | **YES** | +1.00 |
| atr_pct | +0.4 | −20.6 | −3.33 | 0.0009 | **YES** | −0.90 |
| macd_hist | −16.3 | +2.9 | 3.22 | 0.0013 | **YES** | +0.80 |
| ema20_dist | −20.4 | −2.1 | 3.05 | 0.0023 | **YES** | +0.90 |
| rv | −4.4 | −19.4 | −2.43 | 0.0152 | no | −0.90 |
| prem_pct | −5.4 | −20.2 | −2.42 | 0.0154 | no | −0.80 |
| rvol | −11.5 | −17.9 | −1.16 | 0.2477 | no | −0.10 |
| adx | −6.5 | −11.5 | −0.77 | 0.4403 | no | −0.60 |

**My prediction was wrong.** I recorded "no feature survives Holm, and `side`
comes closest without clearing clause 4." Six survived. The half I got right is
that `side` has the smallest p.

---

## THE HOLE IN MY BAR: clause 4 tested a point estimate, not an interval

Clause 4 required the filtered subset's expectancy to be **positive**. It did not
require that to be *distinguishable from zero*. Those are different claims and I
wrote the weaker one.

Backing the sd out of the reported t (23.2 / 3.67 → se 6.32 → **sd ≈ 74.8%**):

```
filtered expectancy   +2.66%   on n = 282
standard error         4.45%
95% CI              [-6.07, +11.39]%
```

**The CI spans zero.** To clear it the filtered subset would need **+8.73%**; it
has **+2.66%** — short by a factor of **3.3**.

The ATR stop test's clause 5 got this right — *"the destination, not the
direction"* — and I did not carry the lesson across. The improvement from −9.27%
to +2.66% is real and significant (that is what the t-test measured). **The
destination being above zero is not.**

## FIVE OF THE SIX SURVIVORS ARE THE SAME EFFECT

| feature | rho | what it says |
|---|---:|---|
| `side` | +1.00 | CALL beats PUT |
| `rsi` | +1.00 | high RSI = bullish |
| `ema20_dist` | +0.90 | price above EMA20 = bullish |
| `macd_hist` | +0.80 | positive histogram = bullish |
| `ema_spread` | +0.60 | EMA20 above EMA50 = bullish |
| `atr_pct` | −0.90 | low volatility — **a different axis** |

```
side:  CALL  n=897  exp  -3.1%
       PUT   n=501  exp -20.4%
```

Five of the six are proxies for *"this setup is bullish"*, which is `side`.
**Holm corrects for multiplicity, not for correlation.** Six views of one effect
are not six pieces of evidence, and the sweep's design cannot tell them apart
because it tests every feature **marginally** and none **conditionally**.

### And that one effect is already in the record, already judged

`results/longshort_split.md`, share level:

```
LONG   +0.268  ->  +0.106  ->  +0.085     CI [-0.037, +0.206] at 10y — spans zero
SHORT  -0.409  ->  -0.210  ->  -0.287     CI [-0.432, -0.141] at 10y
```

That file's own diagnosis of the long side's monotone decay: *"the signature of
an estimate inflated by the noise that made it visible in the first place."*

So the sweep's headline finding is **the known long/short asymmetry, re-found
through six correlated lenses**. The short side is reliably bad; the long side
has never cleared zero outside its discovery sample.

## What `atr_pct` is, and why it is the more interesting row

`atr_pct` is the one survivor on a different axis: **low** volatility at entry
does better (+0.4% vs −20.6%, rho −0.90). `rv` (−0.90) and `prem_pct` (−0.80)
point the same way without clearing Holm — three measures of "the option was
cheap relative to the move required" all agreeing.

That is a coherent, mechanically sensible story: TP +100% needs the premium to
double, and a fat premium needs a bigger move to get there. It is **not** a
restatement of `side`.

It is also not established. `atr_pct` cleared Holm; whether it survives
conditional on `side` is untested.

## What this run does and does not license

The pre-registration fixed this before any number existed, and it is unchanged by
the PASS: **both tranches are spent**, so this is out-of-sample against nothing
and can never be confirmed. A PASS licenses **forward paper-tracking of the
filtered subset and nothing else**.

Given the CI above, even that should be read as tracking a hypothesis whose
central estimate is positive and whose interval is not.

**Do not size anything off +2.66%.**

## The follow-up this run earns, stated as a new test not a reinterpretation

The verdict stands as the bar produced it. The two facts above are **disclosures
the bar did not ask for**, not grounds for re-judging it after the fact. What
they justify is a tightened successor, declared separately:

1. **A CI clause.** The filtered subset's expectancy must clear zero *with its
   interval*, not its point estimate.
2. **A conditional test.** Within CALLs only, does any feature still separate?
   If the answer is no, the sweep found `side` six times and nothing else. If
   `atr_pct` survives conditioning, that is a genuinely new second axis.

The second is the one worth running. It is cheap, it is decisive either way, and
it is the question this design could not answer by construction.
