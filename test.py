#!/usr/bin/env python3
"""Connect to an iPhone and show the QuickTime USB screen stream in OpenCV."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import time
from pathlib import Path
from typing import Any

import cv2

from ios_video_stream import IOSVideoFrameSource


DISPLAY_MAX_HEIGHT = 956
WINDOW_NAME = "QuickTime USB iPhone Stream"


def load_config() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "config.json"
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def resize_for_display(frame: Any) -> Any:
    height, width = frame.shape[:2]
    if height <= DISPLAY_MAX_HEIGHT:
        return frame
    scale = DISPLAY_MAX_HEIGHT / height
    return cv2.resize(frame, (round(width * scale), DISPLAY_MAX_HEIGHT), interpolation=cv2.INTER_AREA)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show the live iPhone QuickTime USB screen stream.")
    parser.add_argument("--udid", help="Device UDID. Defaults to DEVICE_UDID in config.json.")
    parser.add_argument("--max-height", type=int, default=DISPLAY_MAX_HEIGHT, help="Maximum OpenCV display height.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging for USB/protocol messages.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config()
    udid = args.udid or config.get("DEVICE_UDID")
    stop = False

    def handle_sigint(_signal: int, _frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_sigint)

    errors: list[str] = []

    source = IOSVideoFrameSource(
        udid=udid,
        on_error=lambda message: errors.append(message),
    )

    print("Connecting to iPhone over QuickTime USB screen mirroring...")
    print(f"UDID: {udid or '<first compatible Apple USB device>'}")
    source.start()
    print("Stream reader started. Waiting for decoded frames...")
    print("Press q or Ctrl-C to quit")

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)

    shown = 0
    started_at = time.monotonic()
    last_stats_at = started_at
    last_error_printed: str | None = None

    try:
        while not stop:
            frame = source.read_latest(timeout=0.5)
            now = time.monotonic()

            if errors and errors[-1] != last_error_printed:
                last_error_printed = errors[-1]
                print(f"stream error: {last_error_printed}")

            if frame is not None:
                shown += 1
                display_frame = resize_for_display(frame) if args.max_height == DISPLAY_MAX_HEIGHT else resize_to_height(frame, args.max_height)
                cv2.imshow(WINDOW_NAME, display_frame)

            if now - last_stats_at >= 1.0:
                stats = source.stats()
                elapsed = max(now - started_at, 0.001)
                size = f"{stats.width}x{stats.height}" if stats.width and stats.height else "unknown"
                print(
                    f"shown={shown}, fps={shown / elapsed:.1f}, "
                    f"source_frames={stats.frames_received}, "
                    f"video_packets={stats.video_packets_seen}, "
                    f"decoder_errors={stats.decoder_errors}, stream_errors={stats.stream_errors}, "
                    f"stream_size={size}, running={stats.running}"
                )
                if stats.last_error:
                    print(f"last error: {stats.last_error}")
                last_stats_at = now

            if (cv2.waitKey(1) & 0xFF) == ord("q"):
                break
    finally:
        source.stop()
        cv2.destroyAllWindows()

    return 0


def resize_to_height(frame: Any, max_height: int) -> Any:
    if max_height <= 0:
        return frame
    height, width = frame.shape[:2]
    if height <= max_height:
        return frame
    scale = max_height / height
    return cv2.resize(frame, (round(width * scale), max_height), interpolation=cv2.INTER_AREA)


if __name__ == "__main__":
    raise SystemExit(main())
