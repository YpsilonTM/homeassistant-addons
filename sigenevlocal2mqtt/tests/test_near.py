from __future__ import annotations

from src.catalog import tags_by_id
from src.near import (
    NearError,
    build_batch_read,
    build_batch_write,
    decode_batch_read,
    decode_batch_write,
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
