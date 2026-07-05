#!/usr/bin/env bash
# X-attention feeder: collect StockTwits metrics locally (home IP — GitHub
# runners are 403-blocked) and publish the state files to the `x-state`
# branch, which the CI scan restores before running. Single-writer design:
# only this machine pushes x-state; CI only reads. If this never runs, the
# X signal simply stays neutral — alpha-safe by contract.
#
# Scheduled ~25 min BEFORE the CI scan so "today's" X data is in place.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
{
  echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') xfeed ==="
  if ! python3 scripts/trading_day.py; then
    echo "market closed — skipping"
    exit 0
  fi
  PYTHONPATH=src python3 -m rmas.cli --live xfeed --top 40

  # Publish data/state/x_*.json to the x-state branch via plumbing —
  # no checkout churn, works regardless of the current branch state.
  export GIT_INDEX_FILE=.git/xstate-index
  rm -f "$GIT_INDEX_FILE"
  git add -f data/state/x_history.json data/state/x_watchers.json
  TREE=$(git write-tree)
  git fetch origin x-state --depth 1 2>/dev/null || true
  PARENT=$(git rev-parse -q --verify origin/x-state || true)
  COMMIT=$(git commit-tree "$TREE" ${PARENT:+-p "$PARENT"} \
           -m "xfeed $(date -u '+%Y-%m-%d %H:%M')")
  unset GIT_INDEX_FILE
  git push origin "$COMMIT:refs/heads/x-state"
  echo "published x-state ($COMMIT)"
} >> logs/xfeed.log 2>&1
