"""QuickTime USB mirroring control and sample packet handling."""

from __future__ import annotations

import enum
import logging
import struct
import threading
from dataclasses import dataclass
from typing import Protocol

from .coremedia import (
    CMClock,
    CMSampleBuffer,
    NSNumber,
    parse_header,
    parse_sample_buffer,
    parse_string_dict,
    serialize_string_key_dict,
)


LOG = logging.getLogger(__name__)


class SampleConsumer(Protocol):
    def consume(self, sample: CMSampleBuffer) -> bool: ...
    def stop(self) -> None: ...


class PingConst(enum.IntEnum):
    PACKET_MAGIC = 0x70696E67
    LENGTH = 16
    HEADER = 0x0000000100000000


class SyncConst(enum.IntEnum):
    PACKET_MAGIC = 0x73796E63
    REPLY_MAGIC = 0x72706C79
    TIME = 0x74696D65
    CWPA = 0x63777061
    CVRP = 0x63767270
    CLOK = 0x636C6F6B
    OG = 0x676F2120
    SKEW = 0x736B6577
    STOP = 0x73746F70


class AsyncConst(enum.IntEnum):
    PACKET_MAGIC = 0x6173796E
    FEED = 0x66656564
    TJMP = 0x746A6D70
    SRAT = 0x73726174
    SPRP = 0x73707270
    TBAS = 0x74626173
    RELS = 0x72656C73
    HPD1 = 0x68706431
    NEED = 0x6E656564
    HPD0 = 0x68706430


@dataclass
class SyncHeader:
    payload: bytes
    clock_ref: int
    correlation_id: int


class MessageProcessor:
    def __init__(self, device, in_endpoint, out_endpoint, stop_signal: threading.Event, consumer: SampleConsumer) -> None:
        self.device = device
        self.in_endpoint = in_endpoint
        self.out_endpoint = out_endpoint
        self.stop_signal = stop_signal
        self.consumer = consumer
        self.main_clock: CMClock | None = None
        self.need_message: bytes | None = None
        self.release_waiter = threading.Event()

    def receive_data(self, data: bytes) -> None:
        if len(data) < 4:
            LOG.debug("Ignoring short USB message: %d bytes", len(data))
            return
        magic = struct.unpack("<I", data[:4])[0]
        if magic == PingConst.PACKET_MAGIC:
            LOG.info("QuickTime USB stream ping received")
            self.usb_write(new_ping_packet())
        elif magic == SyncConst.PACKET_MAGIC:
            self.handle_sync_packet(data)
        elif magic == AsyncConst.PACKET_MAGIC:
            self.handle_async_packet(data)
        else:
            LOG.debug("Ignoring unknown USB stream packet magic: 0x%08x", magic)

    def handle_sync_packet(self, data: bytes) -> None:
        if len(data) < 16:
            return
        code = struct.unpack("<I", data[12:16])[0]
        if code == SyncConst.OG:
            header = parse_sync_header(data, SyncConst.OG)
            self.usb_write(pack_reply_header(24, header.correlation_id) + struct.pack("<I", 0))
        elif code == SyncConst.CWPA:
            header = parse_sync_header(data, SyncConst.CWPA)
            device_clock_ref = struct.unpack("<Q", header.payload[:8])[0]
            local_clock_ref = device_clock_ref + 1000
            self.usb_write(async_dict_packet(create_hpd1_device(), AsyncConst.HPD1, 1))
            self.usb_write(clock_ref_reply(local_clock_ref, header.correlation_id))
            self.usb_write(async_dict_packet(create_hpd1_device(), AsyncConst.HPD1, 1))
        elif code == SyncConst.CVRP:
            header = parse_sync_header(data, SyncConst.CVRP)
            device_clock_ref = struct.unpack("<Q", header.payload[:8])[0]
            with suppress_parse_errors():
                parse_string_dict(header.payload[8:])
            self.need_message = async_need_packet(device_clock_ref)
            self.usb_write(self.need_message)
            self.usb_write(clock_ref_reply(device_clock_ref + 0x1000AF, header.correlation_id))
        elif code == SyncConst.CLOK:
            header = parse_sync_header(data, SyncConst.CLOK)
            clock_ref = header.clock_ref + 0x10000
            self.main_clock = CMClock(clock_ref)
            self.usb_write(clock_ref_reply(clock_ref, header.correlation_id))
        elif code == SyncConst.TIME:
            header = parse_sync_header(data, SyncConst.TIME)
            now = (self.main_clock or CMClock(header.clock_ref)).get_time()
            self.usb_write(pack_reply_header(44, header.correlation_id) + bytes(now))
        elif code == SyncConst.SKEW:
            header = parse_sync_header(data, SyncConst.SKEW)
            self.usb_write(pack_reply_header(28, header.correlation_id) + struct.pack("<d", 0.0))
        elif code == SyncConst.STOP:
            header = parse_sync_header(data, SyncConst.STOP)
            self.usb_write(pack_reply_header(24, header.correlation_id) + struct.pack("<I", 0))
        else:
            LOG.debug("Ignoring unknown sync packet type: 0x%08x", code)

    def handle_async_packet(self, data: bytes) -> None:
        if len(data) < 16:
            return
        code = struct.unpack("<I", data[12:16])[0]
        if code == AsyncConst.FEED:
            _, _clock_ref = parse_header(data, AsyncConst.PACKET_MAGIC, AsyncConst.FEED)
            self.consumer.consume(parse_sample_buffer(data[16:], media_type=0x76696465))
            if self.need_message:
                self.usb_write(self.need_message)
        elif code == AsyncConst.RELS:
            self.release_waiter.set()
        else:
            LOG.debug("Ignoring async packet type: 0x%08x", code)

    def close_session(self) -> None:
        LOG.info("Closing QuickTime USB stream session")
        if not self.out_endpoint:
            return
        self.usb_write(async_hpd0_packet())
        if not self.release_waiter.wait(5):
            LOG.debug("Timed out waiting for QuickTime stream release")
        self.usb_write(async_hpd0_packet())

    def usb_write(self, data: bytes) -> None:
        self.device.write(self.out_endpoint, data, 100)


class suppress_parse_errors:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return True


def parse_sync_header(data: bytes, message_magic: int) -> SyncHeader:
    payload, clock_ref = parse_header(data, SyncConst.PACKET_MAGIC, message_magic)
    if len(payload) < 8:
        raise RuntimeError("Sync payload is missing correlation id")
    correlation_id = struct.unpack("<Q", payload[:8])[0]
    return SyncHeader(payload=payload[8:], clock_ref=clock_ref, correlation_id=correlation_id)


def pack_reply_header(length: int, correlation_id: int) -> bytes:
    return struct.pack("<IIQI", length, SyncConst.REPLY_MAGIC, correlation_id, 0)


def clock_ref_reply(clock_ref: int, correlation_id: int) -> bytes:
    return pack_reply_header(28, correlation_id) + struct.pack("<Q", clock_ref)


def new_ping_packet() -> bytes:
    return struct.pack("<IIQ", PingConst.LENGTH, PingConst.PACKET_MAGIC, PingConst.HEADER)


def async_dict_packet(values: dict[str, object], subtype: int, clock_ref: int) -> bytes:
    payload = serialize_string_key_dict(values)
    return struct.pack("<IIQI", len(payload) + 20, AsyncConst.PACKET_MAGIC, clock_ref, subtype) + payload


def async_need_packet(clock_ref: int) -> bytes:
    return struct.pack("<IIQI", 20, AsyncConst.PACKET_MAGIC, clock_ref, AsyncConst.NEED)


def async_hpd0_packet() -> bytes:
    return struct.pack("<IIQI", 20, AsyncConst.PACKET_MAGIC, 1, AsyncConst.HPD0)


def create_hpd1_device() -> dict[str, object]:
    return {
        "Valeria": True,
        "HEVCDecoderSupports444": True,
        "DisplaySize": {
            "Width": NSNumber(6, 1920),
            "Height": NSNumber(6, 1200),
        },
    }
