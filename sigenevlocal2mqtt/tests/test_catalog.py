from __future__ import annotations

from src.catalog import load_catalog, tags_by_id


def test_catalog_covers_evac_and_meter() -> None:
    tags = load_catalog()
    by_id = tags_by_id(tags)
    assert len(tags) == 271
    assert by_id[12000].access == "CMD"
    assert by_id[12000].encode(0) == b"\x00\x00"
    assert by_id[12000].encode(1) == b"\x01\x00"
    assert by_id[11000].access == "RO"
    assert by_id[12194].access == "RW"
    assert by_id[12194].scale == 100
    assert by_id[11350].access == "RO"
    assert by_id[1326].node == "meter"
    assert by_id[12002].dangerous is True
    assert by_id[12003].dangerous is True
    assert by_id[12141].secret is True


def test_charge_current_roundtrip() -> None:
    tag = tags_by_id()[12194]
    raw = tag.encode(8)
    assert raw == bytes.fromhex("20030000")
    assert tag.decode(raw) == 8.0


def test_string_roundtrip() -> None:
    tag = tags_by_id()[24]
    raw = tag.encode("120A64150210")
    assert tag.decode(raw) == "120A64150210"
