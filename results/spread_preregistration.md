# PRE-REGISTRATION — the always-on defined-risk spread arms (BACKLOG 34 E)

**Decided by the owner 2026-10-28 ("go with E as well, pre-register the
spread arm"). Written and committed BEFORE the first forward cycle is
recorded and BEFORE run 4 of the backtest.** Everything below is fixed.
Nothing here may be changed after a result is seen; the timestamp on this
file in git is the point of it.

## What this governs

Two things, in order:

1. **Run 4 of `spread_backtest.py --skew-sweep --years 10`**, the
   reproduction of run 3 with the bar-cache fingerprint printed. It is a
   REPRODUCTION, not a new look: same eight arms, same four skews, same
   bar (below). Its job is to say which bars run 3 read and whether the
   five PASS verdicts reproduce on them.
2. **The forward arm**: `spread_log.jsonl`, written by the scanner's
   post-close pass from the day this file merges, one cycle per arm, for at
   least `RESULT_CYCLES` = 12 graded cycles per arm (about a year).

## The mechanism (BACKLOG 13, step 1)

Who is on the other side and why do they lose: index put buyers who pay
for protection, measured as implied vol running +3.63 vol points above
subsequent realised on SPY over ten years, positive in 83.7 % of days
(`results/vrp_measurement.md`). A defined-risk seller collects that
premium on the condition that the path does not reach the short strike
before expiry, and caps what the path can take when it does. The backtest
is the measurement of whether the seller SURVIVES the path; the premium
measurement could not reach that.

## The bar (unchanged from `spread_backtest.py`, 2026-09-10, clause 5 added 2026-09-10)

An arm PASSES only if ALL of the following hold at EVERY skew tested
(0, 10, `REALISTIC_SKEW` = 20, 30 vol points per 1.0 log-moneyness):

1. the 95 % CI of the mean cycle return clears zero (cycles do not
   overlap, so the plain interval);
2. max peak-to-trough drawdown ≤ `MAX_DRAWDOWN_PCT` = 25 % of the account;
3. (clause 3 is the "every skew" rule above);
4. the single worst cycle loses ≤ `risk_params.MAX_POSITION_PCT` = 25 % of
   the account;
5. the mean beats holding the same dollars (the max loss) in the
   underlying over the same cycles.

Costs: `--cost-frac` 0.5 of `MAX_OPTION_SPREAD_PCT` (8 %), crossed at
`CROSS_FRAC` 0.5 of the quoted width, on each leg's own price, at entry
only (held to expiry). Account `DEFAULT_ACCOUNT_SIZE` = $5,000, one
contract per cycle. `consistency_check.check_spread_arm_preregistered`
pins every constant above to the code.

**What a PASS commits anyone to: running the forward arm for 12 graded
cycles per arm. Nothing else.** No arm is traded on a backtest verdict.
What a FAIL on run 4 would mean: the run-3 verdict did not reproduce;
the forward arm still runs, and the backtest reading is the run-4 one.

## Power, stated before the forward record exists (BACKLOG 13, step 2)

From run 3's intervals, a cycle's return has a standard deviation of
about 5.3 % of the account (SPY iron_condor w10: half-width 0.95 on 120
cycles). To detect a true +1.0 %/cycle at 80 % power needs about 220
cycles, eighteen years of one cycle a month. **The forward MEAN RETURN
is therefore not a readable quantity on this record, and will not be
read as one.** It is recorded and reported as a PATH: worst cycle and
drawdown, per arm, against clauses 2 and 4.

What the forward record CAN read inside a year is the model itself. Every
backtest number rests on a Black-Scholes credit from the ^VIX / ^VXN close
and a linear skew. The market quotes the same legs every day. The primary
forward question is therefore:

**H0: the market's mid credit equals the model's credit at skew 20; the
per-cycle ratio (market mid / model) has mean 1.0.**

With a per-cycle ratio sd of 0.15 (assumed; the record will measure it),
the MDE is ±0.17 at 6 cycles and ±0.12 at 12, per arm; pooled across
the four arms of one underlying, ±0.06 at 48. A model that overstates
the credit by 15 % or more is detectable within the year.

## What is recorded, and when

- **Writer.** `scanner.py`, `spread_record_pass()`, in the post-close
  pass, after the cycles whose expiry has closed are graded (so the next
  cycle opens on the settlement session's close, the backtest's own
  re-entry rule, `i = j`). One writer, hash-chained, append-only, stdlib to
  read, committed by `scanner.yml`.
- **The arms.** The backtest's eight, fixed in `spread_log.ARMS`: SPY and
  QQQ × put_spread and iron_condor × width 10 and 5, short strike 5 % OTM.
  All eight are recorded; the five that cleared run 3 are the hypothesis
  arms, the three that failed are recorded beside them so a model bias
  shows on both.
- **The row** (`cycle`): the entry session (the ticker's last settled
  close), the spot, the vol index (^VIX for SPY, ^VXN for QQQ) which MUST
  have closed on the same session or no row is written, the first LISTED
  expiry at least `DTE_DAYS` = 30 calendar days out and its day count, the
  legs `spread_backtest.build()` chose (whole-dollar strikes), the model
  credit at `REALISTIC_SKEW` and flat, the gross, the max loss, the
  backtest's cost, and the market's quotes on exactly those legs: bid and
  ask per leg, the mid credit, and the executable credit (short legs at
  the bid, long legs at the ask). A leg without a usable quote (missing,
  zero bid, ask below bid) leaves `market` null with the reason; the row is
  still written and the model side still grades.
- **Quotes are the post-close snapshot** Yahoo serves after the session:
  closing quotes, not a live market. Stated now as the record's known
  limitation; a wide or stale closing quote reads as a low ratio, which is
  the direction that cannot flatter the model.
- **One open cycle per arm, ever.** An arm with an open cycle is not
  opened again, whatever the pass count.

## How a row is graded

- **Settlement** is the ticker's close ON the expiry date (SPY and QQQ
  options settle on that close). `owed` = intrinsic of the legs at that
  close (`spread_log.settle`, the one copy the backtest also uses).
- **P&L, model** = (model credit − owed − cost) × 100, the backtest's
  arithmetic exactly. **P&L, market** = (executable credit − owed) × 100.
  Both in dollars and as % of the account. **Ratio** = market mid credit /
  model credit, or null without a quote.
- A missing expiry session is HELD (`gap`), as is a ticker with no data
  (`no_data`); both are VOID once the expiry is `NO_BARS_DAYS` = 45 days
  old. A spot that no longer matches the recorded entry close beyond
  `SPLIT_TOL` = 2 % is a split: VOID. A cycle before its expiry is
  `unsettled`. One outcome per cycle, ever.

## The reading, and when it is read

- **Unit.** One cycle per arm. Cycles of one arm do not overlap, so the
  plain SE. Arms of one underlying share the path and are NOT
  independent; the pooled ratio is reported with that said.
- **First reading at 6 graded cycles per arm**, with its SE, whatever it
  says. **Not a result before 12 graded cycles per arm.**
- **Predictions, stated now.** Put spreads: ratio within 0.85–1.15 (the
  model's skew is at the pessimistic end for puts, and VIX is a 30-day
  ATM reference, so the model should price the put wing about right).
  Iron condors: ratio BELOW the put spreads', 0.80–1.00, because the call
  wing's skew runs the other way (OTM calls trade cheaper than a linear
  put skew prices them) and the model overstates that wing. Path: no
  cycle worse than −25 % of the account on the model side in the year.
- **What would void the backtest reading for an arm:** a 12-cycle ratio
  whose 95 % CI lies entirely below 0.85. The backtest's mean credit is
  then overstated by at least that fraction, and the arm's run-3/run-4
  verdict is VOID until the engine prices what the market prices. A CI
  entirely above 1.15 is reported, not acted on: a model that
  understates the credit does not rescue a FAIL.
- **What this record cannot do.** It cannot say the strategy is worth
  trading (the mean is unreadable here); it cannot see early assignment,
  margin, or a real fill against a live book; it has one year of one
  regime. It answers one question: does the model the verdict rests on
  price what the market quotes.

## Enforcement

`spread_log.py --selftest` (one open cycle per arm; refusals; market
credit arithmetic; settlement on the expiry close; held, void, split; the
chain), `scanner.py --selftest` (eight cycles from two chains on one
close; a stale vol index, an unquoted leg and a missing chain handled as
declared), `consistency_check.check_spread_arm_preregistered` (one
writer, post-close only, stdlib, committed, in CI, no fixtures; `settle`,
`CROSS_FRAC`, the arms and the vol indices single-sourced in `spread_log`
and imported by the backtest; every constant in this file equals the
code's).
