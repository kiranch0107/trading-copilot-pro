# PRE-REGISTRATION — the forward record: base signals as `taken`, the HQ tier as a tag

**Decided by the owner 2026-10-08 ("For 24, go with recording the base signal
and tagging HQ"). Written and committed BEFORE the first row under the new rule
is recorded.** Everything below is fixed. Nothing here may be changed after a
result is seen; the timestamp on this file in git is the point of it.

## Amendment 2026-10-13 — the pool bucket (BACKLOG 30). ADDITIVE: nothing below about the live watchlist changes

**Owner's decision 2026-10-13 ("lets run HQ scan once a day during market
hours"), written before the first pool row is recorded.** The scanner
also evaluates the whole candidate pool (`universe.CANDIDATE_POOL`, about
80 names, every reserved tranche already excluded) beyond the eight-name
watchlist, once a session, as a SECOND POPULATION of the same record.

- **Bucket.** Every signal row and every `scan` row carries `bucket`:
  `live` for the watchlist, `pool` for the rest. A row without the field
  (every row before this date) is `live`. The two buckets are read
  separately; `forward_log.summary()` reports `by_bucket`.
- **When.** The pool scan runs on the first alerting scan of each ET date
  (`scanner.pool_due`, state key `POOL:DATE`), so the 11:07 dispatch
  carries it and a backup slot catches a missed one. It evaluates the last
  settled bar exactly as the watchlist loop does. It never runs in the
  record-only pass. `--force-pool` (workflow input `pool`) repeats it.
- **What is recorded.** Every pool signal the rules produce, taken or
  skipped, HQ-tagged exactly as the live rows, under `bucket: pool`. Pool
  `scan` rows are NOT the coverage heartbeat: `last_scan()` reads the live
  bucket.
- **What is sent.** Alerts on the HQ tier only, as for the watchlist, and
  **at most five a session**, ranked by **relative strength**: the
  63-session return spread against SPY (`universe.RS_LOOKBACK`), the number
  the universe already ranks on. Pool HQ signals beyond the cap are
  recorded, not alerted. The four-hour cooldown applies. Each alert carries
  the suggested contract exactly as a watchlist alert does; that is the
  "check the option chain for at most five" of the owner's request.
- **Grading.** The same resolver, the same bases, the same dust floor.
  Pool rows are graded on the pool-scan run (frames already fetched) and in
  the post-close pass; on the other in-session runs they wait.
- **Reading.** H0 and H1 as below, asked of EACH bucket on its own. A
  pooled line across buckets is descriptive only. The 349-episode stopping
  rule applies per bucket.
- **Expected arrival, stated now.** The live record holds 27 signals and 0
  HQ (ADX ≥ 35 failed on every tagged row). By the rule of three the HQ
  share of base signals is below ~11 % at 95 %; at ~6 base signals a day
  across the pool that is fewer than ~0.65 HQ a day. The cap of five will
  rarely bind.
- **Prediction.** HQ fires rarely on the pool; where it fires, its mean R
  does not exceed base by +0.15 R; the pool's base mean is not
  distinguishable from the live bucket's.

Enforcement: `scanner.py --selftest` (the pool harness) and
`consistency_check.check_pool_bucket_wired()`.

## The finding this answers (BACKLOG 24, review H2)

Between 2026-09-16 and 2026-10-07 the scanner recorded 19 base signals on 8
names and marked every one `skipped: not high-quality`. The high-quality (HQ)
tier requires ADX ≥ 35, "Strong" strength (RSI > 60 and volume ≥ 1.2× average),
all four filters passing, and R:R ≥ 1.0. Two of those legs — ADX and RVOL —
were tested in this repo and found non-predictive (`adx_retest_run1.md`,
`rvol_confirm_run1.md`). The forward log is the only remaining route to
evidence, and its `taken` set was gated on refuted features. At zero taken per
three weeks it would never reach power.

## What changes

1. **A long base signal is recorded as `taken`.** "Taken" has always meant
   "the rules produced a tradeable signal", not "an alert went out"
   (scanner.py's `_log` says so). From this change the rules are the BASE
   rules: EMA stack, MACD, RSI band, soft volume floor, R:R ≥ 0.5, long side.
2. **The HQ tier is a tag, not a gate, on the record.** Every signal row
   carries `hq: true|false` at top level and `setup.hq_fail`, the list of HQ
   legs that failed (`rr<1`, `not Strong`, and any failing filter by name).
   The record therefore says both what the rules produced and what the gate
   would have selected.
3. `forward_log.summary()` reports `taken_hq` and `taken_base` separately.

## What does NOT change

- **Alerts.** Telegram still fires on the HQ tier only. This decision is about
  what is recorded, not what is sent. Changing the alert gate is a separate
  decision and would need its own entry here.
- **The direction gate.** Shorts are still `skipped: direction gate`, because
  that gate rests on a measurement (`drift_null`: every one of 200 random draws
  beat the real short signal), not on a refuted feature.
- **The config fingerprint.** No tunable moves, so `config` is unchanged and
  rows before and after this change pool under one config. They are
  distinguishable by the presence of the `hq` field and by bar date
  (≥ 2026-10-08).
- **The chain.** Append-only. The 19 existing rows are not rewritten.

## How the existing rows are read — fixed now, not later

The 19 rows dated 2026-09-16 to 2026-10-07 with `decision: skipped`,
`reason: not high-quality`, `trend: Bullish` are long base signals that failed
the HQ tier. They carry `setup.high_quality: false`. **They count as
`taken, hq=false` for every analysis under this document.** Deciding that now
removes the option of deciding it after seeing their outcomes.

## The question, and the analysis plan — fixed

**H0:** the HQ tier adds no expectancy. Mean R per signal for `hq=true` minus
mean R for `hq=false` is ≤ 0. Consistent with `adx_retest` and `rvol_confirm`.

**H1:** the HQ tier adds ≥ +0.15 R per signal over base. That is the smallest
difference worth the alerts it suppresses; anything smaller is not worth
gating on.

Secondary, descriptive only: mean R of all base signals against zero. That
is the directional edge question, already answered negative on 1,386 trades
and 591 out-of-sample. The forward record reports it; it does not re-litigate
it until it has the sample below.

**The independent unit is an episode, not a row.** A base setup persists
across consecutive bars (TMO logged 8 in a row). Rows on consecutive bars for
the same (ticker, trend) are one episode, dated at its first bar; only the
first bar's row is graded. This is fixed here because the alternative — grading
every bar — multiplies correlated observations and flatters whichever side has
the longer runs.

**Power.** Per-trade R has sd ≈ 1.0 on the 1,386-trade record (its detectable
effect of 0.075 R at n = 1,386 implies that). From `power_check.py`:

| effect sought | independent observations needed |
|---|---|
| +0.15 R (H1 bar) | **349** |
| +0.10 R | 785 |

Arrival rate observed: 19 base rows over 15 bars on 8 names, ≈ 1.3 rows per
bar, but TMO's run shows episodes arrive at roughly a third of that. Call it
**~0.5 episodes per bar ≈ 125 per year on 8 names**. H1 is detectable after
**about three years at the current universe size, or ~6 months at ~50 names.**
That is the honest cost of this test, and it is why BACKLOG's arithmetic
already said ~50 tickers.

**Stopping rule.** No mean R from this record is reported as a result until
**349 episodes have settled outcomes**. Until then `forward_log.report()`
prints descriptive counts only, with its existing "NOT a result" line.

## What this record cannot do yet, stated so it is not smuggled in later

**There is no outcome measurement.** Outcome rows today come only from
`attach_outcomes()`, which matches journal trades the owner actually took.
Base signals that are not alerted will not be traded and so will never get an
outcome that way. The record is intake; the grading is a separate step —
mechanical resolution of each taken signal against later bars (stop, target,
or time-out) on the `stop_distance` basis `record_outcome()` already defines —
and it **requires its own pre-registration** (hold length, fill rule for a
gapped stop per BACKLOG 21, which bar's open is the entry) before a single
outcome is written. BACKLOG 28 carries it. Nothing in this document may be
graded until that document exists.

**Ambiguity will rise.** `attach_outcomes()` refuses a journal trade that
matches more than one taken signal within five days, and with base signals
recorded on consecutive bars that will be the common case. It stays refused —
a wrong attachment is permanent. The fix is for the journal row to carry the
signal's key (ticker, trend, bar date) when the position is opened from an
alert, so the match is exact. Also in BACKLOG 28.

## Enforcement

- `consistency_check.check_hq_tier_annotates_record()` drives
  `scanner.analyze()` with a long signal that fails the HQ tier and asserts the
  row is `taken` with `hq=false` and a populated `hq_fail`, AND that no alert
  payload is returned; then with an HQ signal, asserts `hq=true` and an alert
  payload. A bearish signal must still be `skipped`.
- `scanner.py --selftest` pins the same three paths from inside the module.
- Both falsified before merge.
