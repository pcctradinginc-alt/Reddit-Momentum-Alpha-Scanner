#!/usr/bin/env bash
# Install (or update) the local LaunchAgents:
#   com.rmas.daily-scan  — weekdays 14:35 local: full scan + alert (backup rail)
#   com.rmas.xfeed       — weekdays 14:05 local: X/StockTwits attention feeder
#                          (collects from the home IP — CI runners are blocked —
#                          and publishes to the x-state branch for the CI scan)
#
#   ./scripts/local_autorun_install.sh            install + load both
#   ./scripts/local_autorun_install.sh uninstall  remove both
#
# Cost: $0 — runs on this machine, no cloud minutes.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS_DIR="$HOME/Library/LaunchAgents"

install_agent() {  # label, script, hour, minute
  local LABEL="$1" SCRIPT="$2" HOUR="$3" MIN="$4"
  local PLIST="$AGENTS_DIR/$LABEL.plist"
  mkdir -p "$AGENTS_DIR"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$REPO/$SCRIPT</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
  </array>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$REPO/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>$REPO/logs/launchd.log</string>
</dict>
</plist>
EOF
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  echo "Installed $LABEL — weekdays $HOUR:$MIN local."
}

if [ "${1:-}" = "uninstall" ]; then
  for LABEL in com.rmas.daily-scan com.rmas.xfeed; do
    launchctl unload "$AGENTS_DIR/$LABEL.plist" 2>/dev/null || true
    rm -f "$AGENTS_DIR/$LABEL.plist"
    echo "Removed $LABEL."
  done
  exit 0
fi

# xfeed runs BEFORE the 12:30 UTC CI scan so today's X data is in place.
install_agent com.rmas.xfeed scripts/xfeed_push.sh 14 5
install_agent com.rmas.daily-scan scripts/run_local_scan.sh 14 35
echo "Logs: logs/xfeed.log, logs/local_scan.log"
echo "Remove anytime with: ./scripts/local_autorun_install.sh uninstall"
