#!/bin/bash
# Install mac-cron-manager: Python venv + a launchd agent that serves the web dashboard
# on http://127.0.0.1:$MCM_PORT (default 7878) from login onwards.
#   ./install.sh              install / update
#   ./install.sh --uninstall  stop the web dashboard and remove the agent
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="${MCM_LABEL:-local.mac-cron-manager}"
PORT="${MCM_PORT:-7878}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
STATE="$HOME/.local/state/mac-cron-manager"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$PLIST"
  echo "removed $LABEL (job history in $STATE is kept)"
  exit 0
fi

python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
chmod +x "$DIR/cronmon"
mkdir -p "$STATE" "$(dirname "$PLIST")"

cat >"$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/.venv/bin/python</string>
    <string>$DIR/web.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>MCM_PORT</key><string>$PORT</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$STATE/web.log</string>
  <key>StandardErrorPath</key><string>$STATE/web.log</string>
</dict>
</plist>
PLIST

launchctl bootstrap "$DOMAIN" "$PLIST"
echo "mac-cron-manager is running: http://127.0.0.1:$PORT"
echo "monitor a job:        * * * * * $DIR/cronmon <name> <command...>"
