# Tranche C — reserved 2026-09-15, before the setup-case buckets existed

## Why this exists

Every ticker this project had ever touched was spent. Contaminated set: 19
names burned by the Aug 2026 parameter sweep and the live test. Tranche A:
spent 2026-09-11 on the RVOL confirmation. Tranche B: spent de facto by live
trading, because the RS ranker kept selecting its names and the scanner kept
alerting on them.

That left nothing to confirm anything on. The setup-case work
(`setup_cases.py`) is deliberately run on already-burned names, because
looking at winners and losers to *find* structure is in-sample by
construction. But the buckets that come out of it then have nowhere to be
tested. Without a fresh reservation, stage 3 scores the buckets on the same
trades that produced them, and the honest write-up reads "real-looking,
unconfirmable" — which is where every other line in this project has ended.

So this tranche is reserved **now**, before a single setup case has been
looked at and before any bucket rule has been written down. The commit
timestamp is the evidence: the names were fixed before the hypothesis existed,
so they cannot have been chosen to suit it.

## The names (20)

    MRVL  NXPI  DDOG  SNOW  SHOP  LULU  WDAY  TEAM  UBER  ICE
    ISRG  VRTX  ETN   BKNG  COF   REGN  GILD  CL    XEL   DUK

Liquid, optionable, spread across sectors and across realised volatility —
high-beta software and semis at one end, utilities and staples at the other.
That spread is deliberate: a bucket rule that only works on 40-vol names is a
different claim from one that works everywhere, and a homogeneous holdout
cannot tell those apart.

## Four names were dropped from the first draft

- **ABNB, AMGN, SCHW** — `universe_history/` shows the live weekly scan
  selected them on 2026-09-06 and 2026-09-13. Their charts had been looked at.
  Not a backtest, and the contact is two weeks old against a ten-year window,
  but "barely contaminated" is exactly the argument that erodes a holdout one
  name at a time.
- **SO** — dropped as a *symbol*, not as a company. `SO` matches ordinary
  uppercase prose, so every future contamination grep on this tranche would
  return a false positive, and a check that cries wolf is a check that gets
  ignored. Replaced by XEL, same sector.

All 20 survivors were re-screened against the contaminated set, tranches A and
B, every `universe_history` snapshot, and a whole-repo grep. None appears
anywhere except in its own definition.

## Seven names came out of the live trading pool

`check_universe_not_spending_reserved()` went red the moment the tranche was
written, naming BKNG, CL, ETN, GILD, ISRG, MRVL and NXPI as present in
`universe.CANDIDATE_POOL`. They were removed. The pool goes 90 → 83.

This is a real cost paid in live opportunity, and it is the cost that was NOT
paid for tranche B — which is why tranche B no longer exists as research
capital. The guard did its job; the failure mode it describes is not
hypothetical, it already happened once.

## The rule for spending it

One shot. Before any tranche C data is fetched:

1. The bucket definitions must be written down, committed, and fixed — every
   threshold numeric, no "roughly", no tuning afterwards.
2. The pass bar must be pre-registered: what result counts as the buckets
   working, and what result kills them.
3. Then `python data_reservation.py --spend C --purpose "..."`.

A second, different hypothesis on tranche C is refused by `check_clean()` and
that refusal is not to be worked around. If the buckets fail here, they failed
— that is the whole point of having reserved anything.

## Status

    Clean shots remaining: 1 (C)
