#!/bin/bash
# Pool dev short-clip baseline: v6+kf, n=2 each. Usage: run-pool.sh <tag>
# Logs to runs/pool-<tag>.log. Paid calls; needs META_API_KEY + META_TRUST_PROXY=1.
set -u
cd "$(dirname "$0")/.."
TAG="${1:?tag}"
W="$PWD"
LOG="runs/pool-$TAG.log"
: > "$LOG"
run_one() {
  local clip="$1" gt="$2" out="$3"
  echo "=== $out $(date -u +%H:%M:%S)" | tee -a "$LOG"
  META_TRUST_PROXY=1 .venv/bin/python -B cli.py "eval/clips/pool-mp4/$clip.mp4" \
    --workspace-path "$W" --provider meta --confirm-paid-api \
    --prompt-version v6 --out "runs/$out" >>"$LOG" 2>&1
  .venv/bin/python -B scripts/eval.py --run "runs/$out" --gt "eval/gt/$gt.json" 2>&1 \
    | grep -E "^score" | tee -a "$LOG"
}
for c in R3 H3 H4 H6 H7 H9 N2 N4 P3 A1; do
  g="pool-$(echo "$c" | tr 'A-Z' 'a-z')"
  for n in 1 2; do
    [ "$c" = R3 ] && [ "$n" = 1 ] && continue
    run_one "$c" "$g" "pool-$c-$n"
  done
done
echo "=== DONE $(date -u +%H:%M:%S)" | tee -a "$LOG"
