# Date-clustered CIs, run 1 — the ranges widen 10–19 %, no conclusion moves

**Run by the owner 2026-11-04 in the Codespace on the frozen bars** (BACKLOG
47; owner: "go with the same-date error bars as well"). Raw output:
`backtest_12t10y_engine_dates_codespace_run1.txt`, the same command as
`backtest_12t10y_engine_all_codespace_run1.txt` with the pending fix asked
for by name (`--engine-fixes all,date_clusters`).

## Like for like

- **Same bars:** both print `data: v2-e09e31d6eaf30233 (12 series, 12 cached /
  0 fetched)` and `Bars tested: 28344`.
- **Same trades:** a line-by-line diff of the two files differs only in the
  engine line and the two CI lines. Every trade, R, win rate, PF, drawdown,
  per-ticker row and gate count is identical, as the fix promises (it changes
  no trade, R or mean).

## What moved: the long and short 95 % ranges

| | trades | mean R | independent | by episode (the default) | widest of episode / week / month (date_clusters) |
|---|---:|---:|---|---|---|
| LONG | 875 | +0.066 | [−0.063, +0.196] | [−0.083, +0.216] | **[−0.098, +0.231]** (widest: month; 606 episodes, 323 weeks, 100 months) |
| SHORT | 490 | −0.315 | [−0.468, −0.163] | [−0.480, −0.151] | **[−0.511, −0.120]** (widest: month; 334 episodes, 193 weeks, 80 months) |

The standard error, from those ranges:

| | independent | episode | month | month over episode | month over independent |
|---|---:|---:|---:|---:|---:|
| LONG | 0.066 | 0.076 | 0.084 | ×1.10 | ×1.27 (design effect 1.61) |
| SHORT | 0.078 | 0.084 | 0.100 | ×1.19 | ×1.28 (design effect 1.64) |

**What it says.** Trades on different stocks entered in the same month move
together more than a ticker's own overlapping trades do: the month grouping
is the widest for both sides. The current default (episode only) understates
the long range by about 10 % and the short range by about 19 %.

**No conclusion moves:**
- The long side's range still straddles zero: no edge, as the record says.
- The short side's range still sits wholly below zero: shorts lose reliably,
  which is why the live system is longs-only.

## The decision this leaves (the owner's)

Whether `date_clusters` joins the default engine. Recommended: yes. It is the
more honest range here, it never narrows one, and it moved no conclusion.

If adopted, it becomes one change:
- `date_clusters` moves from `PENDING_ENGINE_FIXES` into
  `ALL_ENGINE_FIXES`, so `all` and the default include it.
- `backtest_12t10y_engine_all_codespace_run1.txt`, the one committed result
  whose ranges it moves, gets an ENGINE CHANGED banner pointing here and to
  this run, as item 47's flip did for twelve files.
- `adx_retest`'s bands read it from then on.

Until then nothing changes: the default engine's ranges are the episode ones.
