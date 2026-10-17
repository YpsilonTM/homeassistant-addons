from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from src.catalog import load_catalog
from src.mqtt_bridge import MqttBridge, active_phases


class FakeMqttClient:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.published: list[tuple[str, str, bool]] = []
        self.subscribed: list[str] = []
        self.will: tuple[str, str, bool] | None = None

    def username_pw_set(self, username: str, password: str = "") -> None:
        del username, password

    def will_set(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> None:
        del qos
        self.will = (topic, payload, retain)

    def reconnect_delay_set(self, min_delay: int = 1, max_delay: int = 120) -> None:
        del min_delay, max_delay

    def connect_async(self, host: str, port: int = 1883) -> None:
        del host, port

    def loop_start(self) -> None:
        return None

    def loop_stop(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)

    def publish(self, topic: str, payload: str = "", qos: int = 0, retain: bool = False):
        del qos
        self.published.append((topic, payload, retain))
        return SimpleNamespace(rc=0)


def _bridge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **kwargs) -> MqttBridge:
    import src.mqtt_bridge as mb

    monkeypatch.setattr(mb.mqtt, "Client", FakeMqttClient)
    monkeypatch.setattr(mb.mqtt, "MQTT_ERR_SUCCESS", 0)
    tags = load_catalog()
    options = dict(
        host="localhost",
        port=1883,
        client_id="test-client",
        tags=tags,
        topic_prefix="sigen_evac_local",
        device_name="Sigen EV AC Local",
        device_id="sigen_evac_local_SN1",
        min_current=6,
        max_current=16,
        migration_marker=tmp_path / "device_discovery_migrated",
    )
    options.update(kwargs)
    instance = MqttBridge(**options)
    instance._connected.set()
    return instance


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> MqttBridge:
    return _bridge(monkeypatch, tmp_path)


def _device_payload(bridge: MqttBridge) -> dict:
    client: FakeMqttClient = bridge._client  # type: ignore[assignment]
    topic = f"homeassistant/device/{bridge.device_id}/config"
    for published_topic, payload, _retain in reversed(client.published):
        if published_topic == topic and payload:
            return json.loads(payload)
    raise AssertionError(f"missing device discovery on {topic}")


def test_topic_construction(bridge: MqttBridge) -> None:
    assert bridge.availability_topic == "sigen_evac_local/availability"
    assert bridge.current_set_topic == "sigen_evac_local/current/set"
    assert bridge.tag_set_topic(bridge.tags_by_id[12194]) == "sigen_evac_local/tag/12194/set"


def test_discovery_device_hides_dangerous_by_default(bridge: MqttBridge) -> None:
    bridge.publish_discovery()
    payload = _device_payload(bridge)
    cmps = payload["cmps"]
    assert payload["o"]["sw"] == "0.2.0"
    assert "availability" in payload
    assert cmps["charge_current"]["p"] == "number"
    assert cmps["charge_current"]["default_entity_id"] == f"number.{bridge.device_id}_charge_current"
    assert cmps["charge_current"]["min"] == 6
    assert cmps["charge_current"]["max"] == 16
    assert "entity_category" not in cmps["charge_current"]
    assert cmps["factory_reset"] == {"p": "button"}
    assert cmps["data_wipe"] == {"p": "button"}
    assert cmps["house_breaker_current"] == {"p": "number"}
    assert cmps["pole_breaker_current"] == {"p": "number"}
    assert cmps["start"]["payload_press"] == "START"
    assert cmps["stop"]["payload_press"] == "STOP"
    assert "charge_start_stop" not in cmps


def test_discovery_ux_cleanup(bridge: MqttBridge) -> None:
    bridge.publish_discovery()
    cmps = _device_payload(bridge)["cmps"]
    assert cmps["charge_power"]["name"] == "Active charge power"
    assert cmps["charge_power"]["unit_of_measurement"] == "kW"
    assert cmps["charge_power_percent"]["name"] == "Charge power limit (%)"
    assert cmps["charge_power_percent"]["entity_category"] == "config"
    assert cmps["charge_power_fixed"]["name"] == "Charge power limit (fixed)"
    assert cmps["dlm_mode"]["enabled_by_default"] is False
    assert cmps["session_duration"]["device_class"] == "duration"
    assert cmps["lifetime_duration"]["device_class"] == "duration"
    assert cmps["session_energy"]["state_class"] == "total_increasing"
    assert cmps["cable_temp"]["unit_of_measurement"] == "°C"
    assert cmps["cable_temp"]["device_class"] == "temperature"
    wifi = cmps["wifi_ap_password"]
    assert wifi["p"] == "text"
    assert "state_topic" not in wifi
    assert wifi["mode"] == "password"
    apn = cmps["apn_password"]
    assert "state_topic" not in apn


def test_discovery_includes_dangerous_when_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    instance = _bridge(monkeypatch, tmp_path, enable_dangerous_commands=True)
    instance.publish_discovery()
    cmps = _device_payload(instance)["cmps"]
    assert cmps["factory_reset"]["p"] == "button"
    assert "unique_id" in cmps["factory_reset"]
    assert cmps["house_breaker_current"]["unique_id"].endswith("house_breaker_current")


def test_discovery_migration_marker(bridge: MqttBridge, tmp_path: Path) -> None:
    marker = tmp_path / "device_discovery_migrated"
    assert not marker.exists()
    bridge.publish_discovery()
    assert marker.exists()
    client: FakeMqttClient = bridge._client  # type: ignore[assignment]
    migrate_topics = [
        topic
        for topic, payload, _ in client.published
        if payload == json.dumps({"migrate_discovery": True})
    ]
    assert any(topic.endswith("_charge_current/config") for topic in migrate_topics)
    cleared = {
        topic
        for topic, payload, retain in client.published
        if payload == "" and retain and topic.startswith("homeassistant/") and topic != bridge.device_discovery_topic
    }
    assert any(topic.endswith("_charge_current/config") for topic in cleared)

    client.published.clear()
    bridge.publish_discovery()
    assert not any(
        payload == json.dumps({"migrate_discovery": True}) for _topic, payload, _ in client.published
    )


def test_publish_values_skips_secrets_and_keeps_real_setpoint(bridge: MqttBridge) -> None:
    bridge.publish_values(
        {
            12194: 8.0,
            11220: 2.06,
            11213: 7.5,
            11215: 7.4,
            11217: 0.0,
            11210: 230.76,
            11350: 1154.29,
            11265: 0,
            12106: 1,
            12520: 0,
            11592: "super-secret",
            12141: "apn-secret",
        }
    )
    client: FakeMqttClient = bridge._client  # type: ignore[assignment]
    by_topic = {topic: payload for topic, payload, _ in client.published}
    assert by_topic["sigen_evac_local/current/state"] == "8"
    assert by_topic["sigen_evac_local/power_w"] == "2060"
    assert by_topic["sigen_evac_local/active_phases"] == "2"
    assert by_topic["sigen_evac_local/lifetime_energy_kwh"] == "1154.29"
    assert by_topic["sigen_evac_local/tag/12194/state"] == "8"
    assert by_topic["sigen_evac_local/status"] == "unplugged"
    assert by_topic["sigen_evac_local/tag/11265/state"] == "unplugged"
    assert by_topic["sigen_evac_local/tag/12106/state"] == "authentication"
    assert by_topic["sigen_evac_local/tag/12520/state"] == "1_phase"
    assert "sigen_evac_local/tag/11592/state" not in by_topic
    assert "sigen_evac_local/tag/12141/state" not in by_topic


def test_publish_on_change_cache(bridge: MqttBridge) -> None:
    bridge.publish("sigen_evac_local/status", "charging", retain=True)
    bridge.publish("sigen_evac_local/status", "charging", retain=True)
    client: FakeMqttClient = bridge._client  # type: ignore[assignment]
    assert sum(1 for topic, payload, _ in client.published if topic.endswith("/status")) == 1
    bridge._publish_cache.clear()
    bridge.publish("sigen_evac_local/status", "charging", retain=True)
    assert sum(1 for topic, payload, _ in client.published if topic.endswith("/status")) == 2


def test_discovery_enum_selects(bridge: MqttBridge) -> None:
    bridge.publish_discovery()
    cmps = _device_payload(bridge)["cmps"]
    assert cmps["auth_mode"]["options"] == ["auto_charge", "authentication"]
    assert cmps["run_state"]["device_class"] == "enum"
    assert "charging" in cmps["run_state"]["options"]
    assert cmps["status"]["options"][0] == "unplugged"


def test_active_phases() -> None:
    assert active_phases({11213: 8.0, 11215: 0.0, 11217: 7.9}) == 2
    assert active_phases({}) == 0


def test_tag_set_handler(bridge: MqttBridge) -> None:
    seen: list[tuple[int, str]] = []
    bridge.set_tag_handler(lambda tag_id, value: seen.append((tag_id, value)))
    message = SimpleNamespace(topic="sigen_evac_local/tag/11625/set", payload=b"1")
    bridge._on_message(None, None, message)
    message = SimpleNamespace(topic="sigen_evac_local/current/set", payload=b"8")
    bridge._on_message(None, None, message)
    assert seen == [(11625, "1"), (12194, "8")]


def test_command_handler(bridge: MqttBridge) -> None:
    seen: list[str] = []
    bridge.set_command_handler(seen.append)
    message = SimpleNamespace(topic="sigen_evac_local/command", payload=b"START")
    bridge._on_message(None, None, message)
    assert seen == ["START"]
