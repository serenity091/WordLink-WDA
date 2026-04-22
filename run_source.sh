#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if command -v brew >/dev/null 2>&1; then
  BREW_PREFIX="$(brew --prefix)"
  export PATH="$BREW_PREFIX/bin:$BREW_PREFIX/sbin:$PATH"
fi

if command -v npm >/dev/null 2>&1; then
  NPM_PREFIX="$(npm prefix -g)"
  export PATH="$NPM_PREFIX/bin:$PATH"
fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Missing .venv. Run: ./scripts/install_source_runtime_deps.sh"
  exit 1
fi

if ! command -v appium >/dev/null 2>&1; then
  echo "Missing appium command. Run: ./scripts/install_source_runtime_deps.sh"
  exit 1
fi

export KMP_DUPLICATE_LIB_OK="${KMP_DUPLICATE_LIB_OK:-TRUE}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"

exec .venv/bin/python start_wda.py
