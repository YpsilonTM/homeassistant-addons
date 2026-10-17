from __future__ import annotations

import struct
from collections.abc import Sequence
from typing import Any

from src.catalog import Register

# Sungrow unavailable sentinels (protocol §1.2).
SENTINEL_U16 = 0xFFFF
SENTINEL_U32 = 0xFFFFFFFF
SENTINEL_S16 = 0x7FFF
SENTINEL_S32 = 0x7FFFFFFF


def is_unavailable(raw: int, data_type: str) -> bool:
    if data_type == "u16":
        return raw == SENTINEL_U16
    if data_type == "u32":
        return raw == SENTINEL_U32
    if data_type == "s16":
        return raw == SENTINEL_S16
    if data_type == "s32":
        return raw == SENTINEL_S32
    return False


def words_to_bytes(words: Sequence[int], *, word_order: str = "little") -> bytes:
    """Assemble Modbus words into big-endian byte stream for struct unpack.

    Sungrow U32/S32 use little-endian double-word (low word first on the wire).
    U16/S16 and UTF-8 use big-endian byte order within each word.
    """
    normalized = [int(word) & 0xFFFF for word in words]
    if word_order == "little" and len(normalized) == 2:
        normalized = list(reversed(normalized))
    raw = bytearray()
    for word in normalized:
        raw.append((word >> 8) & 0xFF)
        raw.append(word & 0xFF)
    return bytes(raw)


def decode_utf8(words: Sequence[int]) -> str:
    raw = words_to_bytes(words, word_order="big")
    text = raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
    return text


def decode_raw_value(words: Sequence[int], data_type: str) -> int | str:
    if data_type == "utf8":
        return decode_utf8(words)

    if data_type in {"u16", "s16"} and len(words) != 1:
        raise ValueError(f"{data_type} requires exactly one word, got {len(words)}")
    if data_type in {"u32", "s32"} and len(words) != 2:
        raise ValueError(f"{data_type} requires exactly two words, got {len(words)}")

    word_order = "little" if data_type in {"u32", "s32"} else "big"
    raw = words_to_bytes(words, word_order=word_order)

    if data_type == "u16":
        return struct.unpack(">H", raw)[0]
    if data_type == "s16":
        return struct.unpack(">h", raw)[0]
    if data_type == "u32":
        return struct.unpack(">I", raw)[0]
    if data_type == "s32":
        return struct.unpack(">i", raw)[0]
    raise ValueError(f"Unsupported data type: {data_type}")


def apply_scale(value: int, *, scale: float, decimals: int) -> int | float:
    transformed = float(value) * scale
    rounded = round(transformed, decimals)
    if decimals == 0:
        return int(rounded)
    return rounded


def decode_register(words: Sequence[int], register: Register) -> Any | None:
    """Decode register words to engineering value, or None if unavailable."""
    if register.data_type == "utf8":
        text = decode_utf8(words)
        return text if text else None

    raw = decode_raw_value(words, register.data_type)
    assert isinstance(raw, int)
    if is_unavailable(raw, register.data_type):
        return None
    return apply_scale(raw, scale=register.scale, decimals=register.decimals)


def encode_register(register: Register, value: float | int | str) -> list[int]:
    """Encode an engineering value into Modbus holding-register words."""
    if register.data_type == "utf8":
        raise ValueError(f"UTF-8 writes are not supported for {register.slug}")

    if register.scale == 0:
        raise ValueError(f"scale must not be zero for {register.slug}")

    numeric = float(value)
    if register.min_value is not None and numeric < register.min_value:
        raise ValueError(
            f"Value {numeric} below min_value={register.min_value} for {register.slug}"
        )
    if register.max_value is not None and numeric > register.max_value:
        raise ValueError(
            f"Value {numeric} above max_value={register.max_value} for {register.slug}"
        )

    raw_value = int(round(numeric / register.scale))

    if register.data_type == "u16":
        packed = struct.pack(">H", raw_value & 0xFFFF)
    elif register.data_type == "s16":
        packed = struct.pack(">h", raw_value)
    elif register.data_type == "u32":
        packed = struct.pack(">I", raw_value & 0xFFFFFFFF)
    elif register.data_type == "s32":
        packed = struct.pack(">i", raw_value)
    else:
        raise ValueError(f"Unsupported write data type: {register.data_type}")

    words: list[int] = []
    for idx in range(0, len(packed), 2):
        chunk = packed[idx : idx + 2]
        words.append((chunk[0] << 8) | chunk[1])

    if register.data_type in {"u32", "s32"} and len(words) == 2:
        words = [words[1], words[0]]
    return words
