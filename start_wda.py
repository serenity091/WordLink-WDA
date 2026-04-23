#!/usr/bin/env python3
"""Start WDA on your iPhone and show an OpenCV screenshot viewer."""

from __future__ import annotations

import os

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import json
from pathlib import Path
import random
import re
import shutil
import signal
import subprocess
import sys
import time
from contextlib import suppress
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ios_video_stream import IOSVideoFrameSource
from read_letters import (
    BoardUnstableError,
    draw_debug_overlay,
    read_letter_grid_from_frame,
    save_qu_dataset_examples,
)
from solve_words import solve_board


def runtime_root() -> Path:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.argv[0]).resolve().parent
    return Path(__file__).resolve().parent


root = runtime_root()
CONFIG_PATH = root / "config.json"

FAILED_WORDS_PATH = root / "failed_words.json"
LAST_DETECTION_FAILURE_PATH = root / "last_detection_failure.png"
LAST_DETECTION_FAILURE_DEBUG_PATH = root / "last_detection_failure_debug.png"
LAST_DETECTION_FAILURE_INFO_PATH = root / "last_detection_failure.json"


def load_config() -> None:
    global APPIUM_URL

    if not CONFIG_PATH.exists():
        raise RuntimeError(f"Missing config file: {CONFIG_PATH}")

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise RuntimeError(f"{CONFIG_PATH} must contain a JSON object")

    globals().update(config)

    APPIUM_URL = f"http://{APPIUM_HOST}:{APPIUM_PORT}"


load_config()

def main() -> int:
    if "--packaging-smoke-test" in sys.argv:
        return packaging_smoke_test()

    require("appium", "Install it with: npm install -g appium")
    udid = DEVICE_UDID or detect_udid()

    appium_process = None
    appium_session: AppiumSession | None = None

    def shutdown(*_: object) -> None:
        print("\nStopping...")
        with suppress(Exception):
            if appium_session:
                appium_session.quit()
        with suppress(Exception):
            if appium_process:
                appium_process.terminate()
                appium_process.wait(timeout=5)
        raise SystemExit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    wda_status = get_wda_status(timeout=2.0)
    if wda_status is not None:
        print(f"Using existing WDA at {WDA_URL}")
    else:
        if not appium_is_running():
            appium_process = start_appium()
            wait_for_appium()
        else:
            print(f"Using existing Appium server at {APPIUM_URL}")

        print(f"Starting WDA on device {udid}...")
        print(f"WDA bundle id: {WDA_BUNDLE_ID}")
        print(f"Use preinstalled WDA: {USE_PREINSTALLED_WDA}")
        print(f"Force new WDA launch: {FORCE_NEW_WDA}")
        print("Creating Appium session...")
        appium_session = create_appium_session(udid)
        wda_status = wait_for_wda()

    print("WDA is running.")
    print("Leave this script open. Press Ctrl-C to stop.")
    if appium_session is not None:
        print(f"Appium session id: {appium_session.session_id}")
    print(f"WDA status: {json.dumps(wda_status)}")

    run_screenshot_viewer(udid)
    return 0


def log(message: str) -> None:
    if VERBOSE_LOGS:
        print(message)


def perf(message: str) -> None:
    if PERF_LOGS:
        print(message)


def perf_ms(started: float) -> float:
    return (time.monotonic() - started) * 1000


def reached_solve_limit(solved_words: int) -> bool:
    return MAX_SOLVED_WORDS_PER_GAME is not None and solved_words >= MAX_SOLVED_WORDS_PER_GAME


def packaging_smoke_test() -> int:
    import importlib

    importlib.import_module("av")
    import cv2  # noqa: F401
    import numpy as np
    import usb.backend.libusb1  # noqa: F401

    from read_letters import tile_cnn_predictor
    from solve_words import dictionary_trie

    for path in (
        CONFIG_PATH,
        root / "models/tile_cnn.npz",
        root / "data/scowl_words.txt",
    ):
        if not path.exists():
            raise RuntimeError(f"Packaging smoke test missing: {path}")

    libusb_paths = (
        root / "lib/libusb-1.0.dylib",
        Path("/opt/homebrew/lib/libusb-1.0.dylib"),
        Path("/usr/local/lib/libusb-1.0.dylib"),
    )
    if not any(path.exists() for path in libusb_paths):
        raise RuntimeError("Packaging smoke test missing libusb-1.0.dylib in bundled lib/ or Homebrew")

    IOSVideoFrameSource(udid=DEVICE_UDID)
    predictor = tile_cnn_predictor()
    predictor(np.zeros((32, 32), dtype=np.uint8))
    dictionary_trie()
    print("packaging smoke test ok")
    return 0


def require(command: str, hint: str) -> None:
    if shutil.which(command):
        return
    print(f"Missing required command: {command}", file=sys.stderr)
    print(hint, file=sys.stderr)
    raise SystemExit(1)


def detect_udid() -> str:
    if shutil.which("idevice_id"):
        result = subprocess.run(["idevice_id", "-l"], text=True, capture_output=True, check=False)
        for line in result.stdout.splitlines():
            udid = line.strip()
            if udid:
                return udid

    result = subprocess.run(
        ["xcrun", "xctrace", "list", "devices"],
        text=True,
        capture_output=True,
        check=False,
    )
    in_devices = False
    for line in result.stdout.splitlines():
        if line.startswith("== Devices =="):
            in_devices = True
            continue
        if line.startswith("== Simulators =="):
            break
        if in_devices and not line.lower().startswith("mac"):
            match = re.search(r"\(([0-9A-Fa-f-]{20,})\)\s*$", line)
            if match:
                return match.group(1)

    raise RuntimeError("Could not auto-detect a connected iPhone UDID")


def appium_is_running() -> bool:
    try:
        with urlopen(f"{APPIUM_URL}/status", timeout=1.0) as response:
            return response.status == 200
    except URLError:
        return False


def start_appium() -> subprocess.Popen[bytes]:
    print(f"Starting Appium at {APPIUM_URL}...")
    return subprocess.Popen(
        ["appium", "--address", APPIUM_HOST, "--port", str(APPIUM_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        text=True,
    )


def wait_for_appium(timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if appium_is_running():
            return
        time.sleep(0.5)
    raise RuntimeError(f"Appium did not start within {timeout:.0f}s")


class AppiumSession:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id

    def quit(self) -> None:
        request = Request(f"{APPIUM_URL}/session/{self.session_id}", method="DELETE")
        with suppress(Exception):
            urlopen(request, timeout=10).read()


def create_appium_session(udid: str) -> AppiumSession:
    prebuilt_wda_path = root / "WebDriverAgentRunner-Runner.app"
    capabilities: dict[str, Any] = {
        "platformName": "iOS",
        "appium:automationName": "XCUITest",
        "appium:udid": udid,
        "appium:deviceName": DEVICE_NAME,
        "appium:noReset": True,
        "appium:wdaLocalPort": 8100,
        "appium:newCommandTimeout": 3600,
        "appium:showXcodeLog": SHOW_XCODE_LOG,
        "appium:wdaStartupRetries": 3,
        "appium:wdaStartupRetryInterval": 10000,
        "appium:wdaLaunchTimeout": 120000,
        "appium:wdaConnectionTimeout": 120000,
        "appium:usePreinstalledWDA": USE_PREINSTALLED_WDA,
        "appium:useNewWDA": FORCE_NEW_WDA,
    }

    if prebuilt_wda_path.exists():
        capabilities["appium:prebuiltWDAPath"] = str(prebuilt_wda_path)

    if WDA_BUNDLE_ID:
        capabilities["appium:updatedWDABundleId"] = WDA_BUNDLE_ID
        capabilities["appium:updatedWDABundleIdSuffix"] = WDA_BUNDLE_ID_SUFFIX

    response = appium_request(
        "POST",
        "/session",
        {"capabilities": {"alwaysMatch": capabilities}},
        timeout=float(globals().get("APPIUM_SESSION_TIMEOUT_SECONDS", 180.0)),
    )
    session_id = extract_appium_session_id(response)
    return AppiumSession(session_id)


def appium_request(method: str, path: str, payload: dict[str, Any] | None = None, timeout: float = 180.0) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(f"{APPIUM_URL}{path}", data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Appium {method} {path} failed with HTTP {exc.code}: {body}") from exc

    return None if not raw else json.loads(raw.decode("utf-8"))


def extract_appium_session_id(response: Any) -> str:
    if isinstance(response, dict):
        value = response.get("value")
        if isinstance(value, dict) and value.get("sessionId"):
            return str(value["sessionId"])
        if response.get("sessionId"):
            return str(response["sessionId"])
    raise RuntimeError(f"Appium did not return a session id: {response}")


def wait_for_wda(timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        status = get_wda_status(timeout=2.0)
        if status is not None:
            return status
        last_error = RuntimeError("WDA status probe failed")
        time.sleep(0.5)
    raise RuntimeError(f"WDA did not respond on localhost:8100 within {timeout:.0f}s: {last_error}")


def get_wda_status(timeout: float = 2.0) -> dict | None:
    try:
        with urlopen(f"{WDA_URL}/status", timeout=timeout) as response:
            if response.status != 200:
                return None
            parsed = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def wda_request(method: str, path: str, payload: dict[str, Any] | None = None, timeout: float = 15.0) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(f"{WDA_URL}{path}", data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"WDA {method} {path} failed with HTTP {exc.code}: {body}") from exc

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


def create_wda_session() -> str:
    response = wda_request("POST", "/session", {"capabilities": {"alwaysMatch": {}}})
    session_id = None
    if isinstance(response, dict):
        session_id = response.get("sessionId")
        if not session_id and isinstance(response.get("value"), dict):
            session_id = response["value"].get("sessionId")
    if not session_id:
        raise RuntimeError(f"WDA did not return a session id: {response}")
    return str(session_id)


def wda_session_path(session_id: str, suffix: str) -> str:
    return f"/session/{session_id}{suffix}"


def get_screen_size(session_id: str) -> dict[str, int]:
    try:
        size = wda_request("GET", wda_session_path(session_id, "/window/size"))
        if isinstance(size, dict) and "width" in size and "height" in size:
            return {"width": int(size["width"]), "height": int(size["height"])}
    except Exception as exc:  # noqa: BLE001 - fallback is fine for a smoke test.
        print(f"Could not read window size, using fallback 440x956: {exc}")
    return {"width": 440, "height": 956}


def tap(session_id: str, x: float, y: float) -> None:
    wda_request("POST", wda_session_path(session_id, "/wda/tap"), {"x": x, "y": y})
    print(f"Tapped: ({x:.0f}, {y:.0f})")


def swipe(session_id: str, direction: str) -> None:
    wda_request("POST", wda_session_path(session_id, "/wda/swipe"), {"direction": direction})
    print(f"Swiped: {direction}")


def type_text(session_id: str, text: str) -> None:
    wda_request("POST", wda_session_path(session_id, "/wda/keys"), {"value": list(text)})
    print(f"Typed {len(text)} characters")


def press_home() -> None:
    wda_request("POST", "/wda/homescreen", {})
    print("Pressed home")


def run_screenshot_viewer(udid: str | None = None) -> None:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV viewer needs: python3 -m pip install opencv-python numpy") from exc

    fps = max(VIEWER_FPS, 0.1)
    delay = 1.0 / fps
    session_id = create_wda_session()
    screen_size = get_screen_size(session_id)
    video_source = IOSVideoFrameSource(
        udid=udid,
        on_error=lambda message: log(f"video frame source: {message}"),
    )
    video_source.start()
    print("Using QuickTime USB screen-mirroring frames")

    active_board: dict[str, list[list[str]] | list[list[int]]] | None = None
    exhausted_board: dict[str, list[list[str]] | list[list[int]]] | None = None
    attempted_words: set[str] = set()
    failed_words = load_failed_words()
    best_path: list[tuple[int, int]] | None = None
    no_tile_since: float | None = None
    last_detection_failure_saved_at = 0.0
    last_post_game_action_at = 0.0
    pending_frame: Any | None = None
    pending_board: dict[str, list[list[str]] | list[list[int]]] | None = None
    pending_parsed_frame: tuple[Any, list[list[str]], list[list[int]], list[list[tuple[int, int, int, int]]]] | None = None
    solved_words_this_game = 0
    waiting_for_game_over = False
    last_limit_random_drag_at = 0.0
    if SHOW_OPENCV_VIEWER:
        print(f"OpenCV viewer running at {fps:g} FPS. Press q or Esc to quit.")
    else:
        print("OpenCV viewer hidden. Press Ctrl-C to quit.")
    print(f"Board refresh wait: {BOARD_REFRESH_WAIT_SECONDS}s, poll: {BOARD_CHANGE_POLL_SECONDS}s")

    while True:
        started = time.monotonic()
        frame_started = time.monotonic()
        if pending_frame is None:
            frame = video_source.read(timeout=IOS_VIDEO_FRAME_TIMEOUT_SECONDS)
            if frame is None:
                time.sleep(min(delay, 0.02))
                continue
        else:
            frame = pending_frame
            pending_frame = None
        frame_ms = perf_ms(frame_started)

        if AUTO_ADVANCE_POST_GAME and time.monotonic() - last_post_game_action_at >= POST_GAME_ACTION_COOLDOWN_SECONDS:
            post_game_action = detect_post_game_action(frame)
            if post_game_action is not None:
                action_name, frame_x, frame_y = post_game_action
                if action_name == "play":
                    solved_words_this_game = 0
                    tap_play_plus_button_before_play(session_id, frame.shape, screen_size)
                tap_frame_point(session_id, frame_x, frame_y, frame.shape, screen_size)
                log(f"post-game action: {action_name}")
                if action_name == "start":
                    solved_words_this_game = 0
                    waiting_for_game_over = False
                    last_limit_random_drag_at = 0.0
                    log("solved counter reset: start button")
                last_post_game_action_at = time.monotonic()
                active_board = None
                exhausted_board = None
                attempted_words.clear()
                best_path = None
                no_tile_since = None
                pending_frame = None
                pending_board = None
                pending_parsed_frame = None
                video_source.drain()
                time.sleep(POST_GAME_ACTION_DELAY_SECONDS)
                continue

        try:
            parse_started = time.monotonic()
            if pending_parsed_frame is None:
                frame, letters, dots, boxes = parse_video_frame(frame)
            else:
                frame, letters, dots, boxes = pending_parsed_frame
                pending_parsed_frame = None
            parse_ms = perf_ms(parse_started)
        except BoardUnstableError as exc:
            pending_frame = None
            pending_board = None
            pending_parsed_frame = None
            no_tile_since = None
            if SHOW_OPENCV_VIEWER:
                display_frame = frame.copy()
                cv2.putText(
                    display_frame,
                    str(exc),
                    (40, 80),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1.2,
                    (0, 255, 255),
                    3,
                )
            else:
                display_frame = frame
        except Exception as exc:  # noqa: BLE001 - keep scanning until the board is visible.
            pending_frame = None
            pending_board = None
            pending_parsed_frame = None
            now = time.monotonic()
            if SAVE_DETECTION_FAILURES and now - last_detection_failure_saved_at >= DETECTION_FAILURE_SAVE_INTERVAL_SECONDS:
                save_detection_failure_debug(frame, exc)
                last_detection_failure_saved_at = now
            board_visible = has_large_letter_board(frame)
            if board_visible:
                no_tile_since = None
                missing_for = 0.0
            else:
                if no_tile_since is None:
                    no_tile_since = time.monotonic()
                missing_for = time.monotonic() - no_tile_since
            active_board = None
            exhausted_board = None
            attempted_words.clear()
            best_path = None
            if SHOW_OPENCV_VIEWER:
                display_frame = frame.copy()
                status = "Game over / no tiles" if missing_for >= NO_TILE_STATUS_SECONDS else "Scanning"
                cv2.putText(
                    display_frame,
                    f"{status}... {exc}",
                    (40, 80),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1.2,
                    (0, 0, 255),
                    3,
                )
            else:
                display_frame = frame
        else:
            no_tile_since = None
            current_board = pending_board or board_from_frame_parts(letters, dots)
            pending_board = None

            if reached_solve_limit(solved_words_this_game):
                if not waiting_for_game_over:
                    log(f"solve limit reached: {solved_words_this_game}/{MAX_SOLVED_WORDS_PER_GAME}; dragging randomly until game over")
                waiting_for_game_over = True
                active_board = current_board
                exhausted_board = current_board
                attempted_words.clear()
                best_path = None
                if (
                    RANDOM_DRAG_AFTER_SOLVE_LIMIT
                    and time.monotonic() - last_limit_random_drag_at >= RANDOM_DRAG_AFTER_LIMIT_INTERVAL_SECONDS
                ):
                    best_path = drag_random_tile_path_after_limit(
                        session_id=session_id,
                        video_source=video_source,
                        boxes=boxes,
                        frame_shape=frame.shape,
                        screen_size=screen_size,
                    )
                    last_limit_random_drag_at = time.monotonic()
            elif current_board != active_board:
                active_board = current_board
                exhausted_board = None
                attempted_words.clear()
                if SAVE_QU_DATASET:
                    save_qu_dataset_examples(frame, boxes, letters)
                solve_started = time.monotonic()
                word_results = solve_board(letters, dots, result_limit=WORD_RESULT_LIMIT)
                solve_ms = perf_ms(solve_started)
                log(json.dumps(letters))
                perf(f"board: frame={frame_ms:.0f}ms ocr={parse_ms:.0f}ms solve={solve_ms:.1f}ms")

                (
                    best_path,
                    pending_frame,
                    pending_board,
                    pending_parsed_frame,
                    solved_word,
                ) = try_words_until_board_changes(
                    session_id=session_id,
                    video_source=video_source,
                    word_results=word_results,
                    attempted_words=attempted_words,
                    failed_words=failed_words,
                    boxes=boxes,
                    frame_shape=frame.shape,
                    screen_size=screen_size,
                    current_board=current_board,
                )
                if best_path is None:
                    exhausted_board = current_board
                elif solved_word:
                    solved_words_this_game += 1
                    log(f"solved words: {solved_words_this_game}/{MAX_SOLVED_WORDS_PER_GAME}")
                    if reached_solve_limit(solved_words_this_game):
                        waiting_for_game_over = True
                        log(f"solve limit reached: {solved_words_this_game}/{MAX_SOLVED_WORDS_PER_GAME}; dragging randomly until game over")
            elif current_board == exhausted_board:
                pass

            display_frame = draw_debug_overlay(frame, boxes, letters, dots, best_path) if SHOW_OPENCV_VIEWER else frame

        if SHOW_OPENCV_VIEWER:
            cv2.imshow("iPhone WDA", resize_display_frame(display_frame))
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break

        elapsed = time.monotonic() - started
        if elapsed < delay:
            time.sleep(delay - elapsed)

    video_source.stop()
    if SHOW_OPENCV_VIEWER:
        cv2.destroyAllWindows()


def resize_display_frame(frame: Any) -> Any:
    import cv2

    height, width = frame.shape[:2]
    if height <= OPENCV_WINDOW_MAX_HEIGHT:
        return frame
    scale = OPENCV_WINDOW_MAX_HEIGHT / height
    return cv2.resize(frame, (round(width * scale), OPENCV_WINDOW_MAX_HEIGHT), interpolation=cv2.INTER_AREA)


def detect_post_game_action(frame: Any) -> tuple[str, float, float] | None:
    if has_large_letter_board(frame):
        return None

    play_point = detect_color_button(
        frame,
        hsv_lower=(5, 100, 100),
        hsv_upper=(25, 255, 255),
        roi=(0.45, 0.72, 0.98, 0.94),
        min_area_fraction=0.05,
        min_width_fraction=0.25,
    )
    if play_point is not None:
        return ("play", play_point[0], play_point[1])

    green_point = detect_large_green_bottom_button(frame)
    if green_point is not None and not has_large_letter_board(frame):
        height = frame.shape[0]
        action = "start" if green_point[1] < height * 0.88 else "continue"
        return (action, green_point[0], green_point[1])

    if is_tap_anywhere_continue_overlay(frame):
        height, width = frame.shape[:2]
        return ("tap_anywhere_continue", width * 0.5, height * 0.84)

    return None


def detect_large_green_bottom_button(frame: Any) -> tuple[float, float] | None:
    return detect_color_button(
        frame,
        hsv_lower=(60, 100, 80),
        hsv_upper=(85, 255, 255),
        roi=(0.08, 0.74, 0.92, 0.98),
        min_area_fraction=0.11,
        min_width_fraction=0.48,
    )


def has_large_letter_board(frame: Any) -> bool:
    try:
        boxes = detect_tile_boxes(frame)
    except Exception:
        return False
    if len(boxes) != 4 or any(len(row) != 4 for row in boxes):
        return False
    height, width = frame.shape[:2]
    flat_boxes = [box for row in boxes for box in row]
    total_area = sum(box_width * box_height for _, _, box_width, box_height in flat_boxes)
    return total_area / float(width * height) > 0.10


def detect_color_button(
    frame: Any,
    hsv_lower: tuple[int, int, int],
    hsv_upper: tuple[int, int, int],
    roi: tuple[float, float, float, float],
    min_area_fraction: float,
    min_width_fraction: float,
) -> tuple[float, float] | None:
    import cv2

    height, width = frame.shape[:2]
    x1 = round(width * roi[0])
    y1 = round(height * roi[1])
    x2 = round(width * roi[2])
    y2 = round(height * roi[3])
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, hsv_lower, hsv_upper)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (21, 21)))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    contour = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(contour)
    if area < crop.shape[0] * crop.shape[1] * min_area_fraction:
        return None

    x, y, w, h = cv2.boundingRect(contour)
    if w < width * min_width_fraction:
        return None
    return (x1 + x + w / 2, y1 + y + h / 2)


def is_tap_anywhere_continue_overlay(frame: Any) -> bool:
    import cv2

    height, width = frame.shape[:2]
    if float(frame.mean()) > 38.0:
        return False

    text_roi = frame[round(height * 0.72) : round(height * 0.90), round(width * 0.20) : round(width * 0.85)]
    if text_roi.size == 0:
        return False

    hsv = cv2.cvtColor(text_roi, cv2.COLOR_BGR2HSV)
    white_mask = cv2.inRange(hsv, (0, 0, 150), (179, 90, 255))
    return float(white_mask.mean() / 255) > 0.004


def tap_frame_point(
    session_id: str,
    frame_x: float,
    frame_y: float,
    frame_shape: tuple[int, ...],
    screen_size: dict[str, int],
) -> None:
    frame_height, frame_width = frame_shape[:2]
    x = round(frame_x * screen_size["width"] / frame_width)
    y = round(frame_y * screen_size["height"] / frame_height)
    wda_request("POST", wda_session_path(session_id, "/wda/tap"), {"x": x, "y": y})


def tap_relative_frame_point(
    session_id: str,
    frame_x_ratio: float,
    frame_y_ratio: float,
    frame_shape: tuple[int, ...],
    screen_size: dict[str, int],
) -> None:
    frame_height, frame_width = frame_shape[:2]
    tap_frame_point(
        session_id,
        frame_width * frame_x_ratio,
        frame_height * frame_y_ratio,
        frame_shape,
        screen_size,
    )


def tap_play_plus_button_before_play(
    session_id: str,
    frame_shape: tuple[int, ...],
    screen_size: dict[str, int],
) -> None:
    for _ in range(PLAY_PLUS_TAPS_BEFORE_PLAY):
        tap_relative_frame_point(
            session_id,
            PLAY_PLUS_TAP_FRAME_X,
            PLAY_PLUS_TAP_FRAME_Y,
            frame_shape,
            screen_size,
        )
        if PLAY_PLUS_TAP_INTERVAL_SECONDS > 0:
            time.sleep(PLAY_PLUS_TAP_INTERVAL_SECONDS)


def parse_video_frame(frame: Any) -> tuple[Any, list[list[str]], list[list[int]], list[list[tuple[int, int, int, int]]]]:
    letters, dots, boxes = read_letter_grid_from_frame(frame, return_boxes=True)
    return frame, letters, dots, boxes


def save_detection_failure_debug(frame: Any, exc: Exception) -> None:
    import cv2

    LAST_DETECTION_FAILURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(LAST_DETECTION_FAILURE_PATH), frame)

    debug_frame = frame.copy()
    cv2.putText(
        debug_frame,
        str(exc)[:160],
        (30, 80),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (0, 0, 255),
        3,
    )
    cv2.imwrite(str(LAST_DETECTION_FAILURE_DEBUG_PATH), debug_frame)
    LAST_DETECTION_FAILURE_INFO_PATH.write_text(
        json.dumps(
            {
                "error": str(exc),
                "saved_at": time.time(),
                "image": str(LAST_DETECTION_FAILURE_PATH),
                "debug_image": str(LAST_DETECTION_FAILURE_DEBUG_PATH),
                "capture": "quicktime_usb_pyav",
            },
            indent=2,
        )
    )


def board_from_frame_parts(letters: list[list[str]], dots: list[list[int]]) -> dict[str, list[list[str]] | list[list[int]]]:
    return {"letters": letters, "dots": dots}


def try_words_until_board_changes(
    session_id: str,
    video_source: Any | None,
    word_results: list[Any],
    attempted_words: set[str],
    failed_words: set[str],
    boxes: list[list[tuple[int, int, int, int]]],
    frame_shape: tuple[int, ...],
    screen_size: dict[str, int],
    current_board: dict[str, list[list[str]] | list[list[int]]],
) -> tuple[
    list[tuple[int, int]] | None,
    Any | None,
    dict[str, list[list[str]] | list[list[int]]] | None,
    tuple[Any, list[list[str]], list[list[int]], list[list[tuple[int, int, int, int]]]] | None,
    bool,
]:
    while True:
        failed_words.update(load_failed_words())
        candidate = choose_word_candidate(word_results, attempted_words, failed_words)
        if candidate is None:
            break
        attempted_words.add(normalize_word(candidate.word))
        log(f"trying word: {candidate.word} length={candidate.length} dots={candidate.dot_score}")

        drag_started = time.monotonic()
        if video_source is not None:
            video_source.drain()
        if DRAG_BEST_WORD:
            drag_word_path(session_id, candidate.path, boxes, frame_shape, screen_size)
        if AFTER_DRAG_SCAN_DELAY_SECONDS > 0:
            time.sleep(AFTER_DRAG_SCAN_DELAY_SECONDS)
        if video_source is not None:
            video_source.drain()
        drag_ms = (time.monotonic() - drag_started) * 1000

        change_started = time.monotonic()
        change_state = wait_for_board_change(
            session_id,
            current_board,
            video_source,
        )
        change_ms = (time.monotonic() - change_started) * 1000
        if change_state.state == "changed":
            perf(f"word {candidate.word}: drag={drag_ms:.0f}ms refresh={change_ms:.0f}ms")
            log(f"accepted word: {candidate.word}")
            return candidate.path, change_state.frame, change_state.board, change_state.parsed_frame, True
        if change_state.state == "missing":
            log(f"tiles disappeared after word: {candidate.word}")
            return candidate.path, None, None, None, True

        log(f"word did not change board: {candidate.word}")
        failed_words.add(normalize_word(candidate.word))
        save_failed_words(failed_words)

    log("no untried words left for this board")
    return None, None, None, None, False


def choose_word_candidate(word_results: list[Any], attempted_words: set[str], failed_words: set[str]) -> Any | None:
    unavailable = {normalize_word(word) for word in attempted_words | failed_words}
    available = [candidate for candidate in word_results if normalize_word(candidate.word) not in unavailable]
    if not available:
        return None
    return available[0]


def load_failed_words() -> set[str]:
    if not FAILED_WORDS_PATH.exists():
        return set()
    try:
        words = json.loads(FAILED_WORDS_PATH.read_text())
    except Exception:
        return set()
    if not isinstance(words, list):
        return set()
    return {normalize_word(word) for word in words if normalize_word(word)}


def save_failed_words(words: set[str]) -> None:
    FAILED_WORDS_PATH.write_text(json.dumps(sorted(normalize_word(word) for word in words if normalize_word(word)), indent=2))


def normalize_word(word: Any) -> str:
    return str(word).strip().upper()


class BoardChangeState:
    def __init__(
        self,
        state: str,
        frame: Any | None = None,
        board: dict[str, list[list[str]] | list[list[int]]] | None = None,
        parsed_frame: tuple[Any, list[list[str]], list[list[int]], list[list[tuple[int, int, int, int]]]] | None = None,
    ) -> None:
        self.state = state
        self.frame = frame
        self.board = board
        self.parsed_frame = parsed_frame


def wait_for_board_change(
    session_id: str,
    previous_board: dict[str, list[list[str]] | list[list[int]]],
    video_source: Any | None = None,
    timeout: float = BOARD_REFRESH_WAIT_SECONDS,
) -> BoardChangeState:
    deadline = time.monotonic() + timeout
    reject_deadline = time.monotonic() + BOARD_UNCHANGED_REJECT_SECONDS
    saw_same_board = False
    saw_unstable_board = False
    poll_count = 0
    frame_total_ms = 0.0
    ocr_total_ms = 0.0
    while time.monotonic() < deadline:
        try:
            poll_count += 1
            frame_started = time.monotonic()
            frame = (
                video_source.read_latest(timeout=max(BOARD_CHANGE_POLL_SECONDS, 0.02))
                if video_source is not None
                else None
            )
            if frame is None:
                time.sleep(BOARD_CHANGE_POLL_SECONDS)
                continue
            frame_ms = perf_ms(frame_started)
            frame_total_ms += frame_ms
            ocr_started = time.monotonic()
            parsed_frame = parse_video_frame(frame)
            ocr_ms = perf_ms(ocr_started)
            ocr_total_ms += ocr_ms
            _, letters, dots, _ = parsed_frame
            current_board = board_from_frame_parts(letters, dots)
            if current_board != previous_board:
                perf(
                    "refresh-poll: "
                    f"polls={poll_count} frame={frame_ms:.0f}ms "
                    f"ocr={ocr_ms:.0f}ms total_frame={frame_total_ms:.0f}ms "
                    f"total_ocr={ocr_total_ms:.0f}ms"
                )
                return BoardChangeState("changed", frame=frame, board=current_board, parsed_frame=parsed_frame)
            saw_same_board = True
            if time.monotonic() >= reject_deadline:
                perf(
                    "refresh-poll-same: "
                    f"polls={poll_count} total_frame={frame_total_ms:.0f}ms "
                    f"total_ocr={ocr_total_ms:.0f}ms"
                )
                return BoardChangeState("same")
        except BoardUnstableError:
            saw_unstable_board = True
        except Exception:
            pass
        time.sleep(BOARD_CHANGE_POLL_SECONDS)
    perf(
        "refresh-poll-timeout: "
        f"polls={poll_count} total_frame={frame_total_ms:.0f}ms "
        f"total_ocr={ocr_total_ms:.0f}ms"
    )
    return BoardChangeState("same" if saw_same_board or saw_unstable_board else "missing")


def drag_random_tile_path_after_limit(
    session_id: str,
    video_source: Any | None,
    boxes: list[list[tuple[int, int, int, int]]],
    frame_shape: tuple[int, ...],
    screen_size: dict[str, int],
) -> list[tuple[int, int]]:
    path = random_tile_path()
    if video_source is not None:
        video_source.drain()
    drag_word_path(session_id, path, boxes, frame_shape, screen_size)
    if video_source is not None:
        video_source.drain()
    log(f"limit random drag: {path}")
    return path


def random_tile_path() -> list[tuple[int, int]]:
    target_length = random.randint(RANDOM_DRAG_AFTER_LIMIT_MIN_TILES, RANDOM_DRAG_AFTER_LIMIT_MAX_TILES)
    position = random.randrange(16)
    used = {position}
    path = [position]

    while len(path) < target_length:
        choices = [neighbor for neighbor in tile_neighbors(position) if neighbor not in used]
        if not choices:
            break
        position = random.choice(choices)
        used.add(position)
        path.append(position)

    return [(position // 4, position % 4) for position in path]


def tile_neighbors(position: int) -> list[int]:
    row, col = divmod(position, 4)
    result: list[int] = []
    for row_delta in (-1, 0, 1):
        for col_delta in (-1, 0, 1):
            if row_delta == 0 and col_delta == 0:
                continue
            next_row = row + row_delta
            next_col = col + col_delta
            if 0 <= next_row < 4 and 0 <= next_col < 4:
                result.append(next_row * 4 + next_col)
    return result


def drag_word_path(
    session_id: str,
    path: list[tuple[int, int]],
    boxes: list[list[tuple[int, int, int, int]]],
    frame_shape: tuple[int, ...],
    screen_size: dict[str, int],
) -> None:
    if len(path) < 2:
        return

    points = tile_path_to_screen_points(path, boxes, frame_shape, screen_size)
    actions = [
        {"type": "pointerMove", "duration": 0, "x": points[0][0], "y": points[0][1]},
        {"type": "pointerDown", "button": 0},
        {"type": "pause", "duration": random.randint(DRAG_HOLD_MS_MIN, DRAG_HOLD_MS_MAX)},
    ]

    for x, y in points[1:]:
        actions.append(
            {
                "type": "pointerMove",
                "duration": random.randint(DRAG_SEGMENT_DURATION_MS_MIN, DRAG_SEGMENT_DURATION_MS_MAX),
                "x": x,
                "y": y,
            }
        )

    actions.extend(
        [
            {"type": "pause", "duration": random.randint(DRAG_HOLD_MS_MIN, DRAG_HOLD_MS_MAX)},
            {"type": "pointerUp", "button": 0},
        ]
    )

    wda_request(
        "POST",
        wda_session_path(session_id, "/actions"),
        {
            "actions": [
                {
                    "type": "pointer",
                    "id": "finger1",
                    "parameters": {"pointerType": "touch"},
                    "actions": actions,
                }
            ]
        },
        timeout=30,
    )
    log(f"dragged path: {points}")


def tile_path_to_screen_points(
    path: list[tuple[int, int]],
    boxes: list[list[tuple[int, int, int, int]]],
    frame_shape: tuple[int, ...],
    screen_size: dict[str, int],
) -> list[tuple[int, int]]:
    frame_height, frame_width = frame_shape[:2]
    scale_x = screen_size["width"] / frame_width
    scale_y = screen_size["height"] / frame_height
    points: list[tuple[int, int]] = []

    for row, col in path:
        x, y, width, height = boxes[row][col]
        center_x = x + width / 2
        center_y = y + height / 2
        points.append((round(center_x * scale_x), round(center_y * scale_y)))

    return points


if __name__ == "__main__":
    raise SystemExit(main())
