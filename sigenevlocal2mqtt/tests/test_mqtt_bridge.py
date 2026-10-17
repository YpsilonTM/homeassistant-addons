from __future__ import annotations

import json
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


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch) -> MqttBridge:
    import src.mqtt_bridge as mb

    monkeypatch.setattr(mb.mqtt, "Client", FakeMqttClient)
    monkeypatch.setattr(mb.mqtt, "MQTT_ERR_SUCCESS", 0)
    tags = load_catalog()
    instance = MqttBridge(
        host="localhost",
        port=1883,
        client_id="test-client",
        tags=tags,
        topic_prefix="sigen_evac_local",
        device_name="Sigen EV AC Local",
        device_id="sigen_evac_local_SN1",
        min_current=6,
        max_current=16,
    )
    instance._connected.set()
    return instance


def test_topic_construction(bridge: MqttBridge) -> None:
    assert bridge.availability_topic == "sigen_evac_local/availability"
    assert bridge.current_set_topic == "sigen_evac_local/current/set"
    assert bridge.tag_set_topic(bridge.tags_by_id[12194]) == "sigen_evac_local/tag/12194/set"


def test_discovery_hides_dangerous_by_default(bridge: MqttBridge) -> None:
    bridge.publish_discovery()
    client: FakeMqttClient = bridge._client  # type: ignore[assignment]
    topics = {topic for topic, _payload, _retain in client.published}
    assert any(topic.endswith("_charge_current/config") for topic in topics)
    assert not any("factory_reset" in topic for topic in topics)
    assert not any("data_wipe" in topic for topic in topics)
    by_topic = {topic: json.loads(payload) for topic, payload, _ in client.published if payload}
    current = next(cfg for topic, cfg in by_topic.items() if topic.endswith("_charge_current/config"))
    assert current["min"] == 6
    assert current["max"] == 16
    assert current["command_topic"] == "sigen_evac_local/current/set"
    start = by_topic[f"homeassistant/button/{bridge.device_id}_start/config"]
    stop = by_topic[f"homeassistant/button/{bridge.device_id}_stop/config"]
    assert start["payload_press"] == "START"
    assert stop["payload_press"] == "STOP"
    assert not any("charge_start_stop" in topic for topic in topics)


def test_discovery_includes_dangerous_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.mqtt_bridge as mb

    monkeypatch.setattr(mb.mqtt, "Client", FakeMqttClient)
    monkeypatch.setattr(mb.mqtt, "MQTT_ERR_SUCCESS", 0)
    instance = MqttBridge(
        host="localhost",
        port=1883,
        client_id="test-client",
        tags=load_catalog(),
        topic_prefix="sigen_evac_local",
        device_id="sigen_evac_local_SN1",
        enable_dangerous_commands=True,
    )
    instance._connected.set()
    instance.publish_discovery()
    client: FakeMqttClient = instance._client  # type: ignore[assignment]
    topics = {topic for topic, _payload, _retain in client.published}
    assert any("factory_reset" in topic for topic in topics)


def test_publish_values(bridge: MqttBridge) -> None:
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
        }
    )
    client: FakeMqttClient = bridge._client  # type: ignore[assignment]
    by_topic = {topic: payload for topic, payload, _ in client.published}
    assert by_topic["sigen_evac_local/current/state"] == "8"
    assert by_topic["sigen_evac_local/power_w"] == "2060"
    assert by_topic["sigen_evac_local/active_phases"] == "2"
    assert by_topic["sigen_evac_local/lifetime_energy_kwh"] == "1154.29"
    assert by_topic["sigen_evac_local/tag/12194/state"] == "8"


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
