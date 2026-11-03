#!/bin/bash
# (e) consistency: 3 rounds of v6 dev-full, then majority vote per clip.
# Usage: scripts/run-vote-round.sh <tag>   (rounds: <tag>a <tag>b <tag>c)
set -u
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ -z "${META_API_KEY:-}" ] && [ -f "$HOME/.claude/secrets/meta.env" ]; then
  set -a
  . "$HOME/.claude/secrets/meta.env"
  set +a
fi
TAG="$1"
for suf in a b c; do
  "$APP_DIR/scripts/run-round.sh" "${TAG}${suf}" v6 || exit 3
done
mkdir -p "$APP_DIR/eval/rounds/${TAG}vote"
for clip in m1 l3 w1 obs s003 winter talk uib; do
  echo "=== vote $clip ==="
  "$APP_DIR/.venv/bin/python" -B "$APP_DIR/scripts/vote.py" \
    --runs "$APP_DIR/runs/eval-${TAG}a-$clip" "$APP_DIR/runs/eval-${TAG}b-$clip" "$APP_DIR/runs/eval-${TAG}c-$clip" \
    --out "$APP_DIR/runs/eval-${TAG}vote-$clip" 2>&1 | tail -1
done
echo "=== ${TAG}vote done ==="
