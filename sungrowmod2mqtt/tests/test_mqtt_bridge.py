from __future__ import annotations

from src.catalog import load_catalog
from src.mqtt_bridge import LEGACY_MQTT_CLIENT_ID, MqttBridge


def test_legacy_discovery_unique_ids() -> None:
    registers, _ = load_catalog()
    bridge = MqttBridge(
        "localhost",
        1883,
        "sungrowmod2mqtt-addon",
        registers,
        device_id="sungrow_TESTSERIAL",
        legacy_modqtt_compat=True,
        allow_writes=False,
    )
    device = bridge._device_block()
    availability = {"topic": bridge.availability_topic}

    total_pv = next(reg for reg in registers if reg.slug == "total_pv_generation")
    topic, payload = bridge._register_discovery(total_pv, device, availability)
    assert topic == "homeassistant/sensor/total_pv_generation/config"
    assert payload["unique_id"] == f"{LEGACY_MQTT_CLIENT_ID}_total_pv_generation"
    assert payload["object_id"] == "sungrow_inverter_total_pv_generation"
    assert payload["state_topic"] == "sungrowmod2mqtt/total_pv_generation/state"
    assert payload["enabled_by_default"] is True


def test_non_legacy_unique_id() -> None:
    registers, _ = load_catalog()
    bridge = MqttBridge(
        "localhost",
        1883,
        "sungrowmod2mqtt-addon",
        registers,
        device_id="sungrow_ABC",
        legacy_modqtt_compat=True,
        allow_writes=False,
    )
    device = bridge._device_block()
    availability = {"topic": bridge.availability_topic}
    reactive = next(reg for reg in registers if reg.slug == "reactive_power")
    topic, payload = bridge._register_discovery(reactive, device, availability)
    assert topic == "homeassistant/sensor/sungrow_ABC_reactive_power/config"
    assert payload["unique_id"] == "sungrow_ABC_reactive_power"


def test_writes_gated_hides_ems_controls() -> None:
    registers, _ = load_catalog()
    bridge = MqttBridge(
        "localhost",
        1883,
        "client",
        registers,
        allow_writes=True,
        enable_ems_writes=False,
    )
    visible = {reg.slug for reg in bridge.visible_registers()}
    assert "ems_mode" not in visible
    assert "clock_year" in visible  # non-EMS holding still visible when writes on

    bridge_off = MqttBridge(
        "localhost",
        1883,
        "client",
        registers,
        allow_writes=False,
        enable_ems_writes=False,
    )
    visible_off = {reg.slug for reg in bridge_off.visible_registers()}
    assert "clock_year" not in visible_off
    assert "total_dc_power" in visible_off


def test_ems_writes_exposes_ems_mode() -> None:
    registers, _ = load_catalog()
    bridge = MqttBridge(
        "localhost",
        1883,
        "client",
        registers,
        allow_writes=True,
        enable_ems_writes=True,
    )
    assert "ems_mode" in {reg.slug for reg in bridge.visible_registers()}
    ems = next(reg for reg in registers if reg.slug == "ems_mode")
    topic, payload = bridge._register_discovery(ems, bridge._device_block(), {"topic": "a"})
    assert topic.startswith("homeassistant/select/")
    assert "command_topic" in payload
    assert "self_consumption" in payload["options"]
