#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="$(command -v python)"
XCODE_DEVELOPER_DIR="$(xcode-select -p)"
XCODE_TOOLCHAIN_BIN="$XCODE_DEVELOPER_DIR/Toolchains/XcodeDefault.xctoolchain/usr/bin"

if [[ ! -x "$XCODE_TOOLCHAIN_BIN/install_name_tool" ]]; then
  echo "Could not find Xcode install_name_tool at: $XCODE_TOOLCHAIN_BIN/install_name_tool"
  exit 1
fi

# Anaconda can put a wrapper install_name_tool first in PATH. Nuitka treats that
# wrapper's macOS code-signing warning as fatal. Keep /usr/bin first so Apple's
# clang gets normal SDK behavior, then put Xcode's install_name_tool before Conda.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$XCODE_TOOLCHAIN_BIN:/opt/homebrew/bin:/usr/local/bin:$PATH"
export SDKROOT="${SDKROOT:-$(xcrun --sdk macosx --show-sdk-path)}"

"$PYTHON_BIN" -m pip show nuitka >/dev/null 2>&1 || "$PYTHON_BIN" -m pip install nuitka
"$PYTHON_BIN" - <<'PY'
import importlib.util
import sys

missing = [
    name
    for name in ("av", "cv2", "numpy", "usb")
    if importlib.util.find_spec(name) is None
]
if missing:
    raise SystemExit(
        "Missing build dependencies: "
        + ", ".join(missing)
        + "\nRun: ./scripts/install_build_deps.sh"
    )
PY

BUILD_DIR="dist-nuitka"
APP_DIR="$BUILD_DIR/start_wda.dist"

rm -rf "$BUILD_DIR"

"$PYTHON_BIN" -m nuitka \
  --mode=standalone \
  --follow-imports \
  --nofollow-import-to=av \
  --nofollow-import-to=cython \
  --nofollow-import-to=Cython \
  --no-deployment-flag=excluded-module-usage \
  --include-package=cv2 \
  --include-package=usb \
  --output-dir="$BUILD_DIR" \
  start_wda.py

APP_DIR_ABS="$(cd "$APP_DIR" && pwd)"

"$PYTHON_BIN" - "$APP_DIR/run.sh" <<'PY'
from pathlib import Path
import sys

Path(sys.argv[1]).write_text("""#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export KMP_DUPLICATE_LIB_OK="${KMP_DUPLICATE_LIB_OK:-TRUE}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
./start_wda.bin
""", encoding="utf-8")
PY
chmod +x "$APP_DIR/run.sh"

echo "Created $APP_DIR/run.sh"

cp scripts/prepare_dist_permissions.sh "$APP_DIR/prepare_permissions.sh"
chmod +x "$APP_DIR/prepare_permissions.sh"

"$PYTHON_BIN" - "$APP_DIR_ABS" <<'PY'
from pathlib import Path
import importlib.util
import shutil
import sys

app_dir = Path(sys.argv[1])

def copy_installed_package(package_name: str, target_name: object = None) -> Path:
    spec = importlib.util.find_spec(package_name)
    if spec is None or spec.origin is None:
        raise SystemExit(f"Could not locate installed package: {package_name}")

    source_dir = Path(spec.origin).resolve().parent
    target_dir = app_dir / (target_name or package_name)
    target_dir.mkdir(parents=True, exist_ok=True)

    for item in source_dir.iterdir():
        target = target_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    return target_dir

cv2_dir = copy_installed_package("cv2")
av_dir = copy_installed_package("av")

for native_binary in list(av_dir.rglob("*.so")):
    stem = native_binary.name.split(".")[0]
    source_file = native_binary.with_name(f"{stem}.py")
    if source_file.exists():
        source_file.unlink()

for pycache in av_dir.rglob("__pycache__"):
    shutil.rmtree(pycache, ignore_errors=True)

for binary in cv2_dir.rglob("cv2*.so"):
    if binary.name != "cv2.so":
        shutil.copy2(binary, cv2_dir / "cv2.so")
        break

if not (cv2_dir / "cv2.so").exists():
    raise SystemExit(f"OpenCV binary was not copied into {cv2_dir}")

if not any(av_dir.glob("_core*.so")):
    raise SystemExit(f"PyAV native extensions were not copied into {av_dir}")
PY

cp config.json "$APP_DIR/config.json"

if [[ -f failed_words.json ]]; then
  cp failed_words.json "$APP_DIR/failed_words.json"
else
  printf '[]\n' > "$APP_DIR/failed_words.json"
fi

mkdir -p "$APP_DIR/models"
cp models/tile_cnn.npz "$APP_DIR/models/tile_cnn.npz"
cp -R data "$APP_DIR/data"

mkdir -p "$APP_DIR/lib"
if [[ -f /opt/homebrew/lib/libusb-1.0.dylib ]]; then
  cp /opt/homebrew/lib/libusb-1.0.dylib "$APP_DIR/lib/libusb-1.0.dylib"
elif [[ -f /usr/local/lib/libusb-1.0.dylib ]]; then
  cp /usr/local/lib/libusb-1.0.dylib "$APP_DIR/lib/libusb-1.0.dylib"
else
  echo "libusb-1.0.dylib not found. Install it on the build machine with: brew install libusb"
  exit 1
fi

if [[ -d WebDriverAgentRunner-Runner.app ]]; then
  cp -R WebDriverAgentRunner-Runner.app "$APP_DIR/WebDriverAgentRunner-Runner.app"
fi

for required in \
  "$APP_DIR/start_wda.bin" \
  "$APP_DIR/run.sh" \
  "$APP_DIR/prepare_permissions.sh" \
  "$APP_DIR/config.json" \
  "$APP_DIR/failed_words.json" \
  "$APP_DIR/models/tile_cnn.npz" \
  "$APP_DIR/data/scowl_words.txt" \
  "$APP_DIR/lib/libusb-1.0.dylib" \
  "$APP_DIR/cv2/cv2.so"
do
  if [[ ! -e "$required" ]]; then
    echo "Missing packaged file: $required"
    exit 1
  fi
done

"$PYTHON_BIN" - "$APP_DIR_ABS" <<'PY'
from pathlib import Path
import sys

av_dir = Path(sys.argv[1]) / "av"
if not any(av_dir.glob("_core*.so")):
    raise SystemExit(f"Missing PyAV native extension in {av_dir}: _core*.so")

shadowing_sources = []
for native_binary in av_dir.rglob("*.so"):
    source_file = native_binary.with_name(native_binary.name.split(".")[0] + ".py")
    if source_file.exists():
        shadowing_sources.append(str(source_file))

if shadowing_sources:
    raise SystemExit(
        "PyAV source files still shadow native extensions:\n"
        + "\n".join(shadowing_sources)
    )
PY

(
  cd "$APP_DIR"
  ./start_wda.bin --packaging-smoke-test
)

echo "Built: $APP_DIR"
echo "Edit config.json and failed_words.json inside that folder before running ./run.sh."
