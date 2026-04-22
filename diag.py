#!/usr/bin/env python3
"""Capture a short QuickTime USB screen-mirroring diagnostic."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import cv2

from ios_video_stream import IOSVideoFrameSource


OUTFILE = Path("quicktime_usb_frame_diagnostic.png")


def load_config() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "config.json"
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def main() -> int:
    config = load_config()
    source = IOSVideoFrameSource(
        udid=config.get("DEVICE_UDID"),
        on_error=lambda message: print(f"frame source error: {message}"),
    )
    source.start()

    print("Collecting QuickTime USB screen-mirroring frames for 5 seconds...")
    first_frame = None
    frames_read = 0
    started_at = time.monotonic()

    try:
        while time.monotonic() - started_at < 5.0:
            frame = source.read_latest(timeout=0.5)
            if frame is None:
                continue
            if first_frame is None:
                first_frame = frame
            frames_read += 1
    finally:
        stats = source.stats()
        source.stop()

    elapsed = max(time.monotonic() - started_at, 0.001)
    print(f"Frames read by diag: {frames_read}")
    print(f"Frames captured by source: {stats.frames_received}")
    print(f"Approx read FPS: {frames_read / elapsed:.1f}")
    print(f"Video packets: {stats.video_packets_seen}")
    print(f"Decoder errors: {stats.decoder_errors}")
    print(f"Stream errors: {stats.stream_errors}")
    if stats.last_error:
        print(f"Last error: {stats.last_error}")

    if first_frame is None:
        print("No frame was captured. Confirm the iPhone is connected, trusted, unlocked, and not using QuickTime.")
        return 1

    cv2.imwrite(str(OUTFILE), first_frame)
    print(f"Saved first frame to {OUTFILE}")
    print(f"Frame shape: {first_frame.shape[1]}x{first_frame.shape[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
