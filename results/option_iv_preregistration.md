# PRE-REGISTRATION — the option engine's IV assumption (BACKLOG 25)

**Written 2026-10-08 at the owner's request, and committed BEFORE any of the
runs below.** Everything here is fixed. It may be AMENDED in a dated section
at the top only until the first run under it is committed; after that nothing
changes.

## AMENDED 2026-10-08, WITH PART B's CODE AND BEFORE ITS FIRST ROW — implementation details, no rule changes

Part A has run (`option_iv_sensitivity_run1.md`), so the only part of this
document still open to amendment is Part B, and only until its first snapshot
row. Fixed here:

1. **Units.** Implied and realised vol are stored in vol points (45.0 = 45 %),
   the units `vrp_check.py` and `vrp_measurement.md` use.
2. **ATM.** The strike nearest spot within 10 %; IV is the mean of the call
   and put IV at that strike when both quote, else the one that does. One
   expiry: the nearest inside 21–45 DTE.
3. **One row per name per session**, taken by the post-close pass only, with
   the day's settled close as spot. Refusals (DTE outside the band, IV
   missing or absurd, strike not at the money, a second row in a day) are
   counted, never written.
4. **Grading** is done by the scanner, 21 sessions after the snapshot, from
   the bars it already holds; one premium row per snapshot, never partial.
5. **The reading gate is 126 distinct snapshot sessions**
   (`READING_SESSIONS`); `report()` prints "NOT A READING" before that.
   Pooling is through the daily cross-name mean and its HAC standard error
   (lag 20), which carries the between-name correlation without a separate
   correction.

## The finding

`option_backtest.simulate_option_trade()` prices every contract, on every bar
of its life, at `iv = realised_vol(20d) × iv_mult`, with `iv_mult = 1.15` set
before anything was measured. No vol-of-vol, no crush, no skew, no term
structure. The only measurement in the repo is `vrp_measurement.md`: **+3.63
vol points on SPX** (VIX against forward realised), which says in its own text
that it does not transfer to single names. The engine runs on seven single
names. Everything downstream inherits the constant: `OPT_WIN_RATE = 0.249`,
`OPT_SWEEP_BY_TP`, the realised-breakeven table, the spread ceiling's
rationale, the app's EV panel.

## What can and cannot be measured from this data source — said first

Historical single-name implied volatility is **not available** from the
provider this repo uses. Yahoo serves no per-name IV series, and the CBOE
single-name indices (VXAPL, VXAZN, VXGOG) cover three names, end in 2021, and
are not reachable from the harness. So "derive `iv_mult` from a single-name
measurement" cannot be done from history. It can be done **forward**: the
live option chain the app already fetches carries each contract's implied
vol. That makes this two measurements with different clocks, pre-registered
together so the quick one is not mistaken for the slow one.

## Part A — the sensitivity sweep (now; the laptop; one afternoon)

**Question.** How much of what the option engine concluded depends on the
guessed constant?

**Design, fixed.** `option_backtest.py --sweep` at four multipliers, nothing
else varied, on the engine's default seven names and 5-year window:

```
for m in 1.00 1.15 1.30 1.50; do
  python option_backtest.py --sweep --iv-mult $m | tee results/option_iv_sensitivity_$m.txt
done
```

1.00 is "no premium"; 1.15 is the current constant; 1.30 and 1.50 bracket
what a single name with earnings and jump risk might carry. The output of
each run already prints its multiplier in the header line (`IV: 1.15x
realised`) — that line is the assumption recorded on the result, and from
this document on every option result in `results/` must carry it.

**What is read, in this order, and the bar for each.**

1. **Ranking of take-profit levels by expectancy** at each multiplier. The
   sweep's standing conclusion is "a wider target is hit strictly less often
   and nothing clears breakeven". **Robust** if the ordering of TP levels by
   win rate is the same at all four multipliers. **Assumption-dependent** if
   any two levels swap.
2. **Sign of expectancy** at every TP level, every multiplier. Robust if
   every cell stays negative. If any cell turns positive at any multiplier,
   that cell's structure is **unmeasured**, not promising: it means the
   verdict on that structure is the constant's, and it may not be quoted
   either way until Part B has a number.
3. **The size of the move in `OPT_WIN_RATE`** (TP+100 / SL−50) across
   1.00 → 1.50. Reported as a range. This is the caveat that goes on the
   constant in `risk_params.py`.

This is a sensitivity analysis, not a hypothesis test: there is no sample to
power. Its product is a sentence of the form "the engine's conclusions
are / are not invariant to the IV assumption over 1.00–1.50", and a range.

**What Part A changes in code, regardless of outcome.** `risk_params.OPT_IV_MULT
= 1.15` becomes the one home for the constant, with its caveat; `option_backtest`
reads its default from there; a consistency check pins the two together and
requires the IV line on every committed option result.

## Part B — the single-name premium, measured forward (months)

**Question.** For the names the engine runs on, is implied vol above the
realised vol that followed, and by how much — the same question
`vrp_check.py` answered for SPX, on the names that matter.

**Design, fixed.**

- A daily snapshot, after the close, of each watchlist name's **at-the-money
  implied vol at the nearest expiry with 21–45 DTE** (the band
  `option_chain.py` already selects from), together with trailing 20-day
  realised vol, written to `iv_snapshots.jsonl` by the scanner's post-close
  pass. NOT the forward log: different file, same single-writer rule, no hash
  chain needed because nothing here is a claim about a trade.
- The premium for day t is `implied[t] − realised over the next 21 sessions`,
  computed by `vrp_check.py`'s existing code path once the forward window has
  closed — same horizon matching, same HAC correction for overlapping
  windows, same dropped-tail accounting.
- **Per name**, and pooled across names only with the between-name
  correlation discounted (`power_check.py --corr`).

**Power, stated now.** SPX had ~119 independent 21-session windows in ten
years and a HAC standard error of 0.63 vol points. Six months of snapshots
is ~6 independent windows per name; across 7–8 names at a between-name
correlation of roughly 0.5 that is ~25 effective windows, SE ≈ 1.4 points.
Enough to see a premium the size of SPX's (+3.6) at 80% power; not enough to
see one half that size. **The first reading is at six months, not before, and
it is reported with its SE whatever it says.** A thinner premium than SPX's
is the expected result for single names (idiosyncratic jump risk is priced,
but so is the stock's own drift), and would mean the engine's 1.15 is, if
anything, generous to the buyer.

**What Part B changes.** Nothing until it reads. Then `OPT_IV_MULT` becomes
`1 + premium / mean realised` per the measurement, the sweep is re-run at
that value, and every downstream constant moves with it — one change, per
CLAUDE.md.

## What is deliberately NOT in this pre-registration

- **A path-dependent IV model** (spot–vol correlation, crush at earnings,
  vol-of-vol). It is a different engine and would need its own validation
  against observed premium paths, which this data source does not have. The
  two live positions that went −50% rule → −82% realised are the kind of
  gap a constant-IV path cannot produce; Part B's snapshots, taken daily,
  will at least show how often ATM IV moves by more than the model's zero.
  Recording that is the most this repo can honestly do about it now.
- **Skew.** The engine prices ATM only, which is what the app selects.

## Enforcement

- `risk_params.OPT_IV_MULT` single-sourced; `option_backtest` default reads
  it; `check_option_iv_single_source()` pins both and requires the
  `IV: <x>x realised` line in every `results/option_*` file.
- `iv_snapshot` rows carry date, ticker, expiry, DTE, ATM strike, implied vol,
  realised vol, and the chain's source; the writer refuses a row whose DTE is
  outside 21–45 or whose IV is missing, and counts the refusal.
- Both falsified before merge, per the repo rule.
