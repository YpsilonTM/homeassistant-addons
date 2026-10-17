from __future__ import annotations

from src.app import _values_equal, parse_write_value
from src.catalog import load_catalog


def test_values_equal_numeric() -> None:
    assert _values_equal(1, 1.0)
    assert _values_equal(2.5, 2.5)
    assert not _values_equal(1, 2)


def test_write_dedup_comparison_matches_parsed() -> None:
    registers, _ = load_catalog()
    reg = next(r for r in registers if r.slug == "battery_max_soc")
    parsed = parse_write_value(reg, "87.5")
    assert _values_equal(87.5, parsed)
    assert not _values_equal(80.0, parsed)
