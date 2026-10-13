# Two checks on real data, run 1: the engines agree; the pullback rule fires about four times a week

**Run by the owner 2026-10-13 on the laptop**, `python engine_differential.py`
and `python pullback_arrival.py`, raw outputs `engine_differential_run1.txt`
and `pullback_arrival_run1.txt`. Both are checks of code and plumbing on
real bars; neither is a result about a strategy, and the second is built
so it cannot be one.

## The engine differential

271 daily frames frozen in the laptop's bar cache (every ticker and window
any backtest has ever read, raw and adjusted variants, 3 to 15 years, and
two volatility indices), 40 random long or short geometries each, through
`backtest.simulate_trade` (every committed result) and
`forward_log.resolve_signal` (every forward outcome), fingerprint
`v2-ef3050fc3ffb9e04`.

| compared | gapped fills, void in both | unsettled in both | disagreements at 5e-5 R |
|---|---|---|---|
| 10,569 | 212 | 59 | **0** |

The two engines return the same outcome and the same R, to the four
decimals the grader records, on every real path drawn, including the 212
where the next open gapped through a level and both refused the fill. The
prose claim in both modules that "the two engines must not disagree on
what a stop costs" is now a measurement on data, not an assertion.

## The pullback rule's arrival rate

83 names (the candidate pool; the watchlist's eight are all in it), the
last 252 sessions ending 2026-10-12, three years of raw bars frozen under
fingerprint `v2-e2805739bd13777d`. The rule was evaluated on every bar's
own prefix, exactly as the scanner sees one settled bar at a time; nothing
after a signal bar was read.

| bearish base setups | of which below the 200-SMA (refused) | zero-risk refusals | pullback signal bars | episodes |
|---|---|---|---|---|
| 3,287 | 2,456 (75 %) | 8 | 823 | 203 |

- **0.81 episodes a session**, 3.27 signal bars a session. At that rate
  **349 episodes take about 433 sessions, 20.6 months**: slower than the
  pre-registration's twelve-to-eighteen-month estimate, which assumed about
  half of bearish setups sit above their 200-day average. Three quarters do
  not. The estimate is corrected by a dated note on the document; nothing
  about the rule or the bar changes.
- 193 of 203 episodes fell in a Bull SPY regime, 10 in Bear: the window is
  almost entirely one regime, which is what the forward record will inherit
  if the market does not turn. A Bear-subset reading is not reachable from
  the last year.
- Arrival is steady across months (9 to 28 episodes a month) and spread
  across names (the most frequent, AVGO and CVX, eight each). No name
  dominates, so the episode count is not a few names' noise.
- Thirteen names never fired (ABT, ACN, ADBE, CMCSA, CMG, CRM, FTNT, KO,
  MU, NOW, PG, SPGI, SYK, T, TGT, TMUS, V): either no bearish base setup
  in the year or every one of them below the average.

## What follows

Nothing to decide. The engines are consistent on data; the pullback bucket
records from the next scan and its reading is about twenty months away at
the measured rate, which BACKLOG 31 now says. If the owner wants the
reading sooner, the only honest levers are more names (the pool is the
universe's reserved-safe list and cannot grow without a reservation
decision) or a smaller detectable effect, which is a different
pre-registration.
