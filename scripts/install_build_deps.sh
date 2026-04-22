#!/usr/bin/env bash
set -euo pipefail

echo "Installing Python build dependencies..."
python -m pip install --upgrade pip
python -m pip install \
  av \
  numpy \
  opencv-python \
  pyusb \
  nuitka

python - <<'PY'
import importlib.util

missing = [
    name
    for name in ("av", "cv2", "numpy", "usb", "nuitka")
    if importlib.util.find_spec(name) is None
]
if missing:
    raise SystemExit("Still missing build dependencies: " + ", ".join(missing))
print("Build dependency check ok.")
PY

echo "Done. Run ./scripts/build_nuitka.sh to build the packaged folder."
