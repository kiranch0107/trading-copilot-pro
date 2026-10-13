# PRE-REGISTRATION — the owner's own chart reading, on paper, graded by the Phase 1 engine (day-trading screen, Phase 2)

**Written 2026-10-13 at the owner's request ("go ahead with Phase 2"),
and committed BEFORE the first call is logged.** Everything below is
fixed. It may be AMENDED in a dated section at the top only until the
first call is committed. Parent: `day_trading_screen.md`, whose Phase 2
paragraph this document makes exact. Phase 1 (five-minute, hourly and
4-hour) measured no edge in any objective version of the tools; this test
is of the one thing Phase 1 could not codify, the owner's reading.

## The question, and the framing it is run under

Does the owner's discretionary intraday chart reading, logged before
entry and graded by a fixed mechanical rule, make money net of costs and
beat random entries at the same times? The base rate is on file: fewer
than 1 % of day traders profit persistently (Taiwan, every trader
1992–2006); 97 % of those who persist lose (Brazil, 2013–2015). **This
is a test of whether the owner is in that top percent**, and it is run on
paper, for the full sample, with that framing.

## What is held fixed

| item | value |
|---|---|
| the record | `daytrade_paper.jsonl`, an append-only hash chain in the forward log's format, written by ONE module, `daytrade_paper.py`, and never read by the journal, the app or the scanner. A call is a row; an outcome is a row that names its call. The chain's timestamp is the call's time and is set by the code, not typed |
| a call | logged **before entry**, during regular hours and before 15:55 ET, with: ticker; side; **setup name from the Phase 1 list** (`orb`, `vwap_reclaim`, `ema_pullback`, `rsi_divergence`, `fib_pullback`, `trendline_break`) or `pattern` (a named chart pattern, in the note) or `other`; the price on the screen; the planned stop; the planned target; a note; optionally a screenshot path. The code refuses a call whose stop and target are not on the right sides of its price |
| the witness | the owner commits and pushes the log the same day. The commit time is the external witness that the call preceded its outcome; the hash chain is the witness that nothing was edited or removed |
| entry | the OPEN of the first 5-minute bar that begins after the call, plus 5 bps. The owner's price is used for the geometry (risk = price − stop) and is checked against the bar that was trading at the call: a price outside that bar's range, by more than 0.1 %, VOIDs the call |
| exit | stop first, then target, else **flat at the session's last bar**. Gapped levels fill at the open. 5 bps a side. No discretion: how the owner would have managed the exit is not measured, by design |
| bars | Yahoo 5-minute regular-hours bars, `auto_adjust=False`, the same fetch Phase 1 used. The provider serves 60 days; a call must be graded inside that window. A call older than **50 days** with no bars is VOID (`no_bars`) |
| dust | an outcome inside ±0.05 R is a scratch, neither win nor loss (`journal_store.DUST_R`, `forward_log.GRADE_DUST_R`). Mean R uses every settled outcome |
| the null | drift_null's method, as in Phase 1: for each call, twenty fixed-seed random bars in the same session, same side, same stop and target distances, same engine and costs. Each outcome row carries its call's null mean |
| one position | not enforced. The owner may log overlapping calls; calls in one session are one cluster, which is what absorbs their correlation |

## The sample and the reading gate

`power_check.py --sd 1.0`: **349 settled episodes** for an edge of +0.15
R at 80 % power. At two or three calls a session that is four to twelve
months. **Nothing is read before 349 settled episodes.** The report
prints counts (calls, open, settled, void, per setup, sessions, days)
until then and **withholds the mean, the interval, the null and the
verdict**; the code enforces that, not the owner's restraint. There is
no peek flag. A reading taken early would be the 30-trade journal
mistake again.

Void outcomes do not count toward 349. A call that is never committed
does not exist.

## The bar, read ONCE at 349

One pooled test, pre-registered to avoid the multiplicity of six per-setup
tests. The record **clears** only if, net of costs: mean R ≥ +0.15; the
**95 %** session-clustered interval lies above zero; and mean R exceeds
the random null by ≥ +0.15 R. Per-setup means are printed as
information and carry no verdict.

**Phase 3** (live, 1 % risk, inside the settled-cash and day-trade
limits) is reachable only from a cleared bar. A result whose interval
spans zero does not go live, whatever its mean.

## Prediction, so it can be scored

The base rate predicts that the record does not clear: the mean will be
within ±0.10 R of the random null at the same times, and negative net of
costs. If the owner's reading carries anything, it will show as a margin
over the null before it shows as a positive mean, because costs are
inside the bar.

## Enforcement

`daytrade_paper.py log|grade|report|verify`. The selftest proves: a call
is refused outside hours, at or after 15:55, with a setup not on the list,
or with a stop on the wrong side; grading enters at the next bar's open,
voids a price outside its bar, voids a stale call with no bars, writes
one outcome per call, carries a twenty-draw null; the chain verifies
with `forward_log.verify()` and detects an edit; the report withholds the
verdict below 349 and prints it at 349.
`check_daytrade_paper_isolated()` pins the module's constants to this
document, holds the one-writer rule, and keeps the file out of the
journal, the app and the scanner.

## How the owner runs it

Before entering, from the repo on the laptop (the market must be open):

    python daytrade_paper.py log TSLA long orb --price 241.30 --stop 239.80 --target 244.30 --note "weekly high, first pullback held"

Same day, after the close:

    git add daytrade_paper.jsonl && git commit -m "paper calls $(date +%F)" && git push

Weekly, inside the provider's 60-day window:

    python daytrade_paper.py grade && git add daytrade_paper.jsonl && git commit -m "paper grades" && git push
    python daytrade_paper.py report
