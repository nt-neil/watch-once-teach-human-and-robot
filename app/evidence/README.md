# Evidence runs (curated subset)

Single-file evidence per run: operations/manual (or checklist), provider result with
model id + usage, cost report, safe request/response (keys and provider private ids
redacted: file-*/resp_* masked, local paths replaced with <local-path>), frames.json,
and
320px frame thumbnails (same filenames as the originals).

- real-smoke (10/30, prompt v1): META muse-spark-1.2, 4 steps, US$0.013325.
- m1-checklist-loop-1030-r2 (10/30, prompt v1): checklist closed loop, Muse CLI + Spark verdict.
- m1-muse-rerun (11/3, prompt v1): same clip re-run, 2 steps (unstable granularity, honest).
- web-20261103T083626Z (11/3, prompt v1): UI live run, META muse-spark-1.2, 2 steps, US$0.011047.
- web-20261103T111002Z (11/3, prompt v2): UI live run, META muse-spark-1.2, 4 steps (0.8/3.8/7.5/10.5s cuts, evidence per step), US$0.0149.
Source clip: AIST Bimanual Manipulation Dataset, CC BY 4.0 (see top README).
