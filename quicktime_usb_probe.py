#!/usr/bin/env python3
"""Probe the local QuickTime USB screen-mirroring transport.

This script intentionally avoids OpenCV and PyAV. It only checks the USB
layer used before video packets are decoded, which makes it useful when one
Mac can stream and another Mac fails before the first frame.
"""

from __future__ import annotations

import argparse
import ctypes.util
import logging
import os
import platform
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any

import usb
import usb.core
import usb.util
from usb.core import Configuration

from wordlink_ios_stream.transport import (
    READ_TIMEOUT_MS,
    describe_usb_error,
    disable_quicktime_config,
    enable_quicktime_config,
    env_bool,
    find_ios_device,
    find_quicktime_interface,
    find_stream_endpoints,
    get_libusb_backend,
    normalize_udid,
    runtime_root,
)


KNOWN_LIBUSB_PATHS = (
    runtime_root() / "lib/libusb-1.0.dylib",
    Path("/opt/homebrew/lib/libusb-1.0.dylib"),
    Path("/usr/local/lib/libusb-1.0.dylib"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe QuickTime USB mirroring compatibility on this Mac.")
    parser.add_argument("--udid", help="Device UDID. Dashes are optional.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    parser.add_argument("--read-test", action="store_true", help="Enable QuickTime config and attempt a single USB read.")
    parser.add_argument("--chunk", type=int, default=64 * 1024, help="Read size to use with --read-test.")
    parser.add_argument("--timeout-ms", type=int, default=READ_TIMEOUT_MS, help="USB read timeout for --read-test.")
    parser.add_argument(
        "--claim",
        action="store_true",
        help="Explicitly claim the QuickTime USB interface before --read-test.",
    )
    parser.add_argument(
        "--endpoint-address",
        action="store_true",
        help="Use numeric endpoint addresses instead of PyUSB endpoint objects for --read-test.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    print_machine_report()

    try:
        backend = get_libusb_backend()
    except Exception as exc:
        print(f"\nlibusb backend: FAILED: {exc}")
        return 1

    print("\nlibusb backend: loaded")
    print(f"ctypes find_library('usb-1.0'): {ctypes.util.find_library('usb-1.0')}")
    for path in KNOWN_LIBUSB_PATHS:
        print(f"known libusb path: {path} exists={path.exists()}")

    devices = list(usb.core.find(find_all=True, backend=backend))
    print(f"\nUSB devices visible to PyUSB/libusb: {len(devices)}")
    print_apple_devices(devices)

    udid = normalize_udid(args.udid)
    try:
        device = find_ios_device(backend, udid)
    except Exception as exc:
        print(f"\nQuickTime candidate match: FAILED: {exc}")
        return 1

    print("\nQuickTime candidate match: OK")
    print_device_summary(device)

    if not args.read_test:
        print("\nRead test skipped. Add --read-test to test the QuickTime bulk endpoint directly.")
        return 0

    return run_read_test(
        backend=backend,
        device=device,
        chunk_size=args.chunk,
        timeout_ms=args.timeout_ms,
        claim=args.claim,
        endpoint_address=args.endpoint_address,
    )


def print_machine_report() -> None:
    print("Machine")
    print(f"  macOS: {platform.mac_ver()[0] or platform.platform()}")
    print(f"  machine: {platform.machine()}")
    print(f"  processor: {platform.processor() or '<unknown>'}")
    print(f"  python executable: {sys.executable}")
    print(f"  python version: {sys.version.split()[0]}")
    print(f"  python arch: {platform.architecture()[0]}")
    print(f"  pyusb module: {getattr(usb, '__file__', '<unknown>')}")
    print(f"  pyusb version: {getattr(usb, '__version__', '<unknown>')}")
    print(f"  conda prefix: {os.environ.get('CONDA_PREFIX', '<none>')}")
    print(f"  venv: {os.environ.get('VIRTUAL_ENV', '<none>')}")
    print(f"  WORDLINK_USB_CLAIM_INTERFACE: {os.environ.get('WORDLINK_USB_CLAIM_INTERFACE', '<unset>')}")
    print(f"  WORDLINK_USB_USE_ENDPOINT_ADDRESS: {os.environ.get('WORDLINK_USB_USE_ENDPOINT_ADDRESS', '<unset>')}")
    print(f"  WORDLINK_USB_READ_CHUNK_SIZE: {os.environ.get('WORDLINK_USB_READ_CHUNK_SIZE', '<unset>')}")


def print_apple_devices(devices: list[Any]) -> None:
    apple_devices = [device for device in devices if getattr(device, "idVendor", None) == 0x05AC]
    print(f"Apple USB devices visible: {len(apple_devices)}")
    for index, device in enumerate(apple_devices, start=1):
        print(f"\nApple device #{index}")
        print_device_summary(device)
        print_device_interfaces(device)


def print_device_summary(device: Any) -> None:
    print(f"  bus/address: {getattr(device, 'bus', '<unknown>')}/{getattr(device, 'address', '<unknown>')}")
    print(f"  vendor/product: 0x{int(device.idVendor):04x}/0x{int(device.idProduct):04x}")
    print(f"  class/subclass/protocol: 0x{int(device.bDeviceClass):02x}/0x{int(device.bDeviceSubClass):02x}/0x{int(device.bDeviceProtocol):02x}")
    print(f"  configurations: {int(device.bNumConfigurations)}")
    try:
        print(f"  serial: {device.serial_number}")
    except Exception as exc:
        print(f"  serial: <unreadable: {describe_usb_error(exc)}>")


def print_device_interfaces(device: Any) -> None:
    try:
        for config_index in range(int(device.bNumConfigurations)):
            config = Configuration(device, config_index)
            print(f"  config {config_index + 1}: value={int(config.bConfigurationValue)}")
            for interface in config:
                print_interface(interface)
    except Exception as exc:
        print(f"  interfaces: <unreadable: {describe_usb_error(exc)}>")


def print_interface(interface: Any) -> None:
    print(
        "    interface "
        f"{int(interface.bInterfaceNumber)} alt={int(interface.bAlternateSetting)} "
        f"class/subclass/protocol="
        f"0x{int(interface.bInterfaceClass):02x}/"
        f"0x{int(interface.bInterfaceSubClass):02x}/"
        f"0x{int(interface.bInterfaceProtocol):02x}"
    )
    for endpoint in interface:
        direction = "in" if usb.util.endpoint_direction(endpoint.bEndpointAddress) == usb.util.ENDPOINT_IN else "out"
        transfer_type = usb.util.endpoint_type(endpoint.bmAttributes)
        print(
            f"      endpoint 0x{int(endpoint.bEndpointAddress):02x} "
            f"{direction} type={transfer_type} max_packet={int(endpoint.wMaxPacketSize)}"
        )


def run_read_test(
    backend: Any,
    device: Any,
    chunk_size: int,
    timeout_ms: int,
    claim: bool,
    endpoint_address: bool,
) -> int:
    print("\nRead Test")
    stop_signal = threading.Event()
    interface_number: int | None = None
    first_packet_seen = False

    try:
        disable_quicktime_config(device)
        device.set_configuration()
        device = enable_quicktime_config(device, backend, stop_signal)

        config_index, quicktime_interface = find_quicktime_interface(device)
        device.set_configuration(config_index)
        device.ctrl_transfer(0x02, 0x01, 0, 0x86, b"")
        device.ctrl_transfer(0x02, 0x01, 0, 0x05, b"")

        in_endpoint, out_endpoint = find_stream_endpoints(quicktime_interface)
        interface_number = int(quicktime_interface.bInterfaceNumber)
        if claim:
            try:
                usb.util.claim_interface(device, interface_number)
                print(f"  claim interface {interface_number}: OK")
            except Exception as exc:
                print(f"  claim interface {interface_number}: FAILED: {describe_usb_error(exc)}")
        else:
            print(f"  claim interface {interface_number}: skipped")

        read_target = int(in_endpoint.bEndpointAddress) if endpoint_address else in_endpoint
        print(f"  config index: {config_index}")
        print(f"  in endpoint: 0x{int(in_endpoint.bEndpointAddress):02x}")
        print(f"  out endpoint: 0x{int(out_endpoint.bEndpointAddress):02x}")
        print(f"  read target: {'address' if endpoint_address else 'endpoint object'}")
        print(f"  chunk size: {chunk_size}")
        print(f"  timeout ms: {timeout_ms}")

        started_at = time.monotonic()
        data = device.read(read_target, chunk_size, timeout_ms)
        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        packet = bytes(data)
        first_packet_seen = True
        print(f"  read: OK bytes={len(packet)} elapsed_ms={elapsed_ms:.1f}")
        print_packet_hint(packet)
        return 0
    except Exception as exc:
        print(f"  read: FAILED: {describe_usb_error(exc)}")
        return 1
    finally:
        try:
            if interface_number is not None:
                usb.util.release_interface(device, interface_number)
        except Exception:
            pass
        try:
            disable_quicktime_config(device)
        except Exception:
            pass
        try:
            usb.util.dispose_resources(device)
        except Exception:
            pass
        if first_packet_seen:
            print("  first packet reached Python; failures after this are protocol/decoder-level.")
        else:
            print("  no packet reached Python; failures here are machine/libusb/USB access-level.")


def print_packet_hint(packet: bytes) -> None:
    if len(packet) < 8:
        print(f"  packet hint: too short, hex={packet.hex()}")
        return
    packet_length = struct.unpack("<I", packet[:4])[0]
    magic = struct.unpack("<I", packet[4:8])[0]
    try:
        magic_ascii = packet[4:8].decode("ascii")
    except UnicodeDecodeError:
        magic_ascii = "<non-ascii>"
    print(f"  first packet length field: {packet_length}")
    print(f"  first packet magic: 0x{magic:08x} ascii={magic_ascii!r}")


if __name__ == "__main__":
    raise SystemExit(main())
