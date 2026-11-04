# The research engine's six fixes, measured (BACKLOG 47) — 2026-11-04

**Run by the owner in a Codespace that holds the record's frozen bars:**
every fingerprint matches the record's own (`v2-24b0f52e1203ecd5`,
`v2-6b5c07fe41849b1c`, `v2-e09e31d6eaf30233`), so the files named
`_codespace_` are frozen-bar runs, not fresh downloads. The run with no fixes
reproduces `record_recheck_floor_run1.txt` (2026-10-29) digit for digit, so
the default engine has not moved since, and **every difference below is the
fixes alone**, each isolated by a run with that fix and no other.

## The three recorded cuts (`record_recheck.py`), trades and mean R

| cut | engine | trades | avg R | long n | long R | short n | short R |
|---|---|---:|---:|---:|---:|---:|---:|
| 7t/5y | none (committed) | 413 | +0.015 | 256 | +0.312 | 157 | −0.469 |
| | `price_gaps` | 413 | +0.015 | 256 | +0.312 | 157 | −0.469 |
| | `lookback` | 405 | +0.010 | 256 | +0.312 | 149 | −0.508 |
| | `open_beyond_target` | 413 | +0.015 | 256 | +0.312 | 157 | −0.469 |
| | `dividends` | 413 | +0.019 | 256 | +0.319 | 157 | −0.472 |
| | **all six** | **405** | **+0.014** | **256** | **+0.319** | **149** | **−0.511** |
| 12t/5y | none (committed) | 680 | −0.029 | 428 | +0.095 | 252 | −0.238 |
| | `price_gaps` | 680 | −0.029 | 428 | +0.095 | 252 | −0.238 |
| | `lookback` | 668 | −0.030 | 428 | +0.095 | 240 | −0.251 |
| | `open_beyond_target` | 680 | −0.029 | 428 | +0.095 | 252 | −0.238 |
| | `dividends` | 680 | −0.026 | 428 | +0.102 | 252 | −0.244 |
| | **all six** | **668** | **−0.027** | **428** | **+0.102** | **240** | **−0.257** |
| 12t/10y | none (committed) | 1376 | −0.075 | 884 | +0.059 | 492 | −0.315 |
| | `price_gaps` | 1376 | −0.075 | 884 | +0.059 | 492 | −0.315 |
| | `lookback` | 1365 | −0.076 | 875 | +0.056 | 490 | −0.312 |
| | `open_beyond_target` | 1376 | −0.072 | 884 | +0.063 | 492 | −0.315 |
| | `dividends` | 1376 | −0.072 | 884 | +0.066 | 492 | −0.318 |
| | **all six** | **1365** | **−0.071** | **875** | **+0.066** | **490** | **−0.315** |

## What each fix did on the record's bars

- **`price_gaps`: nothing.** No price row with a NaN in any of the 31
  frozen series (no `!!` warning printed). The defect is real in the code
  and absent from this data.
- **`lookback`: 8 to 21 fewer trades**, the setups read on the first 19
  bars after the warm-up (133 / 228 / 228 bars no longer read). In the
  5-year cuts every one was a short. Mean R falls by 0.001–0.005 in each cut.
- **`open_beyond_target`: fired once in 1,376 trades** (12t/10y), a −1 R
  stop re-booked as a win at the open: +0.003 avg R, +0.004 long R. Never
  in the 5-year cuts. Rare, as the review said.
- **`dividends`: the largest share.** 24 / 27 / 41 trades held over an
  ex-date, +1.81 / +1.03 / +3.46 R credited net; the long side gains
  +0.007 R in every cut (credits, and stops no longer hit by the ex-date
  drop alone), the short side pays 0.003–0.006 R. These are low-yield
  growth names; a high-yield universe would move more.
- **All six together**: the sum of the above (the fixes barely interact).

## The statistics fixes (12t/10y, `backtest_12t10y_engine_all_codespace_run1.txt`)

- **`clustered_ci`**: the long side's 875 trades form 606 episodes; its CI
  widens from [−0.063, +0.196] to **[−0.083, +0.216]**, still spanning zero.
  The short side's 490 trades form 334 episodes: [−0.480, −0.151], still
  clear of zero on the negative side.
- **`time_order`**: the aggregate MaxDD in exit order is **−186.1 R** on
  −96.4 R total. The committed engine's ticker-order figure on these bars
  was not run, so the fix's own effect on this line is not isolated here
  (the −86.1 R in `longshort_split.md` is an older engine). One command
  would isolate it: the same cut with every fix but `time_order`.

## Reading

**No sign changes and no conclusion moves.** The long side improves by
+0.007 R in every cut, from dividends; the short side gets worse in the
5-year cuts (−0.042 and −0.019 R, mostly the look-back's dropped early
trades) and is unchanged over 10 years. The record's findings stand as they were: the long side's mean is
positive with a CI that spans zero (now wider once overlap is counted),
the short side is reliably negative, and the aggregate is negative on the
out-of-sample set. Turning all six on as the default changes the record's
numbers by these amounts and nothing more. That decision is the owner's.
