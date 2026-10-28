# PRE-REGISTRATION — the RS basket as a holding strategy (BACKLOG 34 B)

**Decided by the owner 2026-10-28 ("go with A and B as well"). Written and
committed BEFORE the first basket row is recorded.** Everything below is
fixed. Nothing here may be changed after a result is seen; the timestamp on
this file in git is the point of it.

## The question

Every week `universe.py` picks eight names from the candidate pool by
relative strength against SPY over `RS_LOOKBACK` sessions, capped per
sector, and the scanner alerts on those eight. The logic review of
2026-10-13 (decision 3) asked whether that selection LOSES to the index.
Nothing in the repo measured it; the system makes every number needed.

**H0: the equal-weight basket of the week's eight names, held for the
week they were selected, returns the same as SPY.** Read as the weekly
difference, basket minus SPY, mean and standard error.

Secondary, descriptive only: how many weeks of N the basket is ahead; the
mean basket and SPY returns on their own.

## What is recorded, and when

- **Writer.** `scanner.py`, in the post-close pass, once per snapshot:
  `basket_log.record()` into `basket_log.jsonl`, hash-chained, append-only,
  stdlib to read, committed by `scanner.yml`. One writer, as the forward
  log and the IV snapshots have.
- **The row.** The snapshot date (`universe_history/<date>.json`, written
  Sunday 12:30 UTC), the eight names, each name's close on the ANCHOR
  session and SPY's close on the same session. The anchor is the last
  settled session on or before the snapshot date — normally the Friday the
  ranking used. Every name and SPY must share one anchor session and have a
  finite positive close, or no row is written and the scan log says why.
- **No signal, no geometry, no stops, no costs.** Equal weight, one
  rebalance a week, dividends ignored on both sides (raw closes). This is
  the selection's gross return against the index's gross return.

## How a row is graded

- **Horizon.** `basket_log.HORIZON_SESSIONS` = 5 exchange sessions after
  the anchor, the week the snapshot was selected for. The end session is
  found with the scanner's exchange calendar (`trading_days_between`): the
  session EXACTLY five after the anchor. A feed whose dates jump past it is
  HELD (reason `gap`), never graded on a shifted end; a missing anchor is
  HELD (`anchor_missing`); a name with no data is HELD (`no_data`). A held
  row is retried on every post-close pass and VOIDED with its reason once
  the anchor is `NO_BARS_DAYS` = 45 days old. An interior missing session
  is not a gap: a two-point return needs only its ends.
- **Split.** A name whose fetched anchor close differs from the recorded
  one by more than `SPLIT_TOL` = 2 % is a split in the window: the row is
  VOID (`split`).
- **The number.** Each name's simple return anchor → end; the basket is
  their mean; SPY's the same; the row's `delta` is basket minus SPY. One
  outcome per basket row, ever.
- **Names that leave the watchlist** are fetched for grading the way IV
  grading fetches departed names, under the same per-pass cap; a deferred
  name holds the row to the next pass.

## The reading, and when it is read

- **Unit.** One week. Weeks do not overlap (anchor Friday, end Friday), so
  the deltas are treated as independent and the SE is the plain one.
- **First reading at 26 graded weeks**, reported with its SE whatever it
  says. **Not a result before 52 graded weeks.** With a weekly
  basket-minus-SPY sd of roughly 2.5 % (eight large caps against the
  index), the MDE at 52 weeks is about ±1.0 % per week, i.e. only a very
  large selection effect is detectable in a year; anything smaller reads
  "not established". That is the honest cost, stated now.
- **Prediction, stated now** (as the 2026-10-19 review stated it): the
  basket reads within ±2 % of SPY over a quarter with no significance.

## What this record cannot do

It cannot say whether the eight names were TRADEABLE (that is the forward
record's job); it cannot price the weekly turnover (3–4 of 8 names rotate
most weeks); it cannot see dividends. It answers one question: does the
ranking pick names that move with the index, ahead of it, or behind it,
over the week it picks them for.

## Enforcement

`basket_log.py --selftest` (one row per snapshot; refusals; the exact
end session; held, void and split; the chain), `scanner.py --selftest`
(the row is written once per snapshot on one shared anchor, with SPY, or
not at all), `consistency_check.check_basket_log_single_writer_and_committed`
(one writer, stdlib, committed, post-close only, in CI, no fixtures).
