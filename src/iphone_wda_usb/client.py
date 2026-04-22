"""Minimal WebDriverAgent HTTP client.

This intentionally talks to WebDriverAgent directly instead of Appium. Appium is
still useful for building/installing WDA, but this module sends the runtime
commands from Python once WDA is reachable through USB port forwarding.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class WDAError(RuntimeError):
    """Raised when WebDriverAgent returns an error or cannot be reached."""


@dataclass(frozen=True)
class Element:
    """A cached WDA element reference."""

    id: str


class WDAClient:
    """Small direct client for an Appium WebDriverAgent server."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8100",
        timeout: float = 15.0,
        session_id: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session_id = session_id

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/status")

    def wait_until_ready(self, timeout: float = 30.0, interval: float = 0.5) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last_error: Optional[Exception] = None
        while time.monotonic() < deadline:
            try:
                status = self.status()
                if status:
                    return status
            except Exception as exc:  # noqa: BLE001 - surfacing the last connection failure is useful here.
                last_error = exc
            time.sleep(interval)
        raise WDAError(f"WDA did not become ready within {timeout:.1f}s: {last_error}")

    def create_session(
        self,
        bundle_id: Optional[str] = None,
        capabilities: Optional[dict[str, Any]] = None,
    ) -> str:
        caps = dict(capabilities or {})
        if bundle_id:
            caps["bundleId"] = bundle_id

        response = self._request("POST", "/session", {"capabilities": {"alwaysMatch": caps}})
        session_id = response.get("sessionId")
        if not session_id and isinstance(response.get("value"), dict):
            session_id = response["value"].get("sessionId")
        if not session_id:
            raise WDAError(f"WDA did not return a session id: {response}")
        self.session_id = str(session_id)
        return self.session_id

    def delete_session(self) -> None:
        if self.session_id:
            self._request("DELETE", self._session_path(""))
            self.session_id = None

    def window_size(self) -> dict[str, float]:
        return self._request("GET", self._session_path("/window/size"))

    def screenshot_png(self) -> bytes:
        value = self._request("GET", self._session_path("/screenshot"))
        if isinstance(value, dict) and "value" in value:
            value = value["value"]
        if not isinstance(value, str):
            raise WDAError(f"Unexpected screenshot response: {value!r}")
        return base64.b64decode(value)

    def save_screenshot(self, path: str) -> None:
        with open(path, "wb") as handle:
            handle.write(self.screenshot_png())

    def source(self, format: str = "xml") -> Any:
        query = urlencode({"format": format}) if format else ""
        suffix = f"/source?{query}" if query else "/source"
        return self._request("GET", self._session_path(suffix))

    def tap(self, x: float, y: float) -> None:
        self._request("POST", self._session_path("/wda/tap"), {"x": x, "y": y})

    def double_tap(self, x: float, y: float) -> None:
        self._request("POST", self._session_path("/wda/doubleTap"), {"x": x, "y": y})

    def touch_and_hold(self, x: float, y: float, duration: float = 1.0) -> None:
        self._request(
            "POST",
            self._session_path("/wda/touchAndHold"),
            {"x": x, "y": y, "duration": duration},
        )

    def swipe(self, direction: str, velocity: Optional[float] = None) -> None:
        payload: dict[str, Any] = {"direction": direction}
        if velocity is not None:
            payload["velocity"] = velocity
        self._request("POST", self._session_path("/wda/swipe"), payload)

    def drag(self, x1: float, y1: float, x2: float, y2: float, duration: float = 0.5) -> None:
        self._request(
            "POST",
            self._session_path("/wda/dragfromtoforduration"),
            {"fromX": x1, "fromY": y1, "toX": x2, "toY": y2, "duration": duration},
        )

    def actions_drag(self, x1: float, y1: float, x2: float, y2: float, duration: float = 0.5) -> None:
        """Perform a drag using standard W3C touch actions."""

        duration_ms = int(duration * 1000)
        payload = {
            "actions": [
                {
                    "type": "pointer",
                    "id": "finger1",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0, "x": x1, "y": y1},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pointerMove", "duration": duration_ms, "x": x2, "y": y2},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        }
        self._request("POST", self._session_path("/actions"), payload)

    def type_text(self, text: str, frequency: Optional[int] = None) -> None:
        payload: dict[str, Any] = {"value": list(text)}
        if frequency is not None:
            payload["frequency"] = frequency
        self._request("POST", self._session_path("/wda/keys"), payload)

    def find_element(self, using: str, value: str) -> Element:
        response = self._request("POST", self._session_path("/element"), {"using": using, "value": value})
        element_id = self._extract_element_id(response)
        if not element_id:
            raise WDAError(f"WDA did not return an element id: {response}")
        return Element(element_id)

    def click(self, element: Element) -> None:
        self._request("POST", self._session_path(f"/element/{element.id}/click"), {})

    def set_value(self, element: Element, text: str, frequency: Optional[int] = None) -> None:
        payload: dict[str, Any] = {"value": list(text)}
        if frequency is not None:
            payload["frequency"] = frequency
        self._request("POST", self._session_path(f"/element/{element.id}/value"), payload)

    def clear(self, element: Element) -> None:
        self._request("POST", self._session_path(f"/element/{element.id}/clear"), {})

    def press_button(self, name: str) -> None:
        self._request("POST", self._session_path("/wda/pressButton"), {"name": name})

    def home(self) -> None:
        self._request("POST", "/wda/homescreen", {})

    def lock(self) -> None:
        self._request("POST", "/wda/lock", {})

    def unlock(self) -> None:
        self._request("POST", "/wda/unlock", {})

    def is_locked(self) -> bool:
        return bool(self._request("GET", "/wda/locked"))

    def active_app_info(self) -> dict[str, Any]:
        return self._request("GET", "/wda/activeAppInfo")

    def launch_app(
        self,
        bundle_id: str,
        arguments: Optional[list[str]] = None,
        environment: Optional[dict[str, str]] = None,
    ) -> None:
        self._request(
            "POST",
            self._session_path("/wda/apps/launch"),
            {
                "bundleId": bundle_id,
                "arguments": arguments or [],
                "environment": environment or {},
            },
        )

    def activate_app(self, bundle_id: str) -> None:
        self._request("POST", self._session_path("/wda/apps/activate"), {"bundleId": bundle_id})

    def terminate_app(self, bundle_id: str) -> bool:
        return bool(self._request("POST", self._session_path("/wda/apps/terminate"), {"bundleId": bundle_id}))

    def app_state(self, bundle_id: str) -> int:
        return int(self._request("POST", self._session_path("/wda/apps/state"), {"bundleId": bundle_id}))

    def open_url(self, url: str, bundle_id: Optional[str] = None) -> None:
        payload: dict[str, Any] = {"url": url}
        if bundle_id:
            payload["bundleId"] = bundle_id
        self._request("POST", self._session_path("/url"), payload)

    def _session_path(self, suffix: str) -> str:
        if not self.session_id:
            self.create_session()
        return f"/session/{self.session_id}{suffix}"

    def _request(self, method: str, path: str, payload: Optional[dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(url, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")

        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise WDAError(f"{method} {url} failed with HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise WDAError(f"{method} {url} failed: {exc}") from exc

        if not raw:
            return None
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return raw

        if isinstance(parsed, dict):
            value = parsed.get("value")
            if isinstance(value, dict) and value.get("error"):
                message = value.get("message") or value.get("error")
                raise WDAError(f"{method} {url} failed: {message}")
            if parsed.get("status") not in (None, 0):
                raise WDAError(f"{method} {url} failed: {parsed}")
            if "value" in parsed and set(parsed.keys()).issubset({"value", "sessionId", "status"}):
                return parsed["value"]
        return parsed

    @staticmethod
    def _extract_element_id(response: Any) -> Optional[str]:
        if isinstance(response, dict) and "value" in response:
            response = response["value"]
        if not isinstance(response, dict):
            return None
        for key in ("ELEMENT", "element-6066-11e4-a52e-4f735466cecf"):
            value = response.get(key)
            if value:
                return str(value)
        return None
