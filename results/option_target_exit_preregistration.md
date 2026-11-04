# PRE-REGISTRATION — D3: sell the option when the underlying reaches the signal's target?

**Owner's decision 2026-11-03 ("go with D3 and D7 as well"), on the expert
review's D3 (`results/expert_review_2026-11-03.md`, BACKLOG 46):
measure an arm before choosing. Written and committed BEFORE the run.** The
run needs the frozen bars on the laptop; nothing below may change after a
number from it is seen. Config: **live** (`backtest.live_cfg()`, BACKLOG 23).

> **Intake note 2026-11-04, before any run (BACKLOG 47).** The research
> engine's six review fixes became the default the day after this was
> written, before the run. Nothing above or below changes: the arms, the
> metric, the bar, the prediction and the two commands stand. The run reads
> its entries through `backtest.evaluate_signal()` on the new default, which
> skips the first 19 bars after each ticker's warm-up (the look-back fix),
> identically for both arms; the option fills and exits do not change. Each
> file prints `engine fixes: … (the default)` on its data line. The power
> check above ran on the engine before; the look-back fix removes under 2 %
> of entries, so the stated MDEs stand.

## The question

The alert names a target on the underlying; the traded structure sells the
call at **+200 % of the premium**. With the stock **at** a resistance-capped
target (1.3–2.0 ATR) the at-the-money call is worth only +24 % to +73 %, and
about +185 % at a full 4-ATR target (the repo's `bs_price`, IV = RV × 1.15,
7–14 days in). So the option's take-profit needs the stock to OVERSHOOT the
target the alert names, and a signal that "wins" on the share leg can still
exit the option on time, thesis or stop. Should the option be sold when the
underlying reaches the signal's own target?

## The mechanism (BACKLOG 13, step 1)

An exit rule creates no edge; it reshapes the payoff. With both arms marked
on the close and one round trip each, a Black-Scholes option under constant
IV is close to a fair game, so on a path with **no persistence** the two arms
should tie: selling at the target trades the rare large win for a frequent
smaller one at the same expectation. The difference between the arms
measures one thing, **whether moves after this signal keep running past its
target**. If they do, selling at the target gives up the run (arm B loses);
if they do not, it costs nothing and converts some later stops and time exits
into wins at the target (B ties or edges ahead). The research record says
the signal adds nothing to random entry (`drift_null`), which points at
little persistence.

## The arms (both on IDENTICAL entries)

- **Entries:** `option_backtest.pairs_on_frame()`: the live config's signals,
  longs only (`risk_params.LONGS_ONLY`), R:R at or above the tier's
  `hq_min_rr` (1.0), and not gapped at the fill (the next open beyond the
  stop or the target is withheld live by the fill gate, J1). Not modelled:
  the earnings blackout and D1 (no calendar in history), the SPY regime (the
  option engine never applied it). The same entries go to both arms.
- **A, the structure as measured:** ATM call, 30 DTE at entry, TP +200 %,
  SL −50 %, out at 7 DTE, thesis exit on, 5 % round trip.
- **B:** A, plus sell when the underlying **closes** at or beyond the
  signal's target, at that close's mark. Checked after STOP and before the
  premium TP. **Marked on the close, like every rule in the engine:** a
  version that sold at the intraday touch was tried first and dropped,
  because it priced B at the touch while A's +200 % is marked on the close,
  and on driftless random walks that asymmetry alone put B about 3.5 points
  ahead. The engine has no intraday option price for either arm.

## The metric

Per entry, **B − A** in percent of the premium (OPEN trades, censored by the
sample's end, are left out of both). Mean, sd, SE and the 95 % CI of the
paired difference, printed by `--target-exit-ab`.

## Power (BACKLOG 13, step 2) — computed before the run

`python option_backtest.py --d3-power` (seeded; synthetic daily paths, sd
1.7 % a day, through `backtest.compute`, the live signal and both arms of
this engine; no market data):

```
no persistence     iv x1.00: pairs  1245 | A  -0.27% B  -2.58% | B-A  -2.31% sd  43.7 | MDE n150 10.0 n300 7.1 n600 5.0
no persistence     iv x1.15: pairs  1244 | A  -6.79% B  -7.35% | B-A  -0.56% sd  44.2 | MDE n150 10.1 n300 7.1 n600 5.1
60-session trends  iv x1.00: pairs   712 | A +70.24% B +48.54% | B-A -21.71% sd  64.9 | MDE n150 14.8 n300 10.5 n600 7.4
60-session trends  iv x1.15: pairs   711 | A +64.29% B +39.25% | B-A -25.03% sd  68.3 | MDE n150 15.6 n300 11.0 n600 7.8
```

The paired difference's sd is about **44–68 % of the premium** per entry.
At n pairs the smallest detectable difference (80 %, 5 % two-sided) is
2.8 · sd / √n: about **10–16 %** at 150 pairs, **7–11 %** at 300, **5–8 %**
at 600 (`power_check.py --sd 45|65 --n 150|300|600` agrees). The run prints n. The
test can tell "the target exit costs a lot because moves keep running"
(−22 to −25 % in the persistent world above) from "it costs about nothing";
it cannot resolve a difference of a few points either way, and does not
claim to.

## The bar (fixed now)

The run is made at **IV 1.15 (primary) and IV 1.00**, because every option
number here rests on the IV assumption (`risk_params.OPT_IV_MULT`) and a
verdict whose sign flips with it is not a verdict.

- **ADOPT the target exit** for the live structure only if B − A > 0 with
  the 95 % CI excluding zero **at both** multipliers.
- **REJECT it, and record that holding past the target pays** if B − A < 0
  with the CI excluding zero at both.
- Otherwise **NOT ESTABLISHED**: the structure stays as measured; whether
  to tie the exit to the target on grounds of coherence (the option's exit
  matching the alert's target) is then the owner's call, made knowing the
  data could not separate the two.
- If n < 150 pairs the run is labelled UNDERPOWERED in its file whatever
  the CI says, and the reading above is not made from it.

## Prediction, stated now

B − A between −10 % and +2 % of the premium at both multipliers, most likely
**NOT ESTABLISHED**: the signal shows no drift beyond random entry, so little
persistence past its targets. If the record's grinding-bull sample carries
persistence, REJECT.

## The run (owed on the laptop, frozen bars)

```
python option_backtest.py --config live --tp 200 --sl 50 --dte-exit 7 --target-exit-ab | tee results/option_target_exit_run1_iv1.15.txt
python option_backtest.py --config live --tp 200 --sl 50 --dte-exit 7 --target-exit-ab --iv-mult 1.00 | tee results/option_target_exit_run1_iv1.00.txt
```

Default tickers and years (the seven of `option_backtest_default_run3`, five
years), so the bars are the frozen ones the structure was measured on. Each
file prints `Config : live`, the bar-cache fingerprint and the coverage.

## What it commits anyone to

Nothing beyond the reading above. An ADOPT is built as a change to
`MEASURED_OPTION_STRUCTURE`, the exit monitor and the journal together, as
the thesis exit was (BACKLOG 42 H1); a REJECT or NOT ESTABLISHED changes
nothing in the live system.

## Enforcement

`option_backtest.py --selftest` (the arm is off by default; a close at the
target sells at its mark, an intraday touch does not; the pair runner keeps
longs, R:R ≥ 1.0 and ungapped fills; the paired report reads B − A without
OPEN trades); `consistency_check.check_option_target_exit_preregistered`
(the arm is off in every committed run's config; this file names the
commands, the bar and the prediction).
