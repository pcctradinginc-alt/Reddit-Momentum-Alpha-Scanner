#!/usr/bin/env bash
# One-time CI activation: the gh OAuth token needs the `workflow` scope to
# push .github/workflows/. This refreshes the scope (browser confirmation),
# merges the ready-made workflow from the local `ci-workflow` branch and
# pushes it.
set -euo pipefail
cd "$(dirname "$0")/.."
gh auth refresh -h github.com -s workflow
git merge --no-edit ci-workflow
git push origin main
echo "CI workflow pushed — the scheduled daily scan is now live on GitHub."
echo "Note: if the local LaunchAgent is also emailing (keys in .env), disable"
echo "one of the two to avoid duplicate emails."
