"""Command line interface for controlling WDA over USB."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from typing import Any, Optional

from .client import WDAClient, WDAError
from .iproxy import IProxy, IProxyError


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except (IProxyError, WDAError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="phonectl",
        description="Control a trusted iPhone via USB + WebDriverAgent.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="WDA host after port forwarding.")
    parser.add_argument("--port", type=int, default=8100, help="Local WDA port.")
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout in seconds.")
    parser.add_argument("--session", help="Reuse an existing WDA session id.")
    parser.add_argument("--udid", help="USB device UDID for iproxy when multiple devices are attached.")
    parser.add_argument(
        "--no-iproxy",
        action="store_true",
        help="Do not start iproxy; assume WDA is already reachable.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add(subparsers, "doctor", cmd_doctor, "Check local setup.")
    add(subparsers, "status", cmd_status, "Print WDA status.")
    add(subparsers, "session", cmd_session, "Create a WDA session and print its id.").add_argument(
        "--bundle-id", help="Bundle id to launch while creating the session."
    )
    add(subparsers, "size", cmd_size, "Print the active window size.")

    screenshot = add(subparsers, "screenshot", cmd_screenshot, "Save a screenshot as PNG.")
    screenshot.add_argument("path", help="Output PNG path.")

    source = add(subparsers, "source", cmd_source, "Print UI hierarchy.")
    source.add_argument("--format", choices=["xml", "json", "description"], default="xml")

    tap = add(subparsers, "tap", cmd_tap, "Tap screen coordinates.")
    tap.add_argument("x", type=float)
    tap.add_argument("y", type=float)

    double_tap = add(subparsers, "double-tap", cmd_double_tap, "Double tap screen coordinates.")
    double_tap.add_argument("x", type=float)
    double_tap.add_argument("y", type=float)

    hold = add(subparsers, "hold", cmd_hold, "Touch and hold screen coordinates.")
    hold.add_argument("x", type=float)
    hold.add_argument("y", type=float)
    hold.add_argument("--duration", type=float, default=1.0)

    swipe = add(subparsers, "swipe", cmd_swipe, "Swipe in a direction.")
    swipe.add_argument("direction", choices=["up", "down", "left", "right"])
    swipe.add_argument("--velocity", type=float)

    drag = add(subparsers, "drag", cmd_drag, "Drag between coordinates.")
    drag.add_argument("x1", type=float)
    drag.add_argument("y1", type=float)
    drag.add_argument("x2", type=float)
    drag.add_argument("y2", type=float)
    drag.add_argument("--duration", type=float, default=0.5)
    drag.add_argument("--actions", action="store_true", help="Use W3C actions instead of WDA drag endpoint.")

    type_text = add(subparsers, "type", cmd_type, "Type text into the focused element.")
    type_text.add_argument("text")
    type_text.add_argument("--frequency", type=int)

    click = add(subparsers, "click", cmd_click, "Find an element and click it.")
    add_locator_arguments(click)

    set_value = add(subparsers, "set-value", cmd_set_value, "Find an element and type text into it.")
    add_locator_arguments(set_value)
    set_value.add_argument("text")
    set_value.add_argument("--clear", action="store_true")
    set_value.add_argument("--frequency", type=int)

    find = add(subparsers, "find", cmd_find, "Find an element and print its WDA id.")
    add_locator_arguments(find)

    press = add(subparsers, "press", cmd_press, "Press a hardware/control-center style WDA button.")
    press.add_argument("name", help="Button name, for example home, volumeUp, volumeDown.")

    add(subparsers, "home", cmd_home, "Go to the home screen.")
    add(subparsers, "lock", cmd_lock, "Lock the device.")
    add(subparsers, "unlock", cmd_unlock, "Unlock the device if WDA can do so.")
    add(subparsers, "active-app", cmd_active_app, "Print active app info.")

    launch = add(subparsers, "launch", cmd_launch, "Launch an installed app by bundle id.")
    launch.add_argument("bundle_id")

    activate = add(subparsers, "activate", cmd_activate, "Activate an installed app by bundle id.")
    activate.add_argument("bundle_id")

    terminate = add(subparsers, "terminate", cmd_terminate, "Terminate an installed app by bundle id.")
    terminate.add_argument("bundle_id")

    open_url = add(subparsers, "open-url", cmd_open_url, "Open a URL or deep link.")
    open_url.add_argument("url")
    open_url.add_argument("--bundle-id")

    return parser


def add(subparsers: argparse._SubParsersAction, name: str, func: Any, help_text: str) -> argparse.ArgumentParser:
    subparser = subparsers.add_parser(name, help=help_text)
    subparser.set_defaults(func=func)
    return subparser


def add_locator_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--using",
        default="accessibility id",
        choices=["accessibility id", "id", "name", "xpath", "class chain", "predicate string", "class name"],
    )
    parser.add_argument("--value", required=True)


def make_client(args: argparse.Namespace) -> WDAClient:
    return WDAClient(f"http://{args.host}:{args.port}", timeout=args.timeout, session_id=args.session)


def with_optional_iproxy(args: argparse.Namespace, func: Any) -> Any:
    if args.no_iproxy:
        return func()
    with IProxy(local_port=args.port, remote_port=8100, udid=args.udid):
        return func()


def cmd_doctor(args: argparse.Namespace) -> int:
    tools = {
        "xcodebuild": shutil.which("xcodebuild"),
        "node": shutil.which("node"),
        "npm": shutil.which("npm"),
        "appium": shutil.which("appium"),
        "iproxy": shutil.which("iproxy"),
    }
    for name, path in tools.items():
        print(f"{name:10} {'ok: ' + path if path else 'missing'}")

    if tools["xcodebuild"]:
        try:
            output = subprocess.check_output(["xcodebuild", "-version"], text=True).strip()
            print(output)
        except subprocess.SubprocessError:
            pass

    if not tools["appium"]:
        print("\nInstall Appium when you are ready to build/open WDA:")
        print("  npm install -g appium")
        print("  appium driver install xcuitest")
        print("  appium driver run xcuitest open-wda")
    if not tools["iproxy"]:
        print("\nInstall iproxy for USB forwarding, for example:")
        print("  brew install libimobiledevice")
        print("or:")
        print("  npm install -g iproxy")

    return 0


def cmd_status(args: argparse.Namespace) -> None:
    def run() -> None:
        client = make_client(args)
        print_json(client.wait_until_ready(timeout=args.timeout))

    with_optional_iproxy(args, run)


def cmd_session(args: argparse.Namespace) -> None:
    def run() -> None:
        client = make_client(args)
        print(client.create_session(bundle_id=args.bundle_id))

    with_optional_iproxy(args, run)


def cmd_size(args: argparse.Namespace) -> None:
    def run() -> None:
        print_json(make_client(args).window_size())

    with_optional_iproxy(args, run)


def cmd_screenshot(args: argparse.Namespace) -> None:
    def run() -> None:
        make_client(args).save_screenshot(args.path)
        print(args.path)

    with_optional_iproxy(args, run)


def cmd_source(args: argparse.Namespace) -> None:
    def run() -> None:
        value = make_client(args).source(format=args.format)
        if isinstance(value, (dict, list)):
            print_json(value)
        else:
            print(value)

    with_optional_iproxy(args, run)


def cmd_tap(args: argparse.Namespace) -> None:
    with_optional_iproxy(args, lambda: make_client(args).tap(args.x, args.y))


def cmd_double_tap(args: argparse.Namespace) -> None:
    with_optional_iproxy(args, lambda: make_client(args).double_tap(args.x, args.y))


def cmd_hold(args: argparse.Namespace) -> None:
    with_optional_iproxy(args, lambda: make_client(args).touch_and_hold(args.x, args.y, args.duration))


def cmd_swipe(args: argparse.Namespace) -> None:
    with_optional_iproxy(args, lambda: make_client(args).swipe(args.direction, args.velocity))


def cmd_drag(args: argparse.Namespace) -> None:
    def run() -> None:
        client = make_client(args)
        if args.actions:
            client.actions_drag(args.x1, args.y1, args.x2, args.y2, args.duration)
        else:
            client.drag(args.x1, args.y1, args.x2, args.y2, args.duration)

    with_optional_iproxy(args, run)


def cmd_type(args: argparse.Namespace) -> None:
    with_optional_iproxy(args, lambda: make_client(args).type_text(args.text, args.frequency))


def cmd_find(args: argparse.Namespace) -> None:
    def run() -> None:
        element = make_client(args).find_element(args.using, args.value)
        print(element.id)

    with_optional_iproxy(args, run)


def cmd_click(args: argparse.Namespace) -> None:
    def run() -> None:
        client = make_client(args)
        client.click(client.find_element(args.using, args.value))

    with_optional_iproxy(args, run)


def cmd_set_value(args: argparse.Namespace) -> None:
    def run() -> None:
        client = make_client(args)
        element = client.find_element(args.using, args.value)
        if args.clear:
            client.clear(element)
        client.set_value(element, args.text, args.frequency)

    with_optional_iproxy(args, run)


def cmd_press(args: argparse.Namespace) -> None:
    with_optional_iproxy(args, lambda: make_client(args).press_button(args.name))


def cmd_home(args: argparse.Namespace) -> None:
    with_optional_iproxy(args, lambda: make_client(args).home())


def cmd_lock(args: argparse.Namespace) -> None:
    with_optional_iproxy(args, lambda: make_client(args).lock())


def cmd_unlock(args: argparse.Namespace) -> None:
    with_optional_iproxy(args, lambda: make_client(args).unlock())


def cmd_active_app(args: argparse.Namespace) -> None:
    with_optional_iproxy(args, lambda: print_json(make_client(args).active_app_info()))


def cmd_launch(args: argparse.Namespace) -> None:
    with_optional_iproxy(args, lambda: make_client(args).launch_app(args.bundle_id))


def cmd_activate(args: argparse.Namespace) -> None:
    with_optional_iproxy(args, lambda: make_client(args).activate_app(args.bundle_id))


def cmd_terminate(args: argparse.Namespace) -> None:
    with_optional_iproxy(args, lambda: print(make_client(args).terminate_app(args.bundle_id)))


def cmd_open_url(args: argparse.Namespace) -> None:
    with_optional_iproxy(args, lambda: make_client(args).open_url(args.url, args.bundle_id))


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
