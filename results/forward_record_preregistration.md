# PRE-REGISTRATION — the forward record: base signals as `taken`, the HQ tier as a tag

**Decided by the owner 2026-10-08 ("For 24, go with recording the base signal
and tagging HQ"). Written and committed BEFORE the first row under the new rule
is recorded.** Everything below is fixed. Nothing here may be changed after a
result is seen; the timestamp on this file in git is the point of it.

## Amendment 2026-10-28 (G) — the HQ tier no longer requires the "Strong" tag (BACKLOG 24, owner's decision). THRESHOLD CHANGE on the tag and the alert tier, declared as an INTAKE choice; the fingerprint moves once and is declared

**Owner's decision 2026-10-28 ("go with the Strong leg as well, make it its
own amendment"), written before the first row under the new hash.** The HQ
tier required "Strong": RSI > 60 (for a long) AND volume ≥ 1.2× its 20-bar
average. From the next scan the tier is **ADX ≥ 30 (unrounded), every
filter passing, R:R ≥ 1.0**, and nothing else. `signal_core.SignalParams`
gains `hq_requires_strong`, False; the old rule is still expressible for a
research run that wants it.

**What it rests on, stated so it cannot be mistaken for a finding.** The
volume half of "Strong" is the RVOL ≥ 1.2× feature, REFUTED out of sample
on 4,544 trades (`rvol_confirm_run1.md`, adequately powered). The RSI half
has no measurement for it (`feature_sweep_run1` records only its sign).
On the record so far (amendment F) the leg is what kept the tier empty:
of 277 tagged rows, 229 failed "not Strong", and every one of the 22 rows
at ADX ≥ 35 that failed the tier failed this leg or another. A gate on a
refuted feature starves the record (the finding of BACKLOG 24 itself);
removing it is an intake choice and no claim that the tier has an edge.

**What it does to the record, measured on the rows so far.** Under F and G
together, **30 of the 277 tagged rows would have been HQ** (18 pool, 12
live; 27 of them "Normal", 3 "Strong") against 3 under F alone and 0 under
the original tier. On the 12 pool scan bars so far that is about 1.5 HQ
rows a bar, 11 % of taken rows, so the alert tier fires for the first
time at a rate the pool's cap of five a day can see. The RSI of the
Normal rows admitted runs 56 to 75: the tier now admits a bullish base
signal on ordinary volume, which is what the base signal is. Stated now
so a later reader does not credit the tier with rows the tag selected.

**What changes.**

- `signal_core.evaluate()`'s `high_quality` no longer requires
  `strength == "Strong"` (unless `hq_requires_strong`). The `strength` tag
  is still computed, recorded on every row (`setup.strength`) and still
  steers the option selector's strike window; only the gate dropped it.
- `setup.hq_fail` no longer lists `not Strong`. Rows before this
  amendment carry it; `forward_log.rank_score()` leaves that leg out of
  its count for every row, so the rank tier (amendment D) ranks on the
  same legs before and after.
- The alert tier fires on the new HQ. The pool's cap of five a day and
  the live bucket's rules are unchanged.
- The fingerprint moves once, from `81fd95d84cfc` to **`4ddd12a3095f`**
  (`hq_requires_strong` is named in `_scan_cfg()`). `forward_log.KNOWN_CONFIGS`
  declares it with this note.

**How the rows are read across the change.** The base-signal question (H0,
the pool, 349 settled episodes) is unaffected: the base rules did not
move. The HQ questions (H1, H1′) are read from amendment F forward, as F
says; this amendment changes which rows the tag selects from that date,
and an HQ row's hash says which tier tagged it. A reader who wants the
tier WITH the Strong leg reconstructs it from `setup.strength` on any row.
**Prediction, stated now**: HQ's mean R does not exceed base by +0.15 R
(H1), as the pool amendment predicted; the tier now admits enough rows
for H1 to be read at all, which it could not under the old legs.

**What does NOT change.** `volume_mult` (1.2, the tag's own threshold),
the base condition's 0.7× volume floor, every other threshold, the
direction gate, the pullback bucket, the control arm, the research
engines' committed results.

**Enforcement.** `signal_core` selftest (a Normal signal with every other
leg passing is HQ; the pre-G rule still gates when asked for; the tag is
still recorded); `scanner.py --selftest` (no `not Strong` leg in
`hq_fail`; the tag recorded beside it; the fingerprint in force pinned to
`4ddd12a3095f`); `forward_log` selftest (the fingerprint reproduces; the
rank ignores the retired leg); `consistency_check.check_hq_tier_legs_declared`
(the flag is False in `DEFAULTS`, `evaluate()` and the scanner's leg list
read it, the scan config names it, this amendment and `KNOWN_CONFIGS`
carry the hash).

## Amendment 2026-10-28 (F) — the HQ tier's ADX leg is 30, not 35 (BACKLOG 33, owner's decision). THRESHOLD CHANGE on the tag and the alert tier, declared as an INTAKE choice; the fingerprint moves once and is declared

**Owner's decision 2026-10-28 ("go with BACKLOG 33 as well, ADX 30"),
written before the first row under the new hash.** `signal_core.DEFAULTS.adx_min`
is 30.0 from the next scan. The ADX-30 set is the ADX-35 set plus exactly
the signals whose signal-bar ADX lies in [30, 35), so this admits that band
to the HQ tag and to the alert tier and changes nothing else.

**What it rests on, stated so it cannot be mistaken for a finding.**
`results/adx_live_preregistration.md` § Result (run 1, 2026-10-14, the
live config, longs only, 12 names, 10 years): the band [30, 35) holds 199
longs at +0.104 R, 95 % CI [−0.159, +0.367], p 0.44, under a 0.376 R
detectable effect: NOT ESTABLISHED on both pre-registered bars, and the
ladder shows no dose-response. The trades this change admits carry nothing
the data can see, in either direction. **This is an intake choice about
alert volume from a tier with no measured edge, not a measured
improvement**, exactly as that pre-registration said such a change would
be; it pre-committed to nothing and this amendment claims nothing.

**What it does to the record, measured on the rows so far.** Of the 298
taken rows, 0 are HQ. 22 carry ADX ≥ 35 and every one failed another leg
("not Strong" on 20 of them). 39 carry ADX in [30, 35) (28 pool, 11
live), and only **3** of those fail on ADX alone (PG and WMT on
2026-10-22, V on 2026-10-27, all pool): under this amendment the record
would hold 3 HQ rows instead of 0. The binding leg of the tier is "Strong"
(RSI > 60 and volume ≥ 1.2×), not ADX. The backtest's "about double the
HQ intake" (544 longs at ADX 30 against 329 at 35) is the ungated
population; on the recorded rows the tier stays nearly empty. Stated now
so a later reader does not credit this change with HQ rows it did not
produce, or blame it for an H1′ reading that stays starved.

**What changes.**

- The HQ tag (`hq`, `setup.hq_fail`: the "ADX Trend Strength" leg) and the
  alert tier read ADX ≥ 30, unrounded (amendment E). The base signal, the
  buckets, the resolver, the control arm, the rank tier (it ranks on
  recorded fields; a lower ADX leg changes which rows have zero `hq_fail`
  legs and nothing about the ranking rule) and the stopping rule are
  unchanged. `backtest.RESEARCH_VS_LIVE` declares the new live value; the
  research config (ADX 25) and the OOS frozen config (ADX 35) are not
  touched.
- The fingerprint moves once, from `ba6b3b46afa6` to **`81fd95d84cfc`**,
  because `adx_min` is in the hash. `forward_log.KNOWN_CONFIGS` declares
  it with this note.

**How the rows are read across the change.** The base-signal question
(H0, the pool, 349 settled episodes) is unaffected: the base rules did
not move, and rows pool across the two hashes as one dataset. The HQ
questions (H1, H1′) are read **from this amendment forward**: the tier
fired 0 times under 35, so there is no HQ arm before this date to
contaminate or to pool with, and an HQ row's hash says which gate tagged
it. A later reader who wants the 35-gate HQ set can reconstruct it from
`setup.adx_raw` on any row; nothing is lost.

**What does NOT change.** Every threshold other than `adx_min`; the
direction gate; the pullback bucket (its own rule, no ADX leg); the
research engines' committed results (built on ADX 25 or the frozen 35;
none is re-run for this, and `RESEARCH_VS_LIVE` says why they differ).

**Enforcement.** `signal_core` selftest (DEFAULTS.adx_min is 30; 29.96
fails and 30.0 passes under DEFAULTS; the unrounded-gate test pins 35
explicitly); `scanner.py --selftest` pins the fingerprint in force to
`81fd95d84cfc`; `forward_log` selftest reproduces it from the exact dict;
`consistency_check.check_research_config_divergence_declared` holds the
live value to the declaration.

## Amendment 2026-10-28 (E) — the ADX and R:R gates compare UNROUNDED values (BACKLOG 36, owner's decision). RULE CHANGE at rounding distance of a threshold; the fingerprint moves once and is declared

**Owner's decision 2026-10-28 ("go with the unrounded-gate declaration as
well"), written before the first row under the new hash.** The external
review of 2026-10-20 found that `signal_core.evaluate()` rounded ADX to one
decimal and R:R to two BEFORE comparing them with their thresholds, so an
ADX of 34.96 passed the 35 gate and an R:R of 0.996 passed the 1.0 leg.
Comparing rounded values is a rule in its own right; changing it moves what
the record records, so it is declared here like any threshold change.

**What changes, from the next scan row.**

- `signal_core.gate_adx(adx_raw, adx_min)` and `gate_rr(rr_raw, rr_min)`
  compare the UNROUNDED values: the ADX Trend Strength filter (an HQ leg),
  the minimum R:R gate (`min_rr` 0.5, which blocks a signal) and the HQ R:R
  leg (`hq_min_rr` 1.0). The entry, stop and target stay cent-rounded, as
  prices are; the ratio between them is no longer rounded before the gate.
- The recorded fields `adx` (one decimal) and `rr` (two) are display; the
  row now also carries `rr_raw` and `adx_raw` where `evaluate()` is the
  writer. The scanner's `hq_fail` leg reads `rr_raw`.
- `scanner._scan_cfg()` names `gate_precision: "unrounded"`, so the
  fingerprint moves once, from `c357a0d01ff5` to **`ba6b3b46afa6`**.
  `forward_log.KNOWN_CONFIGS` declares it with this note, and `report()`
  reads the three hashes as one dataset.

**Why one dataset, with the magnitude stated.** The thresholds are the
same; membership can move only for a bar whose ADX is within 0.05 of 35
or whose R:R is within 0.005 of 0.5 or 1.0. In the 469 signal rows so far,
5 recorded a value exactly on a threshold (1 at ADX 35.0, 4 at R:R 1.00),
and about half of those would have read differently under this rule. That
is 1 % of rows, all in the HQ leg (none at the min-R:R gate), on a tier
that has fired 0 times. The pool's gate, the episode unit and every
reading are unaffected beyond that. A later reader who wants the two
halves apart can split on the fingerprint; the rows carry it.

**What does NOT change.** The thresholds, the buckets, the alerting rule,
the resolver, the control arm, the rank tier (it ranks on recorded fields),
the research engines (they share `evaluate()`, so a re-run would move by
the same 1 % at most; committed results are not re-run for this and say
nothing that this contradicts).

**Enforcement.** `signal_core` selftest (34.96 fails 35; 0.996 fails
1.0; display stays rounded); `consistency_check.check_gates_compare_unrounded`
(every gate reads the raw value through the two functions; the scan config
names the precision; the app's own ADX gate compares raw); the
fingerprint reproduces in `forward_log`'s selftest.

## Amendment 2026-10-28 (A) — a random-entry CONTROL row beside every taken signal (BACKLOG 34 A). ADDITIVE: a new row kind and a new reading; no signal, gate, threshold or alerting changes

**Owner's decision 2026-10-28 ("go with A and B as well"), written before
the next scan row.** "Mean R against zero" cannot separate the entry logic
from drift in the names the rules pick; `drift_null` could only ask that of
spent history. This records the null forward, beside every signal, so the
record can say whether the entry bar contributes anything.

**The control row, fixed here.**

- **When.** One `control` row for every TAKEN signal row, in every bucket,
  written by the scanner in the same call that writes the signal, from the
  frame the scan already holds. No extra fetch. A skipped signal has none.
- **Where.** The same ticker. The control bar is drawn from the
  `forward_log.CONTROL_LOOKBACK` = 20 settled sessions strictly BEFORE the
  signal bar: index = sha256(signal key) mod 20 into those 20 bars. Fixed
  by the row's own key, reproducible by anyone, chosen by nothing that
  knows an outcome. Fewer than 20 settled bars before the signal (a new
  listing): no control, and the scan log says so.
- **What.** The signal's own stop and target DISTANCES, placed around the
  control bar's close (mirrored for a short). Entry at the next open after
  the control bar, the same 2 bps, the same stop-first rule, the same
  20-bar hold, the same dust floor, the same void reasons: `resolve_signal`
  grades a control exactly as it grades a signal, on the stop-distance
  basis only. A journal trade can never attach to a control.
- **The row.** `kind: control`, `ref_seq` (the signal), `ticker`, `trend`,
  `bar_date` (the control bar), `bucket`, `setup` (price/entry/stop/target,
  the two distances), `config`, `draw` (lookback, index, seed). A control is
  never `taken`, never an episode, never alerted, never in the rank tier.

**The reading (new, descriptive until its gate).** By episode: the pair is
the episode's FIRST row and that row's control, both settled. Signal R
minus control R, mean and standard error over pairs, per bucket; the
headline is the pool's (amendment above). `forward_log.summary()
["by_control"]`, printed by `report()` under the pool's episode line.

**H2 (new): the signal bar's mean R exceeds its control's by ≥ +0.15 R
per episode.** The bar is the one the record already uses for a difference
worth gating on. Same gate as H0: 349 settled pool PAIRS. With a paired
difference of sd ≈ 1.4 (two R's of sd ≈ 1), the MDE at 349 pairs is about
±0.21 R, so +0.15 R itself needs ~680 pairs; at the gate a smaller
difference reads "not established", never refuted.

**Prediction, stated now** (as the 2026-10-19 review stated it): signal ≈
control on the pool within costs. If H2 fails, the entry bar contributes
nothing the name and the trend do not, and the system's measured value is
in the record-keeping and the risk discipline, not in the entry.

**What was known when this was declared.** Nothing: no control row exists
before this amendment, and no control is drawn from a bar whose outcome
was consulted. Controls drawn from bars up to 20 sessions back will settle
quickly (some are already settled in the frame at record time); that is a
property of the null, not a leak, because the draw is fixed by the key.

**What does NOT change.** Which rows are signals, taken or skipped; what is
alerted; the HQ tag; the pool, live and pullback populations; the rank
tier; the fingerprint. A control row carries the signal's config
fingerprint and is read under it.

**Enforcement.** `forward_log` selftest (seeded draw, mirrored geometry,
one per signal, graded from the signal's own fetch, pairs by episode) and
`consistency_check.check_control_arm_wired` (both recorders write one;
the grader offers them; never taken).

## Amendment 2026-10-28 (D) — H1 re-posed as a RANK tier that fires by construction (BACKLOG 34 D). READING CHANGE: a tag computed from recorded fields; no alerting, gate, threshold or recording changes

**Owner's decision 2026-10-28 ("go with D as well, re-pose H1"), written
before the next scan row.** H1 as written below compares the HQ tier with
base. The HQ tier has fired 0 times in 243 taken rows (0 of 219 on the
pool), so H1 cannot be read at any sample size: a tier that never fires
has no mean. This amendment re-poses it as the review of 2026-10-19
proposed (option ii): a tier defined by RANK, which fires by construction.
It is chosen for arrival rate, not for edge, and says so.

**The rank tier, fixed here.**

- **Score, from fields every pool row already carries:** fewest failed HQ
  legs first (`setup.hq_fail`: the legs of the HQ definition the bar
  failed — `rr<1`, `not Strong`, and every named filter that failed, ADX
  Trend Strength and Earnings Blackout among them; HQ itself is an empty
  list), then higher signal-bar ADX (`setup.adx`), then higher R:R
  (`setup.rr`), then ticker alphabetical. Deterministic; nothing is
  fetched.
- **Membership:** on each pool scan bar (`bar_date`), the top
  `forward_log.RANK_TIER_K = 5` TAKEN pool rows by that score are the rank
  tier for that bar. Five is `scanner.POOL_ALERT_CAP`, the "at most five a
  session" of the 2026-10-13 amendment, pinned equal by
  `consistency_check`: the tier is what the pool would alert each session
  if the alert gate were this rank instead of the HQ threshold.
- **An episode is in the tier if and only if its FIRST row is.** The
  episode unit, the bases, the resolver and the dust floor are unchanged.
- **It is a tag at read time**, computed by `forward_log.rank_tier()` from
  the recorded rows; no row is rewritten and no row gains a field. Alerts
  still fire on the HQ threshold only; nothing about what is sent changes.

**H1′ (replaces H1 for the pool):** the rank tier's mean R per episode
exceeds the rest of the pool's by **≥ +0.15 R**. The bar is the original
one: the smallest difference worth the alerts a gate suppresses. H0 is
unchanged.

**Power, stated honestly.** The tier is about a fifth of pool episodes
(60 of 219 rows, 9 of 42 episodes at declaration), so this is a
two-sample comparison with unequal groups, not the one-sample test the
349 figure was computed for. With per-episode sd ≈ 1.0, 5 % two-sided and
80 % power, a +0.15 R difference at a tier share of 0.21 needs about
**2,070 settled pool episodes** (≈ 560 bars ≈ 2.2 years at 3.7 a bar).
At the pool's 349-episode gate the minimum detectable difference is about
**+0.37 R**. So: at 349 settled pool episodes H1′ is read, and a
difference under the MDE is reported as NOT ESTABLISHED, never as
refuted; a difference at or above +0.37 R is a result; the +0.15 R bar
itself is reachable at ~2,070 episodes and the record keeps running.
`forward_log.report()` prints the tier and the rest beside each other,
with the counts, under the pool's episode line.

**What was known when this was declared, so it cannot be read as
selection.** At declaration the tier read 9 episodes, 5 settled, mean
−0.892 R; the rest 33 episodes, 21 settled, mean −0.199 R. The record so
far points AGAINST H1′, consistent with `adx_retest`, `rvol_confirm` and
the setup-population table, all of which found the tier's legs
uninformative; the amendment is made with that known and predicts, as the
2026-10-19 review did, that H1′ fails. Five settled episodes are not a
reading of anything.

**What does NOT change.** The HQ tag on every row (`hq`, `hq_fail`) stays
as recorded and keeps gating alerts; the live bucket is the alerting view
(amendment above); the pullback bucket is untouched; BACKLOG 33 (ADX 30
as an intake choice, option i of the review) remains a separate, open
decision and is NOT taken here.

**Enforcement.** `forward_log.rank_tier()` and its selftest (score order,
the cap, first-row membership, a live row never in the tier);
`consistency_check` pins `RANK_TIER_K == scanner.POOL_ALERT_CAP`.

## Amendment 2026-10-28 — the POOL is the population for the base-signal question (BACKLOG 34 C). READING CHANGE: which bucket the stopping rule reads; no rule, gate, threshold or recording changes

**Owner's decision 2026-10-28 ("go with option 1, pool only"), written
before the next scan row.** Nothing about what is recorded, how a row is
graded, or what is alerted changes. What changes is which bucket the
pre-registered QUESTION is asked of.

**Why.** The arrival rates measured on the committed scans
(`results/progress_review_2026-10-26.md`, F1): the live watchlist
produces about 0.61 episodes a bar, so its 349-episode gate is ~2.3 years
away, which this document already predicted ("about three years at the
current universe size"); the pool produces about 3.7 a bar, so its gate is
~4.5 months away. The live watchlist is also a rotating population: the
weekly snapshot swapped 3–4 of its 8 names each week in October, so "the
watchlist" names whichever eight the snapshot chose that week. The pool
(`universe.CANDIDATE_POOL`, fixed, every reserved tranche excluded) is the
population that is both stable and large enough to answer anything.

**What is declared.**

- **The population for H0 and H1 is the pool bucket.** The 349-settled-
  episode stopping rule, the episode unit, the bases, the dust floor and
  the resolver are exactly as written below and apply to `bucket: pool`
  rows. `forward_log.summary()` reports the headline by-basis reading and
  the gate on the pool; `report()` says so on the gate line.
- **The live bucket is the alerting view.** It is still recorded in full,
  still graded, still reported per bucket and per episode, and it NEVER
  gates: no mean R from the live bucket is reported as a result, at any
  sample size, under this document. It is the record of what was alerted.
- **The pullback bucket is unchanged** (its own pre-registration).
- **H1 stands as written** (the HQ tier adds ≥ +0.15 R per episode over
  base, on the pool). Re-posing it for a tier that has fired 0 times in
  243 taken rows (BACKLOG 34 D) is a separate decision and is NOT made
  here.

**What was known when this was declared, stated so it cannot be read as
selection.** Pool rows have been recorded since 2026-10-12, and at
declaration the pool read 42 episodes, 26 settled, mean −0.332 R (18
rows graded stop-first), the live bucket 18 / 15 / −0.196 R. The
decision is driven by arrival rate and population stability, both of
which were in this document's own power paragraph before any pool row
existed ("~6 months at ~50 names"); the observed sign is negative on both
buckets and the amendment does not favour either. Neither number is a
result; the gate is 349 settled pool episodes.

**Enforcement.** `forward_log.HEADLINE_BUCKET = "pool"`; the selftest
asserts the by-basis reading and the gate count come from the pool and
that a live-bucket outcome moves neither; `consistency_check` reads the
same.

## Amendment 2026-10-19 — settled bars only, and a fingerprint that names every rule (BACKLOG 35). READING CHANGES: no rule changes

Two corrections from the external review of 2026-10-19, declared before the
first row they touch.

1. **Grading reads settled bars only.** The frame the scan fetched
   mid-session carried today's forming bar and `resolve_signal` was shown
   it: a 20-bar timeout was marked at an 11:10 ET price and a level touched
   before noon was graded as if the bar had closed. From 2026-10-19
   `scanner.bars_for_grading` drops today's bar until the session closes.
   An outcome that forms intraday is written on the next run after the bar
   settles (the post-close pass at the latest). The resolver, its stop-first
   rule, costs, hold and void reasons are unchanged.
2. **The config fingerprint now names every rule in force** (and, from
   amendment E of 2026-10-28, the gate precision). It hashed six
   fields; `hq_min_rr` 1.0, `volume_mult` 1.2, `volume_soft_mult` 0.7 and
   `spy_regime_on` True were in force throughout but not named, so a change
   to any of them would not have moved it. Naming them moves the hash once,
   from `e7d75b855a51` to `c357a0d01ff5`, with the same rules. Rows under
   both hashes are ONE dataset; `forward_log.KNOWN_CONFIGS` declares both
   and `report()` says so instead of warning. Any later hash is a rule
   change and warns as before.

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
