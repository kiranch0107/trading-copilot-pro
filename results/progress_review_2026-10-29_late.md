# Progress review — 2026-10-29 (late): the live position's exit rules against the measured structure

Owner's request, the second this evening: "Review again thoroughly and let
me know for improvements and pending items." Read against `main` at
4f16ac4 (PR #176). The night review of an hour earlier
(`progress_review_2026-10-29.md`, BACKLOG 41) covered the record, the
unattended system and the day's merges, and every finding it made (G1–G4)
is now built. This pass goes where today's reviews did not: the exit rules
the live position actually carries against the structure that was
measured, the app's form defaults, secrets and branch hygiene, and the
hand-off section. Nothing was fetched; no engine, rule or threshold
changed.

## What has changed since the night review

Four merges (#173–#176): the H1/H1′ and control readings re-anchored at
the amendment F cut (21 episodes read where 3 were), the spread report's
open-cycle ratio line, the "none settled" wording, two cosmetic lines.
The record itself is unchanged: no scan has run since 16:20 ET.

## Findings

### H1. The live position runs the thesis exit; the alert text, the app default and the structure check say or allow otherwise (owner decision)

The measured option structure is TP +200 % / SL −50 % / out at 7 DTE,
entered at 21–45 DTE (`risk_params.MEASURED_OPTION_STRUCTURE`). The run
that measured it, `option_backtest_default_run3.txt`, exited 65 of 395
trades on the THESIS rule (the underlying closing through EMA20), because
`option_backtest`'s command line runs with `use_thesis` on unless
`--no-thesis` is passed; `thesis_exit_preregistration.md` names thesis ON
as production and run 3's verdict is KEEP. So the measured structure ran
WITH the thesis exit. Against that:

- the scanner's alert text ends "Test rules: TP +200% · SL −50% · exit at
  7 DTE · thesis OFF";
- the app's "Thesis invalidation" checkbox defaults to off;
- `option_structure_violation()` checks only tp, sl and dte_exit, so the
  journal accepts a signal trade with the thesis exit on or off;
- the NOW position (opened 10-29 11:21 ET from the alert) carries
  `invalidate_ema: true`, which matches the measurement, because the owner
  ticked it.

Three artefacts say OFF, the measurement and the pre-registration say ON,
and the journal does not ask. **Options:** (a) the thesis exit is part of
the structure: add it to `MEASURED_OPTION_STRUCTURE`, have the violation
check require it on a signal trade, flip the alert text and the app
default to ON, and say so in `STRUCTURE_REASON`; (b) it is not part of the
structure: keep the check silent, change the alert text and the app
default to say "thesis as logged", and record the choice on each row. (a)
is what was measured; the thesis test found the rule undetectable either
way (|delta| 0.34 % under a 1.95 % MDE), so (a) costs nothing measured
and removes a contradiction the next trade would meet. Recommended: (a).

### H2. The HOLD rule on the live position cannot bind (minor)

The NOW row also carries `max_hold_bars: 30`, the app's sidebar hold for
the underlying backtest, written into every option position by the form.
Inside the structure a position is out by 7 DTE at the latest, 38 calendar
days after a 45-DTE entry, about 27 sessions, so a 30-session hold never
fires. Harmless; the form could drop the field for signal trades so the
rules block names only what can act. Folds into H1 (a).

### H3. Three hold lengths coexist, each declared where it differs

The forward grader holds 20 bars (`forward_grading_preregistration.md`,
"matches the record's engine"); the OOS lock holds 30 (`oos_validate`'s
frozen config, 2026-09-02); the app's sidebar default is a literal 30 that
feeds only the journal's HOLD rule of H2. Nothing is wrong; the three are
named here so a reader does not mistake them for one number.

### H4. Secrets and branches

`git grep` for GitHub token and Telegram bot-token patterns over the tree
finds nothing. The remote holds 54 branches, most of them `claude/*` and
`local/*` work branches whose commits are on `main`; pruning them is the
owner's, cosmetic, and changes nothing a check reads.

### H5. The hand-off section carried no dated entries for the reads that are due

CLAUDE.md says the next session reads "Owner hand-off" first; it said
"none open as of 2026-10-26" while four dated reads are due (10-30, ~11-06,
11-09/10, 11-30) and the token rotation by 12-16. Added in this commit as
dated entries, to be retired as each lands.

## Improvements, in the order they pay

1. **H1 (a)**, with H2 folded in: one small change across `risk_params`,
   `scanner.compose_alert`, the app's checkbox default and the structure
   check, with selftests; a BACKLOG 32 decision-3 note. Needs the owner's
   word because it adds a refusal to the journal.
2. Nothing else. G1–G4 are built; the rest is on the clock.

## Pending items, in the order they block things

1. 2026-10-30 11:07 ET: the first revision rows, the key-based alert
   suppression against the seven delivered alerts, whether CME alerts.
2. H1 (a) with a "go".
3. ~2026-11-06: a week of revision rows, then the post-close slot decision.
4. 2026-11-09 / 11-10: the first IV premium rows.
5. 2026-11-30: the first spread settlements; the skew read at 6 graded
   cycles per arm (~April 2027).
6. 2026-12-16: token rotation (app 2027-01-05, dispatch 2027-01-06).
7. The forward clocks as the `clocks:` line prints them: pool 32 of 349,
   basket 0 of 52, spread 0 of 12 per arm, IV premium 0 of 128.
