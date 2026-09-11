"""MqttBridge tests against a fake paho client (no broker)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from src.mqtt_bridge import PHASE_MODE_SET_OPTIONS, BridgeState, MqttBridge


class FakeMqttClient:
    """Minimal stand-in for paho.mqtt.client.Client."""

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

    b = MqttBridge(
        host="localhost",
        port=1883,
        client_id="test-client",
        topic_prefix="sigen_evac",
        device_name="Sigen EV AC",
        device_id="sigen_evac_SN1",
        min_current=6,
        max_current=16,
    )
    b._connected.set()
    return b


def test_topic_construction(bridge: MqttBridge) -> None:
    assert bridge.availability_topic == "sigen_evac/availability"
    assert bridge.current_set_topic == "sigen_evac/current/set"
    assert bridge.phase_mode_state_topic == "sigen_evac/phase_mode/state"
    assert bridge.phase_mode_availability_topic == "sigen_evac/phase_mode/availability"
    assert bridge.command_topic == "sigen_evac/command"
    assert bridge.lifetime_energy_topic == "sigen_evac/lifetime_energy_kwh"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (10.0, "10"),
        (10, "10"),
        (10.5, "10.50"),
        (0.0, "0"),
    ],
)
def test_num_formatting(value: float, expected: str) -> None:
    assert MqttBridge._num(value) == expected


def test_discovery_payload_shape(bridge: MqttBridge) -> None:
    bridge.publish_discovery()
    client: FakeMqttClient = bridge._client  # type: ignore[assignment]
    assert bridge._discovery_published is True

    by_topic = {topic: json.loads(payload) for topic, payload, _ in client.published if payload}
    phase_cfg = by_topic[f"homeassistant/select/{bridge.device_id}_phase_mode/config"]
    assert phase_cfg["options"] == list(PHASE_MODE_SET_OPTIONS)
    assert "unknown" not in phase_cfg["options"]
    assert phase_cfg["availability_mode"] == "all"
    assert len(phase_cfg["availability"]) == 2

    active = by_topic[f"homeassistant/sensor/{bridge.device_id}_active_phases/config"]
    assert active["state_class"] == "measurement"

    number_cfg = by_topic[f"homeassistant/number/{bridge.device_id}_laadstroom/config"]
    assert number_cfg["min"] == 6
    assert number_cfg["max"] == 16

    cleared = {
        topic
        for topic, payload, retain in client.published
        if payload == "" and retain and topic.startswith("homeassistant/button/")
    }
    assert f"homeassistant/button/{bridge.device_id}_phase_1/config" in cleared
    assert f"homeassistant/button/{bridge.device_id}_phase_3/config" in cleared
    assert f"homeassistant/button/{bridge.device_id}_phase_auto/config" in cleared


def test_reconnect_republishes_availability_and_discovery(bridge: MqttBridge) -> None:
    """Bug 1: after discovery was published once, reconnect republishes online + discovery."""
    bridge.publish_discovery()
    client: FakeMqttClient = bridge._client  # type: ignore[assignment]
    client.published.clear()
    client.subscribed.clear()

    reconnect_calls: list[int] = []
    bridge.set_reconnect_handler(lambda: reconnect_calls.append(1))

    bridge._on_connect(client, None, None, reason_code=0)

    assert ("sigen_evac/availability", "online", True) in client.published
    assert any(t.startswith("homeassistant/") for t, _, _ in client.published)
    assert bridge.current_set_topic in client.subscribed
    assert bridge.phase_mode_set_topic in client.subscribed
    assert bridge.command_topic in client.subscribed
    assert reconnect_calls == [1]


def test_first_connect_skips_discovery_before_flag(bridge: MqttBridge) -> None:
    client: FakeMqttClient = bridge._client  # type: ignore[assignment]
    assert bridge._discovery_published is False
    bridge._on_connect(client, None, None, reason_code=0)
    topics = [t for t, _, _ in client.published]
    assert topics == ["sigen_evac/availability"]
    assert not any(t.startswith("homeassistant/") for t in topics)


def test_setpoint_clamped_on_publish(bridge: MqttBridge) -> None:
    """Bug 4: setpoint outside [min,max] is clamped before publish."""
    client: FakeMqttClient = bridge._client  # type: ignore[assignment]
    state = BridgeState(
        status="charging",
        power_w=1000.0,
        current_a=10.0,
        voltage_v=230.0,
        active_phases=1,
        lifetime_energy_kwh=1.0,
        setpoint_a=99.0,
        max_current_a=16.0,
        phase_mode="1_phase",
    )
    bridge.publish_state_bundle(state)

    current_state = [
        payload for topic, payload, _ in client.published if topic == bridge.current_state_topic
    ]
    assert current_state == ["16"]

    bundle_payloads = [
        json.loads(payload) for topic, payload, _ in client.published if topic == bridge.state_topic
    ]
    assert bundle_payloads[0]["setpoint_a"] == 16.0
