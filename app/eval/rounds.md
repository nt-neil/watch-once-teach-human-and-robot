# Eval rounds (real Meta runs, one variable per round)

GT: eval/gt/m1.json (4 events, from gap-report §2) + runs/desktop-1103/ground_truth.json (7 events, frozen).
GT never enters prompts. Scorer: scripts/eval.py. n=1 per cell unless noted.

## R0 baseline (prompt v2, tester runs 11/3)

| run | kind | steps | hits | merges | kw | halluc | cost |
|---|---|---|---|---|---|---|---|
| web-20261103T111002Z | M1 | 4 | 4/4 (err ≤1000ms) | 0 | washer 0/1 | securing,tighten | $0.0149 |
| web-20261103T115813Z | OBS | 5 | 4/7 (miss GT2,GT5,GT6) | 2 | 2/4 (miss 清除×2) | 篩選,filter | $0.0197 |

## R1 (prompt v3 = v2 + verbatim-text rule d)

- M1 eval-r1-m1: 2 steps, 2/4 hits, merges 2, washer 0/1, halluc 0, $0.0127. REGRESSED on granularity.
- OBS eval-r1-obs: 6 steps but frame ref out of range → validation failed, 0/7, $0.0195. Cut times all off.
- Verdict: d shows no positive effect (M1 granularity worse, OBS failed). Drop d.

## R2 (prompt v4 = v2 + no-merge-opposites rule c)

- M1 eval-r2-m1: 4 steps, cuts near GT (800/3600/7000/9500/13000) but step_003 starts 7001 while citing frame@7000 → 1ms out-of-range → validation failed, $0.0182.
- OBS eval-r2-obs: 6 steps, 4/7 hits (miss GT1,GT2,GT6), merges 0, kw 3/4 (miss 鳳梨酥), halluc 0, $0.0211.
- Root cause of M1 failure: model writes xxx1 boundaries against whole-second frames. Fix in R3, not a c-rule failure.

## R3 (prompt v5 = v4 + whole-second boundaries)

- M1 eval-r3-m1: 4 steps (1.0/4.0/7.0/10.0/13.0s), 4/4 hits (err 1000/500/0/500), merges 0, washer 0/1, halluc 0, $0.0133. MEETS M1 bar.
- OBS eval-r3-obs: 6 steps, 4/7 hits (miss GT1,GT2,GT6), merges 0, kw 2/4, halluc 0, $0.0225. Merges 2→0, halluc 2→0, GT5 newly hit vs R0; GT1 newly missed.
- Verdict: best so far on both clips.

## R4 (v5 + 2fps sampling)

- OBS eval-r4-obs: 6 steps but a cited frame lands exactly on step end (exclusive) → validation failed, $0.0206.
- M1 eval-r4-m1: 4 steps, 4/4 hits, merges 0, washer 0/1, halluc 1 (tighten tail back), $0.0146.
- Verdict: denser sampling adds boundary friction (OBS fails) and no gain (M1 same 4/4 with one more halluc word). Drop (a). v5 stays best.

## Decision

- Default prompt is v5 (rounds R3). v1–v4 remain via --prompt-version.
- Honest limits: every cell is n=1; M1 swung 4/2/fail/4/4 across versions; OBS still misses GT1/GT2/GT6 on timing; washer needs a hint; v4 OBS kw 3/4 vs v5 2/4 is within n=1 noise.
- Iteration spend: 8 paid calls, est $0.1425 (R1 2, R2 2, R3 2, R4 2).
