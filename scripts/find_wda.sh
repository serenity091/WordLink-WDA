#!/usr/bin/env bash
set -euo pipefail

find "${APPIUM_HOME:-$HOME/.appium}" -name WebDriverAgent.xcodeproj -print -quit
