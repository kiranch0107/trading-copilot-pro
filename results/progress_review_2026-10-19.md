# Progress review, 2026-10-19 — what moved since the 13 October reviews, what the record now shows, what to build next (BACKLOG 34)

**Owner's request 2026-10-19: "re-perform deeper analysis once again, see if
anything improved from last analysis, what can be further improved, what
idea can be developed."** Parents: `logic_review_2026-10-13.md` (BACKLOG 31)
and `journal_review_2026-10-13.md` (BACKLOG 32). Read on main at the 19:07
UTC heartbeat of 2026-10-19 (forward log 287 rows, chain intact). Every
number below is descriptive; nothing here is a result under any
pre-registered bar.

## 1. What improved

| decision (13 Oct) | state on 19 Oct |
|---|---|
| Stop reading alerts as the output; the record is the output | Three buckets recording: live 43 rows, pool 83, pullback 6. 0 alerts were sent in the period, so the alert-row plumbing (PR #132) has had nothing to record. |
| Pool bucket (BACKLOG 30) | Live since 10-13: 5 sessions, 83 rows, ~25 episodes. Intake is now **~5 episodes a session against the watchlist's ~0.9**. Pool rows are graded on the pool run and post-close only; the deferral is reported as such (PR #133). |
| Pullback bucket, pre-registered (BACKLOG 31 option 1) | 6 rows, 3 episodes, first outcome ROST +1.54 R. JNJ has signalled on four consecutive bars and is one episode. |
| Take or note the two stops (journal decision 2) | PFE closed 10-14 14:46 ET, NVDA closed 10-16 13:08 ET, both with trade times. The exit monitor watched NVDA to the end (STOP alerted, then TIME at 1 DTE) and the position list is empty. |
| Journal fields and app alert history (decisions 4, 5) | Built and in use: the last three rows carry `traded_at`. `alert_history.json` is empty because nothing has been alerted. |
| ADX 30 vs 35 (BACKLOG 33) | Asked and answered on the live config: NOT ESTABLISHED; the [30, 35) band carries nothing measurable. |
| Operations | 47 workflow runs 10-16 to 10-19, all green except one CI run superseded by a later push. Every state push landed on attempt 1. Universe snapshot 10-18 ran. IV snapshots: 48 rows over 6 sessions. |

**Not done from the 13 Oct lists:** the ZZZ test row is still in the journal;
`signal_key` is empty on every journal row, so zero trades attach to the
record; no skipped signal has been logged since 2026-08-31; the Phase 2 paper
record holds one voided test call and nothing since; the RS-basket-versus-SPY
measurement (logic review decision 3) is not started; BACKLOG 23's "which
config" is decided for one run only.

## 2. What the record shows now, read two ways

**The pre-registered unit is the episode and the report does not count it.**
`forward_record_preregistration.md` fixes: rows on consecutive bars for the
same (ticker, trend) are one episode, dated at its first bar, **only the first
bar's row is graded**, and the 349 gate counts episodes. The grader grades
every row and `forward_log.report()` prints row counts under a line that says
"EPISODES". Collapsed by the pre-registration's own rule:

| bucket | rows | episodes | settled rows → mean R | settled episodes (first row) → mean R |
|---|---:|---:|---|---|
| live | 43 | 15 | 26 → −0.177 | 7 → +0.162 |
| pool | 83 | 25 | 22 → −0.442 | 9 → −0.593 |
| pullback | 6 | 3 | 1 → +1.535 | 1 → +1.535 |
| all | 132 | 43 | 49 | 17 |

The two readings disagree in sign on the live bucket. Neither is a result,
but the gate is at 17 of 349, not 49, and the number the report prints is not
the pre-registered one. TMO (8 rows, one episode, 6 losses in a row) and ACN
(5 rows, one rally, 5 wins) are the whole difference. **Fix:** an
`episodes()` reading in `forward_log`, reported beside the rows, gating on
episodes; row-level grading stays (it is data). This is a reading change, not
a record change, and needs no amendment.

**The 4-ATR target has never been reached.** Of 49 settled rows:

| planned reward-to-risk | n | wins | mean R | how they ended |
|---|---:|---:|---:|---|
| ≥ 3 (the full 4-ATR target) | 21 | 2 | −0.859 | 19 stopped, 2 timed out, **0 at target** |
| 1.5 to 3 | 5 | 3 | +0.667 | 3 target, 2 stop |
| < 1.5 (target capped at 20-bar resistance) | 23 | 16 | +0.082 | 16 target, 7 stop |

Every target hit in the record is a capped target. 68 open rows carry the
full target and will say more, but the shape is the logic review's §(c)
measured forward: the stop at 1.25 ATR is inside two days of noise (25 of 49
rows settled within 2 bars) and the 4-ATR target is not reached before it.
The live geometry's realised payoff is +0.97 on a win against −1.02 on a
loss, which needs a 51 % win rate; the live bucket's is 42 %.

**The gate is still starved, and the tier features still point the wrong
way.** 0 of 132 taken rows are high-quality; 0 alerts in the period. Among
settled rows, signal-bar ADX ≥ 35: 0 wins of 9, mean −1.09 R; RSI ≥ 70: 1 of
7, −0.82 R. Nine and seven rows prove nothing, but they are the sign the
ladder (BACKLOG 33) and the setup-population table already gave. H1 (the HQ
tier beats base by +0.15 R) cannot be tested by a tier that never fires.

**Clustering.** 49 outcomes sit on 22 names; TMO, NVDA and ACN are 19 of
them. The watchlist rotated 3 of 8 again on 10-18 (CRWD, MRK, TGT out; CRM,
ABNB, PFE in), so the live bucket's 15 episodes sit on 13 different names in
17 sessions. BACKLOG 24's open half (no cumulative churn view) stands.

**IV snapshots, descriptive only.** 48 rows, 6 sessions: implied above
trailing 20-day realised in 88 % of rows, mean +7.3 vol points. This is not
the pre-registered metric (implied minus the NEXT 21 sessions' realised) and
is reported here only as the direction to expect in April.

## 3. What can be improved now (no new data needed)

1. **Episode reading in the report** (§2, first table). Small change in
   `forward_log`, selftested, no amendment.
2. **Planned-reward split in the report.** The ≥ 3 / < 3 split is the one
   line that explains why wins are small and losses full-size. Descriptive,
   per bucket.
3. **Journal hygiene, owner's:** delete ZZZ; put the signal bar date on each
   open so `signal_key` attaches; log skips again or retire the skip log.
4. **Phase 2:** use it or close it. One voided test call in six sessions is
   neither.
5. **Cumulative churn view** in `churn_tracker` (BACKLOG 24 half): names
   present per week, mean tenure, share of live episodes on names that left
   within a week.

## 4. Ideas worth developing (each is a pre-registration, not a change)

**A. A forward random-entry control arm.** The record's weakness is that
"mean R against zero" cannot separate signal from drift, and `drift_null` can
only answer that on spent history. Record, beside every taken signal, one
**control row**: the same ticker, the same session, entry on the next open
with the same stop and target distances, chosen as if the signal had fired on
a random bar in the prior 20 sessions (seeded, reproducible). Grade both with
the same resolver. The pre-registered reading is signal minus control, which
is the only forward number that can say the entry logic contributes anything.
Cost: one extra row per signal, no extra fetches (same frame). This is the
drift null run forward, clean.

**B. The RS basket as a holding strategy.** Five weekly snapshots exist
(`universe_history/`). Record a **basket row** each Sunday: the 8 names
selected and SPY's close; grade it a week later as equal-weight basket return
minus SPY. No signal, no geometry, no stops. It answers the logic review's
decision 3 ("does the selection lose to the index") with the data the system
already makes, and it is the question that matters for a $5,000 account.

**C. Make the pool the primary population for the base-signal question.**
It arrives five times faster than the watchlist, on the same code, graded
the same way. The forward pre-registration reads buckets separately; an
amendment naming the pool as the population for the "base signal against
zero and against control (A)" question, with the live bucket kept for H1,
would reach 349 episodes in roughly 70 sessions instead of three years.

**D. Re-pose H1.** A tier that fires 0 times in 132 cannot be compared with
anything. The honest options are (i) lower the gate as an intake choice
(BACKLOG 33), or (ii) replace the threshold tier with a pre-registered rank
(top k by one score) so it fires by construction. Since ADX, RSI, ATR % and
volume were all measured uninformative, (ii) would be a tier chosen for
arrival rate, not for edge, and should say so.

**Prediction, stated now.** A reads signal ≈ control on the pool within
costs; B reads the basket within ±2 % of SPY over a quarter with no
significance; the 68 open full-target rows settle mostly at the stop. If all
three hold, the system's measured value is in the record-keeping and the
risk discipline, not in the entry.
