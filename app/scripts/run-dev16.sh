#!/bin/bash
# Dev-16 full pass, one repetition. Usage: scripts/run-dev16.sh <round-id> [prompt-version] [extra-cli-args...]
# GT for scoring only, never for prompts. 16 paid Meta calls per round.
set -u
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ -z "${META_API_KEY:-}" ] && [ -f "$HOME/.claude/secrets/meta.env" ]; then
  set -a
  . "$HOME/.claude/secrets/meta.env"
  set +a
fi
if [ -z "${META_API_KEY:-}" ]; then
  echo "ERROR: META_API_KEY is not configured; no paid call made." >&2
  exit 2
fi
# Sandbox transport only: this eval harness cannot reach api.meta.ai
# directly, so opt into env proxies explicitly. Product default stays off.
export META_TRUST_PROXY=1
ROUND="$1"
VER="${2:-v6}"
shift 2 2>/dev/null || shift 1
EXTRA_ARGS=("$@")
MS="$APP_DIR/../materials-survey"
REPORT_DIR="$APP_DIR/eval/rounds/$ROUND"
mkdir -p "$REPORT_DIR"

run_one() {
  local clip="$1" video="$2" gt="$3" kind="$4"
  local out="$APP_DIR/runs/eval-${ROUND}-${clip}"
  echo "=== [$ROUND/$VER] $clip ==="
  "$APP_DIR/.venv/bin/python" -B "$APP_DIR/cli.py" "$video" \
    --workspace-path "$APP_DIR" --out "$out" \
    --provider meta --confirm-paid-api --prompt-version "$VER" ${EXTRA_ARGS[@]:-} || {
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
  grep -h "score:" "$REPORT_DIR/$clip.score.txt" || true
}

run_one m1    "$MS/research/samples/0001_task_video.mp4"            "eval/gt/m1.json"              "m1"
run_one l3    "$MS/research/muse1/samples/brickstrex-s-candidate.mp4" "runs/l3-1030/ground_truth.json" ""
run_one w1    "$MS/research/samples/aalto_01_glove_assembly_native.mp4" "runs/w1-1030/ground_truth.json" ""
run_one obs   "$APP_DIR/eval/clips/d4-todo.mp4"                     "runs/desktop-1103/ground_truth.json" "desktop"
run_one s003  "$MS/creator/plos-n2/s003-window.mp4"                 "eval/gt/s003-window.json"     ""
run_one winter "$APP_DIR/eval/clips/winter-stream-30s.mp4"          "eval/gt/winter-stream.json"   ""
run_one talk  "$APP_DIR/eval/clips/talk-slides.mp4"                 "eval/gt/talk-slides.json"     ""
run_one uib   "$APP_DIR/eval/clips/ui-b-vertical-noaudio.mp4"       "eval/gt/ui-b.json"            ""
run_one phab  "$APP_DIR/eval/clips/r8mp4/phab-ui.mp4"               "eval/gt/phab-ui.json"         ""
run_one wok   "$APP_DIR/eval/clips/r8mp4/wok-dev.mp4"               "eval/gt/wok-dev.json"         ""
run_one chicken "$APP_DIR/eval/clips/r8mp4/chicken-rice.mp4"        "eval/gt/chicken-rice.json"    ""
run_one alioli60 "$APP_DIR/eval/clips/r8mp4/alioli-0-60.mp4"        "eval/gt/alioli-0-60.json"     ""
run_one m1b   "$APP_DIR/eval/clips/m1-0002.mp4"                     "eval/gt/m1-0002.json"         "m1"
run_one taliya "$APP_DIR/eval/clips/r8mp4/taliya.mp4"               "eval/gt/taliya.json"          ""
run_one pot60 "$APP_DIR/eval/clips/r8mp4/pottery-0-60.mp4"          "eval/gt/pottery-0-60.json"    ""
run_one smith "$APP_DIR/eval/clips/r8mp4/blacksmith.mp4"            "eval/gt/blacksmith.json"      ""
echo "ROUND_DONE $ROUND"
