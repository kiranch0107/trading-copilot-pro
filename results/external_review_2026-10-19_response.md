# External review of 2026-10-19 (reviewed SHA 6095f74): verification and response (BACKLOG 35)

The owner had the repository audited outside this project on 2026-10-19
(comparison base: the 2026-10-08 audit at 92068e7). This file checks its
claims against the code on main at 104db06, which differs from 6095f74 by
markdown only. Each item is marked CONFIRMED (read in the code or
reproduced by an incident in the record), NOT VERIFIED (plausible, not
checked here), or POLICY (behaviour as designed, decision not defect).
Line numbers the review cites match the code.

## Verdict on the review

It is accurate where checked: about twenty claims read against the source,
none wrong. Its priorities are sound and its framing ("correctness before
changing thresholds in response to performance") is this project's own rule.
Two things to weigh: 106 failing audit cases overlap by root cause, as it
says, and several of the new findings sit in paths that hold no live data
yet (the paper record has one voided test call; no IV premium row exists
until about 2026-11-07; no journal row carries a signal key). The ordering
below reflects what is biting the record now, what will bite on a known
date, and what is latent.

## New findings

| # | finding | verdict | impact today | fix size |
|---|---|---|---|---|
| N01 | paper grader treats a truncated session as complete; substitutes the last bar at or before the call | CONFIRMED (`daytrade_paper.resolve_call`: `t0 <= t_call` picks the latest earlier bar; no coverage check through the close) | none: one voided test call in the record | medium; before Phase 2 is used |
| N02 | intraday CI mixes a trade-weighted mean with a session-mean SE | CONFIRMED (`intraday_rules.clustered`) | **no verdict can flip**: all 18 rules across the 5 m, 1 h and 4 h runs failed the mean-R clause, which the SE does not touch | small; fix and re-read, no re-run needed for the verdicts |
| N03 | keyed journal attachment skips ticker and direction | CONFIRMED (`forward_log.attach_outcomes`: key existence, taken status, date only) | none: no journal row carries a key yet | small |
| N04 | IV grading anchors on "history starts before the snapshot", not on the snapshot date; duplicate dates count toward 21 | CONFIRMED (`iv_snapshot.grade`, `forward_realised_points`) | none yet; first premium rows due ~2026-11-07 | small; before then |
| N05 | IV snapshots for names that left the watchlist are never graded (closes come from `frames` only) | CONFIRMED (`scanner.py` post-close block) | **will bite from ~11-07**: 3 of 8 names rotate weekly, so most snapshots will be censored | small: fetch unresolved names once |
| N06 | pullback accepts duplicate/unsorted/NaN history and infinite ATR; IV may persist NaN spot | CONFIRMED for the pullback reads (row count not distinct sessions; positional slice; `inf > 0`); spot=NaN NOT VERIFIED | latent, provider-data robustness | small, shared validator |
| N07 | paper evidence hash covers rounded closes only | CONFIRMED | low | small, versioned |
| N08 | JSONL fallback appends local rows to a partially parsed remote | CONFIRMED (`gh_sync.load_jsonl`) | low: read-only app view | small |

## Earlier findings, rechecked here

| finding | verdict | note |
|---|---|---|
| Close proceeds after a failed journal save | **CONFIRMED by incident**: 2026-10-13, the AAPL close left the position list while the journal put failed on a dead token; the sidebar showed a stale green | P0, as the review says |
| Write retry re-puts the same payload against a fresh SHA without re-merging | CONFIRMED (`gh_sync._gh_put`) | exposure is the app-versus-monitor window, seconds; still P0 class |
| Remote-only additions dropped; equal-rank stale exit reason wins | CONFIRMED (`merge_positions`) | matters with two app clients (phone and laptop) |
| Position id collides within one second | CONFIRMED (`OPT_{ticker}_{epoch}`) | low |
| Daily grader reads today's forming bar | CONFIRMED: `frames[tk]` is the raw frame; `resolve_signal` does not exclude the current date. TMO seq 1's timeout was marked at the 11:10 ET price, not a close | medium, affects the record now; grade on settled bars only |
| SPY regime read from the forming bar | CONFIRMED (`market_context.get_spy_regime` takes `iloc[-1]`; no partial-bar drop) | the regime field can differ between the 11:07 and post-close reads of one setup |
| App batch download uses adjusted prices; single fetch uses raw | CONFIRMED (`app.batch_get_data` omits `auto_adjust=False`) | the app's batch view can disagree with the scanner on dividend payers |
| ADX 34.96 passes 35 after rounding | CONFIRMED | POLICY-adjacent: changing it moves the config fingerprint; decide, then version |
| Warm-up discontinuity (141 rows keep 41, 140 keep 140) | CONFIRMED by reading `compute()` | matches the app on purpose; still a cliff |
| Option entry premium uses the entry bar's close in realised vol | CONFIRMED (`realised_vol(df["Close"].iloc[:entry_i + 1])`) | one-bar look-ahead; the review measured a 0.24-point mean change |
| Drawdown ignores initial equity | CONFIRMED (`backtest.stats`) | trivial |
| Outage cooldown stamped on a failed send | CONFIRMED (return value ignored) | low |
| Exit monitor returns success when every check failed | CONFIRMED (`run()` returns 0 unconditionally; failures are logged) | medium: a green workflow while nothing was checked |
| `_scan_cfg` omits volume threshold, HQ min R:R, regime policy | CONFIRMED | a change to any of them would not move the fingerprint |
| Contract selection differs between scanner and app | NOT VERIFIED (the app has no selector of its own that I found) | ask for the fixture |
| Quote/risk NaN acceptance, DTE/weekend/spread timing, portfolio chronology, CAGR | NOT VERIFIED here | plausible; the review says they reproduce |
| Dry runs write scan rows | POLICY (a dry run evaluated the bar; alerts and stamps are not written) | keep, or add a true no-persistence mode |
| Episode accounting | CONFIRMED, independently found the same day (`progress_review_2026-10-19.md`) | reading change |
| Coverage heartbeat is not coverage | fair: the scan row lists names scanned, not names that failed | small: record attempted/failed per bar |

## Order of work proposed (owner's call)

1. **Now, affects the record:** grade on settled bars only; read the SPY regime from the settled bar; complete `_scan_cfg` (one fingerprint bump, dated); exit-monitor nonzero exit on total failure; outage stamp on delivery only; drawdown.
2. **Before 2026-11-07:** N05 (fetch unresolved IV names), N04 (exact date anchor, distinct sessions).
3. **Before the next journal row with a key, and before Phase 2 is used:** N03; N01 and N07.
4. **Design work, P0 class:** close durability (remove the position only after the journal save is acknowledged; typed failure), write-retry that re-merges, tombstones for deletions, ids with a random suffix.
5. **Research integrity, with re-runs:** N02 (fix, re-read; verdicts cannot change), option entry look-ahead (fix, re-run the option studies on frozen bars, mark the old files), app batch price basis.

Nothing in the review argues for changing a threshold, and nothing here
does.
