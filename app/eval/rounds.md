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

## R5 (v5 dev-full baseline, 6 clips, scripts/run-round.sh)

- M1 eval-r5-m1: 2 steps, 1/4 hits (miss GT2/GT3/GT4), merges 1, kw 0/1, halluc 0, med_err 1000, FAIL.
- L3 eval-r5-l3: validation FAILED (step-004 end 10000 > duration 9600; whole-second overshoot). FAIL, no score.
- W1 eval-r5-w1: 7 steps, 3/4 hits (miss macro1), merges 0, halluc 0, med_err 1000, FAIL (75% < 80%).
- OBS eval-r5-obs: 6 steps, 4/7 hits (miss GT2/GT6/GT7), merges 1, kw 2/4, halluc 0, med_err 583, FAIL.
- S003 eval-r5-s003: 7 steps vs 2 macro GT, 2/2 hits, med_err 250, PASS (extra steps unpenalized by design).
- WIN eval-r5-winter: v5 has no kind gate; forced 1 step on scenery ("uncertain" in text but steps non-empty). kind procedural/non_procedural, FAIL.
- Verdict: 1/6 pass. Same-version rerun swings (M1 4/4→1/4) confirm granularity instability is model-side; direction (e) consistency matters. Whole-second overshoots non-integer durations (L3). video_kind gate justified by winter.
- Round spend: 6 paid calls, in 33604 / out 18693, est $0.1215. Cumulative iteration: 14 calls, est $0.2640.

## R6 (prompt v6 = v5 + video_kind gate, dev full)

- M1 eval-r6-m1: 4 steps, 3/4 hits (miss GT4), merges 0, kw 0/1, halluc 0, kind ok, med_err 500, FAIL.
- L3 eval-r6-l3: 3 steps, 3/4 hits (miss macro4), merges 1, kind ok, med_err 500, FAIL (ran through this time; whole-second did not overshoot).
- W1 eval-r6-w1: 6 steps, 1/4 hits (miss macro1/3/4), kind ok, med_err 1000, FAIL (down from R5 3/4; n=1 noise).
- OBS eval-r6-obs: 6 steps, 4/7 hits (miss GT1/GT2/GT6, same trio as R3/R5), merges 0, kw 2/4, halluc 0, kind ok, med_err 583, FAIL.
- S003 eval-r6-s003: 7 steps vs 2 macro GT, 2/2 hits, kind ok, med_err 250, PASS.
- WIN eval-r6-winter: 0 steps, kind non_procedural/non_procedural, PASS. Gate works.
- Verdict: 2/6 pass. Single-variable goal met (winter fixed, kind ok 6/6). Procedural cells swing within n=1 noise (m1 +2, l3 fail→3/4, w1 −2, obs flat); OBS GT1/GT2/GT6 stably missed on timing. v6 keeps default-candidate status; procedural gains need direction (b)/(e).
- Round spend: 6 paid calls, in 34450 / out 15333, est $0.1082. Cumulative iteration: 20 calls, est $0.3722.

## R7 (v6 fill-in: D7 talk-slides + D8 ui-b)

- TALK eval-r7-talk: 0 steps, kind non_procedural/non_procedural, PASS. Slide bullets not split into steps.
- UIB eval-r7-uib: 3/5 hits (miss GT2 type, GT3 click 新增), merges 1 (focus+type merged, same as OBS), kw 3/3, halluc 0, kind ok, med_err 638, FAIL.
- Verdict: kind gate 2/2 on non-proc dev (winter, talk). Vertical UI: keywords perfect, timing/merge same failure shape as OBS.
- Round spend: 2 paid calls, est $0.0255. Cumulative iteration: 22 calls, est $0.3977.

## E0 (text-token measurement, no generation)

- /v1/responses/input_tokens: text-only 170; text+1280px image 1368 (image ~1198, matches official ~1300-1500); text+video 172 — the endpoint does NOT expand input_file, so video tokens cannot be measured directly.
- Real v5 prompt (4756 chars incl frame list) = 1628 tokens. 20:28 run input 5759 − 1628 = ~4131 for video (+strict schema overhead); est video ≈ 3.6–4.1k tokens ≈ ~3 full-res images. H1 (server downsamples, small text blurs) SUPPORTED.
- Cost: endpoint returned no billing info; recorded $0.
- Text baseline (scripts/score_text.py): 11:58 required HIT, kw 3/5, halluc 3; 20:28 required MISS, kw 2/5, halluc 1. Matches root-cause report.

## E1 (prompt v7 = v6 + verbatim-text rule + hint guard, OBS x2)

- try1 eval-e1-obs-1: events 3/7 (miss GT1/GT2/GT5/GT6), text required MISS (read 買系統 3 盒), kw 3/5, text-halluc 0, unreadable 0.
- try2 eval-e1-obs-2: events 2/7, text required MISS (read 買菜做飯), kw 3/5, text-halluc 10 (incl JSON-escape fragments), unreadable 0.
- Verdict: verbatim rule does NOT recover the required keyword (0/2); model guesses instead of writing unreadable. Events worse than v5 baseline (2-3/7 vs 4/7) but n=1 noise overlaps. As predicted: rule changes hallucination shape, not hit rate. Proceed to E2 (keyframes).
- Round spend: 2 paid calls, in 11868 / out 6038, est $0.0405. Cumulative iteration: 24 calls, est $0.4382.

## E2 (v7+keyframes x2, 14 images) — both failed

- try1: model wrote the same step id 5x → validation failed. Input 23048 tokens (mixed video+images accepted, no 400!). Billed ~$0.043.
- try2: incomplete, max_output_tokens exhausted (4000; reasoning took 2282). Billed ~$0.046.
- Lessons: mixed send works (no fallback needed); 14 images + 4000 output cap = overflow; whole-run flakiness persists.
- Round spend: 2 paid calls, est $0.089.

## Fixes (no paid calls)

- clamp_document: boundaries clamped into [0, duration] in cli.py before validation (L3 R5 overshoot fixed at wrapper); clamped.json records raw vs clamped. Test added.
- frame_ref ±1 frame tolerance: confirmed already done (frame_tolerance_ms + 2 tests, 101-run suite green).
- eval.py: accepts start_s/end_s GT intervals (verified with synthetic data, 2/2 PASS). pass_rule support pending: need the field format from tester (heldout GT not opened).
- score_text.py: fixed JSON-escape fragment bug (walk parsed values, not dumped text). Baselines unchanged; E1-try2 halluc corrected 10 → 4.

## E2' (v6+keyframes x2, 9 images, cap 4000) — both failed

- Both: incomplete, max_output_tokens exhausted (input 16919, reasoning ~2300-2700, JSON cut off). Billed ~$0.038 each.
- Lesson: keyframes need headroom; cap 4000 → 8000 (providers.py + 1 test expectation).
- Round spend: 2 paid calls, est $0.076.

## E2'' (v6+keyframes x2, 9 images, cap 8000)

- try1: events 5/7 (miss GT1/GT2), text required MISS-strict (read 鳳梨酥 3盒, one space off), kw 4/5, halluc 1 (the space variant), unread 0.
- try2: events 3/7 (miss GT1/GT2/GT4/GT6, merges 1), text required HIT, kw 5/5, halluc 0, unread 0.
- Verdict: keyframes fix text (kw 2-3/5 → 4-5/5, first required HIT since 11:58). Events still swing (5/7 vs 3/7); GT1/GT2 stably missed. E2 stop condition (required HIT + halluc 0 + GT ≥6/7 twice) NOT met → proceed to consistency (e).
- Round spend: 2 paid calls, in 33838 / out 8271, est $0.0775. Cumulative iteration: 30 calls, est $0.6807.

## (e)-OBS (v6+kf 3-vote on desktop clip)

- try3 eval-e2pp-obs-3: events 1/7 (!), text required HIT, kw 5/5, halluc 0. Swing confirmed: same setting gives 5/7, 3/7, 1/7.
- VOTE eval-vote-obs (scripts/vote.py, IoU>0.5 clusters, median boundaries, per-field text vote): 7 steps, 6 agreed, 1 needs_review; events 4/7 (miss GT1/GT2/GT6), text required HIT, kw 5/5, halluc 1 (space variant 鳳梨酥 3盒 mixed into merged evidence).
- Verdict: voting stabilizes text (HIT) and lands events at the median (4/7); GT1/GT2/GT6 still missed by all. needs_review flags the disputed step honestly.
- Round spend: 1 paid call (try3), est $0.0362. Cumulative iteration: 31 calls, est $0.7169.

## (e)-dev-full (v6 x3: ea/eb/ec + ed-obs fill, then vote)

Single runs:
- ea: m1 3/4, l3 3/4, w1 0/4, obs RUN_FAIL (frame refs off by 1000ms, correctly rejected), s003 2/2, winter PASS, talk PASS, uib 4/5.
- eb: m1 4/4, l3 3/4, w1 1/4, obs 3/7, s003 2/2, winter PASS, talk PASS, uib 4/5.
- ec: m1 4/4, l3 3/4, w1 0/4, obs 2/7, s003 2/2, winter PASS, talk PASS, uib 4/5.
- ed-obs fill: 4/7.
Voted (evote): m1 4/4 PASS, l3 4/4 PASS, w1 0/4 FAIL, obs 3/7 FAIL, s003 2/2 PASS, winter PASS, talk PASS, uib 4/5 PASS.
- Verdict: 5/8 pass. Voting rescues l3 (3x 3/4 → 4/4) and stabilizes m1/uib; w1 (0,1,0) and obs (3,2,4) stay FAIL — systematic, not noise. uib GT1 missed 3/3 + vote.
- Round spend: 25 paid calls, in 135492 / out 67809, est $0.4576 (over $0.29 estimate: 8k cap lengthened outputs). Cumulative iteration: 56 calls, est $1.1745.

## (b) two-stage v8 dev-full (describe 9 kf + segment with table)

- M1: 4/4 hits but halluc 1 (nut) → FAIL. kw 0/1.
- L3: 3/4 (miss macro2), merges 1 → FAIL.
- W1: 2/4 (miss macro1/3) → FAIL.
- OBS (b0): 1/7 (merges 2, table 3s-granularity misleads cuts), text required HIT, kw 5/5 → FAIL.
- S003: 2/2 PASS. WIN: PASS. TALK: PASS. UIB: 4/5 (miss GT1) PASS.
- Verdict: 4/8 pass. Two-stage fixes text (OBS 5/5) but hurts event timing vs single-pass; m1 hit by nut halluc. Worse than (e) vote 5/8. (b) NOT adopted.
- Round spend: 8 clips x2 calls, in 140371 / out 32664, est $0.3143. Cumulative iteration: 72 calls, est $1.4888.

## held-out (v6 x3 + vote, single evaluation, 7 clips)

- m1-0008: voted 2 steps, 1/4 (miss GT2/GT3/GT4, merges 2), kw 2/2, halluc 0 → FAIL.
- aalto-03: voted 7 steps, 3/5 (miss GT3/GT5), kw 4/4, halluc 0 → FAIL (60%).
- l4-strex: voted 6 steps, 4/4, kw 2/3, halluc 0 → PASS.
- ui-a: voted 10 steps, 6/8 (miss GT1/GT4), kw 4/4, halluc 0 → FAIL (75%).
- postfire-forest: 0 steps, non_procedural → PASS.
- wok-cooking: voted 5 steps, 4/4, kw 3/3, halluc 0 → PASS.
- stump-grinder: voted 1 step procedural → PASS via pass_rule_struct.
- Verdict: 4/7 pass. Generalizes to new cooking/tool/UI clips (wok 4/4, ui-a 6/8, l4 4/4); fails on fine-grained robot assembly (m1-0008 merges) and woodbox boundaries. No further tuning on held-out.
- Round spend: 21 paid calls, in 102120 / out 55833, est $0.3649. Cumulative iteration: 93 calls, est $1.8537.

## Default (post-W15): v6 + keyframes on, vote optional

- Default combo is v6+kf (E2'' text evidence: kw 2-3/5 → 4-5/5, first required HIT). Event timing still swings run to run, so the 3x majority-vote option stays.

## live-m1 (default combo v6+kf single run, for v10 main dish)

- 4 steps (1/4/7/10s cuts), 4/4 hits, kw 0/1, halluc 0, med_err 500, PASS. 5 kf images. Cost $0.0225.
- Cumulative iteration: 94 calls, est $1.8762.

## (b)-dense (v6 + 500ms keyframes, obs/uib validation)

- OBS-dense: 50 kf images, 3/7 (miss GT1/GT2/GT6/GT7), kw 4/4, halluc 0 → FAIL.
- UIB-dense: 22 kf images, 4/5 (miss GT1), kw 3/3 → PASS.
- Verdict: dense frames do NOT recover short UI events (GT1/GT2 still missed at 0.5s sampling). Resolution is not the bottleneck for timing; drop dense-grid, try diff-triggered frames or zoom-in.
- Round spend: 2 paid calls, in 79182 / out 7179, est $0.1295. Cumulative iteration: 96 calls, est $2.0057.

## Expansion (23:xx, CC0/CC BY hunt for dev 15+)

- Found 1/7 needed: phab-ui.webm (CC0, Phabricator board screencast 52s, real software UI, ffprobe ok). GT to be marked 11/4 morning (dev D9).
- Rejected: Wind River timelapse (procedural but textureless → moved to heldout2 reserve, unseen); 3 woodworking/archive.org IDs (license unverifiable, 404/collection-gated); DVIDS empty.
- Still missing: 2nd robot-arm clip, hand-tool/cooking-2nd, woodworking-replacement. Wikimedia CC0 cooking/tool search continues 11/4.
- Spend: $0 (no paid calls).

## UI acceptance (v6+kf default, operator runs from web UI, no repair triggered)

- OBS web-20261103T145232Z: 6 steps; text 鳳梨酥3盒/清除已完成 correct; GT3-GT7 hit; GT1+GT2 merged into one 2000-8000 step (honest record: focus/type merge persists in UI default runs).
- M1 web-20261103T145138Z: 4 steps, cuts 0/4/7/10s.
- Neither run triggered repair_frame_refs; both exported clean. Paid by operator budget (not counted in iteration cumulative).

## (a)-zoomin (v6 plain re-ask on ±2s subclips, obs/uib)

- obs-merge (0-8.5s, covers GT1+GT2): judged UNCERTAIN, 1 step 0-1s (hover, unverified). The 7.5s of typing after it was not seen at all.
- obs-nr (11-18s, covers vote-04 dispute): INCOMPLETE max_output_tokens (reasoning burned 8k, no JSON out).
- uib-focus (0-3.5s, covers GT1): INCOMPLETE max_output_tokens (same).
- Verdict: plain re-ask on subclips does NOT recover short UI events — worse than full-clip (merge lost everything past 1s; 2/3 burned output cap). Short clips make v6 gate more conservative, not sharper. Next: dedicated zoom prompt ("this is a magnification of Xs-Ys, list transitions only") or diff-triggered frames; plain (a) dropped.
- Round spend: 3 paid calls, in 6987 / out 18129, est $0.0886 (over $0.07 estimate: two 8k incomplete outputs). Cumulative iteration: 99 calls, est $2.0943.
