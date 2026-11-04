# PRE-REGISTRATION — the larger D3: sell the option when the underlying reaches the signal's target? (77 pool names outside run 1, 10 years)

**Owner's request 2026-11-04: "write the pre-registration for a larger
D3".** D3 run 1 (`results/option_target_exit_run1.md`) settled 149 pairs,
one under its floor of 150, and was UNDERPOWERED: no reading was made, and
its own text said "a larger D3 would be a new pre-registration". This is
that pre-registration. It is a **new test, not a re-run of run 1**: no name
of run 1 is in it, so none of run 1's 149 pairs can be. Written and
committed BEFORE the run; nothing below may change after a number from it
is seen. Config: **live** (`backtest.live_cfg()`, BACKLOG 23). Engine: the
default (the six fixes, BACKLOG 47), printed on the file's data line.

**Why this is not what run 1 ruled out.** Run 1's reading said that
re-running it, or adding years or tickers to it, after seeing its numbers
would be choosing the analysis by its result. This test does neither: it
shares no pair with run 1, fixes its own population, window and bar before
its own run, and never pools run 1's 149 pairs. What run 1 informed is the
design's scale (its sd and its pairs per name-year, used in section 4),
not its direction: run 1's B − A (+1.77 / +0.53) is used nowhere below.

## 1. What stays exactly as run 1 fixed it

`results/option_target_exit_preregistration.md` holds the question and the
mechanism; neither is restated here. Unchanged:

- **The arms.** A, the structure as measured (`MEASURED_OPTION_STRUCTURE`):
  ATM call, 30 DTE at entry, TP +200 %, SL −50 %, out at 7 DTE, thesis exit
  on, 5 % round trip. B: A, plus sell when the underlying **closes** at or
  beyond the signal's target, at that close's mark, checked after STOP and
  before the premium TP.
- **The entries.** `option_backtest.pairs_on_frame()`'s rule: the live
  config's signals, longs only, R:R at or above the tier's `hq_min_rr`
  (1.0), not gapped at the fill. Both arms on identical entries.
- **The metric.** Per settled pair, B − A in percent of the premium; OPEN
  pairs (censored by the sample's end) left out.
- **The two multipliers.** IV 1.15 (primary) and IV 1.00; a verdict needs
  both to agree, because every option number here rests on the IV
  assumption.
- **ADOPT / REJECT / NOT ESTABLISHED**, read on the 95 % CI exactly as run 1
  fixed them.

## 2. What is new, and why

- **The population: 77 names.** `universe.CANDIDATE_POOL` as of 2026-11-04
  (the 83 names the live pool scans) minus the six of run 1 that are in it
  (AAPL, MSFT, META, NVDA, AMZN, TSLA; ROKU is not in the pool). These are
  the names the live system alerts on, so the decision applies to them.
  Run 1's names are left out whole, not only its five years, so the rule
  is one line and checkable. The list is at the end of this file and is a
  literal in the code (`D3_LARGE_TICKERS`), never recomputed from the pool.
- **The window: 10 years** (`backtest.download(…, 10)`, relative to the
  day of the run: about 2016-11 to 2026-11; the file prints the window's
  first and last dates). Twice run 1's span, and it reaches 2018's fourth
  quarter, the 2020 crash and the 2022 bear market, not only the 2021–26
  window run 1 saw.
- **One command, both multipliers, identical entries.**
  `python option_backtest.py --d3-large` fixes the population, the window,
  the structure and both multipliers, and takes no other option. An entry
  that either multiplier cannot price is dropped from both readings.
- **The standard error allows for pairs that move together: the WIDEST of
  four**, never narrower than any: independent; clustered by the entry's
  calendar **week** (a market move hits every name at once); by calendar
  **month** (an option lives about a month); and by ticker **episode** (a
  ticker's pairs whose holdings overlap: the live cooldown is 3 sessions
  against a ~23-session option, so one move can carry several entries).
  Each clustered SE is CR1. Run 1 used the independent SE; on 77 names that
  would be too narrow.
- **No count floor that voids the run.** Run 1's floor of 150 pairs voided
  every reading below it, whatever the CI said. Here the design is AIMED,
  by the power check, at a 95 % CI half-width of **3.5 points of premium**
  (a smallest detectable difference of 5 points at 80 % power: 2.8 / 1.96 ×
  3.5 = 5), and the verdict does not void a run that misses the aim: once
  the SE can be measured, an ADOPT or REJECT whose 95 % CI clears zero is a
  5 % test at any width, and EQUIVALENT is a test of its own. A wide CI
  weakens only a NOT ESTABLISHED, and the file then says what it cannot
  rule out. **UNDERPOWERED** is kept for a run whose SE cannot be measured:
  fewer than 2 settled pairs, or any of the three clusterings with fewer
  than 30 groups (CR1 is unreliable on fewer).
- **EQUIVALENT, a reading a sample this size can make.** If the 90 % CI
  lies inside **±5 points of premium** at both multipliers (two one-sided
  tests at 5 %), the two exits differ by less than 5 points either way.
  Why 5: run 1's arm A averaged +0.07 % of premium at IV 1.15 and +10 % at
  1.00, so the IV assumption alone moves the structure by about 10 points;
  a difference under half that is smaller than what is already unknown
  about the structure. And the engine's own driftless paths put B − A at
  −0.6 to −2.3 (run 1's power check), inside ±5, so EQUIVALENT is reachable
  when the exits differ only by the engine's mechanics.

## 3. The data, stated plainly

- **No clean data remains** (BACKLOG 15) and none is claimed. All 77 names
  have been used: 12 in the 591-trade validation, 32 in tranche B (spent by
  live trading), and every one by the forward record, which scans the pool.
  This is not held-out data in BACKLOG 15's sense.
- **For this question it is unexamined.** B − A has been computed only on
  run 1's seven names and on synthetic paths; no committed run measured, on
  any of the 77, how far moves run past the signal's target, which is what
  B − A turns on. What is known about these names (the signal's share
  outcomes) bears on B − A only through that, indirectly. The ledger in
  `data_reservation.lock.json` is not touched: there is no tranche to spend.
- **Survivorship** (BACKLOG 8): the 77 are today's large caps. Names that
  survived and grew had moves that kept running, which favours holding past
  the target (arm A). The bias leans toward REJECT; read a REJECT with it.
- **Overlap with run 1's calendar.** The last five of the ten years are run
  1's window on other names, so market-wide persistence in 2021–26 is
  shared; the ten-year window halves that share.

## 4. Power (BACKLOG 13, step 2) — computed before the run

`python option_backtest.py --d3-large-power` (seeded; no market data): 77
synthetic names over 10 years share a market factor with persistent
high-volatility spells (a market move hits every name at once), plus each
name's own noise; three settings for how much of a name's move is the
market's, two market paths each; a drift of about 7.5 % a year and no other
persistence, the world where the two exits should nearly tie. Every name
goes through `backtest.compute`, the live signal and both arms at both
multipliers, and is read exactly as the run is (`d3_clustered`,
`d3_large_verdict`).

```
Larger D3 power (seeded; 77 synthetic names x 10 years on a shared market path; read as the run is: SE the widest of independent / week / month / episode)
  market share low  path 5: 3735 pairs (4.9 a name-year) | verdict NOT ESTABLISHED, EQUIVALENT
     iv x1.15: B-A -0.47 sd 41.6 | DEFF week 1.10 month 1.60 episode 1.15 | SE 0.86 half-width 1.69 MDE 2.41 | 90% CI [-1.89, +0.94]
     iv x1.00: B-A -1.31 sd 42.0 | DEFF week 1.13 month 1.48 episode 1.12 | SE 0.83 half-width 1.64 MDE 2.34 | 90% CI [-2.69, +0.06]
  market share low  path 6: 3921 pairs (5.1 a name-year) | verdict NOT ESTABLISHED, EQUIVALENT
     iv x1.15: B-A -0.46 sd 45.2 | DEFF week 1.28 month 1.59 episode 1.25 | SE 0.91 half-width 1.78 MDE 2.55 | 90% CI [-1.95, +1.04]
     iv x1.00: B-A -1.25 sd 44.6 | DEFF week 1.21 month 1.25 episode 1.15 | SE 0.80 half-width 1.56 MDE 2.23 | 90% CI [-2.56, +0.06]
  market share mid  path 5: 4020 pairs (5.2 a name-year) | verdict NOT ESTABLISHED, EQUIVALENT
     iv x1.15: B-A +1.32 sd 42.4 | DEFF week 1.97 month 2.54 episode 1.18 | SE 1.07 half-width 2.09 MDE 2.98 | 90% CI [-0.43, +3.07]
     iv x1.00: B-A +0.49 sd 40.9 | DEFF week 1.71 month 2.12 episode 1.18 | SE 0.94 half-width 1.84 MDE 2.63 | 90% CI [-1.05, +2.04]
  market share mid  path 6: 4107 pairs (5.3 a name-year) | verdict NOT ESTABLISHED, EQUIVALENT
     iv x1.15: B-A -1.06 sd 46.3 | DEFF week 3.66 month 5.28 episode 1.29 | SE 1.66 half-width 3.25 MDE 4.65 | 90% CI [-3.79, +1.67]
     iv x1.00: B-A -1.15 sd 46.3 | DEFF week 3.25 month 4.16 episode 1.18 | SE 1.47 half-width 2.89 MDE 4.12 | 90% CI [-3.57, +1.27]
  market share high path 5: 4228 pairs (5.5 a name-year) | verdict NOT ESTABLISHED, EQUIVALENT
     iv x1.15: B-A +1.78 sd 44.3 | DEFF week 3.57 month 5.80 episode 1.17 | SE 1.64 half-width 3.21 MDE 4.59 | 90% CI [-0.92, +4.48]
     iv x1.00: B-A +1.43 sd 41.9 | DEFF week 2.97 month 3.69 episode 1.14 | SE 1.24 half-width 2.42 MDE 3.46 | 90% CI [-0.60, +3.47]
  market share high path 6: 4158 pairs (5.4 a name-year) | verdict NOT ESTABLISHED, EQUIVALENT
     iv x1.15: B-A -0.43 sd 43.4 | DEFF week 5.46 month 7.26 episode 1.32 | SE 1.81 half-width 3.56 MDE 5.08 | 90% CI [-3.41, +2.56]
     iv x1.00: B-A -0.37 sd 45.1 | DEFF week 4.30 month 5.25 episode 1.24 | SE 1.60 half-width 3.14 MDE 4.48 | 90% CI [-3.00, +2.26]
```

(B − A, its sd, SE, half-width and MDE in points of premium; MDE = 2.8 ×
SE, 80 % power, 5 % two-sided; DEFF = the clustered variance over the
independent one.) What it says:

- **About 3,700–4,200 pairs** (4.9–5.5 a name-year, counted over the full
  window). Run 1 drew 4.3 a name-year on the same count (149 pairs, seven
  names, five years), and a few of the 77 have shorter histories (ABNB from
  2020, CRWD from 2019), so the real run may draw fewer: about 3,200. At
  3,200 pairs every SE below is about 12 % wider (√(4,000 / 3,200)): a
  half-width of 1.8–4.0 and an MDE of 2.5–5.7 points.
- **sd 41–46 % of premium** per pair (run 1: 36–41), the same scale.
- **Pairs move together, mostly by month:** the month clustering is the
  widest in every setting, DEFF 1.3–7.3 (week 1.1–5.5, episode 1.1–1.3),
  growing with how much of a name's move is the market's, the hold test's
  lesson again.
- **The design resolves about 5 points.** SE 0.8–1.8; the 95 % half-width
  is 1.6–3.6 and the MDE 2.2–5.1 points, against 8–9 at run 1's 149
  pairs. The aim (half-width 3.5) is met in 11 of 12 readings; the twelfth,
  names moving most with the market, is 3.56; at 3,200 pairs three of the
  six worlds would miss it (both high-market paths and one mid, at 3.6–4.0).
  A floor on the width would void those runs; here they read like the
  others, and a NOT ESTABLISHED from them says what it cannot rule out.
- **Under this null B − A lies between −1.3 and +1.8**, and every setting
  reads **NOT ESTABLISHED, EQUIVALENT**: when the exits differ by little,
  the design can say so. In the world with persistent trends (run 1's power
  check) B − A was −22 to −25, which this design would REJECT by a wide
  margin.
- **What it cannot do:** resolve a difference of 1–2 points, the size the
  engine's own mechanics produce with no persistence at all.

## 5. The bar (fixed now)

Read mechanically by `option_backtest.d3_large_verdict()`, printed on the
file's `VERDICT` line.

- **UNDERPOWERED** if, at either multiplier, the SE cannot be measured:
  fewer than 2 settled pairs, or any clustering with fewer than 30 groups.
  No other reading is made from the file.
- **ADOPT the target exit** only if B − A > 0 with the 95 % CI excluding
  zero **at both** multipliers. Built as one change to
  `MEASURED_OPTION_STRUCTURE`, the exit monitor and the journal, as the
  thesis exit was (BACKLOG 42 H1).
- **REJECT it** if B − A < 0 with the 95 % CI excluding zero at both. The
  structure stays as measured. If the run is also EQUIVALENT, the reading
  is "selling at the target costs a little, of the size the engine's
  driftless paths produce", **not** "moves after this signal keep running":
  persistence is read only from a REJECT that is not EQUIVALENT.
- Otherwise **NOT ESTABLISHED**; the structure stays as measured. If it is
  not also EQUIVALENT, the file says the run "cannot rule out a difference
  of 5 points of premium": not evidence of no difference, whatever the
  CI's width.
- **EQUIVALENT** is stated beside any verdict but UNDERPOWERED when the
  90 % CI lies inside ±5 points of premium at both multipliers. With NOT
  ESTABLISHED it means the choice between the exits is worth less than 5
  points either way: tying the option's exit to the alert's target on
  grounds of coherence is then the owner's call, made with that bound
  known.

## 6. Prediction, stated now

B − A between −3 and +2 points of premium at both multipliers, and
**NOT ESTABLISHED, EQUIVALENT**: the signal shows no drift beyond random
entry (`drift_null`), so little persistence past its targets, and the power
check's null worlds read so in every setting. If ten years of rising large
caps (and survivorship) carry persistence past the target, a REJECT, most
likely still EQUIVALENT. An ADOPT would need moves to fall back after the
target, which nothing on file shows.

## 7. The run (owed in the Codespace)

```
python option_backtest.py --d3-large | tee results/option_target_exit_large_run1.txt
```

- The first run fetches 77 series of 10 years from Yahoo, plus their
  dividends (a few minutes; the pairs themselves take about a minute); the
  bar cache keeps them and never expires, and the file prints the
  fingerprint, the engine and the coverage.
- **A partial universe computes nothing.** If any name fails to load, the
  run prints `NOT RUN`, the coverage and no number, and exits 1 (2 if
  nothing loaded, as in a session that cannot reach Yahoo). Re-run until
  all 77 load (the cache keeps what loaded).
- **The first complete run is the run.** Once a file prints a `VERDICT`
  line it is committed as it stands, with its reading. No re-run after a
  number is seen: not with `--refresh-cache`, another engine, other years
  or other names. A larger D3 still would be a new pre-registration.
- The file must print `Config : live`, the window's first and last dates,
  `IV: 1.15x realised (primary) and 1.0x realised`, the data line and
  `DATA COVERAGE: 77 of 77`.

## 8. What it commits anyone to

Nothing beyond the reading above. Only ADOPT changes the live system. Run
1's 149 pairs stay descriptive, never pooled with this run.

## Enforcement

`option_backtest.py --selftest` (in CI): the population is 77 distinct
names with none of run 1's; both multipliers read identical entries, and an
entry one of them cannot price leaves both; the one-set pair loop is
unchanged; the SE is the widest of the four and never narrower than
independence, a ticker's overlapping holdings are one episode, a single
cluster is unmeasurable; every verdict, the EQUIVALENT reading and the
cluster floor; a CI clear of zero decides at any width; persistence is read
only from a REJECT that is not EQUIVALENT; a partial universe computes no
number. Each guard was broken on purpose and the self-test failed every
time (13 of 13).
`consistency_check.check_option_target_exit_large_preregistered`: run 1's
names are its file's; the population in the code is the list below, in
order; arm A is `MEASURED_OPTION_STRUCTURE`; the window, multipliers,
margin, the power check's aim and the cluster floor are this file's;
`--d3-large` takes no options; this file states every verdict, the
prediction, both commands and the power output. Broken on purpose ten ways
(a name dropped or reordered here, the margin, window, cluster floor, arm A
or run 1's names changed in the code, an option passed to `--d3-large`, a
placeholder left, the prediction removed), it failed every time.
`option_backtest.py --d3-large-power` is not run in CI (about six
minutes); its output above is the committed code's.

## The population (fixed)

<!-- population -->
```
GOOGL, ORCL, CRM, ADBE, NOW, PANW, ACN, ANET, CRWD, FTNT, AVGO, AMD, QCOM,
MU, LRCX, ADI, ON, NFLX, CMCSA, T, TMUS, VZ, TGT, ABNB, CMG, ROST, TJX, WMT,
PG, KO, PEP, MDLZ, MO, PM, JPM, BAC, GS, MS, V, MA, AXP, BLK, C, CME, SCHW,
SPGI, WFC, CAT, DE, HON, GE, BA, UNP, UPS, CSX, FDX, LMT, RTX, UNH, JNJ,
LLY, ABBV, MRK, PFE, TMO, ABT, AMGN, BMY, CVS, SYK, XOM, CVX, COP, SLB,
MPC, OXY, PSX
```
