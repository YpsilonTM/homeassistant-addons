from __future__ import annotations

import re
from pathlib import Path
from textwrap import dedent

import pytest
from scripts.gen_options import load_grouped, render_options_block, render_schema_block
from src.catalog import load_catalog
from src.config import enabled_slugs, load_settings

ROOT = Path(__file__).resolve().parents[1]


def test_load_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODBUS_HOST", "192.0.2.10")
    monkeypatch.delenv("MQTT_HOST", raising=False)
    monkeypatch.delenv("LEGACY_MODQTT_COMPAT", raising=False)
    monkeypatch.delenv("REGISTER_TOGGLES", raising=False)
    settings = load_settings()
    assert settings.modbus_host == "192.0.2.10"
    assert settings.modbus_port == 502
    assert settings.allow_writes is False
    assert settings.legacy_modqtt_compat is False
    assert settings.mqtt_host == "core-mosquitto"
    assert settings.topic_prefix == "sungrowmod2mqtt"
    assert settings.register_toggles == {}


def test_register_toggles_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODBUS_HOST", "192.0.2.10")
    monkeypatch.setenv("REGISTER_TOGGLES", '{"total_dc_power":true,"battery_soc":false}')
    settings = load_settings()
    assert settings.register_toggles == {"total_dc_power": True, "battery_soc": False}


def test_enabled_slugs_falls_back_to_catalog_defaults() -> None:
    registers, _ = load_catalog()
    enabled = enabled_slugs(registers, {})
    assert enabled == {reg.slug for reg in registers if reg.enabled_by_default}
    assert "total_dc_power" in enabled
    assert "battery_soc" not in enabled

    overridden = enabled_slugs(registers, {"battery_soc": True, "total_dc_power": False})
    assert "battery_soc" in overridden
    assert "total_dc_power" not in overridden


def test_ems_requires_allow_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODBUS_HOST", "192.0.2.10")
    monkeypatch.setenv("ALLOW_WRITES", "false")
    monkeypatch.setenv("ENABLE_EMS_WRITES", "true")
    with pytest.raises(ValueError, match="ENABLE_EMS_WRITES"):
        load_settings()


def test_missing_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODBUS_HOST", raising=False)
    with pytest.raises(ValueError, match="MODBUS_HOST"):
        load_settings()


def _extract_marked_block(text: str, *, which: int) -> str:
    pattern = re.compile(
        r"# BEGIN GENERATED REGISTERS\n(.*?)\n[ \t]*# END GENERATED REGISTERS",
        re.DOTALL,
    )
    matches = pattern.findall(text)
    assert len(matches) >= which
    return dedent(matches[which - 1]).strip()


def test_config_yaml_registers_synced_with_catalog() -> None:
    config_text = (ROOT / "config.yaml").read_text(encoding="utf-8")
    grouped = load_grouped()
    assert _extract_marked_block(config_text, which=1) == render_options_block(grouped)
    assert _extract_marked_block(config_text, which=2) == render_schema_block(grouped)
