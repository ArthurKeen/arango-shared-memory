#!/usr/bin/env bash
#
# install_maintenance_schedule.sh — register maintain.py as a periodic job so
# lifecycle passes run without human attention.
#
#   macOS : writes + loads a launchd agent (survives reboot; runs missed jobs
#           on wake per launchd semantics)
#   other : prints the crontab line to add (cron is distro-specific enough that
#           we don't edit the crontab for you)
#
# Usage:
#   scripts/install_maintenance_schedule.sh [--interval weekly|daily] \
#       [--server-dir ~/code/arango-solutions-mcp-server] [--with-llm] [--uninstall]
#
# The job runs inside the MCP server's Poetry env (which has python-arango) and
# logs to ~/.arango-shared-memory/maintain.log.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.arango-shared-memory.maintain"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/.arango-shared-memory"
LOG="$LOG_DIR/maintain.log"

INTERVAL="weekly"
SERVER_DIR="$HOME/code/arango-solutions-mcp-server"
WITH_LLM=""
UNINSTALL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --interval)   INTERVAL="${2:-weekly}"; shift 2;;
    --server-dir) SERVER_DIR="${2:-}"; shift 2;;
    --with-llm)   WITH_LLM="--with-llm"; shift;;
    --uninstall)  UNINSTALL=1; shift;;
    -h|--help)    sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "error: unknown argument: $1" >&2; exit 1;;
  esac
done

CMD="cd $SERVER_DIR && poetry run python $SCRIPT_DIR/maintain.py $WITH_LLM >> $LOG 2>&1"

if [ "$(uname)" != "Darwin" ]; then
  if [ "$UNINSTALL" -eq 1 ]; then
    echo "Non-macOS: remove the maintain.py line from your crontab (crontab -e)."
    exit 0
  fi
  echo "Non-macOS platform — add this line to your crontab (crontab -e):"
  case "$INTERVAL" in
    daily)  echo "0 3 * * *  $CMD";;
    *)      echo "0 3 * * 0  $CMD";;
  esac
  exit 0
fi

if [ "$UNINSTALL" -eq 1 ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Uninstalled $LABEL."
  exit 0
fi

[ -d "$SERVER_DIR" ] || { echo "error: server dir not found: $SERVER_DIR (use --server-dir)" >&2; exit 1; }
mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"

if [ "$INTERVAL" = "daily" ]; then
  CALENDAR="    <dict>
      <key>Hour</key><integer>3</integer>
      <key>Minute</key><integer>0</integer>
    </dict>"
else
  CALENDAR="    <dict>
      <key>Weekday</key><integer>0</integer>
      <key>Hour</key><integer>3</integer>
      <key>Minute</key><integer>0</integer>
    </dict>"
fi

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>$CMD</string>
  </array>
  <key>StartCalendarInterval</key>
$CALENDAR
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST_EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Installed $LABEL ($INTERVAL, 03:00). Log: $LOG"
echo "Uninstall with: $0 --uninstall"
