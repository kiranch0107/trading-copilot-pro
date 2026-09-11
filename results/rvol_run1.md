# RVOL: the first positive dose-response in this project — and it still fails

**Run 2026-09-11, `python rvol_retest.py`, 12 tickers / 10 years, 3,406 trades,
exit 1 — NOT ESTABLISHED.**

| bucket | RVOL range | mean | trades | exp R | p | detectable | Holm | n≥floor |
|---|---|---|---|---|---|---|---|---|
| 1 | 0.70–0.81 | 0.76 | 682 | −0.005 | 0.9376 | 0.170 | no | yes |
| 2 | 0.82–0.92 | 0.87 | 681 | −0.012 | 0.8397 | 0.170 | no | yes |
| 3 | 0.92–1.05 | 0.99 | 681 | +0.075 | 0.2601 | 0.186 | no | yes |
| 4 | 1.05–1.31 | 1.16 | 681 | +0.107 | 0.1031 | 0.184 | no | yes |
| 5 | 1.31–5.78 | 1.76 | 681 | **+0.144** | 0.0315 | 0.188 | no | yes |

**Spearman ρ = +0.90** against a +0.6 bar.

## What is new here

**This is the first hypothesis in the project whose dose-response passed.** ADX
was ρ −0.70 — backwards. PEAD was ρ −0.30 — scrambled. RVOL is **+0.90**, and
nearly monotone: the only inversion is between buckets 1 and 2, which are
−0.005 and −0.012, i.e. both indistinguishable from zero.

The mechanism predicted exactly this shape before the run: *volume proxies
information arrival, so the edge should grow with relative volume rather than
appear in one bucket.* It graded.

## And it still fails, on power rather than on sign

No bucket survives Holm. Bucket 5's p = 0.0315 needs p ≤ 0.010 at its rank
across five comparisons. It is also under its own detection threshold: observed
+0.144 against 0.188 detectable.

**Every clause fails in the same direction — underpowered, not absent.** That is
a materially different failure from ADX, where the ordering itself was wrong.

## Clause 4 — the live gate

```
RVOL >= 1.2 :   897 trades, +0.170 R
RVOL <  1.2 : 2,509 trades, +0.023 R
difference  : +0.147 R    95% CI [+0.015, +0.280]
detectable  : 0.160 R
```

The CI clears zero. The difference does **not** clear the detection threshold —
by **0.013 R**. So `volume_mult = 1.2` is **not established** as earning its
place, and equally **not refuted**. Unlike ADX, the sign is positive: the
high-volume half did better, not worse. There is no case here for deleting the
gate, and none yet for trusting it.

## A BUG IN THIS RUN'S OUTPUT — the reported p was wrong

The run printed `95% CI [+0.015, +0.280]` beside `p = 0.0676`. **Both cannot be
true**: a 95% CI that clears zero means p < 0.05.

`live_gate()` built the CI from a Welch standard error but computed p from a
pooled sd at `min(n_hi, n_lo)` — which on an 897 / 2,509 split discards the
larger group's precision entirely. Two numbers describing one quantity,
computed two ways, free to contradict. The same defect as the "spread exceeded
15% — tightest was 13.3%" message, in statistical clothing.

**Corrected: p = 0.0297**, not 0.0676.

**The verdict is unchanged** — clause 4 fails on the detection threshold either
way — but the statistic printed was misleading and is now fixed.

### The guard for it was itself decorative, twice over

The first CI/p agreement check drew random samples at a fixed shift. Every draw
landed far from the boundary, so CI and p agreed under the *buggy* formula too,
and reverting the bug **did not fail the suite**. A guard that cannot reach the
contradiction zone is not a guard.

The fixture is now **constructed** rather than sampled, and pinned to the
observed live case (897 / 2,509, sd 1.6, diff +0.147) where the correct p is
0.018 and the buggy one 0.052. Re-falsified: restoring the bug now fails with
*"CI and p disagree at n=897/2509 ... They describe one quantity and must come
from one standard error."*

That is the **third** dead guard found today — after the equal-count bucketing
assertion that crashed before it could fire, and `adx_retest`'s `main()` that no
test ever called.

## What would settle it

Clause 4 needs the smaller group at roughly **1,060 trades** rather than 897 —
about **18% more data** — to detect +0.147 R. The buckets need roughly 1,160
each, or ~5,800 trades total.

**Nothing here is re-cuttable.** The threshold is fixed at the production value,
the buckets are equal-count, and the bar was pre-registered. The only honest
route to more power is more independent data, not another slice of this data.

**Tranche A is the obvious candidate and this document does not spend it.** The
pre-registration says tranche A confirms a pass and this is not a pass. Whether
"add clean data to a fixed, underpowered, pre-registered test" counts as
confirming or as fishing is a judgment call, and it belongs to the account
owner, made explicitly — not slipped through by a session that wanted a better
number.
