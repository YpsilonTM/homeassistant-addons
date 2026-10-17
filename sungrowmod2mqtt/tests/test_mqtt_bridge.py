from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from src.catalog import load_catalog
from src.mqtt_bridge import ADDON_VERSION, LEGACY_MQTT_CLIENT_ID, MqttBridge


@pytest.fixture()
def registers():
    registers, _ = load_catalog()
    return registers


def _bridge(registers, **kwargs) -> MqttBridge:
    defaults = dict(
        host="localhost",
        port=1883,
        client_id="sungrowmod2mqtt-addon",
        registers=registers,
        device_id="sungrow_SERIAL0000001",
        legacy_modqtt_compat=False,
        allow_writes=False,
    )
    defaults.update(kwargs)
    return MqttBridge(**defaults)  # type: ignore[arg-type]


def test_device_discovery_cmps_structure(registers) -> None:
    bridge = _bridge(registers, legacy_modqtt_compat=True)
    payload = bridge.build_device_discovery()

    assert payload["dev"]["identifiers"] == ["sungrow_SERIAL0000001"]
    assert payload["o"]["name"]
    assert payload["o"]["sw"] == ADDON_VERSION
    assert "cmps" in payload
    assert "availability" not in payload  # shared via avty
    assert payload["avty"][0]["topic"] == bridge.availability_topic

    total_pv = payload["cmps"]["total_pv_generation"]
    assert total_pv["p"] == "sensor"
    assert total_pv["unique_id"] == f"{LEGACY_MQTT_CLIENT_ID}_total_pv_generation"
    assert total_pv["default_entity_id"] == "sensor.sungrow_inverter_total_pv_generation"
    assert "object_id" not in total_pv
    assert "device" not in total_pv


def test_hidden_component_removal_stub(registers) -> None:
    bridge = _bridge(registers, allow_writes=False, enable_ems_writes=False)
    payload = bridge.build_device_discovery()
    ems = payload["cmps"]["ems_mode"]
    assert ems == {"p": "select"}

    bridge_on = _bridge(registers, allow_writes=True, enable_ems_writes=True)
    full = bridge_on.build_device_discovery()["cmps"]["ems_mode"]
    assert full["p"] == "select"
    assert "command_topic" in full
    assert "unique_id" in full


def test_number_step_uses_scale(registers) -> None:
    bridge = _bridge(registers, allow_writes=True, enable_ems_writes=True)
    reg = next(r for r in registers if r.slug == "battery_max_soc")
    assert reg.scale == 0.1
    component = bridge._component_payload(reg)
    assert component["step"] == 0.1
    assert component["min"] == 50.0
    assert component["max"] == 100.0


def test_wide_register_bounds(registers) -> None:
    bridge = _bridge(registers, allow_writes=True, enable_ems_writes=True)
    for slug, expected_min, expected_max in (
        ("charge_cutoff_voltage_wide", 0.0, 1000.0),
        ("max_charging_power", 0.0, 100.0),
        ("max_discharging_power", 0.0, 100.0),
        ("charge_discharge_power_wide", 0.0, 100.0),
    ):
        reg = next(r for r in registers if r.slug == slug)
        assert reg.min_value == expected_min
        assert reg.max_value == expected_max
        component = bridge._component_payload(reg)
        assert component["min"] == expected_min
        assert component["max"] == expected_max


def test_publish_skips_unchanged_retained(registers) -> None:
    bridge = _bridge(registers)
    bridge._connected.set()
    bridge._client = MagicMock()
    bridge._client.publish.return_value = MagicMock(rc=0)

    bridge.publish("topic/a", "online", retain=True)
    bridge.publish("topic/a", "online", retain=True)
    assert bridge._client.publish.call_count == 1

    bridge.publish("topic/a", "offline", retain=True)
    assert bridge._client.publish.call_count == 2


def test_publish_cache_cleared_on_reconnect(registers) -> None:
    bridge = _bridge(registers)
    bridge._connected.set()
    bridge._client = MagicMock()
    bridge._client.publish.return_value = MagicMock(rc=0)
    bridge._publish_cache["topic/a"] = "online"

    bridge._on_connect(bridge._client, None, None, 0)
    assert "topic/a" not in bridge._publish_cache
    assert bridge.availability_topic in bridge._publish_cache
    assert bridge._client.publish.called


def test_discovery_migration_writes_marker(registers, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / "marker"
    monkeypatch.setattr(MqttBridge, "discovery_marker_path", lambda *args: marker)
    bridge = _bridge(registers)
    bridge._connected.set()
    bridge._client = MagicMock()
    bridge._client.publish.return_value = MagicMock(rc=0)

    bridge.publish_discovery()
    assert marker.exists()
    assert ADDON_VERSION in marker.read_text(encoding="utf-8")
    topics = [call.args[0] for call in bridge._client.publish.call_args_list]
    assert bridge.device_discovery_topic in topics
    assert any("migrate_discovery" in str(call.args[1]) for call in bridge._client.publish.call_args_list)

    bridge._client.reset_mock()
    bridge.publish_discovery()
    migrate_calls = [
        call for call in bridge._client.publish.call_args_list if "migrate_discovery" in str(call.args[1])
    ]
    assert migrate_calls == []


def test_writes_gated_hides_ems_controls(registers) -> None:
    bridge = _bridge(registers, allow_writes=True, enable_ems_writes=False)
    visible = {reg.slug for reg in bridge.visible_registers()}
    assert "ems_mode" not in visible
    assert "clock_year" in visible

    bridge_off = _bridge(registers, allow_writes=False, enable_ems_writes=False)
    visible_off = {reg.slug for reg in bridge_off.visible_registers()}
    assert "clock_year" not in visible_off
    assert "total_dc_power" in visible_off


def test_daily_state_class_total_increasing(registers) -> None:
    dailies = [reg for reg in registers if reg.slug.startswith("daily_")]
    assert dailies
    for reg in dailies:
        assert reg.state_class == "total_increasing"
