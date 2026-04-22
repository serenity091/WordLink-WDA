#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/start_wda.sh [options]

Starts WebDriverAgentRunner on a connected physical iPhone using xcodebuild.
Keep this process running while you control the phone.

Options:
  --udid UDID             iPhone UDID. Auto-detected when only one phone is attached.
  --project PATH          Path to WebDriverAgent.xcodeproj.
  --port PORT             Local WDA port forwarded with iproxy. Default: 8100.
  --derived-data PATH     Xcode DerivedData path. Default: .wda-derived-data.
  --no-iproxy             Do not start iproxy.
  --no-provisioning       Do not pass -allowProvisioningUpdates to xcodebuild.
  --dry-run               Print the command that would run, but do not start WDA.
  -h, --help              Show this help.

Environment alternatives:
  UDID, WDA_PROJECT, WDA_PORT, DERIVED_DATA_PATH

Examples:
  scripts/start_wda.sh
  scripts/start_wda.sh --udid 00008140-001A50A93630401C
  scripts/start_wda.sh --no-iproxy
EOF
}

UDID="${UDID:-}"
WDA_PROJECT="${WDA_PROJECT:-}"
WDA_PORT="${WDA_PORT:-8100}"
DERIVED_DATA_PATH="${DERIVED_DATA_PATH:-$PWD/.wda-derived-data}"
START_IPROXY=1
ALLOW_PROVISIONING=1
DRY_RUN=0
IPROXY_PID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --udid)
      UDID="${2:-}"
      shift 2
      ;;
    --project)
      WDA_PROJECT="${2:-}"
      shift 2
      ;;
    --port)
      WDA_PORT="${2:-}"
      shift 2
      ;;
    --derived-data)
      DERIVED_DATA_PATH="${2:-}"
      shift 2
      ;;
    --no-iproxy)
      START_IPROXY=0
      shift
      ;;
    --no-provisioning)
      ALLOW_PROVISIONING=0
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      START_IPROXY=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cleanup() {
  if [[ -n "${IPROXY_PID}" ]] && kill -0 "${IPROXY_PID}" 2>/dev/null; then
    kill "${IPROXY_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

detect_wda_project() {
  local appium_home="${APPIUM_HOME:-$HOME/.appium}"
  local found=""

  if [[ -d "${appium_home}" ]]; then
    found="$(find "${appium_home}" -name WebDriverAgent.xcodeproj -print -quit 2>/dev/null || true)"
  fi

  if [[ -z "${found}" ]] && command -v npm >/dev/null 2>&1; then
    local npm_root
    npm_root="$(npm root -g 2>/dev/null || true)"
    if [[ -n "${npm_root}" ]]; then
      found="$(find "${npm_root}" -name WebDriverAgent.xcodeproj -print -quit 2>/dev/null || true)"
    fi
  fi

  echo "${found}"
}

detect_udid() {
  if command -v idevice_id >/dev/null 2>&1; then
    idevice_id -l | sed -n '1p'
    return
  fi

  xcrun xctrace list devices 2>/dev/null | awk '
    /^== Devices ==/ { in_devices=1; next }
    /^== Simulators ==/ { in_devices=0 }
    in_devices && /\([0-9A-Fa-f-]{20,}\)$/ && $0 !~ /^mac/ {
      line=$0
      sub(/^.*\(/, "", line)
      sub(/\)$/, "", line)
      print line
      exit
    }
  '
}

start_iproxy() {
  if [[ "${START_IPROXY}" != "1" ]]; then
    return
  fi

  require_cmd iproxy

  if lsof -nP -iTCP:"${WDA_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port ${WDA_PORT} is already listening; assuming an existing WDA forward is running."
    return
  fi

  echo "Starting USB forward: localhost:${WDA_PORT} -> iPhone:8100"
  iproxy -u "${UDID}" "${WDA_PORT}:8100" >/tmp/wordlink-iproxy.log 2>&1 &
  IPROXY_PID="$!"
  sleep 0.8

  if ! kill -0 "${IPROXY_PID}" 2>/dev/null; then
    echo "iproxy failed to start:" >&2
    cat /tmp/wordlink-iproxy.log >&2 || true
    exit 1
  fi
}

require_cmd xcodebuild
require_cmd xcrun

if [[ -z "${WDA_PROJECT}" ]]; then
  WDA_PROJECT="$(detect_wda_project)"
fi

if [[ -z "${WDA_PROJECT}" || ! -d "${WDA_PROJECT}" ]]; then
  cat >&2 <<EOF
Could not find WebDriverAgent.xcodeproj.

Run:
  appium driver install xcuitest
  appium driver run xcuitest open-wda

Or pass:
  scripts/start_wda.sh --project /path/to/WebDriverAgent.xcodeproj
EOF
  exit 1
fi

if [[ -z "${UDID}" ]]; then
  UDID="$(detect_udid)"
fi

if [[ -z "${UDID}" ]]; then
  cat >&2 <<'EOF'
Could not auto-detect a connected iPhone UDID.

Find it with:
  xcrun xctrace list devices

Then run:
  scripts/start_wda.sh --udid YOUR_DEVICE_UDID
EOF
  exit 1
fi

start_iproxy

echo "WDA project: ${WDA_PROJECT}"
echo "Device UDID: ${UDID}"
echo "DerivedData: ${DERIVED_DATA_PATH}"

XCODEBUILD_ARGS=(
  test
  -project "${WDA_PROJECT}"
  -scheme WebDriverAgentRunner
  -destination "id=${UDID}"
  -derivedDataPath "${DERIVED_DATA_PATH}"
  CODE_SIGNING_ALLOWED=YES
  COMPILER_INDEX_STORE_ENABLE=NO
  GCC_TREAT_WARNINGS_AS_ERRORS=0
)

if [[ "${ALLOW_PROVISIONING}" == "1" ]]; then
  XCODEBUILD_ARGS+=(-allowProvisioningUpdates)
fi

if [[ "${DRY_RUN}" == "1" ]]; then
  printf 'xcodebuild'
  printf ' %q' "${XCODEBUILD_ARGS[@]}"
  printf '\n'
  exit 0
fi

echo "Starting WebDriverAgentRunner. Leave this terminal open."
echo "In another terminal, test with: phonectl --no-iproxy status"

xcodebuild "${XCODEBUILD_ARGS[@]}"
