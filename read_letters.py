#!/usr/bin/env python3
"""Read the letter board from an iPhone screenshot using OpenCV."""

from __future__ import annotations

import json
import hashlib
from functools import lru_cache
from pathlib import Path
import sys
from typing import Any

import cv2
import numpy as np


def runtime_root() -> Path:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.argv[0]).resolve().parent
    return Path(__file__).resolve().parent


root = runtime_root()
IMAGE_PATH = Path("latest_full.png")
TILE_DATASET_IMAGE_SIZE = 32
TILE_CNN_MODEL_PATH = root / "models/tile_cnn.npz"
QU_DATASET_PATH = root / "tile_dataset/train/QU"
QU_TEMPLATE_MAX_DISTANCE = 0.48
QU_TEMPLATE_MIN_MARGIN_VS_W = 0.12
WAIT_FOR_STABLE_TILES = False
TILE_STABLE_MAX_SIDE_SPREAD = 0.18
TILE_STABLE_MAX_SIDE_CV = 0.08
LETTER_DOT_SCORES = {
    "A": 1,
    "B": 4,
    "C": 2,
    "D": 2,
    "E": 1,
    "F": 4,
    "G": 3,
    "H": 4,
    "I": 1,
    "J": 4,
    "K": 4,
    "L": 1,
    "M": 4,
    "N": 1,
    "O": 2,
    "P": 3,
    "Q": 4,
    "QU": 4,
    "R": 1,
    "S": 1,
    "T": 1,
    "U": 2,
    "V": 4,
    "W": 3,
    "X": 4,
    "Y": 3,
    "Z": 4,
}


class BoardUnstableError(RuntimeError):
    """Raised when tile boxes are visible but still resizing during animation."""


def main() -> int:
    frame = cv2.imread(str(IMAGE_PATH))
    if frame is None:
        raise RuntimeError(f"Could not open {IMAGE_PATH}")

    letters, dots, _ = read_letter_grid_from_frame(frame, return_boxes=True)
    print(json.dumps({"letters": letters, "dots": dots}, indent=2))
    return 0


def read_letter_grid_from_frame(
    frame: np.ndarray,
    return_boxes: bool = False,
) -> list[list[str]] | tuple[list[list[str]], list[list[int]], list[list[tuple[int, int, int, int]]]]:
    boxes = detect_tile_boxes(frame)
    if WAIT_FOR_STABLE_TILES:
        ensure_stable_tile_boxes(boxes)
    letters: list[list[str]] = []
    dots: list[list[int]] = []

    for row in boxes:
        letter_row = [recognize_tile_text(frame[y : y + h, x : x + w]) for x, y, w, h in row]
        letters.append(letter_row)
        dots.append([tile_dot_score(letter) for letter in letter_row])

    if return_boxes:
        return letters, dots, boxes
    return letters


def detect_tile_boxes(frame: np.ndarray) -> list[list[tuple[int, int, int, int]]]:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 0, 100), (179, 120, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    raw_boxes: list[tuple[int, int, int, int]] = []
    frame_area = frame.shape[0] * frame.shape[1]
    min_tile_area = max(2500, frame_area * 0.006)
    max_tile_area = frame_area * 0.045

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        aspect = w / h
        if min_tile_area < area < max_tile_area and 0.75 < aspect < 1.35:
            raw_boxes.append((x, y, w, h))

    boxes = split_merged_tile_boxes(contours, raw_boxes, frame_area)
    boxes = dedupe_tile_boxes(boxes)

    if len(boxes) < 16:
        raise RuntimeError(f"Expected 16 letter tiles, found {len(boxes)}")

    boxes = sorted(boxes, key=lambda box: (box[1], box[0]))
    rows: list[list[tuple[int, int, int, int]]] = []
    row_tolerance = max(45, median_tile_side(boxes) * 0.35)

    for box in boxes:
        center_y = box[1] + box[3] / 2
        for row in rows:
            row_center_y = row[0][1] + row[0][3] / 2
            if abs(row_center_y - center_y) < row_tolerance:
                row.append(box)
                break
        else:
            rows.append([box])

    rows = [sorted(row, key=lambda box: box[0]) for row in rows]
    rows = sorted(rows, key=lambda row: sum(box[1] + box[3] / 2 for box in row) / len(row))

    if len(rows) != 4 or any(len(row) != 4 for row in rows):
        raise RuntimeError(f"Expected a 4x4 tile grid, found row lengths {[len(row) for row in rows]}")

    return rows


def ensure_stable_tile_boxes(box_rows: list[list[tuple[int, int, int, int]]]) -> None:
    boxes = [box for row in box_rows for box in row]
    if len(boxes) != 16:
        return

    sides = np.array([np.sqrt(width * height) for _, _, width, height in boxes], dtype=np.float32)
    median = float(np.median(sides))
    if median <= 0:
        raise BoardUnstableError("Tiles still animating")

    side_spread = float((sides.max() - sides.min()) / median)
    side_cv = float(sides.std() / median)
    if side_spread > TILE_STABLE_MAX_SIDE_SPREAD or side_cv > TILE_STABLE_MAX_SIDE_CV:
        raise BoardUnstableError(f"Tiles still animating: size spread={side_spread:.2f}, cv={side_cv:.2f}")


def split_merged_tile_boxes(
    contours: list[np.ndarray],
    single_boxes: list[tuple[int, int, int, int]],
    frame_area: int,
) -> list[tuple[int, int, int, int]]:
    boxes = list(single_boxes)
    tile_side = median_tile_side(single_boxes)
    if tile_side <= 0:
        return boxes

    min_merged_area = max(frame_area * 0.045, tile_side * tile_side * 1.7)
    max_merged_area = frame_area * 0.20

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        aspect = w / h if h else 0
        if not (min_merged_area <= area <= max_merged_area and 0.55 < aspect < 1.8):
            continue

        cols = round(w / tile_side)
        rows = round(h / tile_side)
        if cols * rows <= 1 or cols > 4 or rows > 4:
            continue

        cell_w = w / cols
        cell_h = h / rows
        if not (0.75 * tile_side <= cell_w <= 1.35 * tile_side):
            continue
        if not (0.75 * tile_side <= cell_h <= 1.35 * tile_side):
            continue

        for row in range(rows):
            for col in range(cols):
                boxes.append(
                    (
                        round(x + col * cell_w),
                        round(y + row * cell_h),
                        round(cell_w),
                        round(cell_h),
                    )
                )

    return boxes


def median_tile_side(boxes: list[tuple[int, int, int, int]]) -> float:
    if not boxes:
        return 0.0
    return float(np.median([max(width, height) for _, _, width, height in boxes]))


def dedupe_tile_boxes(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    ordered = sorted(boxes, key=lambda box: box[2] * box[3])
    result: list[tuple[int, int, int, int]] = []
    for box in ordered:
        center_x = box[0] + box[2] / 2
        center_y = box[1] + box[3] / 2
        side = max(box[2], box[3])
        duplicate = False
        for existing in result:
            existing_center_x = existing[0] + existing[2] / 2
            existing_center_y = existing[1] + existing[3] / 2
            existing_side = max(existing[2], existing[3])
            if abs(center_x - existing_center_x) < min(side, existing_side) * 0.45 and abs(
                center_y - existing_center_y
            ) < min(side, existing_side) * 0.45:
                duplicate = True
                break
        if not duplicate:
            result.append(box)
    return result


def recognize_tile_text(tile: np.ndarray) -> str:
    return recognize_tile_text_with_cnn(tile)


def recognize_tile_text_with_cnn(tile: np.ndarray) -> str:
    predictor = tile_cnn_predictor()
    return predictor(normalize_tile_image(tile))


@lru_cache(maxsize=1)
def tile_cnn_predictor() -> Any:
    if not TILE_CNN_MODEL_PATH.exists():
        raise RuntimeError(f"Missing tile CNN model: {TILE_CNN_MODEL_PATH}")

    checkpoint = np.load(TILE_CNN_MODEL_PATH, allow_pickle=False)
    classes = checkpoint["classes"].astype(str).tolist()
    if int(checkpoint["channels"][0]) != 1:
        raise RuntimeError(f"Unsupported tile CNN model format in {TILE_CNN_MODEL_PATH}")

    weights = {
        "conv1_weight": checkpoint["features_0_weight"],
        "conv1_bias": checkpoint["features_0_bias"],
        "conv2_weight": checkpoint["features_3_weight"],
        "conv2_bias": checkpoint["features_3_bias"],
        "conv3_weight": checkpoint["features_6_weight"],
        "conv3_bias": checkpoint["features_6_bias"],
        "linear_weight": checkpoint["classifier_weight"],
        "linear_bias": checkpoint["classifier_bias"],
    }

    def predict(tile_image: np.ndarray) -> str:
        x = tile_image.astype(np.float32)[None, :, :] / 255.0
        x = np.maximum(conv2d_same(x, weights["conv1_weight"], weights["conv1_bias"]), 0)
        x = max_pool2d(x)
        x = np.maximum(conv2d_same(x, weights["conv2_weight"], weights["conv2_bias"]), 0)
        x = max_pool2d(x)
        x = np.maximum(conv2d_same(x, weights["conv3_weight"], weights["conv3_bias"]), 0)
        x = x.mean(axis=(1, 2))
        logits = weights["linear_weight"] @ x + weights["linear_bias"]
        index = int(np.argmax(logits))
        return str(classes[index])

    return predict


def conv2d_same(x: np.ndarray, weight: np.ndarray, bias: np.ndarray) -> np.ndarray:
    padded = np.pad(x, ((0, 0), (1, 1), (1, 1)), mode="constant")
    windows = np.lib.stride_tricks.sliding_window_view(padded, (3, 3), axis=(1, 2))
    return np.einsum("chwkl,ockl->ohw", windows, weight, optimize=True) + bias[:, None, None]


def max_pool2d(x: np.ndarray) -> np.ndarray:
    channels, height, width = x.shape
    return x.reshape(channels, height // 2, 2, width // 2, 2).max(axis=(2, 4))


def recognize_tile_letter(tile: np.ndarray) -> str:
    return recognize_tile_text(tile)


def tile_dot_score(tile_text: str) -> int:
    return LETTER_DOT_SCORES.get(normalize_tile_label(tile_text), 1)


def normalize_tile_label(tile_text: Any) -> str:
    label = str(tile_text).strip().upper().replace(" ", "")
    if label == "Q":
        return "QU"
    return label if label.isalpha() else ""


def save_qu_dataset_examples(
    frame: np.ndarray,
    boxes: list[list[tuple[int, int, int, int]]],
    letters: list[list[str]] | None = None,
    dataset_path: Path = QU_DATASET_PATH,
) -> int:
    saved = 0
    for row in boxes:
        for x, y, w, h in row:
            raw_tile = frame[y : y + h, x : x + w]
            if not is_qu_tile_by_template(raw_tile):
                continue

            tile = normalize_tile_image(raw_tile)
            digest = hashlib.sha1(tile.tobytes()).hexdigest()
            dataset_path.mkdir(parents=True, exist_ok=True)
            output_path = dataset_path / f"{digest[:16]}.png"
            if not output_path.exists():
                cv2.imwrite(str(output_path), tile)
                saved += 1
    return saved


def is_qu_tile_by_template(tile: np.ndarray) -> bool:
    mask = extract_letter_mask(tile)
    qu_distance = min(mask_distance(mask, template) for template in qu_templates())
    w_distance = min(mask_distance(mask, template) for template in w_templates())
    return qu_distance <= QU_TEMPLATE_MAX_DISTANCE and qu_distance + QU_TEMPLATE_MIN_MARGIN_VS_W < w_distance


@lru_cache(maxsize=1)
def qu_templates() -> tuple[np.ndarray, ...]:
    fonts = (
        cv2.FONT_HERSHEY_SIMPLEX,
        cv2.FONT_HERSHEY_DUPLEX,
        cv2.FONT_HERSHEY_TRIPLEX,
        cv2.FONT_HERSHEY_COMPLEX,
    )
    templates: list[np.ndarray] = []
    for font in fonts:
        for scale in (2.0, 2.3, 2.6, 2.9, 3.2):
            for thickness in (4, 5, 6, 7, 8):
                templates.append(render_template_mask("Qu", font, scale, thickness))
    return tuple(templates)


@lru_cache(maxsize=1)
def w_templates() -> tuple[np.ndarray, ...]:
    fonts = (
        cv2.FONT_HERSHEY_SIMPLEX,
        cv2.FONT_HERSHEY_DUPLEX,
        cv2.FONT_HERSHEY_TRIPLEX,
        cv2.FONT_HERSHEY_COMPLEX,
    )
    templates: list[np.ndarray] = []
    for font in fonts:
        for scale in (3.2, 3.6, 4.0, 4.4):
            for thickness in (5, 7, 9, 11):
                templates.append(render_template_mask("W", font, scale, thickness))
    return tuple(templates)


def render_template_mask(text: str, font: int, scale: float, thickness: int) -> np.ndarray:
    canvas = np.zeros((220, 220), dtype=np.uint8)
    (text_width, text_height), baseline = cv2.getTextSize(text, font, scale, thickness)
    x = (220 - text_width) // 2
    y = (220 + text_height - baseline) // 2
    cv2.putText(canvas, text, (x, y), font, scale, 255, thickness, cv2.LINE_AA)

    ys, xs = np.where(canvas > 0)
    glyph = canvas[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    return normalize_mask(glyph)


def mask_distance(mask: np.ndarray, template: np.ndarray) -> float:
    left = mask > 0
    right = template > 0
    union = np.logical_or(left, right).sum()
    if union == 0:
        return 1.0
    intersection = np.logical_and(left, right).sum()
    return 1.0 - float(intersection / union)


def normalize_tile_image(tile: np.ndarray) -> np.ndarray:
    mask = extract_letter_mask(tile)
    return cv2.resize(mask, (TILE_DATASET_IMAGE_SIZE, TILE_DATASET_IMAGE_SIZE), interpolation=cv2.INTER_AREA)


def extract_letter_mask(tile: np.ndarray) -> np.ndarray:
    height, width = tile.shape[:2]
    roi = tile[int(height * 0.12) : int(height * 0.64), int(width * 0.12) : int(width * 0.88)]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    mask = cv2.inRange(gray, 0, 80)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    bounds: list[tuple[int, int, int, int]] = []
    for contour in contours:
        if cv2.contourArea(contour) > 200:
            x, y, w, h = cv2.boundingRect(contour)
            bounds.append((x, y, x + w, y + h))

    if not bounds:
        return np.zeros((120, 120), dtype=np.uint8)

    x1 = max(0, min(bound[0] for bound in bounds) - 8)
    y1 = max(0, min(bound[1] for bound in bounds) - 8)
    x2 = min(mask.shape[1], max(bound[2] for bound in bounds) + 8)
    y2 = min(mask.shape[0], max(bound[3] for bound in bounds) + 8)

    return normalize_mask(mask[y1:y2, x1:x2])


def normalize_mask(mask: np.ndarray) -> np.ndarray:
    canvas = np.zeros((120, 120), dtype=np.uint8)
    height, width = mask.shape[:2]
    if height == 0 or width == 0:
        return canvas

    scale = min(100 / width, 100 / height)
    resized = cv2.resize(
        mask,
        (max(1, int(width * scale)), max(1, int(height * scale))),
        interpolation=cv2.INTER_NEAREST,
    )
    y = (120 - resized.shape[0]) // 2
    x = (120 - resized.shape[1]) // 2
    canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
    return canvas


def draw_debug_overlay(
    frame: np.ndarray,
    boxes: list[list[tuple[int, int, int, int]]],
    letters: list[list[str]],
    dots: list[list[int]] | None = None,
    highlight_path: list[tuple[int, int]] | None = None,
) -> np.ndarray:
    output = frame.copy()
    highlighted = set(highlight_path or [])
    for row_index, row in enumerate(boxes):
        for col_index, (x, y, w, h) in enumerate(row):
            letter = letters[row_index][col_index]
            dot_text = "" if dots is None else f":{dots[row_index][col_index]}"
            is_highlighted = (row_index, col_index) in highlighted
            color = (0, 255, 255) if is_highlighted else (0, 255, 0)
            thickness = 10 if is_highlighted else 4
            cv2.rectangle(output, (x, y), (x + w, y + h), color, thickness)
            cv2.putText(output, f"{letter}{dot_text}", (x + 20, y + 70), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 255), 5)

    if highlight_path:
        centers = []
        for row_index, col_index in highlight_path:
            x, y, w, h = boxes[row_index][col_index]
            centers.append((x + w // 2, y + h // 2))
        for start, end in zip(centers, centers[1:]):
            cv2.line(output, start, end, (0, 255, 255), 8)

    return output


if __name__ == "__main__":
    raise SystemExit(main())
