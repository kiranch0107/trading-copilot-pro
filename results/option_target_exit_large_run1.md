# The larger D3, run 1 — sell the option when the underlying reaches the signal's target? NOT ESTABLISHED; EQUIVALENT within ±5 points of premium

**Run by the owner 2026-11-04 in the Codespace, the one command of
`results/option_target_exit_large_preregistration.md`, main @ `cc92640`
(the pre-registration merged as PR #199).** Raw output:
`option_target_exit_large_run1.txt`. It prints `Config : live`, all 77
names covered (`DATA COVERAGE: 77 of 77`), the fingerprint
`v2-312f2f28a4920341` (77 series, 44 cached / 33 fetched), the engine (the
six fixes, the default) and the window **2017-04-17 .. 2026-11-03** (signals
after each name's warm-up). Priced under IV: 1.15x realised (the primary)
and IV: 1.0x realised, on the same 2,817 entries.

> **DECIDED 2026-11-04 (owner: "go with the target exit").** On this
> reading the target exit joins the live structure on grounds of
> coherence: `MEASURED_OPTION_STRUCTURE["target_exit"]`, the journal
> refuses a signal trade without the alert's target, the exit monitor
> sells on a close at or beyond it (`TARGET_U`, after STOP and before the
> premium take-profit), the app's three trade forms ask for it, and the
> alert's rules line names it. The bound this run set stands: at most 0.9
> points of premium worse, most likely about 1 better. Nothing measured
> here changes.

## Verdict, by the pre-registered rule: NOT ESTABLISHED; EQUIVALENT

| | IV x1.15 (primary) | IV x1.00 |
|---|---:|---:|
| pairs (settled in both arms) | 2,817 | 2,817 |
| pairs whose two arms differ | 449 | 375 |
| mean A (the structure as measured), % of premium | −2.85 | +2.87 |
| mean B (A + sell at the underlying's target) | −1.62 | +3.89 |
| **B − A** | **+1.23** | **+1.02** |
| sd / SE (the widest: month) | 40.9 / 0.98 | 39.9 / 0.96 |
| 95 % CI | [−0.68, +3.14] | [−0.87, +2.91] |
| 90 % CI (EQUIVALENT if inside ±5) | [−0.37, +2.83] | [−0.57, +2.60] |
| half-width (aim 3.5) / MDE at 80 % | 1.91 / 2.73 | 1.89 / 2.70 |
| clusters: week / month / episode | 439 / 110 / 1,418 | 439 / 110 / 1,462 |
| DEFF: week / month / episode | 1.39 / 1.60 / 1.07 | 1.33 / 1.64 / 1.17 |
| B's exits | STOP 1,373, TARGET_U 588, TIME 419, THESIS 416, TARGET 21 | STOP 1,479, TARGET_U 543, TIME 384, THESIS 332, TARGET 79 |

- **Not ADOPT, not REJECT:** both 95 % CIs include zero.
- **EQUIVALENT:** both 90 % CIs lie inside ±5 points of premium.
- **Not UNDERPOWERED:** every clustering has at least 110 groups (floor 30).
- The design hit its aim with room to spare: a half-width of 1.9 against
  3.5, and an MDE of 2.7 points.

**What it says.** Selling the option when the stock closes at the alert's
own target costs nothing measurable against holding for the +200 %
take-profit:
- The difference is about **+1 point of premium in favour of selling at
  the target**, at both IV assumptions.
- The data rule out the target exit costing more than **0.7–0.9 points**
  (the lower ends of the 95 % CIs). They also rule out it gaining more
  than about 3.
- Holding past the target, waiting for the stock to overshoot, earns
  nothing measurable here.

**The prediction held:** "B − A between −3 and +2 points at both
multipliers, and NOT ESTABLISHED, EQUIVALENT."

## What this means for the live structure (the pre-registered reading)

The structure stays as measured. By the pre-registration, with this
verdict "tying the option's exit to the alert's target on grounds of
coherence is the owner's call, made with that bound known". The bound is
now known:
- **At most 0.9 points worse** (95 %).
- **Most likely about 1 point better.**
- **Changes the outcome on about one trade in seven** (375–449 of 2,817).

## Against the power check

| | the run | the power check |
|---|---|---|
| pairs | 2,817 (3.7 a name-year) | ~3,200 projected (3,700–4,200 synthetic) |
| sd per pair | 40–41 % of premium | 41–46 (run 1: 36–41) |
| month DEFF | 1.6 | 1.3–7.3 |

The run drew fewer pairs than projected. Its pairs moved together less than
most of the synthetic worlds, so the CI came out narrower than the aim
anyway.

## Two things the document did not anticipate

- **44 of the 77 series came from the Codespace's bar cache**, not a fresh
  fetch:
  - **Which:** the 12 names of the 591-trade validation and the 32 of
    tranche B, cached by earlier 10-year research runs up to 55 days
    before.
  - **Effect:** their bars end about mid-September 2026 rather than
    2026-11-03, about two months short of the other 33 out of ten years.
  - **Why it stands:** the rule (`backtest.download(…, 10)`) reads the
    never-expiring cache by design, and no choice was made after seeing a
    number.
  - **The other 33** were fetched by this run, which also shows it was the
    first run to load them: an earlier, partial attempt would have cached
    some of them.
- **33 names dropped one bar dated 2026-11-04**, the session still forming
  when the run was made, as the engine always does.

## Also visible, outside D3's question (descriptive; no test was registered on it)

**Arm A, the live option structure, averages −2.85 % of premium at IV
x1.15 and +2.87 % at x1.00** on these 77 names over the window.
- That is about break-even either way, after the 5 % round trip.
- On run 1's seven large tech names over five years the same structure
  averaged +0.07 % and +10.0 %.
- The structure's measured numbers (`option_backtest_default_run3`,
  `OPT_WIN_RATE`) come from those seven names.

The run prints no CI for arm A, and the live pool adds filters this engine
does not model (the ranking and top five, the sector cap, the earnings
blackout and D1, the fill gate). So this is a question, not a finding:
**does the option structure carry an edge on the names the live system
actually alerts on, or did the seven-name measurement flatter it?** Which
IV multiplier is real is what the single-name IV premium record
(`iv_snapshot`, first reading 2027-04-08) will say; at x1.15 arm A here
loses about 3 % of premium a trade.

## What is not done

- **Nothing in the live system changes.** Only ADOPT would have, and the
  verdict is NOT ESTABLISHED.
- **Whether to tie the option's exit to the alert's target** is the owner's
  call (see above). If taken, it is built as one change to
  `MEASURED_OPTION_STRUCTURE`, the exit monitor and the journal, as the
  thesis exit was. *(Taken the same day; see the note at the top.)*
- **No re-run.** This file is the run; run 1's 149 pairs stay descriptive
  and are not pooled with it.
