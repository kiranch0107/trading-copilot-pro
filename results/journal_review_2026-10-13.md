# Journal review, 2026-10-13: what the trades say we do right and wrong

**Owner's question:** review the trade journal including skipped signals;
what are we doing right versus wrong. **Read from `trade_journal.json` (13
rows), `open_positions.json` (2), `skipped_signals.json` (15),
`alert_history.json` (0) and the forward record, as committed on main.**
Nothing here is a statistical result: thirteen trades cannot be one. It is
a reading of behaviour against the rules this project has measured.

## The trades

| trade | source | mode | cost | % of $5k | DTE at log | held | premium R | P&L |
|---|---|---|---|---|---|---|---|---|
| NVDA 230C | signal | live | $136 | 2.7 | 8 | 0 | +0.69 | +$94 |
| SPY 785C (8/21) | signal | live | $109 | 2.2 | 6 | 0 | +1.31 | +$143 |
| NVDA 220C | signal | live | $48 | 1.0 | logged after expiry | 0 | +0.23 | +$11 |
| SPY 785C (8/31) | signal | live | $106 | 2.1 | 10 | 0 | −0.49 | −$52 |
| TGT 170C | signal | live | $225 | 4.5 | 23 | 1 | +0.53 | +$120 |
| AAPL 330C | signal | paper | $625 | 12.5 | 14 | 4 | −0.72 | −$448 |
| NVDA 240C | signal | live | $229 | 4.6 | 16 | 1 | −0.73 | −$167 |
| NKE 40P | signal | paper | $232 | 4.6 | 15 | 8 | +0.36 | +$83 |
| NKE 35P | signal | live | $94 | 1.9 | 20 | 10 | +0.04 | +$4 |
| AAPL 360C | discretionary | live | $147 | 2.9 | 24 | 1 | +0.71 | +$105 |
| SPY 790C | discretionary | live | $190 | 3.8 | 16 | 0 | −0.55 | −$105 |
| AAPL 355C | discretionary | live | $111 | 2.2 | 15 | 19, to expiry | −1.00 | −$111 |
| ZZZ | none | none | test row | | | | | |
| **open** NVDA 250C | discretionary | live | $91 | 1.8 | 16 | 13 so far | exit-signalled: STOP | |
| **open** PFE 29C | discretionary | live | $43 | 0.9 | 15 | 12 so far | exit-signalled: STOP | |

Live, closed: 11 rows, +$42 net, 5 wins and 4 losses, profit factor 1.25.
By source: the **system's seven signal trades made +$153; the owner's three
discretionary trades lost $111**, and the two open discretionary positions
are both at their stop with three sessions to expiry.

("held" and "DTE at log" are read from the journal's own timestamps, which
are LOG times; see "wrong", item 3.)

## Right

1. **Size.** Every live trade was one or two contracts, $43 to $232, one
   to five percent of the account. The one oversized trade (12.5 %) was
   paper. The worst live loss was $167. This is the single most important
   thing in the file and it was done consistently.
2. **One skip rule, applied every time.** All fifteen skipped alerts carry
   the same reason, "contract too expensive for my risk rule". A rule that
   is applied uniformly can be evaluated; one that bends cannot.
3. **Honest bookkeeping of outcomes.** Paper and live are separated as
   modes, not notes; the expired AAPL call was logged at −100 % rather than
   forgotten; exits carry a note.
4. **The system's trades beat the owner's.** Seven signal-sourced live
   trades net positive, three discretionary ones net negative, two more
   discretionary ones at their stop. In a bull regime the signal's long
   calls caught beta; the discretionary entries did not even do that. The
   logic review said discretionary short-horizon timing has no evidence;
   thirteen trades agree in miniature.

## Wrong

1. **These are not the trades that were measured.** The option structure
   this repo tested and recorded (`option_backtest`, `risk_params`) is
   **21 to 45 days to expiry, take profit at +200 %, stop at −50 %, exit at
   7 days to expiry**. The journal: ten of thirteen were entered under 21
   days, six were weeklies at 6 to 16 days; every win was taken between
   +23 % and +131 %, never +200 %; three losses ran to −72 %, −73 % and
   −100 %, past the −50 % stop; one call was held nineteen days into
   expiry. So the record on file is the record of an **untested**
   structure: short-dated, early profit, late stop. The measured 27.7 %
   win rate at +200 % does not describe it and nothing else does. Whatever
   its expectancy is, it is unknown, and thirteen trades will not tell us.
2. **The risk rule selects the worst contracts.** Five percent of $5,000
   is $250 a contract. On NVDA, MSFT, QQQ or META a 21-to-45-day
   near-the-money call costs several times that, so the rule skips them
   (that is all fifteen skips) and admits only cheap, short-dated,
   far-from-the-money contracts: exactly the retail pattern Bryzgalova,
   Pavlova and Sikorskaya measured as losing on average at 12.6 % spreads.
   The budget rule and the tested DTE window are **incompatible at this
   account size on these names**. That is a fact about the account, not a
   fault of the rule, and it has to be faced: either trade the tested
   structure only where it is affordable (the PFE and NKE tier), or accept
   that the tested structure cannot be traded here.
3. **The journal cannot be read for timing.** `date` is the time the row
   was logged, not the time the trade was opened: a row logged 19 August
   says "sold on 8/4" in its note, another logged 14 August says "closed on
   8/13", and ten rows show zero days held. Holding time and days to
   expiry at entry are therefore unknowable from the record. The app has
   the signal-date field since BACKLOG 28; no row uses it.
4. **No journal trade is attached to the forward record.** `signal_key`
   is empty on all thirteen rows, and an attachment dry run on a copy of
   the log matches nothing. After four weeks the record's premium-basis
   reading has zero rows: the one measurement that would say what the
   owner's option trades on the system's signals return has no data.
5. **The app's alert history is empty.** `alert_history.json` holds zero
   rows, so "which alerts did I act on, which did I skip, which did I
   ignore" is unanswerable. The skip log stopped on 31 August, not because
   skipping stopped but because the HQ tier stopped alerting (0 of 46
   signals since); the skip log is dormant, not informative.
6. **Losers are held to expiry.** AAPL 355C went to zero over nineteen
   days; NVDA 250C and PFE 29C are exit-signalled at their stops and are
   to be "logged on the 16th", which is their expiry. The exit monitor
   fired; the exits did not happen. A stop that is not taken is a −100 %
   stop.
7. **A test row sits in the production journal** (ZZZ, zero risk, mode
   none). It is excluded from the live statistics by its mode, but it is
   in the file.

## What follows, as decisions for the owner

1. **Trade the measured structure or do not trade.** 21 to 45 DTE, +200 %
   target, −50 % stop, out at 7 DTE, on signals only. If that is
   unaffordable on a name at this account size, the name is not tradeable
   here; the skip rule should say so in those words.
2. **Take the two stops now**, or log, in the journal note, that they were
   held to expiry by choice. Either is honest; silence is not.
3. **Stop discretionary option entries**, or move them to Phase 2's paper
   record where they are graded against random entry.
4. **Fix the journal going forward:** log the trade time, not the log
   time; enter the signal date so the row attaches to the record; delete
   the test row. Three fields, and the premium-basis reading starts.
5. The app's alert history should record what the scanner sent; that is a
   small change on the app side and belongs in BACKLOG.

None of this changes a pre-registration. It is the difference between
the strategy that was tested and the one that was traded, which is the
same gap the logic review found at the level of the signal.
