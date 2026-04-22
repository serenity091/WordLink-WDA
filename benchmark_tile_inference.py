#!/usr/bin/env python3
"""Benchmark tile CNN inference on last_letters.png."""

from __future__ import annotations

import time
from pathlib import Path

import cv2

from read_letters import detect_tile_boxes, normalize_tile_image, tile_cnn_predictor


IMAGE_PATH = Path("last_letters.png")
WARMUP_RUNS = 10


def main() -> int:
    frame = cv2.imread(str(IMAGE_PATH))
    if frame is None:
        raise RuntimeError(f"Could not read {IMAGE_PATH}")

    boxes = detect_tile_boxes(frame)
    predictor = tile_cnn_predictor()
    tiles = [
        (row_index, col_index, normalize_tile_image(frame[y : y + h, x : x + w]))
        for row_index, row in enumerate(boxes)
        for col_index, (x, y, w, h) in enumerate(row)
    ]

    for _, _, tile in tiles[:1] * WARMUP_RUNS:
        predictor(tile)

    results: list[tuple[int, int, str, float]] = []
    total_started = time.perf_counter()
    for row, col, tile in tiles:
        started = time.perf_counter()
        label = predictor(tile)
        elapsed_ms = (time.perf_counter() - started) * 1000
        results.append((row, col, label, elapsed_ms))
    total_ms = (time.perf_counter() - total_started) * 1000

    for row, col, label, elapsed_ms in results:
        print(f"{row},{col} {label:>2} {elapsed_ms:.3f} ms")
    print(f"total {total_ms:.3f} ms")
    print(f"avg {total_ms / max(len(results), 1):.3f} ms/letter")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
