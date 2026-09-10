# Post-earnings announcement drift: not present in this sample

**Run 2026-09-10, `python pead_study.py`, 50 tickers / 8 years, exit 1 — NOT ESTABLISHED.**

953 events collected; 382 in the extreme quintile pair that forms the spread.
Hold 60 sessions from the first tradeable open, market-adjusted vs SPY, sorted
on SUE scaled by prior quarters only.

## The grading is the result, and it is scrambled

| quintile | events | mean SUE | abnormal % |
|---|---|---|---|
| 1 | 191 | −0.60 | **+1.63** |
| 2 | 190 | +0.25 | +0.98 |
| 3 | 191 | +0.56 | +1.09 |
| 4 | 190 | +0.97 | +0.94 |
| 5 | 191 | **+2.48** | +1.31 |

**Top minus bottom: −0.16%** over 60 sessions.
Clustered 95% CI [−1.61, +1.29] across 20 earnings-season cohorts.
Spearman ρ = **−0.30** against a +0.6 bar.

The *worst* surprises earned the *best* subsequent return. The best surprises
came fourth. If information diffused slowly after an announcement, this column
would grade with the size of the surprise. It does not grade at all.

## The number that looks like a signal and is not

Every quintile is positive, averaging **+1.19%** market-adjusted. That is not
drift, and reading it as drift would be the error this study was built to avoid.

Market-adjusted returns centre on zero when nothing is there. A uniform +1.1%
across *all five* buckets is 50 of today's mega-caps outperforming SPY over
2021–2026 — survivorship plus the mega-cap era — appearing equally in every
bucket regardless of surprise. It is a property of the universe, not of earnings.

The long-short spread nets exactly that out. **The spread is −0.16%.**

## Power, stated honestly

At 382 paired events the smallest detectable effect is **2.25%**, and modern
PEAD estimates run 2–3% over 60 days. So this test could only just have seen a
live effect, and on its own that would make the result INCONCLUSIVE rather than
negative — the same trap the 83-cycle spread backtest fell into.

It does not resolve that way here, for a specific reason: **both the point
estimate and the grading point the wrong way.** An underpowered test concealing a
real +2% effect would still show a positive spread and positive ρ, merely without
significance. This shows −0.16% and ρ −0.30. That is evidence against the effect
in this sample, not an inability to see it.

## A weakness in this study's own bar, recorded rather than defended

`MIN_EVENTS = 400` was intended as "enough events" but is applied to the
**paired extreme-quintile sample** (382), not the 953 events collected. It fired
on a near-miss of an ambiguously specified threshold. That clause should be
stated precisely — against the paired sample, or against the total, with a
number chosen for whichever — **before** the next run, not adjusted now.

It changes nothing here. The verdict rests on ρ = −0.30 and a negative spread,
neither of which is close.

## What would settle it, and what would not

Terciles instead of quintiles would put ~635 events in the pair, and more
tickers would add more. That is a legitimate power improvement **only if
specified in advance**. Re-cutting this same data until a bucket passes is the
ADX-35 mistake, and the negative grading means there is no positive result
hiding here to rescue.

## Where this leaves the research programme

Three mechanisms tested with adequate design; one real, none tradeable at this
scale:

| hypothesis | verdict |
|---|---|
| directional signal (indicators) | no edge, well powered — trustworthy no |
| ADX filter | **refuted with a backwards dose-response**, ρ −0.70 |
| volatility risk premium | **real**, +3.63 vol points, 11/11 years |
| VRP via static index spreads | fails on drawdown and loses to buy-and-hold |
| post-earnings drift | not present, ρ −0.30 |

The pattern is consistent and worth naming: the only hypothesis that survived
started from a **mechanism** (people overpay for crash insurance). The ones that
started from a **pattern** — indicator crossings, an ADX threshold — produced
nothing. PEAD had a mechanism and forty years of literature, and still came back
empty on retail-accessible data over a five-year mega-cap window, which is itself
informative about how much of the published effect survives into the present.
