#!/usr/bin/env bash
set -euo pipefail

HOST="${APPIUM_HOST:-127.0.0.1}"
PORT="${APPIUM_PORT:-4723}"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: scripts/start_appium.sh

Starts an Appium server for iOS/XCUITest automation.

Environment:
  APPIUM_HOST   Default: 127.0.0.1
  APPIUM_PORT   Default: 4723

Example:
  scripts/start_appium.sh
EOF
  exit 0
fi

if ! command -v appium >/dev/null 2>&1; then
  echo "Missing appium. Install with: npm install -g appium" >&2
  exit 1
fi

echo "Starting Appium on http://${HOST}:${PORT}"
echo "Leave this terminal open while running Python Appium scripts."

appium --address "${HOST}" --port "${PORT}"
