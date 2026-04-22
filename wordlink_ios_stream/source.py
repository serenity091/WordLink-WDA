"""Public frame-source API used by the WordLink solver."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .decoder import ScreenSampleConsumer
from .transport import find_ios_device, get_libusb_backend, normalize_udid, start_reading


ERROR_FRAME_GRACE_SECONDS = 2.0


@dataclass(frozen=True)
class FrameSourceStats:
    frames_received: int
    decoder_errors: int
    stream_errors: int
    video_packets_seen: int
    width: int | None
    height: int | None
    last_error: str | None
    running: bool


class IOSVideoFrameSource:
    def __init__(
        self,
        udid: str | None = None,
        queue_size: int = 2,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.udid = normalize_udid(udid) if udid else None
        self.queue_size = max(int(queue_size), 1)
        self.on_error = on_error
        self.frame_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=self.queue_size)
        self.consumer = ScreenSampleConsumer(self.frame_queue)
        self.stop_signal = threading.Event()
        self.reader_thread: threading.Thread | None = None
        self.last_frame_at = time.monotonic()
        self.stream_errors = 0
        self.last_stream_error: str | None = None

    def start(self) -> None:
        if self.reader_thread is not None and self.reader_thread.is_alive():
            return
        backend = get_libusb_backend()
        device = find_ios_device(backend, self.udid)
        self.stop_signal.clear()
        self.reader_thread = threading.Thread(
            target=self._read_stream,
            args=(backend, device),
            name="quicktime-usb-screen-source",
            daemon=True,
        )
        self.reader_thread.start()

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        self._restart_dead_reader()
        try:
            frame = self.frame_queue.get(timeout=timeout)
        except queue.Empty:
            self._handle_stall()
            return None
        self.last_frame_at = time.monotonic()
        self.consumer.clear_error()
        self.last_stream_error = None
        return frame

    def read_latest(self, timeout: float = 1.0) -> np.ndarray | None:
        frame = self.read(timeout=timeout)
        if frame is None:
            return None
        while True:
            try:
                frame = self.frame_queue.get_nowait()
            except queue.Empty:
                return frame

    def drain(self) -> None:
        while True:
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                return

    def stop(self) -> None:
        self.stop_signal.set()
        self.consumer.stop()
        if self.reader_thread is not None and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=2.0)
        self.reader_thread = None

    def restart(self) -> None:
        self._emit_error("QuickTime USB screen stream stalled; restarting")
        self.stop()
        self.frame_queue = queue.Queue(maxsize=self.queue_size)
        self.consumer = ScreenSampleConsumer(self.frame_queue)
        self.stop_signal = threading.Event()
        self.last_frame_at = time.monotonic()
        self.start()

    def stats(self) -> FrameSourceStats:
        return FrameSourceStats(
            frames_received=self.consumer.decoder.decoded_frames,
            decoder_errors=self.consumer.decoder.decode_errors,
            stream_errors=self.stream_errors,
            video_packets_seen=self.consumer.video_packets_seen,
            width=self.consumer.width,
            height=self.consumer.height,
            last_error=self.consumer.error_message() if self.consumer.has_error() else self.last_stream_error,
            running=self.reader_thread is not None and self.reader_thread.is_alive(),
        )

    def _read_stream(self, backend, device) -> None:
        try:
            start_reading(self.consumer, device, backend, self.stop_signal)
        except Exception as exc:
            self.stream_errors += 1
            self.last_stream_error = str(exc)
            if not self.stop_signal.is_set():
                self._emit_error(f"QuickTime USB stream failed: {exc}")
            self.stop_signal.set()

    def _handle_stall(self) -> None:
        if time.monotonic() - self.last_frame_at < ERROR_FRAME_GRACE_SECONDS:
            return
        if self.consumer.has_error():
            self._emit_error(self.consumer.error_message())
            self.restart()
        elif self.last_stream_error:
            self.restart()

    def _restart_dead_reader(self) -> None:
        if self.stop_signal.is_set():
            return
        if self.reader_thread is not None and self.reader_thread.is_alive():
            return
        self.restart()

    def _emit_error(self, message: str) -> None:
        if self.on_error is not None:
            self.on_error(message)
