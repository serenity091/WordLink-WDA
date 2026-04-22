"""Minimal CoreMedia and binary dictionary parsing for iPhone USB mirroring."""

from __future__ import annotations

import enum
import struct
import time
from ctypes import Structure, c_uint32, c_uint64
from dataclasses import dataclass
from typing import Any


class BinaryParseError(RuntimeError):
    pass


class DictConst(enum.IntEnum):
    KEY_VALUE = 0x6B657976
    STRING_KEY = 0x7374726B
    INT_KEY = 0x6964786B
    BOOL_VALUE = 0x62756C76
    DICTIONARY = 0x64696374
    DATA_VALUE = 0x64617476
    STRING_VALUE = 0x73747276
    NUMBER_VALUE = 0x6E6D6276


class DescriptorConst(enum.IntEnum):
    FORMAT_DESCRIPTION = 0x66647363
    MEDIA_TYPE_VIDEO = 0x76696465
    MEDIA_TYPE = 0x6D646961
    VIDEO_DIMENSION = 0x7664696D
    CODEC = 0x636F6463
    EXTENSION = 0x6578746E
    CODEC_AVC1 = 0x61766331


class CMSampleConst(enum.IntEnum):
    SAMPLE_BUFFER = 0x73627566
    OUTPUT_PRESENTATION_TS = 0x6F707473
    SAMPLE_TIMING_ARRAY = 0x73746961
    SAMPLE_DATA = 0x73646174
    SAMPLE_ATTACHMENTS = 0x73617474
    SAMPLE_ATTACHMENT_ARRAY = 0x73617279
    SAMPLE_SIZES = 0x7373697A
    NUM_SAMPLES = 0x6E736D70
    TIMING_INFO_LENGTH = 72


class NSNumber:
    def __init__(self, type_specifier: int, value: int | float) -> None:
        self.type_specifier = type_specifier
        self.value = value

    @classmethod
    def from_bytes(cls, data: bytes) -> "NSNumber":
        if not data:
            raise BinaryParseError("NSNumber is empty")
        type_specifier = data[0]
        if type_specifier == 3:
            value = struct.unpack("<I", data[1:5])[0]
        elif type_specifier == 4:
            value = struct.unpack("<Q", data[1:9])[0]
        elif type_specifier == 5:
            value = struct.unpack("<I", data[1:5])[0]
        elif type_specifier == 6:
            value = struct.unpack("<d", data[1:9])[0]
        else:
            raise BinaryParseError(f"Unsupported NSNumber type: {type_specifier}")
        return cls(type_specifier, value)

    def to_bytes(self) -> bytes:
        if self.type_specifier == 3:
            return b"\x03" + struct.pack("<I", int(self.value))
        if self.type_specifier == 4:
            return b"\x04" + struct.pack("<Q", int(self.value))
        if self.type_specifier == 6:
            return b"\x06" + struct.pack("<d", float(self.value))
        raise BinaryParseError(f"Unsupported NSNumber type: {self.type_specifier}")


class CMTime(Structure):
    _fields_ = [
        ("value", c_uint64),
        ("scale", c_uint32),
        ("flags", c_uint32),
        ("epoch", c_uint64),
    ]

    def scaled_value(self, other: "CMTime") -> float:
        return float(self.value) * (float(other.scale) / float(self.scale))


class CMClock:
    def __init__(self, clock_id: int, scale: int = 1_000_000_000) -> None:
        self.clock_id = clock_id
        self.scale = scale
        self.started_ns = time.time_ns()

    def get_time(self) -> CMTime:
        return CMTime(
            value=int((time.time_ns() - self.started_ns) * (self.scale / 1_000_000_000)),
            scale=self.scale,
            flags=1,
            epoch=0,
        )


@dataclass
class FormatDescription:
    media_type: int
    width: int | None = None
    height: int | None = None
    codec: int | None = None
    pps: bytes | None = None
    sps: bytes | None = None


@dataclass
class CMSampleBuffer:
    media_type: int
    output_pts: CMTime | None = None
    format_description: FormatDescription | None = None
    has_format_description: bool = False
    sample_data: bytes | None = None
    num_samples: int | None = None
    sample_sizes: list[int] | None = None
    attachments: dict[Any, Any] | None = None


def read_length_magic(data: bytes, expected_magic: int) -> tuple[int, bytes]:
    if len(data) < 8:
        raise BinaryParseError(f"Need 8 bytes for length/magic, got {len(data)}")
    length, magic = struct.unpack("<II", data[:8])
    if length > len(data):
        raise BinaryParseError(f"Chunk length {length} exceeds available {len(data)}")
    if magic != expected_magic:
        raise BinaryParseError(f"Expected magic 0x{expected_magic:08x}, got 0x{magic:08x}")
    return int(length), data[8:]


def write_length_magic(length: int, magic: int) -> bytes:
    return struct.pack("<II", length, int(magic))


def serialize_string_key_dict(values: dict[str, Any]) -> bytes:
    body = b""
    for key, value in values.items():
        key_bytes = write_length_magic(len(key.encode("utf-8")) + 8, DictConst.STRING_KEY) + key.encode("utf-8")
        value_bytes = serialize_value(value)
        body += write_length_magic(len(key_bytes) + len(value_bytes) + 8, DictConst.KEY_VALUE)
        body += key_bytes + value_bytes
    return write_length_magic(len(body) + 8, DictConst.DICTIONARY) + body


def serialize_value(value: Any) -> bytes:
    if isinstance(value, bool):
        return write_length_magic(9, DictConst.BOOL_VALUE) + struct.pack("?", value)
    if isinstance(value, NSNumber):
        payload = value.to_bytes()
        return write_length_magic(len(payload) + 8, DictConst.NUMBER_VALUE) + payload
    if isinstance(value, str):
        payload = value.encode("utf-8")
        return write_length_magic(len(payload) + 8, DictConst.STRING_VALUE) + payload
    if isinstance(value, (bytes, bytearray)):
        payload = bytes(value)
        return write_length_magic(len(payload) + 8, DictConst.DATA_VALUE) + payload
    if isinstance(value, dict):
        return serialize_string_key_dict(value)
    raise TypeError(f"Unsupported serialized value type: {type(value).__name__}")


def parse_string_dict(data: bytes) -> dict[str, Any]:
    _, remaining = read_length_magic(data, DictConst.DICTIONARY)
    result: dict[str, Any] = {}
    while remaining:
        pair_len, _ = read_length_magic(remaining, DictConst.KEY_VALUE)
        pair = remaining[8:pair_len]
        key, after_key = parse_string_key(pair)
        result[key] = parse_value(after_key)
        remaining = remaining[pair_len:]
    return result


def parse_int_dict(data: bytes, magic: int) -> dict[int, Any]:
    _, remaining = read_length_magic(data, magic)
    result: dict[int, Any] = {}
    while remaining:
        pair_len, _ = read_length_magic(remaining, DictConst.KEY_VALUE)
        pair = remaining[8:pair_len]
        key, after_key = parse_int_key(pair)
        result[key] = parse_value(after_key)
        remaining = remaining[pair_len:]
    return result


def parse_string_key(data: bytes) -> tuple[str, bytes]:
    length, _ = read_length_magic(data, DictConst.STRING_KEY)
    return data[8:length].decode("utf-8"), data[length:]


def parse_int_key(data: bytes) -> tuple[int, bytes]:
    length, _ = read_length_magic(data, DictConst.INT_KEY)
    return struct.unpack("<H", data[8:10])[0], data[length:]


def parse_value(data: bytes) -> Any:
    if len(data) < 8:
        raise BinaryParseError("Value chunk is too short")
    length, magic = struct.unpack("<II", data[:8])
    payload = data[8:length]
    if magic == DictConst.STRING_VALUE:
        return payload.decode("utf-8")
    if magic == DictConst.DATA_VALUE:
        return payload
    if magic == DictConst.BOOL_VALUE:
        return bool(payload[0])
    if magic == DictConst.NUMBER_VALUE:
        return NSNumber.from_bytes(payload)
    if magic == DictConst.DICTIONARY:
        try:
            return parse_string_dict(data[:length])
        except Exception:
            return parse_int_dict(data[:length], DictConst.DICTIONARY)
    if magic == DescriptorConst.FORMAT_DESCRIPTION:
        return parse_format_description(data[:length])
    raise BinaryParseError(f"Unsupported value magic: 0x{magic:08x}")


def parse_header(data: bytes, packet_magic: int, message_magic: int) -> tuple[bytes, int]:
    if len(data) < 16:
        raise BinaryParseError("Packet header is too short")
    packet, clock_ref, message = struct.unpack("<IQI", data[:16])
    if packet != packet_magic or message != message_magic:
        raise BinaryParseError(
            f"Unexpected packet header packet=0x{packet:08x} message=0x{message:08x}"
        )
    return data[16:], clock_ref


def parse_format_description(data: bytes) -> FormatDescription:
    length, remaining = read_length_magic(data, DescriptorConst.FORMAT_DESCRIPTION)
    remaining = data[8:length]
    media_type, remaining = parse_media_type(remaining)
    if media_type != DescriptorConst.MEDIA_TYPE_VIDEO:
        raise BinaryParseError(f"Unsupported non-video media type: 0x{media_type:08x}")

    width, height, remaining = parse_video_dimension(remaining)
    codec, remaining = parse_codec(remaining)
    extensions = parse_int_dict(remaining, DescriptorConst.EXTENSION)
    pps, sps = extract_pps_sps(extensions)
    return FormatDescription(media_type=media_type, width=width, height=height, codec=codec, pps=pps, sps=sps)


def parse_media_type(data: bytes) -> tuple[int, bytes]:
    length, remaining = read_length_magic(data, DescriptorConst.MEDIA_TYPE)
    return struct.unpack("<I", remaining[:4])[0], data[length:]


def parse_video_dimension(data: bytes) -> tuple[int, int, bytes]:
    length, remaining = read_length_magic(data, DescriptorConst.VIDEO_DIMENSION)
    width, height = struct.unpack("<II", remaining[:8])
    return width, height, data[length:]


def parse_codec(data: bytes) -> tuple[int, bytes]:
    length, remaining = read_length_magic(data, DescriptorConst.CODEC)
    return struct.unpack("<I", remaining[:4])[0], data[length:]


def extract_pps_sps(extensions: dict[int, Any]) -> tuple[bytes | None, bytes | None]:
    avcc = extensions.get(49)
    if isinstance(avcc, dict):
        avcc = avcc.get(105)
    if not isinstance(avcc, (bytes, bytearray)):
        return None, None
    data = bytes(avcc)
    if len(data) < 11:
        return None, None
    pps_length = data[7]
    pps = data[8 : 8 + pps_length]
    sps_index = 10 + pps_length
    if sps_index >= len(data):
        return pps or None, None
    sps_length = data[sps_index]
    sps = data[sps_index + 1 : sps_index + 1 + sps_length]
    return pps or None, sps or None


def parse_sample_buffer(data: bytes, media_type: int) -> CMSampleBuffer:
    length, remaining = read_length_magic(data, CMSampleConst.SAMPLE_BUFFER)
    if length > len(data):
        raise BinaryParseError("CMSampleBuffer length exceeds input")

    sample = CMSampleBuffer(media_type=media_type)
    remaining = data[8:length]
    while remaining:
        if len(remaining) < 8:
            raise BinaryParseError("CMSampleBuffer child chunk is too short")
        child_length, code = struct.unpack("<II", remaining[:8])
        child = remaining[:child_length]
        payload = child[8:]

        if code == CMSampleConst.OUTPUT_PRESENTATION_TS:
            sample.output_pts = CMTime.from_buffer_copy(payload)
        elif code == CMSampleConst.SAMPLE_TIMING_ARRAY:
            pass
        elif code == CMSampleConst.SAMPLE_DATA:
            sample.sample_data = payload
        elif code == CMSampleConst.NUM_SAMPLES:
            sample.num_samples = struct.unpack("<I", payload[:4])[0]
        elif code == CMSampleConst.SAMPLE_SIZES:
            sample.sample_sizes = [struct.unpack("<I", payload[i : i + 4])[0] for i in range(0, len(payload), 4)]
        elif code == DescriptorConst.FORMAT_DESCRIPTION:
            sample.has_format_description = True
            sample.format_description = parse_format_description(child)
        elif code == CMSampleConst.SAMPLE_ATTACHMENTS:
            sample.attachments = parse_int_dict(child, CMSampleConst.SAMPLE_ATTACHMENTS)
        elif code == CMSampleConst.SAMPLE_ATTACHMENT_ARRAY:
            pass
        else:
            raise BinaryParseError(f"Unknown CMSampleBuffer child magic: 0x{code:08x}")

        remaining = remaining[child_length:]
    return sample

