# The volatility risk premium, measured

**Run 2026-09-10, `python vrp_check.py`, 10 years, exit 0 — PASS.**

`option_backtest.py` has priced every simulated contract at `iv_mult = 1.15`
since it was written. That constant was never measured. It is now.

## What was measured

`^VIX` close (30 calendar days, annualised %) against `SPY` forward 21-session
realised vol (annualised %), same horizon, matched day by day. 2,491 days
measured, 23 dropped with no forward window yet.

## Result

```
mean premium    : +3.63 vol points
median          : +4.61
95% CI (HAC)    : [+2.40, +4.85]   <- the one the bar uses
95% CI (iid)    : [+3.30, +3.95]   (WRONG here: overlapping windows)
SE hac / iid    : 0.625 / 0.166 (x3.8);  ~119 independent windows in 2491 days
days positive   : 83.7%
5th / 95th pct  : -7.24 / +12.59
worst / best    : -66.53 / +24.68
```

| year | premium | days |
|---|---|---|
| 2016 | +5.66 | 78 |
| 2017 | +4.36 | 251 |
| 2018 | **+0.56** | 251 |
| 2019 | +4.20 | 252 |
| 2020 | +2.60 | 253 |
| 2021 | +6.81 | 252 |
| 2022 | **+1.68** | 251 |
| 2023 | +4.27 | 250 |
| 2024 | +3.19 | 252 |
| 2025 | +3.22 | 250 |
| 2026 | +5.45 | 151 |

Eleven for eleven positive. **The two bolded years are below the 2.0-point
thickness bar this test set for the full sample** — the premium thins in exactly
the years you would most want it (Feb 2018's vol-of-vol event, the 2022 bear).
It never inverted on a yearly basis, but "positive every year" and "thick enough
to trade every year" are not the same claim, and only the first one holds.

## The verdict against the pre-registered bar

All three clauses cleared, and the conservative end of each:

- HAC CI lower bound **+2.40** clears zero, and also clears the 2.0 thickness bar
  on its own — the interval, not just the point estimate.
- 83.7% of days positive against a 70% bar.
- Mean +3.63 against a 2.0 bar.

The overlap correction mattered and is why the CI is worth reading: the HAC SE is
**3.8x** the iid SE, and 2,491 daily observations carry only **~119 independent**
21-session windows. The iid interval [+3.30, +3.95] is the wrong one and is
printed only to show the size of the error.

---

## WHAT THIS DOES NOT ESTABLISH — read before building anything

### 1. The tail is measured on n ~= 1, and the tail is what kills short vol

The five worst stretches are **all within twelve days of each other**:

```
2020-02-19  implied 14.4  realised 80.9  ->  -66.53
2020-02-14  implied 13.7  realised 80.0  ->  -66.36
2020-02-18  implied 14.8  realised 81.0  ->  -66.16
2020-02-20  implied 15.6  realised 81.6  ->  -66.04
2020-02-26  implied 27.6  realised 92.5  ->  -64.96
```

That is not five bad outcomes. It is **one event sampled five times**. Ten years
of history contains roughly one true vol catastrophe (arguably two, counting
Feb 2018). The HAC correction fixes the overlap in the *mean*; nothing fixes the
fact that the distribution's left tail rests on one or two independent events.

**Confidence in the mean does not transfer to confidence in survivability.** The
mean of +3.63 is the average of collecting ~4 points most months and losing 66
in one. The pre-registered bar tested whether the premium *exists*, is *broad*,
and is *thick*. It never tested whether a seller *survives*. That was a gap in
the bar, named here rather than after it mattered.

This is the case for defined risk being non-negotiable rather than a preference,
and for the position-sizing rule being the binding constraint from here on.

### 2. It says the edge is in the SIDE, not in the TIMING

This repo is architecturally a signal scanner: it waits, fires occasionally, and
the mechanical signal it fires on has **no demonstrated edge** (10 years, 1,386
trades, -0.048 R, PF 0.93 — `results/longshort_split.md`).

This measurement says something structurally different: a premium of +3.63 points
that is positive in 83.7% of *all* days and in every one of eleven years is an
argument for being **systematically short index vol**, not for picking moments to
be. Nothing here identifies a better or worse day to sell. A strategy that is
short premium on the index continuously captures the measured quantity; a
strategy that is short premium only when a scanner fires captures an unmeasured
subset of it.

That is a redirection of the whole architecture, not an addition to it, and it
should be decided deliberately rather than absorbed.

### 3. INDEX only, and this is a hard boundary

VIX is SPX implied vol. It says nothing about whether MPC's, CRM's or CRWD's
implied vol is rich. Single names carry earnings and idiosyncratic jump risk the
index diversifies away, and this repo's own history — five stale copies of one
constant, seven fixtures that passed with the thing they tested removed — is the
argument against generalising past what was measured.

### 4. The dollar translation below is indicative, NOT measured

Flat Black-Scholes at a single vol for both legs, SPY 650, 30 DTE, r = 4%,
implied 16.0% against a fair 12.37%:

| short K | long K | credit @ IV | credit @ fair | edge | width | edge/width |
|---|---|---|---|---|---|---|
| 630 | 620 | 1.84 | 1.20 | 0.63 | 10 | 6.33% |
| 618 | 608 | 0.93 | 0.43 | 0.50 | 10 | 4.99% |
| 618 | 598 | 1.40 | 0.58 | 0.82 | 20 | 4.09% |
| 585 | 565 | 0.08 | 0.01 | 0.07 | 20 | 0.36% |

Against this repo's own `MAX_OPTION_SPREAD_PCT = 8.0` on each of two legs, the
618/608 spread nets **+0.40** and the 618/598 nets **+0.74** after crossing. The
edge survives the bid-ask with room, which is the question that killed the long
side.

**But real index puts trade on a skew and this calc has none.** OTM puts carry
higher implied than ATM, and their fair vol is also higher because downside
realised is fatter. VIX is a variance-swap measure, not the ATM vol these
strikes trade at. The skew could move these numbers either way and the far-OTM
row (585/565, edge/width 0.36%) is where it would bite hardest. **Do not size
anything off this table.** It exists to show the edge is the right order of
magnitude to be worth measuring properly, and nothing more.

## What was NOT spent

No reserved data. `data_reservation.check_clean(['SPY','^VIX'])` reports SPY
already contaminated and `^VIX` unknown, so no clean tranche was consumed and
Tranche A remains unspent for the directional question.
