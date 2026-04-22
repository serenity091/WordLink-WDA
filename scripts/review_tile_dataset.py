#!/usr/bin/env python3
"""Review tile_dataset images with OpenCV and delete bad samples."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


DATASET_PATH = Path("../tile_dataset")
WINDOW_NAME = "Tile Dataset Review"
DISPLAY_SCALE = 10


def main() -> int:
    samples = load_samples()
    if not samples:
        print("No PNG files found in tile_dataset")
        return 0

    index = 0
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)

    while samples:
        index %= len(samples)
        path = samples[index]
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            samples.pop(index)
            continue

        cv2.imshow(WINDOW_NAME, render_review_image(image, path, index, len(samples)))
        key = cv2.waitKeyEx(0)

        if key in (27, ord("q")):
            break
        if key in (83, 2555904, ord("d"), ord("n")):
            index += 1
            continue
        if key in (81, 2424832, ord("a"), ord("p")):
            index -= 1
            continue
        if key in (8, 127, ord("x")):
            print(f"deleted {path}")
            path.unlink(missing_ok=True)
            samples.pop(index)
            continue

    cv2.destroyAllWindows()
    return 0


def load_samples() -> list[Path]:
    return sorted(path for path in DATASET_PATH.rglob("*.png") if "__MACOSX" not in path.parts)


def render_review_image(image: np.ndarray, path: Path, index: int, total: int) -> np.ndarray:
    image = cv2.resize(image, None, fx=DISPLAY_SCALE, fy=DISPLAY_SCALE, interpolation=cv2.INTER_NEAREST)
    canvas = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    header_height = 90
    header = np.full((header_height, canvas.shape[1], 3), 245, dtype=np.uint8)
    output = np.vstack([header, canvas])

    label = path.parent.name
    split = path.parent.parent.name
    cv2.putText(output, f"{index + 1}/{total}  {split}/{label}", (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    cv2.putText(output, path.name, (12, 64), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (60, 60, 60), 1)
    return output


if __name__ == "__main__":
    raise SystemExit(main())
