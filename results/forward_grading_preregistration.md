# PRE-REGISTRATION — grading the forward record: mechanical resolution of every taken signal

**Written 2026-10-08 at the owner's request ("pls go ahead with preregistration"),
and committed BEFORE any resolver code exists and before any outcome on the
`stop_distance` basis is written.** Everything below is fixed. It may be
AMENDED, in a dated section at the top, only until the first such outcome row
is written; after that nothing here changes. Companion to
`results/forward_record_preregistration.md`, which fixed what is RECORDED;
this fixes how it is GRADED.

## Why grading is a separate act

Outcome rows today come only from `attach_outcomes()`: a journal trade the
owner actually took, attached to its signal, R on the option PREMIUM. Base
signals are recorded but not alerted, so they are never traded, so that path
never grades them. Without grading, the record measures arrival, not edge
(BACKLOG 28). The grader is a second, independent basis — `stop_distance`,
already defined in `record_outcome()` — and the two are never summed.

## The mechanism being graded

The signal says: from this bar's close, price reaches `target` before `stop`.
Grading asks exactly that of the bars that followed, under the rules a trader
following the alert would have faced. Nothing about the signal is re-derived;
the row's `setup` is the contract.

## The rules, fixed

Every rule below matches `backtest.simulate_trade()` — the engine behind the
1,386-trade record — EXCEPT where marked **DIFFERS**, and each difference is
the realistic side of a known defect, chosen so the forward record does not
inherit it.

| rule | value | matches the record? |
|---|---|---|
| bars | daily, `auto_adjust=False`, the scanner's own `get_data()` path (Yahoo, Tiingo fallback) | yes (raw, per `check_adjustment_matches_live`) |
| entry | the OPEN of the first bar AFTER `bar_date`, plus 2 bps slippage in the trade's direction | yes |
| stop, target | the row's `setup.stop` and `setup.target`, as absolute prices | yes |
| risk (R denominator) | `abs(fill − stop)` | yes |
| gapped past its own levels at the fill | fill beyond the stop or beyond the target → **not filled**, outcome `VOID`, reason `gapped_before_fill`. The setup no longer exists at that price. | yes |
| stop hit (Low ≤ stop for a long) | exit at **the worse of the stop and that bar's Open** | **DIFFERS** — the record fills AT the stop (BACKLOG 21); this is the real fill |
| target hit (High ≥ target for a long) | exit at **the better of the target and that bar's Open** | **DIFFERS** — same defect, mirror side |
| stop and target both inside one bar | the stop, conservative | yes |
| timeout | 20 bars after entry, not hit → exit at that bar's Close | yes (`max_hold = 20`) |
| exit slippage | 2 bps against the trade on every exit | yes |
| commission | 0 | yes |
| exit policy | none: no breakeven move, no trail, no partial | yes (the record's baseline, `policy=None`) |
| shorts | not graded; `skipped` rows have no outcome, as `record_outcome()` already enforces | n/a |

**Outcome label.** `WIN` if the target was hit; `LOSS` if the stop was hit;
on timeout, `WIN` / `LOSS` by the sign of R, `BREAKEVEN` if |R| ≤ 0.05 — the
same dust floor as `journal_store.DUST_R`, so the two bases use one
definition of "neither".

**`VOID` is an outcome row too**, with a `reason`, so a signal that cannot be
graded is visibly closed rather than left looking open forever. Reasons, fixed:
`gapped_before_fill` (above); `split_in_window` (a split or reverse split
between the signal bar and the exit bar — the row's prices are in the
pre-split basis and nothing here rescales them); `no_bars` (the data source
returns nothing past `bar_date` after the hold window has elapsed in calendar
time). A VOID carries `r: null` and is excluded from every mean, and its count
is reported beside the settled count every time a mean is.

**Unsettled signals stay unsettled.** A signal whose 20-bar window has not
elapsed gets no row. The resolver never writes a partial.

## What is recorded on each outcome row

`ref_seq`, `outcome`, `r`, `basis: stop_distance`, `exit_price`, and a
`detail` object: `fill` (the entry price actually used), `fill_date`,
`exit_date`, `bars_held`, `exit_rule` (`stop` / `target` / `timeout` /
`void:<reason>`), `gap_fill` (true when the stop or target filled at the open
rather than the level), `cost_bps: 2`, and `r_at_level` — the R the record's
convention would have booked (fill AT the level). `r_at_level` exists so the
size of the BACKLOG 21 defect is measured on live data as a by-product, and
so this record can be compared to the uncorrected 1,386-trade record on its
own terms. **`r`, never `r_at_level`, is the graded number.**

## The 19 rows already on file

Signals dated 2026-09-16 to 2026-10-07 are graded under these same rules by
the first run of the resolver. Their price paths exist today and have not been
computed, inspected or estimated by anyone before this document; the rules
above were fixed without looking. The first resolver run is reported as
`results/forward_grading_run1.txt` with every row's `exit_rule` listed.

## Where it runs, and the one guard that must change

- **In `scanner.py`, the forward log's one writer**, immediately after
  `attach_outcomes()` on every `run()` — including `--record-only` — before the
  scan loop. Non-fatal, like every other log write: a grading failure never
  costs an alert.
- One `get_data()` per ticker with an open signal; tickers already in the
  watchlist reuse the scan's frame.
- `record_outcome()`'s guard "one outcome per `ref_seq`" becomes **one outcome
  per (`ref_seq`, `basis`)**. A journal (premium) outcome and a mechanical
  (stop-distance) outcome may both exist for one signal; `summary()` and
  `report()` split every count and every mean by basis and never pool them.
  `attach_outcomes()`'s `already` check keys on the premium basis only.
- Idempotent by construction: a signal with a `stop_distance` row is never
  graded again, and the resolver is pure (bars in, verdict out) so a re-run
  on the same bars returns the same row.

## What this measures, and the bar — inherited, not new

The hypotheses, the episode definition (first bar of a consecutive run per
ticker and side; later bars in the run are graded but not counted), the
power requirement (**349 settled episodes** for the +0.15 R bar at sd 1.0 R)
and the stopping rule are those of `forward_record_preregistration.md`. This
document adds none. Until 349 episodes have settled, `report()` prints the
settled count, the VOID count, the mean R, and its existing line that this is
NOT a result.

One descriptive number is added from day one because it costs nothing and
answers BACKLOG 21 on live data: **mean(`r` − `r_at_level`)** over stop
exits, with the share of stop exits that gapped. It is reported, not tested.

## What would make a result from this record untrustworthy, said now

- A change to any rule above after the first outcome row. The chain is
  append-only; a rule change is a NEW basis name, never a reinterpretation.
- Pooling `premium` and `stop_distance` rows anywhere.
- Reading the mean R before 349 settled episodes, or reading it on rows
  rather than episodes.
- Grading rows whose bars came from a different adjustment mode than the
  signal was computed on.

## Enforcement, to be built with the code

- `forward_log.resolve()` (or equivalent) is a pure function of `(setup,
  trend, bars after the signal bar)`; its selftest covers every row of the
  rules table: clean stop, clean target, gapped stop (fills at the open,
  `gap_fill` true, `r < −1`), gapped target, both-in-one-bar → stop, timeout
  win / loss / breakeven at the dust floor, gapped-before-fill → VOID,
  split-in-window → VOID, unsettled → no row.
- `consistency_check`: outcomes split by basis everywhere a mean is printed;
  the (`ref_seq`, `basis`) guard holds (a second `stop_distance` row for one
  signal is refused; a `premium` row beside it is allowed); the resolver
  reads bars `auto_adjust=False`.
- Every one of those falsified before merge, per the repo rule.
