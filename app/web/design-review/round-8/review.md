# Design review round-8 (spot review + live-run verification)

Date: reviewed by Muse Code with built-in vision only. Viewed: round-8
result-meta-1280, result-realsmoke-1280, home-1280 + all 7 walkthrough-meta.
Rest carry prior verdicts; operator measured 10/10 scrollWidth == innerWidth.

## Live run (ego real clicks, M1 + meta + confirm)

- Run web-20261103T083626Z: META muse-spark-1.2, 2704/1804 tokens,
  budget 1 call US$0.011047 (matches usage math, correctly accounted).
- Only 2 steps (0–7000 / 7000–14000) — same as the morning re-run, vs 4 on
  10/30. Recorded honestly as unstable granularity; no 4/4 claim.
- Flow 01–07 verified: home → M1+meta+confirm → job polls → META result →
  steps/export/navide (workspace scrubbed, exported_not_sent honest).

## Code-lag note (server needs restart)

walkthrough-meta + round-8/home were shot with pre-round8b server code:
dropdown still shows "— none —" instead of the M1 default, and the key-status
line is absent. The live run itself is unaffected. After restart, reshoot
home-1280/390 (M1 default + key status) and one synthetic result (TEST
PATTERN badge + manual H1 fix proof), then swap into the report.

## H1 + band answers

- Manual H1: fixed in code (.manual h1–h3 capped 1.25/1.15/1.1rem + wrap,
  covered by smoke); result-meta Full manual step headings render normal
  size. Direct H1 proof awaits the synthetic reshoot (M1 H1 sits folded).
- Bottom band: still no CSS culprit (zero shadow/gradient/blur/sticky/filter/
  fixed in the whole stylesheet). The teal strip in walkthrough 06 is a
  scrolled manual content image at the capture boundary, not chrome. No
  defect; need a circled position if it persists on new shots.
