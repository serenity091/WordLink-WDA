#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ "${OSTYPE:-}" != darwin* ]]; then
  echo "This installer is for macOS."
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required. Install it first from https://brew.sh, then rerun this script."
  exit 1
fi

BREW_PREFIX="$(brew --prefix)"
export PATH="$BREW_PREFIX/bin:$BREW_PREFIX/sbin:/usr/local/bin:/usr/local/sbin:$PATH"

if ! command -v xcrun >/dev/null 2>&1; then
  echo "Xcode command line tools are missing. Run: xcode-select --install"
  exit 1
fi

echo "Installing external tools..."
brew install node libusb python@3.12

NPM_PREFIX="$(npm prefix -g)"
export PATH="$NPM_PREFIX/bin:$PATH"

echo "Installing Appium and XCUITest driver..."
npm install -g appium
"$NPM_PREFIX/bin/appium" driver install xcuitest || true

if ! command -v appium >/dev/null 2>&1; then
  echo "Appium installed but is not on PATH."
  echo "Add this to ~/.zprofile, then open a new Terminal:"
  echo "  export PATH=\"$NPM_PREFIX/bin:\\$PATH\""
  exit 1
fi

PYTHON_BIN="$(brew --prefix python@3.12)/bin/python3.12"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

echo "Creating Python venv with: $PYTHON_BIN"
"$PYTHON_BIN" -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip setuptools wheel
python -m pip install \
  av \
  numpy \
  opencv-python \
  pyusb

chmod +x start_wda.py scripts/*.sh 2>/dev/null || true

python - <<'PY'
import importlib.util

missing = [
    name
    for name in ("av", "cv2", "numpy", "usb")
    if importlib.util.find_spec(name) is None
]
if missing:
    raise SystemExit("Missing Python packages: " + ", ".join(missing))
print("Python dependency check ok.")
PY

echo
echo "Source setup complete."
echo "Before running, edit config.json for this Mac/phone:"
echo "  DEVICE_UDID can be empty to auto-detect, or set to the friend's UDID."
echo "  WDA_BUNDLE_ID must match the installed/signed WDA."
echo
echo "Run with:"
echo "  ./run_source.sh"
