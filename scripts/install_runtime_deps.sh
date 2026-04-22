#!/usr/bin/env bash
set -euo pipefail

if [[ "${OSTYPE:-}" != darwin* ]]; then
  echo "This runtime installer is intended for macOS."
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required: https://brew.sh"
  exit 1
fi

echo "Installing external runtime tools..."
brew install node

BREW_PREFIX="$(brew --prefix)"
export PATH="$BREW_PREFIX/bin:$BREW_PREFIX/sbin:/usr/local/bin:/usr/local/sbin:$PATH"
NPM_PREFIX="$(npm prefix -g)"
export PATH="$NPM_PREFIX/bin:$PATH"

echo "Installing Appium and the XCUITest driver..."
npm install -g appium
"$NPM_PREFIX/bin/appium" driver install xcuitest || true

if ! command -v appium >/dev/null 2>&1; then
  echo "Appium installed but is not on PATH."
  echo "Add this to ~/.zprofile, then open a new Terminal:"
  echo "  export PATH=\"$NPM_PREFIX/bin:\\$PATH\""
  exit 1
fi

echo "Done. The packaged app folder should contain config.json, failed_words.json, data/, models/, and WebDriverAgentRunner-Runner.app."
