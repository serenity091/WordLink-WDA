#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/update_wda_provision.sh [options]

Embeds a provisioning profile into WebDriverAgentRunner-Runner.app and re-signs
the nested framework, test bundle, and app bundle in the correct order.

Options:
  --app PATH             Path to WebDriverAgentRunner-Runner.app.
                         Default: WebDriverAgentRunner-Runner.app
  --profile PATH         Path to the .mobileprovision file to embed.
                         Default: WebDriverRunner.mobileprovision
  --identity NAME        Explicit signing identity. Defaults to the app's
                         current signing identity.
  -h, --help             Show this help.
EOF
}

APP_PATH="WebDriverAgentRunner-Runner.app"
PROFILE_PATH="WebDriverRunner.mobileprovision"
SIGNING_IDENTITY="${WDA_CODESIGN_IDENTITY:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app)
      APP_PATH="${2:-}"
      shift 2
      ;;
    --profile)
      PROFILE_PATH="${2:-}"
      shift 2
      ;;
    --identity)
      SIGNING_IDENTITY="${2:-}"
      shift 2
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

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

plist_value() {
  local plist_path="$1"
  local key_path="$2"
  /usr/libexec/PlistBuddy -c "Print :${key_path}" "$plist_path"
}

normalize_path() {
  python3 - "$1" <<'PY'
from pathlib import Path
import sys

print(Path(sys.argv[1]).resolve())
PY
}

profile_field() {
  local profile_path="$1"
  local key_path="$2"

  python3 - "$profile_path" "$key_path" <<'PY'
from pathlib import Path
import plistlib
import subprocess
import sys

profile_path = Path(sys.argv[1])
key_path = sys.argv[2].split(".")

decoded = subprocess.check_output(
    ["security", "cms", "-D", "-i", str(profile_path)],
    text=False,
)
plist = plistlib.loads(decoded)

value = plist
for part in key_path:
    value = value[part]

if isinstance(value, bool):
    print("true" if value else "false")
elif isinstance(value, list):
    for item in value:
        print(item)
else:
    print(value)
PY
}

bundle_id_matches_profile() {
  local application_identifier="$1"
  local bundle_id="$2"
  local suffix="${application_identifier#*.}"

  if [[ "$suffix" == "*" ]]; then
    return 0
  fi

  [[ "$suffix" == "$bundle_id" ]]
}

detect_signing_identity() {
  local app_path="$1"
  codesign -dvv "$app_path" 2>&1 | sed -n 's/^Authority=//p' | awk '
    $0 !~ /^Apple Worldwide Developer Relations/ && $0 !~ /^Apple Root CA$/ {
      print
      exit
    }
  '
}

sign_path() {
  local path="$1"
  echo "Signing $(basename "$path")"
  codesign \
    --force \
    --sign "$SIGNING_IDENTITY" \
    --timestamp=none \
    --generate-entitlement-der \
    --preserve-metadata=identifier,entitlements,requirements,flags \
    "$path"
}

require_cmd codesign
require_cmd python3
require_cmd security

APP_PATH="$(normalize_path "$APP_PATH")"
PROFILE_PATH="$(normalize_path "$PROFILE_PATH")"

if [[ ! -d "$APP_PATH" ]]; then
  echo "Runner app not found: $APP_PATH" >&2
  exit 1
fi

if [[ ! -f "$PROFILE_PATH" ]]; then
  echo "Provisioning profile not found: $PROFILE_PATH" >&2
  exit 1
fi

APP_INFO_PLIST="$APP_PATH/Info.plist"
XCTEST_PATH="$APP_PATH/PlugIns/WebDriverAgentRunner.xctest"
FRAMEWORK_PATH="$XCTEST_PATH/Frameworks/WebDriverAgentLib.framework"

if [[ ! -f "$APP_INFO_PLIST" ]]; then
  echo "App Info.plist not found: $APP_INFO_PLIST" >&2
  exit 1
fi

if [[ ! -d "$XCTEST_PATH" ]]; then
  echo "Missing test bundle: $XCTEST_PATH" >&2
  exit 1
fi

if [[ ! -d "$FRAMEWORK_PATH" ]]; then
  echo "Missing framework: $FRAMEWORK_PATH" >&2
  exit 1
fi

APP_BUNDLE_ID="$(plist_value "$APP_INFO_PLIST" "CFBundleIdentifier")"
PROFILE_NAME="$(profile_field "$PROFILE_PATH" "Name")"
PROFILE_UUID="$(profile_field "$PROFILE_PATH" "UUID")"
PROFILE_TEAM_ID="$(profile_field "$PROFILE_PATH" "TeamIdentifier" | sed -n '1p')"
PROFILE_APP_ID="$(profile_field "$PROFILE_PATH" "Entitlements.application-identifier")"

if ! bundle_id_matches_profile "$PROFILE_APP_ID" "$APP_BUNDLE_ID"; then
  cat >&2 <<EOF
Provisioning profile application identifier does not match the runner app.
  app bundle id: $APP_BUNDLE_ID
  profile app id: $PROFILE_APP_ID
EOF
  exit 1
fi

if [[ -z "$SIGNING_IDENTITY" ]]; then
  SIGNING_IDENTITY="$(detect_signing_identity "$APP_PATH")"
fi

if [[ -z "$SIGNING_IDENTITY" ]]; then
  cat >&2 <<EOF
Could not detect a signing identity from the existing runner app.
Pass one explicitly with:
  scripts/update_wda_provision.sh --identity "Apple Distribution: Alteru Inc. (G2CS7P92FP)"
EOF
  exit 1
fi

echo "Updating WDA runner provisioning profile"
echo "App: $APP_PATH"
echo "Profile: $PROFILE_PATH"
echo "Profile name: $PROFILE_NAME"
echo "Profile UUID: $PROFILE_UUID"
echo "Profile team: $PROFILE_TEAM_ID"
echo "Signing identity: $SIGNING_IDENTITY"

cp "$PROFILE_PATH" "$APP_PATH/embedded.mobileprovision"

sign_path "$FRAMEWORK_PATH"
sign_path "$XCTEST_PATH"
sign_path "$APP_PATH"

codesign --verify --deep --strict --verbose=2 "$APP_PATH"

echo "Updated embedded.mobileprovision and re-signed WebDriverAgentRunner-Runner.app"
