#!/usr/bin/env bash
set -euo pipefail

DIST_DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)}"

if [[ ! -d "$DIST_DIR" ]]; then
  echo "Dist folder not found: $DIST_DIR"
  exit 1
fi

echo "Preparing permissions in: $DIST_DIR"

if command -v xattr >/dev/null 2>&1; then
  xattr -dr com.apple.quarantine "$DIST_DIR" 2>/dev/null || true
fi

find "$DIST_DIR" -type d -exec chmod 755 {} +
find "$DIST_DIR" -type f -exec chmod 644 {} +

find "$DIST_DIR" -type f \( \
  -name "*.sh" \
  -o -name "*.bin" \
  -o -name "*.so" \
  -o -name "*.dylib" \
  -o -name "*.framework" \
\) -exec chmod 755 {} +

if [[ -f "$DIST_DIR/run.sh" ]]; then
  chmod 755 "$DIST_DIR/run.sh"
fi

if [[ -f "$DIST_DIR/start_wda.bin" ]]; then
  chmod 755 "$DIST_DIR/start_wda.bin"
fi

if [[ -d "$DIST_DIR/WebDriverAgentRunner-Runner.app" ]]; then
  find "$DIST_DIR/WebDriverAgentRunner-Runner.app" -type d -exec chmod 755 {} +
  find "$DIST_DIR/WebDriverAgentRunner-Runner.app" -type f -exec chmod 644 {} +
  find "$DIST_DIR/WebDriverAgentRunner-Runner.app" -type f \( \
    -perm -111 \
    -o -name "*.dylib" \
    -o -name "*.so" \
  \) -exec chmod 755 {} +
fi

echo "Done."
