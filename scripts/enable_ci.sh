#!/usr/bin/env bash
# One-time CI activation: the gh OAuth token needs the `workflow` scope to
# push .github/workflows/. This refreshes the scope (browser confirmation)
# and pushes the already-committed workflow.
set -euo pipefail
cd "$(dirname "$0")/.."
gh auth refresh -h github.com -s workflow
git push origin main
echo "CI workflow pushed — the scheduled daily scan is now live on GitHub."
echo "Note: if the local LaunchAgent is also emailing (keys in .env), disable"
echo "one of the two to avoid duplicate emails."
