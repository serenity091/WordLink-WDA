#!/usr/bin/env bash
set -euo pipefail

if command -v appium >/dev/null 2>&1; then
  appium driver run xcuitest open-wda
  exit 0
fi

WDA_PROJECT="$(find "${APPIUM_HOME:-$HOME/.appium}" -name WebDriverAgent.xcodeproj -print -quit)"
if [[ -z "${WDA_PROJECT}" ]]; then
  echo "Could not find WebDriverAgent.xcodeproj under ${APPIUM_HOME:-$HOME/.appium}" >&2
  echo "Run: npm install -g appium && appium driver install xcuitest" >&2
  exit 1
fi

open "${WDA_PROJECT}"
