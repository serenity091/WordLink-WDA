#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

APP_NAME="WordLink WDA"
PYTHON_PACKAGES=(av numpy opencv-python pyusb)
PYTHON_IMPORTS=(av cv2 numpy usb)
BREW_PACKAGES=(node libusb python@3.12)

ASSUME_YES=0
CHECK_ONLY=0

usage() {
  cat <<'EOF'
WordLink WDA dependency setup

Usage:
  ./setup_wordlink.command           Check dependencies, then ask before installing missing ones.
  ./setup_wordlink.command --check   Only check dependencies.
  ./setup_wordlink.command --yes     Install missing dependencies without prompting.
  ./setup_wordlink.command --help    Show this help.

This setup is for macOS. It installs/checks:
  - Xcode command line tools
  - Homebrew packages: node, libusb, python@3.12
  - Appium and the XCUITest driver
  - Python virtualenv packages: av, numpy, opencv-python, pyusb
EOF
}

for arg in "$@"; do
  case "$arg" in
    --check)
      CHECK_ONLY=1
      ;;
    --yes|-y)
      ASSUME_YES=1
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $arg"
      usage
      exit 2
      ;;
  esac
done

say() {
  printf '\n==> %s\n' "$1"
}

ok() {
  printf '  [ok] %s\n' "$1"
}

warn() {
  printf '  [missing] %s\n' "$1"
}

die() {
  printf '\nError: %s\n' "$1" >&2
  exit 1
}

confirm() {
  local prompt="$1"
  if [[ "$ASSUME_YES" == "1" ]]; then
    return 0
  fi
  printf '%s [y/N] ' "$prompt"
  read -r answer
  case "$answer" in
    y|Y|yes|YES)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

add_common_paths() {
  if command -v brew >/dev/null 2>&1; then
    local brew_prefix
    brew_prefix="$(brew --prefix)"
    export PATH="$brew_prefix/bin:$brew_prefix/sbin:$PATH"
  fi
  if command -v npm >/dev/null 2>&1; then
    local npm_prefix
    npm_prefix="$(npm prefix -g 2>/dev/null || true)"
    if [[ -n "$npm_prefix" ]]; then
      export PATH="$npm_prefix/bin:$PATH"
    fi
  fi
  export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:$PATH"
}

check_macos() {
  say "Checking operating system"
  if [[ "${OSTYPE:-}" != darwin* ]]; then
    die "$APP_NAME setup currently supports macOS only."
  fi
  ok "macOS detected"
}

check_xcode_tools() {
  say "Checking Xcode command line tools"
  if command -v xcrun >/dev/null 2>&1 && xcrun --find xcodebuild >/dev/null 2>&1; then
    ok "Xcode command line tools are available"
    return 0
  fi

  warn "Xcode command line tools"
  if [[ "$CHECK_ONLY" == "1" ]]; then
    return 1
  fi
  echo "Install them with: xcode-select --install"
  if confirm "Open the Xcode command line tools installer now?"; then
    xcode-select --install || true
    echo "After the installer finishes, run this setup again."
  fi
  return 1
}

check_homebrew() {
  say "Checking Homebrew"
  if command -v brew >/dev/null 2>&1; then
    ok "Homebrew is installed"
    add_common_paths
    return 0
  fi

  warn "Homebrew"
  echo "Install Homebrew from https://brew.sh, or run:"
  echo '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  return 1
}

install_brew_packages() {
  say "Checking Homebrew packages"
  local missing=()
  for package in "${BREW_PACKAGES[@]}"; do
    if brew list --versions "$package" >/dev/null 2>&1; then
      ok "$package"
    else
      warn "$package"
      missing+=("$package")
    fi
  done

  if [[ "${#missing[@]}" == "0" ]]; then
    return 0
  fi
  if [[ "$CHECK_ONLY" == "1" ]]; then
    return 1
  fi
  if confirm "Install missing Homebrew packages: ${missing[*]}?"; then
    brew install "${missing[@]}"
    add_common_paths
    return 0
  fi
  return 1
}

install_appium() {
  say "Checking Appium"
  add_common_paths

  if command -v npm >/dev/null 2>&1; then
    ok "npm is available"
  else
    warn "npm"
    return 1
  fi

  if command -v appium >/dev/null 2>&1; then
    ok "Appium is installed"
  else
    warn "Appium"
    if [[ "$CHECK_ONLY" == "1" ]]; then
      return 1
    fi
    if confirm "Install Appium globally with npm?"; then
      npm install -g appium
      add_common_paths
    else
      return 1
    fi
  fi

  if command -v appium >/dev/null 2>&1 && appium driver list --installed 2>&1 | grep -qi xcuitest; then
    ok "Appium XCUITest driver is installed"
    return 0
  fi

  warn "Appium XCUITest driver"
  if [[ "$CHECK_ONLY" == "1" ]]; then
    return 1
  fi
  if confirm "Install Appium XCUITest driver?"; then
    appium driver install xcuitest || true
    return 0
  fi
  return 1
}

python_bin() {
  local brew_python="/opt/homebrew/opt/python@3.12/bin/python3.12"
  local intel_python="/usr/local/opt/python@3.12/bin/python3.12"
  if [[ -x "$brew_python" ]]; then
    echo "$brew_python"
  elif [[ -x "$intel_python" ]]; then
    echo "$intel_python"
  elif command -v python3.12 >/dev/null 2>&1; then
    command -v python3.12
  else
    command -v python3
  fi
}

install_python_env() {
  say "Checking Python virtual environment"
  local py
  py="$(python_bin)"
  [[ -n "$py" ]] || die "No Python found after dependency checks."
  ok "Python: $py"

  if [[ ! -x ".venv/bin/python" ]]; then
    warn ".venv"
    if [[ "$CHECK_ONLY" == "1" ]]; then
      return 1
    fi
    if confirm "Create .venv with $py?"; then
      "$py" -m venv .venv
    else
      return 1
    fi
  else
    ok ".venv exists"
  fi

  # shellcheck disable=SC1091
  source .venv/bin/activate

  local missing
  missing="$(python - <<'PY'
import importlib.util
imports = ["av", "cv2", "numpy", "usb"]
missing = [name for name in imports if importlib.util.find_spec(name) is None]
print(" ".join(missing))
PY
)"

  if [[ -z "$missing" ]]; then
    ok "Python packages: ${PYTHON_PACKAGES[*]}"
    return 0
  fi

  warn "Python imports: $missing"
  if [[ "$CHECK_ONLY" == "1" ]]; then
    return 1
  fi
  if confirm "Install/update Python packages in .venv?"; then
    python -m pip install --upgrade pip setuptools wheel
    python -m pip install "${PYTHON_PACKAGES[@]}"
    return 0
  fi
  return 1
}

check_libusb_file() {
  say "Checking libusb dynamic library"
  local paths=(
    "$ROOT_DIR/lib/libusb-1.0.dylib"
    "/opt/homebrew/lib/libusb-1.0.dylib"
    "/usr/local/lib/libusb-1.0.dylib"
  )
  for path in "${paths[@]}"; do
    if [[ -f "$path" ]]; then
      ok "$path"
      return 0
    fi
  done
  warn "libusb-1.0.dylib"
  return 1
}

final_verify() {
  say "Final verification"
  chmod +x start_wda.py run_source.sh scripts/*.sh setup_wordlink.command 2>/dev/null || true

  if [[ ! -x ".venv/bin/python" ]]; then
    warn ".venv/bin/python"
    return 1
  fi

  # shellcheck disable=SC1091
  source .venv/bin/activate
  python - <<'PY'
import importlib.util
missing = [name for name in ("av", "cv2", "numpy", "usb") if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit("Missing Python packages: " + ", ".join(missing))
print("Python dependency check ok.")
PY

  if command -v appium >/dev/null 2>&1; then
    ok "appium command is on PATH"
  else
    warn "appium command is not on PATH"
    echo "If Appium was installed, open a new Terminal or add npm global bin to PATH."
    return 1
  fi

  check_libusb_file
}

main() {
  echo "$APP_NAME setup"
  echo "Repo: $ROOT_DIR"

  local failed=0
  check_macos || failed=1
  check_xcode_tools || failed=1
  check_homebrew || failed=1

  if command -v brew >/dev/null 2>&1; then
    install_brew_packages || failed=1
  fi

  install_appium || failed=1
  install_python_env || failed=1
  final_verify || failed=1

  echo
  if [[ "$failed" == "0" ]]; then
    echo "Setup complete."
    echo "Next:"
    echo "  1. Edit config.json for the device UDID and WDA bundle id."
    echo "  2. Run ./run_source.sh"
    echo "  3. To test only video streaming, run: source .venv/bin/activate && python test.py --debug"
    return 0
  fi

  if [[ "$CHECK_ONLY" == "1" ]]; then
    echo "Check finished with missing dependencies."
  else
    echo "Setup finished, but at least one dependency is still missing."
  fi
  return 1
}

main "$@"
