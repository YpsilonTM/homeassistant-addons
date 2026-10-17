from __future__ import annotations

from src.catalog import filter_registers, load_catalog, model_name


def test_catalog_loads_and_has_known_addresses() -> None:
    registers, device_codes = load_catalog()
    assert len(registers) >= 100
    by_addr = {(reg.register_type, reg.address): reg for reg in registers}

    assert by_addr[("input", 5000)].slug == "device_type_code"
    assert by_addr[("input", 5017)].slug == "total_dc_power"
    assert by_addr[("input", 13003)].slug == "total_pv_generation"
    assert by_addr[("holding", 13050)].slug == "ems_mode"
    assert by_addr[("holding", 13058)].slug == "battery_max_soc"

    assert model_name(device_codes, 0xE01) == "SH6.0RT"


def test_wire_address_is_documented_minus_one() -> None:
    registers, _ = load_catalog()
    by_slug = {reg.slug: reg for reg in registers}
    assert by_slug["inverter_temperature"].address == 5008
    assert by_slug["inverter_temperature"].wire_address == 5007
    assert by_slug["total_dc_power"].wire_address == 5016
    assert by_slug["total_pv_generation"].wire_address == 13002


def test_legacy_modqtt_suffixes_cover_twenty_two_sensors() -> None:
    registers, _ = load_catalog()
    legacy = [
        reg
        for reg in registers
        if reg.legacy_modqtt_suffix and reg.register_type == "input"
    ]
    assert len(legacy) == 22
    suffixes = {reg.legacy_modqtt_suffix for reg in legacy}
    assert "total_pv_generation" in suffixes
    assert "total_dc_power" in suffixes
    assert "export_power_raw" in suffixes


def test_core_enabled_by_default() -> None:
    registers, _ = load_catalog()
    enabled = {reg.slug for reg in registers if reg.enabled_by_default}
    assert "total_dc_power" in enabled
    assert "total_pv_generation" in enabled
    assert "load_power" in enabled
    assert "running_state" in enabled
    assert "battery_soc" not in enabled


def test_sh60rt_filters_meter_ch2() -> None:
    registers, _ = load_catalog()
    # SH RS family marked unsupported for meter ch2; SH6.0RT (0xE01) is RT and should keep them
    active = filter_registers(registers, device_code=0xE01)
    slugs = {reg.slug for reg in active}
    assert "meter_ch2_total_power" in slugs
    assert "total_dc_power" in slugs

    # SH5.0RS unsupported for meter ch2
    active_rs = filter_registers(registers, device_code=0xD0F)
    assert "meter_ch2_total_power" not in {reg.slug for reg in active_rs}


def test_unique_slugs() -> None:
    registers, _ = load_catalog()
    slugs = [reg.slug for reg in registers]
    assert len(slugs) == len(set(slugs))
