# Second external review (2026-10-20, reviewed SHA 340a221): verification and response (BACKLOG 36)

The owner had the fixes of 2026-10-19 (BACKLOG 35, PRs #138 to #143)
re-audited outside this project. This file checks its claims against the
code and says what was done about each. Every claim checked here held.

## Its five priorities

| # | claim | verdict | action |
|---|---|---|---|
| 1 | The first PUT merges against an older GET than the sha it sends; a remote change between the two reads is overwritten with no conflict. Journals are whole-list overwrites across two app sessions. | CONFIRMED (`_gh_put` re-merged only when `attempt > 0`) | **Fixed:** `_gh_put` re-merges on every attempt from the same GET that supplies the sha. App-owned files (journal, skip log, alert log) are now written only at the version this session loaded (`_LOADED_SHA`): a changed remote refuses the overwrite and asks for a reload; a file loaded from the local fallback is "unknown" and is never overwritten blind. Journal rows are not merged by id, by choice: the journal supports deletion (the ZZZ row) and a union would resurrect deletions; the conditional write makes the second session's overwrite impossible instead. |
| 2 | Close acknowledgment incomplete: journal ok but position save failed still `ok=True`; `save_journal` moved the session copy before the durable write; a stale local record of a closed id is resurrected; with GitHub off a failed local write reads as success. | CONFIRMED, all four | **Fixed:** `save_positions` returns the error and `close_position` reports a PENDING close (`journaled: True`, `ok: False`) that a retry completes idempotently; the app says which case it is. `save_journal` moves the session copy on success only. `merge_positions` drops a local id the journal says is closed. With GitHub off, `save_result` returns the local write's failure. |
| 3 | Non-finite inputs can become signals, quotes, costs, outcomes, IV rows; a complete 78-bar paper session with missing lows is finalised; fractional contracts accepted. | CONFIRMED by reading (`evaluate`, `record_outcome`, `iv_snapshot.record` spot, `check_option_cost` carry no finiteness or integer checks); the paper-session case NOT VERIFIED here | **Open, BACKLOG 36 group A:** one validator for finite OHLC, positive prices, sorted unique sessions, finite R before `record_outcome`, finite spot/strike before an IV row, finite OHLC in `session_coverage`, `0 < bid <= ask` and integer contracts in the cost gate. Each is a refusal with a reason, never a fill with zero. |
| 4 | Gates compare rounded values (ADX 34.96 passes 35; R:R 0.996 passes 1.0); no freshness guard; future-dated bars survive after the close. | CONFIRMED by reading | **Owner's decision, not done here:** comparing unrounded values is a RULE change that moves what the record records (a 34.96 bar that passes today would not), so it needs a dated declaration before its first row, like any threshold change. The future-bar drop (bars dated after today removed whatever the clock) is safe and is in group A. A freshness guard matters for the app's manual paths only; the scanner fetches fresh. |
| 5 | NEW REGRESSION: `return save_journal(journal)` in `add_journal_trade` made the alert bookkeeping after it unreachable. | CONFIRMED, introduced by PR #142 | **Fixed:** the journal's durability decides the return; on success the alert is marked journaled and a bookkeeping failure is logged without undoing the row. Selftest covers both paths. |

## Fixes it confirmed held

Paper session coverage, evidence hash v2, keyed attachment, IV window,
clustered SE, JSONL fallback, option entry volatility, settled-bar SPY
regime and grading, batch prices, drawdown, monitor exit status, outage
stamp, position merge, close failure and ids. Its note on the two-byte id
suffix is fair; a UUID-sized suffix is a one-line change and is in group A.

## Its remaining table

Backtest warm-up discontinuity, dropped execution session on missing
volume, regime minimum-history disagreement, partial batch retry, contract
selection divergence, pullback history validation, option clock and
end-of-sample expiry, spread maturity, portfolio chronology, macro status
wording: all pre-existing, all logged under BACKLOG 36 group B (research
engines) and group C (app paths). The three policy items (dry-run rows,
next-open R:R, opening-target precedence) stay policy and are not changed.

## Verification of this response's fixes

Eight mutations (first PUT merging against the older read; stale local
closed id kept; overwrite despite a changed remote; overwrite after a
fallback load; local write failure reported as success; bookkeeping
unreachable again; failed removal reported ok; session journal updated
before acknowledgment) each fail the right assertion; files restored
byte-identical. Full tests.yml harness and 47 consistency checks pass.

## Third pass (same day, reviewed the bot commit 612de5c, code as merged by PR #144)

Five persistence gaps in the fixes above, all CONFIRMED by reading and all
fixed in the follow-up PR:

| gap | what happened | fix |
|---|---|---|
| Shared revision tracking | `_LOADED_SHA` was a module dict; Streamlit serves every tab and device from one process, so a second session's load refreshed the version the first session's save then matched | the ledger lives in `st.session_state` (`_sha_store()`); the module dict only when no session exists |
| Pending-close retry failed | `save_positions` put the list without the position into the session BEFORE the write; on failure the retry found no position | on failure the session keeps the previously persisted list; the close is retryable |
| Rejected writes entered the fallback copy | the local cache was written before the PUT, so a rejected write surfaced as committed in the next outage | the cache is written on a successful durable write only (GitHub off: local is the store and its failure is reported) |
| Closed positions could reappear | a stale session's closed ids came from its own journal copy | `journal_closed_ids()` is the session's ids UNION the repo journal's ids read at save time; unreadable adds nothing |
| First-create race | a file this session loaded as absent, then created by another writer, was overwritten with its sha | `SHA_ABSENT`: the first write must create; a file that now exists refuses with "reload" |

Each guard falsified by mutation. Still open from this review: numeric
validation (group A), rounded gates (owner's rule decision), stale-data
checks, research engines (group B).
