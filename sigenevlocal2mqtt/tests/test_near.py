from __future__ import annotations

import struct
from unittest.mock import MagicMock

from src.catalog import tags_by_id
from src.near import (
    NearError,
    build_batch_read,
    build_batch_write,
    decode_batch_read,
    decode_batch_write,
    parse_frame,
    recv_frame,
)


def test_batch_read_payload() -> None:
    tag = tags_by_id()[12194]
    payload = build_batch_read([tag])
    assert payload[0] == 3
    assert payload[3] == 1
    assert payload[4:6] == bytes.fromhex("a22f")
    assert payload[6] == 2


def test_batch_write_payload() -> None:
    tag = tags_by_id()[12194]
    payload = build_batch_write(tag, tag.encode(8))
    assert payload == bytes.fromhex("040801a22f0220030000")


def test_decode_batch_read() -> None:
    body = bytes.fromhex("030600a22f0220030000")
    values = decode_batch_read(body)
    assert tags_by_id()[12194].decode(values[12194]) == 8.0


def test_decode_exception() -> None:
    try:
        decode_batch_read(bytes.fromhex("1f00000b"))
    except NearError as exc:
        assert exc.code == 11
    else:
        raise AssertionError("expected NearError")


def test_decode_write_ack() -> None:
    decoded = decode_batch_write(bytes.fromhex("840601a22f020000"))
    assert decoded["cmd"] == 4


def test_recv_frame_buffers_leftover() -> None:
    frame1 = struct.pack("<HHH", 1, 21319, 4) + b"ABCD"
    frame2 = struct.pack("<HHH", 2, 21319, 3) + b"XYZ"
    sock = MagicMock()
    sock.recv.side_effect = [frame1 + frame2[:4], frame2[4:], b""]
    buf = bytearray()
    assert recv_frame(sock, 1.0, buf) == frame1
    assert bytes(buf) == frame2[:4]
    assert recv_frame(sock, 1.0, buf) == frame2
    assert buf == bytearray()


def test_recv_frame_incomplete_returns_empty() -> None:
    partial = struct.pack("<HHH", 1, 21319, 10) + b"short"
    sock = MagicMock()
    sock.recv.side_effect = [partial, TimeoutError()]
    buf = bytearray()
    assert recv_frame(sock, 0.2, buf) == b""
    assert bytes(buf) == partial


def test_parse_frame_seq() -> None:
    frame = struct.pack("<HHHB", 42, 21319, 1, 0)
    parsed = parse_frame(frame)
    assert parsed["seq"] == 42
