# Run 3 — the same arms on one calendar: FIVE of eight arms clear the bar

**Run by the owner 2026-10-28, `python spread_backtest.py --skew-sweep
--years 10`, branch `claude/trading-code-review-wt48q8` @ `c1de652` (BACKLOG
36 group B).** Raw output: `spread_backtest_run3.txt`. Same eight arms, same
four skews, same pre-registered bar as runs 1 and 2 (`spread_backtest.py`:
CI clears zero AND max drawdown ≤ 25 % AND worst cycle ≤ 25 % of the
account, at every skew, AND the arm beats the capital-matched hold).

**Why it was re-run.** The engine priced every cycle for `dte`/365 of a year
of time value and then held it `dte` SESSIONS: a "30-day" cycle collected 30
days of premium and bore 42 calendar days of path. The priced maturity and
the holding period disagreed on every cycle, in the seller's disfavour. Group
B settles a cycle on the first session on or after entry + 30 calendar days
and prices it for the days it actually lives (30 to 32).

## Verdict: FIVE ARMS PASS, THREE FAIL. Run 2 said no arm cleared.

At skew 20 (the middle of the range; the four skews differ by hundredths):

| arm | cycles | mean %/cycle | 95 % CI | max DD % | worst % | hold % | run 2 mean | verdict |
|---|---:|---:|---|---:|---:|---:|---:|---|
| SPY iron_condor w10 | 120 | +2.22 | [+1.27, +3.18] | 10.1 | −16.9 | +0.16 | −0.65 | PASS |
| SPY iron_condor w5 | 120 | +1.08 | [+0.55, +1.60] | 8.5 | −8.8 | +0.07 | −0.84 | PASS |
| SPY put_spread w10 | 120 | +1.14 | [+0.41, +1.87] | 13.0 | −18.5 | +0.20 | +0.35 | PASS |
| SPY put_spread w5 | 120 | +0.63 | [+0.25, +1.00] | 9.2 | −9.4 | +0.10 | +0.01 | PASS |
| QQQ put_spread w10 | 120 | +1.13 | [+0.28, +1.98] | 16.4 | −18.1 | +0.28 | +0.72 | PASS |
| QQQ put_spread w5 | 120 | +0.52 | [+0.02, +1.02] | 17.1 | −9.0 | +0.14 | +0.22 | FAIL (skew 30 CI touches zero) |
| QQQ iron_condor w10 | 120 | +1.20 | [−0.07, +2.47] | 27.2 | −16.3 | +0.22 | −0.82 | FAIL (CI, drawdown) |
| QQQ iron_condor w5 | 120 | +0.39 | [−0.33, +1.11] | 21.7 | −7.8 | +0.10 | −1.12 | FAIL (CI) |

Every arm improved, the condors by two to three points a cycle, and every
drawdown fell by a factor of three to ten. That is the direction the fix
must move a seller: run 2 charged each cycle 42 days of risk for 30 days of
premium.

## Read it before acting on it

1. **The engine prints no data fingerprint.** `spread_backtest.run()` reads
   SPY, QQQ, ^VIX and ^VXN through `backtest.download()` but, unlike the
   share and option engines, does not print the bar-cache line. Run 2 was
   dated 2026-09-11 and run 3 2026-10-28, both "10 years relative to today",
   so the window moved by seven weeks and whether the bars were the cached
   ones or a refresh is not recorded. The cycle count (83 → 120) is the
   clock, not the data: 3,650 days / 30.4 days ≈ 120. The owner's
   `python bar_cache.py --info` can settle the data question after the
   fact; the print is added in this change so run 4 will say.
2. **Two looks became three.** Nothing was searched over: the arms, skews
   and bar are the ones written down in run 1. But the same ten years have
   now been read three times, and a verdict that flips on an engine fix is
   a verdict about the engine first.
3. **The bar is the one `spread_backtest.py` declares; no pre-registration
   file says what a PASS commits anyone to.** BACKLOG 13 recorded that run
   1 could detect +18 %/yr; the CIs here are the same design on 120 cycles.
   What, if anything, follows from five arms clearing it is the owner's
   decision, not this file's.
4. **Modelled, not quoted.** Entry IV is the observed ^VIX / ^VXN close
   applied through Black-Scholes with a flat or linear skew; the bid-ask is
   `risk_params`' cap charged on each leg. A real chain's skew, the
   weekend-expiry settlement and early assignment are not in the model.
5. **Run 2's clause-5 critique stands.** `hold %` is capital-matched (the
   dollars the spread ties up, in the index); the comparison that decides
   anything for a $5,000 account is the whole account in the index, which
   the file does not run. SPY iron_condor w10 at +2.22 %/cycle and 12
   cycles a year is roughly +27 %/yr on the whole account at a 10 %
   drawdown; SPY buy-and-hold over the same decade is +13.4 %/yr at 34 %
   (`portfolio_replay_run4.txt`). That comparison now favours the arm, on
   the model.

## Prediction scorecard against run 2

| run 2 said | run 3 |
|---|---|
| "FAIL stands, no arm clears" | **wrong on the fixed engine**: five clear |
| clause 2 (drawdown) sinks every arm | **wrong**: only QQQ iron_condor w10 (27.2 %) |
| clause 1 (CI) sinks every arm | **wrong**: three arms' CIs include zero |
| the skew sweep never decides anything | **right**: hundredths again |
| worst cycle never breaches 25 % | **right**: −18.7 % at most |
| cycles/year is 8.3, not 12 | **superseded**: 12 on the calendar clock |

## Not done

No arm is traded, no constant moved, no pre-registration was amended. The
fingerprint print is the only code this file adds.
