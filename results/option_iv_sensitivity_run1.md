# Option engine IV sensitivity — run 1 (Part A of `option_iv_preregistration.md`)

**Run by the owner 2026-10-08 on a laptop with Yahoo access**, four
multipliers, nothing else varied: `option_backtest.py --sweep --iv-mult m` on
the engine's seven names (TSLA NVDA AAPL MSFT AMZN META ROKU), 5y to
2026-10-08, 393 trades per row at every multiplier (the signal does not
depend on IV; only the pricing does). Priced at IV: 1.00x realised, 1.15x
realised, 1.30x realised and 1.50x realised respectively. Files: `option_iv_sensitivity_1.00.txt`
… `_1.50.txt`. Read in the pre-registered order.

## 1. Ordering of take-profit levels by win rate — ROBUST

| TP / SL−50 | 1.00 | 1.15 | 1.30 | 1.50 |
|---|---|---|---|---|
| +50 | 37.9 | 33.1 | 30.0 | 26.2 |
| +75 | 30.8 | 27.5 | 25.7 | 23.2 |
| +100 | 27.7 | 25.4 | 24.7 | 20.6 |
| +150 | 25.2 | 22.6 | 21.6 | 19.1 |
| +200 | 23.4 | 22.1 | 21.6 | 19.1 |
| +300 | 23.4 | 22.1 | 21.6 | 19.1 |

Monotone at every multiplier, with the same tie at the top (+200 and +300 are
hit on the same trades: the DTE-7 floor closes them before either level).
"A wider target is hit strictly less often" does not depend on the IV
assumption.

## 2. Sign of expectancy — NOT ROBUST at TP ≥ +100

| TP / SL−50 | 1.00 | 1.15 | 1.30 | 1.50 |
|---|---|---|---|---|
| +50 | −1.68 | −6.60 | −8.90 | −11.49 |
| +75 | −0.28 | −4.79 | −8.45 | −10.40 |
| +100 | **+0.69** | −3.10 | −5.63 | −12.00 |
| +150 | **+3.82** | −3.84 | −7.21 | −11.72 |
| +200 | **+3.51** | −3.05 | −6.58 | −11.35 |
| +300 | **+2.12** | −2.68 | −6.57 | −10.72 |

(% of premium risked per trade, 5% round-trip spread charged.)

With **no** volatility premium the four structures from TP+100 up have
positive expectancy; with **any** premium they are negative, and more so the
richer the option. Per the pre-registration this does not make those
structures promising: it makes them **unmeasured**. The verdict on the live
rule (TP+200 / SL−50) is the constant's, not the data's, and it may not be
quoted either way — not "the option leg loses", not "it would win without the
premium" — until Part B has a single-name number. The +50 and +75 structures
are negative at every multiplier and stay refuted.

What this means in plain terms: the entire question of whether buying these
options could have paid is the question of whether these names' implied vol
sits above what they go on to realise. That is exactly what nothing in this
repo has measured on these names, and exactly what Part B measures.

## 3. `OPT_WIN_RATE` across the bracket

TP+100 / SL−50: **27.7% → 25.4% → 24.7% → 20.6%** for 1.00 → 1.50. The
committed constant is 24.9% (2026-09-10, 398 trades); today's 1.15 run reads
25.4% on 393 trades — window drift, not code. The table is recorded as
`risk_params.OPT_WIN_RATE_BY_IV_MULT`, and the multiplier now has one home,
`risk_params.OPT_IV_MULT`, which `option_backtest.py` reads as its default.
`check_option_iv_single_source()` holds the table to these files and
requires the `IV: <x>x realised` line on every option result.

## The sentence

The engine's structural conclusions — which take-profit is hit more often,
and that +50 / +75 lose — are invariant to the IV assumption over 1.00–1.50.
Whether the live TP+200 structure has positive expectancy is not; it flips
sign between 1.00 and 1.15 and is unmeasured until the forward single-name
premium (Part B) reads, at six months, with its standard error.

## Caveats carried from the engine

Option prices are modelled (Black-Scholes on the real underlying path, ATM
only, constant IV for the life of the trade, 5% spread). The comparison
between rows is far more reliable than any absolute number, which is why the
pre-registration read ordering before sign and sign before size.
