from __future__ import annotations

from src.app import IDENTITY_SLUGS, _values_equal, parse_write_value, selectable_for_poll
from src.catalog import load_catalog
from src.config import enabled_slugs


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


def test_poll_selection_excludes_disabled_slugs() -> None:
    registers, _ = load_catalog()
    active = [reg for reg in registers if reg.register_type == "input"]
    enabled = enabled_slugs(active, {"total_dc_power": True, "mppt1_voltage": False})
    selected = selectable_for_poll(active, include_slow=True, include_holding=False)
    selected = [reg for reg in selected if reg.slug in enabled]
    slugs = {reg.slug for reg in selected}
    assert "total_dc_power" in slugs
    assert "mppt1_voltage" not in slugs
    # Catalog defaults still apply for untouched slugs
    assert "daily_pv_generation" in slugs


def test_identity_slugs_present_even_when_toggles_off() -> None:
    registers, _ = load_catalog()
    by_slug = {reg.slug: reg for reg in registers}
    for slug in IDENTITY_SLUGS:
        assert slug in by_slug
    # Toggles off does not remove identity from catalog; app still reads IDENTITY_SLUGS.
    enabled = enabled_slugs(registers, {slug: False for slug in IDENTITY_SLUGS})
    for slug in IDENTITY_SLUGS:
        assert slug not in enabled
        assert slug in by_slug
