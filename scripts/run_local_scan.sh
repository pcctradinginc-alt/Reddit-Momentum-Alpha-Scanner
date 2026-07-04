#!/usr/bin/env bash
# Local automation: daily scan + alert from this machine (LaunchAgent entry).
# Installed by scripts/local_autorun_install.sh; logs to logs/local_scan.log.
#
# Honest by design: without live keys in .env the degraded guard forces
# dry-run, so nothing fake is ever emailed. Once .env has real keys this
# starts emailing signals — if the GitHub Actions scan is ALSO active you
# would get two emails per day; keep only one of the two.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
{
  echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') local scan ==="
  if ! python3 scripts/trading_day.py; then
    echo "market closed — skipping (zero API calls spent)"
    exit 0
  fi
  PYTHONPATH=src python3 -m rmas.cli --live alert
} >> logs/local_scan.log 2>&1
