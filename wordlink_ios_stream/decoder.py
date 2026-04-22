"""H.264 decode and CoreMedia sample consumption."""

from __future__ import annotations

import importlib
import queue
import struct
from typing import Any

import numpy as np

from .coremedia import CMSampleBuffer


START_CODE = b"\x00\x00\x00\x01"


class H264FrameDecoder:
    def __init__(self, frame_queue: queue.Queue[np.ndarray]) -> None:
        self.frame_queue = frame_queue
        self.codec: Any = None
        self.started = False
        self.failed = False
        self.last_error: str | None = None
        self.decode_errors = 0
        self.decoded_frames = 0

    def start(self) -> None:
        if self.started:
            return
        av = importlib.import_module("av")
        self.codec = av.CodecContext.create("h264", "r")
        self.started = True

    def push_parameter_sets(self, pps: bytes | None, sps: bytes | None) -> None:
        self.start()
        for parameter_set in (pps, sps):
            if parameter_set:
                self.push_bytes(START_CODE + parameter_set)

    def push_sample_data(self, sample_data: bytes) -> None:
        self.start()
        for nalu in iter_length_prefixed_nalus(sample_data):
            self.push_bytes(START_CODE + nalu)

    def push_bytes(self, payload: bytes) -> None:
        if self.codec is None:
            return
        try:
            for packet in self.codec.parse(payload):
                for frame in self.codec.decode(packet):
                    image = frame.to_ndarray(format="bgr24")
                    self.decoded_frames += 1
                    put_latest(self.frame_queue, image)
        except Exception as exc:
            self.failed = True
            self.decode_errors += 1
            self.last_error = str(exc)

    def clear_error(self) -> None:
        self.failed = False
        self.last_error = None

    def stop(self) -> None:
        self.codec = None
        self.started = False


class ScreenSampleConsumer:
    def __init__(self, frame_queue: queue.Queue[np.ndarray]) -> None:
        self.decoder = H264FrameDecoder(frame_queue)
        self.video_packets_seen = 0
        self.width: int | None = None
        self.height: int | None = None
        self.codec: int | None = None
        self.have_format_description = False

    def consume(self, sample: CMSampleBuffer) -> bool:
        self.video_packets_seen += 1
        if sample.has_format_description and sample.format_description:
            description = sample.format_description
            self.width = description.width
            self.height = description.height
            self.codec = description.codec
            self.decoder.push_parameter_sets(description.pps, description.sps)
            self.have_format_description = True

        if self.have_format_description and sample.sample_data:
            self.decoder.push_sample_data(sample.sample_data)
        return True

    def stop(self) -> None:
        self.decoder.stop()

    def has_error(self) -> bool:
        return self.decoder.failed

    def error_message(self) -> str:
        return self.decoder.last_error or "H.264 decoder failed"

    def clear_error(self) -> None:
        self.decoder.clear_error()


def iter_length_prefixed_nalus(data: bytes) -> list[bytes]:
    nalus: list[bytes] = []
    offset = 0
    while offset + 4 <= len(data):
        nalu_length = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        if nalu_length <= 0 or offset + nalu_length > len(data):
            break
        nalus.append(data[offset : offset + nalu_length])
        offset += nalu_length
    return nalus


def put_latest(frame_queue: queue.Queue[np.ndarray], frame: np.ndarray) -> None:
    while frame_queue.qsize() >= frame_queue.maxsize:
        try:
            frame_queue.get_nowait()
        except queue.Empty:
            break
    try:
        frame_queue.put_nowait(frame)
    except queue.Full:
        pass
