from __future__ import annotations

from src.catalog import Register
from src.decoder import (
    SENTINEL_S16,
    SENTINEL_U16,
    SENTINEL_U32,
    decode_register,
    encode_register,
    words_to_bytes,
)


def _reg(**kwargs) -> Register:
    defaults = dict(
        slug="test",
        name="Test",
        address=5000,
        register_type="input",
        access="RO",
        data_type="u16",
        length_words=1,
        scale=1.0,
        unit="",
        device_class="",
        state_class="",
        platform="sensor",
        fast=False,
        enabled_by_default=False,
        category="",
        ems_gated=False,
        decimals=0,
        supported_device_codes=None,
        unsupported_device_codes=None,
        legacy_modqtt_suffix=None,
        min_value=None,
        max_value=None,
        enum_key=None,
    )
    defaults.update(kwargs)
    return Register(**defaults)


def test_u16_and_s16() -> None:
    assert decode_register([100], _reg(data_type="u16", scale=0.1, decimals=1)) == 10.0
    assert decode_register([0xFFFF], _reg(data_type="s16", scale=0.1, decimals=1)) == -0.1


def test_u32_word_swap() -> None:
    # Documented example style: low word first on wire → engineering value
    # raw 0x00010002 as words [0x0002, 0x0001] with little word order
    value = decode_register([0x0002, 0x0001], _reg(data_type="u32", scale=1.0, length_words=2))
    assert value == 0x00010002


def test_total_dc_power_style() -> None:
    # 1317 W as u32 little-word: high=0, low=1317 → words [1317, 0]
    value = decode_register([1317, 0], _reg(data_type="u32", scale=1.0, length_words=2))
    assert value == 1317


def test_sentinels_return_none() -> None:
    assert decode_register([SENTINEL_U16], _reg(data_type="u16")) is None
    assert decode_register([SENTINEL_S16], _reg(data_type="s16")) is None
    assert (
        decode_register([0xFFFF, 0xFFFF], _reg(data_type="u32", length_words=2)) is None
    )
    assert SENTINEL_U32 == 0xFFFFFFFF


def test_utf8_sn() -> None:
    # "AB" padded
    words = [0x4142, 0x0000]
    assert decode_register(words, _reg(data_type="utf8", length_words=2)) == "AB"


def test_encode_roundtrip_u16_scaled() -> None:
    reg = _reg(
        data_type="u16",
        scale=0.1,
        decimals=1,
        access="RW",
        register_type="holding",
        min_value=50.0,
        max_value=100.0,
    )
    words = encode_register(reg, 87.5)
    assert words == [875]
    assert decode_register(words, reg) == 87.5


def test_words_to_bytes_endianness() -> None:
    assert words_to_bytes([0x0102], word_order="big") == b"\x01\x02"
    assert words_to_bytes([0x0304, 0x0102], word_order="little") == b"\x01\x02\x03\x04"
