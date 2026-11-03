#!/bin/bash
# Run one eval round over the full dev set, then score each run.
# Usage: scripts/run-round.sh <round-id> <prompt-version>
# Example: scripts/run-round.sh r5 v5
# GT is for scoring only, never for prompts. Each run is one paid Meta call.
set -u
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# Load the paid key without ever printing it. Fails closed when missing.
if [ -z "${META_API_KEY:-}" ] && [ -f "$HOME/.claude/secrets/meta.env" ]; then
  set -a
  . "$HOME/.claude/secrets/meta.env"
  set +a
fi
if [ -z "${META_API_KEY:-}" ]; then
  echo "ERROR: META_API_KEY is not configured; no paid call made." >&2
  exit 2
fi
ROUND="$1"
VER="$2"
REPORT_DIR="$APP_DIR/eval/rounds/$ROUND"
mkdir -p "$REPORT_DIR"

run_one() {
  local clip="$1" video="$2" gt="$3" kind="$4"
  local out="$APP_DIR/runs/eval-${ROUND}-${clip}"
  echo "=== [$ROUND/$VER] $clip ==="
  "$APP_DIR/.venv/bin/python" -B "$APP_DIR/cli.py" "$video" \
    --workspace-path "$APP_DIR" --out "$out" \
    --provider meta --confirm-paid-api --prompt-version "$VER" || {
      echo "RUN_FAILED $clip" | tee -a "$REPORT_DIR/summary.txt"
      return 0
    }
  local kind_arg=""
  [ -n "$kind" ] && kind_arg="--kind $kind"
  # shellcheck disable=SC2086
  "$APP_DIR/.venv/bin/python" -B "$APP_DIR/scripts/eval.py" \
    --run "$out" --gt "$APP_DIR/$gt" $kind_arg \
    > "$REPORT_DIR/$clip.json" 2> "$REPORT_DIR/$clip.score.txt" || {
      echo "EVAL_FAILED $clip" | tee -a "$REPORT_DIR/summary.txt"
      return 0
    }
  cat "$REPORT_DIR/$clip.score.txt" | tee -a "$REPORT_DIR/summary.txt"
}

MS="$APP_DIR/../materials-survey"
run_one m1    "$MS/research/samples/0001_task_video.mp4"            "eval/gt/m1.json"              "m1"
run_one l3    "$MS/research/muse1/samples/brickstrex-s-candidate.mp4" "runs/l3-1030/ground_truth.json" ""
run_one w1    "$MS/research/samples/aalto_01_glove_assembly_native.mp4" "runs/w1-1030/ground_truth.json" ""
run_one obs   "$APP_DIR/eval/clips/d4-todo.mp4"                     "runs/desktop-1103/ground_truth.json" "desktop"
run_one s003  "$MS/creator/plos-n2/s003-window.mp4"                 "eval/gt/s003-window.json"     ""
run_one winter "$APP_DIR/eval/clips/winter-stream-30s.mp4"          "eval/gt/winter-stream.json"   ""
run_one talk   "$APP_DIR/eval/clips/talk-slides.mp4"                "eval/gt/talk-slides.json"     ""
run_one uib    "$APP_DIR/eval/clips/ui-b-vertical-noaudio.mp4"      "eval/gt/ui-b.json"            ""
echo "=== round $ROUND done; scores in $REPORT_DIR ==="
