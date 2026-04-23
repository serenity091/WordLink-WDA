"""PyUSB transport for the hidden QuickTime screen-mirroring configuration."""

from __future__ import annotations

import logging
import os
import queue
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any

import usb.backend.libusb1
import usb.core
import usb.util
from usb.core import Configuration

from .protocol import MessageProcessor, SampleConsumer


LOG = logging.getLogger(__name__)
QT_REENUMERATE_ATTEMPTS = 16
QT_REENUMERATE_INTERVAL_SECONDS = 0.25
READ_CHUNK_SIZE = 64 * 1024
READ_TIMEOUT_MS = 3000
INITIAL_READ_RETRY_SECONDS = 1.5
INITIAL_READ_RETRY_INTERVAL_SECONDS = 0.1


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def runtime_root() -> Path:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.argv[0]).resolve().parent
    return Path(__file__).resolve().parents[1]


def get_libusb_backend() -> Any:
    for path in (
        runtime_root() / "lib/libusb-1.0.dylib",
        Path("/opt/homebrew/lib/libusb-1.0.dylib"),
        Path("/usr/local/lib/libusb-1.0.dylib"),
    ):
        if not path.exists():
            continue
        backend = usb.backend.libusb1.get_backend(find_library=lambda _name, library_path=str(path): str(path))
        if backend is not None:
            LOG.debug("Using libusb backend: %s", path)
            return backend
    raise RuntimeError("Could not load libusb backend from bundled lib/ or Homebrew")


def normalize_udid(udid: str | None) -> str | None:
    if not udid:
        return None
    return "".join(ch for ch in udid.upper() if ch.isalnum())


def safe_serial(device: Any) -> str | None:
    try:
        return str(device.serial_number)
    except Exception:
        return None


def is_quicktime_candidate(device: Any) -> bool:
    try:
        if device.idVendor != 0x05AC:
            return False
    except Exception:
        return False

    try:
        if device.bDeviceClass == 0xFE:
            return True
    except Exception:
        pass

    try:
        for config in device:
            if usb.util.find_descriptor(config, bInterfaceSubClass=0xFE) is not None:
                return True
            if usb.util.find_descriptor(config, bInterfaceSubClass=0x2A) is not None:
                return True
    except Exception:
        return False
    return False


def find_ios_device(backend: Any, udid: str | None = None) -> Any:
    normalized_udid = normalize_udid(udid)
    candidates: list[tuple[Any, str | None]] = []
    for device in usb.core.find(find_all=True, backend=backend):
        if is_quicktime_candidate(device):
            candidates.append((device, safe_serial(device)))

    if not candidates:
        raise RuntimeError("No QuickTime-compatible Apple USB screen device found")

    if normalized_udid:
        for device, serial in candidates:
            normalized_serial = normalize_udid(serial)
            if normalized_serial and (normalized_udid in normalized_serial or normalized_serial in normalized_udid):
                return device
        available = ", ".join(serial or "<unknown>" for _, serial in candidates)
        raise RuntimeError(f"Could not match iOS USB screen device for UDID {udid}; available: {available}")

    return candidates[0][0]


def enable_quicktime_config(device: Any, backend: Any, stop_signal: threading.Event) -> Any:
    LOG.info("Enabling hidden QuickTime USB screen configuration")
    value = device.ctrl_transfer(0x40, 0x52, 0, 2, b"")
    if value:
        raise RuntimeError(f"Enable QuickTime USB config failed: {value}")

    serial = safe_serial(device)
    for _ in range(QT_REENUMERATE_ATTEMPTS):
        if stop_signal.is_set():
            break
        time.sleep(QT_REENUMERATE_INTERVAL_SECONDS)
        try:
            return find_ios_device(backend, serial)
        except Exception as exc:
            LOG.debug("Waiting for QuickTime USB device to re-enumerate: %s", exc)

    stop_signal.set()
    raise RuntimeError("Timed out waiting for QuickTime USB config to re-enumerate")


def disable_quicktime_config(device: Any) -> None:
    try:
        LOG.debug("Disabling hidden QuickTime USB screen configuration")
        device.ctrl_transfer(0x40, 0x52, 0, 0, b"")
    except Exception as exc:
        LOG.debug("QuickTime USB config disable failed: %s", exc)


def find_quicktime_interface(device: Any) -> tuple[int, Any]:
    for index in range(device.bNumConfigurations):
        config = Configuration(device, index)
        interface = usb.util.find_descriptor(config, bInterfaceSubClass=0x2A)
        if interface is not None:
            return index + 1, interface
    raise RuntimeError("Could not find QuickTime USB interface")


def find_stream_endpoints(interface: Any) -> tuple[Any, Any]:
    in_endpoint = None
    out_endpoint = None
    for endpoint in interface:
        direction = usb.util.endpoint_direction(endpoint.bEndpointAddress)
        if direction == usb.util.ENDPOINT_IN:
            in_endpoint = endpoint
        elif direction == usb.util.ENDPOINT_OUT:
            out_endpoint = endpoint
    if in_endpoint is None or out_endpoint is None:
        raise RuntimeError("Could not find QuickTime USB input/output endpoints")
    return in_endpoint, out_endpoint


def describe_usb_error(exc: BaseException) -> str:
    parts = [str(exc) or exc.__class__.__name__]
    for attr in ("errno", "strerror", "backend_error_code", "backend_error_name"):
        value = getattr(exc, attr, None)
        if value not in (None, ""):
            parts.append(f"{attr}={value}")
    return "; ".join(parts)


def claim_interface(device: Any, interface: Any) -> int:
    interface_number = int(interface.bInterfaceNumber)
    if not env_bool("WORDLINK_USB_CLAIM_INTERFACE", False):
        LOG.info("Skipping explicit USB interface claim; set WORDLINK_USB_CLAIM_INTERFACE=1 to enable it")
        return interface_number

    try:
        if hasattr(device, "is_kernel_driver_active") and device.is_kernel_driver_active(interface_number):
            LOG.debug("Detaching kernel driver from QuickTime interface %s", interface_number)
            device.detach_kernel_driver(interface_number)
    except Exception as exc:
        LOG.debug("Kernel driver detach check failed for interface %s: %s", interface_number, exc)

    try:
        usb.util.claim_interface(device, interface_number)
        LOG.info("Claimed QuickTime USB interface %s", interface_number)
    except Exception as exc:
        LOG.warning(
            "Could not explicitly claim QuickTime USB interface %s: %s",
            interface_number,
            describe_usb_error(exc),
        )
    return interface_number


class BytePipe:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.condition = threading.Condition()
        self.closed = False

    def put(self, data: bytes | bytearray) -> None:
        with self.condition:
            self.buffer.extend(bytes(data))
            self.condition.notify_all()

    def get(self, size: int, timeout: float = 5.0) -> bytes | None:
        deadline = time.monotonic() + timeout
        with self.condition:
            while len(self.buffer) < size and not self.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.condition.wait(remaining)
            if len(self.buffer) < size:
                return None
            data = bytes(self.buffer[:size])
            del self.buffer[:size]
            return data

    def close(self) -> None:
        with self.condition:
            self.closed = True
            self.condition.notify_all()


def start_reading(consumer: SampleConsumer, device: Any, backend: Any, stop_signal: threading.Event) -> None:
    disable_quicktime_config(device)
    device.set_configuration()
    device = enable_quicktime_config(device, backend, stop_signal)

    config_index, quicktime_interface = find_quicktime_interface(device)
    device.set_configuration(config_index)
    device.ctrl_transfer(0x02, 0x01, 0, 0x86, b"")
    device.ctrl_transfer(0x02, 0x01, 0, 0x05, b"")
    in_endpoint, out_endpoint = find_stream_endpoints(quicktime_interface)
    interface_number = claim_interface(device, quicktime_interface)
    in_address = int(in_endpoint.bEndpointAddress)
    out_address = int(out_endpoint.bEndpointAddress)
    read_endpoint = in_address if env_bool("WORDLINK_USB_USE_ENDPOINT_ADDRESS", False) else in_endpoint
    write_endpoint = out_address if env_bool("WORDLINK_USB_USE_ENDPOINT_ADDRESS", False) else out_endpoint
    read_chunk_size = env_int("WORDLINK_USB_READ_CHUNK_SIZE", READ_CHUNK_SIZE)
    LOG.info(
        "QuickTime USB connection ready: interface=%s in=0x%02x out=0x%02x chunk=%d endpoint_mode=%s",
        interface_number,
        in_address,
        out_address,
        read_chunk_size,
        "address" if env_bool("WORDLINK_USB_USE_ENDPOINT_ADDRESS", False) else "object",
    )

    byte_pipe = BytePipe()
    errors: queue.Queue[BaseException] = queue.Queue()
    processor = MessageProcessor(device, in_address, write_endpoint, stop_signal, consumer)
    first_packet_seen = threading.Event()

    def read_usb() -> None:
        started_at = time.monotonic()
        while not stop_signal.is_set():
            try:
                data = device.read(read_endpoint, read_chunk_size, READ_TIMEOUT_MS)
                first_packet_seen.set()
                byte_pipe.put(data)
            except Exception as exc:
                if (
                    not first_packet_seen.is_set()
                    and time.monotonic() - started_at < INITIAL_READ_RETRY_SECONDS
                    and not stop_signal.is_set()
                ):
                    LOG.debug("Initial QuickTime USB read failed, retrying: %s", describe_usb_error(exc))
                    time.sleep(INITIAL_READ_RETRY_INTERVAL_SECONDS)
                    continue
                if not stop_signal.is_set():
                    errors.put(RuntimeError(f"QuickTime USB read failed: {describe_usb_error(exc)}"))
                stop_signal.set()
                byte_pipe.close()
                break

    def parse_messages() -> None:
        while not stop_signal.is_set():
            length_buffer = byte_pipe.get(4)
            if length_buffer is None:
                continue
            packet_length = struct.unpack("<I", length_buffer)[0]
            if packet_length < 4:
                errors.put(RuntimeError(f"Invalid QuickTime packet length: {packet_length}"))
                stop_signal.set()
                break
            payload = byte_pipe.get(packet_length - 4)
            if payload is None:
                continue
            try:
                processor.receive_data(payload)
            except Exception as exc:
                errors.put(exc)
                stop_signal.set()
                break

    threads = [
        threading.Thread(target=read_usb, name="quicktime-usb-read", daemon=True),
        threading.Thread(target=parse_messages, name="quicktime-usb-parse", daemon=True),
    ]
    for thread in threads:
        thread.start()

    try:
        while not stop_signal.wait(0.25):
            if not errors.empty():
                raise errors.get()
        if not errors.empty():
            raise errors.get()
    finally:
        byte_pipe.close()
        try:
            if first_packet_seen.is_set():
                try:
                    processor.close_session()
                except Exception as exc:
                    LOG.debug("QuickTime close-session cleanup failed: %s", describe_usb_error(exc))
        finally:
            disable_quicktime_config(device)
            with contextlib_suppress(Exception):
                usb.util.release_interface(device, interface_number)
            with contextlib_suppress(Exception):
                usb.util.dispose_resources(device)
            consumer.stop()


class contextlib_suppress:
    def __init__(self, *exceptions: type[BaseException]) -> None:
        self.exceptions = exceptions or (Exception,)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, self.exceptions)
