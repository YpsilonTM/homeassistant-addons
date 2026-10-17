from __future__ import annotations

import pytest
from src.config import load_settings


def test_load_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODBUS_HOST", "192.0.2.10")
    monkeypatch.delenv("MQTT_HOST", raising=False)
    monkeypatch.delenv("LEGACY_MODQTT_COMPAT", raising=False)
    settings = load_settings()
    assert settings.modbus_host == "192.0.2.10"
    assert settings.modbus_port == 502
    assert settings.allow_writes is False
    assert settings.legacy_modqtt_compat is False
    assert settings.mqtt_host == "core-mosquitto"
    assert settings.topic_prefix == "sungrowmod2mqtt"


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
