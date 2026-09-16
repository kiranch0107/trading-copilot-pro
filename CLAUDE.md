# CLAUDE.md

Operating rules for Claude sessions in this repo. **Substance lives elsewhere** —
`BACKLOG.md`'s "Working conventions" is canonical for how the code works (CRLF
files, single-sourced costs, relative backtest windows, data fingerprints). Do
not restate those here; a second copy is the drift this project has paid for
repeatedly. This file covers only how a session should *operate*.

## Shipping: merge without asking

**Standing instruction from the owner, 2026-09-16.** Work goes to a branch, a
PR, and then **merge it — do not stop to ask.** Two rounds of "have the changes
been merged?" / "merge once done" is the friction this removes.

"Done" means all of:

1. The module `--selftest`s and `python consistency_check.py` pass locally.
2. CI is **green** on the PR's head commit — the `selftests` check completed
   with `success`. Not "queued", not "in progress".
3. The PR is `mergeable_state: clean` — no conflict with `main`.

Then merge, pinning `expectedHeadSha` to the commit you verified, so nothing
merges that was not reviewed.

**Prefer GitHub's native auto-merge** (`enable_pr_auto_merge`) when the
repository allows it: it lets CI gate the merge rather than a session having to
stay awake for it. If it is unavailable, merge directly once the conditions
above hold.

### What this does NOT authorise

The instruction removes a *confirmation step*, not a *judgement step*.

- **Never merge on red CI.** A failing check is work, not a formality to wait
  out. Fix it and push.
- **Never merge a change that needs a decision the owner has not made.** A
  design choice between two defensible options (see BACKLOG 19) is theirs. Ship
  the review of it; do not ship a guess at the answer.
- **Never merge code whose committed result it invalidates.** If a change moves
  numbers in `results/`, the fix and the re-run are ONE change. If the re-run
  cannot be produced in this session, mark the affected file VOID in place, say
  so in the PR body and the merge commit, and leave the fix out — a committed
  result that disagrees with the code that produced it is worse than either
  alone. This is what PR #90 did with `portfolio_replay_run1.txt`.
- **Say what was not done.** A merge is not a claim that the findings were
  fixed. State open items in the PR body and the merge commit, as items 17-20
  are stated, so a later reader cannot mistake "merged" for "closed".

## Market data is usually unavailable

Sessions on Claude Code for the web reach Yahoo through a proxy that blocks it,
so anything calling `yfinance` fails. Every `--selftest` and
`consistency_check.py` is offline by design and still runs — that is the point
of them. Do not conclude a module is broken from a network failure, and do not
record a research result you could not actually run.

## GitHub access

No `gh` CLI. Use the GitHub MCP tools (`mcp__github__*`).
