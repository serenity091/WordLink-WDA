#!/usr/bin/env python3
"""Measure direct WDA /wda/dragfromtoforduration request latency."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def parse_args() -> argparse.Namespace:
    config = load_config()
    parser = argparse.ArgumentParser(description="Benchmark WDA native dragfromtoforduration latency.")
    parser.add_argument("--wda-url", default=config.get("WDA_URL", "http://127.0.0.1:8100"))
    parser.add_argument("--count", type=int, default=10, help="Number of measured drags.")
    parser.add_argument("--warmup", type=int, default=2, help="Warmup drags before measuring.")
    parser.add_argument("--interval", type=float, default=0.25, help="Seconds to wait between drags.")
    parser.add_argument("--duration", type=float, default=0.05, help="Native WDA drag duration in seconds.")
    parser.add_argument("--from-x", type=float, default=None)
    parser.add_argument("--from-y", type=float, default=None)
    parser.add_argument("--to-x", type=float, default=None)
    parser.add_argument("--to-y", type=float, default=None)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--compare-actions", action="store_true", help="Also benchmark equivalent W3C /actions drags.")
    return parser.parse_args()


class WDA:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session_id: str | None = None

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(f"{self.base_url}{path}", data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")

        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"WDA {method} {path} failed with HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"WDA {method} {path} failed: {exc}") from exc

        if not raw:
            return None

        parsed = json.loads(raw.decode("utf-8"))
        if isinstance(parsed, dict):
            value = parsed.get("value")
            if isinstance(value, dict) and value.get("error"):
                raise RuntimeError(f"WDA {method} {path} failed: {value.get('message') or value.get('error')}")
            if parsed.get("status") not in (None, 0):
                raise RuntimeError(f"WDA {method} {path} failed: {parsed}")
            if "value" in parsed and set(parsed.keys()).issubset({"value", "sessionId", "status"}):
                return parsed["value"]
        return parsed

    def status(self) -> dict[str, Any]:
        value = self.request("GET", "/status")
        if not isinstance(value, dict):
            raise RuntimeError(f"Unexpected WDA status response: {value!r}")
        return value

    def create_session(self) -> str:
        response = self.request("POST", "/session", {"capabilities": {"alwaysMatch": {}}})
        session_id = None
        if isinstance(response, dict):
            session_id = response.get("sessionId")
            if not session_id and isinstance(response.get("value"), dict):
                session_id = response["value"].get("sessionId")
        if not session_id:
            raise RuntimeError(f"WDA did not return a session id: {response!r}")
        self.session_id = str(session_id)
        return self.session_id

    def session_path(self, suffix: str) -> str:
        if not self.session_id:
            self.create_session()
        return f"/session/{self.session_id}{suffix}"

    def window_size(self) -> dict[str, int]:
        size = self.request("GET", self.session_path("/window/size"))
        if not isinstance(size, dict) or "width" not in size or "height" not in size:
            raise RuntimeError(f"Unexpected window size response: {size!r}")
        return {"width": int(size["width"]), "height": int(size["height"])}

    def native_drag(self, x1: float, y1: float, x2: float, y2: float, duration: float) -> float:
        payload = {"fromX": x1, "fromY": y1, "toX": x2, "toY": y2, "duration": duration}
        started = time.perf_counter()
        self.request("POST", self.session_path("/wda/dragfromtoforduration"), payload)
        return (time.perf_counter() - started) * 1000

    def actions_drag(self, x1: float, y1: float, x2: float, y2: float, duration: float) -> float:
        payload = {
            "actions": [
                {
                    "type": "pointer",
                    "id": "finger1",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0, "x": round(x1), "y": round(y1)},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pointerMove", "duration": round(duration * 1000), "x": round(x2), "y": round(y2)},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        }
        started = time.perf_counter()
        self.request("POST", self.session_path("/actions"), payload)
        return (time.perf_counter() - started) * 1000


def default_points(size: dict[str, int], args: argparse.Namespace) -> tuple[float, float, float, float]:
    width = size["width"]
    height = size["height"]
    x1 = args.from_x if args.from_x is not None else width * 0.35
    y1 = args.from_y if args.from_y is not None else height * 0.55
    x2 = args.to_x if args.to_x is not None else width * 0.65
    y2 = args.to_y if args.to_y is not None else height * 0.55
    return x1, y1, x2, y2


def print_summary(label: str, samples: list[float]) -> None:
    if not samples:
        return
    sorted_samples = sorted(samples)
    p50 = statistics.median(sorted_samples)
    p90 = sorted_samples[min(round(len(sorted_samples) * 0.90) - 1, len(sorted_samples) - 1)]
    print(
        f"{label} summary: "
        f"min={min(samples):.1f}ms "
        f"p50={p50:.1f}ms "
        f"p90={p90:.1f}ms "
        f"max={max(samples):.1f}ms "
        f"mean={statistics.mean(samples):.1f}ms"
    )


def run_drag_series(
    label: str,
    count: int,
    warmup: int,
    interval: float,
    drag_fn,
) -> list[float]:
    samples: list[float] = []
    total = warmup + count
    for index in range(total):
        elapsed_ms = drag_fn()
        measured = index >= warmup
        prefix = f"{label} #{index - warmup + 1:02d}" if measured else f"{label} warmup #{index + 1:02d}"
        print(f"{prefix}: {elapsed_ms:.1f}ms")
        if measured:
            samples.append(elapsed_ms)
        if interval > 0 and index < total - 1:
            time.sleep(interval)
    print_summary(label, samples)
    return samples


def main() -> int:
    args = parse_args()
    wda = WDA(args.wda_url, args.timeout)

    print(f"Checking WDA: {args.wda_url}")
    status = wda.status()
    print(f"WDA status ok: {json.dumps(status)[:240]}")

    session_id = wda.create_session()
    size = wda.window_size()
    x1, y1, x2, y2 = default_points(size, args)

    print(f"Session: {session_id}")
    print(f"Screen size: {size['width']}x{size['height']}")
    print(f"Drag: ({x1:.0f}, {y1:.0f}) -> ({x2:.0f}, {y2:.0f}), duration={args.duration:.3f}s")
    print("Timing below is HTTP request round-trip time, not camera-measured visual start time.")

    run_drag_series(
        "/wda/dragfromtoforduration",
        args.count,
        args.warmup,
        args.interval,
        lambda: wda.native_drag(x1, y1, x2, y2, args.duration),
    )

    if args.compare_actions:
        print()
        run_drag_series(
            "/actions",
            args.count,
            args.warmup,
            args.interval,
            lambda: wda.actions_drag(x1, y1, x2, y2, args.duration),
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
