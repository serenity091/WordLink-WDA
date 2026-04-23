#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if command -v brew >/dev/null 2>&1; then
  BREW_PREFIX="$(brew --prefix)"
  export PATH="$BREW_PREFIX/bin:$BREW_PREFIX/sbin:$PATH"
fi

if command -v npm >/dev/null 2>&1; then
  NPM_PREFIX="$(npm prefix -g 2>/dev/null || true)"
  if [[ -n "$NPM_PREFIX" ]]; then
    export PATH="$NPM_PREFIX/bin:$PATH"
  fi
fi

export KMP_DUPLICATE_LIB_OK="${KMP_DUPLICATE_LIB_OK:-TRUE}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"

if [[ -x ".venv/bin/python" ]]; then
  exec .venv/bin/python start_wda.py
fi

if command -v python3.12 >/dev/null 2>&1; then
  exec python3.12 start_wda.py
fi

exec python3 start_wda.py
